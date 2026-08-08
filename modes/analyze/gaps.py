# ================================================================
# modes/analyze/gaps.py
# ================================================================
# "What's missing" engine for the Portfolio Analyser (ANALYZE_ROADMAP P7).
#
# Compares the user's holdings against:
#   - NIFTY100 sector-weight benchmark (data/benchmark_sector_weights.json)
#   - approved candidate pool          (data/analyse_candidates.json)
#   - promoter-group map               (data/promoter_groups.json)
#
# Surfaces four kinds of flag:
#   - UNDER_ALLOCATED   sector weight > GAP_PP_THRESHOLD below benchmark
#   - MISSING_DEFENSIVE no FMCG / no PHARMA in a cyclically-tilted book
#   - CONCENTRATION     single-name > SINGLE_NAME_MAX_PCT
#   - GROUP_RISK        group weight > GROUP_MAX_PCT
#
# AI mode (P4) adds a rationale paragraph per Suggestion. NoAI mode
# emits the bare list with the gap framing only.
# ================================================================

from __future__ import annotations


from modes.analyze.enrich_noai import load_reference_data
from modes.analyze.types import (
    GapAnalysis,
    GapFlag,
    PortfolioMetrics,
    StockAnalysis,
)


# ── Tunable thresholds ──────────────────────────────────────────

GAP_PP_THRESHOLD          = 5.0    # underweight by ≥ this many percentage points → flag
SINGLE_NAME_MAX_PCT       = 25.0   # single-stock concentration risk above this
GROUP_MAX_PCT             = 30.0   # promoter-group concentration risk above this
DEFENSIVE_MIN_PCT         = 10.0   # combined FMCG+PHARMA must clear this
CYCLICAL_TILT_PCT         = 60.0   # books over this much cyclical → defensive flag
DEFENSIVE_SECTORS         = {"FMCG", "PHARMA"}
CYCLICAL_SECTORS          = {"AUTO", "METALS", "ENERGY", "INFRA", "CAPGOODS"}
HELD_SUGGESTION_BLOCKLIST = True   # never suggest a stock the user already holds

# Scorecard-driven checks (2026-07-31). Both use the per-stock rating /
# risk grade produced by modes/analyze/scoring.py.
HIGH_RISK_BUCKET_MAX_PCT  = 35.0   # weight in HIGH + VERY HIGH risk names
WEAK_RATING_BUCKET_MAX_PCT = 25.0  # weight in REDUCE + SELL rated names
WEAK_RATINGS              = {"REDUCE", "SELL"}
HIGH_RISK_GRADES          = {"HIGH", "VERY HIGH"}


# ── Public entry ───────────────────────────────────────────────

