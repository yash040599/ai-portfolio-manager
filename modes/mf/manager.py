# ================================================================
# modes/mf/manager.py
# ================================================================
# CLI surface for the mutual-fund book.
#
#   python main.py --mode portfolio --type mf
#   python main.py --mode portfolio --type mf --sips
#   python main.py --mode portfolio --type mf --search "hdfc small cap"
#   python main.py --mode portfolio --type mf --add --scheme INF179K01BB2 \
#                  --units 120.5 --nav 88.2 --broker "Groww"
#   python main.py --mode portfolio --type mf --list-external
#   python main.py --mode portfolio --type mf --remove 3
#
# Read-only by default. The only writes are to data/mf.db, and only
# for funds held outside Coin — Coin holdings are never persisted.
# ================================================================

from __future__ import annotations

from config import Config
from core.logger import Logger
from modes.mf.book import asset_class, build_book, plan_kind
from modes.mf.catalog import ensure_catalog, refresh_scheme_map, search_catalog
from modes.mf.persistence import (
    add_external_holding, external_holdings, init_db, remove_external_holding,
)
from modes.mf.types import SRC_COIN


def _inr(value: float, signed: bool = False) -> str:
    sign = "-" if value < 0 else ("+" if signed else "")
    return f"Rs.{sign}{abs(value):,.2f}"


