# ================================================================
# portfolio/manager_v2.py
# ================================================================
# V2 intraday trading bot using candle-pattern-based pre-filtering.
#
# Extends PortfolioManager (V1) — inherits ALL infrastructure:
#   - Zerodha login, budget management, signal handling
#   - Position entry, exit, square-off, report generation
#   - Wait timers, holiday detection, status display
#   - ATR-based SL/targets, trailing stop, circuit breaker
#   - Crash recovery, order API failure protection
#
# V2 ADDITIONS (everything else is inherited from V1):
#   1. StockScannerV2 replaces V1 scanner (math pre-filter → Claude)
#   2. Claude review includes fresh 5-min candle pattern context
#   3. Dynamic poll interval — halved when near SL or target (0.5%)
#   4. Periodic candle re-scan every V2_CANDLE_RESCAN_MINUTES (free,
#      no Claude cost) — logs strong signals on open positions
#
# DESIGN: inheritance means V2 automatically gets any future V1
# improvements (new risk rules, better prompts, etc.) for free.
#
# Run with: python main.py --mode trade --v2
# ================================================================

import time
import datetime

from config                           import Config
from core.logger                      import Logger
from core.zerodha_client              import ZerodhaClient
from core.claude_client               import ClaudeClient
from services.stock_scanner_v2        import StockScannerV2
from services.order_engine            import OrderEngine
from services.report_writer           import ReportWriter
from services.performance_tracker     import PerformanceTracker
from portfolio.manager                import PortfolioManager