def analyse_gaps(
    holdings: list[StockAnalysis],
    metrics: PortfolioMetrics,
) -> GapAnalysis:
    """Run all gap checks. Pure-Python; reads only seed JSON files."""
    refs = load_reference_data()
    benchmark: dict[str, float]            = refs["benchmark"]
    candidates: dict[str, list[dict]]      = refs["candidates"]
    benchmark_label                        = (
        f"NIFTY100 sector benchmark "
        f"(as of {refs['benchmark_as_of'].strftime('%Y-%m-%d')})"
    )

    held_symbols = {h.symbol for h in holdings}
    flags: list[GapFlag] = []

    # ── Sector under-allocation ──
    user_weights = {sw.sector: sw.weight_pct for sw in metrics.sector_weights}
    for sector, bench_pct in sorted(benchmark.items(), key=lambda kv: -kv[1]):
        if sector == "OTHER":
            continue
        held_pct = user_weights.get(sector, 0.0)
        gap = bench_pct - held_pct
        if gap >= GAP_PP_THRESHOLD:
            sugg = _pick_suggestions(candidates.get(sector, []), held_symbols)
            severity = "WARN" if held_pct == 0 else "INFO"
            flags.append(GapFlag(
                severity=severity,
                category="UNDER_ALLOCATED",
                headline=(
                    f"{sector} under-allocated: "
                    f"{held_pct:.1f}% vs benchmark {bench_pct:.1f}% "
                    f"(gap {gap:+.1f}pp)"
                ),
                detail=(
                    f"Your portfolio holds {held_pct:.1f}% in {sector} vs "
                    f"the NIFTY100 benchmark of {bench_pct:.1f}%. A long-term "
                    f"book that under-weights {sector} by {gap:.1f}pp is "
                    f"making an implicit bet against the index. Consider "
                    f"rebalancing toward benchmark over the next 2-3 quarters."
                ),
                suggested_symbols=[s["symbol"] for s in sugg],
            ))

    # ── Defensive ballast check ──
    defensive_pct = sum(
        sw.weight_pct for sw in metrics.sector_weights
        if sw.sector in DEFENSIVE_SECTORS
    )
    cyclical_pct = sum(
        sw.weight_pct for sw in metrics.sector_weights
        if sw.sector in CYCLICAL_SECTORS
    )
    if defensive_pct < DEFENSIVE_MIN_PCT and cyclical_pct >= CYCLICAL_TILT_PCT:
        sugg_fmcg   = _pick_suggestions(candidates.get("FMCG", []), held_symbols, n=2)
        sugg_pharma = _pick_suggestions(candidates.get("PHARMA", []), held_symbols, n=2)
        suggestions = [s["symbol"] for s in (sugg_fmcg + sugg_pharma)]
        flags.append(GapFlag(
            severity="WARN",
            category="MISSING_DEFENSIVE",
            headline=(
                f"Defensive ballast missing — only {defensive_pct:.1f}% "
                f"in FMCG+PHARMA against {cyclical_pct:.1f}% cyclicals"
            ),
            detail=(
                f"Books with > {CYCLICAL_TILT_PCT:.0f}% in cyclicals (auto, "
                f"metals, energy, infra, capgoods) need at least "
                f"{DEFENSIVE_MIN_PCT:.0f}% in defensive sectors (FMCG, "
                f"pharma) to dampen drawdown in earnings-recession years. "
                f"Add 1-2 names from the suggested list over the next quarter."
            ),
            suggested_symbols=suggestions,
        ))

    # ── Single-name concentration ──
    single_max  = (metrics.single_name_max_pct.value or 0.0) if metrics.single_name_max_pct else 0.0
    single_sym  = (metrics.single_name_max_symbol.value or "") if metrics.single_name_max_symbol else ""
    if single_max >= SINGLE_NAME_MAX_PCT and single_sym:
        flags.append(GapFlag(
            severity="RISK",
            category="CONCENTRATION",
            headline=f"Single-name concentration: {single_sym} at {single_max:.1f}% of portfolio",
            detail=(
                f"{single_sym} represents {single_max:.1f}% of your book — "
                f"above the {SINGLE_NAME_MAX_PCT:.0f}% rule of thumb for a "
                f"diversified long-term portfolio. A single adverse event in "
                f"one stock can dent the entire portfolio P&L. Consider "
                f"trimming over time toward 15-20% of the book."
            ),
            suggested_symbols=[],
        ))

    # ── Group concentration ──
    if metrics.group_concentration and isinstance(metrics.group_concentration.value, dict):
        for group, weight in metrics.group_concentration.value.items():
            if weight >= GROUP_MAX_PCT:
                flags.append(GapFlag(
                    severity="RISK",
                    category="GROUP_RISK",
                    headline=f"{group} group concentration: {weight:.1f}% of portfolio",
                    detail=(
                        f"You hold {weight:.1f}% across {group} group "
                        f"companies. Promoter-group risk is real — a single "
                        f"governance / regulatory event can hit every name "
                        f"simultaneously. Cap group exposure at "
                        f"{GROUP_MAX_PCT:.0f}% as a long-term rule."
                    ),
                    suggested_symbols=[],
                ))

    # ── Cash drag (P8) ──
    if metrics.cash_drag_pct and metrics.cash_drag_pct.value is not None:
        try:
            from config import Config as _Cfg
            cd_thresh = float(getattr(_Cfg, "CASH_DRAG_FLAG_PCT", 25.0))
        except Exception:
            cd_thresh = 25.0
        if metrics.cash_drag_pct.value > cd_thresh:
            cash_inr = (metrics.cash_balance.value
                        if metrics.cash_balance else 0) or 0
            flags.append(GapFlag(
                severity="WARN",
                category="CASH_DRAG",
                headline=(
                    f"Cash drag: {metrics.cash_drag_pct.value:.1f}% "
                    f"of total account value sitting idle "
                    f"(Rs.{cash_inr:,.0f})"
                ),
                detail=(
                    f"Idle cash above {cd_thresh:.0f}% of total account "
                    f"value drags long-term returns — equity compounds, "
                    f"cash erodes against inflation. Either deploy into "
                    f"the suggested under-allocated sectors below, or "
                    f"park in a liquid/short-duration debt fund. The "
                    f"analyser does not place orders for you; this is a "
                    f"prompt for a decision, not an instruction."
                ),
                suggested_symbols=[],
            ))

    # ── Risk-bucket concentration (2026-07-31) ──
    # Sector and single-name limits say nothing about *what kind* of
    # names you hold. A book that is well diversified across sectors but
    # entirely in high-beta, high-drawdown small caps is still fragile.
    risk_bucket = _weight_where(
        holdings, lambda h: _grade(h, "rule_risk_grade") in HIGH_RISK_GRADES)
    if risk_bucket["weight"] > HIGH_RISK_BUCKET_MAX_PCT:
        flags.append(GapFlag(
            severity="RISK",
            category="RISK_CONCENTRATION",
            headline=(
                f"{risk_bucket['weight']:.1f}% of the book sits in HIGH or "
                f"VERY HIGH risk names ({risk_bucket['count']} holdings)"
            ),
            detail=(
                f"Risk grade blends annualised volatility, one-year max "
                f"drawdown, beta, downside capture, market-cap tier and "
                f"traded liquidity. Above {HIGH_RISK_BUCKET_MAX_PCT:.0f}% in "
                f"the top two buckets the portfolio's drawdown in a market "
                f"correction will materially exceed the index. Names: "
                f"{', '.join(risk_bucket['symbols'][:8])}"
                + (" ..." if len(risk_bucket["symbols"]) > 8 else "")
                + ". Trim the weakest-rated of these first, or offset with "
                  "large-cap defensives."
            ),
            suggested_symbols=[],
        ))

    # ── Weak-rating concentration (2026-07-31) ──
    weak = _weight_where(
        holdings, lambda h: _grade(h, "rule_rating") in WEAK_RATINGS)
    if weak["weight"] > WEAK_RATING_BUCKET_MAX_PCT:
        flags.append(GapFlag(
            severity="WARN",
            category="WEAK_RATINGS",
            headline=(
                f"{weak['weight']:.1f}% of the book is rated REDUCE or SELL "
                f"({weak['count']} holdings)"
            ),
            detail=(
                f"The six-pillar scorecard puts these names in the bottom two "
                f"bands on trend, momentum, quality and valuation combined: "
                f"{', '.join(weak['symbols'][:8])}"
                + (" ..." if len(weak["symbols"]) > 8 else "")
                + ". Carrying more than "
                  f"{WEAK_RATING_BUCKET_MAX_PCT:.0f}% in low-rated positions is "
                  "usually inertia rather than a decision. Review each one "
                  "against its original thesis and either add conviction or cut."
            ),
            suggested_symbols=[],
        ))

    # Sort flags by severity (RISK > WARN > INFO) for the report.
    sev_rank = {"RISK": 0, "WARN": 1, "INFO": 2}
    flags.sort(key=lambda f: sev_rank.get(f.severity, 99))

    return GapAnalysis(flags=flags, benchmark_label=benchmark_label)


