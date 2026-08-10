# ================================================================
# modes/mf/types.py
# ================================================================
# Typed records for the mutual-fund book.
#
# Why MFs get their own record instead of reusing StockAnalysis:
#   an open-ended scheme has no intraday tick, no 52-week band, no
#   RSI/SMA and no sector. It has units (fractional), an average
#   buy NAV, and an end-of-day NAV stamped with the date the AMC
#   published it. Forcing it into the equity record would mean a
#   dozen permanently-empty fields and a "live price" that is not
#   live. `nav_date` is a first-class field for exactly that reason.
# ================================================================

from __future__ import annotations

from dataclasses import dataclass, field


# Where a holding came from. COIN rows are broker truth; EXTERNAL
# rows are typed in by hand for funds held at another platform.
SRC_COIN     = "COIN"
SRC_EXTERNAL = "EXTERNAL"


@dataclass
class MFHolding:
    """One fund position, from Coin or from a manually-tracked broker."""

    scheme_code: str            # ISIN — the join key across both sources
    fund: str
    units: float
    avg_nav: float
    nav: float = 0.0
    nav_date: str = ""
    folio: str = ""
    source: str = SRC_COIN
    broker: str = "Zerodha Coin"
    amc: str = ""
    scheme_type: str = ""
    plan: str = ""
    holding_id: int = 0         # only set for EXTERNAL rows
    notes: str = ""

    @property
    def invested_value(self) -> float:
        return self.units * self.avg_nav

    @property
    def current_value(self) -> float:
        # An unresolved NAV must not silently mark the fund to zero.
        return self.units * (self.nav or self.avg_nav)

    @property
    def pnl(self) -> float:
        return self.current_value - self.invested_value

    @property
    def pnl_pct(self) -> float:
        cost = self.invested_value
        return ((self.current_value / cost - 1) * 100) if cost > 0 else 0.0

    @property
    def priced(self) -> bool:
        """False when we are holding the fund at cost for want of a NAV."""
        return self.nav > 0

    def to_dict(self) -> dict:
        return {
            "scheme_code": self.scheme_code,
            "fund": self.fund,
            "units": self.units,
            "avg_nav": self.avg_nav,
            "nav": self.nav,
            "nav_date": self.nav_date,
            "folio": self.folio,
            "source": self.source,
            "broker": self.broker,
            "amc": self.amc,
            "scheme_type": self.scheme_type,
            "plan": self.plan,
            "holding_id": self.holding_id,
            "notes": self.notes,
            "invested_value": round(self.invested_value, 2),
            "current_value": round(self.current_value, 2),
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "priced": self.priced,
        }


@dataclass
class MFSchemeRollup:
    """Same scheme aggregated across every broker that holds it.

    The user can own the identical ISIN on Coin and at another
    platform; this is the row that answers "how much of this fund do
    I actually own", which no single broker statement can show.
    """

    scheme_code: str
    fund: str
    units: float
    invested_value: float
    current_value: float
    nav: float
    nav_date: str
    amc: str = ""
    scheme_type: str = ""
    plan: str = ""
    brokers: list[str] = field(default_factory=list)
    legs: list[MFHolding] = field(default_factory=list)

    @property
    def avg_nav(self) -> float:
        return (self.invested_value / self.units) if self.units > 0 else 0.0

    @property
    def pnl(self) -> float:
        return self.current_value - self.invested_value

    @property
    def pnl_pct(self) -> float:
        return ((self.current_value / self.invested_value - 1) * 100
                if self.invested_value > 0 else 0.0)

    @property
    def is_split(self) -> bool:
        """True when the same scheme is held at more than one broker."""
        return len(self.brokers) > 1

    def to_dict(self) -> dict:
        return {
            "scheme_code": self.scheme_code,
            "fund": self.fund,
            "units": round(self.units, 4),
            "avg_nav": round(self.avg_nav, 4),
            "nav": round(self.nav, 4),
            "nav_date": self.nav_date,
            "amc": self.amc,
            "scheme_type": self.scheme_type,
            "plan": self.plan,
            "brokers": self.brokers,
            "is_split": self.is_split,
            "invested_value": round(self.invested_value, 2),
            "current_value": round(self.current_value, 2),
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "legs": [leg.to_dict() for leg in self.legs],
        }