class PortfolioManagerV2(PortfolioManager):
    """
    V2 intraday trading bot with candle-pattern and technical-indicator
    pre-filtering. Extends V1 — same lifecycle, smarter stock selection.
    """

    def __init__(self, config: type[Config]):
        # Initialize parent (sets up all shared infra)
        super().__init__(config)

        # Replace the V1 scanner with V2 (candle-aware)
        self.scanner = StockScannerV2(
            config,
            self.claude,
            self.zerodha,
            Logger("StockScannerV2"),
        )

        # V2-specific state
        self._fast_poll = False        # True when near SL/target
        self._last_candle_scan = 0.0   # timestamp of last candle re-scan

    # ================================================================
    # OVERRIDE: BANNER
    # ================================================================

    def _print_banner(self):
        """Shows V2 configuration."""
        plan = self.cfg.claude()
        zrd  = self.cfg.zerodha()
        print(f"\n{'='*58}")
        print("  AI PORTFOLIO MANAGER — V2 CANDLE STRATEGY")
        print(f"{'='*58}")
        print(f"  Claude plan    : {self.cfg.CLAUDE_PLAN.upper()}")
        print(f"  → {plan['note']}")
        print()
        print(f"  Zerodha plan   : {self.cfg.ZERODHA_PLAN.upper()}")
        print(f"  → {zrd['note']}")
        print()
        print(f"  Claude model   : {plan['model']}")
        print(f"  Price source   : {zrd['price_source'].upper()}")
        print()
        print(f"  \033[96m★ V2 Strategy\033[0m : Candle patterns + Technical indicators")
        print(f"    Pre-filter  : EMA(9/21), RSI(14), VWAP, SuperTrend(10,3)")
        print(f"    Patterns    : Hammer, Engulfing, Morning/Evening Star, etc.")
        print(f"    Dynamic poll: faster near SL/target zones")
        print(f"{'='*58}\n")

    # ================================================================
    # TEST MODE — run V2 candle pipeline end-to-end, no Claude
    # ================================================================

    def run_test(self):
        """
        Runs the full V2 candle-pattern + technical-indicator pipeline
        without any Claude API calls or order placement. Useful for
        verifying that the math layer works end-to-end.

        Steps:
          1. Validate config + log into Zerodha
          2. Fetch live quotes for the stock universe
          3. Run V2 pre-filter (candle patterns + technical indicators)
          4. Print detailed results for every analysed stock
          5. Show what would have been sent to Claude
        """
        from services.stock_scanner_v2 import MAX_CANDIDATES

        print(f"\n{'='*58}")
        print("  V2 CANDLE PIPELINE — TEST MODE")
        print(f"  No Claude calls. No trades. Just math.")
        print(f"{'='*58}\n")

        # ── Step 1: Validate config ───────────────────────────────
        missing = self.cfg.validate()
        if missing:
            for key in missing:
                self.log.error(f"Missing in .env file: {key}")
            return

        # ── Step 2: Login to Zerodha ──────────────────────────────
        self.log.section("ZERODHA LOGIN")
        try:
            self.zerodha.login()
        except Exception as e:
            self.log.error(f"Zerodha login failed: {e}")
            return

        self.zerodha.print_account_snapshot()

        # ── Step 3: Fetch live quotes ─────────────────────────────
        universe = self.scanner.get_universe()
        self.log.section(f"SCANNING {len(universe)} STOCKS ({self.cfg.SCAN_UNIVERSE})")

        stocks = [{"symbol": s, "exchange": "NSE"} for s in universe]
        quotes = self.zerodha.get_quotes_safe(stocks)
        if quotes is None:
            self.log.error("Could not fetch market data. Aborting.")
            return

        # ── Step 4: Run V2 pre-filter (math only) ────────────────
        self.log.section("V2 PRE-FILTER — Candle Patterns + Technical Indicators")
        self.log.info(f"Interval: {self.cfg.V2_CANDLE_INTERVAL}")
        self.log.info(f"Min score threshold: {self.cfg.V2_MIN_SCORE}")
        self.log.info(f"Max candidates: {MAX_CANDIDATES}")
        self.log.info("")

        scored = []
        skipped = 0
        for i, symbol in enumerate(universe):
            if (i + 1) % 20 == 0 or (i + 1) == len(universe):
                self.log.info(f"  Analysed {i + 1}/{len(universe)} stocks...")

            result = self.scanner._analyse_stock(symbol)
            if result:
                scored.append(result)
            else:
                skipped += 1

        self.log.info(f"\nAnalysed {len(scored)} stocks | Skipped {skipped} (insufficient candle data)")

        # ── Step 5: Show ALL results (not just filtered) ──────────
        scored.sort(key=lambda x: abs(x["combined_score"]), reverse=True)

        self.log.section("ALL STOCKS BY TECHNICAL SCORE")
        print(f"{'Symbol':<14} {'Score':>6}  {'Signal':<12} {'RSI':>5}  "
              f"{'EMA(9/21)':<16} {'SuperTrend':<12} {'VWAP':>10}  "
              f"{'Price':>10}  {'Patterns'}")
        print("-" * 120)

        for r in scored:
            tech = r["technical"]
            ps = r["pattern_summary"]
            rsi_val = tech["rsi"]["rsi"]
            ema_sig = tech["ema_cross"]["signal"]
            st_trend = tech["supertrend"]["trend"]
            patterns = ", ".join(ps["patterns"][:4]) if ps["patterns"] else "-"

            score = r["combined_score"]
            passed = abs(score) >= self.cfg.V2_MIN_SCORE

            marker = "*" if passed else " "
            print(
                f"{marker} {r['symbol']:<12} {score:>+6.1f}  {tech['signal']:<12} "
                f"{rsi_val:>5.0f}  {ema_sig:<16} {st_trend:<12} "
                f"Rs.{r['vwap']:>9.2f}  Rs.{r['current_price']:>9.2f}  "
                f"[{patterns}]"
            )

        # ── Step 6: Show filtered candidates ──────────────────────
        filtered = [s for s in scored if abs(s["combined_score"]) >= self.cfg.V2_MIN_SCORE]
        top = filtered[:MAX_CANDIDATES]

        self.log.section(f"FILTERED CANDIDATES -- {len(filtered)} passed (score >= {self.cfg.V2_MIN_SCORE}), top {len(top)} shown")

        if not top:
            self.log.warning("No stocks passed the pre-filter threshold today.")
            return

        for r in top:
            tech = r["technical"]
            ps = r["pattern_summary"]
            ema = tech["ema_cross"]
            st = tech["supertrend"]
            rsi_data = tech["rsi"]
            sr = tech.get("prev_day_sr", {})

            print(f"\n  {'-'*50}")
            print(f"  {r['symbol']}  --  Combined Score: {r['combined_score']:+.1f}  ({tech['signal']})")
            print(f"  {'-'*50}")
            print(f"  Price    : Rs.{r['current_price']:.2f}")
            print(f"  VWAP     : Rs.{r['vwap']:.2f}  ({'above' if r['current_price'] > r['vwap'] else 'below'} VWAP)")
            print(f"  RSI(14)  : {rsi_data['rsi']:.1f}  ({rsi_data['signal']}, strength: {rsi_data['strength']})")
            print(f"  EMA(9/21): {ema['signal']}  (spread: {ema['spread_pct']:+.2f}%)")
            print(f"  SuperTrnd: {st['trend']}  (signal: {st['signal']})")
            if r.get("rvol", 0) > 0:
                print(f"  RVol     : {r['rvol']:.1f}x  ({'HIGH' if r['rvol'] > 2 else 'LOW' if r['rvol'] < 0.3 else 'normal'})")
            if sr.get("signal", "NONE") != "NONE":
                pivot_str = f"  Pivot: Rs.{sr['pivot']:.2f}" if sr.get("pivot") else ""
                print(f"  PrevDayS&R: {sr['signal']}  (score: {sr['score']:+.1f}){pivot_str}")

            if ps["patterns"]:
                print(f"  Patterns : {', '.join(ps['patterns'])}")
                print(f"  Pat.score: {ps['score']:+.1f}  ({ps['net_signal']})")
                if ps["strongest"]:
                    s = ps["strongest"]
                    print(f"  Strongest: {s['pattern']}  ({s['signal']}, strength: {s['strength']})")
            else:
                print(f"  Patterns : none detected")

            print(f"  Candles  : {r['candle_count']} (15-min candles used)")

        # ── Step 6b: Nifty hard filter simulation ─────────────────
        #    Test mode doesn't have nifty_context, but show what WOULD
        #    happen if market was bearish/bullish to validate the filter.
        would_drop_bear = sum(1 for s in filtered if s["combined_score"] > 0 and abs(s["combined_score"]) < 5)
        would_drop_bull = sum(1 for s in filtered if s["combined_score"] < 0 and abs(s["combined_score"]) < 5)
        if would_drop_bear or would_drop_bull:
            print(f"\n  Nifty hard filter impact (if applied):")
            if would_drop_bear:
                print(f"    BEARISH market → would drop {would_drop_bear} weak BUY signals (score < 5)")
            if would_drop_bull:
                print(f"    BULLISH market → would drop {would_drop_bull} weak SELL signals (score < 5)")

        # ── Step 7: Show what would be sent to Claude ─────────────
        snapshot = self.scanner._build_enriched_snapshot(top, quotes)
        if snapshot:
            self.log.section("ENRICHED SNAPSHOT (would be sent to Claude)")
            print(snapshot)

        # ── Summary ───────────────────────────────────────────────
        self.log.section("TEST SUMMARY")
        bulls = sum(1 for s in top if s["combined_score"] > 0)
        bears = sum(1 for s in top if s["combined_score"] < 0)
        print(f"  Universe       : {len(universe)} stocks ({self.cfg.SCAN_UNIVERSE})")
        print(f"  Analysed       : {len(scored)}")
        print(f"  Skipped        : {skipped} (not enough candle data)")
        print(f"  Passed filter  : {len(filtered)} (|score| >= {self.cfg.V2_MIN_SCORE})")
        print(f"  Top candidates : {len(top)} (max {MAX_CANDIDATES})")
        print(f"  Bullish setups : {bulls}")
        print(f"  Bearish setups : {bears}")
        print(f"\n  Claude calls   : 0  (test mode -- no API cost)")
        print(f"  Orders placed  : 0  (test mode -- no trades)")
        print()

    # ================================================================
    # OVERRIDE: MONITOR LOOP (V2 — with dynamic polling + candle re-scan)
    # ================================================================

    def _run_monitor_loop(self):
        """
        V2 monitor loop with:
        - Dynamic poll interval (faster when near SL/target)
        - Periodic candle re-scan (every 15 min) to detect new setups
        - Enhanced Claude review with position candle context
        """
        self.log.section("V2 MONITORING — Candle-aware price tracking")

        base_poll = self.cfg.PRICE_POLL_SECONDS
        fast_poll = max(5, base_poll // 2)  # halve interval, min 5s
        review_interval = self.cfg.CLAUDE_REVIEW_MINUTES * 60
        candle_rescan_interval = self.cfg.V2_CANDLE_RESCAN_MINUTES * 60

        self.log.info(
            f"Base poll: {base_poll}s | Fast poll: {fast_poll}s | "
            f"Claude review: every {self.cfg.CLAUDE_REVIEW_MINUTES}min | "
            f"Candle rescan: every {self.cfg.V2_CANDLE_RESCAN_MINUTES}min"
        )

        last_review_time = time.time()
        self._last_candle_scan = time.time()

        while not self._shutdown_requested:
            now = datetime.datetime.now()

            # ── Square-off check ──────────────────────────────────
            if self._is_square_off_time(now):
                self.log.info("Square-off time reached")
                break

            # ── All positions closed? ─────────────────────────────
            if not self.engine.open_positions():
                if self.engine.is_order_api_broken():
                    self.log.error("All positions closed, order API broken — stopping")
                    break

                sq_off = now.replace(
                    hour=self.cfg.SQUARE_OFF_HOUR,
                    minute=self.cfg.SQUARE_OFF_MINUTE,
                    second=0, microsecond=0,
                )
                mins_remaining = (sq_off - now).total_seconds() / 60

                if mins_remaining >= self.cfg.MIN_MINUTES_FOR_ENTRY:
                    self.log.info(
                        f"All positions closed with {mins_remaining:.0f} min left — "
                        f"V2 re-scanning with candle analysis..."
                    )
                    closed_trades = self.engine.closed_positions()
                    traded_symbols = list({p["symbol"] for p in closed_trades})
                    day_pnl = self.engine.day_pnl()
                    session_ctx = (
                        f"\nSESSION CONTEXT (V2 mid-day re-scan):\n"
                        f"  Day P&L so far: ₹{day_pnl:,.2f} from {len(closed_trades)} closed trades.\n"
                        f"  Already traded today: {', '.join(traded_symbols) if traded_symbols else 'none'}.\n"
                        f"  DO NOT pick any stock already traded today unless opposite direction.\n"
                        f"  If P&L is negative, only pick high-conviction candle setups.\n"
                    )
                    self._trade_plans = []
                    self._run_pre_market_scan(session_context=session_ctx)
                    if self._trade_plans:
                        self._enter_positions()
                        last_review_time = time.time()
                        self._last_candle_scan = time.time()
                        continue
                    else:
                        self.log.info("V2 re-scan: no new trades — done for the day")
                        break
                else:
                    self.log.info(
                        f"All positions closed — {mins_remaining:.0f} min left, "
                        f"not enough for new trades"
                    )
                    break

            # ── Fetch live quotes ─────────────────────────────────
            open_symbols = [
                {"symbol": p["symbol"], "exchange": p["exchange"]}
                for p in self.engine.open_positions()
            ]
            try:
                quotes = self.zerodha.get_quotes(open_symbols)
            except Exception as e:
                self.log.warning(f"Quote fetch failed: {e} — retrying")
                time.sleep(base_poll)
                continue

            # ── SL/target check (free, rule-based) ────────────────
            closed = self.engine.check_stops_and_targets(quotes)
            if closed > 0:
                self.log.info(f"{closed} position(s) auto-closed")

            # ── Order API broken check ────────────────────────────
            if self.engine.is_order_api_broken():
                self.log.error("Order API broken — shutting down")
                if self.engine.open_positions():
                    self._square_off()
                break

            # ── Circuit breaker ───────────────────────────────────
            if self.engine.check_circuit_breaker():
                self._circuit_broken = True
                self._square_off()
                break

            # ── Dynamic poll rate ─────────────────────────────────
            if self.engine.open_positions():
                near_trigger = self.scanner.should_increase_poll_rate(
                    self.engine.open_positions(), quotes,
                )
                if near_trigger and not self._fast_poll:
                    self._fast_poll = True
                    self.log.info("⚡ Position near SL/target — increasing poll rate")
                elif not near_trigger and self._fast_poll:
                    self._fast_poll = False

            # ── Periodic candle re-scan (free, no Claude cost) ──
            candle_elapsed = time.time() - self._last_candle_scan
            if candle_elapsed >= candle_rescan_interval and self.engine.open_positions():
                self.log.info("V2 candle re-scan: refreshing technical data for open positions")
                for pos in self.engine.open_positions():
                    fresh = self.scanner._analyse_stock(pos["symbol"], pos.get("exchange", "NSE"))
                    if not fresh:
                        continue

                    score = fresh["combined_score"]
                    ps = fresh["pattern_summary"]
                    patterns = ", ".join(ps["patterns"][:3]) if ps["patterns"] else "none"

                    if abs(score) >= 5:
                        self.log.info(
                            f"  {pos['symbol']}: score {score:+.1f}  "
                            f"tech: {fresh['technical']['signal']}  "
                            f"patterns: [{patterns}]"
                        )

                    # Auto-tighten SL on strong contrary signal
                    self._auto_protect_on_contrary_signal(pos, fresh, quotes)

                self._last_candle_scan = time.time()

            # ── Claude review (with candle context) ───────────────
            elapsed = time.time() - last_review_time
            if elapsed >= review_interval and self.engine.open_positions():
                self._run_claude_review_v2(quotes)
                last_review_time = time.time()

            # ── Print status ──────────────────────────────────────
            self._print_status(quotes)

            # ── Sleep (dynamic) ───────────────────────────────────
            poll = fast_poll if self._fast_poll else base_poll
            time.sleep(poll)

    # ================================================================
    # V2 CLAUDE REVIEW (with candle context)
    # ================================================================

    def _run_claude_review_v2(self, quotes: dict):
        """
        Enhanced Claude review that includes real-time candle pattern
        analysis for each open position.
        """
        self.log.section("CLAUDE V2 REVIEW — with candle analysis")
        self.engine.claude_calls += 1

        nifty_context = self._build_nifty_context()

        actions = self.scanner.review_positions_v2(
            open_positions   = self.engine.open_positions(),
            quotes           = quotes,
            day_pnl          = self.engine.day_pnl(),
            budget_remaining = self.engine.budget_remaining(),
            nifty_context    = nifty_context,
            closed_positions = self.engine.closed_positions(),
        )

        if actions:
            self.engine.apply_review_actions(actions, quotes)

        # Re-fetch quotes for table display
        all_open = self.engine.open_positions()
        if all_open:
            open_symbols = [
                {"symbol": p["symbol"], "exchange": p["exchange"]}
                for p in all_open
            ]
            try:
                quotes = self.zerodha.get_quotes(open_symbols)
            except Exception:
                pass

        self.engine.print_position_status(quotes)

    # ================================================================
    # AUTO-PROTECT: TIGHTEN SL ON CONTRARY CANDLE SIGNALS
    # ================================================================

    def _auto_protect_on_contrary_signal(
        self,
        pos: dict,
        analysis: dict,
        quotes: dict,
    ):
        """
        When the free candle re-scan detects a strong signal AGAINST
        an open position, auto-tighten the stop-loss to breakeven or
        lock in partial profit. This acts immediately — no need to
        wait for the next Claude review.

        Triggers:
          - BUY position + score <= -4  (strong bearish signal)
          - SELL position + score >= +4 (strong bullish signal)

        Action:
          - If in profit: move SL to lock 50% of current profit
          - If at breakeven or loss: move SL to entry price (breakeven)
          - SL only moves in the protective direction (never loosened)
        """
        score  = analysis["combined_score"]
        side   = pos["side"]
        symbol = pos["symbol"]
        entry  = pos["entry_price"]
        old_sl = pos["stop_loss"]

        # Check if signal is contrary to position direction
        contrary = (side == "BUY" and score <= -4) or \
                   (side == "SELL" and score >= 4)

        if not contrary:
            return

        # Get current price from quotes
        key = f"{pos.get('exchange', 'NSE')}:{symbol}"
        q = quotes.get(key, {})
        current_price = q.get("last_price", 0)
        if current_price <= 0:
            return

        # Calculate new protective SL
        if side == "BUY":
            profit = current_price - entry
            if profit > 0:
                # In profit — lock 50%
                new_sl = round(entry + profit * 0.5, 2)
            else:
                # At loss — move SL to breakeven (entry)
                new_sl = entry

            # SL must only move UP for BUY (never loosen)
            if new_sl <= old_sl:
                return
        else:
            profit = entry - current_price
            if profit > 0:
                new_sl = round(entry - profit * 0.5, 2)
            else:
                new_sl = entry

            if new_sl >= old_sl:
                return

        # Apply the tighter SL
        pos["stop_loss"] = new_sl
        patterns = ", ".join(analysis["pattern_summary"]["patterns"][:3])
        self.log.warning(
            f"⚠ CANDLE PROTECT {symbol}: contrary signal (score {score:+.1f}, "
            f"[{patterns}]) → SL tightened ₹{old_sl:.2f} → ₹{new_sl:.2f}"
        )
