# ================================================================
# modes/options/option_scanner.py
# ================================================================
# Scans NIFTY option chain from Zerodha and selects the best
# contract (strike + CE/PE) for directional option buying.
#
# Phase O-4 scope — BUY ONLY, no selling, no spreads.
#
# Strategy 1 (Directional Buying on VOLATILE/TREND days):
#   - Uses regime classifier + NIFTY trend signal
#   - Selects ATM or slightly OTM weekly NIFTY options
#   - Returns a candidate with strike, premium, SL, target
#
# Depends on:
#   - ZerodhaClient.load_nfo_instruments() for option chain
#   - NIFTY 50 quote for direction (reuses equity pattern)
#   - India VIX for premium richness check
# ================================================================

import datetime
import math

from config              import Config, now_ist
from core.logger         import Logger
from core.zerodha_client import ZerodhaClient


class OptionScanner:
    """Scans NIFTY option chain and selects strike for directional buying."""

    def __init__(self, config: type[Config], zerodha: ZerodhaClient, log: Logger):
        self.cfg = config
        self.zerodha = zerodha
        self.log = log

    # ================================================================
    # PUBLIC: scan for option trade candidate
    # ================================================================

    def scan(
        self,
        nifty_price: float,
        nifty_trend: str,       # "BULLISH" | "BEARISH" | ""
        india_vix: float,
        market_condition: str,  # "BULLISH_NORMAL" etc.
    ) -> dict | None:
        """
        Scan the NIFTY option chain and return a single candidate dict,
        or None if no trade is appropriate today.

        Candidate dict schema:
        {
            "symbol":          "NIFTY2560924000CE",
            "strike":          24000,
            "option_type":     "CE" | "PE",
            "expiry":          datetime.date(2026, 6, 12),
            "lot_size":        25,
            "lots":            1,
            "qty":             25,           # lots × lot_size
            "premium":         215.50,       # per-unit premium
            "cost":            5387.50,      # total cost (qty × premium)
            "stop_loss":       150.85,       # 30% loss on premium
            "target":          376.13,       # 75% gain on premium
            "nifty_price":     24150.00,
            "nifty_trend":     "BULLISH",
            "india_vix":       14.5,
            "rationale":       "ATM CE on BULLISH NIFTY, VIX 14.5 (NORMAL)",
            "regime":          "VOLATILE",
        }
        """
        # ── Gate 1: Regime filter — skip RANGE days ───────────────
        regime = self._classify_regime(market_condition, india_vix)
        if regime == "RANGE":
            self.log.info(
                "Options scan: RANGE regime detected — skipping "
                "(Strategy 1 is for VOLATILE/TREND days only)."
            )
            return None

        # ── Gate 2: VIX filter — skip when premium is too expensive
        if india_vix > self.cfg.OPTIONS_VIX_MAX:
            self.log.info(
                f"Options scan: India VIX {india_vix:.1f} > "
                f"{self.cfg.OPTIONS_VIX_MAX} cap — premiums too rich, skipping."
            )
            return None

        # ── Gate 3: Need clear direction ──────────────────────────
        if nifty_trend not in ("BULLISH", "BEARISH"):
            self.log.info(
                "Options scan: no clear NIFTY trend — skipping."
            )
            return None

        # ── Determine CE or PE ────────────────────────────────────
        option_type = "CE" if nifty_trend == "BULLISH" else "PE"

        # ── Find nearest weekly expiry ────────────────────────────
        expiry = self._find_nearest_weekly_expiry()
        if not expiry:
            self.log.warning("Options scan: could not determine next weekly expiry.")
            return None

        days_to_expiry = (expiry - now_ist().date()).days
        if days_to_expiry < self.cfg.OPTIONS_MIN_DTE:
            self.log.info(
                f"Options scan: {days_to_expiry} DTE < minimum "
                f"{self.cfg.OPTIONS_MIN_DTE} — skipping (theta too aggressive)."
            )
            return None

        # ── Select strike ─────────────────────────────────────────
        strike = self._select_strike(nifty_price, option_type)

        # ── Build option symbol (Zerodha NFO format) ──────────────
        symbol = self._build_option_symbol("NIFTY", expiry, strike, option_type)

        # ── Fetch live premium ────────────────────────────────────
        premium = self._fetch_premium(symbol)
        if premium is None or premium <= 0:
            self.log.warning(
                f"Options scan: could not fetch premium for {symbol}."
            )
            return None

        # ── Position sizing ───────────────────────────────────────
        lot_size = self.cfg.OPTIONS_NIFTY_LOT_SIZE
        lots = min(
            self.cfg.OPTIONS_MAX_LOTS,
            max(1, int(self.cfg.OPTIONS_BUDGET_INR / (premium * lot_size))),
        )
        qty = lots * lot_size
        cost = round(premium * qty, 2)

        # ── Check budget ──────────────────────────────────────────
        if cost > self.cfg.OPTIONS_BUDGET_INR:
            self.log.info(
                f"Options scan: cost Rs.{cost:,.0f} exceeds budget "
                f"Rs.{self.cfg.OPTIONS_BUDGET_INR:,} — reducing to 1 lot."
            )
            lots = 1
            qty = lot_size
            cost = round(premium * qty, 2)
            if cost > self.cfg.OPTIONS_BUDGET_INR:
                self.log.info("Options scan: even 1 lot exceeds budget — skip.")
                return None

        # ── SL and target (on premium) ────────────────────────────
        sl_premium = round(premium * (1 - self.cfg.OPTIONS_SL_PCT_OF_PREMIUM / 100), 2)
        target_premium = round(premium * (1 + self.cfg.OPTIONS_TARGET_PCT_OF_PREMIUM / 100), 2)

        # ── Build rationale ───────────────────────────────────────
        moneyness = self._moneyness_label(nifty_price, strike, option_type)
        rationale = (
            f"{moneyness} {option_type} on {nifty_trend} NIFTY @ {nifty_price:,.0f}, "
            f"VIX {india_vix:.1f}, {days_to_expiry} DTE, regime={regime}"
        )

        candidate = {
            "symbol":       symbol,
            "strike":       strike,
            "option_type":  option_type,
            "expiry":       expiry,
            "lot_size":     lot_size,
            "lots":         lots,
            "qty":          qty,
            "premium":      premium,
            "cost":         cost,
            "stop_loss":    sl_premium,
            "target":       target_premium,
            "nifty_price":  nifty_price,
            "nifty_trend":  nifty_trend,
            "india_vix":    india_vix,
            "rationale":    rationale,
            "regime":       regime,
        }

        self.log.info(
            f"Options candidate: {symbol} | {lots} lot(s) @ Rs.{premium:.2f} "
            f"| SL {sl_premium:.2f} | Target {target_premium:.2f} "
            f"| Cost Rs.{cost:,.0f}"
        )

        return candidate

    # ================================================================
    # INTERNAL: Regime classification
    # ================================================================

    def _classify_regime(self, market_condition: str, india_vix: float) -> str:
        """
        Classify the day regime for options strategy routing.

        Returns: "VOLATILE" | "TREND" | "RANGE"

        Uses market condition (from equity scanner) + VIX level.
        - VIX > 18 and large move → VOLATILE
        - Clear directional move (>0.5%) → TREND
        - Flat, low VIX → RANGE
        """
        cond_upper = market_condition.upper()

        if "HIGH_VOLATILITY" in cond_upper or india_vix > 18:
            return "VOLATILE"

        if "BULLISH" in cond_upper or "BEARISH" in cond_upper:
            return "TREND"

        return "RANGE"

    # ================================================================
    # INTERNAL: Expiry logic
    # ================================================================

    def _find_nearest_weekly_expiry(self) -> datetime.date | None:
        """
        Find the nearest Thursday (NIFTY weekly expiry).
        If today is Thursday and before square-off, use today.
        Otherwise use next Thursday.
        Skips if DTE < OPTIONS_MIN_DTE.
        """
        today = now_ist().date()
        # Thursday = weekday 3
        days_ahead = (3 - today.weekday()) % 7
        if days_ahead == 0:
            # It's Thursday — use today if before square-off
            now = now_ist()
            sqoff = now.replace(
                hour=self.cfg.OPTIONS_SQUARE_OFF_HOUR,
                minute=self.cfg.OPTIONS_SQUARE_OFF_MINUTE,
                second=0, microsecond=0,
            )
            if now < sqoff:
                return today
            else:
                # After square-off on Thursday → next week
                return today + datetime.timedelta(days=7)
        return today + datetime.timedelta(days=days_ahead)

    # ================================================================
    # INTERNAL: Strike selection
    # ================================================================

    def _select_strike(self, nifty_price: float, option_type: str) -> int:
        """
        Select the ATM or slightly OTM strike for NIFTY.

        NIFTY strikes are at 50-point intervals.
        - For CE (bullish): pick ATM or 1 strike OTM
        - For PE (bearish): pick ATM or 1 strike OTM

        ATM = nearest 50 to current NIFTY price.
        """
        step = self.cfg.OPTIONS_NIFTY_STRIKE_STEP
        atm = round(nifty_price / step) * step

        offset = self.cfg.OPTIONS_STRIKE_OFFSET_STEPS * step
        if option_type == "CE":
            # For calls, OTM = above spot
            return int(atm + offset)
        else:
            # For puts, OTM = below spot
            return int(atm - offset)

    # ================================================================
    # INTERNAL: Symbol builder (Zerodha NFO format)
    # ================================================================

    def _build_option_symbol(
        self,
        index: str,       # "NIFTY" or "BANKNIFTY"
        expiry: datetime.date,
        strike: int,
        option_type: str,  # "CE" or "PE"
    ) -> str:
        """
        Build Zerodha NFO trading symbol.
        Format: NIFTY2560924000CE
        - NIFTY = index name
        - 25 = last 2 digits of year
        - 6 = month (hex: 1-9 for Jan-Sep, O/N/D for Oct/Nov/Dec)
        - 09 = day (zero-padded)
        - 24000 = strike price
        - CE = call/put
        """
        yy = expiry.year % 100
        month = expiry.month
        # Months 1-9 are "1"-"9", 10=O, 11=N, 12=D
        month_codes = {
            1: "1", 2: "2", 3: "3", 4: "4", 5: "5",
            6: "6", 7: "7", 8: "8", 9: "9",
            10: "O", 11: "N", 12: "D",
        }
        month_code = month_codes[month]
        dd = f"{expiry.day:02d}"
        return f"{index}{yy}{month_code}{dd}{strike}{option_type}"

    # ================================================================
    # INTERNAL: Premium fetch
    # ================================================================

    def _fetch_premium(self, symbol: str) -> float | None:
        """Fetch the last traded price for an NFO option contract."""
        try:
            quotes = self.zerodha.get_quotes(
                [{"symbol": symbol, "exchange": "NFO"}]
            )
            q = quotes.get(f"NFO:{symbol}", {})
            return q.get("last_price", 0)
        except Exception as e:
            self.log.warning(f"Failed to fetch premium for {symbol}: {e}")
            return None

    # ================================================================
    # INTERNAL: Moneyness label
    # ================================================================

    def _moneyness_label(
        self, spot: float, strike: int, option_type: str
    ) -> str:
        """Return 'ATM', 'ITM', or 'OTM' label."""
        diff = abs(spot - strike)
        pct = diff / spot * 100 if spot > 0 else 0
        if pct < 0.3:
            return "ATM"
        if option_type == "CE":
            return "ITM" if strike < spot else "OTM"
        else:
            return "ITM" if strike > spot else "OTM"
