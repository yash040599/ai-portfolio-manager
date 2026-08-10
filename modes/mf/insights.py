# ================================================================
# modes/mf/insights.py
# ================================================================
# Deterministic analysis for the mutual-fund book.
#
# Everything here runs on `MFSchemeRollup` rows, which are already
# merged across Coin AND externally-tracked brokers. That is
# deliberate: a fund you hold in two places is one economic position,
# so every finding below counts it once, at its combined weight.
#
# Design stance for a long-horizon book:
#   * No buy/sell calls. Ranking funds on trailing return is mostly a
#     measure of WHEN you bought, not what to do next.
#   * Findings are structural — redundancy, drift, concentration, and
#     the tax cost of fixing them. Those are the decisions a
#     buy-and-hold investor actually faces.
#   * Anything we cannot compute honestly is left out rather than
#     approximated (see `holding_period_known` on the tax view).
# ================================================================

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field

from modes.mf.book import asset_class, plan_kind
from modes.mf.persistence import nav_history_meta, nav_series
from modes.mf.types import MFBook, MFSchemeRollup
from shared.quant_metrics import (
    annualised_volatility_pct, max_drawdown_pct, sharpe_ratio,
)


# LTCG on equity-oriented funds is exempt up to this much per FY
# (Budget 2024 regime, mirrored in modes/dashboard/tax/slabs.py).
LTCG_EXEMPTION_INR = 1_25_000.0

# Two funds whose daily NAVs move together this tightly are one bet
# however different their names are.
CORRELATION_REDUNDANT = 0.90

# Below this, a position is too small to matter but still costs
# attention at every review and every tax filing.
SUB_SCALE_PCT = 2.0


# ── Exposure classification ─────────────────────────────────────
# Ordered most-specific first: "SBI PSU BANK INDEX" must land in PSU,
# not in the generic BANKING or INDEX bucket.
_EXPOSURE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Gold & silver",        ("GOLD", "SILVER", "PRECIOUS")),
    ("US equity",            ("NASDAQ", "U.S.", "US OPPORTUNITIES", "AMERICA")),
    ("International (ex-US)", ("INTERNATIONAL", "GLOBAL", "EMERGING", "CHINA",
                               "JAPAN", "EUROPE")),
    ("PSU",                  ("PSU",)),
    ("Pharma & healthcare",  ("PHARMA", "HEALTHCARE", "HEALTH")),
    ("Technology",           ("TECHNOLOGY", "DIGITAL", " IT ", "INFOTECH")),
    ("Power & infrastructure", ("POWER", "INFRA", "ENERGY")),
    ("Banking & financials", ("BANK", "FINANCIAL", "FINANCE")),
    ("Consumption",          ("CONSUMPTION", "FMCG", "CONSUMER")),
    ("Large-cap index",      ("NIFTY 50", "NIFTY50", "SENSEX", "NIFTY NEXT",
                              "TOP 100", "LARGE CAP", "LARGECAP", "BLUECHIP")),
    ("Mid cap",              ("MID CAP", "MIDCAP")),
    ("Small cap",            ("SMALL CAP", "SMALLCAP")),
    ("ELSS (lock-in)",       ("ELSS", "TAX SAVER", "LONG TERM EQUITY")),
    ("Flexi / multi / focused", ("FLEXI", "MULTI CAP", "MULTICAP", "FOCUSED",
                                 "CONTRA", "VALUE FUND")),
    ("Arbitrage & hybrid",   ("ARBITRAGE", "BALANCED", "HYBRID", "ASSET ALLOC")),
    ("Debt",                 ("LIQUID", "GILT", "DEBT", "BOND", "MONEY MARKET",
                              "CORPORATE BOND", "OVERNIGHT")),
)


def exposure_of(fund: str, scheme_type: str = "") -> str:
    """Bucket a scheme by what it is actually exposed to.

    Name-based because Kite's `scheme_type` is too coarse to tell a
    Nifty-50 tracker from a smallcap tracker — both are "Index Funds".
    """
    blob = f" {fund} {scheme_type} ".upper()
    for label, needles in _EXPOSURE_RULES:
        if any(n in blob for n in needles):
            return label
    return asset_class(scheme_type, fund).title()


# ── Result types ────────────────────────────────────────────────

@dataclass
class Finding:
    """One structural observation. `severity` drives page ordering."""
    code: str
    severity: str        # REVIEW | NOTE | GOOD
    title: str
    detail: str
    value: float = 0.0   # rupees the finding concerns
    schemes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "severity": self.severity,
            "title": self.title, "detail": self.detail,
            "value": round(self.value, 2), "schemes": self.schemes,
        }


