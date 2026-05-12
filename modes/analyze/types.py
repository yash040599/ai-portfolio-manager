# ================================================================
# modes/analyze/types.py
# ================================================================
# Typed records for the portfolio analyser. Every field carries a
# `source` tag and an `as_of` timestamp so the report and the
# dashboard can show how stale each number actually is.
#
# Design note (ANALYZE_ROADMAP P1, 2026-05-12):
#   The whole flow is:
#     PortfolioSnapshot
#       ├─ holdings: list[StockAnalysis]      (one per demat row)
#       ├─ metrics:  PortfolioMetrics         (HHI, beta, ...)   — P6
#       └─ gaps:     GapAnalysis              (what's missing)   — P7
#   A `StockAnalysis` carries deterministic NoAI fields (always
#   filled) AND optional AI-overlay fields (filled only on --ai).
#   Both modes share the same record. AI never overwrites NoAI
#   numbers — it only writes into its own slots (ai_thesis, ...).
# ================================================================

from __future__ import annotations

import dataclasses
import datetime
from dataclasses import dataclass, field, asdict
from typing import Any, Generic, TypeVar

from config import now_ist


T = TypeVar("T")


# ── Source vocabulary (string enum kept as plain str for JSON ease) ──
# When adding a new value, also document it in the analyse roadmap so
# the dashboard / report rendering knows how to label it.
SRC_ZERODHA_API   = "zerodha_api"
SRC_CANDLE_CACHE  = "candle_cache"
SRC_SECTOR_MAP    = "sector_map"
SRC_DIVIDENDS     = "dividends_seed"
SRC_FUNDAMENTALS  = "fundamentals_seed"
SRC_RULE_ENGINE   = "rule_engine"
SRC_CLAUDE_PRO    = "claude_pro"
SRC_CLAUDE_FREE   = "claude_free"
SRC_CLAUDE_MAX    = "claude_max"
SRC_DERIVED       = "derived"          # computed locally from other Field values
SRC_MISSING       = "missing"          # explicit "we don't have this data"


