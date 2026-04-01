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
                    if fresh and abs(fresh["combined_score"]) >= 5:
                        ps = fresh["pattern_summary"]
                        patterns = ", ".join(ps["patterns"][:3]) if ps["patterns"] else "none"
                        self.log.info(
                            f"  {pos['symbol']}: score {fresh['combined_score']:+.1f}  "
                            f"tech: {fresh['technical']['signal']}  "
                            f"patterns: [{patterns}]"
                        )
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
