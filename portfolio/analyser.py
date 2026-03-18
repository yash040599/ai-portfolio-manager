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

import os
import datetime

from config                        import Config
from core.logger                   import Logger
from core.zerodha_client           import ZerodhaClient
from core.claude_client            import ClaudeClient
from services.market_data          import MarketData
from services.analysis_queue       import AnalysisQueue
from services.report_writer        import ReportWriter
from services.performance_tracker  import PerformanceTracker


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
        self.tracker = PerformanceTracker(config, Logger("PerformanceTracker"))

    # ================================================================
    # RUN
    # ================================================================

    def run(self):
        """Executes the full end-to-end analysis flow."""
        self._print_banner()

        # ── Check if today's report already exists ────────────────
        today = datetime.date.today()
        if os.path.exists(ReportWriter.portfolio_report_path(today)):
            answer = input(
                f"\n⚠️  Report for {today} already exists and will be overwritten.\n"
                f"   Do you want to run again? (y/n): "
            ).strip().lower()
            if answer != 'y':
                self.log.info("Skipped — existing report preserved.")
                return

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

        # ── Step 4b: Load previous report for comparison ──────────
        prev_data = self.tracker.get_latest_portfolio_analysis()
        if prev_data is None:
            # Fallback to JSON file scan if DB is empty (first run after DB was added)
            prev_data = ReportWriter.find_latest_portfolio_data(datetime.date.today())
        if prev_data:
            self.log.info(f"Previous report found ({prev_data['date']}) — Claude will compare changes")
        else:
            self.log.info("No previous report found — first run")

        # ── Step 5: Analyse via Claude API ────────────────────────
        self.queue.load(portfolio, previous_data=prev_data)
        analyses, skipped, failed_log = self.queue.run()

        # ── Step 6: Save report ───────────────────────────────────
        self.log.section("SAVING REPORT")
        self.report.save(portfolio, analyses, skipped, failed_log)

        # ── Step 7: Record to performance database ────────────────
        self.tracker.record_portfolio_analyses(portfolio, analyses)

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
        """Delegates to ZerodhaClient's shared account snapshot."""
        self.zerodha.print_account_snapshot()

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
        print(f"  Report : {ReportWriter.portfolio_report_path(today)}")
        print(f"  Data   : {ReportWriter.portfolio_data_path(today)}")
        print()
        print(f"  Managed budget ready for Phase 2 (dynamic from Zerodha funds)")
        print(f"{'='*58}\n")
