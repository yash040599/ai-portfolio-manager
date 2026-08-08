# ================================================================
# modes/options/manager.py
# ================================================================
# Options trading bot orchestrator.
#
# Phase O-4 scope: Directional option BUYING on NIFTY weekly options.
# BUY ONLY — no selling, no spreads, no naked writes.
# Starts in DRY_RUN mode always.
#
# Lifecycle (mirrors equity PortfolioManager):
#   1. Validate config + check trading day
#   2. Zerodha login + load NFO instruments
#   3. Fetch NIFTY quote + India VIX → regime classification
#   4. Wait for market open + entry delay
#   5. Scan option chain → select strike + premium
#   6. Enter position (or dry-run log)
#   7. Monitor loop: poll premiums, check SL/target, square-off
#   8. Generate report + persist to DB
#
# Safety:
#   - DRY_RUN is the default (OPTIONS_DRY_RUN = True)
#   - Naked sell hard block (always on)
#   - Circuit breaker (daily loss cap)
#   - Graceful shutdown (Ctrl+C squares off first)
# ================================================================

import signal
import sys
import time
import datetime

from config                            import Config, now_ist
from core.logger                       import Logger
from core.zerodha_client               import ZerodhaClient
from modes.options.option_scanner      import OptionScanner
from modes.options.order_engine        import OptionsOrderEngine
from modes.options.performance_tracker import OptionsPerformanceTracker
from modes.options.report_writer       import OptionsReportWriter


