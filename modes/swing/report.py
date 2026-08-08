# ================================================================
# modes/swing/report.py
# ================================================================
# Swing report writer (SWING_ROADMAP S10).
#
# Writes:
#   reports/swing/<YYYY>/<MM>/swing_report_DD.txt
#   reports/swing/<YYYY>/<MM>/swing_data_DD.json
# ================================================================

from __future__ import annotations

import json
import os

from config import now_ist
from modes.swing.types import SwingRunResult


REPORT_DIR = os.path.join("reports", "swing")


def save_report(result: SwingRunResult) -> tuple[str, str]:
    """Write the human-readable report + JSON dump. Returns (txt_path, json_path)."""
    ts = now_ist()
    sub = os.path.join(REPORT_DIR, str(ts.year), f"{ts.month:02d}")
    os.makedirs(sub, exist_ok=True)

    txt_path = os.path.join(sub, f"swing_report_{ts.day:02d}.txt")
    json_path = os.path.join(sub, f"swing_data_{ts.day:02d}.json")

    # ── Text report ─────────────────────────────────────────────

    lines: list[str] = []
    _sep = "=" * 68

    lines.append(_sep)
    lines.append(f"  Swing Report — {result.run_for_date}")
    lines.append(f"  Mode: {result.mode}  |  Universe: {result.universe}")
    lines.append(f"  Generated: {result.finished_at}")
    if result.blocked_reason:
        lines.append(f"  BLOCKED: {result.blocked_reason}")
    lines.append(_sep)

    # Open swing book
    open_pos = [p for p in result.positions if p.status == "OPEN"]
    lines.append("")
    lines.append(f"  OPEN SWING BOOK ({len(open_pos)} positions)")
    lines.append("-" * 68)
    if open_pos:
        for p in open_pos:
            lines.append(
                f"  {p.symbol:12s}  Qty {p.managed_qty:>5d}  "
                f"Entry Rs.{p.entry_price:>10,.2f}  "
                f"Stop Rs.{p.stop_price:>10,.2f}  "
                f"Action: {p.daily_action}"
            )
    else:
        lines.append("  (no open positions)")

    # New entry candidates
    accepted = [c for c in result.candidates if c.status == "ACCEPTED"]
    accepted.sort(key=lambda c: c.priority_rank)
    lines.append("")
    lines.append(f"  NEW ENTRY CANDIDATES ({len(accepted)} accepted)")
    lines.append("-" * 68)
    if accepted:
        lines.append(
            f"  {'#':>3s}  {'Symbol':12s}  {'Setup':20s}  {'Score':>5s}  "
            f"{'Entry':>10s}  {'Stop':>10s}  {'Target':>10s}  "
            f"{'Qty':>5s}  {'R:R':>4s}"
        )
        for c in accepted:
            lines.append(
                f"  {c.priority_rank:>3d}  {c.symbol:12s}  {c.setup_type:20s}  "
                f"{c.score:>5.1f}  Rs.{c.entry_price:>9,.2f}  "
                f"Rs.{c.stop_price:>9,.2f}  Rs.{c.target_price:>9,.2f}  "
                f"{c.suggested_qty:>5d}  {c.rr_ratio:>4.1f}"
            )
    else:
        lines.append("  (no candidates qualified)")

    # Rejections (top 10)
    rejected = [c for c in result.candidates if c.status == "REJECTED"]
    lines.append("")
    lines.append(f"  REJECTIONS ({len(rejected)} total, showing top 10)")
    lines.append("-" * 68)
    for c in rejected[:10]:
        lines.append(f"  {c.symbol:12s}  {c.rejected_reason}")

    # Actions
    lines.append("")
    lines.append(f"  ACTIONS ({len(result.actions)})")
    lines.append("-" * 68)
    for a in result.actions:
        lines.append(
            f"  [{a.action_type:15s}] {a.symbol:12s}  "
            f"Qty {a.suggested_qty:>5d}  @ Rs.{a.suggested_price:>10,.2f}  "
            f"Status: {a.status}"
        )

    lines.append("")
    lines.append(_sep)

    txt_content = "\n".join(lines) + "\n"

    # ── JSON dump ───────────────────────────────────────────────

    json_data = {
        "run_id": result.run_id,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "mode": result.mode,
        "universe": result.universe,
        "run_for_date": result.run_for_date,
        "candidates": [c.to_dict() for c in result.candidates],
        "actions": [a.to_dict() for a in result.actions],
        "positions": [p.to_dict() for p in result.positions],
    }

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt_content)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, default=str)

    return txt_path, json_path
