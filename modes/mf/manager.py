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


def _pct(value) -> str:
    return "-" if value is None else f"{float(value):+.1f}%"


def _mag(value) -> str:
    """Volatility and drawdown are magnitudes, not returns — no sign."""
    return "-" if value is None else f"{abs(float(value)):.1f}%"


def _wrap(text: str, width: int) -> list[str]:
    words = str(text).split()
    lines: list[str] = []
    line = ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines


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

    # ── Insights ────────────────────────────────────────────────

    def insights(self, *, live: bool = False, refresh_history: bool = False) -> None:
        """Structural review of the book (no buy/sell calls by design)."""
        from modes.mf.insights import build_insights

        self.log.section("MUTUAL FUND REVIEW")
        init_db()
        book = build_book(live=live, log=self.log)
        if not book.holdings:
            self.log.warning("No mutual-fund holdings to review.")
            return

        if refresh_history:
            from modes.mf.catalog import refresh_nav_history
            self.log.info("Refreshing NAV history (one call per scheme)...")
            ensure_catalog(log=self.log)
            refresh_nav_history([s.scheme_code for s in book.schemes],
                                log=self.log)

        ins = build_insights(book)
        self._print_findings(ins)
        self._print_accumulation(ins)
        self._print_clusters(ins)
        self._print_consolidation(ins)
        self._print_risk(ins, book)

    def _print_findings(self, ins: dict) -> None:
        findings = ins.get("findings") or []
        if not findings:
            return
        self.log.section("WHAT STANDS OUT")
        for f in findings:
            print(f"  [{f['severity']:<6}] {f['title']}")
            if f.get("detail"):
                for line in _wrap(f["detail"], 88):
                    print(f"            {line}")
            print()

    def _print_accumulation(self, ins: dict) -> None:
        a = ins["accumulation"]
        self.log.section("ACCUMULATION vs DORMANT CORPUS")
        print(f"  New money      : {_inr(a['monthly_inflow'])}/month "
              f"({_inr(a['annual_inflow'])}/yr = {a['inflow_rate_pct']:.1f}% "
              f"of the book)")
        print(f"  Receiving money: {a['funded_count']} schemes, "
              f"{_inr(a['funded_value'])}")
        print(f"  Dormant        : {a['dormant_count']} schemes, "
              f"{_inr(a['dormant_value'])} ({a['dormant_pct']:.1f}% of corpus)")
        if a["funded"]:
            print()
            print("  Where new money goes:")
            for row in a["funded"]:
                print(f"    {_inr(row['sip_amount']):>12}  "
                      f"{row['sip_share_pct']:>5.1f}%  "
                      f"{row['exposure'][:22]:<24}{row['fund'][:40]}")
        rows = a.get("corpus_vs_inflow") or []
        if rows:
            print()
            print(f"    {'asset class':<18}{'corpus':>9}{'new money':>12}{'gap':>9}")
            for r in rows:
                print(f"    {r['label']:<18}{r['corpus_pct']:>8.1f}%"
                      f"{r['inflow_pct']:>11.1f}%{r['gap']:>+8.1f}")

    def _print_clusters(self, ins: dict) -> None:
        self.log.section("EXPOSURE MAP")
        print(f"  {'exposure':<26}{'funds':>6}{'weight':>9}{'dormant':>9}  "
              f"{'monthly SIP':>12}")
        print("  " + "-" * 74)
        for c in ins["clusters"]:
            mark = "  <-- same bet held more than once" if c["is_redundant"] else ""
            print(f"  {c['label'][:25]:<26}{c['fund_count']:>6}"
                  f"{c['weight_pct']:>8.1f}%{c['dormant_count']:>9}  "
                  f"{_inr(c['sip_amount']):>12}{mark}")

        pairs = ins.get("correlated_pairs") or []
        if pairs:
            print()
            print("  Funds whose NAVs move together (r >= 0.90):")
            for p in pairs[:8]:
                print(f"    r={p['correlation']:.2f}  {p['a_fund'][:34]:<36} "
                      f"| {p['b_fund'][:34]}")

    def _print_consolidation(self, ins: dict) -> None:
        options = ins.get("consolidation") or []
        tax = ins["tax"]
        self.log.section("CONSOLIDATION & TAX")
        print(f"  LTCG exemption : {_inr(tax['ltcg_exemption'])}/FY  "
              f"(remaining {_inr(tax['headroom'])})")
        print(f"  Unrealised     : gain {_inr(tax['unrealised_gain'])}, "
              f"loss {_inr(tax['unrealised_loss'])}")
        print(f"  Note           : {tax['equity_oriented_note']}")
        print("  Caveat         : per-lot purchase dates are not available "
              "from the broker,")
        print("                   so long-term status must be verified before "
              "acting.")
        if not options:
            print()
            print("  No dormant duplicates to merge.")
            return
        print()
        for o in options:
            fits = ("fits this year's exemption" if o["fits_exemption"]
                    else "exceeds the exemption or is not equity-oriented")
            print(f"  {o['cluster']}: keep {o['keep'][:44]}")
            for m in o["merge"]:
                print(f"    merge {m['fund'][:46]:<48} "
                      f"{_inr(m['value']):>12}  gain {_inr(m['gain']):>12}")
            print(f"    -> frees {_inr(o['freed_value'])}, realises "
                  f"{_inr(o['gain_realised'])} \u2014 {fits}")
            print()

    def _print_risk(self, ins: dict, book) -> None:
        cov = ins.get("nav_history_coverage") or {}
        risk = ins.get("risk") or {}
        if not risk:
            self.log.section("RETURN & RISK")
            print("  No NAV history stored yet. Run with --refresh-history "
                  "to download it (one call per scheme).")
            return
        self.log.section("RETURN & RISK (from NAV history)")
        print(f"  Coverage: {cov.get('have', 0)}/{cov.get('total', 0)} schemes")
        print()
        print(f"  {'fund':<44}{'1Y':>8}{'3Y':>8}{'5Y':>8}{'vol':>8}{'maxDD':>8}")
        print("  " + "-" * 84)
        for s in book.schemes:
            p = risk.get(s.scheme_code)
            if not p:
                continue
            print(f"  {s.fund[:43]:<44}{_pct(p['cagr_1y']):>8}"
                  f"{_pct(p['cagr_3y']):>8}{_pct(p['cagr_5y']):>8}"
                  f"{_mag(p['volatility']):>8}{_mag(p['max_drawdown']):>8}")
        print()
        print("  CAGR is annualised from published NAVs, so it measures the "
              "fund itself,")
        print("  not your entry timing. Compare funds within the same "
              "exposure row above.")

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
