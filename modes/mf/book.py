# ================================================================
# modes/mf/book.py
# ================================================================
# Builds the mutual-fund book and everything derived from it.
#
# The book is the union of two sources keyed by ISIN:
#   COIN     — kite.mf_holdings(), broker truth, re-fetched each run.
#   EXTERNAL — data/mf.db, hand-entered funds held elsewhere.
# Both are marked with the same NAV, so a scheme held at two brokers
# values consistently and can be rolled up into a single position.
#
# `build_book(live=False)` never touches Zerodha — that is the path
# the dashboard renders on first paint. `live=True` refreshes from
# Coin and re-banks the NAV cache.
# ================================================================

from __future__ import annotations

from config import Config, now_ist
from core.logger import Logger
from modes.mf.catalog import cached_catalog, scheme_by_code
from modes.mf.persistence import (
    cache_navs, cached_navs, coin_holdings, coin_orders, coin_sips,
    external_holdings, init_db, last_synced_at, save_coin_snapshot,
)
from modes.mf.types import MFBook, MFHolding, MFSchemeRollup, MFSip, SRC_COIN


# Kite's scheme_type strings vary by AMC; fold them into the buckets
# an asset-allocation view actually cares about. Order matters — the
# first hint that appears in the string wins.
_ASSET_CLASS_HINTS = (
    ("FUND OF FUNDS", "FUND OF FUNDS"),
    ("SOLUTION", "SOLUTION ORIENTED"),
    ("EQUITY", "EQUITY"),
    ("ELSS", "EQUITY"),
    ("INDEX", "EQUITY"),
    ("DEBT", "DEBT"),
    ("LIQUID", "DEBT"),
    ("GILT", "DEBT"),
    ("INCOME", "DEBT"),
    ("MONEY", "DEBT"),
    ("HYBRID", "HYBRID"),
    ("BALANCED", "HYBRID"),
)


def asset_class(scheme_type: str, fund: str = "") -> str:
    blob = f"{scheme_type} {fund}".upper()
    for needle, bucket in _ASSET_CLASS_HINTS:
        if needle in blob:
            return bucket
    return "OTHER"


def plan_kind(plan: str, fund: str = "") -> str:
    blob = f"{plan} {fund}".upper()
    if "DIRECT" in blob:
        return "DIRECT"
    if "REGULAR" in blob:
        return "REGULAR"
    return "UNKNOWN"


# ── Coin fetch ──────────────────────────────────────────────────

def _fetch_coin(log: Logger) -> tuple[list[MFHolding], list[MFSip], list[dict], str]:
    """Pull holdings, SIPs and orders from Coin. Returns an error
    string instead of raising so the page still renders offline."""
    try:
        from core.zerodha_client import ZerodhaClient
        zerodha = ZerodhaClient(Config, log)
        zerodha.login(interactive=False)
        raw_holdings = zerodha.get_mf_holdings()
    except Exception as exc:  # noqa: BLE001 — degrade to cache
        return [], [], [], str(exc)[:200]

    holdings = [
        MFHolding(
            scheme_code=h["scheme_code"],
            fund=h["fund"],
            units=h["units"],
            avg_nav=h["avg_nav"],
            nav=h["nav"],
            nav_date=h["nav_date"],
            folio=h["folio"],
            source=SRC_COIN,
            broker="Zerodha Coin",
        )
        for h in raw_holdings
        if h.get("units", 0) > 0
    ]

    sips: list[MFSip] = []
    try:
        for s in zerodha.get_mf_sips():
            sips.append(MFSip(
                sip_id=s["sip_id"], scheme_code=s["scheme_code"],
                fund=s["fund"], status=s["status"], frequency=s["frequency"],
                instalment_amount=s["instalment_amount"],
                instalment_day=s["instalment_day"],
                completed_instalments=s["completed_instalments"],
                pending_instalments=s["pending_instalments"],
                next_instalment=s["next_instalment"],
                last_instalment=s["last_instalment"],
                created=s["created"], tag=s["tag"],
            ))
    except Exception as exc:  # noqa: BLE001 — SIPs are optional detail
        log.warning(f"MF SIP fetch failed: {exc}")

    orders: list[dict] = []
    try:
        orders = zerodha.get_mf_orders()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"MF order fetch failed: {exc}")

    # Bank today's NAVs so the next offline render is still valued.
    try:
        cache_navs([{
            "scheme_code": h["scheme_code"], "fund": h["fund"],
            "nav": h["nav"], "nav_date": h["nav_date"],
        } for h in raw_holdings])
    except Exception as exc:  # noqa: BLE001
        log.warning(f"NAV cache write failed: {exc}")

    # Store the fetch itself, not just the NAVs, so landing on the page
    # shows the last known book instead of an empty one.
    try:
        save_coin_snapshot(
            [h.to_dict() for h in holdings],
            [s.to_dict() for s in sips],
            orders,
        )
    except Exception as exc:  # noqa: BLE001 — a failed cache write is
        # not a reason to throw away a good live fetch.
        log.warning(f"Coin snapshot write failed: {exc}")

    return holdings, sips, orders, ""


