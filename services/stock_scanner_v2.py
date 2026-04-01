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
from services.candle_patterns        import detect_all, summarise_signals
from services.technical_indicators   import (
    compute_technical_score, vwap, rsi, ema_crossover,
)


# Maximum candidates to send to Claude (rest are filtered out by math)
MAX_CANDIDATES = 15


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
        Returns list of candle dicts: {date, open, high, low, close, volume}.
        Returns empty list on failure (non-blocking).
        """
        to_dt = datetime.datetime.now()
        from_dt = to_dt - datetime.timedelta(days=days_back)

        try:
            candles = self.zerodha.get_historical(
                symbol, exchange, from_dt, to_dt, interval,
            )
            return candles if candles else []
        except Exception:
            return []

    def _fetch_daily_candles(
        self,
        symbol: str,
        exchange: str = "NSE",
        days_back: int = 30,
    ) -> list[dict]:
        """Fetches daily candles for trend context."""
        to_dt = datetime.date.today()
        from_dt = to_dt - datetime.timedelta(days=days_back)

        try:
            candles = self.zerodha.get_historical(
                symbol, exchange, from_dt, to_dt, "day",
            )
            return candles if candles else []
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
        - 15-min candle patterns
        - Technical indicators (EMA, RSI, VWAP, SuperTrend)
        - Daily candle context

        Returns a scored dict or None if insufficient data.
        """
        candles_15m = self._fetch_intraday_candles(symbol, exchange, "15minute", days_back=2)
        candles_day = self._fetch_daily_candles(symbol, exchange, days_back=30)

        if len(candles_15m) < 10:
            return None

        # Candle patterns on 15-min chart
        patterns = detect_all(candles_15m)
        pattern_summary = summarise_signals(patterns)

        # Technical indicators
        tech = compute_technical_score(candles_15m, candles_day)

        # Combine scores: candle patterns + technical indicators
        combined_score = pattern_summary["score"] + tech["score"]

        # VWAP for the trading day
        today_candles = self._filter_today_candles(candles_15m)
        current_vwap = vwap(today_candles) if today_candles else 0

        # Current price
        current_price = candles_15m[-1]["close"] if candles_15m else 0

        return {
            "symbol":          symbol,
            "exchange":        exchange,
            "current_price":   current_price,
            "combined_score":  combined_score,
            "pattern_summary": pattern_summary,
            "technical":       tech,
            "vwap":            current_vwap,
            "candle_count":    len(candles_15m),
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

    def _prefilter_universe(self, quotes: dict) -> list[dict]:
        """
        Analyses all stocks in the universe using candle patterns
        and technical indicators. Returns the top candidates ranked
        by combined score.

        NOTE: This makes 2 sequential API calls per stock (15-min +
        daily candles). For NIFTY100 = ~200 calls, taking 2-3 minutes.
        This runs once pre-market so latency is acceptable.

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
        min_score = self.cfg.V2_MIN_SCORE
        scored = [s for s in scored if abs(s["combined_score"]) >= min_score]

        # Sort by absolute combined score (strongest signals first)
        scored.sort(key=lambda x: abs(x["combined_score"]), reverse=True)

        # Take top candidates
        top = scored[:MAX_CANDIDATES]

        if top:
            self.log.info(f"  Top {len(top)} candidates by technical score:")
            for r in top:
                ps = r["pattern_summary"]
                patterns_str = ", ".join(ps["patterns"][:3]) if ps["patterns"] else "none"
                self.log.info(
                    f"    {r['symbol']:<14} score: {r['combined_score']:>+5.1f}  "
                    f"tech: {r['technical']['signal']:<12} "
                    f"patterns: {patterns_str}"
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
        # Step 1: Math-based pre-filter
        candidates = self._prefilter_universe(quotes)

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
            self.log.success(f"Claude recommended {len(trades)} trades from {len(candidates)} candidates")
            return trades
        except Exception as e:
            error = ClaudeClient.classify_error(e)
            self.log.error(f"V2 scan failed: {error}")
            return []

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

            lines.append(
                f"{symbol:<14} "
                f"₹{price:>10.2f}  Chg: {change_pct:>+6.2f}%  "
                f"Vol: {volume:>12,}  "
                f"VWAP: ₹{c['vwap']:.2f}  "
                f"RSI: {rsi_val:.0f}  "
                f"EMA(9/21): {ema_info['signal']}  "
                f"SuperTrend: {st_info['trend']}  "
                f"Score: {c['combined_score']:+.1f}  "
                f"Patterns: [{patterns}]"
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

        return f"""You are an expert Indian stock market intraday trader (NSE) with 15 years of experience in technical analysis.
Today is {today}, current time is {now} IST. All positions MUST be closed by 3:10 PM IST today.

BUDGET: ₹{budget:,} total capital.
MAX POSITIONS: {max_positions} stocks simultaneously.
MAX PER STOCK: {max_pct}% of budget (= ₹{budget * max_pct // 100:,} max per stock).
{nifty_context}{perf_context}{session_context}
IMPORTANT: The stocks below have been PRE-FILTERED by mathematical technical analysis.
Each stock shows real-time technical indicators — use them to make better decisions.

INDICATOR GUIDE:
- RSI > 70 = overbought (consider SHORT or AVOID for BUY). RSI < 30 = oversold (consider BUY).
- EMA(9/21): BULLISH_CROSS = fast EMA just crossed above slow (strong BUY signal).
  BEARISH_CROSS = fast EMA just crossed below slow (strong SELL signal).
- SuperTrend: UP = bullish trend, DOWN = bearish trend. Trend changes are strong signals.
- VWAP: price above VWAP = bullish. Below VWAP = bearish. Near VWAP = mean-reversion zone.
- Candle patterns: HAMMER, BULLISH_ENGULFING, MORNING_STAR = bullish reversal.
  SHOOTING_STAR, BEARISH_ENGULFING, EVENING_STAR = bearish reversal.
  THREE_WHITE_SOLDIERS = strong bullish. THREE_BLACK_CROWS = strong bearish.
- Score: positive = net bullish, negative = net bearish. Higher absolute value = stronger signal.

CRITICAL RULES — MUST FOLLOW:
1. ALIGN WITH INDICATORS: if RSI says overbought and candle shows SHOOTING_STAR, that's a strong SHORT setup. If RSI is oversold with a HAMMER, that's a strong BUY.
2. DO NOT fight the SuperTrend direction unless you have strong reversal patterns.
3. CONFLUENCE matters: trades where 3+ indicators agree are highest conviction.
4. Use VWAP as the anchor — for BUY entries, prefer stocks near or pulling back to VWAP. For SHORT entries, stocks rejected at VWAP.
5. RISK:REWARD must be at least 1:1.5 for every trade.
6. Use REALISTIC stop-loss levels — base SL on chart structure, SuperTrend value, or VWAP. Range: {default_sl}% to 2%.
7. Total position value across all trades MUST NOT exceed ₹{budget:,}.
8. Actively consider SHORT (SELL) trades when indicators show bearish signals.

PRE-FILTERED CANDIDATES (ranked by technical score):
{snapshot}

RESPOND WITH EXACTLY THIS FORMAT. One block per trade. No text before or after.
If no good trades exist today, respond with exactly: NO_TRADES_TODAY

TRADE 1:
SYMBOL: [NSE stock symbol]
SIDE: [BUY or SELL]
ENTRY_PRICE: [realistic entry price near current price]
STOP_LOSS: [stop-loss based on SuperTrend or VWAP or chart structure]
TARGET: [target price — at least 1.5× SL distance from entry]
QTY: [number of shares within budget constraints]
RATIONALE: [1-2 sentences: which indicators align, what pattern confirms the setup]
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

            pos_text += (
                f"  {pos['symbol']}: {pos['side']} {pos['qty']} shares @ ₹{entry:.2f}  "
                f"Current: ₹{current_price:.2f}  P&L: ₹{pnl:.2f} ({r_multiple:+.1f}R)  "
                f"SL: ₹{pos.get('stop_loss', 'N/A')}  Target: ₹{pos.get('target_price', 'N/A')}\n"
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

        return f"""You are an expert Indian stock market intraday trader (NSE) with 15 years of technical analysis experience.
Today is {today}, current time is {now} IST. Market closes at 3:30 PM, we square off at 3:10 PM.
TIME REMAINING: {mins_left:.0f} minutes until square-off.
{nifty_context}
CURRENT OPEN POSITIONS (with live technical indicators):
{pos_text if pos_text else "  (none)"}

CLOSED TRADES TODAY:
{closed_text if closed_text else "  (none)"}

DAY P&L SO FAR: ₹{day_pnl:,.2f}
REMAINING BUDGET: ₹{budget_remaining:,.2f}
MAX POSITIONS: {max_positions} stocks simultaneously.
MAX PER STOCK: {max_pct}% of ₹{budget:,} = ₹{max_per:,} max per stock.
{blocked_text}

V2 REVIEW RULES — MUST FOLLOW:
1. USE THE TECHNICAL INDICATORS shown for each position. If 5-min candle shows a reversal pattern against your position, EXIT or tighten SL aggressively.
2. If RSI is extreme (>80 for longs or <20 for shorts), consider taking profits.
3. TRAILING STOP: If profitable by more than 1× risk, move SL to at least breakeven.
4. TIME DECAY: With {mins_left:.0f} min left — under 60 min → lower target 30%. Under 30 min → EXIT unless near target.
5. CUT LOSERS EARLY: if underwater + bearish candle patterns forming, EXIT.
6. DO NOT AVERAGE DOWN.
7. NEW TRADES: only if 60+ min remain AND strong technical setup (score >= 3).
8. DO NOT PANIC-EXIT winners with intact trends and 30+ minutes remaining.

Review each position. For each, respond:

REVIEW 1:
SYMBOL: [symbol]
ACTION: [HOLD | EXIT | ADJUST_SL | ADJUST_TARGET]
NEW_SL: [new stop-loss if ADJUST_SL, otherwise blank]
NEW_TARGET: [new target if ADJUST_TARGET, otherwise blank]
REASON: [1 sentence — reference the technical indicators in your decision]
---

For new trades (optional, 60+ min remaining only):
NEW_TRADE:
SYMBOL: [symbol]
SIDE: [BUY or SELL]
ENTRY_PRICE: [price]
STOP_LOSS: [price]
TARGET: [price]
QTY: [must satisfy: QTY × ENTRY ≤ ₹{min(budget_remaining, max_per):,.0f}]
RATIONALE: [1 sentence — which indicators support this trade]
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