@dataclass
class Cluster:
    """Every scheme sharing one exposure."""
    label: str
    schemes: list[MFSchemeRollup]
    value: float
    weight_pct: float
    sip_amount: float

    @property
    def dormant(self) -> list[MFSchemeRollup]:
        return [s for s in self.schemes if not s.is_accumulating]

    @property
    def is_redundant(self) -> bool:
        return len(self.schemes) > 1

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "value": round(self.value, 2),
            "weight_pct": round(self.weight_pct, 2),
            "sip_amount": round(self.sip_amount, 2),
            "fund_count": len(self.schemes),
            "dormant_count": len(self.dormant),
            "is_redundant": self.is_redundant,
            "funds": [{
                "scheme_code": s.scheme_code,
                "fund": s.fund,
                "value": round(s.current_value, 2),
                "pnl": round(s.pnl, 2),
                "pnl_pct": round(s.pnl_pct, 2),
                "sip_amount": round(s.sip_amount, 2),
                "accumulating": s.is_accumulating,
                "brokers": s.brokers,
            } for s in sorted(self.schemes,
                              key=lambda x: x.current_value, reverse=True)],
        }


@dataclass
class RiskProfile:
    scheme_code: str
    fund: str
    points: int = 0
    cagr_1y: float | None = None
    cagr_3y: float | None = None
    cagr_5y: float | None = None
    volatility: float | None = None
    max_drawdown: float | None = None
    sharpe: float | None = None

    def to_dict(self) -> dict:
        return {
            "scheme_code": self.scheme_code, "fund": self.fund,
            "points": self.points,
            "cagr_1y": _r(self.cagr_1y), "cagr_3y": _r(self.cagr_3y),
            "cagr_5y": _r(self.cagr_5y), "volatility": _r(self.volatility),
            "max_drawdown": _r(self.max_drawdown), "sharpe": _r(self.sharpe),
        }


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


# ── Accumulation vs dormant corpus ──────────────────────────────

def accumulation_view(book: MFBook) -> dict:
    """Split the book into what still receives money and what does not.

    This is the honest frame for a book whose owner has deliberately
    narrowed their SIPs: dormant is not neglected, it is simply the
    corpus the plan no longer adds to.
    """
    schemes = book.schemes
    total = sum(s.current_value for s in schemes)
    funded = [s for s in schemes if s.is_accumulating]
    dormant = [s for s in schemes if not s.is_accumulating]
    monthly = sum(s.sip_amount for s in schemes)
    annual = monthly * 12

    return {
        "monthly_inflow": round(monthly, 2),
        "annual_inflow": round(annual, 2),
        "inflow_rate_pct": round(annual / total * 100, 2) if total > 0 else 0.0,
        "funded_count": len(funded),
        "dormant_count": len(dormant),
        "funded_value": round(sum(s.current_value for s in funded), 2),
        "dormant_value": round(sum(s.current_value for s in dormant), 2),
        "dormant_pct": round(
            sum(s.current_value for s in dormant) / total * 100, 2)
            if total > 0 else 0.0,
        "funded": [{
            "scheme_code": s.scheme_code, "fund": s.fund,
            "value": round(s.current_value, 2),
            "sip_amount": round(s.sip_amount, 2),
            "sip_share_pct": round(s.sip_amount / monthly * 100, 2)
                             if monthly > 0 else 0.0,
            "exposure": exposure_of(s.fund, s.scheme_type),
        } for s in sorted(funded, key=lambda x: x.sip_amount, reverse=True)],
        "corpus_vs_inflow": _corpus_vs_inflow(schemes, total, monthly),
    }


def _corpus_vs_inflow(schemes: list[MFSchemeRollup], total: float,
                      monthly: float) -> list[dict]:
    """Where the corpus sits vs where new money is going, by asset class."""
    corpus: dict[str, float] = {}
    inflow: dict[str, float] = {}
    for s in schemes:
        cls = asset_class(s.scheme_type, s.fund)
        corpus[cls] = corpus.get(cls, 0.0) + s.current_value
        if s.sip_amount > 0:
            inflow[cls] = inflow.get(cls, 0.0) + s.sip_amount

    rows = []
    for cls in sorted(set(corpus) | set(inflow)):
        cp = corpus.get(cls, 0.0) / total * 100 if total > 0 else 0.0
        ip = inflow.get(cls, 0.0) / monthly * 100 if monthly > 0 else 0.0
        rows.append({
            "label": cls,
            "corpus_pct": round(cp, 2),
            "inflow_pct": round(ip, 2),
            "gap": round(ip - cp, 2),
        })
    rows.sort(key=lambda r: r["corpus_pct"], reverse=True)
    return rows


