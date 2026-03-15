# ================================================================
# portfolio/analyser.py
# ================================================================
# Phase 1 orchestrator — read-only portfolio analysis.
#
# Responsibilities:
#   Coordinate the six-step flow in order:
#     1. Validate config
#     2. Login to Zerodha
#     3. Fetch holdings
#     4. Enrich with market data
#     5. Analyse via Claude API
#     6. Save report
#
# This class itself is intentionally thin — all real logic lives
# in the service classes it calls. Adding behaviour here means
# adding a new step to the flow, not changing an existing one.
#
# Phase 2 note:
#   PortfolioManager (portfolio/manager.py) is structured identically
#   and uses the same four shared classes. Neither class knows about
#   the other — main.py decides which one to run.
# ================================================================

import datetime

from config                  import Config
from core.logger             import Logger
from core.zerodha_client     import ZerodhaClient
from core.claude_client      import ClaudeClient
from services.market_data    import MarketData
from services.analysis_queue import AnalysisQueue
from services.report_writer  import ReportWriter


class PortfolioAnalyser:

    def __init__(self, config: type[Config]):
        self.cfg = config

        # Each class gets its own named logger so log entries are
        # clearly attributed in logs/portfolio.log
        self.log     = Logger("PortfolioAnalyser")
        self.zerodha = ZerodhaClient(config, Logger("ZerodhaClient"))
        self.claude  = ClaudeClient(config,  Logger("ClaudeClient"))
        self.market  = MarketData(config, self.zerodha, Logger("MarketData"))
        self.queue   = AnalysisQueue(config, self.claude, Logger("AnalysisQueue"))
        self.report  = ReportWriter(config, Logger("ReportWriter"))

    # ================================================================
    # RUN
    # ================================================================

    def run(self):
        """Executes the full end-to-end analysis flow."""
        self._print_banner()

        # ── Step 1: Validate config ───────────────────────────────
        missing = self.cfg.validate()
        if missing:
            self.log.section("CONFIGURATION ERROR")
            for key in missing:
                self.log.error(f"Missing in .env file: {key}=your_value_here")
            self.log.info("Create or edit the .env file in this folder and re-run.")
            return

        for warning in self.cfg.mismatch_warnings():
            self.log.warning(f"Plan mismatch: {warning}")

        # ── Step 2: Login to Zerodha ──────────────────────────────
        self.log.section("ZERODHA LOGIN")
        self.zerodha.login()

        # ── Step 2b: Show account snapshot ─────────────────────────
        self._print_account_snapshot()

        # ── Step 3: Fetch holdings ────────────────────────────────
        self.log.section("FETCHING HOLDINGS")
        portfolio = self.zerodha.get_holdings()
        if not portfolio:
            self.log.warning("No holdings found in your account.")
            return
        self.log.success(f"Found {len(portfolio)} stocks in your demat account")

        # ── Step 4: Enrich with market data ───────────────────────
        self.log.section("ENRICHING WITH MARKET DATA")
        portfolio = self.market.enrich(portfolio)

        # ── Step 5: Analyse via Claude API ────────────────────────
        self.queue.load(portfolio)
        analyses, skipped, failed_log = self.queue.run()

        # ── Step 6: Save report ───────────────────────────────────
        self.log.section("SAVING REPORT")
        self.report.save(portfolio, analyses, skipped, failed_log)

        self._print_summary(analyses, skipped, failed_log)

    # ================================================================
    # DISPLAY HELPERS
    # ================================================================

    def _print_banner(self):
        """Shows the active configuration at the top of every run."""
        plan = self.cfg.claude()
        zrd  = self.cfg.zerodha()
        print(f"\n{'='*58}")
        print("  AI PORTFOLIO MANAGER \u2014 CONFIGURATION")
        print(f"{'='*58}")
        print(f"  Claude plan    : {self.cfg.CLAUDE_PLAN.upper()}")
        print(f"  \u2192 {plan['note']}")
        print()
        print(f"  Zerodha plan   : {self.cfg.ZERODHA_PLAN.upper()}")
        print(f"  \u2192 {zrd['note']}")
        print()
        print(f"  Claude model   : {plan['model']}")
        print(f"  Price source   : {zrd['price_source'].upper()}")
        print(f"{'='*58}\n")

    def _print_account_snapshot(self):
        """Prints account overview: balance, portfolio size, invested vs current value."""
        self.log.section("ACCOUNT SNAPSHOT")

        try:
            funds = self.zerodha.get_available_funds()
            self.log.info(f"Available balance: \u20b9{funds:,.2f}")
        except Exception:
            self.log.warning("Could not fetch available balance")

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
                self.log.info(f"Invested value     : \u20b9{invested:,.2f}")
                self.log.info(f"Current value      : \u20b9{current:,.2f}")
                self.log.info(
                    f"Portfolio P&L      : {pnl_color}\u20b9{pnl:+,.2f} "
                    f"({pnl_pct:+.2f}%){reset}"
                )
            else:
                self.log.info("No stocks in portfolio")
        except Exception:
            self.log.warning("Could not fetch portfolio holdings")

    def _print_summary(
        self,
        analyses:   list[dict],
        skipped:    list[str],
        failed_log: list[dict],
    ):
        today = datetime.date.today()
        print(f"\n{'='*58}")
        self.log.success(f"Run complete")
        self.log.success(f"Analysed : {len(analyses)} stocks")
        if skipped:
            self.log.warning(f"Skipped  : {len(skipped)} — {', '.join(skipped)}")
        if failed_log:
            self.log.error(f"Failed   : {len(failed_log)} (see report for details)")
        print()
        print(f"  Report : reports/portfolio_report_{today}.txt")
        print(f"  Data   : reports/portfolio_data_{today}.json")
        print()
        print(f"  Managed budget ready for Phase 2 (dynamic from Zerodha funds)")
        print(f"{'='*58}\n")
