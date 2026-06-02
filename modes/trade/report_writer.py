# ================================================================
# modes/trade/report_writer.py
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
import subprocess

from config      import Config, now_ist
from core.logger import Logger


# Roadmap D13 / V2 #246 — git SHA at session start, stamped into
# trading_data_DD.json so the dashboard can overlay strategy-version
# boundaries on the cumulative-P&L chart. Cached per-process: every
# session save in the same Python invocation reuses the value (a single
# bot run cannot have changed its own checkout mid-session).
_GIT_SHA_CACHE: str | None = None
_GIT_SHA_RESOLVED: bool = False


def _git_short_sha() -> str | None:
    """Return the short SHA of HEAD, or None if git is unavailable.

    Failure modes that return None: git not on PATH, not a git repo,
    detached/empty repo, or any non-zero exit. Never raises.
    """
    global _GIT_SHA_CACHE, _GIT_SHA_RESOLVED
    if _GIT_SHA_RESOLVED:
        return _GIT_SHA_CACHE
    _GIT_SHA_RESOLVED = True
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
        _GIT_SHA_CACHE = out or None
    except (FileNotFoundError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, OSError):
        _GIT_SHA_CACHE = None
    return _GIT_SHA_CACHE


class ReportWriter:

    # Separator widths — used throughout for consistent alignment
    SEP_MAJOR = "=" * 58
    SEP_MINOR = "─" * 58
    SEP_TABLE = "─" * 86

    def __init__(self, config: type[Config], log: Logger):
        self.cfg = config
        self.log = log

    def _research_phase_payload(self) -> dict:
        """Status metadata only; not part of strategy-config hash."""
        return {
            "stage": str(getattr(self.cfg, "TRADE_RESEARCH_STAGE", "") or ""),
            "label": str(getattr(self.cfg, "TRADE_RESEARCH_PHASE_LABEL", "") or ""),
            "note": str(getattr(self.cfg, "TRADE_RESEARCH_PHASE_NOTE", "") or ""),
            "live_trading_paused": bool(getattr(self.cfg, "TRADE_LIVE_TRADING_PAUSED", False)),
        }

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
    def trading_report_path(date: datetime.date, *, dry_run: bool = False) -> str:
        suffix = "_dry_run" if dry_run else ""
        return f"{ReportWriter._trading_dir(date)}/trading_report_{date.day:02d}{suffix}.txt"

    @staticmethod
    def trading_data_path(date: datetime.date, *, dry_run: bool = False) -> str:
        suffix = "_dry_run" if dry_run else ""
        return f"{ReportWriter._trading_dir(date)}/trading_data_{date.day:02d}{suffix}.json"

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
            # parts: .../portfolio/<year>/<month>/portfolio_data_DD.json
            try:
                year  = int(parts[-3])
                month = int(parts[-2])
                day   = int(re.search(r"portfolio_data_(\d+)\.json", parts[-1]).group(1))
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
        portfolio_review: str       = None,
        new_stock_recommendations: list[dict] = None,
    ) -> str:
        """
        Writes the report (.txt) and data file (.json).
        Returns the path to the .txt file.
        """
        today     = now_ist().date()
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
            if portfolio_review:
                self._write_portfolio_review(f, portfolio_review)

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
                "portfolio_review": portfolio_review,
                "new_stock_recommendations": new_stock_recommendations or [],
            }, f, indent=2)

        # Write the spreadsheet-friendly TSV file
        tsv_path = self.portfolio_sheet_path(today)
        self._write_spreadsheet(tsv_path, analyses, new_stock_recommendations)

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
        f.write(f"Total invested : Rs.{invested:,.2f}\n")
        f.write(f"Current value  : Rs.{current:,.2f}\n")
        f.write(f"Overall P&L    : Rs.{pnl:,.2f}  ({pnl_pct:.1f}%)\n\n")

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

    def _write_portfolio_review(self, f, review_text: str):
        f.write(f"\n{self.SEP_MAJOR}\n")
        f.write("PORTFOLIO-LEVEL REVIEW\n")
        f.write(f"{self.SEP_MAJOR}\n\n")
        for line in review_text.strip().splitlines():
            f.write(f"  {line}\n")
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
            f"Avg Rs.{stock['avg_buy_price']}  "
            f"Current Rs.{stock['current_price']}"
        )
        lines.append(f"  P&L        : Rs.{stock['pnl']}  ({stock['pnl_percent']}%)")
        lines.append(
            f"  52-WEEK    : Rs.{stock.get('52w_low','N/A')} – Rs.{stock.get('52w_high','N/A')}  "
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
        """Extract low and high from target price string like 'Rs.450-500' or 'Rs.1,320–Rs.1,380'."""
        # Remove Rs. and commas, find all numbers
        cleaned = target_str.replace("Rs.", "").replace(",", "")
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
        """Extract the first number from a price field like 'Rs.840' or '1200'."""
        cleaned = value.replace("Rs.", "").replace(",", "")
        nums = re.findall(r"[\d]+(?:\.[\d]+)?", cleaned)
        return nums[0] if nums else "0"

    def _write_spreadsheet(self, path: str, analyses: list[dict], new_stock_recommendations: list[dict] = None):
        """
        Writes a tab-separated file for easy copy-paste into Google Sheets / Excel.
        Includes both existing portfolio stocks and new stock recommendations.
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

        # Append new stock recommendations from portfolio review
        for rec in (new_stock_recommendations or []):
            symbol = rec.get("symbol", "")
            target_low, target_high = self._parse_target_range(rec.get("target_price", ""))
            row = [
                symbol,
                rec.get("horizon", ""),
                f"NEW BUY — {rec.get('rationale', '')}",
                "BUY",
                "",   # num_stocks (unknown — not in portfolio yet)
                "",   # value
                "",   # avg_buy_price (no holding)
                "",   # current_price (not fetched)
                target_low,
                target_high,
                rec.get("rationale", ""),
                "",   # trigger_price
                "",   # trigger_action
                "",   # trigger_num
                "",   # val_at_trigger
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
            Dry-run mode uses *_dry_run filenames so simulated reports
            never merge into live report artifacts for the same date.

        Returns the path to the .txt file.
        """
        today     = now_ist().date()
        os.makedirs(self._trading_dir(today), exist_ok=True)
        txt_path  = self.trading_report_path(today, dry_run=dry_run)
        json_path = self.trading_data_path(today, dry_run=dry_run)

        # ── Merge with existing session data if report exists ─────
        session_count = 1
        prev_claude_cost = 0.0
        curr_claude_cost = pnl.get("charges", {}).get("claude_api_cost", 0.0)
        # Sticky reconcile flags — preserved across session saves so that
        # a human's manual fix (marked _reconciled=true) is not silently
        # dropped when a later session rewrites the JSON.
        preserved_reconciled: bool = False
        preserved_reconcile_note: str | None = None
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)

                prev_positions = existing.get("positions", [])
                prev_trade_log = existing.get("trade_log", [])
                session_count  = existing.get("sessions", 1) + 1
                prev_claude_cost = existing.get("pnl", {}).get("charges", {}).get("claude_api_cost", 0.0)
                preserved_reconciled = bool(existing.get("_reconciled"))
                preserved_reconcile_note = existing.get("_reconciled_note")

                # Add session separator to trade log
                separator_entry = {
                    "time":   now_ist().strftime("%H:%M:%S"),
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
        research_phase = self._research_phase_payload()
        strategy_config_version, strategy_config_hash = self.cfg.snapshot_hash()
        strategy_profile = str(getattr(self.cfg, "TRADE_STRATEGY_PROFILE", ""))

        with open(txt_path, "w", encoding="utf-8") as f:
            # ── Header ────────────────────────────────────────────
            f.write(f"{self.SEP_MAJOR}\n")
            f.write(f"  INTRADAY TRADING REPORT — {today}\n")
            f.write(f"  Mode: {mode_label}\n")
            if research_phase["label"]:
                phase = research_phase["label"]
                if research_phase["stage"]:
                    phase = f"{research_phase['stage']} - {phase}"
                f.write(f"  Research phase: {phase}\n")
                paused = "YES" if research_phase["live_trading_paused"] else "NO"
                f.write(f"  Live trading paused: {paused}\n")
            f.write(f"  Sessions: {session_count} (Run {session_count})\n")
            f.write(f"{self.SEP_MAJOR}\n\n")

            # ── Configuration ─────────────────────────────────────
            f.write("CONFIGURATION\n")
            f.write(f"{self.SEP_MINOR}\n")
            f.write(f"AI provider     : {self.cfg.AI_PROVIDER.upper()}  ({self.cfg.ai().get('model', '')})\n")
            f.write(f"AI plan         : {self.cfg.AI_PLAN.upper()}\n")
            f.write(f"Budget          : Rs.{budget:,.2f} (from Zerodha funds)\n")
            f.write(f"Universe        : {self.cfg.SCAN_UNIVERSE}\n")
            if strategy_profile:
                f.write(f"Strategy profile: {strategy_profile}\n")
            if market_condition:
                f.write(f"Market condition: {market_condition}\n")
            f.write(f"Max positions   : {self.cfg.MAX_POSITIONS}\n")
            f.write(f"Stop-loss       : {self.cfg.DEFAULT_STOP_LOSS_PCT}%\n")
            f.write(f"Target          : {self.cfg.DEFAULT_TARGET_PCT}%\n")
            f.write(f"Circuit breaker : {self.cfg.MAX_LOSS_PER_DAY_PCT}%\n\n")
            if research_phase["note"]:
                f.write("RESET NOTE\n")
                f.write(f"{self.SEP_MINOR}\n")
                f.write(f"{research_phase['note']}\n\n")

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
                f"{'SYMBOL':<16} {'SIDE':<6} {'QTY':>5} "
                f"{'ENTRY':>10} {'EXIT':>10} {'P&L':>12} "
                f"{'REASON':<14} {'ENTRY_T':<10} {'EXIT_T':<10}\n"
            )
            f.write(f"{self.SEP_TABLE}\n")

            for p in positions:
                exit_p  = f"Rs.{p['exit_price']:.2f}" if p.get("exit_price") else "—"
                total_pnl = p.get('pnl', 0) + p.get('_partial_pnl', 0)
                pnl_val = f"Rs.{total_pnl:+,.2f}" if p.get("exit_price") else "—"
                origin  = "[M] " if p.get("_external") else ""
                display_qty = p['qty'] + p.get('_partial_qty', 0)
                f.write(
                    f"{origin}{p['symbol']:<{16 - len(origin)}} {p['side']:<6} {display_qty:>5} "
                    f"Rs.{p['entry_price']:>9.2f} {exit_p:>10} {pnl_val:>12} "
                    f"{(p.get('exit_reason') or 'OPEN'):<14} "
                    f"{(p.get('entry_time') or '—'):<10} "
                    f"{(p.get('exit_time') or '—'):<10}\n"
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

            f.write(f"Gross P&L               : Rs.{pnl['gross_pnl']:+,.2f}\n\n")

            f.write("CHARGES & TAXES:\n")
            f.write(f"  Brokerage             : Rs.{charges['brokerage']:,.2f}\n")
            f.write(f"  STT (sell side)       : Rs.{charges['stt']:,.2f}\n")
            f.write(f"  Exchange transaction  : Rs.{charges['exchange_txn']:,.2f}\n")
            f.write(f"  GST (18%)             : Rs.{charges['gst']:,.2f}\n")
            f.write(f"  SEBI charges          : Rs.{charges['sebi_charges']:,.4f}\n")
            f.write(f"  Stamp duty (buy side) : Rs.{charges['stamp_duty']:,.2f}\n")
            f.write(f"  {'─' * 40}\n")
            f.write(f"  Total tax & charges   : Rs.{charges['total_tax_and_charges']:,.2f}\n\n")

            f.write(f"  {'─' * 40}\n")
            f.write(f"  Total all costs       : Rs.{charges['total_costs']:,.2f}\n\n")

            f.write(f"{'=' * 42}\n")
            f.write(f"  NET PROFIT AFTER ALL  : Rs.{pnl['net_profit']:+,.2f}\n")
            f.write(f"{'=' * 42}\n")
            profitable = "YES ✓" if pnl["is_profitable"] else "NO ✗"
            f.write(f"  Profitable?           : {profitable}\n")
            if budget > 0:
                returns_pct = pnl["net_profit"] / budget * 100
                f.write(f"  Day returns           : {returns_pct:+.2f}% on Rs.{budget:,.0f} budget\n")
            # AI API spend is informational only — NOT deducted from net.
            if charges.get("claude_api_cost", 0) > 0:
                f.write(
                    f"  FYI: {self.cfg.AI_PROVIDER.upper()} API est : "
                    f"Rs.{charges['claude_api_cost']:,.2f} (not deducted above)\n"
                )
            f.write("\n")

            # ── Estimated Income Tax ──────────────────────────────
            tax_rate_pct = pnl.get("tax_rate_pct", 0)
            estimated_tax = pnl.get("estimated_tax", 0)
            profit_after_tax = pnl.get("profit_after_tax", pnl["net_profit"])
            f.write("ESTIMATED INCOME TAX (speculative business income)\n")
            f.write(f"{self.SEP_MINOR}\n")
            f.write(f"  Tax slab rate         : {self.cfg.TAX_RATE_PCT}% + {self.cfg.TAX_CESS_PCT}% cess = {tax_rate_pct}% effective\n")
            if pnl["net_profit"] > 0:
                f.write(f"  Estimated tax         : Rs.{estimated_tax:,.2f}\n")
                f.write(f"  Profit after tax      : Rs.{profit_after_tax:+,.2f}\n")
            else:
                f.write(f"  Estimated tax         : Rs.0.00 (no tax on losses)\n")
                f.write(f"  Loss can be carried forward for 4 years (speculative only)\n")
            f.write("\n")

            f.write(f"  FYI: Zerodha Kite Connect subscription is Rs.{self.cfg.ZERODHA_MONTHLY_COST:,.0f}/month (not deducted above).\n")
            f.write(f"  Track cumulative daily profits to ensure they cover this monthly cost.\n\n")

            # ── Turnover Details ──────────────────────────────────
            f.write("TURNOVER DETAILS\n")
            f.write(f"{self.SEP_MINOR}\n")
            f.write(f"  Buy turnover          : Rs.{charges['buy_turnover']:,.2f}\n")
            f.write(f"  Sell turnover         : Rs.{charges['sell_turnover']:,.2f}\n")
            f.write(f"  Total turnover        : Rs.{charges['total_turnover']:,.2f}\n")
            f.write(f"  Total orders          : {charges['num_orders']}\n\n")

            # ── Chronological Trade Log ───────────────────────────
            f.write("CHRONOLOGICAL TRADE LOG\n")
            f.write(f"{self.SEP_MINOR}\n")
            for entry in trade_log:
                f.write(
                    f"  [{entry['time']}] {entry['action']:<14} "
                    f"{entry['symbol']:<12} {entry['side']:<5} "
                    f"{entry['qty']:>5}  Rs.{entry['price']:>10}  "
                    f"{entry['detail']}\n"
                )
            f.write("\n")

        # ── JSON data dump ────────────────────────────────────────
        payload = {
            "date":             str(today),
            "mode":             "dry_run" if dry_run else "live",
            "sessions":         session_count,
            "market_condition": market_condition,
            "research_phase": research_phase,
            "config": {
                "claude_plan":  self.cfg.CLAUDE_PLAN,
                "zerodha_plan": self.cfg.ZERODHA_PLAN,
                "budget":       budget,
                "universe":     self.cfg.SCAN_UNIVERSE,
                "max_positions": self.cfg.MAX_POSITIONS,
                "stop_loss_pct": self.cfg.DEFAULT_STOP_LOSS_PCT,
                "target_pct":    self.cfg.DEFAULT_TARGET_PCT,
                "strategy_profile": strategy_profile,
                "strategy_config_version": strategy_config_version,
                "strategy_config_hash": strategy_config_hash,
                "trade_stage_name": str(getattr(self.cfg, "TRADE_STAGE_NAME", "")),
                "git_sha":      _git_short_sha(),
            },
            "positions":  positions,
            "trade_log":  trade_log,
            "pnl":        pnl,
        }
        # Preserve sticky reconcile flags across session rewrites so
        # downstream scripts (import_zerodha_taxpnl, performance_tracker)
        # keep honouring the manual fix.
        if preserved_reconciled:
            payload["_reconciled"] = True
            if preserved_reconcile_note:
                payload["_reconciled_note"] = preserved_reconcile_note
        # Atomic write: serialise to a temp file in the same directory and
        # os.replace() into place. This guards the merge source against
        # corruption (and silent prior-session data loss) if the process is
        # killed mid-write.
        tmp_path = f"{json_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, json_path)

        self.log.success(f"Trading report : {txt_path}")
        self.log.success(f"Trading data   : {json_path}")

        # ── Auto-fill dry-run analysis ledger for simulated days ──
        dryrun_analysis_updated = False
        if dry_run:
            expected_closed = sum(
                1
                for p in positions
                if p.get("status") == "CLOSED"
                and str(p.get("order_id") or "").startswith("DRY_RUN")
            )
            try:
                from scripts.trade.fill_dryrun_analysis import fill_reports
                stats = fill_reports(date_from=str(today), date_to=str(today))
                dryrun_analysis_updated = True
                closed = int(stats.get("closed_positions", 0) or 0)
                inserted = int(stats.get("inserted", 0) or 0)
                skipped = int(stats.get("skipped", 0) or 0)
                self.log.info(
                    "Dry-run analysis DB: "
                    f"{closed} closed simulated trade(s), {inserted} inserted, "
                    f"{skipped} already present"
                )
                if expected_closed and closed < expected_closed:
                    self.log.warning(
                        "Dry-run analysis DB incomplete: "
                        f"report has {expected_closed} closed dry-run position(s), "
                        f"fill saw {closed}"
                    )
            except ModuleNotFoundError:
                # fill_dryrun_analysis was retired with the dry-run data
                # overhaul — DB auto-fill is intentionally unavailable.
                pass
            except Exception as e:
                self.log.warning(f"Dry-run analysis auto-fill skipped: {e}")

        # ── Auto-fill intraday tax ledger for live trading days ───
        if not dry_run:
            try:
                import sys as _sys, os as _os
                _scripts = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
    "scripts",
)
                if _scripts not in _sys.path:
                    _sys.path.insert(0, _os.path.dirname(_scripts))
                from scripts.trade.fill_intraday_ledger import fill_fy
                from shared.tax_db import current_fy
                n = fill_fy(current_fy())
                if n:
                    self.log.info(f"Tax ledger     : {n} trade(s) added to intraday_tax_ledger")
            except Exception as e:
                self.log.warning(f"Tax ledger auto-fill skipped: {e}")

        try:
            from scripts.trade.chan_daily_evidence import write_daily_evidence
            evidence = write_daily_evidence(
                str(today),
                "dryrun" if dry_run else "live",
                update_dbs=dry_run and not dryrun_analysis_updated,
            )
            self.log.info(
                f"Chan evidence  : {evidence['evidence_markdown_path']} "
                f"({evidence.get('status', 'UNKNOWN')})"
            )
        except Exception as e:
            self.log.warning(f"Chan evidence snapshot skipped: {e}")

        return txt_path

    # ================================================================
    # COMBINED P&L CALCULATION (for multi-session day reports)
    # ================================================================

    def _calculate_combined_pnl(self, all_positions: list[dict], claude_api_cost: float = 0.0) -> dict:
        """
        Recalculates P&L from a merged list of positions across
        multiple sessions. Delegates charge calculation to Config.
        """
        closed = [p for p in all_positions if p.get("status") == "CLOSED"]

        gross_pnl = sum(
            p.get("pnl", 0) + p.get("_partial_pnl", 0)
            for p in closed
        )

        total_buy_turnover  = 0.0
        total_sell_turnover = 0.0
        num_orders          = 0

        for p in closed:
            partial_qty = p.get("_partial_qty", 0)
            full_qty    = p.get("qty", 0) + partial_qty
            entry_value = p.get("entry_price", 0) * full_qty
            exit_value  = p.get("exit_price", 0)  * p.get("qty", 0)
            partial_exit_value = p.get("_partial_exit_price", p.get("entry_price", 0)) * partial_qty

            if p.get("side") == "BUY":
                total_buy_turnover  += entry_value
                total_sell_turnover += exit_value + partial_exit_value
            else:
                total_sell_turnover += entry_value
                total_buy_turnover  += exit_value + partial_exit_value

            num_orders += 2 + (1 if partial_qty > 0 else 0)

        # Reverse-calculate claude_calls from cost (for merged reports)
        claude_calls = int(claude_api_cost / self.cfg.CLAUDE_COST_PER_CALL) if self.cfg.CLAUDE_COST_PER_CALL > 0 else 0

        charges = self.cfg.calculate_charges(
            total_buy_turnover, total_sell_turnover,
            num_orders, claude_calls,
        )

        net = gross_pnl - charges["total_costs"]

        # Estimated tax liability (only on positive net profit)
        tax_rate = self.cfg.TAX_RATE_PCT * (1 + self.cfg.TAX_CESS_PCT / 100) / 100
        estimated_tax = round(net * tax_rate, 2) if net > 0 else 0.0
        profit_after_tax = round(net - estimated_tax, 2)

        return {
            "gross_pnl":         round(gross_pnl, 2),
            "charges":           charges,
            "net_profit":        round(net, 2),
            "is_profitable":     net > 0,
            "tax_rate_pct":      round(self.cfg.TAX_RATE_PCT * (1 + self.cfg.TAX_CESS_PCT / 100), 2),
            "estimated_tax":     estimated_tax,
            "profit_after_tax":  profit_after_tax,
        }

