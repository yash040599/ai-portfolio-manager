# ================================================================
# modes/trade/stock_scanner.py
# ================================================================
# Stock scanner — candle-pattern + technical-indicator pre-filter.
#
# WHAT IT DOES
# ------------
# Walks the configured stock universe (NIFTY 50 / 100 / 150 / 200) and:
#   1. Fetches 15-min and daily candles from Zerodha (via candle
#      cache so we re-use prior session data when available)
#   2. Detects 14 candlestick patterns + 14 technical indicators
#      (EMA, RSI, VWAP w/ bands, MACD, SuperTrend, ADX, ATR,
#       StochRSI, BollingerBands, ORB, Gap, Hourly EMA, etc.)
#   3. Computes a composite score (-25 .. +25) per symbol
#   4. Filters by MIN_SCORE, applies sector diversification,
#      tape-breadth penalty, and Nifty hard-filter
#   5. Returns the top-N candidates ready for OrderEngine.enter_trade
#
# Two scanning entry points:
#   scan_noai(...)  — used by the default --noai path; pure rules,
#                     zero Claude calls
#   scan(...)       — used by --ai path; sends pre-filtered candidates
#                     to Claude for final ranking + position-review
#
# MERGED 2026-05-12 — inlined V1 StockScanner
# (was: modes/trade/stock_scanner.py + modes/trade/stock_scanner.py
#  inheritance pair)
# ================================================================

import re
import datetime

from config             import Config, now_ist
from core.logger        import Logger
from core.claude_client import ClaudeClient


# ================================================================
# NIFTY INDEX CONSTITUENTS
# ================================================================
# Sourced from `shared/nifty_universe.py` since 2026-05-14 (S54).
# This module re-exports the four tier constants for back-compat
# with callers that historically imported them from
# `modes.trade.stock_scanner`. **Do not edit the lists here** —
# update the canonical source in `shared/nifty_universe.py` and
# every importer (trade scanner, swing scanner, dip-buy scanner,
# backtest, dashboard, compare) will pick up the new constituents
# automatically.
#
# The four-tier layout is incremental — each tier adds exactly 50
# more symbols on top of the previous tier, so SCAN_UNIVERSE
# settings of NIFTY50/100/150/200 map to a contiguous prefix of
# the same canonical list.
# ================================================================

from shared.nifty_universe import (
    NIFTY50, NIFTY100_EXTRA, NIFTY150_EXTRA, NIFTY200_EXTRA,
)



# ================================================================
# PARSING HELPERS
# ================================================================
# Shared by _extract_trade_fields() and _parse_review_response().
# ================================================================

def _parse_price(val: str) -> float:
    """Strips \u20b9, commas, spaces and converts to float. Returns 0.0 on failure."""
    cleaned = re.sub(r'[\u20b9,\s]', '', val)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_price_optional(val: str) -> float | None:
    """Like _parse_price but returns None for empty/invalid input."""
    if not val:
        return None
    cleaned = re.sub(r'[\u20b9,\s]', '', val)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(val: str) -> int:
    """Strips commas/spaces and converts to int. Returns 0 on failure."""
    cleaned = re.sub(r'[,\s]', '', val)
    try:
        return int(float(cleaned))
    except ValueError:
        return 0



# ── extra constants/helpers from the former v2 module ──
# V2 stock scanner: candle-pattern + technical-indicator pre-filter
# before sending candidates to Claude for final trade selection.
# WHY V2 EXISTS:
# V1 sends ALL 50-100+ stock prices to Claude as a flat text table.
# Claude picks trades purely from price/volume data + its training.
# This is like asking a doctor to diagnose from a photo — it works
# sometimes but has no structured clinical data.
# V2 first runs FREE mathematical analysis (no Claude cost) on every
# stock: candlestick patterns, EMA crossover, RSI, VWAP, SuperTrend.
# Stocks are ranked by a composite score. Only the top 15 candidates
# with the strongest technical setups are sent to Claude, along with
# their exact indicator values (RSI=28, SuperTrend=UP, etc.).
# Result: Claude sees fewer stocks but with much richer data per stock.
# It can reason about specific indicator confluences instead of
# guessing from raw prices.
# FLOW:
#   1. Fetch 15-min and daily candles for the entire universe
#      (sequential Zerodha API calls — ~2-3 min for NIFTY100)
#   2. Run candle pattern detection + technical indicators on each
#   3. Filter by MIN_SCORE, rank by composite score
#   4. Send top 15 filtered candidates to Claude with enriched data
#   5. Claude picks final trades from the pre-filtered set
# DURING MONITORING:
#   - Position reviews include fresh 5-min candle data per position
#   - Claude can see real-time pattern formations on open positions


from config                          import Config, now_ist
from core.logger                     import Logger
from core.claude_client              import ClaudeClient
from core.zerodha_client             import ZerodhaClient
from shared.candle_patterns        import (
    detect_all,
    detect_all_with_freshness,
    summarise_signals,
    BEARISH_REVERSAL_PATTERNS,
    BULLISH_REVERSAL_PATTERNS,
    INDECISION_PATTERNS,
)
from shared.technical_indicators   import (
    compute_technical_score, prev_day_sr_score,
    vwap, rsi, ema_crossover, supertrend, stoch_rsi,
)
from shared.candle_cache           import CandleCache
from modes.trade.candidate_telemetry    import CandidateTelemetry
from modes.trade.volume_baseline        import get_baseline_share


# Maximum candidates to send to Claude (rest are filtered out by math)
MAX_CANDIDATES = 15

# Maximum positions allowed per sector (prevents correlated drawdowns)
MAX_PER_SECTOR = 2


# PRE-OPEN SCORE FRESHNESS HELPER (Roadmap #262, 2026-05-07)

def _is_pre_open_score_time(now: datetime.datetime | None = None) -> bool:
    """
    Returns True when `now` (defaults to now_ist()) is before the close
    of the first 15-min candle on a given trading day.

    Operator-clarity helper for the candidate-result log lines. Scores
    computed before that boundary are derived from previous-close-anchored
    daily/15-min candles and the entry pipeline will revalidate them
    against fresh first-15-min candle data before any trade fires
    (stale-score guards #196 / #199). The `[pre-open]` suffix tells the
    operator reading the log not to trust an apparently-strong number
    until the live re-check.
    """
    n = now if now is not None else now_ist()
    cfg = Config
    open_dt = n.replace(
        hour=cfg.MARKET_OPEN_HOUR,
        minute=cfg.MARKET_OPEN_MINUTE,
        second=0, microsecond=0,
    )
    first_15m_close = open_dt + datetime.timedelta(minutes=15)
    return n < first_15m_close

# SECTOR MAPPING — NSE NIFTY STOCKS
# Used by the sector diversification filter.
# Stocks not in this map default to "OTHER".

SECTOR_MAP = {
    # Banking & Financial Services
    "HDFCBANK": "BANKING", "ICICIBANK": "BANKING", "KOTAKBANK": "BANKING",
    "AXISBANK": "BANKING", "SBIN": "BANKING", "BANKBARODA": "BANKING",
    "PNB": "BANKING", "CANBK": "BANKING", "UNIONBANK": "BANKING",
    "IDFCFIRSTB": "BANKING", "FEDERALBNK": "BANKING", "YESBANK": "BANKING",
    "INDUSINDBK": "BANKING",
    # NBFC / Financial
    "BAJFINANCE": "FINANCE", "BAJAJFINSV": "FINANCE", "CHOLAFIN": "FINANCE",
    "SHRIRAMFIN": "FINANCE", "MUTHOOTFIN": "FINANCE", "JIOFIN": "FINANCE",
    "HDFCLIFE": "FINANCE", "SBILIFE": "FINANCE", "HDFCAMC": "FINANCE",
    "PFC": "FINANCE", "RECLTD": "FINANCE", "IRFC": "FINANCE",
    "CANFINHOME": "FINANCE", "MFSL": "FINANCE", "LICHSGFIN": "FINANCE",
    "TATACAP": "FINANCE",
    # IT / Tech
    "TCS": "IT", "INFY": "IT", "HCLTECH": "IT", "WIPRO": "IT",
    "TECHM": "IT", "LTM": "IT", "NAUKRI": "IT", "OFSS": "IT",
    # Pharma / Healthcare
    "SUNPHARMA": "PHARMA", "DRREDDY": "PHARMA", "CIPLA": "PHARMA",
    "APOLLOHOSP": "PHARMA", "DIVISLAB": "PHARMA", "TORNTPHARM": "PHARMA",
    "MAXHEALTH": "PHARMA", "BIOCON": "PHARMA", "AUROPHARMA": "PHARMA",
    "ZYDUSLIFE": "PHARMA",
    # Auto
    "MARUTI": "AUTO", "BAJAJ-AUTO": "AUTO", "EICHERMOT": "AUTO",
    "M&M": "AUTO", "TVSMOTOR": "AUTO", "HYUNDAI": "AUTO",
    "HEROMOTOCO": "AUTO", "TATAMOTORS": "AUTO",
    "MOTHERSON": "AUTO", "ESCORTS": "AUTO",
    "TMPV": "AUTO", "TMCV": "AUTO",
    # Oil & Gas / Energy
    "RELIANCE": "ENERGY", "ONGC": "ENERGY", "BPCL": "ENERGY",
    "IOC": "ENERGY", "GAIL": "ENERGY", "PETRONET": "ENERGY",
    "ADANIENT": "ENERGY", "ADANIPORTS": "ENERGY",
    "ADANIGREEN": "ENERGY", "ADANIENSOL": "ENERGY", "ADANIPOWER": "ENERGY",
    # Metals & Mining
    "TATASTEEL": "METALS", "JSWSTEEL": "METALS", "HINDALCO": "METALS",
    "VEDL": "METALS", "COALINDIA": "METALS", "NMDC": "METALS",
    "JINDALSTEL": "METALS", "SAIL": "METALS", "HINDZINC": "METALS",
    # FMCG / Consumer
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "TATACONSUM": "FMCG", "GODREJCP": "FMCG",
    "DMART": "FMCG", "VBL": "FMCG", "UNITDSPR": "FMCG",
    "TRENT": "FMCG", "TITAN": "FMCG", "PAGEIND": "FMCG",
    "ASIANPAINT": "FMCG",
    # Infra / Construction / Power
    "LT": "INFRA", "NTPC": "INFRA", "POWERGRID": "INFRA",
    "TATAPOWER": "INFRA", "ULTRACEMCO": "INFRA", "GRASIM": "INFRA",
    "SHREECEM": "INFRA", "AMBUJACEM": "INFRA", "DLF": "INFRA",
    "OBEROIRLTY": "INFRA", "LODHA": "INFRA",
    # Telecom
    "BHARTIARTL": "TELECOM", "TATACOMM": "TELECOM",
    "INDUSTOWER": "TELECOM",
    # Aviation
    "INDIGO": "OTHER",  # InterGlobe Aviation — airline, not telecom
    # Capital Goods / Engineering
    "ABB": "CAPGOODS", "SIEMENS": "CAPGOODS", "HAL": "CAPGOODS",
    "BEL": "CAPGOODS", "CUMMINSIND": "CAPGOODS", "CGPOWER": "CAPGOODS",
    "BOSCHLTD": "CAPGOODS", "MAZDOCK": "CAPGOODS", "POLYCAB": "CAPGOODS",
    "PIDILITIND": "CAPGOODS", "SOLARINDS": "CAPGOODS",
    # Specialty
    "ETERNAL": "OTHER", "JUBLFOOD": "OTHER", "MRF": "OTHER",
    "NAVINFLUOR": "OTHER", "PIIND": "OTHER", "VOLTAS": "OTHER",
    "CONCOR": "OTHER", "GMRINFRA": "OTHER", "TORNTPOWER": "OTHER",
    "ENRIN": "OTHER", "BALKRISIND": "OTHER", "BHARATFORG": "OTHER",
    "INDHOTEL": "OTHER", "BAJAJHLDNG": "OTHER",
}


def _as_of_dt(as_of: datetime.datetime | None = None) -> datetime.datetime:
    return as_of if as_of is not None else now_ist()


def _candle_date(candle: dict):
    dt = candle.get("date")
    if dt is None:
        return None
    return dt.date() if hasattr(dt, "date") else dt


def filter_session_candles(
    candles: list[dict],
    as_of: datetime.datetime | None = None,
) -> list[dict]:
    session_date = _as_of_dt(as_of).date()
    return [c for c in candles if _candle_date(c) == session_date]


def analyse_candle_snapshot(
    *,
    symbol: str,
    exchange: str,
    candles_15m: list[dict],
    candles_day: list[dict] | None,
    config: type[Config],
    as_of: datetime.datetime | None = None,
    log: Logger | None = None,
) -> dict | None:
    """Run the scanner's candle-pattern + technical score on supplied candles."""
    if len(candles_15m) < 10:
        return None

    now = _as_of_dt(as_of)
    candles_day = candles_day or []
    patterns = detect_all_with_freshness(candles_15m)
    pattern_summary = summarise_signals(patterns)
    current_price = candles_15m[-1]["close"] if candles_15m else 0
    tech = compute_technical_score(
        candles_15m,
        candles_day,
        current_price,
        config=config,
        as_of=now,
    )
    combined_score = pattern_summary["score"] + tech["score"]

    if (
        getattr(config, "PATTERN_CONTRADICTION_PENALTY_ENABLED", False)
        and combined_score != 0
    ):
        try:
            pset = {str(p).upper() for p in pattern_summary.get("patterns", []) or []}
        except Exception:
            pset = set()
        penalty_total = 0.0
        penalty_reasons: list[str] = []
        if pset & INDECISION_PATTERNS:
            p_indecision = float(config.PATTERN_INDECISION_PENALTY)
            if p_indecision > 0:
                penalty_total += p_indecision
                penalty_reasons.append(f"DOJI -{p_indecision:.1f}")
        opposing = (
            BEARISH_REVERSAL_PATTERNS if combined_score > 0
            else BULLISH_REVERSAL_PATTERNS
        )
        conflicts = pset & opposing
        if conflicts:
            p_contra = float(config.PATTERN_CONTRADICTION_PENALTY)
            if p_contra > 0:
                penalty_total += p_contra
                penalty_reasons.append(f"{sorted(conflicts)[0]} -{p_contra:.1f}")
        if penalty_total > 0:
            magnitude = max(0.0, abs(combined_score) - penalty_total)
            new_score = magnitude if combined_score > 0 else -magnitude
            if log and getattr(config, "PATTERN_CONTRADICTION_PENALTY_ENABLED", False):
                log.debug(
                    f"{symbol}: pattern penalty applied "
                    f"({', '.join(penalty_reasons)}) - "
                    f"score {combined_score:+.1f} -> {new_score:+.1f}"
                )
            combined_score = new_score

    rvol = 0.0
    today_candles = filter_session_candles(candles_15m, now)
    if today_candles and candles_day and len(candles_day) >= 5:
        n_today = len(today_candles)
        if n_today >= 4:
            today_vol = sum(c.get("volume", 0) for c in today_candles)
            prorated_vol = today_vol * (25 / n_today)
            if getattr(config, "INTRADAY_VOLUME_BASELINE_ENABLED", False):
                try:
                    share = get_baseline_share(
                        symbol, exchange, now.hour,
                        min_samples=config.INTRADAY_VOLUME_BASELINE_MIN_SAMPLES,
                    )
                    if share and share > 0:
                        prorated_vol = today_vol / share
                except Exception as e:
                    if log:
                        log.warning(
                            f"{symbol}: volume-baseline lookup failed, "
                            f"falling back to linear pro-rating: {e}"
                        )
            recent_vols = [d.get("volume", 0) for d in candles_day[-5:] if d.get("volume", 0) > 0]
            if recent_vols:
                avg_daily_vol = sum(recent_vols) / len(recent_vols)
                if avg_daily_vol > 0:
                    rvol = prorated_vol / avg_daily_vol
                    if rvol > 2.0:
                        combined_score += 1
                    elif rvol < 0.3:
                        combined_score -= 1

    current_vwap = vwap(today_candles) if today_candles else 0
    return {
        "symbol":          symbol,
        "exchange":        exchange,
        "current_price":   current_price,
        "combined_score":  round(combined_score, 1),
        "pattern_summary": pattern_summary,
        "technical":       tech,
        "vwap":            current_vwap,
        "candle_count":    len(candles_15m),
        "rvol":            round(rvol, 2),
    }