# ── Helpers ────────────────────────────────────────────────────

def _grade(holding: StockAnalysis, attr: str) -> str:
    """Read a scorecard Field off a holding, tolerating older snapshots
    that pre-date the scorecard entirely."""
    f = getattr(holding, attr, None)
    if f is None or getattr(f, "value", None) is None:
        return ""
    return str(f.value).upper()


def _weight_where(holdings: list[StockAnalysis], predicate) -> dict:
    """Total portfolio weight, count and symbols matching `predicate`."""
    symbols: list[str] = []
    weight = 0.0
    for h in holdings:
        try:
            if not predicate(h):
                continue
        except Exception:
            continue
        symbols.append(h.symbol)
        w = getattr(h, "weight_in_portfolio_pct", None)
        if w is not None and getattr(w, "value", None) is not None:
            try:
                weight += float(w.value)
            except (TypeError, ValueError):
                pass
    # Heaviest first so the truncated list in the flag names the ones
    # that actually matter.
    symbols.sort(
        key=lambda s: next(
            (float(getattr(h.weight_in_portfolio_pct, "value", 0) or 0)
             for h in holdings if h.symbol == s), 0.0),
        reverse=True,
    )
    return {"weight": weight, "count": len(symbols), "symbols": symbols}

def _pick_suggestions(pool: list[dict], held: set[str],
                      n: int = 3) -> list[dict]:
    """Return up to n candidates from `pool` that the user does NOT
    already hold. Preserves the order in the seed file (so the operator
    controls priority by re-ordering the JSON)."""
    out: list[dict] = []
    for item in pool:
        sym = item.get("symbol")
        if not sym:
            continue
        if HELD_SUGGESTION_BLOCKLIST and sym in held:
            continue
        out.append(item)
        if len(out) >= n:
            break
    return out
