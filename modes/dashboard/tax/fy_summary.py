"""Per-FY ledger aggregation — pure read of trades.db.

Pulls intraday + capital-gains numbers shaped to fit ITR-3 Schedule BP +
Schedule CG. Mirrors ``scripts/shared/tax_summary.py`` but returns a structured
dataclass instead of printing.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from shared.tax_db import current_fy, fy_date_range, fy_label, get_db


@dataclass(frozen=True)
class ChargeBreakdown:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_txn: float = 0.0
    gst: float = 0.0
    sebi: float = 0.0
    stamp_duty: float = 0.0
    total: float = 0.0


@dataclass(frozen=True)
class IntradaySummary:
    """Speculative business income — Section 43(5)."""
    trade_count: int = 0
    trading_days: int = 0
    verified_count: int = 0
    gross_pnl: float = 0.0
    charges: ChargeBreakdown = field(default_factory=ChargeBreakdown)
    net_pnl: float = 0.0                    # gross_pnl - charges.total
    speculative_turnover: float = 0.0       # absolute-sum method (Section 43(5))


@dataclass(frozen=True)
class CapitalGainsSummary:
    stcg_profit: float = 0.0
    stcg_loss: float = 0.0
    ltcg_profit: float = 0.0
    ltcg_loss: float = 0.0
    stcg_net: float = 0.0
    ltcg_net: float = 0.0
    stcg_trade_count: int = 0
    ltcg_trade_count: int = 0


@dataclass(frozen=True)
class FYSummary:
    fy_start: int
    fy_label: str
    window_from: str
    window_to: str
    intraday: IntradaySummary
    capital_gains: CapitalGainsSummary
    has_data: bool


def _empty_intraday() -> IntradaySummary:
    return IntradaySummary(charges=ChargeBreakdown())


def _empty_cg() -> CapitalGainsSummary:
    return CapitalGainsSummary()


def compute_fy_summary(fy_start: int | None = None) -> FYSummary:
    """Aggregate the intraday + capital-gains ledgers for a given FY."""

    if fy_start is None:
        fy_start = current_fy()
    fy_from, fy_to = fy_date_range(fy_start)

    conn = get_db()
    try:
        intraday_rows = conn.execute(
            "SELECT * FROM intraday_tax_ledger "
            "WHERE date>=? AND date<=? ORDER BY date, id",
            (fy_from, fy_to),
        ).fetchall()
        try:
            cg_rows = conn.execute(
                "SELECT * FROM capital_gains_ledger "
                "WHERE sell_date>=? AND sell_date<=? ORDER BY sell_date, id",
                (fy_from, fy_to),
            ).fetchall()
        except Exception:
            cg_rows = []
    finally:
        conn.close()

    if intraday_rows:
        gross    = sum(r["gross_pnl"]     for r in intraday_rows)
        brk      = sum(r["brokerage"]     for r in intraday_rows)
        stt      = sum(r["stt"]           for r in intraday_rows)
        exch     = sum(r["exchange_txn"]  for r in intraday_rows)
        gst      = sum(r["gst"]           for r in intraday_rows)
        sebi     = sum(r["sebi_charges"]  for r in intraday_rows)
        stamp    = sum(r["stamp_duty"]    for r in intraday_rows)
        chg_tot  = sum(r["total_charges"] for r in intraday_rows)
        net      = round(gross - chg_tot, 2)
        turnover = sum(abs(r["gross_pnl"]) for r in intraday_rows)
        days     = len({r["date"] for r in intraday_rows})
        verified = sum(1 for r in intraday_rows if r["verified"] == "verified")
        intraday = IntradaySummary(
            trade_count=len(intraday_rows),
            trading_days=days,
            verified_count=verified,
            gross_pnl=round(gross, 2),
            charges=ChargeBreakdown(
                brokerage=round(brk, 2),
                stt=round(stt, 2),
                exchange_txn=round(exch, 2),
                gst=round(gst, 2),
                sebi=round(sebi, 2),
                stamp_duty=round(stamp, 2),
                total=round(chg_tot, 2),
            ),
            net_pnl=net,
            speculative_turnover=round(turnover, 2),
        )
    else:
        intraday = _empty_intraday()

    if cg_rows:
        st_rows = [r for r in cg_rows if r["trade_type"] == "short_term"]
        lt_rows = [r for r in cg_rows if r["trade_type"] == "long_term"]
        st_profits = [r["realised_pnl"] for r in st_rows if r["realised_pnl"] > 0]
        st_losses  = [r["realised_pnl"] for r in st_rows if r["realised_pnl"] < 0]
        lt_profits = [r["realised_pnl"] for r in lt_rows if r["realised_pnl"] > 0]
        lt_losses  = [r["realised_pnl"] for r in lt_rows if r["realised_pnl"] < 0]
        cg = CapitalGainsSummary(
            stcg_profit=round(sum(st_profits), 2),
            stcg_loss=round(sum(st_losses), 2),
            ltcg_profit=round(sum(lt_profits), 2),
            ltcg_loss=round(sum(lt_losses), 2),
            stcg_net=round(sum(r["realised_pnl"] for r in st_rows), 2),
            ltcg_net=round(sum(r["realised_pnl"] for r in lt_rows), 2),
            stcg_trade_count=len(st_rows),
            ltcg_trade_count=len(lt_rows),
        )
    else:
        cg = _empty_cg()

    return FYSummary(
        fy_start=fy_start,
        fy_label=fy_label(fy_start),
        window_from=fy_from,
        window_to=fy_to,
        intraday=intraday,
        capital_gains=cg,
        has_data=bool(intraday_rows or cg_rows),
    )


__all__ = [
    "ChargeBreakdown",
    "IntradaySummary",
    "CapitalGainsSummary",
    "FYSummary",
    "compute_fy_summary",
]