def _restore_coin(log: Logger) -> tuple[list[MFHolding], list[MFSip], list[dict]]:
    """Rebuild the last Coin fetch from disk."""
    try:
        holdings = coin_holdings()
        sips = [MFSip(
            sip_id=str(s.get("sip_id") or ""),
            scheme_code=str(s.get("scheme_code") or ""),
            fund=str(s.get("fund") or ""),
            status=str(s.get("status") or ""),
            frequency=str(s.get("frequency") or ""),
            instalment_amount=float(s.get("instalment_amount") or 0),
            instalment_day=int(s.get("instalment_day") or 0),
            completed_instalments=int(s.get("completed_instalments") or 0),
            pending_instalments=int(s.get("pending_instalments") or 0),
            next_instalment=str(s.get("next_instalment") or ""),
            last_instalment=str(s.get("last_instalment") or ""),
            created=str(s.get("created") or ""),
            tag=str(s.get("tag") or ""),
        ) for s in coin_sips()]
        return holdings, sips, coin_orders()
    except Exception as exc:  # noqa: BLE001 — render an empty book instead
        log.warning(f"Coin snapshot read failed: {exc}")
        return [], [], []


# ── Enrichment ──────────────────────────────────────────────────

def _enrich(holdings: list[MFHolding]) -> None:
    """Fill NAV and scheme metadata in place.

    NAV precedence: whatever Coin already returned for this holding,
    then the Coin catalogue, then the local cache. Metadata always
    comes from the catalogue because a hand-typed external row only
    carries a scheme code — and so does the NAV date, since
    `mf_holdings` returns it blank.
    """
    codes = [h.scheme_code for h in holdings if h.scheme_code]
    db_navs = cached_navs(codes) if codes else {}
    coin_navs = {h.scheme_code: (h.nav, h.nav_date)
                 for h in holdings if h.nav > 0 and h.scheme_code}

    for h in holdings:
        meta = scheme_by_code(h.scheme_code)
        cached = db_navs.get(h.scheme_code.upper()) or {}
        if meta:
            h.fund = h.fund or str(meta.get("name") or "")
            h.amc = str(meta.get("amc") or "")
            h.scheme_type = str(meta.get("scheme_type") or "")
            h.plan = str(meta.get("plan") or "")

        if h.nav <= 0:
            same_scheme = coin_navs.get(h.scheme_code)
            if same_scheme and same_scheme[0] > 0:
                h.nav, h.nav_date = same_scheme
            elif meta and float(meta.get("nav") or 0) > 0:
                h.nav = float(meta["nav"])
                h.nav_date = str(meta.get("nav_date") or "")
            elif float(cached.get("nav") or 0) > 0:
                h.nav = float(cached["nav"])
                h.nav_date = str(cached.get("nav_date") or "")
                h.fund = h.fund or str(cached.get("fund") or "")

        # `mf_holdings` ships an empty last_price_date, so the date the
        # NAV actually belongs to has to come from the catalogue.
        if not h.nav_date:
            h.nav_date = (str(meta.get("nav_date") or "") if meta
                          else str(cached.get("nav_date") or ""))

        if not h.fund:
            h.fund = h.scheme_code


# ── Roll-ups and analytics ──────────────────────────────────────

def _rollup(holdings: list[MFHolding]) -> list[MFSchemeRollup]:
    """Merge legs of the same scheme across brokers into one position."""
    merged: dict[str, MFSchemeRollup] = {}
    for h in holdings:
        key = h.scheme_code or h.fund
        row = merged.get(key)
        if row is None:
            row = MFSchemeRollup(
                scheme_code=h.scheme_code, fund=h.fund, units=0.0,
                invested_value=0.0, current_value=0.0, nav=h.nav,
                nav_date=h.nav_date, amc=h.amc, scheme_type=h.scheme_type,
                plan=h.plan,
            )
            merged[key] = row
        row.units += h.units
        row.invested_value += h.invested_value
        row.current_value += h.current_value
        row.sip_amount += h.sip_amount
        row.legs.append(h)
        if h.broker not in row.brokers:
            row.brokers.append(h.broker)
        if h.nav > 0 and not row.nav:
            row.nav, row.nav_date = h.nav, h.nav_date

    out = list(merged.values())
    out.sort(key=lambda r: r.current_value, reverse=True)
    return out