@dataclass(frozen=True)
class Field(Generic[T]):
    """One typed value with provenance.

    `value`  — the actual data, or None when the source had nothing.
    `source` — short tag from the SRC_* constants above.
    `as_of`  — when this value was fetched/computed (IST naive).
    `note`   — optional short comment (e.g. "manual seed 2026-05-01").
    """
    value: T | None
    source: str
    as_of: datetime.datetime
    note: str = ""

    @property
    def staleness_minutes(self) -> int:
        """Minutes between `as_of` and `now_ist()`. Rounded down.

        Defensive: if `as_of` somehow ended up tz-aware (e.g. raw
        Kite historical_data datetime), strip tzinfo before the
        subtraction so we never crash with the offset-naive vs
        offset-aware error in a render path."""
        if self.as_of is None:
            return 0
        ts = self.as_of
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        delta = now_ist() - ts
        return max(0, int(delta.total_seconds() // 60))

    @property
    def staleness_label(self) -> str:
        """Human-readable age — '3 min ago', '2 hr 14 min ago', '5 days ago'."""
        m = self.staleness_minutes
        if m < 1:
            return "just now"
        if m < 60:
            return f"{m} min ago"
        h, m = divmod(m, 60)
        if h < 24:
            return f"{h} hr {m} min ago" if m else f"{h} hr ago"
        d, h = divmod(h, 24)
        if d < 30:
            return f"{d} day ago" if d == 1 else f"{d} days ago"
        return self.as_of.strftime("%Y-%m-%d")

    def to_dict(self) -> dict:
        return {
            "value":  self.value,
            "source": self.source,
            "as_of":  self.as_of.isoformat() if self.as_of else None,
            "note":   self.note,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "Field | None":
        if not d:
            return None
        as_of = d.get("as_of")
        if isinstance(as_of, str):
            try:
                as_of = datetime.datetime.fromisoformat(as_of)
            except ValueError:
                as_of = now_ist()
        elif as_of is None:
            as_of = now_ist()
        return cls(
            value  = d.get("value"),
            source = d.get("source") or SRC_MISSING,
            as_of  = as_of,
            note   = d.get("note", ""),
        )

    @classmethod
    def missing(cls, note: str = "") -> "Field":
        """Convenience for explicit-missing fields."""
        return cls(value=None, source=SRC_MISSING, as_of=now_ist(), note=note)


# ── Stock-level analysis record ─────────────────────────────────

@dataclass
class StockAnalysis:
    """Per-stock analysis. NoAI fields always populated; AI overlay
    fields default to None and are filled only when run with --ai."""

    symbol: str
    exchange: str

    # ── Position (Zerodha) ──
    qty:             Field[int]
    avg_buy_price:   Field[float]
    current_price:   Field[float]
    invested_value:  Field[float]
    current_value:   Field[float]
    pnl:             Field[float]
    pnl_pct:         Field[float]

    # ── Market context ──
    high_52w:        Field[float]
    low_52w:         Field[float]
    sector:          Field[str]
    industry:        Field[str]
    beta_vs_nifty:   Field[float]
    dividend_yield_ttm: Field[float]   # % annual
    weighted_pe:     Field[float]      # symbol-level P/E (TTM)

    # ── Long-term technical snapshot ──
    sma_50:          Field[float]
    sma_200:         Field[float]
    rsi_daily:       Field[float]
    above_sma_200:   Field[bool]
    price_vs_high_52w_pct: Field[float]   # negative = below high

    # ── Rule-based recommendation (NoAI deterministic) ──
    rule_action:     Field[str]
    rule_conviction: Field[str]
    rule_horizon:    Field[str]
    rule_target_price: Field[str]
    rule_reasoning:  Field[str]

    # ── AI overlay (None when --noai) ──
    ai_thesis_long_term:   Field[str] | None = None
    ai_qualitative_risks:  Field[list[str]] | None = None
    ai_peer_comparison:    Field[str] | None = None
    ai_news_context:       Field[str] | None = None
    ai_change_vs_prior:    Field[str] | None = None
    ai_action:             Field[str] | None = None
    ai_action_detail:      Field[str] | None = None

    # ── Derived helpers (filled by the analyser, not the enricher) ──
    weight_in_portfolio_pct: Field[float] | None = None

    # ── Optional classification (filled by enrich_noai when seed
    # data exists). Kept optional so older persisted runs round-trip
    # cleanly without a schema migration.
    market_cap_tier: Field[str] | None = None   # 'LARGE' | 'MID' | 'SMALL' | 'ETF' | 'UNKNOWN'

    # ── Methods ──

    def all_fields(self) -> list[Field]:
        """Flat list of every populated `Field` on this record.
        Used by `most_stale_at()` and the report renderer."""
        out: list[Field] = []
        for f in dataclasses.fields(self):
            if f.name in ("symbol", "exchange"):
                continue
            v = getattr(self, f.name)
            if isinstance(v, Field):
                out.append(v)
        return out

    def most_stale_at(self) -> datetime.datetime:
        """Returns the oldest `as_of` across all populated fields.
        Exposed in the report header so the user knows the worst
        case freshness for this stock.

        Defensive: any tz-aware datetime is normalised to naive IST
        before `min()` so a stray Kite-historical_data timestamp
        cannot crash the snapshot render path."""
        all_as = []
        for f in self.all_fields():
            if f and f.value is not None and f.as_of is not None:
                ts = f.as_of
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                all_as.append(ts)
        if not all_as:
            return now_ist()
        return min(all_as)

    def effective_action(self) -> str:
        """AI action when present, else rule-based action."""
        if self.ai_action and self.ai_action.value:
            return str(self.ai_action.value)
        if self.rule_action and self.rule_action.value:
            return str(self.rule_action.value)
        return "HOLD"

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"symbol": self.symbol, "exchange": self.exchange}
        for f in dataclasses.fields(self):
            if f.name in ("symbol", "exchange"):
                continue
            v = getattr(self, f.name)
            if isinstance(v, Field):
                out[f.name] = v.to_dict()
            elif v is None:
                out[f.name] = None
            else:
                out[f.name] = v
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "StockAnalysis":
        kwargs: dict[str, Any] = {"symbol": d["symbol"], "exchange": d["exchange"]}
        for f in dataclasses.fields(cls):
            if f.name in ("symbol", "exchange"):
                continue
            raw = d.get(f.name)
            if raw is None:
                # Optional Field[...] | None slots accept None directly.
                kwargs[f.name] = None
            else:
                kwargs[f.name] = Field.from_dict(raw)
        return cls(**kwargs)


# ── Portfolio-level metrics (P6) ────────────────────────────────

@dataclass
class SectorWeight:
    sector: str
    weight_pct: float
    holdings_count: int


@dataclass
class PortfolioMetrics:
    """Industry-standard portfolio-level metrics. See
    `modes/analyze/metrics.py` for formulas. Each field carries an
    `as_of` so the report can flag stale inputs."""
    sector_weights:        list[SectorWeight]
    hhi_concentration:     Field[float]      # 0-10000
    top_5_concentration_pct: Field[float]
    single_name_max_pct:   Field[float]
    single_name_max_symbol: Field[str]
    group_concentration:   Field[dict]       # {group_name: weight_pct}
    weighted_pe:           Field[float]
    weighted_dividend_yield: Field[float]
    portfolio_beta_vs_nifty: Field[float]

    # Headline figures
    total_invested:        Field[float]
    total_current_value:   Field[float]
    total_pnl:             Field[float]
    total_pnl_pct:         Field[float]

    # ── Risk / return (industry-standard, P8) ──
    # All four computed from the per-stock daily-returns history
    # cached in `data/candle_cache.db` plus prior `PortfolioSnapshot`
    # rows in `data/portfolio_analyses.db`.
    volatility_30d_pct:    Field[float] | None = None   # annualised std dev × √252
    sharpe_ratio:          Field[float] | None = None   # (daily_ret - rfr/252) / daily_vol × √252
    max_drawdown_pct:      Field[float] | None = None   # peak-to-trough across prior runs
    xirr_pct:              Field[float] | None = None   # money-weighted compound annual return
    annual_dividend_estimate: Field[float] | None = None  # sum of (dps × qty)

    # ── Cash position (P8) ──
    cash_balance:          Field[float] | None = None   # Zerodha funds.live_balance
    cash_drag_pct:         Field[float] | None = None   # cash / (cash + invested) × 100

    # ── Market-cap tier breakdown (P9) ──
    # {tier_name: weight_pct}, e.g. {'LARGE': 78.4, 'MID': 12.1, 'SMALL': 0.0, 'ETF': 9.5}
    cap_tier_weights:      Field[dict] | None = None

    def to_dict(self) -> dict:
        out = {
            "sector_weights": [asdict(s) for s in self.sector_weights],
            "hhi_concentration": self.hhi_concentration.to_dict(),
            "top_5_concentration_pct": self.top_5_concentration_pct.to_dict(),
            "single_name_max_pct": self.single_name_max_pct.to_dict(),
            "single_name_max_symbol": self.single_name_max_symbol.to_dict(),
            "group_concentration": self.group_concentration.to_dict(),
            "weighted_pe": self.weighted_pe.to_dict(),
            "weighted_dividend_yield": self.weighted_dividend_yield.to_dict(),
            "portfolio_beta_vs_nifty": self.portfolio_beta_vs_nifty.to_dict(),
            "total_invested": self.total_invested.to_dict(),
            "total_current_value": self.total_current_value.to_dict(),
            "total_pnl": self.total_pnl.to_dict(),
            "total_pnl_pct": self.total_pnl_pct.to_dict(),
        }
        # Optional risk/return + cash slots: include when populated.
        for name in ("volatility_30d_pct", "sharpe_ratio",
                     "max_drawdown_pct", "xirr_pct",
                     "annual_dividend_estimate",
                     "cash_balance", "cash_drag_pct",
                     "cap_tier_weights"):
            v = getattr(self, name, None)
            if v is not None:
                out[name] = v.to_dict()
        return out


# ── Gap-analysis (P7) ───────────────────────────────────────────

@dataclass
class GapFlag:
    """One detected portfolio gap or risk."""
    severity: str          # 'INFO' | 'WARN' | 'RISK'
    category: str          # 'UNDER_ALLOCATED' | 'MISSING_DEFENSIVE' | 'CONCENTRATION' | 'GROUP_RISK'
    headline: str          # one-line summary for the report
    detail: str            # longer explanation
    suggested_symbols: list[str] = field(default_factory=list)


@dataclass
class GapAnalysis:
    flags: list[GapFlag]
    benchmark_label: str   # e.g. "NIFTY100 sector weights — 2026-Q2"

    def to_dict(self) -> dict:
        return {
            "flags": [asdict(f) for f in self.flags],
            "benchmark_label": self.benchmark_label,
        }


# ── Top-level snapshot ──────────────────────────────────────────

@dataclass
class PortfolioSnapshot:
    """One complete `--mode analyze` run."""
    timestamp: datetime.datetime
    mode: str              # 'NOAI' | 'AI'
    holdings: list[StockAnalysis]
    metrics: PortfolioMetrics
    gaps: GapAnalysis
    notes: str = ""        # operator note (e.g. "post-earnings spike")

    def most_stale_at(self) -> datetime.datetime:
        """Oldest `as_of` across the entire snapshot — what the
        dashboard header renders prominently. Defensive against
        stray tz-aware timestamps (see StockAnalysis.most_stale_at)."""
        ts = [h.most_stale_at() for h in self.holdings]
        for f in (self.metrics.hhi_concentration,
                  self.metrics.weighted_pe,
                  self.metrics.portfolio_beta_vs_nifty):
            if f and f.as_of:
                a = f.as_of
                if a.tzinfo is not None:
                    a = a.replace(tzinfo=None)
                ts.append(a)
        if not ts:
            return self.timestamp
        # Re-normalise the snapshot timestamp too in case a caller
        # passed a tz-aware now() instead of now_ist().
        ts = [t.replace(tzinfo=None) if t.tzinfo is not None else t
              for t in ts]
        return min(ts)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "mode":      self.mode,
            "holdings":  [h.to_dict() for h in self.holdings],
            "metrics":   self.metrics.to_dict(),
            "gaps":      self.gaps.to_dict(),
            "notes":     self.notes,
        }
