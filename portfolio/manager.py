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
#      - Every CLAUDE_REVIEW_MINUTES: Claude reviews positions (paid)
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

from config                   import Config
from core.logger              import Logger
from core.zerodha_client      import ZerodhaClient
from core.claude_client       import ClaudeClient
from services.stock_scanner   import StockScanner
from services.order_engine    import OrderEngine
from services.report_writer   import ReportWriter


class PortfolioManager:
    """
    Phase 2 intraday trading bot for the managed budget.

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

        # ── State ─────────────────────────────────────────────────
        self._shutdown_requested = False   # set by Ctrl+C handler
        self._trade_plans: list[dict] = [] # trades Claude picked pre-market
        self._circuit_broken = False       # true if max daily loss hit
        self._available_funds: float = 0.0 # fetched from Zerodha at startup
        self._budget: float = 0.0          # actual trading budget for the day
        self._scan_failed = False          # true if quote fetch failed

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
        missing = self.cfg.validate()
        if missing:
            self.log.section("CONFIGURATION ERROR")
            for key in missing:
                self.log.error(f"Missing in .env file: {key}=your_value_here")
            self.log.info("Create or edit the .env file in this folder and re-run.")
            return

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
            now = datetime.datetime.now()
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
                    f"skipping today"
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

        # ── Step 7: Enter positions ───────────────────────────────
        self._enter_positions()

        # ── Step 8: Monitor loop ──────────────────────────────────
        self._run_monitor_loop()

        # ── Step 9: Square off (if not already done) ──────────────
        if self.engine.open_positions() and not self._shutdown_requested:
            self._square_off()

        # ── Step 10: Generate report ──────────────────────────────
        self._generate_report()

    # ================================================================
    # PRE-MARKET SCAN
    # ================================================================

    def _run_pre_market_scan(self):
        """
        Fetches live quotes for the stock universe and asks Claude
        to pick the best intraday trade candidates.
        """
        now = datetime.datetime.now()
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
        self.log.info(f"Budget: ₹{self._budget:,.2f}")
        self.log.info(f"Mode: {'DRY RUN' if self.cfg.DRY_RUN else 'LIVE TRADING'}")

        universe = self.scanner.get_universe()
        self.log.info(f"Scanning {len(universe)} stocks...")

        # Fetch live quotes for the universe
        stocks = [{"symbol": s, "exchange": "NSE"} for s in universe]
        try:
            quotes = self.zerodha.get_quotes(stocks)
        except Exception as e:
            self.log.error(f"Failed to fetch quotes: {e}")

            # If it's an auth error, the token may be stale — force re-login
            if "api_key" in str(e).lower() or "access_token" in str(e).lower():
                self.log.info("Token appears invalid — forcing re-login...")
                self.zerodha.force_relogin()
                try:
                    quotes = self.zerodha.get_quotes(stocks)
                except Exception as e2:
                    self.log.error(f"Retry also failed: {e2}")
                    self.log.error("Could not fetch market data. Aborting scan.")
                    self._scan_failed = True
                    return
            else:
                self.log.error("Could not fetch market data. Aborting scan.")
                self._scan_failed = True
                return

        if not quotes:
            self.log.warning("No quotes returned — market may not be open yet")
            # In pre-market, previous close data is still available
            # Proceed anyway — Claude can work with available data

        # Ask Claude to pick trades
        self.engine.claude_calls += 1
        self._trade_plans = self.scanner.scan(quotes)

        if self._trade_plans:
            self.log.section("TRADE PLAN")
            for i, t in enumerate(self._trade_plans, 1):
                self.log.info(
                    f"  Trade {i}: {t['side']} {t['qty']}x {t['symbol']} "
                    f"@ ₹{t['entry_price']:.2f} | "
                    f"SL: ₹{t['stop_loss']:.2f} | "
                    f"Target: ₹{t['target_price']:.2f}"
                )
                self.log.info(f"           {t.get('rationale', '')}")

    # ================================================================
    # ENTER POSITIONS
    # ================================================================

    def _enter_positions(self):
        """
        Enters all trade plans at market open.
        Each trade goes through OrderEngine which checks budget and
        position limits before placing/logging the order.
        """
        self.log.section("ENTERING POSITIONS")

        for trade in self._trade_plans:
            if self._shutdown_requested:
                break
            self.engine.enter_trade(trade)
            time.sleep(0.5)  # small gap between order placements

        open_count = len(self.engine.open_positions())
        self.log.success(f"Entered {open_count} positions")

    # ================================================================
    # MONITOR LOOP
    # ================================================================

    def _run_monitor_loop(self):
        """
        Main trading loop that runs from market open until square-off.

        Two independent timers:
          1. Price polling (every PRICE_POLL_SECONDS) — checks SL/target
             hits using rule-based logic. No Claude API calls.
          2. Claude review (every CLAUDE_REVIEW_MINUTES) — asks Claude
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
            f"Claude review: every {self.cfg.CLAUDE_REVIEW_MINUTES}min"
        )

        poll_interval    = self.cfg.PRICE_POLL_SECONDS
        review_interval  = self.cfg.CLAUDE_REVIEW_MINUTES * 60  # convert to seconds
        last_review_time = time.time()

        while not self._shutdown_requested:
            now = datetime.datetime.now()

            # ── Check if it's square-off time ─────────────────────
            if self._is_square_off_time(now):
                self.log.info("Square-off time reached")
                break

            # ── Check if all positions are already closed ─────────
            if not self.engine.open_positions():
                self.log.info("All positions closed — nothing left to monitor")
                break

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
                self.log.info(f"{closed} position(s) auto-closed")

            # ── Circuit breaker check ─────────────────────────────
            if self.engine.check_circuit_breaker():
                self._circuit_broken = True
                self._square_off()
                break

            # ── Periodic Claude review (paid) ─────────────────────
            elapsed = time.time() - last_review_time
            if elapsed >= review_interval and self.engine.open_positions():
                self._run_claude_review(quotes)
                last_review_time = time.time()

            # ── Print status line ─────────────────────────────────
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

        actions = self.scanner.review_positions(
            open_positions  = self.engine.open_positions(),
            quotes          = quotes,
            day_pnl         = self.engine.day_pnl(),
            budget_remaining = self.engine.budget_remaining(),
        )

        if actions:
            self.engine.apply_review_actions(actions, quotes)

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
        """
        self.log.section("END OF DAY REPORT")

        pnl_summary = self.engine.net_profit()
        self.report.save_trading_day(
            positions  = self.engine.positions,
            trade_log  = self.engine.trade_log,
            pnl        = pnl_summary,
            dry_run    = self.cfg.DRY_RUN,
            budget     = self._budget,
        )

        self._print_pnl_summary(pnl_summary)

    # ================================================================
    # ACCOUNT SNAPSHOT
    # ================================================================

    def _print_account_snapshot(self):
        """
        Prints a quick overview of the Zerodha account right after login.
        Shows available balance, portfolio size, invested vs current value.
        Runs even on holidays so you can see your account status anytime.
        """
        self.log.section("ACCOUNT SNAPSHOT")

        # Available funds
        try:
            self._available_funds = self.zerodha.get_available_funds()
            self.log.info(f"Available balance: ₹{self._available_funds:,.2f}")
        except Exception:
            self.log.warning("Could not fetch available balance")

        # Portfolio holdings
        try:
            holdings = self.zerodha.get_holdings()
            if holdings:
                invested = sum(h["invested_value"] for h in holdings)
                current  = sum(h["current_value"]  for h in holdings)
                pnl      = current - invested
                pnl_pct  = (pnl / invested * 100) if invested > 0 else 0
                pnl_color = "\033[92m" if pnl >= 0 else "\033[91m"
                reset     = "\033[0m"

                self.log.info(f"Stocks in portfolio: {len(holdings)}")
                self.log.info(f"Invested value     : ₹{invested:,.2f}")
                self.log.info(f"Current value      : ₹{current:,.2f}")
                self.log.info(
                    f"Portfolio P&L      : {pnl_color}₹{pnl:+,.2f} "
                    f"({pnl_pct:+.2f}%){reset}"
                )
            else:
                self.log.info("No stocks in portfolio")
        except Exception:
            self.log.warning("Could not fetch portfolio holdings")

    # ================================================================
    # ACCOUNT FUNDS & BUDGET
    # ================================================================

    def _fetch_and_set_budget(self):
        """
        Fetches available cash from Zerodha and sets the trading budget.

        Budget = min(available_funds, MAX_BUDGET_INR).
        So even if account has ₹50K, the bot only uses up to ₹10K.

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
                f"Available funds in Zerodha: ₹{self._available_funds:,.2f}"
            )
        except Exception as e:
            self.log.warning(f"Could not fetch Zerodha funds: {e}")
            if self.cfg.DRY_RUN:
                self._available_funds = float(max_budget)
                self.log.info(
                    f"DRY RUN — using max budget as fallback: ₹{max_budget:,}"
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
                    f"Funds ₹{self._available_funds:,.2f} below minimum "
                    f"₹{min_balance:,} — ignored in DRY RUN mode"
                )
            else:
                self.log.error(
                    f"Funds ₹{self._available_funds:,.2f} below minimum "
                    f"₹{min_balance:,}. Add funds to Zerodha and retry."
                )
                self._budget = 0
                return

        if self.cfg.DRY_RUN:
            # Dry run always uses MAX_BUDGET_INR regardless of account balance
            self._budget = float(max_budget)
            self.log.info(f"DRY RUN — using max budget: ₹{max_budget:,}")
        else:
            # Live mode: cap at MAX_BUDGET_INR
            self._budget = min(self._available_funds, float(max_budget))

            if self._available_funds > max_budget:
                self.log.info(
                    f"Using maximum budget: ₹{max_budget:,}"
                )
            else:
                self.log.info(
                    f"Using ₹{self._budget:,.2f} to trade"
                )

        # Set budget on engine and scanner so they use the live value
        self.engine.set_budget(self._budget)
        self.scanner.set_budget(self._budget)

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
        today = datetime.date.today()

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
        today = datetime.date.today()
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

        if datetime.datetime.now() >= pre_market:
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
        market_open = datetime.datetime.now().replace(
            hour=self.cfg.MARKET_OPEN_HOUR,
            minute=self.cfg.MARKET_OPEN_MINUTE,
            second=0, microsecond=0,
        )

        if datetime.datetime.now() >= market_open:
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
        while datetime.datetime.now() < target and not self._shutdown_requested:
            remaining = target - datetime.datetime.now()
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
        market_open = datetime.datetime.now().replace(
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
        print(f"  Min balance    : ₹{self.cfg.MIN_BALANCE_TO_TRADE:,}")
        print(f"  Max positions  : {self.cfg.MAX_POSITIONS}")
        print(f"  Universe       : {self.cfg.SCAN_UNIVERSE}")
        print(f"  Claude model   : {plan['model']}")
        print(f"  Price poll     : every {self.cfg.PRICE_POLL_SECONDS}s")
        print(f"  Claude review  : every {self.cfg.CLAUDE_REVIEW_MINUTES}min")
        print(f"  Stop-loss      : {self.cfg.DEFAULT_STOP_LOSS_PCT}%")
        print(f"  Target         : {self.cfg.DEFAULT_TARGET_PCT}%")
        print(f"  Circuit breaker: {self.cfg.MAX_LOSS_PER_DAY_PCT}% of budget")
        print(f"  Market open    : {self.cfg.MARKET_OPEN_HOUR}:{self.cfg.MARKET_OPEN_MINUTE:02d}")
        print(f"  Square off     : {self.cfg.SQUARE_OFF_HOUR}:{self.cfg.SQUARE_OFF_MINUTE:02d}")
        print(f"{'='*58}\n")

    def _print_status(self, quotes: dict):
        """Compact one-line status during monitor loop."""
        open_pos   = self.engine.open_positions()
        closed_pos = self.engine.closed_positions()
        unrealised = self.engine.unrealised_pnl(quotes)
        realised   = self.engine.day_pnl()
        now        = datetime.datetime.now().strftime("%H:%M:%S")

        # Color the P&L values
        u_color = "\033[92m" if unrealised >= 0 else "\033[91m"
        r_color = "\033[92m" if realised >= 0 else "\033[91m"

        print(
            f"\r  [{now}]  "
            f"Open: {len(open_pos)}  "
            f"Closed: {len(closed_pos)}  "
            f"Unrealised: {u_color}₹{unrealised:+,.2f}\033[0m  "
            f"Realised: {r_color}₹{realised:+,.2f}\033[0m  ",
            end="", flush=True,
        )

    def _print_pnl_summary(self, pnl: dict):
        """Prints the final P&L breakdown to terminal."""
        charges = pnl["charges"]

        color = "\033[92m" if pnl["is_profitable"] else "\033[91m"
        reset = "\033[0m"

        print(f"\n{'='*58}")
        print("  FINAL P&L SUMMARY")
        print(f"{'='*58}")
        print(f"  Total trades     : {len(self.engine.closed_positions())}")
        print(f"  Gross P&L        : ₹{pnl['gross_pnl']:+,.2f}")
        print(f"{'─'*58}")
        print(f"  CHARGES & TAXES:")
        print(f"    Brokerage      : ₹{charges['brokerage']:,.2f}")
        print(f"    STT            : ₹{charges['stt']:,.2f}")
        print(f"    Exchange txn   : ₹{charges['exchange_txn']:,.2f}")
        print(f"    GST            : ₹{charges['gst']:,.2f}")
        print(f"    SEBI charges   : ₹{charges['sebi_charges']:,.4f}")
        print(f"    Stamp duty     : ₹{charges['stamp_duty']:,.2f}")
        print(f"    ────────────────────────────")
        print(f"    Total tax+chrg : ₹{charges['total_tax_and_charges']:,.2f}")
        print(f"{'─'*58}")
        print(f"  CLAUDE API COST:")
        print(f"    Claude API     : ₹{charges['claude_api_cost']:,.2f} ({self.engine.claude_calls} calls)")
        print(f"{'─'*58}")
        print(f"  Total all costs  : ₹{charges['total_costs']:,.2f}")
        print(f"{'='*58}")
        print(f"  {color}NET PROFIT       : ₹{pnl['net_profit']:+,.2f}{reset}")
        print(f"{'='*58}")
        print(f"  FYI: Zerodha Kite Connect: ₹{charges['zerodha_monthly_fyi']:,.0f}/month (not deducted above)")
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

            self.log.warning("\nShutdown requested — squaring off positions...")
            self._shutdown_requested = True

        signal.signal(signal.SIGINT, handler)

    def _emergency_shutdown(self):
        """Square off all positions during unexpected shutdown."""
        if self.engine.open_positions():
            self.log.section("EMERGENCY SHUTDOWN")
            self._square_off()
            self._generate_report()