# ── Exposure clusters ───────────────────────────────────────────

def exposure_clusters(book: MFBook) -> list[Cluster]:
    total = sum(s.current_value for s in book.schemes)
    grouped: dict[str, list[MFSchemeRollup]] = {}
    for s in book.schemes:
        grouped.setdefault(exposure_of(s.fund, s.scheme_type), []).append(s)

    out = []
    for label, rows in grouped.items():
        value = sum(r.current_value for r in rows)
        out.append(Cluster(
            label=label, schemes=rows, value=value,
            weight_pct=(value / total * 100) if total > 0 else 0.0,
            sip_amount=sum(r.sip_amount for r in rows),
        ))
    out.sort(key=lambda c: c.value, reverse=True)
    return out


# ── NAV-series analytics ────────────────────────────────────────

def _closes(series: list[tuple[str, float]]) -> list[float]:
    return [nav for _, nav in series]


def _cagr(series: list[tuple[str, float]], years: int) -> float | None:
    """Annualised return over `years`, anchored on dates not bar counts.

    NAV series skip market holidays unevenly, so counting bars would
    quietly drift the window by weeks over a 5-year lookback.
    """
    if len(series) < 30:
        return None
    end_date = datetime.date.fromisoformat(series[-1][0])
    target = end_date - datetime.timedelta(days=365 * years)
    start = next((p for p in series if datetime.date.fromisoformat(p[0]) >= target),
                 None)
    if start is None or start[1] <= 0:
        return None
    actual_years = (end_date - datetime.date.fromisoformat(start[0])).days / 365.25
    # Refuse to annualise a window materially shorter than requested.
    if actual_years < years * 0.85:
        return None
    growth = series[-1][1] / start[1]
    if growth <= 0:
        return None
    return (growth ** (1 / actual_years) - 1) * 100


def risk_profiles(book: MFBook) -> dict[str, RiskProfile]:
    """Per-scheme return/risk from the stored NAV history."""
    out: dict[str, RiskProfile] = {}
    for s in book.schemes:
        series = nav_series(s.scheme_code)
        profile = RiskProfile(scheme_code=s.scheme_code, fund=s.fund,
                              points=len(series))
        if len(series) >= 30:
            closes = _closes(series)
            profile.cagr_1y = _cagr(series, 1)
            profile.cagr_3y = _cagr(series, 3)
            profile.cagr_5y = _cagr(series, 5)
            profile.volatility = annualised_volatility_pct(closes)
            profile.max_drawdown = max_drawdown_pct(closes)
            profile.sharpe = sharpe_ratio(closes)
        out[s.scheme_code] = profile
    return out


def _aligned(a: list[tuple[str, float]],
             b: list[tuple[str, float]]) -> tuple[list[float], list[float]]:
    """Two NAV series restricted to the dates they share."""
    mb = dict(b)
    dates = [d for d, _ in a if d in mb]
    return [dict(a)[d] for d in dates], [mb[d] for d in dates]


def correlated_pairs(book: MFBook,
                     threshold: float = CORRELATION_REDUNDANT) -> list[dict]:
    """Pairs of held funds whose NAVs move together.

    This is the honest substitute for true holdings overlap: we cannot
    see what each fund owns without AMFI portfolio disclosures, but if
    two funds' daily NAVs correlate above ~0.9 they are one bet
    regardless of what their factsheets say.
    """
    from shared.quant_metrics import beta_and_correlation

    series = {s.scheme_code: nav_series(s.scheme_code) for s in book.schemes}
    by_code = {s.scheme_code: s for s in book.schemes}
    codes = [c for c, ser in series.items() if len(ser) >= 120]

    pairs = []
    for i, ca in enumerate(codes):
        for cb in codes[i + 1:]:
            xa, xb = _aligned(series[ca], series[cb])
            if len(xa) < 120:
                continue
            _, corr = beta_and_correlation(xa, xb, window=len(xa) - 1)
            if corr is None or corr < threshold:
                continue
            sa, sb = by_code[ca], by_code[cb]
            pairs.append({
                "a_code": ca, "a_fund": sa.fund,
                "a_value": round(sa.current_value, 2),
                "a_accumulating": sa.is_accumulating,
                "b_code": cb, "b_fund": sb.fund,
                "b_value": round(sb.current_value, 2),
                "b_accumulating": sb.is_accumulating,
                "correlation": round(corr, 3),
                "combined_value": round(sa.current_value + sb.current_value, 2),
                "same_exposure": (exposure_of(sa.fund, sa.scheme_type)
                                  == exposure_of(sb.fund, sb.scheme_type)),
            })
    pairs.sort(key=lambda p: p["correlation"], reverse=True)
    return pairs


