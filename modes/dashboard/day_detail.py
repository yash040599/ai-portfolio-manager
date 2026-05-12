"""Per-day trade detail builder (Roadmap addendum 2026-04-23).

Backs the bar-chart drill-down: clicking a daily bar in the dashboard
hits `/api/day?date=YYYY-MM-DD`, which calls `day_detail()` here.

Joins two sources for one trading day:
  1. `intraday_tax_ledger` (DB)        — authoritative P&L, charges, qty.
  2. `reports/trading/.../trading_data_DD.json` — adds the *bot context*
     that's not in the DB: entry score, RSI at entry, exit score,
     pre-trade rationale. Matched on `order_id` (both sides record it).

DB is the source of truth for numbers; the JSON only enriches.
Missing JSON => trades still render, just without the extras.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import asdict, dataclass
from pathlib import Path

# Three .parent hops: day_detail.py -> dashboard/ -> modes/ -> root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR  = PROJECT_ROOT / "reports" / "trading"


@dataclass(frozen=True)
class TradeDetail:
    """Per-trade row shown in the drill-down panel."""
    symbol:         str
    side:           str
    qty:            int
    entry_price:    float
    exit_price:     float
    entry_time:     str | None
    exit_time:      str | None
    exit_reason:    str | None
    gross_pnl:      float
    total_charges:  float
    net_pnl:        float
    order_id:       str | None
    sheet_verified: str
    # Enrichments from the trading_data JSON (None if report missing).
    entry_score:    float | None
    entry_rsi:      float | None
    exit_score:     float | None
    rationale:      str | None


def _report_path(date: str) -> Path:
    d = datetime.date.fromisoformat(date)
    return REPORTS_DIR / f"{d.year:04d}" / f"{d.month:02d}" / f"trading_data_{d.day:02d}.json"


def _load_report_extras(date: str) -> dict[str, dict]:
    """Map order_id -> dict of bot-context extras. Empty if report missing."""
    p = _report_path(date)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict] = {}
    for pos in data.get("positions") or []:
        oid = pos.get("order_id")
        if not oid:
            continue
        out[str(oid)] = {
            "entry_score": pos.get("_entry_score"),
            "entry_rsi":   pos.get("_entry_rsi"),
            "exit_score":  pos.get("_exit_score"),
            "rationale":   pos.get("rationale"),
        }
    return out


def day_detail(date: str, *, include_provisional: bool = True) -> dict:
    """Return the JSON payload for a single trading day's detail panel."""
    # Validate (raises ValueError on bad input — caller turns into HTTP 400).
    datetime.date.fromisoformat(date)

    extras = _load_report_extras(date)

    # Direct query (rather than via data_layer.fetch_trades) because the
    # drill-down needs entry_time / exit_time / exit_reason / order_id
    # columns that TradeRow doesn't carry. Keeping this self-contained
    # avoids widening TradeRow and breaking other callers.
    from shared.tax_db import get_db  # local import keeps top-level lean
    trades: list[dict] = []
    conn = get_db()
    try:
        sql = (
            "SELECT symbol, side, qty, entry_price, exit_price, "
            "       entry_time, exit_time, exit_reason, "
            "       gross_pnl, total_charges, net_pnl, "
            "       order_id, sheet_verified "
            "FROM intraday_tax_ledger "
            "WHERE date = ?"
        )
        args: list = [date]
        if not include_provisional:
            sql += " AND sheet_verified = 'verified'"
        sql += " ORDER BY entry_time, id"
        for r in conn.execute(sql, args).fetchall():
            oid = r["order_id"]
            extra = extras.get(str(oid)) if oid else {}
            trades.append(asdict(TradeDetail(
                symbol         = r["symbol"],
                side           = r["side"],
                qty            = int(r["qty"]),
                entry_price    = float(r["entry_price"]),
                exit_price     = float(r["exit_price"]),
                entry_time     = r["entry_time"],
                exit_time      = r["exit_time"],
                exit_reason    = r["exit_reason"],
                gross_pnl      = float(r["gross_pnl"]),
                total_charges  = float(r["total_charges"]),
                net_pnl        = float(r["net_pnl"]),
                order_id       = oid,
                sheet_verified = r["sheet_verified"] or "pending",
                entry_score    = (extra or {}).get("entry_score"),
                entry_rsi      = (extra or {}).get("entry_rsi"),
                exit_score     = (extra or {}).get("exit_score"),
                rationale      = (extra or {}).get("rationale"),
            )))
    finally:
        conn.close()

    if not trades:
        return {
            "date":        date,
            "trade_count": 0,
            "gross_pnl":   0.0,
            "charges":     0.0,
            "net_pnl":     0.0,
            "winners":     0,
            "losers":      0,
            "report_found": _report_path(date).exists(),
            "trades":      [],
        }

    gross = sum(t["gross_pnl"]     for t in trades)
    chg   = sum(t["total_charges"] for t in trades)
    net   = sum(t["net_pnl"]       for t in trades)
    wins  = sum(1 for t in trades if t["net_pnl"] > 0)
    loss  = sum(1 for t in trades if t["net_pnl"] < 0)
    return {
        "date":         date,
        "trade_count":  len(trades),
        "gross_pnl":    round(gross, 2),
        "charges":      round(chg, 2),
        "net_pnl":      round(net, 2),
        "winners":      wins,
        "losers":       loss,
        "report_found": _report_path(date).exists(),
        "trades":       trades,
    }


__all__ = ["day_detail"]