def _attach_sips(holdings: list[MFHolding], sips: list[MFSip]) -> None:
    """Point each Coin leg at its active SIP.

    External legs already carry a user-entered `sip_amount`; only Coin
    legs can be resolved from the broker's SIP book. Paused SIPs
    contribute nothing — a paused plan is not new money.
    """
    monthly: dict[str, float] = {}
    for s in sips:
        if s.is_active and s.scheme_code:
            monthly[s.scheme_code] = monthly.get(s.scheme_code, 0.0) + s.monthly_outflow
    for h in holdings:
        if h.source == SRC_COIN:
            h.sip_amount = monthly.get(h.scheme_code, 0.0)


def _weights(buckets: dict[str, float], total: float) -> list[dict]:
    if total <= 0:
        return []
    rows = [{"label": k, "value": round(v, 2),
             "weight_pct": round(v / total * 100, 2)}
            for k, v in buckets.items()]
    rows.sort(key=lambda r: r["value"], reverse=True)
    return rows


def _allocation(schemes: list[MFSchemeRollup]) -> dict:
    total = sum(s.current_value for s in schemes)
    by_asset: dict[str, float] = {}
    by_amc: dict[str, float] = {}
    by_plan: dict[str, float] = {}
    by_broker: dict[str, float] = {}

    for s in schemes:
        by_asset[asset_class(s.scheme_type, s.fund)] = (
            by_asset.get(asset_class(s.scheme_type, s.fund), 0.0) + s.current_value)
        amc = s.amc or "Unknown AMC"
        by_amc[amc] = by_amc.get(amc, 0.0) + s.current_value
        kind = plan_kind(s.plan, s.fund)
        by_plan[kind] = by_plan.get(kind, 0.0) + s.current_value
        for leg in s.legs:
            by_broker[leg.broker] = by_broker.get(leg.broker, 0.0) + leg.current_value

    # HHI on scheme weights — the same concentration measure the
    # equity analyser uses, so the two books read the same way.
    hhi = 0.0
    if total > 0:
        hhi = sum((s.current_value / total * 100) ** 2 for s in schemes)

    ranked = sorted(schemes, key=lambda s: s.pnl_pct, reverse=True)
    # A fund held at cost for want of a NAV has no return to rank.
    priced = [s for s in ranked if s.invested_value > 0 and s.nav > 0]

    return {
        "total_value": round(total, 2),
        "by_asset_class": _weights(by_asset, total),
        "by_amc": _weights(by_amc, total)[:12],
        "by_plan": _weights(by_plan, total),
        "by_broker": _weights(by_broker, total),
        "hhi": round(hhi, 1),
        "top_weight_pct": round(
            max((s.current_value / total * 100) for s in schemes), 2)
            if schemes and total > 0 else 0.0,
        "scheme_count": len(schemes),
        "split_schemes": [s.scheme_code for s in schemes if s.is_split],
        "best": [s.to_dict() for s in priced[:3]],
        "worst": [s.to_dict() for s in priced[-3:][::-1]],
        "regular_plan_value": round(by_plan.get("REGULAR", 0.0), 2),
    }


# ── Entry point ─────────────────────────────────────────────────

def build_book(*, live: bool = False, log: Logger | None = None) -> MFBook:
    """Assemble the full mutual-fund book.

    `live=False` is cache-only and safe to call from any page render:
    it replays the last Coin fetch from disk. A live fetch that fails
    falls back to that same snapshot, so a dead token degrades to
    stale-but-real numbers rather than an empty book.
    """
    log = log or Logger("MF")
    init_db()

    coin_error = ""
    fetched_live = False
    if live:
        coin_list, sips, orders, coin_error = _fetch_coin(log)
        fetched_live = not coin_error
        if coin_error:
            coin_list, sips, orders = _restore_coin(log)
    else:
        coin_list, sips, orders = _restore_coin(log)

    holdings = coin_list + external_holdings()
    _enrich(holdings)
    _attach_sips(holdings, sips)
    schemes = _rollup(holdings)

    holdings.sort(key=lambda h: h.current_value, reverse=True)
    return MFBook(
        holdings=holdings,
        schemes=schemes,
        sips=sips,
        orders=orders,
        allocation=_allocation(schemes),
        generated_at=now_ist().isoformat(timespec="seconds"),
        synced_at=last_synced_at(),
        coin_available=fetched_live or bool(coin_list),
        coin_error=coin_error,
    )


def catalog_ready() -> bool:
    return bool(cached_catalog())


__all__ = ["build_book", "asset_class", "plan_kind", "catalog_ready"]