class MFManager:
    """Mutual-fund book runner (Coin + externally-held funds)."""

    def __init__(self, config: type[Config] = Config):
        self.cfg = config
        self.log = Logger("MF")

    # ── Full book report ────────────────────────────────────────

    def run(self, *, live: bool = True) -> None:
        self.log.section("MUTUAL FUND BOOK")
        init_db()
        if live:
            self.log.info("Refreshing Coin scheme catalogue...")
            ensure_catalog(log=self.log)

        book = build_book(live=live, log=self.log)

        if book.coin_error:
            self.log.warning(f"Coin unavailable: {book.coin_error}")
            self.log.warning("Showing externally-tracked funds and cached NAVs only.")
        if not book.holdings:
            self.log.warning("No mutual-fund holdings found.")
            self.log.info("Add funds held outside Coin with --add "
                          "(find the scheme code with --search).")
            return

        self._print_headline(book)
        self._print_holdings(book)
        self._print_allocation(book)
        if book.sips:
            self._print_sips(book)

    # ── Report sections ─────────────────────────────────────────

    def _print_headline(self, book) -> None:
        self.log.section("SUMMARY")
        print(f"  Schemes         : {len(book.schemes)} "
              f"({len(book.holdings)} legs across brokers)")
        print(f"  Invested        : {_inr(book.invested_value)}")
        print(f"  Current value   : {_inr(book.current_value)}")
        print(f"  Unrealised P&L  : {_inr(book.pnl, signed=True)} "
              f"({book.pnl_pct:+.2f}%)")
        if book.nav_as_of:
            print(f"  NAV as of       : {book.nav_as_of} "
                  f"(end-of-day, not a live price)")
        if book.unpriced_count:
            print(f"  Unpriced        : {book.unpriced_count} scheme(s) held at "
                  f"cost — no NAV resolved yet")

    def _print_holdings(self, book) -> None:
        self.log.section("HOLDINGS")
        print(f"  {'Fund':<44} {'Units':>11} {'Avg NAV':>9} "
              f"{'NAV':>9} {'Value':>13} {'P&L':>13} {'%':>8}")
        print("  " + "-" * 112)
        for s in book.schemes:
            name = s.fund[:43]
            flag = " *" if s.is_split else ""
            print(f"  {name:<44} {s.units:>11,.3f} {s.avg_nav:>9,.2f} "
                  f"{s.nav:>9,.2f} {s.current_value:>13,.2f} "
                  f"{s.pnl:>+13,.2f} {s.pnl_pct:>+7.2f}%{flag}")

        split = [s for s in book.schemes if s.is_split]
        if split:
            print()
            print("  * held at more than one broker:")
            for s in split:
                legs = ", ".join(
                    f"{leg.broker} {leg.units:,.3f}u @ {leg.avg_nav:,.2f}"
                    for leg in s.legs)
                print(f"      {s.fund[:60]}: {legs}")

    def _print_allocation(self, book) -> None:
        alloc = book.allocation
        self.log.section("ALLOCATION")
        for label, rows in (("Asset class", alloc.get("by_asset_class")),
                            ("Plan", alloc.get("by_plan")),
                            ("Broker", alloc.get("by_broker")),
                            ("AMC", alloc.get("by_amc"))):
            if not rows:
                continue
            print(f"  {label}:")
            for row in rows[:8]:
                bar = "#" * int(row["weight_pct"] / 4)
                print(f"    {row['label'][:34]:<34} {row['weight_pct']:>6.2f}%  "
                      f"{_inr(row['value']):>16}  {bar}")
            print()

        print(f"  Concentration HHI : {alloc.get('hhi', 0):,.1f} "
              f"(top scheme {alloc.get('top_weight_pct', 0):.1f}%)")
        regular = float(alloc.get("regular_plan_value") or 0)
        if regular > 0:
            print(f"  Regular plans     : {_inr(regular)} — a direct plan of the "
                  f"same scheme saves the distributor trail each year")

    def _print_sips(self, book) -> None:
        self.log.section("SIPs")
        active, paused = book.active_sips, book.paused_sips
        print(f"  Active: {len(active)}   Paused: {len(paused)}   "
              f"Monthly commitment: {_inr(book.monthly_sip_outflow)}")
        print()
        print(f"  {'Status':<10} {'Fund':<46} {'Amount':>12} "
              f"{'Freq':<10} {'Next':<12} {'Done':>5}")
        print("  " + "-" * 100)
        for s in book.sips:
            print(f"  {s.status:<10} {s.fund[:45]:<46} "
                  f"{s.instalment_amount:>12,.2f} {s.frequency[:9]:<10} "
                  f"{(s.next_instalment or '-')[:10]:<12} "
                  f"{s.completed_instalments:>5}")

    # ── Sub-commands ────────────────────────────────────────────

    def list_sips(self) -> None:
        book = build_book(live=True, log=self.log)
        if not book.sips:
            self.log.warning("No SIPs found on Coin.")
            return
        self._print_sips(book)

    def search(self, query: str) -> None:
        self.log.section(f"SCHEME SEARCH — {query!r}")
        ensure_catalog(log=self.log)
        results = search_catalog(query, limit=30)
        if not results:
            self.log.warning("No schemes matched. Try fewer words.")
            return
        print(f"  {'Scheme code':<16} {'NAV':>10}  Name")
        print("  " + "-" * 96)
        for row in results:
            print(f"  {row['scheme_code']:<16} {row['nav']:>10,.2f}  "
                  f"{row['name'][:64]}")
        print()
        print("  Add one with:  --add --scheme <code> --units N --nav X "
              "--broker \"Broker name\"")

    def add_external(self, *, scheme_code: str, units: float, avg_nav: float,
                     broker: str, folio: str = "", notes: str = "") -> None:
        ensure_catalog(log=self.log)
        from modes.mf.catalog import scheme_by_code
        meta = scheme_by_code(scheme_code)
        if not meta:
            self.log.warning(f"{scheme_code} is not in the Coin catalogue — "
                             f"it will be tracked, but NAV may not resolve.")
        try:
            holding_id = add_external_holding(
                scheme_code=scheme_code,
                fund=str(meta.get("name") or scheme_code),
                units=units, avg_nav=avg_nav, broker=broker,
                folio=folio, notes=notes,
            )
        except ValueError as exc:
            self.log.error(f"Could not add holding: {exc}")
            return
        self.log.success(
            f"Tracking {meta.get('name') or scheme_code} at {broker} "
            f"(#{holding_id}): {units:,.3f} units @ {avg_nav:,.4f}")

    def list_external(self) -> None:
        self.log.section("EXTERNALLY-HELD FUNDS")
        rows = external_holdings()
        if not rows:
            self.log.warning("Nothing tracked outside Coin yet.")
            return
        print(f"  {'ID':>4}  {'Broker':<16} {'Fund':<46} "
              f"{'Units':>11} {'Avg NAV':>10}")
        print("  " + "-" * 92)
        for h in rows:
            print(f"  {h.holding_id:>4}  {h.broker[:15]:<16} {h.fund[:45]:<46} "
                  f"{h.units:>11,.3f} {h.avg_nav:>10,.4f}")

    def remove_external(self, holding_id: int) -> None:
        if remove_external_holding(holding_id):
            self.log.success(f"Removed external holding #{holding_id}")
        else:
            self.log.warning(f"No external holding with id {holding_id}")

    def refresh_scheme_codes(self) -> None:
        self.log.section("AMFI SCHEME MAP")
        mapping = refresh_scheme_map(log=self.log)
        self.log.success(f"{len(mapping):,} ISINs mapped to AMFI scheme codes")


__all__ = ["MFManager", "asset_class", "plan_kind", "SRC_COIN"]
