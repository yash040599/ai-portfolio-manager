# ================================================================
# portfolio/manager.py
# ================================================================
# Phase 2: Intraday trading bot.
#
# This is the main orchestrator. It runs continuously from whenever
# you start it (even the night before) and:
#
#   1. Waits for pre-market time (MARKET_OPEN - PRE_MARKET_MINUTES_BEFORE)
#   2. Logs into Zerodha
#   3. Fetches live quotes for the stock universe
#   4. Asks Claude to pick intraday trades (pre-market scan)
#   5. Waits for market open (9:15 AM IST)
#   6. Enters positions at market open
#   7. Monitors prices in a loop:
#      - Every PRICE_POLL_SECONDS: check SL/target hits (rule-based, free)
#      - Every POSITION_REVIEW_MINUTES: Claude reviews positions (paid)
#   8. At SQUARE_OFF time (3:10 PM): closes all positions
#   9. Generates full P&L report with taxes and charges
#
# Safety features:
#   - DRY_RUN mode: no real orders, simulated P&L on live prices
#   - Circuit breaker: stops trading if daily loss exceeds threshold
#   - Graceful shutdown: Ctrl+C squares off all positions first
#   - Budget cap: uses actual Zerodha account balance
#   - Max positions limit: prevents over-diversification
#
# Key constraint: Only ever touches the managed budget pool.
# Existing holdings in the demat account are READ-ONLY and NEVER touched.
# ================================================================

import signal
import sys
import time
import datetime
import json
import os

from config                        import Config, now_ist
from core.logger                   import Logger
from core.zerodha_client           import ZerodhaClient
from core.claude_client            import ClaudeClient
from services.stock_scanner        import StockScanner
from services.order_engine         import OrderEngine
from services.report_writer        import ReportWriter
from services.performance_tracker  import PerformanceTracker
from services.technical_indicators import adx as _calc_adx