@dataclass
class MFSip:
    sip_id: str
    scheme_code: str
    fund: str
    status: str
    frequency: str = ""
    instalment_amount: float = 0.0
    instalment_day: int = 0
    completed_instalments: int = 0
    pending_instalments: int = 0
    next_instalment: str = ""
    last_instalment: str = ""
    created: str = ""
    tag: str = ""

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    @property
    def is_paused(self) -> bool:
        return self.status == "PAUSED"

    @property
    def monthly_outflow(self) -> float:
        """Instalment normalised to a monthly rupee commitment."""
        if not self.is_active:
            return 0.0
        per_month = {
            "day": 30.0, "daily": 30.0,
            "week": 4.333, "weekly": 4.333,
            "month": 1.0, "monthly": 1.0,
            "quarter": 1 / 3, "quarterly": 1 / 3,
            "year": 1 / 12, "yearly": 1 / 12,
        }
        return self.instalment_amount * per_month.get(
            (self.frequency or "monthly").lower(), 1.0)

    def to_dict(self) -> dict:
        return {
            "sip_id": self.sip_id,
            "scheme_code": self.scheme_code,
            "fund": self.fund,
            "status": self.status,
            "frequency": self.frequency,
            "instalment_amount": self.instalment_amount,
            "instalment_day": self.instalment_day,
            "completed_instalments": self.completed_instalments,
            "pending_instalments": self.pending_instalments,
            "next_instalment": self.next_instalment,
            "last_instalment": self.last_instalment,
            "created": self.created,
            "tag": self.tag,
            "monthly_outflow": round(self.monthly_outflow, 2),
        }


@dataclass
class MFBook:
    """The whole mutual-fund book — both sources, plus analytics."""

    holdings: list[MFHolding] = field(default_factory=list)
    schemes: list[MFSchemeRollup] = field(default_factory=list)
    sips: list[MFSip] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    allocation: dict = field(default_factory=dict)
    generated_at: str = ""
    synced_at: str = ""          # when Coin was last fetched
    coin_available: bool = False
    coin_error: str = ""

    @property
    def invested_value(self) -> float:
        return sum(h.invested_value for h in self.holdings)

    @property
    def current_value(self) -> float:
        return sum(h.current_value for h in self.holdings)

    @property
    def pnl(self) -> float:
        return self.current_value - self.invested_value

    @property
    def pnl_pct(self) -> float:
        cost = self.invested_value
        return ((self.current_value / cost - 1) * 100) if cost > 0 else 0.0

    @property
    def nav_as_of(self) -> str:
        """Oldest NAV date in the book — the honest freshness stamp."""
        dates = [h.nav_date for h in self.holdings if h.nav_date]
        return min(dates) if dates else ""

    @property
    def unpriced_count(self) -> int:
        return sum(1 for h in self.holdings if not h.priced)

    @property
    def active_sips(self) -> list[MFSip]:
        return [s for s in self.sips if s.is_active]

    @property
    def paused_sips(self) -> list[MFSip]:
        return [s for s in self.sips if s.is_paused]

    @property
    def monthly_sip_outflow(self) -> float:
        return sum(s.monthly_outflow for s in self.active_sips)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "synced_at": self.synced_at,
            "coin_available": self.coin_available,
            "coin_error": self.coin_error,
            "invested_value": round(self.invested_value, 2),
            "current_value": round(self.current_value, 2),
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "nav_as_of": self.nav_as_of,
            "unpriced_count": self.unpriced_count,
            "holdings": [h.to_dict() for h in self.holdings],
            "schemes": [s.to_dict() for s in self.schemes],
            "sips": [s.to_dict() for s in self.sips],
            "orders": self.orders,
            "allocation": self.allocation,
            "monthly_sip_outflow": round(self.monthly_sip_outflow, 2),
            "active_sip_count": len(self.active_sips),
            "paused_sip_count": len(self.paused_sips),
        }


__all__ = [
    "SRC_COIN", "SRC_EXTERNAL",
    "MFHolding", "MFSchemeRollup", "MFSip", "MFBook",
]
