# ================================================================
# modes/options/report_writer.py
# ================================================================
# End-of-day option trading report generator.
# Writes human-readable .txt and machine-readable .json under
# reports/options/<year>/<month>/.
#
# Mirrors the equity ReportWriter pattern.
# ================================================================

import os
import json
import datetime

from config      import Config, now_ist
from core.logger import Logger


class OptionsReportWriter:

    def __init__(self, config: type[Config], log: Logger):
        self.cfg = config
        self.log = log

    # ================================================================
    # PUBLIC: save report
    # ================================================================

    def save(
        self,
        positions: list[dict],
        market_condition: str,
        india_vix: float,
        nifty_close: float,
        summary_stats: dict,
    ):
        """Generate and save end-of-day options report."""
        today = now_ist()
        txt_path = self._report_path(today, "txt")
        json_path = self._report_path(today, "json")

        os.makedirs(os.path.dirname(txt_path), exist_ok=True)

        # ── Text report ───────────────────────────────────────────
        lines = self._build_text_report(
            today, positions, market_condition, india_vix,
            nifty_close, summary_stats,
        )
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self.log.info(f"Options report saved: {txt_path}")

        # ── JSON data ─────────────────────────────────────────────
        data = self._build_json_data(
            today, positions, market_condition, india_vix,
            nifty_close, summary_stats,
        )
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        self.log.info(f"Options data saved: {json_path}")

    # ================================================================
    # INTERNAL: paths
    # ================================================================

    def _report_path(self, dt: datetime.datetime, ext: str) -> str:
        year = dt.strftime("%Y")
        month = dt.strftime("%m_%B")
        day = dt.strftime("%d")
        filename = f"options_{'report' if ext == 'txt' else 'data'}_{day}.{ext}"
        return os.path.join("reports", "options", year, month, filename)

    # ================================================================
    # INTERNAL: text report
    # ================================================================

    def _build_text_report(
        self,
        dt: datetime.datetime,
        positions: list[dict],
        market_condition: str,
        india_vix: float,
        nifty_close: float,
        summary: dict,
    ) -> list[str]:
        lines = []
        lines.append("=" * 70)
        lines.append(f"  OPTIONS TRADING REPORT — {dt.strftime('%Y-%m-%d %A')}")
        lines.append("=" * 70)
        lines.append("")

        # ── Config ────────────────────────────────────────────────
        dry = "YES" if self.cfg.OPTIONS_DRY_RUN else "NO"
        lines.append(f"  Mode:     {'DRY RUN' if self.cfg.OPTIONS_DRY_RUN else 'LIVE'}")
        lines.append(f"  Budget:   Rs.{self.cfg.OPTIONS_BUDGET_INR:,}")
        lines.append(f"  Max lots: {self.cfg.OPTIONS_MAX_LOTS}")
        lines.append(f"  Index:    {self.cfg.OPTIONS_INDEX}")
        lines.append(f"  Strategy: Directional Buying (Phase O-4)")
        lines.append("")

        # ── Market context ────────────────────────────────────────
        lines.append(f"  NIFTY close: {nifty_close:,.2f}")
        lines.append(f"  India VIX:   {india_vix:.2f}")
        lines.append(f"  Condition:   {market_condition}")
        lines.append("")

        # ── Summary ───────────────────────────────────────────────
        closed = [p for p in positions if p.get("status") == "CLOSED"]
        total_pnl = sum(p.get("pnl", 0) for p in closed)
        wins = [p for p in closed if p.get("pnl", 0) > 0]
        losses = [p for p in closed if p.get("pnl", 0) < 0]

        lines.append(f"  Trades:    {len(closed)}")
        lines.append(f"  Wins:      {len(wins)}")
        lines.append(f"  Losses:    {len(losses)}")
        wr = round(len(wins) / len(closed) * 100, 1) if closed else 0
        lines.append(f"  Win rate:  {wr}%")
        lines.append(f"  Day P&L:   Rs.{total_pnl:+,.2f}")
        lines.append("")

        # ── Per-trade details ─────────────────────────────────────
        if closed:
            lines.append("-" * 70)
            lines.append(
                f"  {'Symbol':<25} {'Type':<4} {'Strike':>7} "
                f"{'Entry':>8} {'Exit':>8} {'P&L':>10} {'Reason':<10}"
            )
            lines.append("-" * 70)
            for p in closed:
                icon = "✓" if p.get("pnl", 0) >= 0 else "✗"
                pnl_val = p.get("pnl", 0)
                pnl_str = f"Rs.{pnl_val:+,.2f}"
                lines.append(
                    f"  {icon} {p.get('symbol', ''):<23} "
                    f"{p.get('option_type', ''):<4} "
                    f"{p.get('strike', 0):>7} "
                    f"{p.get('entry_premium', 0):>8.2f} "
                    f"{p.get('exit_premium', 0):>8.2f} "
                    f"{pnl_str:>10} "
                    f"{p.get('exit_reason', ''):<10}"
                )
            lines.append("-" * 70)
        else:
            lines.append("  No trades today.")

        lines.append("")
        lines.append("=" * 70)
        return lines

    # ================================================================
    # INTERNAL: JSON data
    # ================================================================

    def _build_json_data(
        self,
        dt: datetime.datetime,
        positions: list[dict],
        market_condition: str,
        india_vix: float,
        nifty_close: float,
        summary: dict,
    ) -> dict:
        closed = [p for p in positions if p.get("status") == "CLOSED"]
        total_pnl = sum(p.get("pnl", 0) for p in closed)

        return {
            "date": dt.strftime("%Y-%m-%d"),
            "mode": "options",
            "dry_run": self.cfg.OPTIONS_DRY_RUN,
            "strategy": "directional_buying_v1",
            "index": self.cfg.OPTIONS_INDEX,
            "budget": self.cfg.OPTIONS_BUDGET_INR,
            "nifty_close": nifty_close,
            "india_vix": india_vix,
            "market_condition": market_condition,
            "trade_count": len(closed),
            "day_pnl": round(total_pnl, 2),
            "summary": summary,
            "trades": [
                {
                    "symbol": p.get("symbol"),
                    "option_type": p.get("option_type"),
                    "strike": p.get("strike"),
                    "expiry": str(p.get("expiry", "")),
                    "side": p.get("side"),
                    "lots": p.get("lots"),
                    "qty": p.get("qty"),
                    "entry_premium": p.get("entry_premium"),
                    "exit_premium": p.get("exit_premium"),
                    "stop_loss": p.get("stop_loss"),
                    "target": p.get("target"),
                    "pnl": p.get("pnl"),
                    "exit_reason": p.get("exit_reason"),
                    "entry_time": p.get("entry_time"),
                    "exit_time": p.get("exit_time"),
                    "nifty_price": p.get("nifty_price"),
                    "nifty_trend": p.get("nifty_trend"),
                    "india_vix": p.get("india_vix"),
                    "regime": p.get("regime"),
                    "rationale": p.get("rationale"),
                }
                for p in closed
            ],
        }