class PortfolioManager:
    """
    Phase 2 intraday trading bot for the managed budget.
    DEPRECATED: V1 is frozen as of 2026-04-08. Use V2 (default) or NoAI.
    V1 still works but receives no new features or strategy improvements.

    Key constraint: Only ever touches the managed budget pool.
    Existing holdings in the demat account are READ-ONLY.
    """

    def __init__(self, config: type[Config]):
        self.cfg = config

        # ── Infrastructure ────────────────────────────────────────
        self.log     = Logger("PortfolioManager")
        self.zerodha = ZerodhaClient(config, Logger("ZerodhaClient"))
        self.claude  = ClaudeClient(config,  Logger("ClaudeClient"))
        self.scanner = StockScanner(config, self.claude, Logger("StockScanner"))
        self.engine  = OrderEngine(config, self.zerodha, Logger("OrderEngine"))
        self.report  = ReportWriter(config, Logger("ReportWriter"))
        self.tracker = PerformanceTracker(config, Logger("PerformanceTracker"))

        # ── State ─────────────────────────────────────────────────
        self._shutdown_requested = False   # set by Ctrl+C handler
        self._trade_plans: list[dict] = [] # trades Claude picked pre-market
        self._circuit_broken = False       # true if max daily loss hit
        self._available_funds: float = 0.0 # fetched from Zerodha at startup
        self._budget: float = 0.0          # actual trading budget for the day
        self._scan_failed = False          # true if quote fetch failed
        self._market_condition: str = ""   # BULLISH/BEARISH/NEUTRAL + volatility
        self._last_partial_rescan: float = 0.0  # cooldown for partial re-scans
        self._prev_runs: dict | None = None     # cached previous-run totals for today
        self._run_number: int | None = None      # current bot run # for today (1, 2, 3...)
        self._status_lines_printed = False       # tracks 2-line status display
        self._last_external_sync: float = 0.0    # periodic manual trade detection
        self._initial_entry_done = False         # True after first _enter_positions() completes

        # ── Market intelligence ───────────────────────────────────
        self._india_vix: float = 0.0           # India VIX value (0 = unavailable)
        self._india_vix_open: float = 0.0      # VIX at market open (for spike detection)
        self._vix_adjustments_applied = False   # prevent double-applying VIX tweaks
        self._preopen_data: dict = {}          # symbol → {gap_pct, volume, direction}
        self._fii_dii_bias: str = ""           # BULLISH / BEARISH / NEUTRAL / ""

    # ================================================================
    # RUN — MAIN ENTRY POINT
    # ================================================================

    def run(self):
        """
        Full day lifecycle. Can be started anytime — even the night
        before. It will sleep until pre-market time, then run the
        full trading day, then generate the report and exit.
        """
        self._setup_signal_handler()
        self._print_banner()

        # ── Step 1: Validate config ───────────────────────────────
        missing = self.cfg.validate(require_claude=not getattr(self, '_noai', False))
        if missing:
            self.log.section("CONFIGURATION ERROR")
            for key in missing:
                self.log.error(f"Missing in .env file: {key}=your_value_here")
            self.log.info("Create or edit the .env file in this folder and re-run.")
            return

        # ── Step 1b: Sanity-check numeric config ranges ───────────
        # Catches typos like ATR_MULTIPLIER=0 (div-by-zero) or
        # MAX_LOSS_PER_DAY_PCT=-1 before they corrupt live trades.
        range_errors = self.cfg.validate_ranges()
        if range_errors:
            self.log.section("CONFIGURATION ERROR")
            for err in range_errors:
                self.log.error(err)
            self.log.info("Fix config.py and re-run.")
            return

        # ── Step 1c: NSE early-close shift (#193) ─────────────────
        # On Diwali Muhurat eve / year-end days the NSE closes at
        # 13:30 IST and Zerodha auto-squares at distress prices.
        # If today is in NSE_EARLY_CLOSE_DATES_<year>, advance our
        # SQUARE_OFF time so we exit cleanly first. Idempotent.
        early = self.cfg.apply_early_close_if_today()
        if early is not None:
            self.log.warning(
                f"NSE early-close day detected — SQUARE_OFF advanced to "
                f"{early[0]:02d}:{early[1]:02d} IST to beat Zerodha "
                f"auto-square distress prices."
            )

        # ── Step 2: Login to Zerodha ──────────────────────────────
        # Login early so we can show account details even on holidays.
        self.log.section("ZERODHA LOGIN")
        try:
            self.zerodha.login()
        except Exception as e:
            self.log.error(f"Zerodha login failed: {e}")
            self.log.info("Fix your API credentials in .env and try again.")
            return

        # ── Step 2b: Show account snapshot ─────────────────────────
        self._print_account_snapshot()

        # ── Step 3: Wait for next trading day ─────────────────────
        # Checks weekends + NSE holiday calendar. If today is not a
        # trading day, shows a countdown to the next market open.
        # This prevents wasted Claude API calls on closed days.
        self._wait_for_trading_day()
        if self._shutdown_requested:
            return

        # ── Step 4: Fetch account funds & set budget ──────────────
        self._fetch_and_set_budget()
        if not self.cfg.DRY_RUN and self._budget <= 0:
            return

        # ── Step 5: Wait for pre-market time ─────────────────────
        self._wait_for_pre_market()
        if self._shutdown_requested:
            return

        # Re-login in case we waited across midnight and token expired
        self.log.info("Refreshing Zerodha login...")
        try:
            self.zerodha.login()
        except Exception as e:
            self.log.error(f"Zerodha re-login failed: {e}")
            return

        # Refresh funds after re-login
        self._fetch_and_set_budget()
        if not self.cfg.DRY_RUN and self._budget <= 0:
            return

        # ── Step 6: Stock scan ─────────────────────────────────
        # Check if we're too close to square-off to trade.
        # If too late, wait for the next trading day and retry.
        while not self._shutdown_requested:
            now = now_ist()
            square_off = now.replace(
                hour=self.cfg.SQUARE_OFF_HOUR,
                minute=self.cfg.SQUARE_OFF_MINUTE,
                second=0, microsecond=0,
            )
            minutes_left = (square_off - now).total_seconds() / 60

            if minutes_left <= 0:
                reason = (
                    f"Square-off time ({self.cfg.SQUARE_OFF_HOUR}:"
                    f"{self.cfg.SQUARE_OFF_MINUTE:02d}) already passed — "
                    f"too late to trade today"
                )
            elif minutes_left < self.cfg.CUTOFF_MINUTES_BEFORE_CLOSE:
                reason = (
                    f"Only {minutes_left:.0f} minutes until square-off — "
                    f"need at least {self.cfg.CUTOFF_MINUTES_BEFORE_CLOSE} minutes, "
                    f"skipping today "
                    f"(change CUTOFF_MINUTES_BEFORE_CLOSE in config.py to lower the threshold)"
                )
            else:
                break  # Enough time to trade — proceed

            # Too late — wait for the next market open
            self._wait_for_next_market_open(reason)
            if self._shutdown_requested:
                return

            # New day: re-login (token expired overnight) and refresh funds
            self.log.info("Refreshing Zerodha login for new trading day...")
            try:
                self.zerodha.login()
            except Exception as e:
                self.log.error(f"Zerodha re-login failed: {e}")
                return

            self._fetch_and_set_budget()
            if not self.cfg.DRY_RUN and self._budget <= 0:
                return

        if self._shutdown_requested:
            return

        # ── Step 5b: Check for existing positions on Zerodha ─────
        # If the bot crashed or was stopped while positions were open,
        # resume monitoring them instead of starting fresh.
        resumed = 0
        recovered_closed = 0
        if not self.cfg.DRY_RUN:
            resumed = self.engine.load_existing_positions()
            if resumed > 0:
                self.log.success(
                    f"Resumed {resumed} existing position(s) from Zerodha — "
                    f"skipping to monitor loop"
                )
            # Roadmap #203 — reconstruct realised P&L from prior-session
            # round-trip closes that finished while the bot was down.
            # Without this, day_pnl() resets to 0 on restart even when
            # Zerodha holds the truth, breaking the MTM-aware circuit
            # breaker (#197) and adaptive sizing.
            recovered_closed = self.engine.recover_prior_session_fills()

        # ── Step 5b': Multi-day pause arming (#251 + #253) ──────
        # Query the canonical intraday_tax_ledger for the trailing
        # rolling-PF and rolling-side-WR windows; fetch a NIFTY
        # rolling-7d return; arm the engine's session-wide pauses.
        # This runs ONCE per session (next session = fresh engine,
        # fresh arming). Pauses are session-sticky on purpose — a
        # cold streak does not warm up by lunch.
        self._arm_multiday_pauses()

        # ── Step 5c: Thursday F&O expiry adjustments ────────────
        self._apply_expiry_day_adjustments()

        # ── Step 5d: Market intelligence (VIX, FII/DII, pre-open) ─
        self._fetch_fii_dii_data()
        self._build_nifty_context()      # also fetches India VIX
        self._apply_vix_adjustments()
        self._fetch_preopen_data()

        if resumed > 0:
            # Already have live positions — run an immediate Claude
            # review so it can assess the resumed positions, then
            # start the normal monitor loop.
            open_symbols = [
                {"symbol": p["symbol"], "exchange": p["exchange"]}
                for p in self.engine.open_positions()
            ]
            try:
                quotes = self.zerodha.get_quotes(open_symbols)
                self._run_claude_review(quotes)
            except Exception as e:
                self.log.warning(f"Initial review quote fetch failed: {e}")
            self._run_monitor_loop()
        else:
            self._run_pre_market_scan()
            if self._shutdown_requested:
                return

            if not self._trade_plans:
                if self._scan_failed:
                    self.log.error("Scan failed — could not fetch market data. Exiting.")
                else:
                    self.log.warning("No trades recommended by Claude. Nothing to do today.")
                self._generate_report()
                return

            # ── Step 6: Wait for market open ──────────────────────────
            self._wait_for_market_open()
            if self._shutdown_requested:
                self._emergency_shutdown()
                return

            # ── Step 7: Observation period + Enter positions ──────────
            self._observe_and_enter()

            # If order API broke during entry, skip monitor and shut down
            if self.engine.is_order_api_broken():
                self.log.error(
                    "Order API broken during entry — shutting down. "
                    "No Claude calls will be made."
                )
                if self.engine.open_positions():
                    self._square_off()
            else:
                # ── Step 8: Monitor loop ──────────────────────────────────
                self._run_monitor_loop()

        # ── Step 9: Square off (if not already done) ──────────────
        # Always attempt square-off — even on Ctrl+C. Real money
        # positions must be closed. Resume feature is a safety net,
        # not the primary shutdown path.
        if self.engine.open_positions():
            self._square_off()

        # ── Step 9b: Reconcile with Zerodha ─────────────────────
        # Fetch actual trade data from Zerodha and correct any
        # price/P&L discrepancies before generating the report.
        try:
            self.engine.reconcile_with_zerodha()
        except Exception as e:
            self.log.warning(f"Reconciliation failed: {e} — using internal data")

        # ── Step 10: Generate report ──────────────────────────────
        try:
            self._generate_report()
        except Exception as e:
            self.log.error(f"Report generation failed: {e}")
            self.log.warning(
                "Trading data may not be saved. Check Zerodha for actual P&L."
            )

        # ── Step 11: Verify trades against Zerodha ───────────────
        if not self.cfg.DRY_RUN:
            try:
                from scripts.verify_trades import verify_today
                self.log.info("Verifying trades against Zerodha API...")
                stats = verify_today()
                corrected = stats.get("corrected", 0)
                if corrected:
                    self.log.info(f"Verification complete — {corrected} trade(s) corrected")
                else:
                    self.log.info("Verification complete — all trades match Zerodha")
            except Exception as e:
                self.log.warning(f"Trade verification failed: {e} — run manually with: python scripts/verify_trades.py")

        # ── Step 12: Rejection audit (post-trade review aid) ──────
        # Parses today's rejection log lines, fetches close prices,
        # prints a verdict table, and appends it to the trading
        # report. Read-only — never touches positions or the engine.
        # Disabled in DRY_RUN (no real Zerodha session) and when the
        # config kill-switch REJECTION_AUDIT_ENABLED is False.
        if (not self.cfg.DRY_RUN
                and getattr(self.cfg, "REJECTION_AUDIT_ENABLED", True)):
            try:
                from scripts.rejection_audit import run_audit
                self.log.info("Running rejection audit (post-trade review)...")
                run_audit(
                    append_report=True,
                    print_to_stdout=True,
                    log=self.log,
                    budget=self._budget or None,
                )
            except Exception as e:
                self.log.warning(
                    f"Rejection audit failed: {e} — run manually with: "
                    f"python scripts/rejection_audit.py --append-report"
                )

    # ================================================================
    # PRE-MARKET SCAN
    # ================================================================

    def _run_pre_market_scan(self, session_context: str = ""):
        """
        Fetches live quotes for the stock universe and asks Claude
        to pick the best intraday trade candidates.
        """
        now = now_ist()
        market_open = now.replace(
            hour=self.cfg.MARKET_OPEN_HOUR,
            minute=self.cfg.MARKET_OPEN_MINUTE,
            second=0, microsecond=0,
        )

        if now < market_open:
            self.log.section("PRE-MARKET SCAN")
        else:
            self.log.section("MARKET SCAN (joined late)")
            self.log.info(f"Started at {now.strftime('%I:%M %p')} — picking stocks at current prices")

        self.log.info(f"Universe: {self.cfg.SCAN_UNIVERSE}")
        self.log.info(f"Budget: Rs.{self._budget:,.2f}")
        self.log.info(f"Mode: {'DRY RUN' if self.cfg.DRY_RUN else 'LIVE TRADING'}")

        universe = self.scanner.get_universe()
        self.log.info(f"Scanning {len(universe)} stocks...")

        # Fetch live quotes for the universe
        stocks = [{"symbol": s, "exchange": "NSE"} for s in universe]
        quotes = self.zerodha.get_quotes_safe(stocks)
        if quotes is None:
            self.log.error("Could not fetch market data. Aborting scan.")
            self._scan_failed = True
            return

        if not quotes:
            self.log.warning("No quotes returned — market may not be open yet")
            # In pre-market, previous close data is still available
            # Proceed anyway — Claude can work with available data

        # Fetch NIFTY 50 index for trend context + market condition
        nifty_context = self._build_nifty_context()

        # Get historical performance context for Claude
        perf_context = self.tracker.get_claude_prompt_context()

        # Ask Claude to pick trades
        self.engine.claude_calls += 1
        self._trade_plans = self.scanner.scan(quotes, nifty_context, perf_context, session_context)

        if self._trade_plans:
            self.log.section("TRADE PLAN")
            for i, t in enumerate(self._trade_plans, 1):
                self.log.info(
                    f"  Trade {i}: {t['side']} {t['qty']}x {t['symbol']} "
                    f"@ Rs.{t['entry_price']:.2f} | "
                    f"SL: Rs.{t['stop_loss']:.2f} | "
                    f"Target: Rs.{t['target_price']:.2f}"
                )
                self.log.info(f"           {t.get('rationale', '')}")

    # ================================================================
    # ENTER POSITIONS
    # ================================================================

    def _enter_positions(self, trades: list[dict] | None = None):
        """
        Enters all trade plans at market open.
        Each trade goes through OrderEngine which checks budget and
        position limits before placing/logging the order.

        Stops immediately if Zerodha order API is broken (consecutive
        failures hit the limit).

        If all candidates fail and the R:R floor is still at INITIAL
        level, activates the delta step-down and retries once.
        """
        self.log.section("ENTERING POSITIONS")

        # Prime VIX-spike state for the engine-side gate (#211) before
        # any per-trade attempts. Closes the window where intraday VIX
        # spiked between the manager's last NIFTY recheck and now.
        # Cheap (one Kite quote) and idempotent.
        try:
            self.engine.set_vix_spike(self._check_vix_spike())
        except Exception as e:
            self.log.debug(f"VIX-spike prime before _enter_positions failed: {e}")

        # Sync with Zerodha to detect any positions opened manually
        # between the scan and now (affects budget + slot limits)
        self.engine.sync_external_positions()

        plans = trades if trades is not None else self._trade_plans
        # Reset the engine's per-batch R:R rejection counter so the
        # mid-day retry decision below can tell whether ANY of these
        # candidates failed for an R:R-floor reason. If none did, a
        # second pass at a lower floor is guaranteed to reject the
        # same set (RSI / VWAP / pattern / liquidity rejections don't
        # depend on the floor) — wasting Claude/Kite calls and log
        # noise. Observed 2026-04-24 11:00:56 → 11:01:13 batch.
        # NOTE (#243): the actual mid-day retry pass was removed once
        # the R:R floor became uniform 1.3 all day — a "step-down"
        # retry would re-run with the SAME floor and reject the same
        # candidates. The reset is kept so future floor-tiering work
        # has the counter ready, and so #206's no-retry skip-condition
        # remains observable in the logs.
        self.engine._rr_rejection_count = 0
        entered = self._attempt_entries(plans)

        self.log.success(f"Entered {entered} position(s)")

        # Show budget utilisation after entry
        if entered > 0:
            exposure = self.engine._total_open_exposure()
            budget = self.engine._budget
            remaining = budget - exposure
            open_count = len(self.engine.open_positions())
            self.log.info(
                f"  Budget deployed: Rs.{exposure:,.0f} / Rs.{budget:,.0f} "
                f"({exposure/budget*100:.0f}%) | "
                f"Remaining: Rs.{remaining:,.0f} | "
                f"Positions: {open_count}/{self.cfg.MAX_POSITIONS}"
            )

        # Track scan result for adaptive R:R relaxation
        self.engine.record_scan_result(entered)

        # Mark initial entry done — subsequent calls are mid-day rescans
        self._initial_entry_done = True

        # Return so callers can distinguish a no-op entry pass (every
        # candidate rejected by a gate, e.g. NO_RESCUE_ZONE post-#246)
        # from one that actually opened a fresh position. Critical for
        # the V2 monitor loop's `_last_candle_scan` bookkeeping — see
        # manager_v2.py callers. (#250 bugfix)
        return entered

    def _attempt_entries(self, plans: list[dict]) -> int:
        """Run through trade plans and attempt to enter each. Returns count."""
        entered = 0
        tried = 0
        skipped_full = 0
        for trade in plans:
            if self._shutdown_requested:
                break
            if self.engine.is_order_api_broken():
                self.log.error(
                    "Zerodha order API is broken — aborting remaining entries"
                )
                break
            if len(self.engine.open_positions()) >= self.cfg.MAX_POSITIONS:
                skipped_full = len(plans) - tried
                break
            tried += 1
            if self.engine.enter_trade(trade):
                entered += 1
            time.sleep(0.5)

        # Summary: show user what happened across all candidates
        rejected = tried - entered
        remaining = len(plans) - tried - skipped_full
        if tried > 0:
            parts = [f"Tried {tried} candidate(s): {entered} entered"]
            if rejected > 0:
                parts.append(f"{rejected} rejected (see warnings above)")
            if skipped_full > 0:
                parts.append(f"{skipped_full} skipped (slots full)")
            if remaining > 0:
                parts.append(f"{remaining} not tried (slots full or shutdown)")
            self.log.info("  Entry summary: " + ", ".join(parts))
        return entered

    # ================================================================
    # OBSERVATION PERIOD + DELAYED ENTRY
    # ================================================================

    def _observe_and_enter(self):
        """
        If ENTRY_DELAY_MINUTES > 0, observes prices after market open
        and only enters stocks that show directional movement (>ENTRY_MIN_MOVE_PCT
        from their open price). Filters out whipsaw / indecisive stocks.

        If ENTRY_DELAY_MINUTES == 0, enters immediately (old behaviour).

        Smart delay: if the market has already been open for longer than
        the configured delay (e.g. bot started at 9:40 with 15-min delay),
        the delay is shortened to 5 min since the opening volatility has
        already settled and prices have established direction.
        """
        delay = self.cfg.ENTRY_DELAY_MINUTES
        if delay <= 0:
            # Late entry guard: don't enter if too few minutes remain
            now_check = now_ist()
            sq_off_check = now_check.replace(
                hour=self.cfg.SQUARE_OFF_HOUR,
                minute=self.cfg.SQUARE_OFF_MINUTE,
                second=0, microsecond=0,
            )
            mins_to_close = (sq_off_check - now_check).total_seconds() / 60
            if mins_to_close < self.cfg.MIN_MINUTES_FOR_ENTRY:
                self.log.warning(
                    f"Only {mins_to_close:.0f} min until square-off — "
                    f"need {self.cfg.MIN_MINUTES_FOR_ENTRY} min for entry. Skipping. "
                    f"(change MIN_MINUTES_FOR_ENTRY in config.py to allow later entries)"
                )
                return
            self._enter_positions()
            return

        # Observation-window semantics:
        #   entry_time = market_open + delay (so at 9:30 start with delay=30,
        #   entry is at 9:45 — we've already observed 15 of the 30 min).
        # If market has already been open LONGER than the configured delay
        # (late script start), use a short floor observation from now:
        #   normal days: 5 min
        #   expiry days: EXPIRY_ENTRY_DELAY_LATE_FLOOR (15 min) — F&O
        #     settlement creates instability through the whole morning.
        now = now_ist()
        market_open = now.replace(
            hour=self.cfg.MARKET_OPEN_HOUR,
            minute=self.cfg.MARKET_OPEN_MINUTE,
            second=0, microsecond=0,
        )
        target_entry_time = market_open + datetime.timedelta(minutes=delay)
        minutes_since_open = (now - market_open).total_seconds() / 60

        if now >= target_entry_time:
            # Late script start — observation window already passed.
            expiry_mode = getattr(self, '_expiry_applied', False)
            floor = self.cfg.EXPIRY_ENTRY_DELAY_LATE_FLOOR if expiry_mode else 5
            entry_time = now + datetime.timedelta(minutes=floor)
            delay = floor
            self.log.info(
                f"Market already open for {minutes_since_open:.0f} min — "
                f"observation window passed, using {floor}-min floor "
                f"({'expiry' if expiry_mode else 'opening volatility passed'})"
            )
        else:
            # Normal path — entry aligned to market_open + delay
            entry_time = target_entry_time
            remaining = (target_entry_time - now).total_seconds() / 60
            self.log.info(
                f"Entry aligned to market_open + {delay} min "
                f"(market open {minutes_since_open:.0f} min, "
                f"{remaining:.0f} min to entry)"
            )

        self.log.section(f"OBSERVATION MODE — entry at {entry_time.strftime('%I:%M %p')}")
        self.log.info(
            f"Trades will be entered at {entry_time.strftime('%I:%M %p')} "
            f"for stocks with >{self.cfg.ENTRY_MIN_MOVE_PCT}% directional move"
        )

        # Collect open prices at start of observation
        plan_symbols = [
            {"symbol": t["symbol"], "exchange": t.get("exchange", "NSE")}
            for t in self._trade_plans
        ]
        try:
            open_quotes = self.zerodha.get_quotes(plan_symbols)
        except Exception as e:
            self.log.warning(f"Quote fetch failed during observation: {e}")
            self.log.warning("Skipping observation — will enter with pre-trade checks only")
            self._enter_positions()
            return

        open_prices = {}
        for t in self._trade_plans:
            key = f"{t.get('exchange', 'NSE')}:{t['symbol']}"
            q = open_quotes.get(key, {})
            ohlc = q.get("ohlc", {})
            day_open = ohlc.get("open", 0)
            if day_open > 0:
                open_prices[t["symbol"]] = day_open
            else:
                # Use last_price as fallback
                open_prices[t["symbol"]] = q.get("last_price", t["entry_price"])

        # Wait until entry time — print status during observation
        while now_ist() < entry_time and not self._shutdown_requested:
            remaining = (entry_time - now_ist()).total_seconds()
            mins, secs = divmod(int(remaining), 60)
            print(
                f"\r  \U0001f50d Observing: {mins:02d}:{secs:02d} remaining  ",
                end="", flush=True,
            )
            time.sleep(1)
        print()

        if self._shutdown_requested:
            return

        # Fetch live quotes after observation period (retry up to 3 times)
        current_quotes = None
        for attempt in range(1, 4):
            try:
                current_quotes = self.zerodha.get_quotes(plan_symbols)
                break
            except Exception as e:
                self.log.warning(
                    f"Quote fetch failed after observation (attempt {attempt}/3): {e}"
                )
                if attempt < 3:
                    time.sleep(2 * attempt)

        if current_quotes is None:
            self.log.warning(
                "All retries failed — skipping observation filter. "
                "Trades will enter with pre-trade checks only"
            )
            self._enter_positions()
            return

        # Filter: only enter stocks that moved >ENTRY_MIN_MOVE_PCT from open
        min_move = self.cfg.ENTRY_MIN_MOVE_PCT
        confirmed = []
        skipped = []

        for trade in self._trade_plans:
            symbol = trade["symbol"]
            key = f"{trade.get('exchange', 'NSE')}:{symbol}"
            q = current_quotes.get(key, {})
            current_price = q.get("last_price", 0)
            day_open_price = open_prices.get(symbol, 0)

            if current_price <= 0 or day_open_price <= 0:
                confirmed.append(trade)  # no data — let it through
                continue

            move_pct = abs(current_price - day_open_price) / day_open_price * 100
            side = trade["side"]

            # Direction must align: BUY needs price moving UP, SELL needs DOWN
            direction_ok = (
                (side == "BUY" and current_price > day_open_price) or
                (side == "SELL" and current_price < day_open_price)
            )

            if move_pct >= min_move and direction_ok:
                # Update entry price to current market price
                trade["entry_price"] = round(current_price, 2)
                confirmed.append(trade)
                direction = "↑" if current_price > day_open_price else "↓"
                self.log.info(
                    f"  ✓ {symbol}: {direction} {move_pct:.2f}% from open "
                    f"(Rs.{day_open_price:.2f} → Rs.{current_price:.2f}) — CONFIRMED"
                )
            elif move_pct >= min_move and not direction_ok:
                skipped.append(trade)
                direction = "↑" if current_price > day_open_price else "↓"
                self.log.info(
                    f"  ✗ {symbol}: {direction} {move_pct:.2f}% but WRONG direction "
                    f"for {side} — SKIPPED"
                )
            else:
                skipped.append(trade)
                self.log.info(
                    f"  ✗ {symbol}: only {move_pct:.2f}% move from open "
                    f"(Rs.{day_open_price:.2f} → Rs.{current_price:.2f}) — SKIPPED"
                )

        if skipped:
            self.log.info(
                f"Filtered out {len(skipped)} stocks with <{min_move}% move"
            )

        if confirmed:
            # Late entry guard after observation period
            now_post = now_ist()
            sq_off_post = now_post.replace(
                hour=self.cfg.SQUARE_OFF_HOUR,
                minute=self.cfg.SQUARE_OFF_MINUTE,
                second=0, microsecond=0,
            )
            mins_left_post = (sq_off_post - now_post).total_seconds() / 60
            if mins_left_post < self.cfg.MIN_MINUTES_FOR_ENTRY:
                self.log.warning(
                    f"Only {mins_left_post:.0f} min until square-off after observation — "
                    f"need {self.cfg.MIN_MINUTES_FOR_ENTRY} min. Skipping all entries. "
                    f"(change MIN_MINUTES_FOR_ENTRY in config.py to allow later entries)"
                )
                return
            # Stale-score guard (#196): refresh composite score on a
            # fresh candle pull and drop trades whose conviction has
            # decayed during the observation window. No-op when the
            # active scanner doesn't expose `_analyse_stock` (V1).
            confirmed = self._stale_score_filter(confirmed, delay)
            if not confirmed:
                self.log.warning(
                    "All entries dropped by stale-score guard — "
                    "scores decayed during observation"
                )
                return
            self._enter_positions(confirmed)
        else:
            self.log.warning("No stocks passed the observation filter")

    # ================================================================
    # STALE-SCORE GUARD (Roadmap #196)
    # ================================================================

    def _stale_score_filter(
        self,
        trades: list[dict],
        wait_minutes: int,
    ) -> list[dict]:
        """Drop entries whose composite score has decayed during the
        observation window (Roadmap #196).

        Pipeline:
          1.  Skip entirely when feature disabled, wait < min, or the
              active scanner can't re-score (V1 StockScanner has no
              `_analyse_stock` — V1 is FROZEN, not modified).
          2.  Per surviving trade, fetch fresh score via
              `scanner._analyse_stock(symbol, exchange)`.
          3.  Abort the trade if:
                - sign(fresh) != sign(entry)             (signal flipped)
                - abs(fresh) < abs(entry) × FRACTION    (decay below floor)
          4.  Otherwise update `trade["_entry_score"] = fresh` so all
              downstream score-gated checks compare against the
              freshest available value (lunch-lull bypass, ADX override,
              gap-coherence override, average-down prevention, …).

        Fail-open on any per-symbol exception or missing fresh score —
        we log a warning but let the trade through; the existing entry
        gates remain active.
        """
        if not getattr(self.cfg, "FRESH_ENTRY_RECHECK_ENABLED", False):
            return trades
        if wait_minutes < self.cfg.FRESH_ENTRY_RECHECK_MIN_WAIT_MINUTES:
            return trades
        if not hasattr(self.scanner, "_analyse_stock"):
            return trades

        fraction = self.cfg.FRESH_ENTRY_DECAY_FRACTION
        survivors: list[dict] = []
        for trade in trades:
            symbol   = trade["symbol"]
            exchange = trade.get("exchange", "NSE")
            entry_score = trade.get("_entry_score")

            try:
                entry_val = float(entry_score) if entry_score is not None else 0.0
            except (TypeError, ValueError):
                entry_val = 0.0

            if entry_val == 0.0:
                # No entry score to compare against — let the trade
                # through; downstream gates will handle it.
                survivors.append(trade)
                continue

            try:
                fresh = self.scanner._analyse_stock(symbol, exchange)
            except Exception as e:
                self.log.warning(
                    f"  ⚠ {symbol}: stale-score recheck failed ({type(e).__name__}: {e}) — "
                    f"letting trade through with entry score {entry_val:+.1f}"
                )
                survivors.append(trade)
                continue

            if not fresh or "combined_score" not in fresh:
                self.log.warning(
                    f"  ⚠ {symbol}: stale-score recheck returned no data — "
                    f"letting trade through with entry score {entry_val:+.1f}"
                )
                survivors.append(trade)
                continue

            try:
                fresh_val = float(fresh["combined_score"])
            except (TypeError, ValueError):
                survivors.append(trade)
                continue

            # Sign flip — abort.
            if (entry_val > 0) != (fresh_val > 0):
                self.log.info(
                    f"  ✗ {symbol}: stale-score guard — entry {entry_val:+.1f} → "
                    f"fresh {fresh_val:+.1f} (SIGN FLIP), skipping"
                )
                continue

            floor = abs(entry_val) * fraction
            if abs(fresh_val) < floor:
                retained_pct = abs(fresh_val) / abs(entry_val) * 100
                self.log.info(
                    f"  ✗ {symbol}: stale-score guard — entry {entry_val:+.1f} → "
                    f"fresh {fresh_val:+.1f} ({retained_pct:.0f}% retained, "
                    f"floor {fraction*100:.0f}%), skipping"
                )
                continue

            # Monotonic-direction gate (Roadmap #199, follow-up to #196).
            # Even when retention floor is met, a *falling* score during
            # the observation window is the market actively telling us the
            # edge is decaying. Require fresh ≥ entry magnitude (within a
            # small jitter tolerance). Kill-switch:
            # FRESH_ENTRY_REQUIRE_MONOTONIC = False reverts to legacy
            # (#196) behaviour where only retention floor is enforced.
            if getattr(self.cfg, "FRESH_ENTRY_REQUIRE_MONOTONIC", False):
                tolerance = float(
                    getattr(self.cfg, "FRESH_ENTRY_MONOTONIC_TOLERANCE", 0.0)
                )
                if abs(fresh_val) + tolerance < abs(entry_val):
                    drop = abs(entry_val) - abs(fresh_val)
                    self.log.info(
                        f"  ✗ {symbol}: stale-score guard — entry {entry_val:+.1f} → "
                        f"fresh {fresh_val:+.1f} (magnitude DROPPED by {drop:.1f}, "
                        f"tolerance {tolerance:.1f}; falling score = decaying edge), "
                        f"skipping"
                    )
                    continue

            # Survived — refresh stored score so downstream gates use
            # the latest value.
            retained_pct = (
                abs(fresh_val) / abs(entry_val) * 100 if entry_val else 0
            )
            self.log.info(
                f"  ✓ {symbol}: stale-score recheck — entry {entry_val:+.1f} → "
                f"fresh {fresh_val:+.1f} ({retained_pct:.0f}% retained, floor "
                f"{fraction*100:.0f}%)"
            )
            trade["_entry_score"] = fresh_val
            survivors.append(trade)

        dropped = len(trades) - len(survivors)
        if dropped > 0:
            self.log.info(
                f"Stale-score guard: dropped {dropped} of {len(trades)} "
                f"entries after {wait_minutes}-min observation"
            )
        return survivors


    # ================================================================
    # MONITOR LOOP
    # ================================================================

    def _run_monitor_loop(self):
        """
        Main trading loop that runs from market open until square-off.

        Two independent timers:
          1. Price polling (every PRICE_POLL_SECONDS) — checks SL/target
             hits using rule-based logic. No Claude API calls.
          2. Claude review (every POSITION_REVIEW_MINUTES) — asks Claude
             to re-evaluate positions and suggest adjustments.

        The loop exits when:
          - Square-off time is reached
          - All positions are closed (SL/target hit for all)
          - Circuit breaker triggers (max daily loss exceeded)
          - User presses Ctrl+C (graceful shutdown)
        """
        self.log.section("MONITORING — Live price tracking")
        self.log.info(
            f"Price poll: every {self.cfg.PRICE_POLL_SECONDS}s | "
            f"Claude review: every {self.cfg.POSITION_REVIEW_MINUTES}min"
        )

        poll_interval    = self.cfg.PRICE_POLL_SECONDS
        review_interval  = self.cfg.POSITION_REVIEW_MINUTES * 60  # convert to seconds
        last_review_time = time.time()
        self._last_external_sync = time.time()

        while not self._shutdown_requested:
            now = now_ist()

            # ── Check if it's square-off time ─────────────────────
            if self._is_square_off_time(now):
                self._clear_status_line()
                self.log.info("Square-off time reached")
                break

            # ── Check if all positions are closed ─────────────────
            if not self.engine.open_positions():
                # If order API is broken, don't scan for more trades
                if self.engine.is_order_api_broken():
                    self._clear_status_line()
                    self.log.error(
                        "All positions closed and order API is broken — "
                        "stopping (not scanning for new trades)"
                    )
                    break

                # Check if there's enough time to re-scan and trade more
                sq_now = now_ist()
                sq_off = sq_now.replace(
                    hour=self.cfg.SQUARE_OFF_HOUR,
                    minute=self.cfg.SQUARE_OFF_MINUTE,
                    second=0, microsecond=0,
                )
                mins_remaining = (sq_off - sq_now).total_seconds() / 60

                if mins_remaining >= self.cfg.MIN_MINUTES_FOR_ENTRY:
                    if self.engine.is_sl_paused():
                        self._clear_status_line()
                        self.log.info(
                            f"All positions closed but SL pause active — "
                            f"waiting for pause to expire before re-scanning"
                        )
                        time.sleep(poll_interval)
                        continue
                    self._clear_status_line()
                    self.log.info(
                        f"All positions closed with {mins_remaining:.0f} min left — "
                        f"scanning for new opportunities..."
                    )
                    # Sync with Zerodha before re-scan (detect manual trades, refresh budget)
                    self.engine.sync_external_positions()
                    self.engine.refresh_budget()

                    # Build session context for the re-scan so Claude knows
                    # what already happened today (P&L, traded symbols)
                    closed_trades = self.engine.closed_positions()
                    traded_symbols = list({p["symbol"] for p in closed_trades})
                    day_pnl = self.engine.day_pnl()
                    session_ctx = (
                        f"\nSESSION CONTEXT (mid-day re-scan):\n"
                        f"  Day P&L so far: Rs.{day_pnl:,.2f} from {len(closed_trades)} closed trades.\n"
                        f"  Already traded today: {', '.join(traded_symbols) if traded_symbols else 'none'}.\n"
                        f"  DO NOT pick any stock already traded today unless you have a "
                        f"fundamentally different setup (opposite direction or new catalyst).\n"
                        f"  If day P&L is negative, prioritise capital preservation — "
                        f"pick only high-conviction setups with tight stops.\n"
                    )
                    self._trade_plans = []
                    self._run_pre_market_scan(session_context=session_ctx)
                    if self._trade_plans:
                        self._enter_positions()
                        last_review_time = time.time()  # reset review timer
                        continue
                    else:
                        self.log.info("No new trades found — done for the day")
                        break
                else:
                    self._clear_status_line()
                    self.log.info(
                        f"All positions closed — only {mins_remaining:.0f} min left, "
                        f"not enough time for new trades "
                        f"(change MIN_MINUTES_FOR_ENTRY in config.py to allow later entries)"
                    )
                    break

            # ── Periodic external position sync (detect manual trades) ─
            # Run FIRST before quote fetch so manual positions are included
            # in quotes, SL/target checks, and slot counting.
            external_sync_interval = self.cfg.V2_CANDLE_RESCAN_MINUTES * 60
            if not self.cfg.DRY_RUN and time.time() - self._last_external_sync >= external_sync_interval:
                new_ext = self.engine.sync_external_positions()
                if new_ext > 0:
                    self._clear_status_line()
                    self.log.info(f"Detected {new_ext} manual trade(s) — now managed by bot")
                self._last_external_sync = time.time()

            # ── Fetch live quotes ─────────────────────────────────
            open_symbols = [
                {"symbol": p["symbol"], "exchange": p["exchange"]}
                for p in self.engine.open_positions()
            ]
            try:
                quotes = self.zerodha.get_quotes(open_symbols)
            except Exception as e:
                self.log.warning(f"Quote fetch failed: {e} — retrying next cycle")
                time.sleep(poll_interval)
                continue

            # ── Rule-based SL/target check (free) ─────────────────
            closed = self.engine.check_stops_and_targets(quotes)
            if closed > 0:
                self._clear_status_line()
                self.log.info(f"{closed} position(s) auto-closed")
                # ── Partial re-scan: fill empty slots with new trades ─
                # Sync with Zerodha to detect manual trades before counting slots
                self.engine.sync_external_positions()
                self.engine.refresh_budget()
                open_count = len(self.engine.open_positions())
                rescan_cooldown = 120  # min 2 min between partial re-scans
                time_since_rescan = time.time() - self._last_partial_rescan
                if (
                    open_count > 0
                    and open_count < self.cfg.MAX_POSITIONS
                    and not self.engine.is_order_api_broken()
                    and not self._circuit_broken
                    and not self.engine.is_sl_paused()
                    and time_since_rescan >= rescan_cooldown
                ):
                    sq_now = now_ist()
                    sq_off = sq_now.replace(
                        hour=self.cfg.SQUARE_OFF_HOUR,
                        minute=self.cfg.SQUARE_OFF_MINUTE,
                        second=0, microsecond=0,
                    )
                    mins_left = (sq_off - sq_now).total_seconds() / 60
                    slots = self.cfg.MAX_POSITIONS - open_count
                    if mins_left >= self.cfg.MIN_MINUTES_FOR_ENTRY:
                        self.log.info(
                            f"{slots} slot(s) free, {mins_left:.0f} min left — "
                            f"scanning for replacement trades..."
                        )
                        closed_trades = self.engine.closed_positions()
                        traded_symbols = list({p["symbol"] for p in closed_trades})
                        day_pnl = self.engine.day_pnl()
                        session_ctx = (
                            f"\nSESSION CONTEXT (partial re-scan — {slots} slot(s) available):\n"
                            f"  Day P&L so far: Rs.{day_pnl:,.2f} from {len(closed_trades)} closed trades.\n"
                            f"  Already traded today: {', '.join(traded_symbols) if traded_symbols else 'none'}.\n"
                            f"  Currently holding: {', '.join(p['symbol'] for p in self.engine.open_positions())}.\n"
                            f"  You have {slots} slot(s) available. Pick at most {slots} new trade(s).\n"
                            f"  DO NOT pick any stock already traded or currently held.\n"
                            f"  If day P&L is negative, only pick high-conviction setups.\n"
                        )
                        self._trade_plans = []
                        self._run_pre_market_scan(session_context=session_ctx)
                        if self._trade_plans:
                            self._enter_positions()
                            last_review_time = time.time()
                        else:
                            self.log.info("Partial re-scan: no replacement trades found")
                        self._last_partial_rescan = time.time()

            # ── Check if Zerodha order API is broken ──────────────
            # If consecutive order failures hit the limit, stop
            # calling Claude (wastes money) and shut down gracefully.
            if self.engine.is_order_api_broken():
                self._clear_status_line()
                self.log.error(
                    "ZERODHA ORDER API BROKEN — stopping all trading. "
                    "Will not call Claude again. "
                    "Attempting to close any open positions..."
                )
                if self.engine.open_positions():
                    self._square_off()
                break

            # ── Circuit breaker check ─────────────────────────────
            if self.engine.check_circuit_breaker():
                self._circuit_broken = True
                self._square_off()
                cooldown = self.cfg.CIRCUIT_BREAKER_COOLDOWN_MINUTES
                if cooldown > 0 and not self.engine.circuit_breaker_trips_exhausted():
                    sq_off = now.replace(
                        hour=self.cfg.SQUARE_OFF_HOUR,
                        minute=self.cfg.SQUARE_OFF_MINUTE,
                        second=0, microsecond=0,
                    )
                    mins_left = (sq_off - now).total_seconds() / 60
                    if mins_left > cooldown + self.cfg.MIN_MINUTES_FOR_ENTRY:
                        self.log.info(
                            f"Circuit breaker cooldown: waiting {cooldown} min "
                            f"before resuming with reduced budget..."
                        )
                        # Polling sleep — check shutdown every 10s
                        for _ in range(cooldown * 6):
                            if self._shutdown_requested:
                                break
                            time.sleep(10)
                        if self._shutdown_requested:
                            break
                        self._circuit_broken = False
                        self.engine.reset_circuit_breaker_baseline()
                        self.engine.refresh_budget()
                        self.log.info(
                            f"Circuit breaker cooldown complete — resuming with "
                            f"loss-adjusted budget Rs.{self.engine.loss_adjusted_budget():,.2f}"
                        )
                        continue
                self.log.warning(
                    "Circuit breaker: stopping for the day "
                    "(change MAX_CIRCUIT_BREAKER_TRIPS or CIRCUIT_BREAKER_COOLDOWN_MINUTES in config.py to adjust)"
                )
                break

            # ── Periodic Claude review (paid) ─────────────────────
            elapsed = time.time() - last_review_time
            if elapsed >= review_interval and self.engine.open_positions():
                self._clear_status_line()
                self._run_claude_review(quotes)
                last_review_time = time.time()

            # ── Late-day loser exit ───────────────────────────────
            if self.engine.open_positions():
                loser_closed = self.engine.check_loser_exit(quotes)
                if loser_closed > 0:
                    self._clear_status_line()
                    self.log.info(f"{loser_closed} losing position(s) auto-exited (late-day loser cleanup)")

            # ── Print compact status line ─────────────────────────
            self._print_status(quotes)

            # ── Sleep until next poll ─────────────────────────────
            time.sleep(poll_interval)

    # ================================================================
    # CLAUDE REVIEW
    # ================================================================

    def _run_claude_review(self, quotes: dict):
        """
        Periodic Claude review of open positions.
        Claude can recommend exits, SL/target adjustments, or new trades.
        """
        self.log.section("CLAUDE REVIEW")
        self.engine.claude_calls += 1

        nifty_context = self._build_nifty_context()

        actions = self.scanner.review_positions(
            open_positions   = self.engine.open_positions(),
            quotes           = quotes,
            day_pnl          = self.engine.day_pnl(),
            budget_remaining = self.engine.budget_remaining(),
            nifty_context    = nifty_context,
            closed_positions = self.engine.closed_positions(),
        )

        if actions:
            self.engine.apply_review_actions(actions, quotes)

        # Re-fetch quotes to include any newly added positions
        all_open = self.engine.open_positions()
        if all_open:
            open_symbols = [
                {"symbol": p["symbol"], "exchange": p["exchange"]}
                for p in all_open
            ]
            try:
                quotes = self.zerodha.get_quotes(open_symbols)
            except Exception:
                pass  # fall back to original quotes

        # Always show per-stock P&L table after Claude review
        self.engine.print_position_status(quotes)

    # ================================================================
    # SQUARE OFF
    # ================================================================

    def _square_off(self):
        """Closes all open positions at current market prices."""
        if not self.engine.open_positions():
            return

        # Fetch latest quotes for open positions
        open_symbols = [
            {"symbol": p["symbol"], "exchange": p["exchange"]}
            for p in self.engine.open_positions()
        ]
        try:
            quotes = self.zerodha.get_quotes(open_symbols)
        except Exception as e:
            self.log.error(
                f"Cannot fetch quotes for square-off: {e} — "
                f"MANUAL INTERVENTION MAY BE NEEDED"
            )
            # Use entry prices as fallback for P&L calculation
            quotes = {}

        self.engine.square_off_all(quotes)

    # ================================================================
    # REPORT GENERATION
    # ================================================================

    def _generate_report(self):
        """
        Writes the end-of-day trading report with full P&L breakdown,
        including taxes, Zerodha charges, and Claude API costs.
        Also records trades to the performance database.
        """
        self.log.section("END OF DAY REPORT")

        pnl_summary = self.engine.net_profit()
        self.report.save_trading_day(
            positions        = self.engine.positions,
            trade_log        = self.engine.trade_log,
            pnl              = pnl_summary,
            dry_run          = self.cfg.DRY_RUN,
            budget           = self._budget,
            market_condition = self._market_condition,
        )

        # Record to performance database (live trades only — dry-run
        # data is excluded to keep the trades table clean for analysis)
        if not self.cfg.DRY_RUN:
            self.tracker.record_trades(
                self.engine.positions,
                market_condition=self._market_condition,
            )

        self._print_pnl_summary(pnl_summary)

    # ================================================================
    # ACCOUNT SNAPSHOT
    # ================================================================

    def _print_account_snapshot(self):
        """
        Delegates to ZerodhaClient for display, captures the
        returned funds amount for budget calculation.
        """
        self._available_funds = self.zerodha.print_account_snapshot()

    # ================================================================
    # MULTI-DAY PAUSE ARMING (Roadmap #251 + #253)
    # ================================================================

    def _arm_multiday_pauses(self) -> None:
        """Read the trailing N-day intraday_tax_ledger and NIFTY
        history; arm session-wide pauses on the engine.

        This runs ONCE at session start. Both pauses are sticky for
        the session (a cold streak does not warm up by lunch); they
        clear naturally when a fresh OrderEngine is constructed for
        the next session.

        Failure modes are non-blocking — if the DB read or NIFTY
        fetch fails, neither pause arms and the bot trades normally.
        """
        cfg = self.cfg
        rolling_pf_enabled = getattr(cfg, "ROLLING_PF_PAUSE_ENABLED", True)
        directional_enabled = getattr(cfg, "DIRECTIONAL_PAUSE_ENABLED", True)
        if not rolling_pf_enabled and not directional_enabled:
            return

        # ── Choose the longest lookback so we read the DB once ────
        pf_lookback = int(getattr(cfg, "ROLLING_PF_PAUSE_LOOKBACK_DAYS", 3))
        dir_lookback = int(getattr(cfg, "DIRECTIONAL_PAUSE_LOOKBACK_DAYS", 7))
        lookback = max(pf_lookback, dir_lookback)
        today = now_ist().date()
        # Pull a comfortable calendar buffer (lookback × 2 + weekends)
        # so we have enough rows to count back N *trading* days.
        calendar_floor = today - datetime.timedelta(days=lookback * 2 + 7)

        # ── Read the canonical ledger (read-only) ─────────────────
        rolling_pf = rolling_net = None
        rolling_n_trades = 0
        side_stats: dict[str, dict] = {}
        try:
            from scripts.tax_db import get_db
            conn = get_db()
            rows = conn.execute(
                "SELECT date, side, gross_pnl FROM intraday_tax_ledger "
                "WHERE date >= ? ORDER BY date",
                (calendar_floor.isoformat(),),
            ).fetchall()
            conn.close()
        except Exception as e:
            self.log.warning(
                f"Multi-day pause arming: skipped (DB read failed: {e})"
            )
            rows = []

        if rows:
            # Group by date so we can count trading days, not rows.
            by_date: dict[str, list] = {}
            for r in rows:
                by_date.setdefault(r["date"], []).append(r)
            sorted_dates = sorted(by_date.keys())

            # ── Rolling-PF window (pf_lookback trading days) ──────
            pf_dates = sorted_dates[-pf_lookback:]
            pf_rows = [r for d in pf_dates for r in by_date[d]]
            if pf_rows:
                wins = sum(r["gross_pnl"] for r in pf_rows if r["gross_pnl"] > 0)
                losses = sum(r["gross_pnl"] for r in pf_rows if r["gross_pnl"] < 0)
                rolling_net = wins + losses
                rolling_n_trades = len(pf_rows)
                if losses < 0:
                    rolling_pf = wins / abs(losses)
                else:
                    # No losses in the window — PF is undefined / huge.
                    # Treat as "definitely not a cold streak"; leave
                    # rolling_pf at a value above any threshold.
                    rolling_pf = 999.0

            # ── Directional window (dir_lookback trading days) ────
            dir_dates = sorted_dates[-dir_lookback:]
            dir_rows = [r for d in dir_dates for r in by_date[d]]
            for side in ("BUY", "SELL"):
                side_rows = [r for r in dir_rows if r["side"] == side]
                wins = sum(1 for r in side_rows if r["gross_pnl"] > 0)
                side_stats[side] = {"n": len(side_rows), "wins": wins}

        # ── Fetch rolling NIFTY return for the directional check ──
        nifty_return_pct: float | None = None
        if directional_enabled:
            try:
                to_date = today
                from_date = to_date - datetime.timedelta(days=dir_lookback * 2 + 5)
                candles = self.zerodha.get_historical(
                    "NIFTY 50", "NSE", from_date, to_date, "day"
                )
                if candles and len(candles) >= 2:
                    # Use the last `dir_lookback` daily closes.
                    series = candles[-(dir_lookback + 1):]
                    if len(series) >= 2 and series[0]["close"] > 0:
                        first_close = float(series[0]["close"])
                        last_close = float(series[-1]["close"])
                        nifty_return_pct = (
                            (last_close - first_close) / first_close * 100
                        )
            except Exception as e:
                self.log.warning(
                    f"Multi-day pause arming: NIFTY history fetch "
                    f"failed ({e}); directional pause stays disabled "
                    f"this session."
                )

        # ── Hand off to engine ────────────────────────────────────
        self.engine.arm_multiday_pauses(
            rolling_pf=rolling_pf,
            rolling_net=rolling_net,
            rolling_n_trades=rolling_n_trades,
            side_stats=side_stats,
            nifty_return_pct=nifty_return_pct,
        )

    # ================================================================
    # THURSDAY F&O EXPIRY ADJUSTMENTS
    # ================================================================

    def _apply_expiry_day_adjustments(self):
        """
        On weekly F&O expiry Thursdays, NIFTY stocks see wider swings.
        Dynamically widen SLs, reduce position count, and raise min score.

        When Thursday is an NSE holiday (Holi, Eid, etc., ~3 days/year),
        expiry shifts to the prior trading day (normally Wednesday).
        Roadmap #41 — gated by `HOLIDAY_SHIFTED_EXPIRY_ENABLED`.
        """
        if getattr(self, '_expiry_applied', False):
            return
        today = now_ist()
        wd = today.weekday()  # 0=Mon ... 6=Sun
        is_expiry = (wd == 3)  # normal Thursday
        if (
            not is_expiry
            and wd == 2  # Wednesday
            and getattr(self.cfg, 'HOLIDAY_SHIFTED_EXPIRY_ENABLED', True)
        ):
            # Tomorrow's Thursday in YYYY-MM-DD
            tomorrow_str = (
                today.date() + datetime.timedelta(days=1)
            ).strftime('%Y-%m-%d')
            # Year-aware lookup so calendar rollover (e.g. 2026-12-31 →
            # 2027-01-01) doesn't silently fail-open. Falls back to empty.
            attr = f'NSE_HOLIDAYS_{tomorrow_str[:4]}'
            holidays = getattr(self.cfg, attr, []) or []
            if tomorrow_str in holidays:
                is_expiry = True
                self.log.info(
                    f"  Expiry shifted to today (Wednesday) \u2014 Thursday "
                    f"{tomorrow_str} is an NSE holiday."
                )
        if not is_expiry:
            return
        self._expiry_applied = True
        self.cfg._expiry_applied = True  # expose to order_engine for stagnant timer

        bump_atr   = self.cfg.EXPIRY_ATR_BUMP
        reduce_pos = self.cfg.EXPIRY_POSITION_REDUCTION
        bump_score = self.cfg.EXPIRY_SCORE_BUMP

        # Skip position reduction on small accounts — combining fewer
        # slots + tighter trade cap + stronger signal bar leaves small
        # accounts unable to cover charges. Only reduce when budget is
        # large enough that fewer slots still provide meaningful rotations.
        budget = getattr(self, '_budget', 0) or 0
        if budget > 0 and budget < self.cfg.EXPIRY_POSITION_REDUCTION_MIN_BUDGET:
            reduce_pos = 0

        # Save originals so they can be restored if needed (Config is class-level)
        self._pre_expiry_atr = self.cfg.ATR_MULTIPLIER
        self._pre_expiry_max_pos = self.cfg.MAX_POSITIONS
        self._pre_expiry_min_score = self.cfg.V2_MIN_SCORE

        self.cfg.ATR_MULTIPLIER += bump_atr
        self.cfg.MAX_POSITIONS = max(1, self.cfg.MAX_POSITIONS - reduce_pos)
        self.cfg.V2_MIN_SCORE += bump_score

        # Override entry delay for expiry — longer observation to avoid
        # F&O settlement-driven opening volatility (first 15-30 min).
        if self.cfg.EXPIRY_ENTRY_DELAY_MINUTES > self.cfg.ENTRY_DELAY_MINUTES:
            self._pre_expiry_entry_delay = self.cfg.ENTRY_DELAY_MINUTES
            self.cfg.ENTRY_DELAY_MINUTES = self.cfg.EXPIRY_ENTRY_DELAY_MINUTES

        self.log.info(
            f"📅 Thursday F&O expiry: ATR multiplier → {self.cfg.ATR_MULTIPLIER:.1f}, "
            f"max positions → {self.cfg.MAX_POSITIONS}, "
            f"min score → {self.cfg.V2_MIN_SCORE:.1f}"
        )

    # ================================================================
    # NIFTY INDEX TREND FILTER
    # ================================================================

    def _build_nifty_context(self) -> str:
        """
        Fetches NIFTY 50 index quote and builds a concise trend context
        string for Claude prompts. Helps Claude align trade direction
        with the broader market.

        Also classifies market condition (BULLISH/BEARISH/NEUTRAL) and
        volatility regime (HIGH_VOLATILITY/NORMAL), storing them on
        self._market_condition for the trading report.

        Returns empty string if the fetch fails (non-blocking).
        """
        try:
            nifty_quote = self.zerodha.get_quotes(
                [{"symbol": "NIFTY 50", "exchange": "NSE"}]
            )
            q = nifty_quote.get("NSE:NIFTY 50", {})
            price = q.get("last_price", 0)
            ohlc  = q.get("ohlc", {})
            prev_close = ohlc.get("close", 0)
            day_open   = ohlc.get("open", 0)
            day_high   = ohlc.get("high", 0)
            day_low    = ohlc.get("low", 0)

            if not price or not prev_close:
                return ""

            change = price - prev_close
            change_pct = (change / prev_close) * 100

            # ── Market condition classification ───────────────────
            if change_pct > 0.5:
                bias = "BULLISH — favour BUY trades, be selective with shorts"
                condition = "BULLISH"
            elif change_pct < -0.5:
                bias = "BEARISH — favour SELL (short) trades, avoid buying into weakness"
                condition = "BEARISH"
            else:
                bias = "NEUTRAL — no strong directional bias, favour mean-reversion setups"
                condition = "NEUTRAL"

            # ── Volatility regime (from last 5 days of NIFTY) ─────
            volatility_label = "NORMAL"
            volatility_text  = ""
            try:
                to_date   = now_ist().date()
                from_date = to_date - datetime.timedelta(days=10)
                nifty_candles = self.zerodha.get_historical(
                    "NIFTY 50", "NSE", from_date, to_date, "day"
                )
                if nifty_candles and len(nifty_candles) >= 5:
                    recent = nifty_candles[-5:]
                    intraday_ranges = []
                    for c in recent:
                        if c["open"] > 0:
                            intraday_ranges.append(
                                (c["high"] - c["low"]) / c["open"] * 100
                            )
                    if intraday_ranges:
                        avg_range = sum(intraday_ranges) / len(intraday_ranges)
                        if avg_range > 1.5:
                            volatility_label = "HIGH_VOLATILITY"
                        volatility_text = (
                            f"\n  Volatility: {volatility_label} "
                            f"(avg 5-day intraday range: {avg_range:.2f}%)"
                        )
                        if volatility_label == "HIGH_VOLATILITY":
                            volatility_text += (
                                "\n  HIGH VOLATILITY: reduce position sizes by 20-30%, "
                                "widen stop-losses, prefer liquid large-caps"
                            )
            except Exception:
                pass  # volatility data is optional — don't fail

            self._market_condition = f"{condition}_{volatility_label}"

            # ── Fetch India VIX ───────────────────────────────────
            vix_text = ""
            try:
                vix_quote = self.zerodha.get_quotes(
                    [{"symbol": "INDIA VIX", "exchange": "NSE"}]
                )
                vix_q = vix_quote.get("NSE:INDIA VIX", {})
                vix_price = vix_q.get("last_price", 0)
                if vix_price > 0:
                    self._india_vix = vix_price
                    vix_ohlc = vix_q.get("ohlc", {})
                    vix_day_open = vix_ohlc.get("open", 0)
                    if vix_day_open > 0 and self._india_vix_open == 0:
                        self._india_vix_open = vix_day_open

                    vix_regime = "NORMAL"
                    if vix_price >= self.cfg.VIX_HIGH_THRESHOLD:
                        vix_regime = "HIGH"
                    elif vix_price <= self.cfg.VIX_LOW_THRESHOLD:
                        vix_regime = "LOW"

                    vix_text = f"\n  India VIX (Volatility Index): {vix_price:.2f} ({vix_regime})"
                    if vix_regime == "HIGH":
                        vix_text += (
                            f" — HIGH FEAR: reduce position sizes, widen SLs, "
                            f"only high-conviction setups"
                        )
                    elif vix_regime == "LOW":
                        vix_text += (
                            f" — LOW VIX: market is calm, breakout strategies "
                            f"favoured, tighter targets work"
                        )

                    # Detect intraday VIX spike
                    if self._india_vix_open > 0:
                        vix_change_pct = (
                            (vix_price - self._india_vix_open)
                            / self._india_vix_open * 100
                        )
                        if vix_change_pct >= self.cfg.VIX_SPIKE_PCT:
                            vix_text += (
                                f"\n  ⚠ VIX (Volatility Index) SPIKE: +{vix_change_pct:.1f}% intraday "
                                f"— CAUTION with new entries"
                            )
            except Exception:
                pass  # VIX data is optional

            # ── NIFTY ADX feed for choppy-morning pause (#192) ────
            # Compute 15-min NIFTY ADX(14) and feed it to the engine
            # so the choppy-morning gate has a rolling buffer of recent
            # trend-strength readings. Fail-soft: any error is swallowed
            # (gate fails open when buffer is empty).
            try:
                to_d   = now_ist().date()
                from_d = to_d - datetime.timedelta(days=5)
                n15 = self.zerodha.get_historical(
                    "NIFTY 50", "NSE", from_d, to_d, "15minute"
                )
                if n15 and len(n15) >= 30:
                    adx_res = _calc_adx(n15[-60:], period=14)
                    nifty_adx = adx_res.get("adx", 0) if isinstance(adx_res, dict) else 0
                    if nifty_adx and hasattr(self, "engine") and self.engine is not None:
                        self.engine.record_nifty_adx(nifty_adx)
            except Exception as _e:
                self.log.warning(f"NIFTY ADX feed failed: {_e}")  # gate fails-open

            # ── Strong-gap continuation flag for #194 ─────────────
            # If today's NIFTY gap is ≥ ±1.0% AND continues prior-day
            # direction, arm the ADX boost for the rest of the day.
            # Idempotent — repeated calls inside the same session are
            # no-ops after the first arming.
            try:
                if (
                    hasattr(self, "engine") and self.engine is not None
                    and day_open and prev_close and len(nifty_candles) >= 2
                ):
                    today_gap_pct = (day_open - prev_close) / prev_close * 100
                    prev_prev_close = nifty_candles[-2]["close"]
                    if prev_prev_close > 0:
                        prior_dir = prev_close - prev_prev_close
                        is_strong = abs(today_gap_pct) >= 1.0
                        is_continuation = (
                            (today_gap_pct > 0 and prior_dir > 0) or
                            (today_gap_pct < 0 and prior_dir < 0)
                        )
                        if is_strong and is_continuation:
                            gap_dir = "UP" if today_gap_pct > 0 else "DOWN"
                            self.engine.record_strong_gap_day(gap_dir)
            except Exception as _e:
                self.log.warning(f"strong-gap detection failed: {_e}")  # flag stays disarmed

            # ── FII/DII bias (if available) ───────────────────────
            fii_dii_text = ""
            if self._fii_dii_bias:
                fii_dii_text = f"\n  FII/DII (Foreign & Domestic Institutional Investors) bias (prev day): {self._fii_dii_bias}"

            # ── Pre-open intelligence (if available) ──────────────
            preopen_text = ""
            if self._preopen_data:
                sig_gaps = [
                    (sym, d) for sym, d in self._preopen_data.items()
                    if abs(d["gap_pct"]) >= self.cfg.PREOPEN_GAP_SIGNIFICANT_PCT
                ]
                if sig_gaps:
                    preopen_text = f"\n  Pre-open significant gaps:"
                    for sym, d in sig_gaps[:8]:
                        arrow = "↑" if d["gap_pct"] > 0 else "↓"
                        preopen_text += (
                            f"\n    {sym}: {arrow}{abs(d['gap_pct']):.1f}% gap"
                        )

            sector_advice = ""
            if condition == "BEARISH":
                sector_advice = (
                    "\n  BEARISH DAY: prefer defensive sectors (FMCG, Pharma, IT services), "
                    "avoid leveraged/cyclical stocks"
                )
            elif condition == "BULLISH":
                sector_advice = (
                    "\n  BULLISH DAY: favour momentum sectors (Banks, Auto, Metals), "
                    "look for breakout setups"
                )

            return (
                f"\nMARKET TREND (NIFTY 50 INDEX):\n"
                f"  NIFTY 50: Rs.{price:,.2f}  Change: {change_pct:+.2f}%  "
                f"Open: Rs.{day_open:,.2f}  High: Rs.{day_high:,.2f}  Low: Rs.{day_low:,.2f}  "
                f"PrevClose: Rs.{prev_close:,.2f}\n"
                f"  Market bias: {bias}"
                f"{sector_advice}"
                f"{volatility_text}"
                f"{vix_text}"
                f"{fii_dii_text}"
                f"{preopen_text}\n"
            )
        except Exception:
            return ""

    # ================================================================
    # INDIA VIX — VOLATILITY REGIME ADJUSTMENTS
    # ================================================================

    def _apply_vix_adjustments(self):
        """
        Applies config adjustments based on India VIX level.
        Called once after the first NIFTY context fetch (which also
        fetches VIX). Prevents double-application via flag.

        High VIX (≥20): reduce MAX_POSITIONS, raise V2_MIN_SCORE.
        Low VIX (≤12): no config changes — just informational.
        """
        if self._vix_adjustments_applied:
            return
        if self._india_vix <= 0:
            return
        self._vix_adjustments_applied = True

        if self._india_vix >= self.cfg.VIX_HIGH_THRESHOLD:
            reduce_pos = self.cfg.VIX_HIGH_POSITION_REDUCTION
            bump_score = self.cfg.VIX_HIGH_SCORE_BUMP

            self._pre_vix_max_pos = self.cfg.MAX_POSITIONS
            self._pre_vix_min_score = self.cfg.V2_MIN_SCORE

            self.cfg.MAX_POSITIONS = max(1, self.cfg.MAX_POSITIONS - reduce_pos)
            self.cfg.V2_MIN_SCORE += bump_score

            self.log.info(
                f"📈 India VIX (Volatility Index) {self._india_vix:.1f} ≥ {self.cfg.VIX_HIGH_THRESHOLD} "
                f"(HIGH VOLATILITY): max positions → {self.cfg.MAX_POSITIONS}, "
                f"min score → {self.cfg.V2_MIN_SCORE:.1f}"
            )
        elif self._india_vix <= self.cfg.VIX_LOW_THRESHOLD:
            self.log.info(
                f"📉 India VIX (Volatility Index) {self._india_vix:.1f} ≤ {self.cfg.VIX_LOW_THRESHOLD} "
                f"(LOW VOLATILITY): breakout-friendly market, tighter targets"
            )
        else:
            self.log.info(
                f"📊 India VIX (Volatility Index) {self._india_vix:.1f} (NORMAL range)"
            )

    def _check_vix_spike(self) -> bool:
        """
        Checks if India VIX has spiked intraday. Called during NIFTY
        rechecks in the monitor loop. Returns True if spike detected.
        """
        if self._india_vix_open <= 0 or self._india_vix <= 0:
            return False
        vix_change_pct = (
            (self._india_vix - self._india_vix_open)
            / self._india_vix_open * 100
        )
        return vix_change_pct >= self.cfg.VIX_SPIKE_PCT

    # ================================================================
    # PRE-OPEN AUCTION DATA
    # ================================================================

    def _fetch_preopen_data(self):
        """
        Fetches quotes for the stock universe and computes gap analysis
        from the pre-open auction session. Called between 9:08 and 9:15
        when pre-open equilibrium prices are available.

        Stores gap direction, magnitude, and volume per stock in
        self._preopen_data. This data enriches the scan context.
        """
        if not self.cfg.PREOPEN_ENABLED:
            return

        try:
            universe = self.scanner.get_universe()
            stocks = [{"symbol": s, "exchange": "NSE"} for s in universe]
            quotes = self.zerodha.get_quotes_safe(stocks)
            if not quotes:
                return

            significant = 0
            for symbol in universe:
                key = f"NSE:{symbol}"
                q = quotes.get(key, {})
                if not q:
                    continue

                ohlc = q.get("ohlc", {})
                prev_close = ohlc.get("close", 0)
                day_open = ohlc.get("open", 0)
                last_price = q.get("last_price", 0)
                volume = q.get("volume", 0)

                # Use day_open if available (post 9:08), else last_price
                ref_price = day_open if day_open > 0 else last_price
                if ref_price <= 0 or prev_close <= 0:
                    continue

                gap_pct = (ref_price - prev_close) / prev_close * 100
                direction = "BUY" if gap_pct > 0 else "SELL" if gap_pct < 0 else "FLAT"

                self._preopen_data[symbol] = {
                    "gap_pct": round(gap_pct, 2),
                    "volume": volume,
                    "direction": direction,
                    "open_price": ref_price,
                    "prev_close": prev_close,
                }

                if abs(gap_pct) >= self.cfg.PREOPEN_GAP_SIGNIFICANT_PCT:
                    significant += 1

            self.log.info(
                f"Pre-open data: {len(self._preopen_data)} stocks analysed, "
                f"{significant} with significant gaps (≥{self.cfg.PREOPEN_GAP_SIGNIFICANT_PCT}%)"
            )

            # Log significant gaps
            sig_gaps = sorted(
                [(s, d) for s, d in self._preopen_data.items()
                 if abs(d["gap_pct"]) >= self.cfg.PREOPEN_GAP_SIGNIFICANT_PCT],
                key=lambda x: abs(x[1]["gap_pct"]),
                reverse=True,
            )
            for sym, d in sig_gaps[:10]:
                arrow = "↑" if d["gap_pct"] > 0 else "↓"
                self.log.info(
                    f"  {sym}: {arrow}{abs(d['gap_pct']):.1f}% gap from prev close "
                    f"(Rs.{d['prev_close']:.2f} → Rs.{d['open_price']:.2f})"
                )

        except Exception as e:
            self.log.warning(f"Pre-open data fetch failed: {e}")

    # ================================================================
    # FII/DII FLOW BIAS
    # ================================================================

    def _fetch_fii_dii_data(self):
        """
        Fetches previous day's FII/DII net buy/sell data from NSE.
        Sets self._fii_dii_bias to BULLISH / BEARISH / NEUTRAL.

        NSE publishes this data daily. We use it as a morning bias
        signal — not a hard filter, just context for Claude and a
        slight preference for institutional-aligned trades.

        Falls back gracefully if NSE blocks the request.
        """
        if not self.cfg.FII_DII_ENABLED:
            return

        import urllib.request
        import ssl

        try:
            url = "https://www.nseindia.com/api/fiidiiTradeReact"
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "Mozilla/5.0")
            req.add_header("Accept", "application/json")
            req.add_header("Referer", "https://www.nseindia.com/")

            # NSE requires TLS; skip cert verification for reliability
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                import json as _json
                data = _json.loads(resp.read().decode("utf-8"))

            # Parse FII/DII net values
            # NSE response is a list of dicts with category, buyValue, sellValue
            # Values are strings and already in crores (Rs.Cr)
            fii_net = 0.0
            dii_net = 0.0
            for entry in data:
                category = entry.get("category", "").upper()
                buy_val = float(entry.get("buyValue", 0) or 0)
                sell_val = float(entry.get("sellValue", 0) or 0)
                net = buy_val - sell_val

                if "FII" in category or "FPI" in category:
                    fii_net += net
                elif "DII" in category:
                    dii_net += net

            # Classify bias
            if fii_net > 0 and dii_net > 0:
                self._fii_dii_bias = "BULLISH (FII + DII both net buyers)"
            elif fii_net < 0 and dii_net < 0:
                self._fii_dii_bias = "BEARISH (FII + DII both net sellers)"
            elif fii_net < 0 and dii_net > 0:
                self._fii_dii_bias = "MIXED (FII selling, DII absorbing)"
            elif fii_net > 0 and dii_net < 0:
                self._fii_dii_bias = "MIXED (FII buying, DII selling)"
            else:
                self._fii_dii_bias = "NEUTRAL"

            # Values from NSE are already in crores
            fii_label = f"+Rs.{fii_net:,.0f}Cr" if fii_net >= 0 else f"-Rs.{abs(fii_net):,.0f}Cr"
            dii_label = f"+Rs.{dii_net:,.0f}Cr" if dii_net >= 0 else f"-Rs.{abs(dii_net):,.0f}Cr"
            self.log.info(
                f"FII/DII (Foreign & Domestic Institutional Investors) prev day: "
                f"FII {fii_label}, DII {dii_label} → {self._fii_dii_bias}"
            )

        except Exception as e:
            self.log.warning(f"FII/DII data fetch failed: {e} — skipping (no impact on trading)")

    # ================================================================
    # ACCOUNT FUNDS & BUDGET
    # ================================================================

    def _fetch_and_set_budget(self):
        """
        Fetches available cash from Zerodha and sets the trading budget.

        Budget = min(available_funds, MAX_BUDGET_INR).
        So even if account has Rs.50K, the bot only uses up to Rs.10K.

        Live mode:
          - Fetches real balance, checks against MIN_BALANCE_TO_TRADE.
          - If below minimum, stops trading.

        Dry-run mode:
          - Tries to fetch real balance for display.
          - If fetch fails, uses MAX_BUDGET_INR as fallback.
          - Min balance check is skipped (only a warning).
        """
        self.log.section("ACCOUNT FUNDS")

        max_budget = self.cfg.MAX_BUDGET_INR

        try:
            self._available_funds = self.zerodha.get_available_funds()
            self.log.success(
                f"Available funds in Zerodha: Rs.{self._available_funds:,.2f}"
            )
        except Exception as e:
            self.log.warning(f"Could not fetch Zerodha funds: {e}")
            if self.cfg.DRY_RUN:
                self._available_funds = float(max_budget)
                self.log.info(
                    f"DRY RUN — using max budget as fallback: Rs.{max_budget:,}"
                )
            else:
                self.log.error(
                    "Cannot trade without knowing account balance. Aborting."
                )
                self._budget = 0
                return

        min_balance = self.cfg.MIN_BALANCE_TO_TRADE

        if self._available_funds < min_balance:
            if self.cfg.DRY_RUN:
                self.log.warning(
                    f"Funds Rs.{self._available_funds:,.2f} below minimum "
                    f"Rs.{min_balance:,} — ignored in DRY RUN mode"
                )
            else:
                self.log.error(
                    f"Funds Rs.{self._available_funds:,.2f} below minimum "
                    f"Rs.{min_balance:,}. Add funds to Zerodha and retry. "
                    f"(change MIN_BALANCE_TO_TRADE in config.py to lower the threshold)"
                )
                self._budget = 0
                return

        if self.cfg.DRY_RUN:
            # Dry run always uses MAX_BUDGET_INR regardless of account balance
            self._budget = float(max_budget)
            self.log.info(f"DRY RUN — using max budget: Rs.{max_budget:,}")
        else:
            # Live mode: cap at MAX_BUDGET_INR
            self._budget = min(self._available_funds, float(max_budget))

            if self._available_funds > max_budget:
                self.log.info(
                    f"Using maximum budget: Rs.{max_budget:,}"
                )
            else:
                self.log.info(
                    f"Using Rs.{self._budget:,.2f} to trade"
                )

        # Set budget on engine and scanner so they use the live value
        self.engine.set_budget(self._budget)
        self.scanner.set_budget(self._budget)

        # Roadmap #171: seed engine's live-funds reading so the budget
        # check respects margin already blocked by user's manual MIS
        # positions from the start (don't wait for the first mid-day
        # rescan). No-op in dry-run.
        if not self.cfg.DRY_RUN:
            self.engine.refresh_budget()

    # ================================================================
    # TIMING HELPERS
    # ================================================================

    def _is_trading_day(self, date: datetime.date) -> bool:
        """
        Returns True if the given date is a valid NSE trading day.
        Checks:
          1. Not a Saturday or Sunday (weekday 5, 6)
          2. Not in the NSE_HOLIDAYS list from config
        """
        # Weekend check
        if date.weekday() >= 5:
            return False

        # Holiday check against the configured calendar
        date_str = date.strftime("%Y-%m-%d")
        if date_str in self.cfg.NSE_HOLIDAYS_2026:
            return False

        return True

    def _next_trading_day(self, from_date: datetime.date) -> datetime.date:
        """
        Finds the next valid trading day starting from from_date.
        If from_date itself is a trading day, returns from_date.
        Otherwise advances day-by-day until a trading day is found.
        """
        date = from_date
        # Safety limit: don't loop more than 15 days (covers worst case
        # of long weekends + consecutive holidays)
        for _ in range(15):
            if self._is_trading_day(date):
                return date
            date += datetime.timedelta(days=1)

        # Fallback — should never reach here
        self.log.warning(
            f"Could not find a trading day within 15 days of {from_date}. "
            f"Check NSE_HOLIDAYS_2026 in config.py."
        )
        return date

    def _holiday_name(self, date: datetime.date) -> str:
        """
        Returns the holiday name for a given date, if it's in the
        holiday list. Extracts from the comment in config.
        Returns '' if not a listed holiday.
        """
        # Holiday names mapped from the config comments for display
        names = {
            "2026-01-15": "Municipal Corporation Elections",
            "2026-01-26": "Republic Day",
            "2026-03-03": "Holi",
            "2026-03-26": "Shri Ram Navami",
            "2026-03-31": "Shri Mahavir Jayanti",
            "2026-04-03": "Good Friday",
            "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
            "2026-05-01": "Maharashtra Day",
            "2026-05-28": "Bakri Eid",
            "2026-06-26": "Moharram",
            "2026-09-14": "Ganesh Chaturthi",
            "2026-10-02": "Mahatma Gandhi Jayanti",
            "2026-10-20": "Dussehra",
            "2026-11-10": "Diwali-Balipratipada",
            "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
            "2026-12-25": "Christmas",
        }
        return names.get(date.strftime("%Y-%m-%d"), "")

    def _wait_for_trading_day(self):
        """
        Checks if today is a trading day. If not (weekend or holiday),
        determines the reason and delegates to _wait_for_next_market_open.
        """
        today = now_ist().date()

        if self._is_trading_day(today):
            self.log.success(f"Today ({today.strftime('%A, %B %d')}) is a trading day")
            return

        # Determine WHY today is not a trading day
        if today.weekday() == 5:
            reason = "Today is Saturday — market is closed"
        elif today.weekday() == 6:
            reason = "Today is Sunday — market is closed"
        else:
            holiday = self._holiday_name(today)
            name = f" ({holiday})" if holiday else ""
            reason = f"Today is a market holiday{name} — market is closed"

        self._wait_for_next_market_open(reason)

    def _wait_for_next_market_open(self, reason: str = ""):
        """
        Common wait: finds the next trading day, shows why we're
        waiting, and counts down to pre-market time.

        Used by ALL "market not open" scenarios:
          - Weekend / holiday (_wait_for_trading_day)
          - Square-off time already passed (too late)
          - Not enough time before close (cutoff)

        After this returns, callers should re-login to Zerodha
        (token expires at midnight) and refresh budget.
        """
        today = now_ist().date()
        next_day = self._next_trading_day(today + datetime.timedelta(days=1))
        next_open = datetime.datetime(
            next_day.year, next_day.month, next_day.day,
            self.cfg.MARKET_OPEN_HOUR, self.cfg.MARKET_OPEN_MINUTE, 0,
        )
        next_pre_market = next_open - datetime.timedelta(
            minutes=self.cfg.PRE_MARKET_MINUTES_BEFORE
        )

        self.log.section("WAITING FOR NEXT MARKET OPEN")
        if reason:
            self.log.warning(reason)
        self.log.info(f"Next trading day: {next_day.strftime('%A, %B %d, %Y')}")
        self.log.info(f"Pre-market scan at: {next_pre_market.strftime('%I:%M %p')}")
        self.log.info(f"Market opens at: {next_open.strftime('%I:%M %p')}")
        self.log.info("Press Ctrl+C to abort.\n")
        self._countdown_to(next_pre_market, "Next market open in")

    def _wait_for_pre_market(self):
        """
        Sleeps until PRE_MARKET_MINUTES_BEFORE the market opens.
        If already past pre-market time, returns immediately.
        """
        pre_market = self._get_pre_market_time()

        if now_ist() >= pre_market:
            self.log.info("Pre-market time already reached — starting scan")
            return

        self.log.section("WAITING FOR PRE-MARKET")
        self.log.info(f"Pre-market scan at: {pre_market.strftime('%I:%M %p')}")
        self.log.info(f"Market opens at: {self.cfg.MARKET_OPEN_HOUR}:{self.cfg.MARKET_OPEN_MINUTE:02d}")
        self.log.info("Press Ctrl+C to abort.\n")
        self._countdown_to(pre_market, "Pre-market in")

    def _wait_for_market_open(self):
        """
        Sleeps until market open time (9:15 AM IST by default).
        If already past open time, returns immediately.
        """
        market_open = now_ist().replace(
            hour=self.cfg.MARKET_OPEN_HOUR,
            minute=self.cfg.MARKET_OPEN_MINUTE,
            second=0, microsecond=0,
        )

        if now_ist() >= market_open:
            self.log.info("Market already open — entering positions now")
            return

        self.log.section("WAITING FOR MARKET OPEN")
        self.log.info(f"Market opens at: {market_open.strftime('%I:%M %p')}")
        self.log.info("Press Ctrl+C to abort.\n")
        self._countdown_to(market_open, "Market open in")

    def _countdown_to(self, target: datetime.datetime, label: str):
        """
        Common countdown loop. Shows a live timer until target time.
        Used by _wait_for_pre_market, _wait_for_market_open, and
        _wait_for_next_market_open.
        """
        while now_ist() < target and not self._shutdown_requested:
            remaining = target - now_ist()
            total_secs = int(remaining.total_seconds())
            days, remainder = divmod(total_secs, 86400)
            hrs, remainder  = divmod(remainder, 3600)
            mins, secs      = divmod(remainder, 60)

            if days > 0:
                countdown = f"{days}d {hrs:02d}:{mins:02d}:{secs:02d}"
            else:
                countdown = f"{hrs:02d}:{mins:02d}:{secs:02d}"

            print(f"\r  \u23f3 {label}: {countdown}  ", end="", flush=True)
            time.sleep(1)

        print()  # newline after countdown

    def _get_pre_market_time(self) -> datetime.datetime:
        """Returns today's pre-market scan start time."""
        market_open = now_ist().replace(
            hour=self.cfg.MARKET_OPEN_HOUR,
            minute=self.cfg.MARKET_OPEN_MINUTE,
            second=0, microsecond=0,
        )
        return market_open - datetime.timedelta(minutes=self.cfg.PRE_MARKET_MINUTES_BEFORE)

    def _is_square_off_time(self, now: datetime.datetime) -> bool:
        """Returns True if current time is at or past square-off time."""
        square_off = now.replace(
            hour=self.cfg.SQUARE_OFF_HOUR,
            minute=self.cfg.SQUARE_OFF_MINUTE,
            second=0, microsecond=0,
        )
        return now >= square_off

    # ================================================================
    # DISPLAY HELPERS
    # ================================================================

    def _print_banner(self):
        """Shows the active configuration at startup."""
        plan = self.cfg.claude()
        mode = "DRY RUN (no real orders)" if self.cfg.DRY_RUN else "LIVE TRADING"
        print(f"\n{'='*58}")
        print("  AI PORTFOLIO MANAGER — PHASE 2 INTRADAY BOT")
        print(f"{'='*58}")
        print(f"  Mode           : {mode}")
        print(f"  Max budget     : \u20b9{self.cfg.MAX_BUDGET_INR:,}")
        print(f"  Min balance    : Rs.{self.cfg.MIN_BALANCE_TO_TRADE:,}")
        print(f"  Max positions  : {self.cfg.MAX_POSITIONS}")
        print(f"  Universe       : {self.cfg.SCAN_UNIVERSE}")
        print(f"  Claude model   : {plan['model']}")
        print(f"  Price poll     : every {self.cfg.PRICE_POLL_SECONDS}s")
        print(f"  Claude review  : every {self.cfg.POSITION_REVIEW_MINUTES}min")
        print(f"  Stop-loss      : {self.cfg.DEFAULT_STOP_LOSS_PCT}%")
        print(f"  Target         : {self.cfg.DEFAULT_TARGET_PCT}%")
        print(f"  Trailing SL    : after {self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R, lock {self.cfg.TRAIL_STEP_PCT}% profit")
        print(f"  Slippage (sim) : {self.cfg.SLIPPAGE_PCT}%")
        print(f"  Circuit breaker: {self.cfg.MAX_LOSS_PER_DAY_PCT}% of budget")
        print(f"  Market open    : {self.cfg.MARKET_OPEN_HOUR}:{self.cfg.MARKET_OPEN_MINUTE:02d}")
        print(f"  Square off     : {self.cfg.SQUARE_OFF_HOUR}:{self.cfg.SQUARE_OFF_MINUTE:02d}")
        print(f"{'='*58}\n")

    def _compute_run_number(self) -> int:
        """
        Returns this bot run's session number for today (1, 2, 3, ...).
        Reads `sessions` from today's existing trading_data_DD.json. If the
        file does not exist yet, this is run 1; otherwise it is sessions+1
        (matches ReportWriter.save_trading_day's merge logic).
        """
        try:
            today = now_ist().date()
            path = self.report.trading_data_path(today)
            if not os.path.exists(path):
                return 1
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            return int(existing.get("sessions", 1)) + 1
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return 1

    def _print_status(self, quotes: dict):
        """Compact 2-line status during monitor loop — session P&L + net daily totals."""
        open_pos   = self.engine.open_positions()
        closed_pos = self.engine.closed_positions()
        try:
            unrealised = self.engine.unrealised_pnl(quotes)
        except ValueError:
            unrealised = 0.0  # partial quotes — display 0 rather than crash status line
        realised   = self.engine.day_pnl()
        now        = now_ist().strftime("%H:%M:%S")

        # Color the P&L values
        u_color = "\033[92m" if unrealised >= 0 else "\033[91m"
        r_color = "\033[92m" if realised >= 0 else "\033[91m"

        session_line = (
            f"  [{now}]  "
            f"Open: {len(open_pos)}  "
            f"Closed: {len(closed_pos)}  "
            f"Unrealised: {u_color}Rs.{unrealised:+,.2f}\033[0m  "
            f"Realised: {r_color}Rs.{realised:+,.2f}\033[0m"
        )

        # Cumulative daily totals (previous runs from DB + current run)
        if self._prev_runs is None:
            self._prev_runs = self.tracker.get_today_previous_runs()
        if self._run_number is None:
            self._run_number = self._compute_run_number()

        prev = self._prev_runs
        total_closed = prev["trade_count"] + len(closed_pos)
        total_realised = prev["realised_pnl"] + realised
        total_combined = total_realised + unrealised
        t_color = "\033[92m" if total_combined >= 0 else "\033[91m"
        tr_color = "\033[92m" if total_realised >= 0 else "\033[91m"

        run_tag = f" (Run {self._run_number})" if self._run_number and self._run_number > 1 else ""
        net_line = (
            f"  "
            f"Net today{run_tag}: {total_closed} trades  "
            f"Realised: {tr_color}Rs.{total_realised:+,.2f}\033[0m  "
            f"Net: {t_color}Rs.{total_combined:+,.2f}\033[0m"
        )

        # Use ANSI cursor-up to overwrite both lines on each poll
        if self._status_lines_printed:
            # Move cursor up 1 line (back to session_line), then overwrite both
            print(f"\033[1A\r\033[2K{session_line}", flush=True)
            print(f"\r\033[2K{net_line}", end="", flush=True)
        else:
            # First print — no cursor-up needed
            print(f"\r\033[2K{session_line}", flush=True)
            print(f"\r\033[2K{net_line}", end="", flush=True)
            self._status_lines_printed = True

    def _clear_status_line(self):
        """Call before any log/print that interrupts the 2-line status display."""
        if self._status_lines_printed:
            print("\n")  # finish the partial line + blank separator
            self._status_lines_printed = False

    def _print_pnl_summary(self, pnl: dict):
        """Prints the final P&L breakdown to terminal."""
        charges = pnl["charges"]

        color = "\033[92m" if pnl["is_profitable"] else "\033[91m"
        reset = "\033[0m"

        print(f"\n{'='*58}")
        print("  FINAL P&L SUMMARY")
        print(f"{'='*58}")
        print(f"  Total trades     : {len(self.engine.closed_positions())}")
        print(f"  Gross P&L        : Rs.{pnl['gross_pnl']:+,.2f}")
        print(f"{'─'*58}")
        print(f"  CHARGES & TAXES:")
        print(f"    Brokerage      : Rs.{charges['brokerage']:,.2f}")
        print(f"    STT            : Rs.{charges['stt']:,.2f}")
        print(f"    Exchange txn   : Rs.{charges['exchange_txn']:,.2f}")
        print(f"    GST            : Rs.{charges['gst']:,.2f}")
        print(f"    SEBI charges   : Rs.{charges['sebi_charges']:,.4f}")
        print(f"    Stamp duty     : Rs.{charges['stamp_duty']:,.2f}")
        print(f"    ────────────────────────────")
        print(f"    Total tax+chrg : Rs.{charges['total_tax_and_charges']:,.2f}")
        print(f"{'─'*58}")
        print(f"  CLAUDE API COST:")
        print(f"    Claude API     : Rs.{charges['claude_api_cost']:,.2f} ({self.engine.claude_calls} calls)")
        print(f"{'─'*58}")
        print(f"  Total all costs  : Rs.{charges['total_costs']:,.2f}")
        print(f"{'='*58}")
        print(f"  {color}NET PROFIT       : Rs.{pnl['net_profit']:+,.2f}{reset}")
        print(f"{'='*58}")
        if self._budget > 0:
            returns_pct = pnl["net_profit"] / self._budget * 100
            color2 = "\033[92m" if returns_pct >= 0 else "\033[91m"
            print(f"  Day returns      : {color2}{returns_pct:+.2f}%{reset} on Rs.{self._budget:,.0f} budget")
        print(f"  FYI: Zerodha Kite Connect: Rs.{charges['zerodha_monthly_fyi']:,.0f}/month (not deducted above)")
        print()

    # ================================================================
    # GRACEFUL SHUTDOWN (Ctrl+C)
    # ================================================================

    def _setup_signal_handler(self):
        """
        Registers Ctrl+C handler for graceful shutdown.
        On first Ctrl+C: sets shutdown flag, squares off positions.
        On second Ctrl+C: hard exit (in case square-off hangs).
        """
        def handler(sig, frame):
            if self._shutdown_requested:
                # Second Ctrl+C — force exit
                self.log.error("Force exit — some positions may still be open!")
                sys.exit(1)

            # Note: no leading "\n" — the formatter already prefixes
            # the timestamp/level, so a leading newline produced an
            # empty WARNING record followed by the real message on the
            # next line, which broke log parsers that key on the
            # leading timestamp.
            self.log.warning("Shutdown requested — will square off and exit...")
            self._shutdown_requested = True

        signal.signal(signal.SIGINT, handler)

    def _emergency_shutdown(self):
        """Square off all positions during unexpected shutdown."""
        if self.engine.open_positions():
            self.log.section("EMERGENCY SHUTDOWN")
            self._square_off()
            self._generate_report()