# ── Tax ─────────────────────────────────────────────────────────

def tax_view(book: MFBook, *, ltcg_used_this_fy: float = 0.0) -> dict:
    """Unrealised gains against the annual LTCG exemption.

    The exemption is the cheapest tool a long-term holder has for
    tidying a book: consolidating a redundant fund is close to free if
    the gain fits inside it. We deliberately do NOT claim a holding
    period — Kite's MF order book returns only recent orders, so per-lot
    purchase dates are unknown. `holding_period_known` says so, and the
    UI must repeat it rather than imply these gains are all long-term.
    """
    headroom = max(0.0, LTCG_EXEMPTION_INR - max(0.0, ltcg_used_this_fy))
    gainers = [s for s in book.schemes if s.pnl > 0]

    return {
        "ltcg_exemption": LTCG_EXEMPTION_INR,
        "ltcg_used": round(max(0.0, ltcg_used_this_fy), 2),
        "headroom": round(headroom, 2),
        "unrealised_gain": round(sum(s.pnl for s in gainers), 2),
        "unrealised_loss": round(
            sum(s.pnl for s in book.schemes if s.pnl < 0), 2),
        "holding_period_known": False,
        "equity_oriented_note": (
            "Gold/silver, international and debt-oriented funds are not "
            "equity-oriented and do not share this exemption."
        ),
    }


def consolidation_options(book: MFBook, clusters: list[Cluster],
                          tax: dict) -> list[dict]:
    """Cost of collapsing each redundant cluster into its largest fund.

    Only the smaller legs are assumed sold, and only dormant ones — a
    fund still receiving a SIP is part of the plan, not a leftover.
    """
    headroom = float(tax.get("headroom") or 0)
    options = []
    for c in clusters:
        if not c.is_redundant:
            continue
        ordered = sorted(c.schemes, key=lambda s: s.current_value, reverse=True)
        keep, rest = ordered[0], ordered[1:]
        sellable = [s for s in rest if not s.is_accumulating]
        if not sellable:
            continue
        gain = sum(s.pnl for s in sellable)
        equity = all(asset_class(s.scheme_type, s.fund) == "EQUITY"
                     for s in sellable)
        options.append({
            "cluster": c.label,
            "keep": keep.fund,
            "keep_code": keep.scheme_code,
            "merge": [{"fund": s.fund, "scheme_code": s.scheme_code,
                       "value": round(s.current_value, 2),
                       "gain": round(s.pnl, 2)} for s in sellable],
            "freed_value": round(sum(s.current_value for s in sellable), 2),
            "gain_realised": round(gain, 2),
            "equity_oriented": equity,
            "fits_exemption": bool(equity and 0 < gain <= headroom),
            "funds_removed": len(sellable),
        })
    options.sort(key=lambda o: o["freed_value"], reverse=True)
    return options


# ── Findings ────────────────────────────────────────────────────

