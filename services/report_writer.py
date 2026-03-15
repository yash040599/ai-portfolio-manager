# ================================================================
# services/report_writer.py
# ================================================================
# Generates the human-readable .txt report and .json data dump.
#
# The key design principle here:
#   Every stock section in the report goes through _format_section()
#   which uses a fixed template. This guarantees identical formatting
#   for every stock regardless of which Claude call produced the data
#   or how many retry attempts it took.
#
# Outputs (both written to reports/):
#   portfolio_report_YYYY-MM-DD.txt  — human-readable, open in any editor
#   portfolio_data_YYYY-MM-DD.json   — raw data for Phase 2 manager
# ================================================================

import os
import json
import datetime

from config      import Config
from core.logger import Logger


class ReportWriter:

    # Separator widths — used throughout for consistent alignment
    SEP_MAJOR = "=" * 58
    SEP_MINOR = "─" * 58
    SEP_TABLE = "─" * 86

    def __init__(self, config: type[Config], log: Logger):
        self.cfg = config
        self.log = log

    # ================================================================
    # PUBLIC ENTRY POINT
    # ================================================================

    def save(
        self,
        portfolio:       list[dict],
        analyses:        list[dict],
        skipped_symbols: list[str]  = None,
        failed_log:      list[dict] = None,
    ) -> str:
        """
        Writes the report (.txt) and data file (.json) to reports/.
        Returns the path to the .txt file.
        """
        os.makedirs("reports", exist_ok=True)
        today     = datetime.date.today()
        txt_path  = f"reports/portfolio_report_{today}.txt"
        json_path = f"reports/portfolio_data_{today}.json"

        skipped_symbols = skipped_symbols or []
        failed_log      = failed_log      or []

        # Portfolio-level aggregates for the summary section
        total_invested = sum(s["invested_value"] for s in portfolio)
        total_current  = sum(s["current_value"]  for s in portfolio)
        total_pnl      = total_current - total_invested
        pnl_pct        = (total_pnl / total_invested * 100) if total_invested else 0

        # Write the human-readable report
        with open(txt_path, "w", encoding="utf-8") as f:
            self._write_header(f, today)
            self._write_config_section(f)
            self._write_summary_section(
                f, portfolio, analyses, skipped_symbols,
                total_invested, total_current, total_pnl, pnl_pct
            )
            self._write_quick_reference(f, analyses, skipped_symbols)
            self._write_detailed_analysis(f, analyses)

            if skipped_symbols:
                self._write_skipped_section(f, skipped_symbols)
            if failed_log:
                self._write_failed_log(f, failed_log)

        # Write the JSON data dump for Phase 2
        with open(json_path, "w") as f:
            json.dump({
                "date": str(today),
                "config": {
                    "claude_plan":  self.cfg.CLAUDE_PLAN,
                    "zerodha_plan": self.cfg.ZERODHA_PLAN,
                    "budget":       self.cfg.MANAGED_BUDGET_INR,
                },
                "portfolio": portfolio,
                "analyses": [
                    {
                        "symbol":   a["symbol"],
                        "parsed":   a["parsed"],
                        "raw":      a["raw"],
                        "attempts": a["attempts"],
                    }
                    for a in analyses
                ],
                "skipped": skipped_symbols,
                "failed":  failed_log,
            }, f, indent=2)

        self.log.success(f"Report : {txt_path}")
        self.log.success(f"Data   : {json_path}")
        return txt_path

    # ================================================================
    # SECTION WRITERS
    # ================================================================

    def _write_header(self, f, today: datetime.date):
        f.write(f"{self.SEP_MAJOR}\n")
        f.write(f"  PORTFOLIO ANALYSIS REPORT — {today}\n")
        f.write(f"{self.SEP_MAJOR}\n\n")

    def _write_config_section(self, f):
        plan = self.cfg.claude()
        zrd  = self.cfg.zerodha()
        f.write("CONFIGURATION\n")
        f.write(f"{self.SEP_MINOR}\n")
        f.write(f"Claude plan    : {self.cfg.CLAUDE_PLAN.upper()}  ({plan['model']})\n")
        f.write(f"Zerodha plan   : {self.cfg.ZERODHA_PLAN.upper()}\n")
        f.write(f"Price source   : {zrd['price_source'].upper()}\n")
        f.write(f"Managed budget : ₹{self.cfg.MANAGED_BUDGET_INR:,}  (existing stocks READ-ONLY)\n\n")

    def _write_summary_section(
        self, f, portfolio, analyses, skipped,
        invested, current, pnl, pnl_pct
    ):
        f.write("PORTFOLIO SUMMARY\n")
        f.write(f"{self.SEP_MINOR}\n")
        f.write(f"Total stocks   : {len(portfolio)}\n")
        f.write(f"Analysed       : {len(analyses)}\n")
        f.write(f"Skipped        : {len(skipped)}\n")
        f.write(f"Total invested : ₹{invested:,.2f}\n")
        f.write(f"Current value  : ₹{current:,.2f}\n")
        f.write(f"Overall P&L    : ₹{pnl:,.2f}  ({pnl_pct:.1f}%)\n\n")

    def _write_quick_reference(self, f, analyses: list[dict], skipped: list[str]):
        """
        Compact one-line-per-stock table so you can scan the whole
        portfolio at a glance before reading the detailed sections.
        """
        f.write("QUICK REFERENCE\n")
        f.write(f"{self.SEP_TABLE}\n")
        f.write(f"{'STOCK':<14} {'ACTION':<16} {'CONVICTION':<12} {'HORIZON':<22} {'TARGET PRICE'}\n")
        f.write(f"{self.SEP_TABLE}\n")

        for a in analyses:
            p = a["parsed"]
            f.write(
                f"{a['symbol']:<14} "
                f"{p.get('ACTION','N/A')[:15]:<16} "
                f"{p.get('CONVICTION','N/A')[:11]:<12} "
                f"{p.get('HORIZON','N/A')[:21]:<22} "
                f"{p.get('TARGET_PRICE','N/A')}\n"
            )
        for sym in skipped:
            f.write(f"{sym:<14} {'SKIPPED':<16} {'—':<12} {'—':<22} —\n")

        f.write("\n\n")

    def _write_detailed_analysis(self, f, analyses: list[dict]):
        f.write(f"{self.SEP_MAJOR}\n")
        f.write("DETAILED ANALYSIS\n")
        f.write(f"{self.SEP_MAJOR}\n\n")

        if not analyses:
            f.write("No analyses completed in this run.\n\n")
            return

        for a in analyses:
            # Every stock goes through the same template — consistent formatting
            f.write(self._format_section(
                symbol   = a["symbol"],
                parsed   = a["parsed"],
                stock    = a["stock"],
                attempts = a["attempts"],
            ))
            f.write("\n")

    def _write_skipped_section(self, f, skipped_symbols: list[str]):
        f.write(f"{self.SEP_MAJOR}\n")
        f.write("SKIPPED STOCKS\n")
        f.write(f"{self.SEP_MINOR}\n")
        f.write("Re-run the script to retry these.\n\n")
        for sym in skipped_symbols:
            f.write(f"  • {sym}\n")
        f.write("\n")

    def _write_failed_log(self, f, failed_log: list[dict]):
        f.write(f"{self.SEP_MAJOR}\n")
        f.write("FAILED STOCKS LOG\n")
        f.write(f"{self.SEP_MINOR}\n")
        for entry in failed_log:
            f.write(f"  • {entry['symbol']}: {entry['error']}\n")
        f.write("\n")

    # ================================================================
    # STOCK SECTION FORMATTER
    # ================================================================

    def _format_section(
        self,
        symbol:   str,
        parsed:   dict,
        stock:    dict,
        attempts: int,
    ) -> str:
        """
        Formats one stock's analysis into a fixed-template text block.

        This is the single function responsible for report consistency.
        Every stock — whether it was analysed on the first attempt or
        the third retry — goes through this exact same template.
        The parsed dict always has the same keys (guaranteed by the
        parser in AnalysisQueue), so this function never breaks.
        """
        lines = [self.SEP_MINOR]

        # ── Stock header ──────────────────────────────────────────
        lines.append(f"  STOCK      : {symbol} ({stock.get('exchange','NSE')})")
        lines.append(
            f"  HELD       : {stock['quantity']} shares  "
            f"Avg ₹{stock['avg_buy_price']}  "
            f"Current ₹{stock['current_price']}"
        )
        lines.append(f"  P&L        : ₹{stock['pnl']}  ({stock['pnl_percent']}%)")
        lines.append(
            f"  52-WEEK    : ₹{stock.get('52w_low','N/A')} – ₹{stock.get('52w_high','N/A')}  "
            f"Trend: {stock.get('price_trend','N/A')}  "
            f"Momentum: {stock.get('momentum','N/A')}"
        )
        if attempts > 1:
            lines.append(f"  NOTE       : Succeeded on attempt {attempts}")

        lines.append(self.SEP_MINOR)

        # ── Analysis fields — always in this exact order ──────────
        lines.append(f"  ACTION       : {parsed['ACTION']}")
        lines.append(f"  CONVICTION   : {parsed['CONVICTION']}")
        lines.append(f"  HORIZON      : {parsed['HORIZON']}")
        lines.append(f"  TARGET PRICE : {parsed['TARGET_PRICE']}")
        lines.append("")

        lines.append("  REASONING")
        for line in parsed["REASONING"].splitlines():
            if line.strip():
                lines.append(f"    {line.strip()}")
        lines.append("")

        lines.append("  RISKS")
        for line in parsed["RISKS"].splitlines():
            if line.strip():
                lines.append(f"    {line.strip()}")
        lines.append("")

        lines.append(f"  WATCH        : {parsed['WATCH']}")
        lines.append("")

        lines.append("  NEXT STEPS")
        for line in parsed["NEXT_STEPS"].splitlines():
            if line.strip():
                lines.append(f"    {line.strip()}")
        lines.append("")

        return "\n".join(lines)

    # ================================================================
    # PHASE 2 — TRADING DAY REPORT
    # ================================================================
    # Generates a full end-of-day report for intraday trading.
    # Includes: trade log, position details, P&L breakdown,
    # taxes, charges, subscription costs, and net profit.
    # ================================================================

    def save_trading_day(
        self,
        positions:  list[dict],
        trade_log:  list[dict],
        pnl:        dict,
        dry_run:    bool = True,
    ) -> str:
        """
        Writes the Phase 2 intraday trading report.

        Args:
            positions:  all positions (open and closed) from OrderEngine
            trade_log:  chronological action log from OrderEngine
            pnl:        net_profit() dict from OrderEngine
            dry_run:    whether this was a dry run

        Outputs:
            reports/trading_report_YYYY-MM-DD.txt  — human-readable
            reports/trading_data_YYYY-MM-DD.json   — machine-readable

        Returns the path to the .txt file.
        """
        os.makedirs("reports", exist_ok=True)
        today     = datetime.date.today()
        txt_path  = f"reports/trading_report_{today}.txt"
        json_path = f"reports/trading_data_{today}.json"

        mode_label = "DRY RUN (simulated)" if dry_run else "LIVE TRADING"
        charges    = pnl["charges"]

        with open(txt_path, "w", encoding="utf-8") as f:
            # ── Header ────────────────────────────────────────────
            f.write(f"{self.SEP_MAJOR}\n")
            f.write(f"  INTRADAY TRADING REPORT — {today}\n")
            f.write(f"  Mode: {mode_label}\n")
            f.write(f"{self.SEP_MAJOR}\n\n")

            # ── Configuration ─────────────────────────────────────
            f.write("CONFIGURATION\n")
            f.write(f"{self.SEP_MINOR}\n")
            f.write(f"Claude plan     : {self.cfg.CLAUDE_PLAN.upper()}\n")
            f.write(f"Budget          : ₹{self.cfg.MANAGED_BUDGET_INR:,}\n")
            f.write(f"Universe        : {self.cfg.SCAN_UNIVERSE}\n")
            f.write(f"Max positions   : {self.cfg.MAX_POSITIONS}\n")
            f.write(f"Stop-loss       : {self.cfg.DEFAULT_STOP_LOSS_PCT}%\n")
            f.write(f"Target          : {self.cfg.DEFAULT_TARGET_PCT}%\n")
            f.write(f"Circuit breaker : {self.cfg.MAX_LOSS_PER_DAY_PCT}%\n\n")

            # ── Trade Summary ─────────────────────────────────────
            closed = [p for p in positions if p.get("status") == "CLOSED"]
            open_p = [p for p in positions if p.get("status") == "OPEN"]
            winners = [p for p in closed if p.get("pnl", 0) > 0]
            losers  = [p for p in closed if p.get("pnl", 0) < 0]

            f.write("TRADE SUMMARY\n")
            f.write(f"{self.SEP_MINOR}\n")
            f.write(f"Total trades    : {len(closed)}\n")
            f.write(f"Winners         : {len(winners)}\n")
            f.write(f"Losers          : {len(losers)}\n")
            f.write(f"Still open      : {len(open_p)}\n")
            if closed:
                win_rate = len(winners) / len(closed) * 100
                f.write(f"Win rate        : {win_rate:.1f}%\n")
            f.write("\n")

            # ── Trade Details Table ───────────────────────────────
            f.write("TRADE DETAILS\n")
            f.write(f"{self.SEP_TABLE}\n")
            f.write(
                f"{'SYMBOL':<12} {'SIDE':<6} {'QTY':>5} "
                f"{'ENTRY':>10} {'EXIT':>10} {'P&L':>12} "
                f"{'REASON':<14} {'ENTRY_T':<10} {'EXIT_T':<10}\n"
            )
            f.write(f"{self.SEP_TABLE}\n")

            for p in positions:
                exit_p  = f"₹{p['exit_price']:.2f}" if p.get("exit_price") else "—"
                pnl_val = f"₹{p.get('pnl', 0):+,.2f}" if p.get("exit_price") else "—"
                f.write(
                    f"{p['symbol']:<12} {p['side']:<6} {p['qty']:>5} "
                    f"₹{p['entry_price']:>9.2f} {exit_p:>10} {pnl_val:>12} "
                    f"{p.get('exit_reason', 'OPEN'):<14} "
                    f"{p.get('entry_time', '—'):<10} "
                    f"{p.get('exit_time', '—'):<10}\n"
                )

            f.write("\n")

            # ── Rationales ────────────────────────────────────────
            f.write("TRADE RATIONALES\n")
            f.write(f"{self.SEP_MINOR}\n")
            for p in positions:
                f.write(f"  {p['symbol']}: {p.get('rationale', '—')}\n")
            f.write("\n")

            # ── P&L Breakdown ─────────────────────────────────────
            f.write(f"{self.SEP_MAJOR}\n")
            f.write("P&L BREAKDOWN\n")
            f.write(f"{self.SEP_MAJOR}\n\n")

            f.write(f"Gross P&L               : ₹{pnl['gross_pnl']:+,.2f}\n\n")

            f.write("CHARGES & TAXES:\n")
            f.write(f"  Brokerage             : ₹{charges['brokerage']:,.2f}\n")
            f.write(f"  STT (sell side)       : ₹{charges['stt']:,.2f}\n")
            f.write(f"  Exchange transaction  : ₹{charges['exchange_txn']:,.2f}\n")
            f.write(f"  GST (18%)             : ₹{charges['gst']:,.2f}\n")
            f.write(f"  SEBI charges          : ₹{charges['sebi_charges']:,.4f}\n")
            f.write(f"  Stamp duty (buy side) : ₹{charges['stamp_duty']:,.2f}\n")
            f.write(f"  {'─' * 40}\n")
            f.write(f"  Total tax & charges   : ₹{charges['total_tax_and_charges']:,.2f}\n\n")

            f.write("CLAUDE API COST:\n")
            f.write(f"  Claude API usage      : ₹{charges['claude_api_cost']:,.2f}  (est. ₹{self.cfg.CLAUDE_COST_PER_CALL}/call × actual calls)\n")
            f.write(f"  {'─' * 40}\n")
            f.write(f"  Total all costs       : ₹{charges['total_costs']:,.2f}\n\n")

            f.write(f"{'=' * 42}\n")
            f.write(f"  NET PROFIT AFTER ALL  : ₹{pnl['net_profit']:+,.2f}\n")
            f.write(f"{'=' * 42}\n")
            profitable = "YES ✓" if pnl["is_profitable"] else "NO ✗"
            f.write(f"  Profitable?           : {profitable}\n\n")

            f.write(f"  FYI: Zerodha Kite Connect subscription is ₹{self.cfg.ZERODHA_MONTHLY_COST:,.0f}/month (not deducted above).\n")
            f.write(f"  Track cumulative daily profits to ensure they cover this monthly cost.\n\n")

            # ── Turnover Details ──────────────────────────────────
            f.write("TURNOVER DETAILS\n")
            f.write(f"{self.SEP_MINOR}\n")
            f.write(f"  Buy turnover          : ₹{charges['buy_turnover']:,.2f}\n")
            f.write(f"  Sell turnover         : ₹{charges['sell_turnover']:,.2f}\n")
            f.write(f"  Total turnover        : ₹{charges['total_turnover']:,.2f}\n")
            f.write(f"  Total orders          : {charges['num_orders']}\n\n")

            # ── Chronological Trade Log ───────────────────────────
            f.write("CHRONOLOGICAL TRADE LOG\n")
            f.write(f"{self.SEP_MINOR}\n")
            for entry in trade_log:
                f.write(
                    f"  [{entry['time']}] {entry['action']:<14} "
                    f"{entry['symbol']:<12} {entry['side']:<5} "
                    f"{entry['qty']:>5}  ₹{entry['price']:>10}  "
                    f"{entry['detail']}\n"
                )
            f.write("\n")

        # ── JSON data dump ────────────────────────────────────────
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "date":       str(today),
                "mode":       "dry_run" if dry_run else "live",
                "config": {
                    "claude_plan":  self.cfg.CLAUDE_PLAN,
                    "zerodha_plan": self.cfg.ZERODHA_PLAN,
                    "budget":       self.cfg.MANAGED_BUDGET_INR,
                    "universe":     self.cfg.SCAN_UNIVERSE,
                    "max_positions": self.cfg.MAX_POSITIONS,
                    "stop_loss_pct": self.cfg.DEFAULT_STOP_LOSS_PCT,
                    "target_pct":    self.cfg.DEFAULT_TARGET_PCT,
                },
                "positions":  positions,
                "trade_log":  trade_log,
                "pnl":        pnl,
            }, f, indent=2, default=str)

        self.log.success(f"Trading report : {txt_path}")
        self.log.success(f"Trading data   : {json_path}")
        return txt_path

