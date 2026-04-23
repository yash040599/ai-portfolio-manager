"""Capital-ladder traffic-light verdict (Roadmap D1, expanded in D6).

D1 ships the minimum viable verdict using only headline P&L:
  - GREEN   if net_pnl > 0 AND trades >= MIN_SAMPLE
  - AMBER   if net_pnl > 0 BUT trades < MIN_SAMPLE  (sample too small)
  - RED     if net_pnl <= 0
  - GREY    if no trades in window

D6 will plug the full criteria (win rate, profit factor, max DD,
weeks-required) once those metrics land in `metrics.py`. The shape
returned here is forward-compatible with that expansion — D6 just
adds more `failed_thresholds` entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from Dashboard.metrics import HeadlinePnL

Verdict = Literal["GREEN", "AMBER", "RED", "GREY"]

# D1: a baseline so a single profitable trade doesn't trigger GREEN.
# D6 supersedes this with per-rung `weeks_required` etc.
MIN_SAMPLE_TRADES = 20


@dataclass(frozen=True)
class LadderRung:
    """One step on the capital-deployment ladder."""
    budget:            int
    win_rate_min:      float
    profit_factor_min: float
    max_dd_pct:        float
    weeks_required:    int


@dataclass(frozen=True)
class VerdictResult:
    verdict:            Verdict
    headline:           str
    current_budget:     int
    recommended_budget: int
    rationale:          str
    failed_thresholds:  list[str] = field(default_factory=list)


def find_current_rung(ladder: list[LadderRung], budget: int) -> LadderRung | None:
    """Largest rung whose `budget` is ≤ the live budget.

    Returns None if `ladder` is empty or every rung sits above the
    current budget (e.g. budget set below the smallest configured
    rung — caller renders GREY with a "configure ladder" hint).
    """
    if not ladder:
        return None
    eligible = [r for r in ladder if r.budget <= budget]
    if not eligible:
        return None
    return max(eligible, key=lambda r: r.budget)


def find_next_rung(ladder: list[LadderRung], current: LadderRung) -> LadderRung | None:
    """Next rung above `current`, or None when already at the top."""
    above = [r for r in ladder if r.budget > current.budget]
    if not above:
        return None
    return min(above, key=lambda r: r.budget)


def verdict_for(
    headline: HeadlinePnL,
    *,
    ladder: list[LadderRung],
    budget: int,
    min_sample: int = MIN_SAMPLE_TRADES,
) -> VerdictResult:
    """Compute the traffic-light verdict.

    Pure function — takes the already-aggregated headline metrics and
    the configured ladder + live budget. No DB / config imports here so
    it stays trivially unit-testable.
    """
    current = find_current_rung(ladder, budget)
    next_   = find_next_rung(ladder, current) if current else None
    rec_budget = (next_.budget if next_ else current.budget) if current else budget

    if headline.trade_count == 0:
        return VerdictResult(
            verdict            = "GREY",
            headline           = "INSUFFICIENT DATA",
            current_budget     = budget,
            recommended_budget = budget,
            rationale          = "No sheet-verified trades in the selected window.",
        )

    failed: list[str] = []
    if headline.net_pnl <= 0:
        failed.append(f"net P&L = Rs.{headline.net_pnl:,.0f} (≤ 0)")
    if headline.trade_count < min_sample:
        failed.append(
            f"sample size = {headline.trade_count} (< {min_sample} required)"
        )

    if headline.net_pnl > 0 and headline.trade_count >= min_sample:
        v: Verdict = "GREEN"
        if current is None:
            head = "PROFITABLE — BELOW SMALLEST LADDER RUNG"
            rationale = (
                f"Net P&L Rs.{headline.net_pnl:,.0f} over "
                f"{headline.trade_count} trades / {headline.trading_days} days. "
                f"Current budget Rs.{budget:,} is below the smallest configured "
                "rung — set MAX_BUDGET_INR to a ladder rung or extend "
                "Config.CAPITAL_LADDER to scale."
            )
        else:
            head = "READY TO SCALE" if next_ else "AT TOP RUNG — HOLD"
            rationale = (
                f"Net P&L Rs.{headline.net_pnl:,.0f} over "
                f"{headline.trade_count} trades / {headline.trading_days} days. "
                "D6 will tighten this once win-rate / profit-factor / DD land."
            )
    elif headline.net_pnl > 0:
        v = "AMBER"
        head = "PROFITABLE BUT SAMPLE TOO SMALL"
        rationale = "; ".join(failed)
        rec_budget = current.budget if current else budget
    else:
        v = "RED"
        head = "NOT READY"
        rationale = "; ".join(failed)
        rec_budget = current.budget if current else budget

    return VerdictResult(
        verdict            = v,
        headline           = head,
        current_budget     = budget,
        recommended_budget = rec_budget,
        rationale          = rationale,
        failed_thresholds  = failed,
    )


__all__ = [
    "LadderRung",
    "VerdictResult",
    "find_current_rung",
    "find_next_rung",
    "verdict_for",
    "MIN_SAMPLE_TRADES",
]