class OptionsManager:
    """Options trading bot. See module docstring for the lifecycle."""

    def __init__(self, config: type[Config]):
        self.cfg = config
        self.log = Logger("OptionsManager")
        self.zerodha = ZerodhaClient(config, self.log)
        self.scanner = OptionScanner(config, self.zerodha, Logger("OptionScanner"))
        self.engine = OptionsOrderEngine(config, self.zerodha, Logger("OptionsEngine"))
        self.tracker = OptionsPerformanceTracker(config, self.log)
        self.report = OptionsReportWriter(config, self.log)

        # ── State ─────────────────────────────────────────────────
        self._shutdown_requested: bool = False
        self._market_condition: str = ""
        self._india_vix: float = 0.0
        self._nifty_price: float = 0.0
        self._nifty_trend: str = ""

    # ================================================================
    # MAIN ENTRY POINT
    # ================================================================

    def run(self):
        """Full options trading day lifecycle (dry-run or live)."""
        self._setup_signal_handlers()
        self._print_banner()

        # ── Step 1: Validate config ───────────────────────────────
        self._validate_config()

        # ── Step 2: Check trading day ─────────────────────────────
        if not self._is_trading_day():
            self.log.info("Not a trading day (weekend/holiday). Exiting.")
            return

        # ── Step 3: Zerodha login ─────────────────────────────────
        self.log.info("Logging into Zerodha...")
        self.zerodha.login()
        self.log.success("Zerodha login successful.")

        # ── Step 4: Load NFO instruments ──────────────────────────
        self.log.info("Loading NFO instrument tokens...")
        self.zerodha.load_nfo_instruments()
        self.log.success("NFO instruments loaded.")

        # ── Step 5: Set budget ────────────────────────────────────
        self.engine.set_budget(self.cfg.OPTIONS_BUDGET_INR)
        self.log.info(f"Options budget: Rs.{self.cfg.OPTIONS_BUDGET_INR:,}")

        # ── Step 6: Wait for market open ──────────────────────────
        self._wait_for_market_open()

        if self._shutdown_requested:
            return

        # ── Step 7: Fetch NIFTY + VIX context ─────────────────────
        self._fetch_market_context()

        if not self._nifty_price:
            self.log.error("Could not fetch NIFTY price. Cannot proceed.")
            return

        # ── Step 8: Scan for option trade ─────────────────────────
        self.log.info("")
        self.log.info("=" * 60)
        self.log.info("  OPTION CHAIN SCAN")
        self.log.info("=" * 60)

        candidate = self.scanner.scan(
            nifty_price=self._nifty_price,
            nifty_trend=self._nifty_trend,
            india_vix=self._india_vix,
            market_condition=self._market_condition,
        )

        if candidate:
            self.tracker.record_candidate(candidate, accepted=True)
            entered = self.engine.enter_trade(candidate)
            if not entered:
                self.log.info("Trade entry rejected by order engine.")
        else:
            self.tracker.record_candidate(None, accepted=False, reject_reason="no_signal")
            self.log.info("No option trade candidate today.")

        # ── Step 9: Monitor loop ──────────────────────────────────
        self._run_monitor_loop()

        # ── Step 10: Square off remaining ─────────────────────────
        self.engine.square_off_all()

        # ── Step 11: Generate report ──────────────────────────────
        self._generate_report()

        self.log.info("")
        self.log.success("Options session complete.")

    # ================================================================
    # MONITOR LOOP
    # ================================================================

    def _run_monitor_loop(self):
        """Poll premiums and check SL/target until square-off time."""
        open_pos = self.engine.open_positions()
        if not open_pos:
            self.log.info("No open positions to monitor.")
            return

        self.log.info("")
        self.log.info("Entering monitor loop...")
        poll_seconds = self.cfg.OPTIONS_POLL_SECONDS

        while not self._shutdown_requested:
            now = now_ist()
            sqoff = now.replace(
                hour=self.cfg.OPTIONS_SQUARE_OFF_HOUR,
                minute=self.cfg.OPTIONS_SQUARE_OFF_MINUTE,
                second=0, microsecond=0,
            )

            # ── Time to square off? ──────────────────────────────
            if now >= sqoff:
                self.log.info(
                    f"Square-off time ({self.cfg.OPTIONS_SQUARE_OFF_HOUR}:"
                    f"{self.cfg.OPTIONS_SQUARE_OFF_MINUTE:02d}) reached."
                )
                break

            # ── No more open positions? ──────────────────────────
            if not self.engine.open_positions():
                self.log.info("All positions closed. Exiting monitor loop.")
                break

            # ── Fetch live premiums ──────────────────────────────
            symbols = [
                {"symbol": p["symbol"], "exchange": "NFO"}
                for p in self.engine.open_positions()
            ]
            try:
                quotes = self.zerodha.get_quotes(symbols)
                self.engine.update_premiums(quotes)
            except Exception as e:
                self.log.warning(f"Premium fetch failed: {e}")

            # ── Periodic NIFTY context refresh (every 5 min) ─────
            # (Update market context for potential re-scans in future)

            # ── Status line ──────────────────────────────────────
            open_pos = self.engine.open_positions()
            day_pnl = self.engine.day_pnl()
            if open_pos:
                pos = open_pos[0]
                entry = pos["entry_premium"]
                current = pos["current_premium"]
                pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0
                self.log.info(
                    f"  {pos['symbol']} | Entry: {entry:.2f} | "
                    f"Now: {current:.2f} ({pnl_pct:+.1f}%) | "
                    f"SL: {pos['stop_loss']:.2f} | "
                    f"Target: {pos['target']:.2f} | "
                    f"Day P&L: Rs.{day_pnl:+,.2f}"
                )

            time.sleep(poll_seconds)

    # ================================================================
    # MARKET CONTEXT
    # ================================================================

    def _fetch_market_context(self):
        """Fetch NIFTY 50 price, trend, and India VIX."""
        try:
            # ── NIFTY 50 ─────────────────────────────────────────
            nifty_quote = self.zerodha.get_quotes(
                [{"symbol": "NIFTY 50", "exchange": "NSE"}]
            )
            q = nifty_quote.get("NSE:NIFTY 50", {})
            self._nifty_price = q.get("last_price", 0)
            ohlc = q.get("ohlc", {})
            prev_close = ohlc.get("close", 0)

            if self._nifty_price and prev_close:
                change_pct = (self._nifty_price - prev_close) / prev_close * 100
                if change_pct > 0.5:
                    self._nifty_trend = "BULLISH"
                    self._market_condition = "BULLISH"
                elif change_pct < -0.5:
                    self._nifty_trend = "BEARISH"
                    self._market_condition = "BEARISH"
                else:
                    self._nifty_trend = ""
                    self._market_condition = "NEUTRAL"

                self.log.info(
                    f"NIFTY 50: {self._nifty_price:,.2f} "
                    f"({change_pct:+.2f}%) → {self._nifty_trend or 'NEUTRAL'}"
                )
            else:
                self.log.warning("NIFTY 50 quote incomplete.")

            # ── India VIX ─────────────────────────────────────────
            vix_quote = self.zerodha.get_quotes(
                [{"symbol": "INDIA VIX", "exchange": "NSE"}]
            )
            vix_q = vix_quote.get("NSE:INDIA VIX", {})
            self._india_vix = vix_q.get("last_price", 0)
            if self._india_vix > 0:
                vix_regime = "NORMAL"
                if self._india_vix >= 20:
                    vix_regime = "HIGH"
                elif self._india_vix <= 12:
                    vix_regime = "LOW"
                self.log.info(f"India VIX: {self._india_vix:.2f} ({vix_regime})")

                # Append volatility to market condition
                if self._india_vix >= 18:
                    self._market_condition += "_HIGH_VOLATILITY"
                else:
                    self._market_condition += "_NORMAL"

        except Exception as e:
            self.log.error(f"Failed to fetch market context: {e}")

    # ================================================================
    # REPORT GENERATION
    # ================================================================

    def _generate_report(self):
        """Generate end-of-day report and persist trades."""
        positions = self.engine.all_positions()
        summary = self.tracker.get_summary(days=30)

        # ── Persist closed trades to DB ───────────────────────────
        self.tracker.record_trades(positions)

        # ── Generate report files ─────────────────────────────────
        self.report.save(
            positions=positions,
            market_condition=self._market_condition,
            india_vix=self._india_vix,
            nifty_close=self._nifty_price,
            summary_stats=summary,
        )

        # ── Print summary ─────────────────────────────────────────
        closed = [p for p in positions if p.get("status") == "CLOSED"]
        day_pnl = sum(p.get("pnl", 0) for p in closed)
        self.log.info("")
        self.log.info("=" * 60)
        self.log.info("  OPTIONS DAY SUMMARY")
        self.log.info(f"  Trades: {len(closed)} | Day P&L: Rs.{day_pnl:+,.2f}")
        if summary.get("total_trades", 0) > 0:
            self.log.info(
                f"  30-day: {summary['total_trades']} trades | "
                f"PF {summary['profit_factor']:.2f} | "
                f"WR {summary['win_rate']}%"
            )
        self.log.info("=" * 60)

    # ================================================================
    # HELPERS
    # ================================================================

    def _print_banner(self):
        dry = " (DRY RUN)" if self.cfg.OPTIONS_DRY_RUN else " (LIVE)"
        self.log.info("")
        self.log.info("=" * 60)
        self.log.info(f"  OPTIONS TRADING BOT{dry}")
        self.log.info(f"  Index: {self.cfg.OPTIONS_INDEX} | "
                       f"Budget: Rs.{self.cfg.OPTIONS_BUDGET_INR:,} | "
                       f"Max lots: {self.cfg.OPTIONS_MAX_LOTS}")
        self.log.info("  Strategy: Directional Buying (Phase O-4)")
        self.log.info(f"  Square-off: {self.cfg.OPTIONS_SQUARE_OFF_HOUR}:"
                       f"{self.cfg.OPTIONS_SQUARE_OFF_MINUTE:02d} IST")
        self.log.info("=" * 60)
        self.log.info("")

    def _validate_config(self):
        """Validate options-specific config values."""
        assert self.cfg.OPTIONS_BUDGET_INR > 0, "OPTIONS_BUDGET_INR must be > 0"
        assert self.cfg.OPTIONS_MAX_LOTS >= 1, "OPTIONS_MAX_LOTS must be >= 1"
        assert 0 < self.cfg.OPTIONS_SL_PCT_OF_PREMIUM < 100, "SL pct must be 0-100"
        assert self.cfg.OPTIONS_TARGET_PCT_OF_PREMIUM > 0, "Target pct must be > 0"

        if not self.cfg.OPTIONS_DRY_RUN:
            self.log.warning(
                "⚠ OPTIONS LIVE MODE — real orders will be placed on Zerodha!"
            )
        else:
            self.log.info("Dry-run mode: no real orders will be placed.")

    def _is_trading_day(self) -> bool:
        """Check if today is a weekday (basic check — no holiday calendar)."""
        today = now_ist().date()
        if today.weekday() >= 5:  # Saturday/Sunday
            return False
        return True

    def _wait_for_market_open(self):
        """Wait until market open + entry delay, or skip if already past."""
        now = now_ist()
        market_open = now.replace(
            hour=self.cfg.MARKET_OPEN_HOUR,
            minute=self.cfg.MARKET_OPEN_MINUTE,
            second=0, microsecond=0,
        )
        # Add entry delay (same as equity — wait for first candle)
        entry_time = market_open + datetime.timedelta(
            minutes=self.cfg.OPTIONS_ENTRY_DELAY_MINUTES
        )

        if now >= entry_time:
            self.log.info("Market already open. Proceeding immediately.")
            return

        wait_seconds = (entry_time - now).total_seconds()
        self.log.info(
            f"Waiting {wait_seconds / 60:.0f} min for market open + "
            f"{self.cfg.OPTIONS_ENTRY_DELAY_MINUTES} min entry delay "
            f"(until {entry_time.strftime('%H:%M')})..."
        )

        while not self._shutdown_requested:
            now = now_ist()
            if now >= entry_time:
                break
            time.sleep(min(30, (entry_time - now).total_seconds()))

    # ================================================================
    # SIGNAL HANDLERS
    # ================================================================

    def _setup_signal_handlers(self):
        """Register Ctrl+C handler for graceful shutdown."""
        def _handler(sig, frame):
            if self._shutdown_requested:
                self.log.warning("Force exit requested. Aborting.")
                sys.exit(1)
            self._shutdown_requested = True
            self.log.warning(
                "Shutdown requested — squaring off positions before exit..."
            )
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
