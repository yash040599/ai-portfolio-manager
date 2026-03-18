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
# Outputs:
#   reports/portfolio/<year>/<month>/portfolio_report_DD.txt
#   reports/portfolio/<year>/<month>/portfolio_data_DD.json
#   reports/trading/<year>/<month>/trading_report_DD.txt
#   reports/trading/<year>/<month>/trading_data_DD.json
# ================================================================

import os
import re
import json
import glob
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
    # PATH HELPERS
    # ================================================================

    @staticmethod
    def _portfolio_dir(date: datetime.date) -> str:
        return f"reports/portfolio/{date.year}/{date.month:02d}"

    @staticmethod
    def _trading_dir(date: datetime.date) -> str:
        return f"reports/trading/{date.year}/{date.month:02d}"

    @staticmethod
    def portfolio_report_path(date: datetime.date) -> str:
        return f"{ReportWriter._portfolio_dir(date)}/portfolio_report_{date.day:02d}.txt"

    @staticmethod
    def portfolio_data_path(date: datetime.date) -> str:
        return f"{ReportWriter._portfolio_dir(date)}/portfolio_data_{date.day:02d}.json"

    @staticmethod
    def portfolio_sheet_path(date: datetime.date) -> str:
        return f"{ReportWriter._portfolio_dir(date)}/portfolio_sheet_{date.day:02d}.tsv"

    @staticmethod
    def trading_report_path(date: datetime.date) -> str:
        return f"{ReportWriter._trading_dir(date)}/trading_report_{date.day:02d}.txt"

    @staticmethod
    def trading_data_path(date: datetime.date) -> str:
        return f"{ReportWriter._trading_dir(date)}/trading_data_{date.day:02d}.json"

    @staticmethod
    def find_latest_portfolio_data(before: datetime.date) -> dict | None:
        """
        Scans reports/portfolio/ for the most recent portfolio_data JSON
        strictly before the given date.  Returns the parsed dict or None.
        """
        best_date = None
        best_path = None

        for path in glob.glob("reports/portfolio/*/*/portfolio_data_*.json"):
            path = path.replace("\\", "/")
            parts = path.split("/")
            # parts: reports / portfolio / <year> / <month> / portfolio_data_DD.json
            try:
                year  = int(parts[2])
                month = int(parts[3])
                day   = int(re.search(r"portfolio_data_(\d+)\.json", parts[4]).group(1))
                d     = datetime.date(year, month, day)
            except (ValueError, IndexError, AttributeError):
                continue

            if d < before and (best_date is None or d > best_date):
                best_date = d
                best_path = path

        if best_path is None:
            return None

        with open(best_path, "r", encoding="utf-8") as f:
            return json.load(f)

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
        Writes the report (.txt) and data file (.json).
        Returns the path to the .txt file.
        """
        today     = datetime.date.today()
        os.makedirs(self._portfolio_dir(today), exist_ok=True)
        txt_path  = self.portfolio_report_path(today)
        json_path = self.portfolio_data_path(today)

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
                    "budget":       "dynamic",
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

        # Write the spreadsheet-friendly TSV file
        tsv_path = self.portfolio_sheet_path(today)
        self._write_spreadsheet(tsv_path, analyses)

        self.log.success(f"Report : {txt_path}")
        self.log.success(f"Data   : {json_path}")
        self.log.success(f"Sheet  : {tsv_path}")
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
        f.write(f"Managed budget : Dynamic (from Zerodha account funds)\n\n")

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
    # SPREADSHEET TABLE (TSV)
    # ================================================================

    @staticmethod
    def _parse_target_range(target_str: str) -> tuple[str, str]:
        """Extract low and high from target price string like '₹450-500' or '₹1,320–₹1,380'."""
        # Remove ₹ and commas, find all numbers
        cleaned = target_str.replace("₹", "").replace(",", "")
        numbers = re.findall(r"[\d]+(?:\.[\d]+)?", cleaned)
        if len(numbers) >= 2:
            return numbers[0], numbers[1]
        elif len(numbers) == 1:
            return numbers[0], numbers[0]
        return "", ""

    @staticmethod
    def _parse_int_field(value: str) -> str:
        """Extract the first integer from a field like '25 shares' or '0'."""
        nums = re.findall(r"\d+", value)
        return nums[0] if nums else "0"

    @staticmethod
    def _parse_price_field(value: str) -> str:
        """Extract the first number from a price field like '₹840' or '1200'."""
        cleaned = value.replace("₹", "").replace(",", "")
        nums = re.findall(r"[\d]+(?:\.[\d]+)?", cleaned)
        return nums[0] if nums else "0"

    def _write_spreadsheet(self, path: str, analyses: list[dict]):
        """
        Writes a tab-separated file for easy copy-paste into Google Sheets / Excel.
        """
        headers = [
            "Ticker",
            "Horizon",
            "Action Detail",
            "Buy/Sell",
            "No of Stocks",
            "Value",
            "My Average",
            "Current Price",
            "Target Low",
            "Target High",
            "Next Steps",
            "Trigger Price",
            "Action at Trigger",
            "Stocks at Trigger",
            "Value at Trigger",
        ]

        rows = []
        for a in analyses:
            p     = a["parsed"]
            stock = a["stock"]
            action = p.get("ACTION", "")

            # Determine Buy/Sell from action
            if action in ("AVERAGE DOWN", "ADD MORE"):
                buy_sell = "BUY"
            elif action in ("PARTIAL EXIT", "FULL EXIT"):
                buy_sell = "SELL"
            else:
                buy_sell = ""

            # Number of stocks for immediate action
            num_stocks_raw = self._parse_int_field(p.get("NUM_STOCKS", "0"))
            num_stocks = num_stocks_raw if num_stocks_raw != "0" else ""

            # Value = num_stocks * current_price
            current_price = float(stock.get("current_price", 0))
            try:
                value = str(round(int(num_stocks) * current_price, 2)) if num_stocks else ""
            except (ValueError, TypeError):
                value = ""

            # Target range
            target_low, target_high = self._parse_target_range(p.get("TARGET_PRICE", ""))

            # Next steps — join into single cell, replace newlines with semicolons
            next_steps = p.get("NEXT_STEPS", "").replace("\n", " ").replace("\t", " ").strip()

            # Trigger fields
            trigger_price_raw = self._parse_price_field(p.get("TRIGGER_PRICE", "0"))
            trigger_price = trigger_price_raw if trigger_price_raw != "0" else ""

            trigger_action = p.get("TRIGGER_ACTION", "NONE").strip().upper()
            if trigger_action == "NONE" or trigger_action == "[NOT PROVIDED]":
                trigger_action = ""

            trigger_num_raw = self._parse_int_field(p.get("TRIGGER_NUM_STOCKS", "0"))
            trigger_num = trigger_num_raw if trigger_num_raw != "0" else ""

            # Value at trigger = trigger_num * trigger_price
            try:
                val_at_trigger = str(round(int(trigger_num) * float(trigger_price_raw), 2)) if trigger_num and trigger_price else ""
            except (ValueError, TypeError):
                val_at_trigger = ""

            row = [
                a["symbol"],
                p.get("HORIZON", ""),
                p.get("ACTION_DETAIL", action),
                buy_sell,
                num_stocks,
                value,
                str(stock.get("avg_buy_price", "")),
                str(stock.get("current_price", "")),
                target_low,
                target_high,
                next_steps,
                trigger_price,
                trigger_action,
                trigger_num,
                val_at_trigger,
            ]
            rows.append(row)

        with open(path, "w", encoding="utf-8") as f:
            f.write("\t".join(headers) + "\n")
            for row in rows:
                f.write("\t".join(row) + "\n")

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
        budget:     float = 0,
        market_condition: str = "",
    ) -> str:
        """
        Writes the Phase 2 intraday trading report.

        If a report already exists for today, merges the new session's
        data with the existing data and writes a combined report with
        cumulative P&L and %returns for the day.

        Args:
            positions:  all positions (open and closed) from OrderEngine
            trade_log:  chronological action log from OrderEngine
            pnl:        net_profit() dict from OrderEngine
            dry_run:    whether this was a dry run
            budget:     actual trading budget used (from Zerodha funds)

        Outputs:
            reports/trading/<year>/<month>/trading_report_DD.txt
            reports/trading/<year>/<month>/trading_data_DD.json

        Returns the path to the .txt file.
        """
        today     = datetime.date.today()
        os.makedirs(self._trading_dir(today), exist_ok=True)
        txt_path  = self.trading_report_path(today)
        json_path = self.trading_data_path(today)

        # ── Merge with existing session data if report exists ─────
        session_count = 1
        prev_claude_cost = 0.0
        curr_claude_cost = pnl.get("charges", {}).get("claude_api_cost", 0.0)
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)

                prev_positions = existing.get("positions", [])
                prev_trade_log = existing.get("trade_log", [])
                session_count  = existing.get("sessions", 1) + 1
                prev_claude_cost = existing.get("pnl", {}).get("charges", {}).get("claude_api_cost", 0.0)

                # Add session separator to trade log
                separator_entry = {
                    "time":   datetime.datetime.now().strftime("%H:%M:%S"),
                    "action": "SESSION",
                    "symbol": "",
                    "side":   "",
                    "qty":    0,
                    "price":  0,
                    "detail": f"═══ SESSION {session_count} START ═══",
                }

                # Merge: previous data + separator + current session data
                positions = prev_positions + positions
                trade_log = prev_trade_log + [separator_entry] + trade_log
                budget    = max(budget, existing.get("config", {}).get("budget", budget))

                # Recalculate combined P&L from all positions
                total_claude_cost = prev_claude_cost + curr_claude_cost
                pnl = self._calculate_combined_pnl(positions, total_claude_cost)

                self.log.info(
                    f"Merging with existing report (session {session_count})"
                )
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                self.log.warning(f"Could not merge with existing report: {e} — overwriting")
                session_count = 1

        mode_label = "DRY RUN (simulated)" if dry_run else "LIVE TRADING"
        charges    = pnl["charges"]

        with open(txt_path, "w", encoding="utf-8") as f:
            # ── Header ────────────────────────────────────────────
            f.write(f"{self.SEP_MAJOR}\n")
            f.write(f"  INTRADAY TRADING REPORT — {today}\n")
            f.write(f"  Mode: {mode_label}\n")
            if session_count > 1:
                f.write(f"  Sessions: {session_count} (combined)\n")
            f.write(f"{self.SEP_MAJOR}\n\n")

            # ── Configuration ─────────────────────────────────────
            f.write("CONFIGURATION\n")
            f.write(f"{self.SEP_MINOR}\n")
            f.write(f"Claude plan     : {self.cfg.CLAUDE_PLAN.upper()}\n")
            f.write(f"Budget          : ₹{budget:,.2f} (from Zerodha funds)\n")
            f.write(f"Universe        : {self.cfg.SCAN_UNIVERSE}\n")
            if market_condition:
                f.write(f"Market condition: {market_condition}\n")
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
            f.write(f"  Profitable?           : {profitable}\n")
            if budget > 0:
                returns_pct = pnl["net_profit"] / budget * 100
                f.write(f"  Day returns           : {returns_pct:+.2f}% on ₹{budget:,.0f} budget\n")
            f.write("\n")

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
                "date":             str(today),
                "mode":             "dry_run" if dry_run else "live",
                "sessions":         session_count,
                "market_condition": market_condition,
                "config": {
                    "claude_plan":  self.cfg.CLAUDE_PLAN,
                    "zerodha_plan": self.cfg.ZERODHA_PLAN,
                    "budget":       budget,
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

    # ================================================================
    # COMBINED P&L CALCULATION (for multi-session day reports)
    # ================================================================

    def _calculate_combined_pnl(self, all_positions: list[dict], claude_api_cost: float = 0.0) -> dict:
        """
        Recalculates P&L from a merged list of positions across
        multiple sessions. Uses the same charge formula as OrderEngine
        but operates on the combined position list directly.
        """
        closed = [p for p in all_positions if p.get("status") == "CLOSED"]

        gross_pnl = sum(p.get("pnl", 0) for p in closed)

        total_buy_turnover  = 0.0
        total_sell_turnover = 0.0
        num_orders          = 0

        for p in closed:
            entry_value = p.get("entry_price", 0) * p.get("qty", 0)
            exit_value  = p.get("exit_price", 0)  * p.get("qty", 0)

            if p.get("side") == "BUY":
                total_buy_turnover  += entry_value
                total_sell_turnover += exit_value
            else:
                total_sell_turnover += entry_value
                total_buy_turnover  += exit_value

            num_orders += 2

        total_turnover = total_buy_turnover + total_sell_turnover

        brokerage_flat = self.cfg.ZERODHA_BROKERAGE_FLAT * num_orders
        brokerage_pct  = total_turnover * self.cfg.ZERODHA_BROKERAGE_PCT / 100
        brokerage      = min(brokerage_flat, brokerage_pct) if num_orders > 0 else 0

        stt          = total_sell_turnover * self.cfg.STT_SELL_PCT / 100
        exchange_txn = total_turnover * self.cfg.EXCHANGE_TXN_PCT / 100
        sebi         = total_turnover / 1e7 * self.cfg.SEBI_CHARGE_PER_CR
        gst          = (brokerage + sebi + exchange_txn) * self.cfg.GST_PCT / 100
        stamp_duty   = total_buy_turnover * self.cfg.STAMP_DUTY_BUY_PCT / 100

        total_charges = brokerage + stt + exchange_txn + gst + sebi + stamp_duty

        charges = {
            "total_turnover":        round(total_turnover, 2),
            "buy_turnover":          round(total_buy_turnover, 2),
            "sell_turnover":         round(total_sell_turnover, 2),
            "num_orders":            num_orders,
            "brokerage":             round(brokerage, 2),
            "stt":                   round(stt, 2),
            "exchange_txn":          round(exchange_txn, 2),
            "gst":                   round(gst, 2),
            "sebi_charges":          round(sebi, 4),
            "stamp_duty":            round(stamp_duty, 2),
            "total_tax_and_charges": round(total_charges, 2),
            "claude_api_cost":       round(claude_api_cost, 2),
            "total_costs":           round(total_charges + claude_api_cost, 2),
            "zerodha_monthly_fyi":   self.cfg.ZERODHA_MONTHLY_COST,
        }

        net = gross_pnl - charges["total_costs"]

        return {
            "gross_pnl":     round(gross_pnl, 2),
            "charges":       charges,
            "net_profit":    round(net, 2),
            "is_profitable": net > 0,
        }

