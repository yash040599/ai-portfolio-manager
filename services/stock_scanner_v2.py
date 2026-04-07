# ================================================================
# services/stock_scanner_v2.py
# ================================================================
# V2 stock scanner: candle-pattern + technical-indicator pre-filter
# before sending candidates to Claude for final trade selection.
#
# WHY V2 EXISTS:
# V1 sends ALL 50-100+ stock prices to Claude as a flat text table.
# Claude picks trades purely from price/volume data + its training.
# This is like asking a doctor to diagnose from a photo — it works
# sometimes but has no structured clinical data.
#
# V2 first runs FREE mathematical analysis (no Claude cost) on every
# stock: candlestick patterns, EMA crossover, RSI, VWAP, SuperTrend.
# Stocks are ranked by a composite score. Only the top 15 candidates
# with the strongest technical setups are sent to Claude, along with
# their exact indicator values (RSI=28, SuperTrend=UP, etc.).
#
# Result: Claude sees fewer stocks but with much richer data per stock.
# It can reason about specific indicator confluences instead of
# guessing from raw prices.
#
# FLOW:
#   1. Fetch 15-min and daily candles for the entire universe
#      (sequential Zerodha API calls — ~2-3 min for NIFTY100)
#   2. Run candle pattern detection + technical indicators on each
#   3. Filter by V2_MIN_SCORE, rank by composite score
#   4. Send top 15 filtered candidates to Claude with enriched data
#   5. Claude picks final trades from the pre-filtered set
#
# DURING MONITORING:
#   - Position reviews include fresh 5-min candle data per position
#   - Claude can see real-time pattern formations on open positions
# ================================================================

import datetime

from config                          import Config
from core.logger                     import Logger
from core.claude_client              import ClaudeClient
from core.zerodha_client             import ZerodhaClient
from services.stock_scanner          import StockScanner, _parse_price, _parse_int
from services.candle_patterns        import detect_all, detect_all_with_freshness, summarise_signals
from services.technical_indicators   import (
    compute_technical_score, prev_day_sr_score,
    vwap, rsi, ema_crossover, supertrend,
)
from services.candle_cache           import CandleCache


# Maximum candidates to send to Claude (rest are filtered out by math)
MAX_CANDIDATES = 15

# Maximum positions allowed per sector (prevents correlated drawdowns)
MAX_PER_SECTOR = 2

# ================================================================
# SECTOR MAPPING — NSE NIFTY STOCKS
# ================================================================
# Used by the sector diversification filter.
# Stocks not in this map default to "OTHER".
# ================================================================

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
    "TECHM": "IT", "LTIM": "IT", "NAUKRI": "IT", "OFSS": "IT",
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