class StockScanner:
    """Candle-pattern + indicator scanner. See module docstring."""


    def __init__(
        self,
        config:  type[Config],
        claude:  ClaudeClient,
        zerodha: ZerodhaClient,
        log:     Logger,
    ):
        self.cfg = config
        self.claude = claude
        self.log = log
        self._budget = float(config.MAX_BUDGET_INR)
        self.zerodha = zerodha
        self._cache = CandleCache()

        # ── Score momentum tracking ───────────────────────────────
        # Caches the last scan's composite scores per symbol so we can
        # compute score RoC (Rate of Change) across scans. Detects
        # decelerating setups before entry — a stock accelerating from
        # +5→+8 is better than one decelerating from +10→+7.
        self._prev_scan_scores: dict[str, float] = {}

        # ── Sector-cascade tracking (Roadmap #149) ────────────────
        # `last_sector_momentum`: the per-sector AVERAGE score from
        # the most recent _prefilter_universe pass (computed during
        # the existing sector-momentum block). Manager reads this
        # after each scan to detect fast collapses and tighten SLs
        # of open positions in cascading sectors. Two-tick state:
        # `_prev_sector_momentum` is the snapshot from the previous
        # pass — the cascade check compares prev → last.
        self.last_sector_momentum: dict[str, float] = {}
        self._prev_sector_momentum: dict[str, float] = {}

        # Tape-breadth snapshot — {buys, sells, ratio, tape} stamped
        # by `_prefilter_universe` so the manager can forward it to
        # the engine for the directional-pause breadth-bypass. Cleared
        # (None) on small-sample scans so the engine never bypasses
        # on stale data.
        self.last_tape_breadth: dict | None = None

        # AI's explanation when it returns zero trades from a scan.
        # Surfaced by the manager so a no-trade day is never silent.
        self.last_scan_rationale: str = ""

        # ── Per-candidate telemetry (Roadmap #259) ───────────────
        # Best-effort, write-only, swallows exceptions. Records every
        # V2_MIN-passing candidate to data/trades.db::intraday_candidates
        # with the full feature snapshot + config version/hash. Engine
        # then updates the row to ENTERED/REJECTED, and PerformanceTracker
        # backfills exit_price/pnl/exit_reason on close. The complete
        # rejected-candidate stream removes the selection-bias problem
        # that has blocked replay/backtest work.
        self.telemetry = CandidateTelemetry(self.log)
        # Single timestamp per call to _prefilter_universe so all rows
        # written from one scan share the same scan_time and the engine
        # can match the SCORED row by (date, symbol, side, scan_time).
        self.last_scan_time: str | None = None

        # Optional NIFTY context piggybacked by the manager so the
        # telemetry rows pick up `nifty_trend` and `vix` without us
        # round-tripping through Kite again.
        self.last_nifty_trend: str = ""
        self.last_vix: float | None = None

        # Cleanup old cached data on startup (keep 45 days)
        try:
            cleaned = self._cache.cleanup_old(keep_days=45)
            if cleaned:
                self.log.info(f"Candle cache: cleaned {cleaned} old entries")
        except Exception:
            pass

    def set_budget(self, amount: float):
        """Sets the trading budget (called by PortfolioManager after fetching funds)."""
        self._budget = amount

    # ================================================================
    # GET STOCK UNIVERSE
    # ================================================================

    def get_universe(self) -> list[str]:
        """
        Returns the list of stock symbols the bot is allowed to trade.
        Controlled by Config.SCAN_UNIVERSE.
        """
        universe = self.cfg.SCAN_UNIVERSE.upper()

        if universe == "NIFTY50":
            return list(NIFTY50)
        elif universe == "NIFTY100":
            return list(NIFTY50) + list(NIFTY100_EXTRA)
        elif universe in ("NIFTY150", "NIFTYMIDCAP150", "MIDCAP150"):
            return list(NIFTY50) + list(NIFTY100_EXTRA) + list(NIFTY150_EXTRA)
        elif universe == "NIFTY200":
            return (
                list(NIFTY50)
                + list(NIFTY100_EXTRA)
                + list(NIFTY150_EXTRA)
                + list(NIFTY200_EXTRA)
            )
        elif universe == "CUSTOM":
            return list(self.cfg.CUSTOM_WATCHLIST)
        else:
            self.log.warning(
                f"Unknown SCAN_UNIVERSE '{universe}', falling back to NIFTY50"
            )
            return list(NIFTY50)

    # ================================================================
    # OVERRIDE: SCAN WITH PRE-FILTERING
    # ================================================================

    def scan(self, quotes: dict, nifty_context: str = "", perf_context: str = "", session_context: str = "") -> list[dict]:
        """
        V2 scan: pre-filter with candle math, then send top candidates
        to the configured AI provider (Gemini / GPT / Claude per
        Config.AI_PROVIDER) with enriched technical data.
        """
        provider = self.cfg.AI_PROVIDER.upper()

        # Reset per-scan AI rationale (set when zero trades are returned)
        self.last_scan_rationale = ""

        # Extract Nifty trend from context string for hard filter
        nifty_trend = ""
        if "BEARISH" in nifty_context.upper():
            nifty_trend = "BEARISH"
        elif "BULLISH" in nifty_context.upper():
            nifty_trend = "BULLISH"

        # Step 1: Math-based pre-filter (with Nifty trend hard filter)
        candidates = self._prefilter_universe(quotes, nifty_trend)

        if not candidates:
            self.log.warning("Pre-filter found no candidates with signals")
            # No candidates passed the math pre-filter — no trades this scan
            return []

        # Step 2: Build enriched snapshot for the AI (only candidates)
        snapshot = self._build_enriched_snapshot(candidates, quotes)

        if not snapshot:
            self.log.warning("No valid enriched snapshot — no trades this scan")
            return []

        # Step 3: Send to the AI provider with technical context
        prompt = self._build_v2_scan_prompt(snapshot, nifty_context, perf_context, session_context)

        self.log.info(f"Asking {provider} to pick trades from pre-filtered candidates...")
        try:
            raw = self.claude.call(prompt)
            trades = self._parse_scan_response(raw)
            # Enrich AI trades with indicator snapshot data for learning
            self._enrich_trades_with_indicators(trades, candidates)
            if not trades and not self.last_scan_rationale:
                # Parser found no valid blocks but the AI didn't emit the
                # NO_TRADES_TODAY token — keep the raw reply as the reason.
                cleaned = self._clean_rationale(raw)
                self.last_scan_rationale = cleaned or "no parseable trades returned"
            self.log.success(f"{provider} recommended {len(trades)} trades from {len(candidates)} candidates")
            return trades
        except Exception as e:
            error = ClaudeClient.classify_error(e)
            self.log.error(f"AI scan failed: {error}")
            return []

    # ================================================================
    # MID-DAY REVIEW
    # ================================================================

    def review_positions(
        self,
        open_positions: list[dict],
        quotes: dict,
        day_pnl: float,
        budget_remaining: float,
        nifty_context: str = "",
        closed_positions: list[dict] | None = None,
    ) -> list[dict]:
        """
        Periodic Claude review of open positions + market conditions.
        Called every POSITION_REVIEW_MINUTES during trading hours.

        Claude can recommend:
          - HOLD:  keep position, adjust SL/target
          - EXIT:  close position immediately
          - NEW:   open a new trade (if budget allows)

        Returns list of action dicts:
          {"action": "HOLD|EXIT|NEW", "symbol": ..., ...}
        """
        prompt = self._build_review_prompt(
            open_positions, quotes, day_pnl, budget_remaining, nifty_context,
            closed_positions or [],
        )

        provider = self.cfg.AI_PROVIDER.upper()
        self.log.info(f"{provider} reviewing open positions...")
        try:
            raw = self.claude.call(prompt)
            actions = self._parse_review_response(raw)
            self.log.success(f"{provider} review: {len(actions)} recommendations")
            return actions
        except Exception as e:
            error = ClaudeClient.classify_error(e)
            self.log.warning(f"{provider} review failed: {error} — keeping current positions")
            return []

    # ================================================================
    # PROMPT BUILDERS
    # ================================================================
    # Two main prompts sent to Claude:
    #
    # _build_scan_prompt  — "Find new trades": gives Claude market
    #   snapshot + strict rejection filters + budget constraints.
    #   Claude returns structured trade plans (BUY/SELL with levels).
    #
    # _build_review_prompt — "Manage open positions": gives Claude
    #   portfolio state + R-multiple framework. Claude recommends
    #   HOLD/ADJUST_SL/EXIT for each position.
    #
    # Both prompts contain time-of-day context because intraday
    # strategy changes significantly throughout the trading day.
    # ================================================================

    def _build_snapshot(self, quotes: dict) -> str:
        """
        Converts raw Kite quotes into a compact text table for Claude.
        Only includes stocks that have valid price data.
        """
        lines = []
        for key, q in sorted(quotes.items()):
            price = q.get("last_price", 0)
            if not price or price <= 0:
                continue

            ohlc   = q.get("ohlc", {})
            change = price - ohlc.get("close", price)
            change_pct = (change / ohlc["close"] * 100) if ohlc.get("close") else 0
            volume = q.get("volume", 0)

            # Extract symbol from "NSE:RELIANCE" format
            symbol = key.split(":")[1] if ":" in key else key

            lines.append(
                f"{symbol:<16} "
                f"Rs.{price:>10.2f}  "
                f"Chg: {change_pct:>+6.2f}%  "
                f"O: Rs.{ohlc.get('open', 0):.2f}  "
                f"H: Rs.{ohlc.get('high', 0):.2f}  "
                f"L: Rs.{ohlc.get('low', 0):.2f}  "
                f"PrevClose: Rs.{ohlc.get('close', 0):.2f}  "
                f"Vol: {volume:>12,}"
            )

        return "\n".join(lines)

    def _build_scan_prompt(self, snapshot: str, nifty_context: str = "", perf_context: str = "", session_context: str = "") -> str:
        """
        Builds the pre-market scan prompt.
        Claude is given the full price data and budget constraints,
        and must return trade plans in a strict parseable format.
        """
        today  = now_ist().date().strftime("%B %d, %Y")
        now    = now_ist().strftime("%I:%M %p")
        budget = self._budget
        max_positions  = self.cfg.MAX_POSITIONS
        max_pct        = self.cfg.MAX_POSITION_PCT
        default_sl     = self.cfg.DEFAULT_STOP_LOSS_PCT
        default_target = self.cfg.DEFAULT_TARGET_PCT

        # Time-of-day context
        hour = now_ist().hour
        if hour < 10:
            time_phase = "OPENING (before 10 AM): ORB trades strongest. Wait for 15-min candle close. Avoid chasing opening spikes."
        elif hour < 11:
            time_phase = "MORNING TREND (10-11 AM): Best trending window. Momentum trades have highest success."
        elif hour < 13:
            time_phase = "MIDDAY (11 AM-1 PM): Volume drops. Favour mean-reversion near day's VWAP."
        elif hour < 14:
            time_phase = "AFTERNOON (1-2 PM): European market opens — fresh volatility. Less session time means tighter trade selection but no automatic target compression (the entry target is what you're trading for)."
        else:
            time_phase = "LATE SESSION (after 2 PM): Only high-conviction setups. Targets are honoured at entry — closure is governed by stagnant-exit, momentum kill, and 3:10 PM square-off."

        # R:R floor is uniform across the trading day since #243
        # (collapsed from the deprecated time-tiered floors).
        rr_min_text = f"1:{self.cfg.RR_HARD_FLOOR}"

        return f"""You are an expert Indian stock market intraday trader (NSE) with 15 years of experience in price action, sector rotation, and NSE microstructure.
Today is {today}, current time is {now} IST. All positions MUST be closed by 3:10 PM IST today.
CURRENT TIME PHASE: {time_phase}

BUDGET: Rs.{budget:,} total capital (Rs.{budget // max_positions:,} per slot).
MAX POSITIONS: {max_positions} stocks simultaneously.
MAX PER STOCK: {max_pct}% of budget (= Rs.{budget * max_pct // 100:,} max per stock).
{nifty_context}{perf_context}{session_context}
══════════════════════════════════════════════════════════
HARD REJECTION FILTERS — REJECT any trade that fails even ONE:
══════════════════════════════════════════════════════════
✗ REJECT BUY if stock already UP >2% from PrevClose — move is extended, mean-reversion risk.
✗ REJECT SELL if stock already DOWN >2% from PrevClose — move is extended, bounce risk.
✗ REJECT if Risk:Reward < {rr_min_text} — the system enforces this floor for the current time period. Aim for ≥{self.cfg.RR_TARGET_RATIO}:1 when possible.
✗ REJECT if no clear structural level for stop-loss — no random % stops.
✗ REJECT if stock gapped up/down >1.5% AND is still near the extreme — do NOT chase gaps. If it pulled back toward the gap edge, a pullback entry is acceptable.
✗ REJECT if total position cost across ALL trades would exceed Rs.{budget:,}.

NOTE: The system automatically takes partial profit (33% of qty) at {self.cfg.TRAIL_AFTER_RISK_MULTIPLE}× risk profit and trails SL at {int(self.cfg.TRAIL_STEP_PCT)}% of profit on the remainder. Prefer qty >= 3 so partial exits can work.

══════════════════════════════════════════════════════════
TRADING APPROACH (use price action from the data below):
══════════════════════════════════════════════════════════
WHAT YOU HAVE: OHLC, % change from previous close, and volume for each stock.
Use these to infer setups:

1. OPENING RANGE BREAKOUT (ORB):
   Stock where current price > today's high AND Chg >0.3% = potential breakout BUY.
   Stock where current price < today's low AND Chg <-0.3% = potential breakout SELL.
   Best before 10:30 AM, weakens after 11 AM.

2. MEAN-REVERSION (best setup for small capital):
   • BUY: stock DOWN 0.5-1.5% but Open was near PrevClose (no gap) → price likely reverting to open.
   • SELL: stock UP 0.5-1.5% but Open was near PrevClose → price likely reverting to open.
   • SL on wrong side of the open price. Target = halfway back to PrevClose.

3. RELATIVE STRENGTH/WEAKNESS:
   • Compare each stock's Chg% to NIFTY's Chg%. Stocks significantly outperforming = strong (BUY on pullback). Stocks significantly underperforming = weak (SELL on rally).
   • If NIFTY is DOWN >1.5%: SHORT cyclicals (Banking, Auto, Metals). AVOID BUY except defensives (Pharma, IT, FMCG).
   • If NIFTY is UP >1.5%: BUY cyclicals. AVOID shorting defensives.

4. VOLUME CONFIRMATION:
   High volume (relative to position in sorted list) confirms the move is real.
   Low volume on a breakout = likely false breakout → avoid.

5. STOP-LOSS PLACEMENT:
   BUY: SL just below today's low or open (whichever is tighter and structural). Range: {default_sl}%-{self.cfg.MAX_INTRADAY_SL_PCT}%.
   SELL: SL just above today's high or open. Range: {default_sl}%-{self.cfg.MAX_INTRADAY_SL_PCT}%.
   NEVER use arbitrary fixed % — always reference a structural level (O, H, L, PrevClose).

══════════════════════════════════════════════════════════
COMMON MISTAKES (from actual loss patterns):
══════════════════════════════════════════════════════════
✗ Shorting a stock already down 3-5% hoping it falls more — it BOUNCES.
✗ Buying a stock already up 3-5% hoping it goes higher — it REVERSES.
✗ Shorting a stock that is UP while the market is DOWN — it has relative strength and will snap back.
✗ All trades in same direction on same sector — if sector reverses, ALL lose together.
✗ Over-trading: With Rs.{budget:,} capital, fewer high-conviction trades always beat many mediocre ones. 2-3 good trades > 5 weak ones.
✗ Chasing gaps: wait for the pullback to the gap edge, don't buy/sell at the extremes.

CURRENT MARKET DATA (live prices):
{snapshot}

══════════════════════════════════════════════════════════
RESPONSE FORMAT — STRICTLY FOLLOW:
══════════════════════════════════════════════════════════
One block per trade. No text before or after.
If no trades pass ALL rejection filters, respond with: NO_TRADES_TODAY: <one-line reason why> (e.g. NO_TRADES_TODAY: all candidates extended or below RVol threshold).
Prefer FEWER high-conviction trades (1-2) over many mediocre ones.
The system enforces MAX 2 TRADES PER DAY. Return at most 2.

TRADE 1:
SYMBOL: [NSE stock symbol e.g. RELIANCE]
SIDE: [BUY or SELL]
ENTRY_PRICE: [realistic entry price in Rs., near current price]
STOP_LOSS: [stop-loss price in Rs. — state which structural level: today's L/H, Open, or PrevClose]
TARGET: [target price in Rs. — must be at least {self.cfg.RR_TARGET_RATIO}× the SL distance from entry]
QTY: [number of shares — must fit within budget constraints]
RATIONALE: [2-3 sentences: (1) what setup (ORB/mean-reversion/relative strength), (2) structural SL level, (3) R:R ratio. If Chg >2%, explain why it's NOT an extended-move violation.]
---
TRADE 2:
...
---
===END===
"""

    def _build_review_prompt(
        self,
        positions: list[dict],
        quotes: dict,
        day_pnl: float,
        budget_remaining: float,
        nifty_context: str = "",
        closed_positions: list[dict] | None = None,
    ) -> str:
        """
        Builds the periodic review prompt for open positions.
        """
        today = now_ist().date().strftime("%B %d, %Y")
        now   = now_ist().strftime("%I:%M %p")

        budget         = self._budget
        max_positions  = self.cfg.MAX_POSITIONS
        max_pct        = self.cfg.MAX_POSITION_PCT
        max_per        = budget * max_pct // 100
        max_reentries  = self.cfg.MAX_REENTRIES_PER_STOCK

        # Calculate minutes until square-off for time-pressure context
        now_dt = now_ist()
        square_off = now_dt.replace(
            hour=self.cfg.SQUARE_OFF_HOUR,
            minute=self.cfg.SQUARE_OFF_MINUTE,
            second=0, microsecond=0,
        )
        mins_left = max(0, (square_off - now_dt).total_seconds() / 60)

        pos_text = ""
        for p in positions:
            key = f"NSE:{p['symbol']}"
            q   = quotes.get(key, {})
            current_price = q.get("last_price", p.get("entry_price", 0))
            entry = p.get("entry_price", 0)
            pnl = (current_price - entry) * p.get("qty", 0)
            if p.get("side") == "SELL":
                pnl = (entry - current_price) * p.get("qty", 0)

            # Calculate risk and R-multiple for Claude's context
            sl = p.get("stop_loss", entry)
            risk_per_share = abs(entry - sl) if sl else 0
            r_multiple = (pnl / (risk_per_share * p.get("qty", 1))) if risk_per_share > 0 else 0

            pos_text += (
                f"  {p['symbol']}: {p['side']} {p['qty']} shares @ Rs.{entry:.2f}  "
                f"Current: Rs.{current_price:.2f}  P&L: Rs.{pnl:.2f} ({r_multiple:+.1f}R)  "
                f"SL: Rs.{p.get('stop_loss', 'N/A')}  Target: Rs.{p.get('target_price', 'N/A')}\n"
            )

        # Build closed/failed trade history so Claude doesn't re-enter losers
        closed_text = ""
        reentry_counts: dict[str, int] = {}
        for cp in (closed_positions or []):
            sym = cp.get("symbol", "")
            reentry_counts[sym] = reentry_counts.get(sym, 0) + 1
            closed_text += (
                f"  {sym}: {cp.get('side', '?')} {cp.get('qty', 0)} shares @ Rs.{cp.get('entry_price', 0):.2f}  "
                f"Exit: Rs.{cp.get('exit_price', 0):.2f}  P&L: Rs.{cp.get('pnl', 0):.2f}  "
                f"Reason: {cp.get('exit_reason', '?')}\n"
            )

        # Build list of stocks at re-entry limit
        blocked_stocks = [
            sym for sym, count in reentry_counts.items()
            if max_reentries > 0 and count >= max_reentries
        ]
        blocked_text = (
            f"\nBLOCKED FROM RE-ENTRY (already traded {max_reentries}x today): "
            + ", ".join(blocked_stocks)
            if blocked_stocks else ""
        )

        return f"""You are an expert Indian stock market intraday trader (NSE) specialising in position management with 15 years of experience.
Today is {today}, current time is {now} IST. Market closes at 3:30 PM, we square off at 3:10 PM.
TIME REMAINING: {mins_left:.0f} minutes until square-off.
{nifty_context}
CURRENT OPEN POSITIONS:
{pos_text if pos_text else "  (none)"}

CLOSED TRADES TODAY:
{closed_text if closed_text else "  (none)"}

DAY P&L SO FAR: Rs.{day_pnl:,.2f}
REMAINING BUDGET: Rs.{budget_remaining:,.2f}
MAX POSITIONS: {max_positions} stocks simultaneously.
MAX PER STOCK: {max_pct}% of Rs.{budget:,} = Rs.{max_per:,} max per stock.
{blocked_text}

══════════════════════════════════════════════════════════
POSITION MANAGEMENT FRAMEWORK (R-multiple based):
══════════════════════════════════════════════════════════
AUTOMATIC ACTIONS (handled by the system — do NOT suggest these):
  • At {self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R profit: system auto-exits 33% of qty and begins trailing SL (you'll see reduced qty in positions above).
  • Trailing SL: system continuously moves SL to lock in {int(self.cfg.TRAIL_STEP_PCT)}% of current profit. This is automatic.
  • You should only suggest ADJUST_SL to tighten BEYOND what the system already set, never to loosen.

YOUR ROLE — Use the R-multiple to guide ADDITIONAL decisions:
  Deep loser (<-0.5R): Trade thesis is FAILING. Unless price is clearly reversing back in your favour, EXIT.
  Losing (-0.5R to 0R): Still within initial risk. Check if NIFTY trend still supports trade direction. If yes → HOLD. If NIFTY reversed → EXIT.
  Breakeven (0R to +0.5R): HOLD and let it develop.
  Small winner (+0.5R to +{self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R): HOLD — system will auto-take partial profit at {self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R.
  Good winner (+{self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R to +2R): Partial profit already taken by system. Remaining position has trailing SL. HOLD unless trend has clearly reversed.
  Large winner (>+2R): System trailing is active. Consider ADJUST_TARGET closer if time is short (<60 min remain).

══════════════════════════════════════════════════════════
REVIEW RULES — MUST FOLLOW:
══════════════════════════════════════════════════════════
1. TRAILING STOP (handled automatically at {int(self.cfg.TRAIL_STEP_PCT)}% of profit):
   Only suggest ADJUST_SL to tighten MORE than the auto-trail (e.g. due to NIFTY reversal).
   *** NEVER suggest loosening SL (moving it further from current price). ***

2. TIME MANAGEMENT ({mins_left:.0f} min remaining):
   • >120 min: Full discretion. Manage positions normally.
   • 60-120 min: No new trades unless strong setup.
   • 30-60 min: EXIT underwater positions. HOLD only profitable positions with strong momentum.
   • <30 min: EXIT ALL positions unless within 0.3% of target.
   NOTE: The system applies a {self.cfg.TARGET_DECAY_PCT:.0f}% time-decay target compression on OPEN positions after {self.cfg.TARGET_DECAY_AFTER_HOUR}:00. Targets shown above reflect this adjustment. Only suggest ADJUST_TARGET for trend-based reasons, not for time alone.

3. CUT LOSERS: Positions underwater that have been drifting sideways for 2+ review cycles → EXIT. Dead money is worse than a small loss.

4. DO NOT AVERAGE DOWN on losing positions. Only suggest NEW trades for fresh setups.

5. NIFTY ALIGNMENT: If NIFTY has turned against your trade direction since entry, tighten SL to within 0.5% of current price.

6. RE-ENTRY: DO NOT re-enter a stock that hit STOP_LOSS today unless in the OPPOSITE direction. Check CLOSED TRADES above.

7. PROTECT WINNERS: DO NOT exit profitable positions just because of "time pressure" when 30+ min remain AND the trend is intact. Tighten SL instead of exiting. Only exit if trend has clearly reversed or target is unreachable.

8. NEW TRADES (strict criteria):
   • Only if 60+ minutes remain
   • Stock must NOT already be extended (within ±2% of previous close)
   • Budget must be available
   • QTY × ENTRY_PRICE ≤ min(REMAINING BUDGET Rs.{budget_remaining:,.0f}, MAX PER STOCK Rs.{max_per:,})
   • Prefer FEWER new trades — 1 good trade > 2 mediocre trades

Review each position. For each, respond with EXACTLY this format:

REVIEW 1:
SYMBOL: [symbol]
ACTION: [HOLD | EXIT | ADJUST_SL | ADJUST_TARGET]
NEW_SL: [new stop-loss price if ADJUST_SL, otherwise leave blank]
NEW_TARGET: [new target price if ADJUST_TARGET, otherwise leave blank]
REASON: [1-2 sentences — reference the R-multiple, time remaining, and NIFTY alignment in your decision]
---

For new trades (optional, strict criteria above):
NEW_TRADE:
SYMBOL: [symbol]
SIDE: [BUY or SELL]
ENTRY_PRICE: [price]
STOP_LOSS: [price — based on structural level]
TARGET: [price — reduced for time remaining]
QTY: [quantity — MUST satisfy budget constraint above]
RATIONALE: [1-2 sentences — setup type, R:R ratio, why worth the late-day risk]
---
===END===
"""

    # ================================================================
    # RESPONSE PARSERS
    # ================================================================

    def _clean_rationale(self, text: str, max_len: int = 280) -> str:
        """
        Normalises an AI free-text reason for single-line display:
        collapses internal whitespace/newlines, strips stray template
        tokens, and truncates to a readable length with an ellipsis.
        """
        if not text:
            return ""
        # Drop any structured block leftovers so only prose remains
        cleaned = re.sub(r"(?im)^(SYMBOL|SIDE|ENTRY_PRICE|STOP_LOSS|TARGET|QTY|RATIONALE|TRADE\s*\d+)\s*:.*$", " ", text)
        cleaned = cleaned.replace("===END===", " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -—:")
        if len(cleaned) > max_len:
            cleaned = cleaned[:max_len].rstrip() + "…"
        return cleaned

    def _parse_scan_response(self, raw: str) -> list[dict]:
        """
        Parses Claude's trade recommendations from the pre-market scan.
        Returns a list of trade plan dicts.

        Tolerant of minor format variations (extra spaces, missing fields).
        Validates that total cost doesn't exceed budget.
        """
        text = raw.strip()

        if "NO_TRADES_TODAY" in text:
            # Capture any one-line reason the AI gave after the token
            # so the manager can surface why nothing was picked.
            m = re.search(r"NO_TRADES_TODAY\s*:?\s*(.+)", text, re.DOTALL)
            reason = m.group(1).strip() if m and m.group(1).strip() else ""
            reason = self._clean_rationale(reason)
            self.last_scan_rationale = reason
            if reason:
                self.log.info(f"AI says no trades today — {reason}")
            else:
                self.log.info("AI says: no good trades today")
            return []

        trades = []
        # Split by the --- separator to get individual trade blocks
        blocks = re.split(r'-{3,}', text)

        for block in blocks:
            block = block.strip()
            if not block or "===END===" in block:
                continue

            trade = self._extract_trade_fields(block)
            if trade:
                trades.append(trade)

        # Validate total cost doesn't exceed budget
        trades = self._validate_budget(trades)
        return trades

    def _extract_trade_fields(self, block: str) -> dict | None:
        """
        Extracts structured fields from one trade block.
        Returns None if critical fields are missing.
        """
        def extract(field: str) -> str:
            pattern = rf"(?i){field}\s*:\s*(.+)"
            match = re.search(pattern, block)
            return match.group(1).strip() if match else ""

        symbol = extract("SYMBOL")
        side   = extract("SIDE").upper()
        entry  = extract("ENTRY_PRICE")
        sl     = extract("STOP_LOSS")
        target = extract("TARGET")
        qty    = extract("QTY")
        reason = extract("RATIONALE") or extract("REASON")

        # All critical fields must be present
        if not all([symbol, side, entry, qty]):
            return None

        # Side must be exactly BUY or SELL
        if side not in ("BUY", "SELL"):
            return None

        entry_price  = _parse_price(entry)
        stop_loss    = _parse_price(sl) if sl else 0.0
        target_price = _parse_price(target) if target else 0.0
        quantity     = _parse_int(qty)

        if entry_price <= 0 or quantity <= 0:
            return None

        # Apply default SL/target if Claude didn't provide them
        if stop_loss <= 0:
            sl_pct = self.cfg.DEFAULT_STOP_LOSS_PCT / 100
            stop_loss = round(
                entry_price * (1 - sl_pct) if side == "BUY"
                else entry_price * (1 + sl_pct),
                2
            )

        if target_price <= 0:
            tgt_pct = self.cfg.DEFAULT_TARGET_PCT / 100
            target_price = round(
                entry_price * (1 + tgt_pct) if side == "BUY"
                else entry_price * (1 - tgt_pct),
                2
            )

        # Validate SL/target are on the correct side of entry
        if side == "BUY":
            if stop_loss >= entry_price:
                sl_pct = self.cfg.DEFAULT_STOP_LOSS_PCT / 100
                stop_loss = round(entry_price * (1 - sl_pct), 2)
            if target_price <= entry_price:
                tgt_pct = self.cfg.DEFAULT_TARGET_PCT / 100
                target_price = round(entry_price * (1 + tgt_pct), 2)
        else:  # SELL
            if stop_loss <= entry_price:
                sl_pct = self.cfg.DEFAULT_STOP_LOSS_PCT / 100
                stop_loss = round(entry_price * (1 + sl_pct), 2)
            if target_price >= entry_price:
                tgt_pct = self.cfg.DEFAULT_TARGET_PCT / 100
                target_price = round(entry_price * (1 - tgt_pct), 2)

        return {
            "symbol":       symbol,
            "exchange":     "NSE",
            "side":         side,
            "entry_price":  round(entry_price, 2),
            "stop_loss":    round(stop_loss, 2),
            "target_price": round(target_price, 2),
            "qty":          quantity,
            "rationale":    reason,
            "status":       "PENDING",   # PENDING → OPEN → CLOSED
        }

    def _validate_budget(self, trades: list[dict]) -> list[dict]:
        """
        Ensures total trade value doesn't exceed budget.
        Reduces qty to fit when possible, drops only as a last resort.
        """
        budget    = self._budget
        max_pct   = self.cfg.MAX_POSITION_PCT / 100
        max_per   = budget * max_pct
        allocated = 0
        valid     = []

        for t in trades:
            cost = t["entry_price"] * t["qty"]
            entry = t["entry_price"]

            # Check per-stock limit — reduce qty to fit if needed
            if cost > max_per and entry > 0:
                new_qty = int(max_per / entry)
                if new_qty >= 1:
                    self.log.warning(
                        f"{t['symbol']}: {t['qty']}x @ Rs.{entry:.2f} = Rs.{cost:,.0f} exceeds "
                        f"per-stock limit Rs.{max_per:,.0f}. Reducing qty to {new_qty}"
                    )
                    t["qty"] = new_qty
                    cost = entry * new_qty
                else:
                    self.log.warning(
                        f"Dropping {t['symbol']}: Rs.{cost:,.0f} exceeds "
                        f"per-stock limit of Rs.{max_per:,.0f} and min qty is 1"
                    )
                    continue

            # Check total budget — reduce qty to fit if needed
            if allocated + cost > budget and entry > 0:
                remaining = budget - allocated
                new_qty = int(remaining / entry)
                if new_qty >= 1:
                    self.log.warning(
                        f"{t['symbol']}: {t['qty']}x @ Rs.{entry:.2f} = Rs.{cost:,.0f} exceeds "
                        f"remaining budget Rs.{remaining:,.0f}. Reducing qty to {new_qty}"
                    )
                    t["qty"] = new_qty
                    cost = entry * new_qty
                else:
                    self.log.warning(
                        f"Dropping {t['symbol']}: Rs.{cost:,.0f} would exceed "
                        f"total budget of Rs.{budget:,} (only Rs.{remaining:,.0f} left)"
                    )
                    continue

            allocated += cost
            valid.append(t)

        if valid:
            self.log.info(
                f"Total allocated: Rs.{allocated:,.0f} / Rs.{budget:,} "
                f"({allocated / budget * 100:.1f}%)"
            )

        return valid

    def _parse_review_response(self, raw: str) -> list[dict]:
        """
        Parses Claude's review response into action dicts.
        Each action has: symbol, action, new_sl, new_target, reason.
        New trade suggestions are also parsed.
        """
        text = raw.strip()
        actions = []
        blocks = re.split(r'-{3,}', text)

        for block in blocks:
            block = block.strip()
            if not block or "===END===" in block:
                continue

            # Check if it's a new trade suggestion
            if "NEW_TRADE" in block.upper() or "NEW TRADE" in block.upper():
                trade = self._extract_trade_fields(block)
                if trade:
                    trade["action"] = "NEW"
                    actions.append(trade)
                continue

            # Otherwise it's a position review
            def extract(field: str) -> str:
                pattern = rf"(?i){field}\s*:\s*(.+)"
                match = re.search(pattern, block)
                return match.group(1).strip() if match else ""

            symbol = extract("SYMBOL")
            action = extract("ACTION").upper()
            new_sl = extract("NEW_SL")
            new_target = extract("NEW_TARGET")
            reason = extract("REASON")

            if symbol and action:
                actions.append({
                    "symbol":     symbol,
                    "action":     action,
                    "new_sl":     _parse_price_optional(new_sl),
                    "new_target": _parse_price_optional(new_target),
                    "reason":     reason,
                })

        return actions

    # ================================================================
    # CANDLE DATA FETCHER
    # ================================================================

    def _fetch_intraday_candles(
        self,
        symbol: str,
        exchange: str = "NSE",
        interval: str = "15minute",
        days_back: int = 2,
    ) -> list[dict]:
        """
        Fetches intraday candles for one stock.
        Previous days' candles are served from cache; today's are always
        fetched live from Zerodha (they update every 15 min).
        Returns list of candle dicts: {date, open, high, low, close, volume}.
        Returns empty list on failure (non-blocking).
        """
        today = now_ist().date()

        # Dynamic lookback: start with days_back, widen up to +3 extra
        # days if cache returns nothing (handles weekends, holidays,
        # long weekends like Fri holiday + Sat + Sun).
        from_date = today - datetime.timedelta(days=days_back)
        cached = []
        if days_back > 0:
            for extra in range(4):  # try 0, +1, +2, +3 extra days
                from_date = today - datetime.timedelta(days=days_back + extra)
                cached = self._cache.get_cached_candles(
                    symbol, exchange, interval, from_date, today,
                )
                if cached:
                    break

        if not cached and days_back > 0:
            # Cold cache — single Zerodha call for full range (avoids
            # a wasted live-only call that the fallback would duplicate)
            try:
                from_dt = datetime.datetime.combine(
                    from_date, datetime.time(9, 0),
                )
                all_candles = self.zerodha.get_historical(
                    symbol, exchange, from_dt, now_ist(), interval,
                )
                if all_candles:
                    self._cache.store_candles(symbol, exchange, interval, all_candles)
                    return all_candles
            except Exception as e:
                self.log.info(f"Candle fetch failed for {symbol}: {e}")
            return []

        # Cache hit — check for corporate action (split/bonus) before using
        if cached:
            last_cached_close = cached[-1]["close"]
            try:
                today_start = datetime.datetime.combine(today, datetime.time(9, 0))
                now = now_ist()
                live = self.zerodha.get_historical(
                    symbol, exchange, today_start, now, interval,
                )
                live = live if live else []
            except Exception:
                live = []

            # Detect price discontinuity (>35% gap = likely corporate action)
            if live and last_cached_close > 0:
                first_live_open = live[0]["open"]
                gap = abs(first_live_open - last_cached_close) / last_cached_close
                if gap > 0.35:
                    # Likely split/bonus — invalidate cache, refetch everything
                    self._cache.invalidate_symbol(symbol, exchange)
                    try:
                        from_dt = datetime.datetime.combine(
                            from_date, datetime.time(9, 0),
                        )
                        all_candles = self.zerodha.get_historical(
                            symbol, exchange, from_dt, now_ist(), interval,
                        )
                        if all_candles:
                            self._cache.store_candles(symbol, exchange, interval, all_candles)
                            return all_candles
                    except Exception:
                        pass
                    return live

            return cached + live

        return []

    def _fetch_daily_candles(
        self,
        symbol: str,
        exchange: str = "NSE",
        days_back: int = 30,
    ) -> list[dict]:
        """
        Fetches daily candles for trend context.
        Previous days' candles are served from cache; only missing
        dates are fetched from Zerodha.
        """
        today = now_ist().date()
        from_date = today - datetime.timedelta(days=days_back)

        # Check cache for previous days
        cached = self._cache.get_cached_candles(
            symbol, exchange, "day", from_date, today,
        )

        if cached:
            # Cache has data — return it (daily candles for past days don't change)
            # Corporate action detection is handled by _fetch_intraday_candles
            # (called first in _analyse_stock) which invalidates ALL intervals
            # for the symbol if a >35% price gap is detected.
            return cached

        # Nothing cached — fetch full range from Zerodha and cache
        try:
            candles = self.zerodha.get_historical(
                symbol, exchange, from_date, today, "day",
            )
            if candles:
                self._cache.store_candles(symbol, exchange, "day", candles)
                # Exclude today's partial daily candle (matches cached behaviour)
                return [
                    c for c in candles
                    if (c["date"].date() if hasattr(c["date"], "date") else c["date"]) < today
                ]
            return []
        except Exception:
            return []

    # ================================================================
    # TECHNICAL ANALYSIS FOR ONE STOCK
    # ================================================================

    def _analyse_stock(
        self,
        symbol: str,
        exchange: str = "NSE",
        candles_15m: list[dict] | None = None,
        candles_day: list[dict] | None = None,
        as_of: datetime.datetime | None = None,
    ) -> dict | None:
        """
        Runs full technical analysis on one stock:
        - 15-min candle patterns (with freshness decay + volume confirmation)
        - Technical indicators (EMA, RSI, VWAP, SuperTrend, prev-day S&R)
        - Relative Volume (RVol) scoring
        - Daily candle context

        Returns a scored dict or None if insufficient data.
        """
        if candles_15m is None:
            candles_15m = self._fetch_intraday_candles(symbol, exchange, "15minute", days_back=3)
        if candles_day is None:
            candles_day = self._fetch_daily_candles(symbol, exchange, days_back=30)

        return analyse_candle_snapshot(
            symbol=symbol,
            exchange=exchange,
            candles_15m=candles_15m,
            candles_day=candles_day,
            config=self.cfg,
            as_of=as_of,
            log=self.log,
        )

    def _filter_today_candles(
        self,
        candles: list[dict],
        as_of: datetime.datetime | None = None,
    ) -> list[dict]:
        """Filters candles to only today's intraday data (for VWAP)."""
        return filter_session_candles(candles, as_of)

    # ================================================================
    # PRE-FILTER SCAN (MATH-BASED, FREE)
    # ================================================================

    def _prefilter_universe(
        self,
        quotes: dict,
        nifty_trend: str = "",
        min_score_override: float | None = None,
        as_of: datetime.datetime | None = None,
    ) -> list[dict]:
        """
        Analyses all stocks in the universe using candle patterns
        and technical indicators. Returns the top candidates ranked
        by combined score.

        If nifty_trend is "BEARISH", BUY signals need a higher score
        threshold (≥3 instead of default). Vice versa for "BULLISH"
        — SELL signals need |score| ≥3. This prevents trading against
        the broad market direction with weak signals.

        Stocks below MIN_SCORE are filtered out entirely.
        Both bullish AND bearish signals pass through (since we can
        trade both directions in intraday).
        """
        universe = self.get_universe()
        self.log.info(f"Pre-filter: analysing {len(universe)} stocks with candle patterns...")

        # ── Price range filter ────────────────────────────────────
        # Skip stocks outside SCAN_MIN_PRICE / SCAN_MAX_PRICE range.
        # Eliminates illiquid penny stocks and stocks too expensive to size.
        min_price = self.cfg.SCAN_MIN_PRICE
        max_price = self.cfg.SCAN_MAX_PRICE
        if max_price <= 0:
            # Auto from budget: price must allow at least 1 share
            # within the per-stock capital cap.
            max_price = self._budget * self.cfg.MAX_POSITION_PCT / 100

        price_filtered = []
        dropped_price = 0
        dropped_no_quote = 0
        missing_quote_symbols = []

        for symbol in universe:
            key = f"NSE:{symbol}"
            q = quotes.get(key, {})
            ltp = q.get("last_price", 0) if isinstance(q, dict) else 0
            if ltp <= 0:
                missing_quote_symbols.append(symbol)

        if missing_quote_symbols:
            retry_quotes = self.zerodha.get_quotes_safe(
                [{"symbol": s, "exchange": "NSE"} for s in missing_quote_symbols],
                max_retries=3,
            ) or {}
            recovered = 0
            for symbol in missing_quote_symbols:
                key = f"NSE:{symbol}"
                q = retry_quotes.get(key, {})
                ltp = q.get("last_price", 0) if isinstance(q, dict) else 0
                if ltp > 0:
                    quotes[key] = q
                    recovered += 1
            if recovered:
                self.log.info(
                    f"  Price filter: recovered {recovered}/"
                    f"{len(missing_quote_symbols)} missing quotes on retry"
                )

        for symbol in universe:
            key = f"NSE:{symbol}"
            q = quotes.get(key, {})
            ltp = q.get("last_price", 0) if isinstance(q, dict) else 0
            if ltp <= 0:
                dropped_no_quote += 1
                continue
            if ltp < min_price or ltp > max_price:
                dropped_price += 1
                continue
            price_filtered.append(symbol)

        if dropped_no_quote:
            self.log.warning(
                f"  Price filter: skipped {dropped_no_quote} stocks with "
                f"missing live quotes after 3 attempts"
            )

        if dropped_price:
            self.log.info(
                f"  Price filter: dropped {dropped_price} stocks outside "
                f"Rs.{min_price:.0f}-{max_price:.0f} range"
            )

        # Earnings/results-day blackout (Roadmap #167).
        # Skip names announcing results today — Q1-Q4 result days
        # produce 3-5 % gap moves intraday that no technical setup
        # can predict. Reads from a user-maintained config dict
        # (EARNINGS_BLACKOUT_SYMBOLS_<year>: dict[str, list[str]]
        # keyed by "YYYY-MM-DD" → list of NSE symbols). Empty dict
        # means no blackout active that day. Kill-switch:
        # EARNINGS_BLACKOUT_ENABLED.
        if getattr(self.cfg, "EARNINGS_BLACKOUT_ENABLED", True):
            try:
                today_str = _as_of_dt(as_of).strftime("%Y-%m-%d")
                year = today_str[:4]
                cal = getattr(self.cfg, f"EARNINGS_BLACKOUT_SYMBOLS_{year}", {}) or {}
                blackout_today = set(cal.get(today_str, []))
                if blackout_today:
                    before = len(price_filtered)
                    price_filtered = [s for s in price_filtered if s not in blackout_today]
                    dropped_earn = before - len(price_filtered)
                    if dropped_earn:
                        self.log.info(
                            f"  Earnings blackout: skipped {dropped_earn} "
                            f"symbol(s) announcing results today"
                        )
            except Exception as e:
                self.log.debug(f"Earnings blackout check failed: {e}")

        scored = []
        drift_warn_count = 0
        drift_check_enabled = bool(getattr(self.cfg, "VWAP_DRIFT_CHECK_ENABLED", True))
        drift_warn_pct = float(getattr(self.cfg, "VWAP_DRIFT_WARN_PCT", 0.30))
        for i, symbol in enumerate(price_filtered):
            # Progress indicator — every 25% of universe
            quarter = max(1, len(price_filtered) // 4)
            if (i + 1) % quarter == 0 or i + 1 == len(price_filtered):
                self.log.info(f"  Analysing... {i + 1}/{len(price_filtered)}")

            result = self._analyse_stock(symbol, as_of=as_of)
            if result:
                scored.append(result)

                # ── Broker session-VWAP drift sanity check (Roadmap #268) ──
                # Pure observability: compare exchange-truth `average_price`
                # from the live quote against the VWAP we just computed
                # from cached candles. A divergence > VWAP_DRIFT_WARN_PCT
                # means our cache is stale or has gaps, and the three
                # production VWAP gates (#34/#125/#228) will evaluate
                # against the wrong reference. No gate logic touched
                # here — we only WARN so the operator can trace the
                # candle-cache pipeline before it bleeds money.
                if drift_check_enabled and drift_warn_pct > 0:
                    try:
                        q = quotes.get(f"NSE:{symbol}", {}) or {}
                        broker_vwap = float(q.get("average_price", 0) or 0)
                        our_vwap = float(result.get("vwap", 0) or 0)
                        if broker_vwap > 0 and our_vwap > 0:
                            delta_pct = abs(our_vwap - broker_vwap) / broker_vwap * 100
                            if delta_pct > drift_warn_pct:
                                drift_warn_count += 1
                                self.log.warning(
                                    f"{symbol}: VWAP drift "
                                    f"broker=Rs.{broker_vwap:.2f} "
                                    f"ours=Rs.{our_vwap:.2f} "
                                    f"(delta {delta_pct:.2f}%) — "
                                    f"candle cache may be stale"
                                )
                    except Exception as e:
                        self.log.debug(
                            f"{symbol}: VWAP-drift check skipped ({type(e).__name__}: {e})"
                        )

        self.log.info(f"  Analysed {len(scored)} stocks with sufficient candle data")
        if drift_check_enabled and drift_warn_count > 0:
            self.log.warning(
                f"  VWAP drift sanity check: {drift_warn_count} symbol(s) "
                f"diverged > {drift_warn_pct:.2f}% from broker session VWAP "
                f"(see WARN lines above; investigate candle-cache pipeline "
                f"if rate stays elevated on healthy market days)"
            )

        # Filter out weak signals below MIN_SCORE threshold
        min_score = min_score_override if min_score_override is not None else self.cfg.MIN_SCORE
        passed_score = []
        for s in scored:
            if abs(s["combined_score"]) >= min_score:
                passed_score.append(s)

        dropped_score = len(scored) - len(passed_score)
        if dropped_score:
            self.log.info(f"  Score filter: dropped {dropped_score} stocks below |score| {min_score}")

        # Tape-breadth filter. Count BUY vs SELL after the score floor;
        # when the minority side is at/below the BEARISH/BULLISH ratio
        # of {BUY+SELL} the tape is one-directional, so penalise the
        # minority's |score|. Skipped on small samples. The snapshot
        # is stamped on `self.last_tape_breadth` regardless of penalty
        # firing so the engine can consult it for the breadth-bypass;
        # cleared to None on small samples to prevent stale-data bypass.
        self.last_tape_breadth = None
        if (
            getattr(self.cfg, "BREADTH_FILTER_ENABLED", True)
            and len(passed_score) >= int(self.cfg.BREADTH_MIN_CANDIDATES)
        ):
            n_buys  = sum(1 for s in passed_score if s["combined_score"] > 0)
            n_sells = sum(1 for s in passed_score if s["combined_score"] < 0)
            total   = n_buys + n_sells
            buy_ratio  = (n_buys / total) if total > 0 else 0.5
            sell_ratio = (n_sells / total) if total > 0 else 0.5
            penalty    = float(self.cfg.BREADTH_PENALTY)
            tape       = "NEUTRAL"
            penalised  = 0
            if buy_ratio <= float(self.cfg.BREADTH_BEARISH_BUY_RATIO):
                tape = "BEARISH"
                for s in passed_score:
                    if s["combined_score"] > 0:
                        mag = max(0.0, abs(s["combined_score"]) - penalty)
                        s["combined_score"] = round(mag, 1)
                        penalised += 1
            elif sell_ratio <= float(self.cfg.BREADTH_BULLISH_SELL_RATIO):
                tape = "BULLISH"
                for s in passed_score:
                    if s["combined_score"] < 0:
                        mag = max(0.0, abs(s["combined_score"]) - penalty)
                        s["combined_score"] = round(-mag, 1)
                        penalised += 1
            if penalised:
                self.log.info(
                    f"  Tape-breadth filter ({tape}): "
                    f"BUYs={n_buys} SELLs={n_sells} "
                    f"({buy_ratio*100:.0f}%/{sell_ratio*100:.0f}%) — "
                    f"penalised {penalised} counter-tape candidate(s) "
                    f"by -{penalty:.1f} |score|"
                )
            else:
                self.log.debug(
                    f"  Tape-breadth: BUYs={n_buys} SELLs={n_sells} "
                    f"({buy_ratio*100:.0f}%/{sell_ratio*100:.0f}%) — "
                    f"{tape}, no penalty applied"
                )
            # Snapshot is PRE-penalty so the engine's breadth-bypass
            # sees genuine paused-side strength, not the suppressed remnant.
            self.last_tape_breadth = {
                "buys":  n_buys,
                "sells": n_sells,
                "ratio": round(buy_ratio, 3),
                "tape":  tape,
            }
            # Re-apply score floor — penalised candidates may now
            # have fallen below MIN_SCORE and should be dropped
            # before sector momentum / nifty trend filters.
            before = len(passed_score)
            passed_score = [
                s for s in passed_score if abs(s["combined_score"]) >= min_score
            ]
            dropped_post = before - len(passed_score)
            if dropped_post:
                self.log.info(
                    f"  Score filter (post-breadth): dropped {dropped_post} more"
                )

        # Sector momentum: compute average score per sector.
        # Stocks from sectors where 3+ stocks agree on direction get
        # a small score boost (sector is trending). This is applied
        # BEFORE the Nifty trend hard filter so the boost helps marginal
        # candidates survive the filter.
        sector_scores: dict[str, list[float]] = {}
        for s in passed_score:
            sector = SECTOR_MAP.get(s["symbol"], "OTHER")
            sector_scores.setdefault(sector, []).append(s["combined_score"])

        sector_momentum_applied = 0
        for s in passed_score:
            sector = SECTOR_MAP.get(s["symbol"], "OTHER")
            scores_in_sector = sector_scores.get(sector, [])
            if len(scores_in_sector) >= 3:
                same_dir = sum(
                    1 for sc in scores_in_sector
                    if (sc > 0) == (s["combined_score"] > 0)
                )
                if same_dir >= 3:
                    # Sector is trending in this direction — small boost
                    boost = 0.5 if s["combined_score"] > 0 else -0.5
                    s["combined_score"] = round(s["combined_score"] + boost, 1)
                    sector_momentum_applied += 1

        if sector_momentum_applied:
            self.log.info(
                f"  Sector momentum: boosted {sector_momentum_applied} stocks "
                f"in trending sectors (+/-0.5)"
            )

        # Publish per-sector AVERAGE score for the cascade-exit
        # gate (#149). Two-tick rolling window so the manager can
        # spot a fast collapse (prev > 0, last << 0).
        sector_avg_now: dict[str, float] = {}
        for sec, sc_list in sector_scores.items():
            if sc_list:
                sector_avg_now[sec] = sum(sc_list) / len(sc_list)
        self._prev_sector_momentum = self.last_sector_momentum
        self.last_sector_momentum = sector_avg_now

        # Sector-rank directional bias (Roadmap #215).
        # Rank ALL sectors with ≥1 candidate by their AVERAGE
        # combined_score at scan time — top-ranked sectors are the
        # day's most-bullish, bottom-ranked the most-bearish. Then
        # nudge each candidate's |score| by
        #   bias = (mid_rank - rank_of_my_sector) * STEP, clamped to MAX
        # sign-aware: BUYs in top-ranked sectors get a positive nudge,
        # SELLs in bottom-ranked sectors get a deeper-negative nudge.
        # Counter-rank candidates are penalised. Operates on magnitude
        # — sign is preserved (round(±mag,1) keeps direction). Skipped
        # when fewer than SECTOR_RANK_MIN_SECTORS distinct sectors
        # are present (sample too small to be meaningful).
        if (
            getattr(self.cfg, "SECTOR_RANK_BIAS_ENABLED", True)
            and len(sector_scores) >= int(self.cfg.SECTOR_RANK_MIN_SECTORS)
        ):
            avg_by_sector: list[tuple[str, float]] = []
            for sec, sc_list in sector_scores.items():
                if not sc_list:
                    continue
                avg_by_sector.append((sec, sum(sc_list) / len(sc_list)))
            # Rank descending by average score: most-bullish first.
            avg_by_sector.sort(key=lambda t: t[1], reverse=True)
            sector_rank = {sec: idx for idx, (sec, _) in enumerate(avg_by_sector)}
            n_sectors = len(avg_by_sector)
            mid_rank = (n_sectors - 1) / 2.0
            step = float(self.cfg.SECTOR_RANK_BIAS_STEP)
            cap  = float(self.cfg.SECTOR_RANK_BIAS_MAX)
            rank_applied = 0
            for s in passed_score:
                sector = SECTOR_MAP.get(s["symbol"], "OTHER")
                if sector not in sector_rank:
                    continue
                # Top sectors → positive bias; bottom → negative.
                raw_bias = (mid_rank - sector_rank[sector]) * step
                raw_bias = max(-cap, min(cap, raw_bias))
                if abs(raw_bias) < 1e-9:
                    continue
                # Sign-aware: bullish-tape sector lifts BUY |score|
                # (raw_bias > 0 + score > 0 → add); bearish-tape
                # sector deepens SELL |score| (raw_bias < 0 + score
                # < 0 → also widens magnitude). Counter-rank pairs
                # subtract from |score|.
                score = s["combined_score"]
                if score == 0:
                    continue
                if (raw_bias > 0 and score > 0) or (raw_bias < 0 and score < 0):
                    delta = abs(raw_bias)
                else:
                    delta = -abs(raw_bias)
                mag = max(0.0, abs(score) + delta)
                s["combined_score"] = round(mag if score > 0 else -mag, 1)
                rank_applied += 1
            if rank_applied:
                self.log.info(
                    f"  Sector-rank bias: nudged {rank_applied} candidates "
                    f"across {n_sectors} sectors (max +/-{cap:.1f} |score|)"
                )

        # Nifty trend hard filter: against-trend trades need stronger signals
        filtered = []
        dropped_trend = 0
        for s in passed_score:
            abs_score = abs(s["combined_score"])
            if nifty_trend == "BEARISH" and s["combined_score"] > 0 and abs_score < 3:
                dropped_trend += 1
                continue  # weak BUY in a bearish market — skip
            if nifty_trend == "BULLISH" and s["combined_score"] < 0 and abs_score < 3:
                dropped_trend += 1
                continue  # weak SELL in a bullish market — skip
            filtered.append(s)

        if dropped_trend:
            self.log.info(
                f"  Nifty trend filter ({nifty_trend}): "
                f"dropped {dropped_trend} weak against-trend signals"
            )

        # Sort by absolute combined score (strongest signals first)
        filtered.sort(key=lambda x: abs(x["combined_score"]), reverse=True)

        # Sector diversification: limit to MAX_PER_SECTOR per sector
        sector_diversified = []
        sector_counts: dict[str, int] = {}
        dropped_sector = 0
        for s in filtered:
            sector = SECTOR_MAP.get(s["symbol"], "OTHER")
            count = sector_counts.get(sector, 0)
            if count >= MAX_PER_SECTOR:
                dropped_sector += 1
                continue
            sector_counts[sector] = count + 1
            sector_diversified.append(s)

        if dropped_sector:
            self.log.info(
                f"  Sector diversification: dropped {dropped_sector} stocks "
                f"(max {MAX_PER_SECTOR} per sector)"
            )

        # Take top candidates
        top = sector_diversified[:MAX_CANDIDATES]

        # ── Score momentum (RoC) — compare with previous scan ─────
        # Enriches each candidate with score_delta from last scan.
        # Positive delta = accelerating, negative = decelerating.
        # First scan of the day has no delta (None).
        # DECISION IMPACT (NoAI + AI): When two candidates have the same
        # |score|, the accelerating one ranks higher (sort tiebreaker).
        # This means NoAI will prefer +7 (Δ+2) over +7 (Δ-1) when both
        # compete for the same slot. A strong penalty of -0.5 is applied
        # to rapidly decelerating candidates (Δ ≤ -2) as a score adjustment.
        new_scores: dict[str, float] = {}
        momentum_count = 0
        for c in top:
            sym = c["symbol"]
            raw_score = c["combined_score"]  # save BEFORE penalty
            new_scores[sym] = raw_score      # store RAW score for next scan's delta
            prev = self._prev_scan_scores.get(sym)
            if prev is not None:
                delta = round(raw_score - prev, 1)
                c["score_delta"] = delta
                momentum_count += 1
                # Penalize rapidly decelerating setups — momentum is fading
                if abs(delta) >= 2 and delta < 0 and c["combined_score"] > 0:
                    c["combined_score"] = round(c["combined_score"] - 0.5, 1)
                elif abs(delta) >= 2 and delta > 0 and c["combined_score"] < 0:
                    c["combined_score"] = round(c["combined_score"] + 0.5, 1)
            else:
                c["score_delta"] = None  # first scan, no delta
        self._prev_scan_scores = new_scores  # store RAW scores (no penalty) for next scan

        # Re-sort with momentum as tiebreaker: |score| primary, delta secondary
        top.sort(key=lambda x: (abs(x["combined_score"]), x.get("score_delta") or 0), reverse=True)

        if momentum_count:
            accel = sum(1 for c in top if (c.get("score_delta") or 0) > 0)
            decel = sum(1 for c in top if (c.get("score_delta") or 0) < 0)
            if accel or decel:
                self.log.info(
                    f"  Score momentum: {accel} accelerating, {decel} decelerating "
                    f"(vs previous scan)"
                )

        if top:
            self.log.info(
                f"  ── Result: {len(top)} candidates from {len(universe)} stocks "
                f"(dropped: {dropped_price} price, {dropped_score} score, "
                f"{dropped_trend} trend, {dropped_sector} sector)"
            )
            pre_open_tag = " [pre-open]" if _is_pre_open_score_time(as_of) else ""
            for r in top:
                ps = r["pattern_summary"]
                patterns_str = ", ".join(ps["patterns"][:3]) if ps["patterns"] else "none"
                rvol_str = f"  RVol: {r['rvol']:.1f}x" if r.get("rvol", 0) > 0 else ""
                delta = r.get("score_delta")
                delta_str = f"  Δ{delta:+.1f}" if delta is not None else ""
                self.log.info(
                    f"    {r['symbol']:<14} score: {r['combined_score']:>+5.1f}{pre_open_tag}{delta_str}  "
                    f"tech: {r['technical']['signal']:<12} "
                    f"patterns: {patterns_str}{rvol_str}"
                )

        # ── Per-candidate telemetry (#259) ─────────────────────
        # Stamp every survivor with the scan timestamp so the engine
        # can locate the row when the trade attempt resolves, then
        # write a SCORED row per candidate. Best-effort: any DB error
        # is logged and swallowed by `CandidateTelemetry`.
        scan_ts = _as_of_dt(as_of).strftime("%Y-%m-%d %H:%M:%S")
        self.last_scan_time = scan_ts
        tape_label = ""
        if isinstance(self.last_tape_breadth, dict):
            tape_label = self.last_tape_breadth.get("tape", "") or ""
        for r in top:
            r["_scan_time"] = scan_ts
            sector = SECTOR_MAP.get(r["symbol"], "OTHER")
            self.telemetry.record_scored(
                r,
                scan_time=scan_ts,
                nifty_trend=nifty_trend or self.last_nifty_trend,
                vix=self.last_vix,
                tape=tape_label,
                sector=sector,
            )

        return top

    # ================================================================
    # NO-AI SCAN — AUTO-SELECT FROM TECHNICAL SCORES
    # ================================================================

    def _simple_mr_score(self, candidate: dict) -> tuple[float, str] | None:
        tech = candidate.get("technical", {}) or {}
        rsi_data = tech.get("rsi", {}) or {}
        vwap_data = tech.get("vwap", {}) or {}
        band_data = tech.get("vwap_bands", {}) or {}
        try:
            rsi_val = float(rsi_data.get("rsi", 0) or 0)
            vwap_dev = float(vwap_data.get("deviation_pct", 0) or 0)
        except (TypeError, ValueError):
            return None
        if rsi_val <= 0:
            return None

        band = str(band_data.get("signal", "INSIDE") or "INSIDE").upper()
        buy_band = band in {"AT_LOWER_1SD", "AT_LOWER_2SD"}
        sell_band = band in {"AT_UPPER_1SD", "AT_UPPER_2SD"}
        min_dev = float(getattr(self.cfg, "SIMPLE_MR_MIN_VWAP_DEV_PCT", 0.35))
        require_band = bool(getattr(self.cfg, "SIMPLE_MR_REQUIRE_VWAP_BAND", True))

        is_buy = (
            vwap_dev <= -min_dev
            and rsi_val <= float(getattr(self.cfg, "SIMPLE_MR_RSI_BUY_MAX", 40.0))
            and (buy_band or not require_band)
        )
        is_sell = (
            vwap_dev >= min_dev
            and rsi_val >= float(getattr(self.cfg, "SIMPLE_MR_RSI_SELL_MIN", 60.0))
            and (sell_band or not require_band)
        )
        if not (is_buy or is_sell):
            return None

        score_abs = 2.0 + min(abs(vwap_dev), 1.5)
        if band in {"AT_LOWER_2SD", "AT_UPPER_2SD"}:
            score_abs += 1.0
        elif band in {"AT_LOWER_1SD", "AT_UPPER_1SD"}:
            score_abs += 0.5
        if (is_buy and rsi_val <= 30) or (is_sell and rsi_val >= 70):
            score_abs += 0.5

        min_score = float(getattr(self.cfg, "SIMPLE_MR_MIN_SCORE", 3.0))
        if score_abs < min_score:
            return None
        score_abs = round(min(score_abs, 6.0), 1)
        side = "BUY" if is_buy else "SELL"
        signed = score_abs if side == "BUY" else -score_abs
        reason = (
            f"{side} VWAP mean-reversion: dev {vwap_dev:+.2f}% "
            f"band {band}, RSI {rsi_val:.0f}"
        )
        return signed, reason

    def _scan_noai_simple_mr(
        self, quotes: dict, max_trades: int,
        session_context: str = "", open_buys: int = 0,
        open_sells: int = 0, as_of: datetime.datetime | None = None,
    ) -> list[dict]:
        profile = "NOAI_SIMPLE_MR_BASELINE"
        universe = self.get_universe()
        self.last_tape_breadth = None
        self.log.info(
            f"NoAI strategy profile: {profile} — VWAP/RSI mean-reversion only"
        )
        self.log.info(f"Simple MR scan: analysing {len(universe)} stocks")

        min_price = self.cfg.SCAN_MIN_PRICE
        max_price = self.cfg.SCAN_MAX_PRICE
        if max_price <= 0:
            max_price = self._budget * self.cfg.MAX_POSITION_PCT / 100

        missing_quote_symbols = []
        for symbol in universe:
            key = f"NSE:{symbol}"
            q = quotes.get(key, {})
            ltp = q.get("last_price", 0) if isinstance(q, dict) else 0
            if ltp <= 0:
                missing_quote_symbols.append(symbol)
        if missing_quote_symbols:
            retry_quotes = self.zerodha.get_quotes_safe(
                [{"symbol": s, "exchange": "NSE"} for s in missing_quote_symbols],
                max_retries=3,
            ) or {}
            for symbol in missing_quote_symbols:
                key = f"NSE:{symbol}"
                q = retry_quotes.get(key, {})
                ltp = q.get("last_price", 0) if isinstance(q, dict) else 0
                if ltp > 0:
                    quotes[key] = q

        price_filtered = []
        dropped_no_quote = 0
        dropped_price = 0
        for symbol in universe:
            key = f"NSE:{symbol}"
            q = quotes.get(key, {})
            ltp = q.get("last_price", 0) if isinstance(q, dict) else 0
            if ltp <= 0:
                dropped_no_quote += 1
                continue
            if ltp < min_price or ltp > max_price:
                dropped_price += 1
                continue
            price_filtered.append(symbol)
        if dropped_no_quote:
            self.log.warning(
                f"  Simple MR: skipped {dropped_no_quote} stocks with missing quotes"
            )
        if dropped_price:
            self.log.info(
                f"  Simple MR: dropped {dropped_price} stocks outside "
                f"Rs.{min_price:.0f}-{max_price:.0f} range"
            )

        if getattr(self.cfg, "EARNINGS_BLACKOUT_ENABLED", True):
            try:
                today_str = _as_of_dt(as_of).strftime("%Y-%m-%d")
                year = today_str[:4]
                cal = getattr(self.cfg, f"EARNINGS_BLACKOUT_SYMBOLS_{year}", {}) or {}
                blackout_today = set(cal.get(today_str, []))
                if blackout_today:
                    before = len(price_filtered)
                    price_filtered = [s for s in price_filtered if s not in blackout_today]
                    dropped_earn = before - len(price_filtered)
                    if dropped_earn:
                        self.log.info(
                            f"  Simple MR earnings blackout: skipped {dropped_earn} symbol(s)"
                        )
            except Exception as e:
                self.log.debug(f"Simple MR earnings blackout check failed: {e}")

        candidates: list[dict] = []
        for i, symbol in enumerate(price_filtered):
            quarter = max(1, len(price_filtered) // 4)
            if (i + 1) % quarter == 0 or i + 1 == len(price_filtered):
                self.log.info(f"  Simple MR analysing... {i + 1}/{len(price_filtered)}")
            result = self._analyse_stock(symbol, as_of=as_of)
            if not result:
                continue
            scored = self._simple_mr_score(result)
            if not scored:
                continue
            score, reason = scored
            result["combined_score"] = score
            result["strategy_id"] = profile
            result["strategy_reason"] = reason
            candidates.append(result)

        if not candidates:
            self.log.warning("Simple MR scan: no candidates passed baseline rules")
            return []
        candidates.sort(key=lambda x: abs(x["combined_score"]), reverse=True)
        top = candidates[:MAX_CANDIDATES]

        scan_ts = _as_of_dt(as_of).strftime("%Y-%m-%d %H:%M:%S")
        self.last_scan_time = scan_ts
        for r in top:
            r["_scan_time"] = scan_ts
            sector = SECTOR_MAP.get(r["symbol"], "OTHER")
            self.telemetry.record_scored(
                r,
                scan_time=scan_ts,
                nifty_trend=self.last_nifty_trend,
                vix=self.last_vix,
                tape="SIMPLE_MR",
                sector=sector,
            )

        max_same_dir = max(1, self.cfg.MAX_POSITIONS - 1)
        buy_slots = max(0, max_same_dir - open_buys)
        sell_slots = max(0, max_same_dir - open_sells)
        budget = self._budget
        max_pct = self.cfg.MAX_POSITION_PCT / 100
        max_per = budget * max_pct
        budget_per_slot = min(budget / max_trades, max_per)

        skip_symbols: set[str] = set()
        if session_context:
            m = re.search(r"Already traded today:\s*(.+)", session_context)
            if m and m.group(1).strip().lower() != "none":
                skip_symbols = {s.strip() for s in m.group(1).split(",")}
            m = re.search(r"Currently holding:\s*(.+)", session_context)
            if m and m.group(1).strip().lower() != "none":
                skip_symbols |= {s.strip() for s in m.group(1).split(",")}

        trades = []
        used_buy = 0
        used_sell = 0
        noai_sector_counts: dict[str, int] = {}
        for c in top:
            symbol = c["symbol"]
            if symbol in skip_symbols:
                continue
            score = c["combined_score"]
            side = "BUY" if score > 0 else "SELL"
            if side == "BUY":
                if used_buy >= buy_slots:
                    continue
            else:
                if used_sell >= sell_slots:
                    continue

            sector = SECTOR_MAP.get(symbol, "OTHER")
            sec_count = noai_sector_counts.get(sector, 0)
            if sec_count >= MAX_PER_SECTOR:
                continue

            price = c["current_price"]
            if price <= 0:
                continue
            noai_sector_counts[sector] = sec_count + 1
            if side == "BUY":
                used_buy += 1
            else:
                used_sell += 1
            sl_pct = self.cfg.DEFAULT_STOP_LOSS_PCT / 100
            tgt_pct = self.cfg.DEFAULT_TARGET_PCT / 100
            if side == "BUY":
                sl = round(price * (1 - sl_pct), 2)
                target = round(price * (1 + tgt_pct), 2)
            else:
                sl = round(price * (1 + sl_pct), 2)
                target = round(price * (1 - tgt_pct), 2)
            qty = max(1, int(budget_per_slot / price))
            tech = c["technical"]
            trades.append({
                "symbol": symbol,
                "exchange": "NSE",
                "side": side,
                "entry_price": round(price, 2),
                "stop_loss": sl,
                "target_price": target,
                "qty": qty,
                "rationale": c.get("strategy_reason", profile),
                "status": "PENDING",
                "strategy_id": profile,
                "_entry_score": score,
                "_entry_rsi": tech.get("rsi", {}).get("rsi", 0),
                "_entry_adx": tech.get("adx", {}).get("adx", 0),
                "_entry_plus_di": tech.get("adx", {}).get("plus_di", 0),
                "_entry_minus_di": tech.get("adx", {}).get("minus_di", 0),
                "_entry_patterns": [],
                "_indicator_snapshot": self._build_indicator_snapshot(tech, c),
                "_scan_time": c.get("_scan_time"),
            })

        if not trades:
            self.log.warning(
                f"Simple MR scan: {len(top)} candidates found but none fit slots/sector/budget"
            )
            return []

        primary = self._validate_budget(trades[:max_trades])
        fallback = trades[max_trades:]
        while len(primary) < max_trades and fallback:
            promoted = fallback.pop(0)
            candidate = self._validate_budget([promoted])
            if candidate:
                primary.extend(candidate)
        primary = self._score_weight_sizing(primary)
        all_trades = primary + fallback
        self.log.success(
            f"Simple MR scan: {len(primary)} primary + {len(fallback)} fallback "
            f"= {len(all_trades)} candidates for entry loop"
        )
        return all_trades

    def scan_noai(
        self, quotes: dict, nifty_context: str = "",
        max_trades: int = 0, session_context: str = "",
        day_pnl: float = 0.0,
        open_buys: int = 0, open_sells: int = 0,
        as_of: datetime.datetime | None = None,
    ) -> list[dict]:
        """
        Selects trades purely from technical scores — no Claude call.
        Uses the same candle pre-filter as V2, then auto-generates
        trade plans from the top candidates.

        open_buys/open_sells: current open positions by direction.
        Used to respect the direction diversification limit BEFORE
        selecting candidates (avoids picking trades that enter_trade
        would reject).

        Returns a list of trade dicts identical to what Claude would
        produce (same keys: symbol, side, entry_price, stop_loss,
        target_price, qty, rationale, status).
        """
        if max_trades <= 0:
            max_trades = self.cfg.MAX_POSITIONS
        if max_trades <= 0:
            self.log.warning("NoAI scan: MAX_POSITIONS is 0 — cannot select trades")
            return []

        profile = getattr(self.cfg, "TRADE_STRATEGY_PROFILE", "NOAI_LEGACY_FULL")
        if profile == "NOAI_SIMPLE_MR_BASELINE":
            return self._scan_noai_simple_mr(
                quotes, max_trades,
                session_context=session_context,
                open_buys=open_buys,
                open_sells=open_sells,
                as_of=as_of,
            )

        # Extract Nifty trend for hard filter
        nifty_trend = ""
        if "BEARISH" in nifty_context.upper():
            nifty_trend = "BEARISH"
        elif "BULLISH" in nifty_context.upper():
            nifty_trend = "BULLISH"

        # Dynamic score: raise bar after losses
        min_score_override = None
        if day_pnl < 0 and self.cfg.LOSS_SCORE_BUMP_PCT > 0:
            loss_pct = abs(day_pnl) / self._budget * 100
            if loss_pct >= self.cfg.LOSS_SCORE_BUMP_PCT:
                min_score_override = self.cfg.MIN_SCORE + self.cfg.LOSS_SCORE_BUMP_AMOUNT
                self.log.info(
                    f"Dynamic score threshold: day loss {loss_pct:.1f}% ≥ "
                    f"{self.cfg.LOSS_SCORE_BUMP_PCT}% — raising MIN_SCORE "
                    f"to {min_score_override:.1f}"
                )

        # Budget-regime score bump (Roadmap #165): tighter score floor
        # for small accounts where each losing trade hurts more.
        if self.cfg.BUDGET_REGIME_ENABLED:
            regime = Config.budget_regime(self._budget)
            delta = self.cfg.BUDGET_MIN_SCORE_DELTA.get(regime, 0.0)
            if delta > 0:
                base = min_score_override if min_score_override is not None else self.cfg.MIN_SCORE
                regime_score = base + float(delta)
                if min_score_override is None or regime_score > min_score_override:
                    min_score_override = regime_score
                    self.log.info(
                        f"Budget-regime ({regime}): MIN_SCORE raised by "
                        f"+{delta:.1f} → {regime_score:.1f}"
                    )

        # Step 1: Math-based pre-filter
        candidates = self._prefilter_universe(
            quotes, nifty_trend, min_score_override, as_of=as_of,
        )
        if not candidates:
            self.log.warning("NoAI scan: no candidates passed pre-filter")
            return []

        # ── Smart direction allocation (financial analyst logic) ──
        # Instead of a hard "max N in same direction" rule, evaluate
        # whether the available BUY and SELL candidates justify
        # concentrating in one direction.
        #
        # Rationale: on a strongly trending day (e.g. broad market up 1.5%),
        # ALL good setups may be BUY. Forcing a SELL just for "diversification"
        # means taking a weak counter-trend trade that's likely to lose.
        # A financial analyst would say: "go with the trend, don't fight it."
        #
        # Logic:
        #   1. Separate candidates by direction
        #   2. Compare best BUY score vs best SELL score
        #   3. If gap >= 3 points → market is biased, allow all slots
        #      in the dominant direction (don't waste capital on weak setups)
        #   4. If gap < 3 → both directions have comparable setups,
        #      apply the normal max_same_dir limit to stay diversified
        buy_candidates = [c for c in candidates if c["combined_score"] > 0]
        sell_candidates = [c for c in candidates if c["combined_score"] < 0]
        best_buy_score = buy_candidates[0]["combined_score"] if buy_candidates else 0
        best_sell_score = abs(sell_candidates[0]["combined_score"]) if sell_candidates else 0
        score_gap = abs(best_buy_score - best_sell_score)

        max_same_dir_hard = self.cfg.MAX_POSITIONS  # maximum possible
        max_same_dir_normal = max(1, self.cfg.MAX_POSITIONS - 1)

        # Determine effective direction limits
        if score_gap >= 3:
            # Strong directional bias — let the dominant side take all slots
            dominant = "BUY" if best_buy_score > best_sell_score else "SELL"
            buy_limit = max_same_dir_hard if dominant == "BUY" else max_same_dir_normal
            sell_limit = max_same_dir_hard if dominant == "SELL" else max_same_dir_normal
            self.log.info(
                f"Direction analysis: BUY best {best_buy_score:+.1f} vs SELL best "
                f"{best_sell_score:+.1f} (gap {score_gap:.1f}) — {dominant} dominant, "
                f"allowing up to {max_same_dir_hard} {dominant} positions"
            )
        else:
            buy_limit = max_same_dir_normal
            sell_limit = max_same_dir_normal

        buy_slots = max(0, buy_limit - open_buys)
        sell_slots = max(0, sell_limit - open_sells)

        # ── Post short-cutoff: reallocate SELL slots to BUY ──────
        # After SHORT_ENTRY_CUTOFF_HOUR, no shorts can be placed.
        # Rather than waste those slots, give them to BUY side —
        # but only if decent BUY candidates exist (score ≥ 4.0).
        short_cutoff = self.cfg.SHORT_ENTRY_CUTOFF_HOUR
        if _as_of_dt(as_of).hour >= short_cutoff and sell_slots > 0:
            strong_buys = [c for c in buy_candidates if c["combined_score"] >= 4.0]
            if strong_buys:
                reallocated = sell_slots
                buy_slots += sell_slots
                sell_slots = 0
                self.log.info(
                    f"Post {short_cutoff}:00 short cutoff: reallocated "
                    f"{reallocated} SELL slot(s) → BUY "
                    f"({len(strong_buys)} BUY candidates with score ≥ 4.0)"
                )
            else:
                dropped = sell_slots
                sell_slots = 0
                self.log.info(
                    f"Post {short_cutoff}:00 short cutoff: dropped {dropped} "
                    f"SELL slot(s) (no BUY candidates with score ≥ 4.0)"
                )

        # Separate candidates into primary (within slot limits) and
        # fallback (extra candidates in the same direction).  Previous
        # logic hard-clipped at slot count, killing all fallbacks.  Now
        # extras are kept so that if the primary pick fails entry checks
        # (R:R, spread, price drift) the next candidate can be tried.
        direction_primary = []
        direction_fallback = []
        _used_buy = 0
        _used_sell = 0
        for c in candidates:
            cs = c["combined_score"]
            if cs > 0:
                side = "BUY"
            elif cs < 0:
                side = "SELL"
            else:
                # Roadmap #169: score == 0 has no directional bias.
                # MIN_SCORE prefilter normally blocks zeros; defensive skip
                # keeps a future config tweak from accidentally force-shorting.
                self.log.warning(
                    f"  Skipping {c.get('symbol', '?')} — combined_score is 0 (no direction)"
                )
                continue
            if side == "BUY":
                if _used_buy < buy_slots:
                    _used_buy += 1
                    direction_primary.append(c)
                elif buy_slots > 0:
                    # Extra BUY — keep as fallback (direction is allowed)
                    direction_fallback.append(c)
                # else: buy_slots == 0 → direction fully blocked, skip
            else:
                if _used_sell < sell_slots:
                    _used_sell += 1
                    direction_primary.append(c)
                elif sell_slots > 0:
                    # Extra SELL — keep as fallback
                    direction_fallback.append(c)

        if not direction_primary and candidates:
            buy_cands = len(buy_candidates)
            sell_cands = len(sell_candidates)
            self.log.warning(
                f"NoAI scan: {len(candidates)} candidates found but all blocked by "
                f"direction limit (BUY slots: {buy_slots}, SELL slots: {sell_slots}, "
                f"candidates: {buy_cands} BUY / {sell_cands} SELL)"
            )
        elif direction_primary:
            self.log.info(
                f"  Stock picking: {len(direction_primary)} primary + "
                f"{len(direction_fallback)} fallback from {len(candidates)} candidates "
                f"(BUY slots: {buy_slots}, SELL slots: {sell_slots})"
            )

        # Primary picks first, then fallbacks (sorted by |score|).
        # The entry loop in _enter_positions enforces MAX_POSITIONS, budget,
        # and sector limits. Returning more candidates means if top picks
        # fail R:R or other entry checks, the loop can try lower-scored
        # candidates rather than triggering a wasteful full rescan.
        top = direction_primary + direction_fallback

        # Step 3: Build trade plans from technical data
        budget = self._budget
        max_pct = self.cfg.MAX_POSITION_PCT / 100
        max_per = budget * max_pct
        budget_per_slot = min(budget / max_trades, max_per)

        # Parse already-traded symbols from session context
        skip_symbols: set[str] = set()
        if session_context:
            import re as _re
            m = _re.search(r"Already traded today:\s*(.+)", session_context)
            if m and m.group(1).strip().lower() != "none":
                skip_symbols = {s.strip() for s in m.group(1).split(",")}
            m = _re.search(r"Currently holding:\s*(.+)", session_context)
            if m and m.group(1).strip().lower() != "none":
                skip_symbols |= {s.strip() for s in m.group(1).split(",")}

        trades = []
        noai_sector_counts: dict[str, int] = {}
        for c in top:
            symbol = c["symbol"]
            if symbol in skip_symbols:
                continue

            # Sector limit for NoAI trades
            sector = SECTOR_MAP.get(symbol, "OTHER")
            sec_count = noai_sector_counts.get(sector, 0)
            if sec_count >= MAX_PER_SECTOR:
                continue
            noai_sector_counts[sector] = sec_count + 1

            price = c["current_price"]
            if price <= 0:
                continue

            score = c["combined_score"]
            if score > 0:
                side = "BUY"
            elif score < 0:
                side = "SELL"
            else:
                # Roadmap #169: score == 0 has no directional bias — skip.
                continue

            # Default SL/target (ATR will override in enter_trade)
            sl_pct = self.cfg.DEFAULT_STOP_LOSS_PCT / 100
            tgt_pct = self.cfg.DEFAULT_TARGET_PCT / 100
            if side == "BUY":
                sl = round(price * (1 - sl_pct), 2)
                target = round(price * (1 + tgt_pct), 2)
            else:
                sl = round(price * (1 + sl_pct), 2)
                target = round(price * (1 - tgt_pct), 2)

            qty = max(1, int(budget_per_slot / price))

            # Build rationale from indicators
            tech = c["technical"]
            ps = c["pattern_summary"]
            parts = []
            parts.append(f"Score {score:+.1f}")
            parts.append(f"RSI {tech['rsi']['rsi']:.0f}")
            parts.append(f"EMA {tech['ema_cross']['signal']}")
            parts.append(f"ST {tech['supertrend']['trend']}")
            macd_info = tech.get("macd", {})
            if macd_info.get("signal", "NONE") != "NONE":
                parts.append(f"MACD {macd_info['signal']}/{macd_info['momentum']}")
            orb_info = tech.get("orb", {})
            if orb_info.get("signal", "NONE") not in ("NONE", "INSIDE_RANGE"):
                parts.append(f"ORB {orb_info['signal']}")
            gap_info = tech.get("gap", {})
            if gap_info.get("signal", "NO_GAP") != "NO_GAP":
                parts.append(f"Gap {gap_info['signal']}")
            hourly_info = tech.get("hourly_ema", {})
            if hourly_info.get("signal", "NEUTRAL") != "NEUTRAL":
                parts.append(f"Hourly {hourly_info['signal']}")
            bb_info = tech.get("bb_squeeze", {})
            if bb_info.get("squeeze", False):
                parts.append(f"BB {bb_info['signal']}")
            adx_info = tech.get("adx", {})
            if adx_info.get("adx", 0) > 0:
                parts.append(f"ADX {adx_info['adx']:.0f}({adx_info['trend_strength']})")
            fib_info = tech.get("fibonacci", {})
            if fib_info.get("signal", "NONE") != "NONE":
                parts.append(f"Fib {fib_info['signal']}")
            vwap_b_info = tech.get("vwap_bands", {})
            if vwap_b_info.get("signal", "INSIDE") != "INSIDE":
                parts.append(f"VWAP-Band {vwap_b_info['signal']}")
            stoch_info = tech.get("stoch_rsi", {})
            if stoch_info.get("signal", "NEUTRAL") != "NEUTRAL":
                parts.append(f"StochRSI {stoch_info['signal']}")
            if ps["patterns"]:
                parts.append(f"Patterns: {', '.join(ps['patterns'][:2])}")
            if c.get("rvol", 0) > 1.5:
                parts.append(f"RVol {c['rvol']:.1f}x")
            delta = c.get("score_delta")
            if delta is not None:
                parts.append(f"Δ{delta:+.1f}")

            trades.append({
                "symbol": symbol,
                "exchange": "NSE",
                "side": side,
                "entry_price": round(price, 2),
                "stop_loss": sl,
                "target_price": target,
                "qty": qty,
                "rationale": " | ".join(parts),
                "status": "PENDING",
                "_entry_score": score,
                "_entry_rsi": tech["rsi"]["rsi"],
                "_entry_adx": tech.get("adx", {}).get("adx", 0),
                "_entry_plus_di": tech.get("adx", {}).get("plus_di", 0),
                "_entry_minus_di": tech.get("adx", {}).get("minus_di", 0),
                "_entry_patterns": list(ps.get("patterns", []) or []),
                "_indicator_snapshot": self._build_indicator_snapshot(tech, c),
                # #259: forward the scan timestamp so the engine can
                # match the SCORED telemetry row when this trade
                # resolves (entered or rejected).
                "_scan_time": c.get("_scan_time"),
            })

        # Split primary picks and fallback candidates.
        # Budget validation + boost only on primary picks — fallback
        # candidates keep per-slot sizing; enter_trade enforces budget
        # dynamically at order time.
        primary = trades[:max_trades]
        fallback = trades[max_trades:]

        # Promote fallbacks into primary when stocks are dropped (e.g.
        # BOSCHLTD @ Rs.37k exceeds per-stock cap with min qty 1).
        # Without this, dropped slots leave budget idle.
        primary = self._validate_budget(primary)
        while len(primary) < max_trades and fallback:
            promoted = fallback.pop(0)
            candidate = self._validate_budget([promoted])
            if candidate:
                primary.extend(candidate)
                self.log.info(
                    f"Promoted {promoted['symbol']} from fallback to fill "
                    f"dropped primary slot ({len(primary)}/{max_trades})"
                )
            # else: this fallback also failed budget validation, try next

        primary = self._score_weight_sizing(primary)

        all_trades = primary + fallback
        self.log.success(
            f"NoAI scan: {len(primary)} primary + {len(fallback)} fallback "
            f"= {len(all_trades)} candidates for entry loop"
        )
        return all_trades

    # ================================================================
    # SCORE-WEIGHTED POSITION SIZING
    # ================================================================

    def _score_weight_sizing(self, trades: list[dict]) -> list[dict]:
        """
        Redistributes budget across primary trades proportional to
        their technical conviction scores. Higher-score stocks get
        larger positions (more capital where confidence is highest).

        Industry standard: conviction-weighted sizing (simplified
        Kelly criterion). A score-10 stock with 5+ indicator
        confluences deserves more capital than a score-4 with 2.

        Formula: weight_i = |score_i| / Σ|scores|
                 budget_i = weight_i × total_budget
                 qty_i    = floor(budget_i / price_i)
        Capped at MAX_POSITION_PCT per stock. Excess redistributed
        to lower-scored stocks to maximize deployment.

        Roadmap #258 (2026-05-07): kill-switch
        `Config.SCORE_WEIGHTED_SIZING_ENABLED` (default False) bypasses
        this pass entirely — trades keep their equal-sizing qty from
        the upstream slot allocation. Audit data 04-22→05-06 showed
        |score|≥6 buckets are anti-correlated with realised P&L on
        n=44 trades; equal-sizing is the documented OOS fallback when
        factor confidence is low. Re-enable trigger #258R.
        """
        if not self.cfg.SCORE_WEIGHTED_SIZING_ENABLED:
            self.log.info(
                "  Score-weighted sizing disabled (kill-switch); "
                "using equal sizing"
            )
            return trades

        if len(trades) <= 1:
            return trades

        budget = self._budget
        max_pct = self.cfg.MAX_POSITION_PCT / 100
        max_per = budget * max_pct

        total_score = sum(abs(t.get("_entry_score", 0)) for t in trades)
        if total_score <= 0:
            return trades

        # First pass: allocate proportionally, cap at max_per
        excess = 0.0
        uncapped_score = 0.0
        allocations = []
        for t in trades:
            score = abs(t.get("_entry_score", 0))
            weight = score / total_score
            target_budget = weight * budget
            if target_budget > max_per:
                excess += target_budget - max_per
                target_budget = max_per
            else:
                uncapped_score += score
            allocations.append(target_budget)

        # Second pass: redistribute excess to uncapped stocks
        if excess > 0 and uncapped_score > 0:
            for i, t in enumerate(trades):
                if allocations[i] < max_per:
                    score = abs(t.get("_entry_score", 0))
                    bonus = excess * (score / uncapped_score)
                    allocations[i] = min(allocations[i] + bonus, max_per)

        # Apply new quantities
        for i, t in enumerate(trades):
            entry = t["entry_price"]
            if entry <= 0:
                continue
            new_qty = max(1, int(allocations[i] / entry))
            if new_qty != t["qty"]:
                score = abs(t.get("_entry_score", 0))
                self.log.info(
                    f"  Score-weight: {t['symbol']} |score| {score:.1f} → "
                    f"qty {t['qty']} → {new_qty} "
                    f"(Rs.{t['qty'] * entry:,.0f} → Rs.{new_qty * entry:,.0f})"
                )
                t["qty"] = new_qty

        # Final safety: ensure total doesn't exceed budget
        total_cost = sum(t["entry_price"] * t["qty"] for t in trades)
        if total_cost > budget:
            excess_cost = total_cost - budget
            for t in sorted(trades, key=lambda x: abs(x.get("_entry_score", 0))):
                entry = t["entry_price"]
                if entry <= 0 or t["qty"] <= 1:
                    continue
                reduce = min(t["qty"] - 1, int(excess_cost / entry) + 1)
                if reduce > 0:
                    t["qty"] -= reduce
                    excess_cost -= reduce * entry
                if excess_cost <= 0:
                    break

        final_cost = sum(t["entry_price"] * t["qty"] for t in trades)
        self.log.info(
            f"Score-weighted sizing: Rs.{final_cost:,.0f} / Rs.{budget:,.0f} "
            f"({final_cost / budget * 100:.0f}%)"
        )
        return trades

    # ================================================================
    # INDICATOR SNAPSHOT FOR LEARNING
    # ================================================================

    def _build_indicator_snapshot(self, tech: dict, candidate: dict) -> str:
        """Builds a compact JSON string of key indicators at entry time."""
        import json
        snap = {
            "score": candidate.get("combined_score", 0),
            "rsi": tech["rsi"]["rsi"],
            "ema": tech["ema_cross"]["signal"],
            "ema_spread": tech["ema_cross"]["spread_pct"],
            "st": tech["supertrend"]["trend"],
            "st_signal": tech["supertrend"]["signal"],
            "vwap_dev": tech["vwap"]["deviation_pct"],
            # VWAP statistical-band classification (Roadmap #201).
            # Read by the OrderEngine VWAP-band gate to reject BUY at
            # AT_UPPER_1SD/2SD and SELL at AT_LOWER_1SD/2SD unless
            # |score| ≥ VWAP_BAND_OVERRIDE_SCORE.
            "vwap_band": tech.get("vwap_bands", {}).get("signal", "INSIDE"),
            "adx": tech.get("adx", {}).get("adx", 0),
            "adx_strength": tech.get("adx", {}).get("trend_strength", ""),
            "macd": tech.get("macd", {}).get("signal", ""),
            "macd_mom": tech.get("macd", {}).get("momentum", ""),
            "orb": tech.get("orb", {}).get("signal", ""),
            "rvol": candidate.get("rvol", 0),
            "ext_move": tech.get("extended_move_pct", 0),
            "score_delta": candidate.get("score_delta"),
            # Gap signal (#173 gap-coherence gate). Read by OrderEngine
            # pre-trade check to reject contradictory-direction entries
            # on STRONG gaps unless score is exceptionally high.
            "gap": tech.get("gap", {}).get("signal", "NO_GAP"),
        }
        return json.dumps(snap)

    def _enrich_trades_with_indicators(
        self, trades: list[dict], candidates: list[dict]
    ):
        """
        Adds indicator snapshot data to Claude-parsed trades by matching
        them against the pre-filter candidates.
        """
        cand_by_symbol = {c["symbol"]: c for c in candidates}
        for t in trades:
            c = cand_by_symbol.get(t.get("symbol", ""))
            if c:
                tech = c["technical"]
                t["_entry_score"] = c["combined_score"]
                t["_entry_rsi"] = tech["rsi"]["rsi"]
                # Roadmap #157 — ADX + DI fields used by the order engine's
                # entry gate. Must be set here too (not just in the PENDING
                # scanner path) so AI-picked trades go through the same gate.
                t["_entry_adx"]      = tech.get("adx", {}).get("adx", 0)
                t["_entry_plus_di"]  = tech.get("adx", {}).get("plus_di", 0)
                t["_entry_minus_di"] = tech.get("adx", {}).get("minus_di", 0)
                t["_indicator_snapshot"] = self._build_indicator_snapshot(tech, c)
                # #259: forward scan timestamp so engine can locate the
                # SCORED telemetry row when this trade resolves.
                t["_scan_time"] = c.get("_scan_time")

    # ================================================================
    # ENRICHED SNAPSHOT BUILDER
    # ================================================================

    def _build_enriched_snapshot(self, candidates: list[dict], quotes: dict) -> str:
        """
        Builds a rich text snapshot for Claude that includes technical
        indicator data alongside price data for each candidate.
        """
        lines = []
        for c in candidates:
            symbol = c["symbol"]
            key = f"NSE:{symbol}"
            q = quotes.get(key, {})

            price = q.get("last_price", c["current_price"])
            ohlc = q.get("ohlc", {})
            volume = q.get("volume", 0)

            change = price - ohlc.get("close", price)
            change_pct = (change / ohlc["close"] * 100) if ohlc.get("close") else 0

            # Pattern info
            ps = c["pattern_summary"]
            patterns = ", ".join(ps["patterns"]) if ps["patterns"] else "none"

            # Technical indicators
            tech = c["technical"]
            rsi_val = tech["rsi"]["rsi"]
            ema_info = tech["ema_cross"]
            st_info = tech["supertrend"]

            # Previous day S&R levels
            sr = tech.get("prev_day_sr", {})
            sr_str = ""
            sr_signal = sr.get("signal", "NONE")
            if sr_signal in ("AT_RESISTANCE", "AT_SUPPORT", "ABOVE_PIVOT", "BELOW_PIVOT"):
                sr_str = f"  PrevDay: {sr_signal}"
            if sr.get("pivot", 0) > 0:
                sr_str += f"  Pivot: Rs.{sr['pivot']:.2f}"

            # Relative volume
            rvol_str = ""
            if c.get("rvol", 0) > 0:
                rvol_str = f"  RVol: {c['rvol']:.1f}x"

            # MACD
            macd = tech.get("macd", {})
            macd_str = ""
            if macd.get("signal", "NONE") != "NONE":
                macd_str = f"  MACD: {macd['signal']}/{macd['momentum']}"

            # ORB
            orb = tech.get("orb", {})
            orb_str = ""
            if orb.get("signal", "NONE") not in ("NONE", "INSIDE_RANGE"):
                orb_str = f"  ORB: {orb['signal']}"

            # Gap
            gap = tech.get("gap", {})
            gap_str = ""
            if gap.get("signal", "NO_GAP") != "NO_GAP":
                gap_str = f"  Gap: {gap['signal']}({gap['gap_pct']:+.1f}%)"

            # Hourly EMA alignment
            hourly = tech.get("hourly_ema", {})
            hourly_str = ""
            if hourly.get("signal", "NEUTRAL") != "NEUTRAL":
                hourly_str = f"  Hourly: {hourly['signal']}"

            # Bollinger Band squeeze
            bb = tech.get("bb_squeeze", {})
            bb_str = ""
            if bb.get("squeeze", False):
                bb_str = f"  BB: {bb['signal']}"

            # ADX trend strength
            adx_info = tech.get("adx", {})
            adx_str = ""
            if adx_info.get("adx", 0) > 0:
                adx_str = f"  ADX: {adx_info['adx']:.0f}({adx_info['trend_strength']})"

            # Fibonacci retracement levels
            fib = tech.get("fibonacci", {})
            fib_str = ""
            if fib.get("signal", "NONE") != "NONE":
                fib_str = f"  Fib: {fib['signal']}({fib['nearest_level']})"

            # VWAP SD bands
            vwap_b = tech.get("vwap_bands", {})
            vwap_b_str = ""
            if vwap_b.get("signal", "INSIDE") != "INSIDE":
                vwap_b_str = f"  VWAP-Band: {vwap_b['signal']}"

            # StochRSI entry timing
            stoch = tech.get("stoch_rsi", {})
            stoch_str = ""
            if stoch.get("signal", "NEUTRAL") != "NEUTRAL":
                stoch_str = f"  StochRSI: {stoch['signal']}(K:{stoch['k']:.0f}/D:{stoch['d']:.0f})"

            # Extended move from open
            ext_move = tech.get("extended_move_pct", 0)
            ext_str = ""
            if abs(ext_move) > 1.5:
                ext_str = f"  ⚠ ExtMove: {ext_move:+.1f}%"

            # Score momentum (delta from previous scan)
            delta = c.get("score_delta")
            delta_str = f"  ScoreΔ: {delta:+.1f}" if delta is not None else ""

            lines.append(
                f"{symbol:<14} "
                f"Rs.{price:>10.2f}  Chg: {change_pct:>+6.2f}%  "
                f"Vol: {volume:>12,}{rvol_str}  "
                f"VWAP: Rs.{c['vwap']:.2f}  "
                f"RSI: {rsi_val:.0f}  "
                f"EMA(9/21): {ema_info['signal']}  "
                f"SuperTrend: {st_info['trend']}  "
                f"Score: {c['combined_score']:+.1f}  "
                f"Patterns: [{patterns}]{sr_str}{macd_str}{orb_str}{gap_str}{hourly_str}{bb_str}{adx_str}{fib_str}{vwap_b_str}{stoch_str}{ext_str}{delta_str}"
            )

        return "\n".join(lines)

    def _build_v2_scan_prompt(
        self, snapshot: str, nifty_context: str = "",
        perf_context: str = "", session_context: str = "",
    ) -> str:
        """Claude prompt with enriched technical data for V2 candidates."""
        today  = now_ist().date().strftime("%B %d, %Y")
        now    = now_ist().strftime("%I:%M %p")
        budget = self._budget
        max_positions  = self.cfg.MAX_POSITIONS
        max_pct        = self.cfg.MAX_POSITION_PCT
        default_sl     = self.cfg.DEFAULT_STOP_LOSS_PCT
        default_target = self.cfg.DEFAULT_TARGET_PCT

        # Time-of-day context for strategy adaptation
        hour = now_ist().hour
        minute = now_ist().minute
        if hour == 9 and minute < 45:
            time_phase = "OPENING (9:15-9:45 AM): High volatility. ORB setups are strongest now. Wait for 15-min candle close before entry. Avoid chasing opening spikes."
        elif hour < 11:
            time_phase = "MORNING TREND (9:45-11:00 AM): Best trending window of the day. Momentum and breakout trades have highest success. Use full position sizes."
        elif hour < 13:
            time_phase = "MIDDAY LULL (11:00 AM-1:00 PM): Volume drops, ranges narrow. Favour mean-reversion setups near VWAP. Reduce conviction on breakout trades."
        elif hour < 14:
            time_phase = "AFTERNOON (1:00-2:00 PM): European markets opening can bring fresh volatility. Less session time means tighter trade selection but no automatic target compression — the entry target is what you're trading for."
        else:
            time_phase = "LATE SESSION (after 2:00 PM): Only take HIGH conviction setups (score >= 5 with 3+ confluences). Targets are honoured at entry — closure is governed by stagnant-exit, momentum kill, and 3:10 PM square-off."

        # R:R floor varies with time — align Claude's rejection with code
        # R:R floor is uniform across the trading day since #243
        # (collapsed from the deprecated time-tiered floors).
        rr_min_text = f"1:{self.cfg.RR_HARD_FLOOR}"

        return f"""You are an expert Indian stock market intraday trader (NSE) specialising in NIFTY F&O stocks.
You have 15 years of experience with deep knowledge of Indian market microstructure — FII/DII flow dynamics, weekly F&O expiry effects, sector rotation, and NSE intraday volume patterns.

Today is {today}, current time is {now} IST. All positions MUST be closed by 3:10 PM IST today.
CURRENT TIME PHASE: {time_phase}

BUDGET: Rs.{budget:,} total capital.
MAX POSITIONS: {max_positions} stocks simultaneously (Rs.{budget // max_positions:,} per slot).
MAX PER STOCK: {max_pct}% of budget (= Rs.{budget * max_pct // 100:,} max per stock).
Idle capital is fine — do NOT add mediocre trades to deploy more cash. Quality over quantity.
{nifty_context}{perf_context}{session_context}
The stocks below are PRE-FILTERED by mathematical technical analysis.
Each stock shows real-time indicators — use them for precise, evidence-based decisions.

YOUR ROLE: RANK and VETO from these pre-filtered candidates.
The system has already computed technical scores, ATR-based SL/targets, and indicators.
You are the QUALITY GATE. The bot will take MAX 2 TRADES TODAY (hard cap).
This means YOU pick the absolute best 2 from ~50 NIFTY50 stocks.
Think of it as: "If I could only bet on 2 horses today, which 2?"

Focus on what you are BEST at:
  1. SELECTING the 2 highest-conviction setups — the ones with the most indicator confluence AND cleanest risk/reward
  2. VETOING stocks that look technically valid but have narrative risk (sector events, extended moves, concentration)
  3. Setting SL at STRUCTURAL levels visible in the data (VWAP, SuperTrend, PrevDay S&R, Fib levels) — NOT arbitrary percentages
  4. DIVERSIFYING — pick from DIFFERENT sectors. If both top picks are banking, drop the weaker one and find the best non-banking setup.
Do NOT fabricate price targets from nothing. Use the indicator levels shown (VWAP, pivot, Fib) as anchors.
With only 2 trades, EVERY pick must be a conviction play. If you see fewer than 2 high-quality setups, respond with 1 or NO_TRADES_TODAY. Never pad.

══════════════════════════════════════════════════════════
INDICATOR INTERPRETATION:
══════════════════════════════════════════════════════════
RSI:
  >70 = overbought → SHORT candidate or AVOID for BUY (but 70-80 can sustain in strong trends)
  <30 = oversold → BUY candidate (but wait for a REVERSAL CANDLE to confirm bottom)
  *** RSI <25 = CAPITULATION ZONE → DO NOT SHORT — bounce is imminent ***
  *** RSI >80 = EUPHORIA ZONE → DO NOT BUY — sharp pullback is imminent ***
  40-60 = neutral — rely on other indicators for direction

EMA(9/21):
  BULLISH_CROSS = fast EMA crossed above slow → BUY signal (strongest when recent)
  BEARISH_CROSS = fast EMA crossed below slow → SELL signal
  Price above both EMAs = bullish structure | Price below both = bearish structure

SuperTrend:
  UP = bullish trend → favour BUY on pullbacks | DOWN = bearish trend → favour SELL on rallies
  *** Trend CHANGE is a stronger signal than trend continuation ***
  *** DO NOT trade against SuperTrend unless you have a confirmed reversal candle pattern ***

VWAP:
  Price above VWAP = institutional buyers dominant (bullish)
  Price below VWAP = institutional sellers dominant (bearish)
  *** Mean-reversion: stocks >1% from VWAP tend to revert within 2-3 hours ***
  Best entries: BUY on pullback TO VWAP in uptrend | SELL on rally TO VWAP in downtrend

Volume (RVol):
  >2.0 = unusual activity → high conviction signal
  1.0-2.0 = normal → standard conviction
  *** <0.5 = NO INTEREST → SKIP this stock regardless of other signals ***

PrevDay S&R:
  AT_RESISTANCE = near yesterday's high → headwind for BUY, clean SHORT level
  AT_SUPPORT = near yesterday's low → floor for BUY, risky to SHORT
  Pivot = (H+L+C)/3 → institutional reference. Breaks above/below are significant.

Candle Patterns:
  Bullish reversal: HAMMER, BULLISH_ENGULFING, MORNING_STAR
  Bearish reversal: SHOOTING_STAR, BEARISH_ENGULFING, EVENING_STAR
  Bullish continuation: THREE_WHITE_SOLDIERS | Bearish continuation: THREE_BLACK_CROWS

MACD:
  BULLISH/GROWING = momentum accelerating up | BEARISH/GROWING = accelerating down
  *** Any direction + SHRINKING = momentum FADING → do NOT enter new trades in this direction ***

ORB:
  BREAKOUT_UP = above first 15-min high → strong BUY (ONLY before 10:30 AM — weakens rapidly after, near-zero value by 11 AM)
  BREAKOUT_DOWN = below first 15-min low → strong SELL (same time limit)

Gap:
  GAP_UP_STRONG (with volume) = continuation likely → buy pullbacks to gap edge
  GAP_UP_WEAK (low volume) = gap fill risk → possible SHORT
  GAP_DOWN_STRONG (with volume) = continuation down → sell rallies to gap edge
  GAP_DOWN_WEAK (low volume) = gap fill likely → possible BUY

Hourly EMA:
  ALIGNED_BULL = both 15-min and hourly EMA(9/21) are bullish → stronger BUY conviction
  ALIGNED_BEAR = both timeframes bearish → stronger SELL conviction
  *** Multi-timeframe agreement is a high-quality confluence signal ***

BB Squeeze:
  SQUEEZE_BULL = Bollinger Bands contracted + price above middle band → bullish breakout imminent
  SQUEEZE_BEAR = BB contracted + price below middle band → bearish breakout imminent
  *** Squeeze = low volatility → breakout is coming. Direction biased by price position ***

ADX (trend strength):
  ADX < 20 (WEAK) = range-bound market. Trend-following signals (EMA, SuperTrend continuation) are unreliable. Prefer mean-reversion setups or skip.
  ADX 20-30 (MODERATE) = developing trend. Normal signals apply.
  ADX > 30 (STRONG) = strong trend. Trend-following signals are high conviction. Counter-trend trades are VERY risky.
  *** When ADX is WEAK, EMA/SuperTrend continuation scores are automatically halved by the system ***

Fibonacci Retracement:
  AT_FIB_LEVEL = price near a Fib level (38.2/50/61.8% of prev day range) — structural S&R zone
  *** Fib levels are natural S&R where institutional traders place orders ***

VWAP Bands:
  AT_LOWER_2SD = price at VWAP -2σ → strong mean-reversion BUY signal (deeply oversold vs avg)
  AT_LOWER_1SD = price at VWAP -1σ → moderate BUY signal
  AT_UPPER_1SD = price at VWAP +1σ → moderate SELL signal
  AT_UPPER_2SD = price at VWAP +2σ → strong mean-reversion SELL signal (deeply overbought vs avg)
  *** VWAP SD bands measure how far price has deviated from institutional consensus ***

StochRSI (entry timing):
  BULLISH_CROSS = StochRSI %K crossed above %D → BUY entry timing confirmation (most useful when RSI 40-60)
  BEARISH_CROSS = StochRSI %K crossed below %D → SELL entry timing confirmation
  OVERBOUGHT = StochRSI >80 → momentum exhaustion, avoid new BUY entries
  OVERSOLD = StochRSI <20 → momentum exhaustion, avoid new SELL entries
  *** More responsive than RSI for precise intraday entry timing ***

Score:
  |score| >= 5 = high conviction (3+ aligned indicators)
  |score| 3-5 = moderate (needs confirming indicators)
  |score| < 3 = weak → skip unless other factors are very strong

══════════════════════════════════════════════════════════
HARD REJECTION FILTERS — REJECT any trade that fails even ONE:
══════════════════════════════════════════════════════════
✗ REJECT BUY if stock already UP >2% from today's open — move is EXTENDED, mean-reversion risk is high.
✗ REJECT SHORT if stock already DOWN >2% from today's open — move is EXTENDED, bounce risk is high.
✗ REJECT SHORT if RSI < 25 — stock is in CAPITULATION zone, a bounce is almost certain.
✗ REJECT BUY if RSI > 80 — stock is in EUPHORIA zone, a pullback is almost certain.
✗ REJECT if Risk:Reward < {rr_min_text} — the system enforces this floor for the current time period. Aim for ≥{self.cfg.RR_TARGET_RATIO}:1 when possible.
✗ REJECT if RVol < 0.5 — no institutional participation, random noise.
✗ REJECT if trading AGAINST SuperTrend AND no confirmed reversal candle pattern exists.
✗ REJECT if MACD momentum = SHRINKING in the trade's direction — momentum is fading.
✗ REJECT if total position cost across ALL trades would exceed Rs.{budget:,}.
✗ REJECT if adding this trade would put more than {max(1, self.cfg.MAX_POSITIONS - 1)} positions in the SAME direction — the system enforces direction diversification to avoid one-sided exposure.

══════════════════════════════════════════════════════════
CONFLUENCE REQUIREMENT — Count aligned indicators before every trade:
══════════════════════════════════════════════════════════
For BUY, count TRUE items:
  □ SuperTrend = UP
  □ EMA cross = BULLISH (or price above both EMAs)
  □ RSI 35-65 (room to run, not overbought)
  □ Bullish candle pattern present
  □ Price near/above VWAP (or pulling back to VWAP from above)
  □ MACD = BULLISH/GROWING
  □ ORB = BREAKOUT_UP (if before 11 AM)
  □ RVol > 1.5
  □ Hourly EMA = ALIGNED_BULL (multi-timeframe confirmation)
  □ BB = SQUEEZE_BULL (volatility breakout imminent)
  □ ADX > 20 (trend is developing or strong — not range-bound)
  □ Fib = AT_FIB_LEVEL (price near Fibonacci retracement level)
  □ VWAP-Band = AT_LOWER_1SD or AT_LOWER_2SD (mean-reversion support)
  □ StochRSI = BULLISH_CROSS (entry timing confirmation)

For SELL, mirror with bearish signals.
→ 2 or fewer = DO NOT TRADE (insufficient evidence)
→ 3-4 = acceptable trade (moderate conviction)
→ 5+ = strong trade (high conviction — use full position size)

Explicitly state the confluence count in your RATIONALE (e.g. "7/14 confluence").

══════════════════════════════════════════════════════════
INDIAN MARKET AWARENESS:
══════════════════════════════════════════════════════════
• If NIFTY is DOWN >1.5%: This is a RISK-OFF session. SHORT cyclicals (Banking, Auto, Metals, Infra). AVOID BUY trades except in defensive sectors (Pharma, FMCG, IT).
• If NIFTY is UP >1.5%: RISK-ON session. BUY cyclicals. AVOID shorting defensives.
• Sector relative strength: A stock outperforming its sector on a weak day = genuine strength (good BUY). A stock underperforming its sector on a strong day = hidden weakness (good SHORT).
• Thursday is weekly F&O expiry — wider intraday swings, use slightly wider SL (add 0.2%).
• BANKING stocks amplify NIFTY moves by 1.5-2×. If shorting banks, use tighter SL.
• DO NOT cluster all trades in one sector — max 2 trades in same sector to avoid correlated losses.

══════════════════════════════════════════════════════════
STOP-LOSS AND TARGET RULES:
══════════════════════════════════════════════════════════
• Base SL on the nearest STRUCTURAL LEVEL: VWAP, SuperTrend value, previous day pivot, Fibonacci retracement level, or recent swing high/low. DO NOT use arbitrary fixed percentages.
• SL range: {default_sl}% to {self.cfg.MAX_INTRADAY_SL_PCT}% from entry. The system may widen SL up to {self.cfg.MAX_INTRADAY_SL_PCT}% using ATR if the stock's volatility demands it, but prefer tighter SLs near structural levels.
• Target: minimum {self.cfg.RR_TARGET_RATIO}× the SL distance from entry. Prefer 2× for afternoon entries when time is short.
• For volatile stocks (change already >1.5%): use SL near structural support/resistance, not % based.
• NOTE: At {self.cfg.TRAIL_AFTER_RISK_MULTIPLE}× risk profit, the system AUTOMATICALLY exits 33% of the position (1/3 qty) and trails SL at {int(self.cfg.TRAIL_STEP_PCT)}% of profit. Factor this into your qty sizing — prefer qty >= 3 so partial exits can split. You do NOT need to suggest partial exits.
• NOTE: The system uses ATR-based SL (14-period, 15-min candles) when available, falling back to your SL otherwise. Set your SL at the structural level you actually want — don't add buffer. Entry price is also overridden by the live Zerodha quote at execution time.

══════════════════════════════════════════════════════════
COMMON MISTAKES TO AVOID (from actual loss patterns):
══════════════════════════════════════════════════════════
✗ Shorting a stock already down 3-5% ("it'll fall more") — it BOUNCES. RSI <30 on an extended move = EXIT not ENTRY.
✗ Buying a stock already up 3-5% — it REVERSES intraday. The easy money was made at the open.
✗ MOST IMPORTANT: ANY stock with "⚠ ExtMove" in its data line has ALREADY moved >1.5% from today's open. The technical score already penalizes this by -1.5 to -3. Do NOT buy stocks that are ALREADY UP >1.5% or short stocks ALREADY DOWN >1.5% — you are CHASING, not trading. The move is done.
✗ Shorting a stock that is UP while the market is DOWN — this stock has relative STRENGTH. It will snap back harder when selling pressure eases.
✗ Taking 4-5 trades all SHORT in a bearish market — if market reverses (common after 1 PM), ALL trades lose together. Mix directions or keep 1-2 slots empty.
✗ Ignoring volume — breakouts without volume (RVol <1.0) fail 70% of the time.
✗ Chasing gap-ups: A >1.5% gap usually partially fills. Don't buy AT the gap, buy the PULLBACK.
✗ Over-trading: With Rs.{budget:,} capital, every trade costs ~Rs.15-25 in charges. Pick 1-3 HIGH-CONVICTION trades only. Idle capital is better than forced trades. Do NOT add filler trades to deploy more capital.

PRE-FILTERED CANDIDATES (ranked by technical score):
{snapshot}

══════════════════════════════════════════════════════════
RESPONSE FORMAT — STRICTLY FOLLOW:
══════════════════════════════════════════════════════════
One block per trade. No text before or after.
If no trades pass ALL hard rejection filters, respond with: NO_TRADES_TODAY: <one-line reason why> (e.g. NO_TRADES_TODAY: all candidates extended or below RVol threshold).
Prefer FEWER high-conviction trades (1-2) over many mediocre ones.
The system enforces MAX 2 TRADES PER DAY. Return at most 2.

TRADE 1:
SYMBOL: [NSE stock symbol e.g. RELIANCE]
SIDE: [BUY or SELL]
ENTRY_PRICE: [realistic entry price in Rs., near current price]
STOP_LOSS: [stop-loss price in Rs. — state which structural level: today's L/H, Open, or PrevClose]
TARGET: [target price — at least {self.cfg.RR_TARGET_RATIO}× SL distance from entry]
QTY: [number of shares within budget constraints]
RATIONALE: [2-3 sentences: (1) confluence count X/13 and which indicators align with specific values, (2) what structural level SL is based on, (3) R:R ratio. If stock Chg >2%, explain why it's NOT an extended-move violation.]
---
TRADE 2:
...
---
===END===
"""

    # ================================================================
    # POSITION REVIEW WITH CANDLE CONTEXT
    # ================================================================

    def review_positions_v2(
        self,
        open_positions: list[dict],
        quotes: dict,
        day_pnl: float,
        budget_remaining: float,
        nifty_context: str = "",
        closed_positions: list[dict] | None = None,
    ) -> list[dict]:
        """
        Enhanced position review that includes fresh candle analysis
        for each open position. Gives Claude more data to decide
        hold/exit/adjust.
        """
        # Fetch fresh 5-min candles for each open position
        position_context = []
        for pos in open_positions:
            symbol = pos["symbol"]
            candles_5m = self._fetch_intraday_candles(symbol, pos.get("exchange", "NSE"), "5minute", days_back=1)

            tech_ctx = ""
            if len(candles_5m) >= 10:
                patterns = detect_all(candles_5m)
                ps = summarise_signals(patterns)
                rsi_val = rsi(candles_5m, period=14)
                ema_data = ema_crossover(candles_5m, fast=9, slow=21)
                today_candles = self._filter_today_candles(candles_5m)
                current_vwap = vwap(today_candles) if today_candles else 0
                stoch_data = stoch_rsi(candles_5m, rsi_period=14, stoch_period=14)

                pattern_str = ", ".join(ps["patterns"][:3]) if ps["patterns"] else "none"
                stoch_str = ""
                if stoch_data.get("signal", "NEUTRAL") != "NEUTRAL":
                    stoch_str = f"  StochRSI: {stoch_data['signal']}(K:{stoch_data['k']:.0f})"
                tech_ctx = (
                    f"  5min patterns: [{pattern_str}]  "
                    f"RSI(14): {rsi_val:.0f}  "
                    f"EMA(9/21): {ema_data['signal']}  "
                    f"VWAP: Rs.{current_vwap:.2f}"
                    f"{stoch_str}"
                )

            # Also include 15-min re-scan score if available
            rescan_ctx = ""
            result_15m = self._analyse_stock(symbol)
            if result_15m:
                rescan_score = result_15m["combined_score"]
                rescan_ctx = f"  15min Score: {rescan_score:+.1f}"

            position_context.append((pos, tech_ctx + rescan_ctx))

        # Build enhanced prompt and delegate to parent's review
        # with extra technical data injected
        prompt = self._build_v2_review_prompt(
            position_context, quotes, day_pnl, budget_remaining,
            nifty_context, closed_positions,
        )

        provider = self.cfg.AI_PROVIDER.upper()
        self.log.info(f"{provider} reviewing positions with candle analysis...")
        try:
            raw = self.claude.call(prompt)
            actions = self._parse_review_response(raw)
            self.log.success(f"{provider} review: {len(actions)} recommendations")
            return actions
        except Exception as e:
            error = ClaudeClient.classify_error(e)
            self.log.warning(f"{provider} review failed: {error} — keeping current positions")
            return []

    def _build_v2_review_prompt(
        self,
        position_context: list[tuple],
        quotes: dict,
        day_pnl: float,
        budget_remaining: float,
        nifty_context: str = "",
        closed_positions: list[dict] | None = None,
    ) -> str:
        """Builds review prompt with injected candle pattern data per position."""
        today = now_ist().date().strftime("%B %d, %Y")
        now   = now_ist().strftime("%I:%M %p")

        budget         = self._budget
        max_positions  = self.cfg.MAX_POSITIONS
        max_pct        = self.cfg.MAX_POSITION_PCT
        max_per        = budget * max_pct // 100
        max_reentries  = self.cfg.MAX_REENTRIES_PER_STOCK

        now_dt = now_ist()
        square_off = now_dt.replace(
            hour=self.cfg.SQUARE_OFF_HOUR,
            minute=self.cfg.SQUARE_OFF_MINUTE,
            second=0, microsecond=0,
        )
        mins_left = max(0, (square_off - now_dt).total_seconds() / 60)

        pos_text = ""
        for pos, tech_ctx in position_context:
            key = f"NSE:{pos['symbol']}"
            q = quotes.get(key, {})
            current_price = q.get("last_price", pos.get("entry_price", 0))
            entry = pos.get("entry_price", 0)
            pnl = (current_price - entry) * pos.get("qty", 0)
            if pos.get("side") == "SELL":
                pnl = (entry - current_price) * pos.get("qty", 0)

            sl = pos.get("stop_loss", entry)
            risk_per_share = abs(entry - sl) if sl else 0
            r_multiple = (pnl / (risk_per_share * pos.get("qty", 1))) if risk_per_share > 0 else 0

            entry_time = pos.get('entry_time', '')
            entry_time_str = f"  Entered: {entry_time}" if entry_time else ""
            pos_text += (
                f"  {pos['symbol']}: {pos['side']} {pos['qty']} shares @ Rs.{entry:.2f}  "
                f"Current: Rs.{current_price:.2f}  P&L: Rs.{pnl:.2f} ({r_multiple:+.1f}R)  "
                f"SL: Rs.{pos.get('stop_loss', 'N/A')}  Target: Rs.{pos.get('target_price', 'N/A')}"
                f"{entry_time_str}\n"
            )
            if tech_ctx:
                pos_text += f"  {tech_ctx}\n"

        # Closed trades context
        closed_text = ""
        reentry_counts: dict[str, int] = {}
        for cp in (closed_positions or []):
            sym = cp.get("symbol", "")
            reentry_counts[sym] = reentry_counts.get(sym, 0) + 1
            closed_text += (
                f"  {sym}: {cp.get('side', '?')} {cp.get('qty', 0)} shares @ Rs.{cp.get('entry_price', 0):.2f}  "
                f"Exit: Rs.{cp.get('exit_price', 0):.2f}  P&L: Rs.{cp.get('pnl', 0):.2f}  "
                f"Reason: {cp.get('exit_reason', '?')}\n"
            )

        blocked_stocks = [
            sym for sym, count in reentry_counts.items()
            if max_reentries > 0 and count >= max_reentries
        ]
        blocked_text = (
            f"\nBLOCKED FROM RE-ENTRY (already traded {max_reentries}x today): "
            + ", ".join(blocked_stocks)
            if blocked_stocks else ""
        )

        return f"""You are an expert Indian stock market intraday trader (NSE) specialising in NIFTY F&O stocks with 15 years of experience managing live positions.
Today is {today}, current time is {now} IST. Market closes at 3:30 PM, we square off at 3:10 PM.
TIME REMAINING: {mins_left:.0f} minutes until square-off.
{nifty_context}
CURRENT OPEN POSITIONS (with live 5-min technical indicators):
{pos_text if pos_text else "  (none)"}

CLOSED TRADES TODAY:
{closed_text if closed_text else "  (none)"}

DAY P&L SO FAR: Rs.{day_pnl:,.2f}
REMAINING BUDGET: Rs.{budget_remaining:,.2f}
MAX POSITIONS: {max_positions} stocks simultaneously.
MAX PER STOCK: {max_pct}% of Rs.{budget:,} = Rs.{max_per:,} max per stock.
{blocked_text}

══════════════════════════════════════════════════════════
POSITION MANAGEMENT FRAMEWORK (R-multiple based):
══════════════════════════════════════════════════════════
AUTOMATIC ACTIONS (handled by the system — do NOT suggest these):
  • At {self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R profit: system auto-exits 33% of qty and begins trailing SL (you'll see reduced qty in positions above).
  • Trailing SL: system continuously moves SL to lock in {int(self.cfg.TRAIL_STEP_PCT)}% of current profit. This is automatic — do NOT suggest SL adjustments that are LESS protective than current SL.
  • Candle re-scan: every {self.cfg.CANDLE_RESCAN_MINUTES} min, the system auto-tightens SL if a strong contrary candle signal forms.

YOUR ROLE — Use the R-multiple to guide ADDITIONAL decisions:
  Deep loser (<-0.5R): Trade thesis is FAILING. Unless a fresh reversal pattern is forming IN YOUR FAVOUR per the 5-min indicators, EXIT immediately. Do not hope for recovery.
  Losing (-0.5R to 0R): Still within initial risk. Check if indicators still support the trade direction. If yes → HOLD. If indicators have flipped → EXIT.
  Breakeven (0R to +0.5R): HOLD and let it develop. System trailing is not yet active.
  Small winner (+0.5R to +{self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R): HOLD — working as planned. System will auto-take partial profit at {self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R.
  Good winner (+{self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R to +2R): Partial profit already taken by system. Remaining position has trailing SL. HOLD unless 5-min reversal pattern is forming — then EXIT remainder.
  Large winner (>+2R): System trailing is active. Consider whether target is still achievable in remaining time. If time is short (<60 min), suggest ADJUST_TARGET closer.

══════════════════════════════════════════════════════════
REVIEW RULES — MUST FOLLOW:
══════════════════════════════════════════════════════════
1. USE THE 5-MIN TECHNICAL INDICATORS shown for each position:
   • Reversal candle AGAINST your position (e.g. HAMMER forming on your SHORT) → EXIT or tighten SL to within 0.3%.
   • RSI divergence (RSI rising while price falling on your SHORT, or vice versa) → early warning, tighten SL.
   • EMA cross AGAINST your position → strong exit signal unless within 0.3% of target.

2. RSI EXTREMES (check before acting):
   • LONG position with RSI >75 → if system already took partial profit (qty reduced), HOLD remainder with tight SL. If no partial taken yet, suggest EXIT.
   • SHORT position with RSI <25 → same logic: if partial already taken, HOLD. If not, suggest EXIT.
   • These are exhaustion zones — but if the auto-trail already locked in profit on the remainder, let it ride to target or tight SL hit.

3. TRAILING STOP (handled automatically by the system at {int(self.cfg.TRAIL_STEP_PCT)}% of profit):
   The system auto-trails SL. You should only suggest ADJUST_SL if you want to tighten SL MORE than the auto-trail (e.g. due to a bearish reversal candle on a long position).
   *** NEVER suggest loosening SL (moving it further from current price). ***

4. TIME MANAGEMENT (based on {mins_left:.0f} min remaining):
   • >120 min: Full discretion. HOLD winners, manage losers normally.
   • 60-120 min: No new trades unless score ≥5.
   • 30-60 min: EXIT any position that is underwater. HOLD only profitable positions with strong momentum.
   • <30 min: EXIT ALL positions unless they are within 0.3% of target. Do NOT hold into square-off hoping for last-minute moves.
   NOTE: The system applies a {self.cfg.TARGET_DECAY_PCT:.0f}% time-decay target compression on OPEN positions after {self.cfg.TARGET_DECAY_AFTER_HOUR}:00. Targets shown above reflect this adjustment. Only suggest ADJUST_TARGET for trend-based reasons, not for time alone.

5. CUT LOSERS: If position is underwater AND 5-min candle shows a reversal pattern forming (e.g. HAMMER on your SHORT), EXIT immediately. Dead positions that drift sideways for 2+ reviews should also be exited — capital is better used elsewhere.

6. DO NOT AVERAGE DOWN on any existing position.

7. PROTECT WINNERS: DO NOT exit a profitable position just because of minor time pressure if 30+ min remain AND the 5-min trend (EMA, SuperTrend direction) still supports your trade direction. Only exit winners if the trend has clearly reversed per the indicators.

8. NIFTY ALIGNMENT: If NIFTY has reversed direction since your trade entry (e.g. you're LONG but NIFTY turned bearish), tighten SL to within 0.5% of current price regardless of other factors.

9. NEW TRADES IN REVIEW: Be very selective. Only suggest if ALL of:
   • 60+ minutes remain
   • Strong technical setup (score ≥ 5 with 3+ indicator confluence)
   • Stock is NOT already extended (within ±2% of today's open)
   • Budget available and would not exceed max positions

Review each position. For each, respond:

REVIEW 1:
SYMBOL: [symbol]
ACTION: [HOLD | EXIT | ADJUST_SL | ADJUST_TARGET]
NEW_SL: [new stop-loss price if ADJUST_SL, otherwise blank]
NEW_TARGET: [new target price if ADJUST_TARGET, otherwise blank]
REASON: [1-2 sentences — reference specific R-multiple, time remaining, and technical indicators (RSI value, candle pattern, EMA direction) that support your decision]
---

For new trades (optional, strict criteria above):
NEW_TRADE:
SYMBOL: [symbol]
SIDE: [BUY or SELL]
ENTRY_PRICE: [price]
STOP_LOSS: [price based on structural level]
TARGET: [price — reduced target given time remaining]
QTY: [must satisfy: QTY × ENTRY ≤ Rs.{min(budget_remaining, max_per):,.0f}]
RATIONALE: [1-2 sentences — confluence count, which indicators align, and why this is worth the late-day risk]
---
===END===
"""

    # ================================================================
    # CANDLE-BASED MONITOR HINTS
    # ================================================================

    def should_increase_poll_rate(self, positions: list[dict], quotes: dict) -> bool:
        """
        Returns True if any position is within 0.5% of SL or target,
        suggesting we should poll more frequently.
        """
        for pos in positions:
            key = f"{pos['exchange']}:{pos['symbol']}"
            q = quotes.get(key, {})
            price = q.get("last_price", 0)
            if price <= 0:
                continue

            sl = pos["stop_loss"]
            target = pos["target_price"]

            if pos["side"] == "BUY":
                sl_dist = abs(price - sl) / price * 100
                tgt_dist = abs(target - price) / price * 100
            else:
                sl_dist = abs(sl - price) / price * 100
                tgt_dist = abs(price - target) / price * 100

            if sl_dist < 0.5 or tgt_dist < 0.5:
                return True

        return False