def _findings(book: MFBook, acc: dict, clusters: list[Cluster],
              pairs: list[dict], options: list[dict], tax: dict) -> list[Finding]:
    out: list[Finding] = []
    total = sum(s.current_value for s in book.schemes)
    if total <= 0:
        return out

    dormant_pct = float(acc.get("dormant_pct") or 0)
    rate = float(acc.get("inflow_rate_pct") or 0)
    if dormant_pct > 50 and rate > 0:
        out.append(Finding(
            code="DORMANT_CORPUS",
            severity="NOTE",
            title=f"{dormant_pct:.0f}% of the corpus receives no new money",
            detail=(
                f"New money is {rate:.0f}% of the book a year and flows into "
                f"{acc['funded_count']} of {len(book.schemes)} schemes. "
                f"Anything redundant in the dormant "
                f"{_inr(acc['dormant_value'])} will not be diluted by "
                f"future SIPs — it only changes if you act on it."
            ),
            value=float(acc["dormant_value"]),
        ))

    for c in clusters:
        if not c.is_redundant:
            continue
        dormant = len(c.dormant)
        tail = ""
        if dormant == 1:
            tail = " \u2014 one of them receives no new money."
        elif dormant > 1:
            tail = f" \u2014 {dormant} of them receive no new money."
        out.append(Finding(
            code=f"OVERLAP_{c.label.upper().replace(' ', '_')}",
            severity="REVIEW" if dormant > 1 else "NOTE",
            title=(f"{len(c.schemes)} funds share one exposure: "
                   f"{c.label} ({c.weight_pct:.1f}% of book)"),
            detail=(
                ", ".join(f"{s.fund} {_inr(s.current_value)}"
                          for s in sorted(c.schemes,
                                          key=lambda x: x.current_value,
                                          reverse=True))
                + tail
            ),
            value=c.value,
            schemes=[s.scheme_code for s in c.schemes],
        ))

    for p in pairs[:5]:
        if p["same_exposure"]:
            continue  # already reported as a cluster
        out.append(Finding(
            code="CORRELATED_PAIR",
            severity="NOTE",
            title=(f"{p['a_fund']} and {p['b_fund']} move together "
                   f"(r={p['correlation']:.2f})"),
            detail=(
                f"Different categories, near-identical daily NAV moves across "
                f"{_inr(p['combined_value'])}. Holding both adds names, not "
                f"diversification."
            ),
            value=p["combined_value"],
            schemes=[p["a_code"], p["b_code"]],
        ))

    sub = [s for s in book.schemes
           if 0 < s.current_value / total * 100 < SUB_SCALE_PCT]
    if len(sub) >= 3:
        out.append(Finding(
            code="SUB_SCALE",
            severity="NOTE",
            title=f"{len(sub)} positions are under {SUB_SCALE_PCT:.0f}% of the book",
            detail=(
                f"Together {_inr(sum(s.current_value for s in sub))}. Each one "
                f"is a scheme to track and a line in every tax filing, but "
                f"none can move the portfolio on its own."
            ),
            value=sum(s.current_value for s in sub),
            schemes=[s.scheme_code for s in sub],
        ))

    free = [o for o in options if o["fits_exemption"]]
    if free:
        best = free[0]
        out.append(Finding(
            code="TAX_FREE_CONSOLIDATION",
            severity="REVIEW",
            title=(f"{best['cluster']} can be consolidated inside this year's "
                   f"LTCG exemption"),
            detail=(
                f"Merging {best['funds_removed']} dormant fund(s) into "
                f"{best['keep']} realises {_inr(best['gain_realised'])} of gain "
                f"against {_inr(tax['headroom'])} of remaining exemption. "
                f"Assumes the units are long-term held \u2014 verify, because "
                f"per-lot purchase dates are not available from the broker."
            ),
            value=best["freed_value"],
        ))

    regular = float((book.allocation or {}).get("regular_plan_value") or 0)
    if regular <= 0 and book.schemes:
        out.append(Finding(
            code="ALL_DIRECT",
            severity="GOOD",
            title="Every fund is a direct plan",
            detail="No distributor trail is being paid on any holding.",
        ))

    order = {"REVIEW": 0, "NOTE": 1, "GOOD": 2}
    out.sort(key=lambda f: (order.get(f.severity, 3), -f.value))
    return out


def _inr(v: float) -> str:
    return f"Rs.{v:,.0f}"


# ── Entry point ─────────────────────────────────────────────────

def build_insights(book: MFBook, *, ltcg_used_this_fy: float = 0.0) -> dict:
    """Full analysis bundle for the page and the CLI."""
    acc = accumulation_view(book)
    clusters = exposure_clusters(book)
    tax = tax_view(book, ltcg_used_this_fy=ltcg_used_this_fy)
    options = consolidation_options(book, clusters, tax)

    meta = nav_history_meta()
    have_history = sum(1 for s in book.schemes
                       if meta.get(s.scheme_code, {}).get("ok"))
    pairs = correlated_pairs(book) if have_history >= 2 else []
    profiles = risk_profiles(book) if have_history else {}

    return {
        "accumulation": acc,
        "clusters": [c.to_dict() for c in clusters],
        "correlated_pairs": pairs,
        "tax": tax,
        "consolidation": options,
        "risk": {k: v.to_dict() for k, v in profiles.items()},
        "nav_history_coverage": {
            "have": have_history,
            "total": len(book.schemes),
        },
        "findings": [f.to_dict()
                     for f in _findings(book, acc, clusters, pairs, options, tax)],
    }


__all__ = [
    "build_insights", "exposure_of", "accumulation_view", "exposure_clusters",
    "correlated_pairs", "risk_profiles", "tax_view", "consolidation_options",
    "Finding", "Cluster", "RiskProfile", "LTCG_EXEMPTION_INR",
]