class StockScannerV2(StockScanner):
    """
    Extends StockScanner with candle-pattern and technical-indicator
    pre-filtering. Overrides scan() to add the filtering layer.
    """

    def __init__(
        self,
        config:  type[Config],
        claude:  ClaudeClient,
        zerodha: ZerodhaClient,
        log:     Logger,
    ):
        super().__init__(config, claude, log)
        self.zerodha = zerodha
        self._cache = CandleCache()

        # Cleanup old cached data on startup (keep 45 days)
        try:
            cleaned = self._cache.cleanup_old(keep_days=45)
            if cleaned:
                self.log.info(f"Candle cache: cleaned {cleaned} old entries")
        except Exception:
            pass

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
        today = datetime.date.today()

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
                    symbol, exchange, from_dt, datetime.datetime.now(), interval,
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
                now = datetime.datetime.now()
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
                            symbol, exchange, from_dt, datetime.datetime.now(), interval,
                        )
                        if all_candles:
                            self._cache.store_candles(symbol, exchange, interval, all_candles)
                            return all_candles
                    except Exception:
                        pass
                    return live

            return cached + live

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
        today = datetime.date.today()
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
    ) -> dict | None:
        """
        Runs full technical analysis on one stock:
        - 15-min candle patterns (with freshness decay + volume confirmation)
        - Technical indicators (EMA, RSI, VWAP, SuperTrend, prev-day S&R)
        - Relative Volume (RVol) scoring
        - Daily candle context

        Returns a scored dict or None if insufficient data.
        """
        candles_15m = self._fetch_intraday_candles(symbol, exchange, "15minute", days_back=2)
        candles_day = self._fetch_daily_candles(symbol, exchange, days_back=30)

        if len(candles_15m) < 10:
            return None

        # Candle patterns with freshness decay and volume confirmation
        patterns = detect_all_with_freshness(candles_15m)
        pattern_summary = summarise_signals(patterns)

        # Current price
        current_price = candles_15m[-1]["close"] if candles_15m else 0

        # Technical indicators (now includes prev-day S&R)
        tech = compute_technical_score(candles_15m, candles_day, current_price)

        # Combine scores: candle patterns + technical indicators
        combined_score = pattern_summary["score"] + tech["score"]

        # ── Relative Volume (RVol) bonus/penalty ──────────────
        # Compare today's volume so far to the average from recent
        # daily candles, pro-rated to full day. Without pro-rating,
        # early-morning scans (1-2 candles) would show tiny RVol.
        # NSE session: 9:15 AM – 3:30 PM = 375 min = 25 × 15-min candles.
        rvol = 0.0
        today_candles = self._filter_today_candles(candles_15m)
        if today_candles and candles_day and len(candles_day) >= 5:
            n_today = len(today_candles)
            # Need at least 4 candles (~1 hour) for reliable pro-rating.
            # The first 1-2 candles carry disproportionate volume from
            # the NSE opening auction, making early pro-rating unreliable.
            if n_today >= 4:
                today_vol = sum(c.get("volume", 0) for c in today_candles)
                # Pro-rate to full-day estimate (25 fifteen-min candles per session)
                prorated_vol = today_vol * (25 / n_today)
                recent_vols = [d.get("volume", 0) for d in candles_day[-5:] if d.get("volume", 0) > 0]
                if recent_vols:
                    avg_daily_vol = sum(recent_vols) / len(recent_vols)
                    if avg_daily_vol > 0:
                        rvol = prorated_vol / avg_daily_vol
                        if rvol > 2.0:
                            combined_score += 1   # unusual volume = bonus
                        elif rvol < 0.3:
                            combined_score -= 1   # dead volume = penalty

        # VWAP for the trading day
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

    def _filter_today_candles(self, candles: list[dict]) -> list[dict]:
        """Filters candles to only today's intraday data (for VWAP)."""
        today = datetime.date.today()
        result = []
        for c in candles:
            dt = c.get("date")
            if dt is None:
                continue
            if hasattr(dt, "date"):
                cdate = dt.date()
            else:
                cdate = dt
            if cdate == today:
                result.append(c)
        return result

    # ================================================================
    # PRE-FILTER SCAN (MATH-BASED, FREE)
    # ================================================================

    def _prefilter_universe(self, quotes: dict, nifty_trend: str = "", min_score_override: float | None = None) -> list[dict]:
        """
        Analyses all stocks in the universe using candle patterns
        and technical indicators. Returns the top candidates ranked
        by combined score.

        If nifty_trend is "BEARISH", BUY signals need a higher score
        threshold (≥5 instead of default). Vice versa for "BULLISH"
        — SELL signals need ≥5. This prevents trading against the
        broad market direction with weak signals.

        Stocks below V2_MIN_SCORE are filtered out entirely.
        Both bullish AND bearish signals pass through (since we can
        trade both directions in intraday).
        """
        universe = self.get_universe()
        self.log.info(f"V2 pre-filter: analysing {len(universe)} stocks with candle patterns...")

        scored = []
        for i, symbol in enumerate(universe):
            # Progress indicator for large universes
            if (i + 1) % 20 == 0:
                self.log.info(f"  ...analysed {i + 1}/{len(universe)}")

            result = self._analyse_stock(symbol)
            if result:
                scored.append(result)

        self.log.info(f"  Analysed {len(scored)} stocks with sufficient candle data")

        # Filter out weak signals below V2_MIN_SCORE threshold
        min_score = min_score_override if min_score_override is not None else self.cfg.V2_MIN_SCORE
        passed_score = []
        for s in scored:
            if abs(s["combined_score"]) >= min_score:
                passed_score.append(s)

        dropped_score = len(scored) - len(passed_score)
        if dropped_score:
            self.log.info(f"  Score filter: dropped {dropped_score} stocks below |score| {min_score}")

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

        if top:
            self.log.info(f"  Top {len(top)} candidates by technical score:")
            for r in top:
                ps = r["pattern_summary"]
                patterns_str = ", ".join(ps["patterns"][:3]) if ps["patterns"] else "none"
                rvol_str = f"  RVol: {r['rvol']:.1f}x" if r.get("rvol", 0) > 0 else ""
                self.log.info(
                    f"    {r['symbol']:<14} score: {r['combined_score']:>+5.1f}  "
                    f"tech: {r['technical']['signal']:<12} "
                    f"patterns: {patterns_str}{rvol_str}"
                )

        return top

    # ================================================================
    # OVERRIDE: SCAN WITH PRE-FILTERING
    # ================================================================

    def scan(self, quotes: dict, nifty_context: str = "", perf_context: str = "", session_context: str = "") -> list[dict]:
        """
        V2 scan: pre-filter with candle math, then send top candidates
        to Claude with enriched technical data.
        """
        # Extract Nifty trend from context string for hard filter
        nifty_trend = ""
        if "BEARISH" in nifty_context.upper():
            nifty_trend = "BEARISH"
        elif "BULLISH" in nifty_context.upper():
            nifty_trend = "BULLISH"

        # Step 1: Math-based pre-filter (with Nifty trend hard filter)
        candidates = self._prefilter_universe(quotes, nifty_trend)

        if not candidates:
            self.log.warning("V2 pre-filter found no candidates with signals")
            # Fall back to V1 behaviour (send all quotes to Claude)
            return super().scan(quotes, nifty_context, perf_context, session_context)

        # Step 2: Build enriched snapshot for Claude (only candidates)
        snapshot = self._build_enriched_snapshot(candidates, quotes)

        if not snapshot:
            self.log.warning("No valid enriched snapshot — falling back to V1")
            return super().scan(quotes, nifty_context, perf_context, session_context)

        # Step 3: Send to Claude with technical context
        prompt = self._build_v2_scan_prompt(snapshot, nifty_context, perf_context, session_context)

        self.log.info("Asking Claude to pick trades from pre-filtered candidates...")
        try:
            raw = self.claude.call(prompt)
            trades = self._parse_scan_response(raw)
            trades = self._boost_underdeployed(trades)
            self.log.success(f"Claude recommended {len(trades)} trades from {len(candidates)} candidates")
            return trades
        except Exception as e:
            error = ClaudeClient.classify_error(e)
            self.log.error(f"V2 scan failed: {error}")
            return []

    # ================================================================
    # NO-AI SCAN — AUTO-SELECT FROM TECHNICAL SCORES
    # ================================================================

    def scan_noai(
        self, quotes: dict, nifty_context: str = "",
        max_trades: int = 0, session_context: str = "",
        day_pnl: float = 0.0,
    ) -> list[dict]:
        """
        Selects trades purely from technical scores — no Claude call.
        Uses the same candle pre-filter as V2, then auto-generates
        trade plans from the top candidates.

        Returns a list of trade dicts identical to what Claude would
        produce (same keys: symbol, side, entry_price, stop_loss,
        target_price, qty, rationale, status).
        """
        if max_trades <= 0:
            max_trades = self.cfg.MAX_POSITIONS
        if max_trades <= 0:
            self.log.warning("NoAI scan: MAX_POSITIONS is 0 — cannot select trades")
            return []

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
                min_score_override = self.cfg.V2_MIN_SCORE + self.cfg.LOSS_SCORE_BUMP_AMOUNT
                self.log.info(
                    f"Dynamic score threshold: day loss {loss_pct:.1f}% ≥ "
                    f"{self.cfg.LOSS_SCORE_BUMP_PCT}% — raising MIN_SCORE "
                    f"to {min_score_override:.1f}"
                )

        # Step 1: Math-based pre-filter
        candidates = self._prefilter_universe(quotes, nifty_trend, min_score_override)
        if not candidates:
            self.log.warning("NoAI scan: no candidates passed pre-filter")
            return []

        # Step 2: Take the top N candidates by absolute score
        top = candidates[:max_trades]

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
            side = "BUY" if score > 0 else "SELL"

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
            if ps["patterns"]:
                parts.append(f"Patterns: {', '.join(ps['patterns'][:2])}")
            if c.get("rvol", 0) > 1.5:
                parts.append(f"RVol {c['rvol']:.1f}x")

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
            })

        # Validate budget (same logic as Claude path)
        trades = self._validate_budget(trades)
        trades = self._boost_underdeployed(trades)
        self.log.success(f"NoAI scan: selected {len(trades)} trades from {len(candidates)} candidates")
        return trades

    # ================================================================
    # MINIMUM CAPITAL DEPLOYMENT BOOST
    # ================================================================

    def _boost_underdeployed(self, trades: list[dict]) -> list[dict]:
        """
        If total trade cost is below MIN_BUDGET_UTILISATION_PCT of
        budget, proportionally increase qty on each trade to reach
        the minimum. Respects MAX_POSITION_PCT per-stock limit.
        """
        min_util_pct = self.cfg.MIN_BUDGET_UTILISATION_PCT
        if min_util_pct <= 0 or not trades:
            return trades

        budget = self._budget
        min_deploy = budget * min_util_pct / 100
        max_pct = self.cfg.MAX_POSITION_PCT / 100
        max_per = budget * max_pct

        total_cost = sum(t["entry_price"] * t["qty"] for t in trades)
        if total_cost >= min_deploy:
            return trades  # already meeting minimum

        self.log.info(
            f"Capital under-deployed: ₹{total_cost:,.0f} / ₹{budget:,.0f} "
            f"({total_cost / budget * 100:.0f}%) — minimum is {min_util_pct:.0f}%"
        )

        # Boost each trade proportionally, respecting per-stock cap
        scale = min_deploy / total_cost if total_cost > 0 else 1
        new_total = 0
        for t in trades:
            entry = t["entry_price"]
            if entry <= 0:
                continue
            target_cost = min(entry * t["qty"] * scale, max_per)
            new_qty = max(t["qty"], int(target_cost / entry))
            # Don't exceed budget cap for this single stock
            if new_qty * entry > max_per:
                new_qty = int(max_per / entry)
            if new_qty > t["qty"]:
                self.log.info(
                    f"  {t['symbol']}: qty {t['qty']} → {new_qty} "
                    f"(₹{t['qty'] * entry:,.0f} → ₹{new_qty * entry:,.0f})"
                )
                t["qty"] = new_qty
            new_total += t["entry_price"] * t["qty"]

        # Final budget check — don't exceed total budget
        if new_total > budget:
            # Scale back the last trade(s) to fit
            excess = new_total - budget
            for t in reversed(trades):
                entry = t["entry_price"]
                if entry <= 0:
                    continue
                reduce_qty = min(t["qty"] - 1, int(excess / entry) + 1)
                if reduce_qty > 0:
                    t["qty"] -= reduce_qty
                    excess -= reduce_qty * entry
                if excess <= 0:
                    break

        final_cost = sum(t["entry_price"] * t["qty"] for t in trades)
        self.log.info(
            f"After boost: ₹{final_cost:,.0f} / ₹{budget:,.0f} "
            f"({final_cost / budget * 100:.0f}%)"
        )
        return trades

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
                sr_str += f"  Pivot: ₹{sr['pivot']:.2f}"

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

            lines.append(
                f"{symbol:<14} "
                f"₹{price:>10.2f}  Chg: {change_pct:>+6.2f}%  "
                f"Vol: {volume:>12,}{rvol_str}  "
                f"VWAP: ₹{c['vwap']:.2f}  "
                f"RSI: {rsi_val:.0f}  "
                f"EMA(9/21): {ema_info['signal']}  "
                f"SuperTrend: {st_info['trend']}  "
                f"Score: {c['combined_score']:+.1f}  "
                f"Patterns: [{patterns}]{sr_str}{macd_str}{orb_str}{gap_str}{hourly_str}{bb_str}"
            )

        return "\n".join(lines)

    def _build_v2_scan_prompt(
        self, snapshot: str, nifty_context: str = "",
        perf_context: str = "", session_context: str = "",
    ) -> str:
        """Claude prompt with enriched technical data for V2 candidates."""
        today  = datetime.date.today().strftime("%B %d, %Y")
        now    = datetime.datetime.now().strftime("%I:%M %p")
        budget = self._budget
        max_positions  = self.cfg.MAX_POSITIONS
        max_pct        = self.cfg.MAX_POSITION_PCT
        default_sl     = self.cfg.DEFAULT_STOP_LOSS_PCT
        default_target = self.cfg.DEFAULT_TARGET_PCT

        # Time-of-day context for strategy adaptation
        hour = datetime.datetime.now().hour
        minute = datetime.datetime.now().minute
        if hour == 9 and minute < 45:
            time_phase = "OPENING (9:15-9:45 AM): High volatility. ORB setups are strongest now. Wait for 15-min candle close before entry. Avoid chasing opening spikes."
        elif hour < 11:
            time_phase = "MORNING TREND (9:45-11:00 AM): Best trending window of the day. Momentum and breakout trades have highest success. Use full position sizes."
        elif hour < 13:
            time_phase = "MIDDAY LULL (11:00 AM-1:00 PM): Volume drops, ranges narrow. Favour mean-reversion setups near VWAP. Reduce conviction on breakout trades."
        elif hour < 14:
            time_phase = "AFTERNOON (1:00-2:00 PM): European markets opening can bring fresh volatility. Reduce targets by 30% due to less time for trades to play out."
        else:
            time_phase = "LATE SESSION (after 2:00 PM): Only take HIGH conviction setups (score >= 5 with 3+ confluences). Reduce targets by 50%."

        min_util = self.cfg.MIN_BUDGET_UTILISATION_PCT

        return f"""You are an expert Indian stock market intraday trader (NSE) specialising in NIFTY F&O stocks.
You have 15 years of experience with deep knowledge of Indian market microstructure — FII/DII flow dynamics, weekly F&O expiry effects, sector rotation, and NSE intraday volume patterns.

Today is {today}, current time is {now} IST. All positions MUST be closed by 3:10 PM IST today.
CURRENT TIME PHASE: {time_phase}

BUDGET: ₹{budget:,} total capital.
MAX POSITIONS: {max_positions} stocks simultaneously (₹{budget // max_positions:,} per slot).
MAX PER STOCK: {max_pct}% of budget (= ₹{budget * max_pct // 100:,} max per stock).
MINIMUM DEPLOYMENT: Deploy at least {min_util:.0f}% of budget (= ₹{budget * min_util / 100:,.0f}) across your trades. Do this by sizing HIGH-CONVICTION picks with larger qty — NOT by adding mediocre trades. Idle capital earns nothing intraday.
{nifty_context}{perf_context}{session_context}
The stocks below are PRE-FILTERED by mathematical technical analysis.
Each stock shows real-time indicators — use them for precise, evidence-based decisions.

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
  BREAKOUT_UP = above first 15-min high → strong BUY (best before 10:30 AM, weakens after 11)
  BREAKOUT_DOWN = below first 15-min low → strong SELL

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

Score:
  |score| >= 5 = high conviction (3+ aligned indicators)
  |score| 3-5 = moderate (needs confirming indicators)
  |score| < 3 = weak → skip unless other factors are very strong

══════════════════════════════════════════════════════════
HARD REJECTION FILTERS — REJECT any trade that fails even ONE:
══════════════════════════════════════════════════════════
✗ REJECT BUY if stock already UP >2% from previous close — move is EXTENDED, mean-reversion risk is high.
✗ REJECT SHORT if stock already DOWN >2% from previous close — move is EXTENDED, bounce risk is high.
✗ REJECT SHORT if RSI < 25 — stock is in CAPITULATION zone, a bounce is almost certain.
✗ REJECT BUY if RSI > 80 — stock is in EUPHORIA zone, a pullback is almost certain.
✗ REJECT if Risk:Reward < 1:1.5 — not enough edge to cover costs and slippage.
✗ REJECT if RVol < 0.5 — no institutional participation, random noise.
✗ REJECT if trading AGAINST SuperTrend AND no confirmed reversal candle pattern exists.
✗ REJECT if MACD momentum = SHRINKING in the trade's direction — momentum is fading.
✗ REJECT if total position cost across ALL trades would exceed ₹{budget:,}.

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

For SELL, mirror with bearish signals.
→ 2 or fewer = DO NOT TRADE (insufficient evidence)
→ 3-4 = acceptable trade (moderate conviction)
→ 5+ = strong trade (high conviction — use full position size)

Explicitly state the confluence count in your RATIONALE (e.g. "5/10 confluence").

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
• Base SL on the nearest STRUCTURAL LEVEL: VWAP, SuperTrend value, previous day pivot, or recent swing high/low. DO NOT use arbitrary fixed percentages.
• SL range: {default_sl}% to 2% from entry. The system may widen SL up to {self.cfg.MAX_INTRADAY_SL_PCT}% using ATR if the stock's volatility demands it, but prefer tighter SLs near structural levels.
• Target: minimum 1.5× the SL distance from entry. Prefer 2× for afternoon entries when time is short.
• For volatile stocks (change already >1.5%): use SL near structural support/resistance, not % based.
• NOTE: At 1× risk profit, the system AUTOMATICALLY exits 50% of the position and trails SL. Factor this into your qty sizing — prefer qty >= 2 so partial exits can split. Claude does NOT need to suggest partial exits.
• NOTE: The system may tighten your SL further using ATR (14-period, 15-min candles). It uses the TIGHTER of Claude's SL vs ATR SL. So a wider SL you suggest may be narrowed automatically. Entry price is also overridden by the live Zerodha quote at execution time.

══════════════════════════════════════════════════════════
COMMON MISTAKES TO AVOID (from actual loss patterns):
══════════════════════════════════════════════════════════
✗ Shorting a stock already down 3-5% ("it'll fall more") — it BOUNCES. RSI <30 on an extended move = EXIT not ENTRY.
✗ Buying a stock already up 3-5% — it REVERSES intraday. The easy money was made at the open.
✗ Shorting a stock that is UP while the market is DOWN — this stock has relative STRENGTH. It will snap back harder when selling pressure eases.
✗ Taking 4-5 trades all SHORT in a bearish market — if market reverses (common after 1 PM), ALL trades lose together. Mix directions or keep 1-2 slots empty.
✗ Ignoring volume — breakouts without volume (RVol <1.0) fail 70% of the time.
✗ Chasing gap-ups: A >1.5% gap usually partially fills. Don't buy AT the gap, buy the PULLBACK.
✗ Over-trading: With ₹{budget:,} capital, every trade costs ~₹15-25 in charges. Fewer high-conviction trades > many mediocre trades. 2-3 good trades with large qty beats 5 small trades. Meet deployment target by sizing up your best picks, not adding filler trades.

PRE-FILTERED CANDIDATES (ranked by technical score):
{snapshot}

══════════════════════════════════════════════════════════
RESPONSE FORMAT — STRICTLY FOLLOW:
══════════════════════════════════════════════════════════
One block per trade. No text before or after.
If no trades pass ALL hard rejection filters, respond with exactly: NO_TRADES_TODAY
Prefer FEWER high-conviction trades (2-3) over many mediocre ones.

TRADE 1:
SYMBOL: [NSE stock symbol]
SIDE: [BUY or SELL]
ENTRY_PRICE: [realistic entry price near current price]
STOP_LOSS: [price based on structural level — state which: VWAP/SuperTrend/pivot/swing]
TARGET: [target price — at least 1.5× SL distance from entry]
QTY: [number of shares within budget constraints]
RATIONALE: [2-3 sentences: (1) confluence count X/10 and which indicators align with specific values, (2) what structural level SL is based on, (3) R:R ratio. If stock Chg >2%, explain why it's NOT an extended-move violation.]
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

                pattern_str = ", ".join(ps["patterns"][:3]) if ps["patterns"] else "none"
                tech_ctx = (
                    f"  5min patterns: [{pattern_str}]  "
                    f"RSI(14): {rsi_val:.0f}  "
                    f"EMA(9/21): {ema_data['signal']}  "
                    f"VWAP: ₹{current_vwap:.2f}"
                )

            position_context.append((pos, tech_ctx))

        # Build enhanced prompt and delegate to parent's review
        # with extra technical data injected
        prompt = self._build_v2_review_prompt(
            position_context, quotes, day_pnl, budget_remaining,
            nifty_context, closed_positions,
        )

        self.log.info("Claude reviewing positions with candle analysis...")
        try:
            raw = self.claude.call(prompt)
            actions = self._parse_review_response(raw)
            self.log.success(f"Claude V2 review: {len(actions)} recommendations")
            return actions
        except Exception as e:
            error = ClaudeClient.classify_error(e)
            self.log.warning(f"Claude V2 review failed: {error} — keeping current positions")
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
        today = datetime.date.today().strftime("%B %d, %Y")
        now   = datetime.datetime.now().strftime("%I:%M %p")

        budget         = self._budget
        max_positions  = self.cfg.MAX_POSITIONS
        max_pct        = self.cfg.MAX_POSITION_PCT
        max_per        = budget * max_pct // 100
        max_reentries  = self.cfg.MAX_REENTRIES_PER_STOCK

        now_dt = datetime.datetime.now()
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
                f"  {pos['symbol']}: {pos['side']} {pos['qty']} shares @ ₹{entry:.2f}  "
                f"Current: ₹{current_price:.2f}  P&L: ₹{pnl:.2f} ({r_multiple:+.1f}R)  "
                f"SL: ₹{pos.get('stop_loss', 'N/A')}  Target: ₹{pos.get('target_price', 'N/A')}"
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
                f"  {sym}: {cp.get('side', '?')} {cp.get('qty', 0)} shares @ ₹{cp.get('entry_price', 0):.2f}  "
                f"Exit: ₹{cp.get('exit_price', 0):.2f}  P&L: ₹{cp.get('pnl', 0):.2f}  "
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

DAY P&L SO FAR: ₹{day_pnl:,.2f}
REMAINING BUDGET: ₹{budget_remaining:,.2f}
MAX POSITIONS: {max_positions} stocks simultaneously.
MAX PER STOCK: {max_pct}% of ₹{budget:,} = ₹{max_per:,} max per stock.
{blocked_text}

══════════════════════════════════════════════════════════
POSITION MANAGEMENT FRAMEWORK (R-multiple based):
══════════════════════════════════════════════════════════
AUTOMATIC ACTIONS (handled by the system — do NOT suggest these):
  • At 1R profit: system auto-exits 50% of qty and begins trailing SL (you'll see reduced qty in positions above).
  • Trailing SL: system continuously moves SL to lock in {int(self.cfg.TRAIL_STEP_PCT)}% of current profit. This is automatic — do NOT suggest SL adjustments that are LESS protective than current SL.
  • Candle re-scan: every {self.cfg.V2_CANDLE_RESCAN_MINUTES} min, the system auto-tightens SL if a strong contrary candle signal forms.

YOUR ROLE — Use the R-multiple to guide ADDITIONAL decisions:
  Deep loser (<-0.5R): Trade thesis is FAILING. Unless a fresh reversal pattern is forming IN YOUR FAVOUR per the 5-min indicators, EXIT immediately. Do not hope for recovery.
  Losing (-0.5R to 0R): Still within initial risk. Check if indicators still support the trade direction. If yes → HOLD. If indicators have flipped → EXIT.
  Breakeven (0R to +0.5R): HOLD and let it develop. System trailing is not yet active.
  Small winner (+0.5R to +1R): HOLD — working as planned. System will auto-take partial profit at 1R.
  Good winner (+1R to +2R): Partial profit already taken by system. Remaining position has trailing SL. HOLD unless 5-min reversal pattern is forming — then EXIT remainder.
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
   • 60-120 min: Reduce open targets by 30%. No new trades unless score ≥5.
   • 30-60 min: Reduce open targets by 50%. EXIT any position that is underwater. HOLD only profitable positions with strong momentum.
   • <30 min: EXIT ALL positions unless they are within 0.3% of target. Do NOT hold into square-off hoping for last-minute moves.

5. CUT LOSERS: If position is underwater AND 5-min candle shows a reversal pattern forming (e.g. HAMMER on your SHORT), EXIT immediately. Dead positions that drift sideways for 2+ reviews should also be exited — capital is better used elsewhere.

6. DO NOT AVERAGE DOWN on any existing position.

7. PROTECT WINNERS: DO NOT exit a profitable position just because of minor time pressure if 30+ min remain AND the 5-min trend (EMA, SuperTrend direction) still supports your trade direction. Only exit winners if the trend has clearly reversed per the indicators.

8. NIFTY ALIGNMENT: If NIFTY has reversed direction since your trade entry (e.g. you're LONG but NIFTY turned bearish), tighten SL to within 0.5% of current price regardless of other factors.

9. NEW TRADES IN REVIEW: Be very selective. Only suggest if ALL of:
   • 60+ minutes remain
   • Strong technical setup (score ≥ 5 with 3+ indicator confluence)
   • Stock is NOT already extended (within ±2% of previous close)
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
QTY: [must satisfy: QTY × ENTRY ≤ ₹{min(budget_remaining, max_per):,.0f}]
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
