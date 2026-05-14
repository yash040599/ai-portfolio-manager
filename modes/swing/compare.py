"""
modes/swing/compare.py
======================

Side-by-side comparison of up to N (default 4) swing candidates.
Pure read-only — calls `SwingScanner.scan_one()` per symbol and
arranges the results into a metrics-x-stocks matrix with a
"winner per row" annotation so the dashboard / CLI can highlight
which name is best on each axis.

Two entry points:

  * `compare_symbols(symbols, scanner, swing_capital)` — explicit list.
  * `compare_sector(sector, scanner, swing_capital, n=4)` — auto-pick
    the top-N stocks in `sector` from `SECTOR_MAP` and feed them to
    `compare_symbols`.

Used by:
  * `GET /api/swing/compare?symbols=A,B,C,D` and `?sector=BANKING`.
  * `python main.py --mode swing --compare A,B,C,D` and
    `--compare-sector BANKING`.

No side-effects: every comparison is in-memory only. The underlying
`scan_one()` does write a 1-symbol `swing_runs` row per stock — that's
SEARCH_BOX-tagged and filtered out of the dashboard's main list per
S43, so it's safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from modes.swing.types import (
    SwingCandidate, SETUP_52W_DIP, DIP_SETUP_TYPES,
)


# Cap on how many stocks one comparison can pull in. Picked at 4 so
# the table fits on a 1280-wide laptop screen + the user's request
# was "compare upto 4 stocks". CLI / endpoint refuse anything above
# this so a typo can't accidentally pull a 50-stock fan-out.
MAX_COMPARE_STOCKS = 4


# ── Metric definitions ─────────────────────────────────────────
#
# Each row in the comparison table is one metric. `direction` tells
# the renderer which value is "best":
#   "high"  -> larger value wins (composite score, RS, R:R, volume).
#   "low"   -> smaller value wins (today's overall rank — lower is
#              better; suggested-buy / target if you want a cheap
#              entry, etc.).
#   "dip_aware" -> setup-family-aware (e.g. "% Below 52w high"):
#              if all candidates are dip-buy → higher wins (deeper
#              dip = better entry); if all are momentum-side →
#              lower wins (closer to the 52w high = stronger);
#              mixed → no winner highlight.
#   "high_dip" -> larger value wins, but ONLY meaningful for dip-buy
#              candidates (so a name in dip territory doesn't win
#              against an at-52w-high name on this row).
#   "true"  -> True wins (weekly trend up / above SMA-200).
#   "neutral" -> no winner highlight (display-only).
#   "rsi"   -> closest to 50 wins (oversold AND overbought are bad
#              signals; the sweet spot is the middle).
#
# The renderer is free to ignore the direction (e.g. for a CLI
# table) — it's just an annotation.


@dataclass
class CompareRow:
    label: str
    values: list[str]              # one cell per stock, already formatted
    raw: list[float | bool | None] # numeric values for winner picking
    direction: str                 # see comment above
    winner_idx: int | None = None  # index of winning column, None if no winner
    explain: str = ""              # one-line tooltip


@dataclass
class CompareResult:
    symbols: list[str]
    candidates: list[SwingCandidate]   # one per symbol, may be REJECTED
    rows: list[CompareRow] = field(default_factory=list)
    sector: str = ""                   # populated when triggered by --compare-sector
    notes: list[str] = field(default_factory=list)

    def winner_overall(self) -> str | None:
        """Return the symbol with the most "winner" cells across rows.
        None if no row has a winner. Used as the headline takeaway
        line ("HDFCBANK wins 7 of 11 metrics")."""
        if not self.rows or not self.symbols:
            return None
        wins = [0] * len(self.symbols)
        total = 0
        for r in self.rows:
            if r.winner_idx is not None:
                wins[r.winner_idx] += 1
                total += 1
        if total == 0:
            return None
        best = max(range(len(wins)), key=lambda i: wins[i])
        return self.symbols[best]

    def win_count(self, idx: int) -> int:
        return sum(1 for r in self.rows if r.winner_idx == idx)


# ── Public API ────────────────────────────────────────────────


def normalise_sector(sector_query: str) -> str:
    """Map free-text sector input to SECTOR_MAP keys.

    Accepts "banking" / "BANK" / "Banks" / etc and returns the
    canonical key ("BANKING"). Returns the input upper-cased when
    no match — caller can decide whether to error out.
    """
    if not sector_query:
        return ""
    q = sector_query.strip().upper()
    aliases = {
        "BANK":     "BANKING",
        "BANKS":    "BANKING",
        "BANKING":  "BANKING",
        "FIN":      "FINANCE",
        "NBFC":     "FINANCE",
        "FINANCE":  "FINANCE",
        "TECH":     "IT",
        "IT":       "IT",
        "PHARMA":   "PHARMA",
        "HEALTH":   "PHARMA",
        "HEALTHCARE": "PHARMA",
        "AUTO":     "AUTO",
        "AUTOMOBILE": "AUTO",
        "ENERGY":   "ENERGY",
        "OIL":      "ENERGY",
        "OILGAS":   "ENERGY",
        "METALS":   "METALS",
        "METAL":    "METALS",
        "FMCG":     "FMCG",
        "CONSUMER": "FMCG",
        "INFRA":    "INFRA",
        "POWER":    "INFRA",
        "TELECOM":  "TELECOM",
        "TELECOMM": "TELECOM",
        "CAPGOODS": "CAPGOODS",
        "CAPITAL":  "CAPGOODS",
        "ENGINEERING": "CAPGOODS",
        "DEFENCE":  "CAPGOODS",
    }
    return aliases.get(q, q)


def top_n_in_sector(sector: str, n: int = MAX_COMPARE_STOCKS) -> list[str]:
    """Return up to N NSE symbols in `sector` (canonical SECTOR_MAP key).

    Order follows SECTOR_MAP insertion order, which puts the
    higher-cap names first by convention (e.g. HDFCBANK before
    YESBANK in BANKING). This is good enough as an "auto-populate"
    seed; the user can edit the symbol list afterwards.
    """
    if not sector:
        return []
    # Lazy import — keeps swing-mode boot light when compare isn't used.
    from modes.trade.stock_scanner import SECTOR_MAP
    sector = sector.strip().upper()
    return [sym for sym, sec in SECTOR_MAP.items() if sec == sector][:n]


def list_known_sectors() -> list[str]:
    """All distinct SECTOR_MAP values, alphabetically. Used for the
    dashboard dropdown + CLI help."""
    from modes.trade.stock_scanner import SECTOR_MAP
    return sorted(set(SECTOR_MAP.values()))


def compare_symbols(
    symbols: list[str],
    *,
    scan_one: Callable[[str], tuple[SwingCandidate, Any]],
    sector: str = "",
) -> CompareResult:
    """Run scan_one for each symbol, build the comparison matrix.

    `scan_one` is a callable so this module doesn't have to know
    how to construct a `SwingScanner` (which would require Zerodha
    login). Pass it from the dashboard endpoint or the CLI driver.
    """
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    # De-dup preserving order.
    seen: set[str] = set()
    syms = [s for s in syms if not (s in seen or seen.add(s))]
    if not syms:
        return CompareResult(symbols=[], candidates=[], sector=sector,
                             notes=["No symbols supplied."])
    if len(syms) > MAX_COMPARE_STOCKS:
        return CompareResult(
            symbols=syms[:MAX_COMPARE_STOCKS],
            candidates=[],
            sector=sector,
            notes=[
                f"Truncated to {MAX_COMPARE_STOCKS} symbols "
                f"(asked for {len(syms)})."
            ],
        )

    cands: list[SwingCandidate] = []
    notes: list[str] = []
    for sym in syms:
        try:
            cand, _action = scan_one(sym)
            cands.append(cand)
        except Exception as exc:
            # Build a placeholder candidate so the column still renders.
            placeholder = SwingCandidate(
                symbol=sym, status="REJECTED",
                rejected_reason=f"scan_one failed: {exc}",
            )
            cands.append(placeholder)
            notes.append(f"{sym}: {exc}")

    rows = _build_rows(cands)
    return CompareResult(
        symbols=syms, candidates=cands, rows=rows, sector=sector, notes=notes,
    )


# ── Row builder ───────────────────────────────────────────────


def _fmt_rs(v: float | None) -> str:
    if v is None:
        return "—"
    return f"Rs.{float(v):,.2f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{float(v):+.2f}%"


def _fmt_pct_unsigned(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{float(v):.2f}%"


def _fmt_num(v: float | None, places: int = 2) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.{places}f}"


def _fmt_bool(v: bool | None) -> str:
    if v is None:
        return "—"
    return "✓" if v else "✗"


def _winner_high(raw: list) -> int | None:
    nums = [(i, x) for i, x in enumerate(raw)
            if isinstance(x, (int, float)) and x is not None]
    if not nums:
        return None
    return max(nums, key=lambda kv: kv[1])[0]


def _winner_low(raw: list) -> int | None:
    nums = [(i, x) for i, x in enumerate(raw)
            if isinstance(x, (int, float)) and x is not None]
    if not nums:
        return None
    return min(nums, key=lambda kv: kv[1])[0]


def _winner_true(raw: list) -> int | None:
    trues = [i for i, x in enumerate(raw) if x is True]
    return trues[0] if trues else None


def _winner_rsi(raw: list) -> int | None:
    """Closest to 50 wins (sweet spot)."""
    nums = [(i, x) for i, x in enumerate(raw)
            if isinstance(x, (int, float)) and x is not None and x > 0]
    if not nums:
        return None
    return min(nums, key=lambda kv: abs(kv[1] - 50.0))[0]


def _pick_winner(direction: str, raw: list,
                 cands: list[SwingCandidate] | None = None) -> int | None:
    if direction == "high":
        return _winner_high(raw)
    if direction == "low":
        return _winner_low(raw)
    if direction == "true":
        return _winner_true(raw)
    if direction == "rsi":
        return _winner_rsi(raw)
    if direction == "high_dip":
        # Only count rows whose candidate is a dip-buy setup.
        return _winner_high(raw)
    if direction == "dip_aware":
        # "% Below 52w high" means OPPOSITE things depending on setup
        # family:
        #   * Continuation / momentum setups (BREAKOUT, TREND_CONT,
        #     PULLBACK, SUPPORT_REV) — LOWER % (closer to high) is
        #     stronger; those names have momentum.
        #   * Dip-buy setups (52W_DIP / ATH_DIP) — HIGHER % (deeper
        #     dip) is the better entry by definition.
        # Mixed comparison → no winner highlight (it would be
        # misleading to crown either side).
        # Origin: 2026-05-14 user reported "lower % marked green even
        # when HDFC had a bigger drop". Pre-fix this row was hard-
        # coded `direction="low"` so HDFC@25.91% (dip) would lose
        # to SBIN@20.62% (also a dip) — wrong winner for the dip-buy
        # interpretation.
        if not cands:
            return None
        sides = {(c.setup_type in DIP_SETUP_TYPES) for c in cands}
        if len(sides) != 1:
            return None  # mixed — no clear winner
        is_dip_side = next(iter(sides))
        return _winner_high(raw) if is_dip_side else _winner_low(raw)
    return None  # neutral


def _build_rows(cands: list[SwingCandidate]) -> list[CompareRow]:
    """Build the metrics-x-stocks matrix as a list of `CompareRow`."""
    rows: list[CompareRow] = []

    def _row(label: str, getter: Callable[[SwingCandidate], Any],
             fmt: Callable[[Any], str], direction: str,
             explain: str = "") -> None:
        raw = [getter(c) for c in cands]
        values = [fmt(v) for v in raw]
        winner = _pick_winner(direction, raw, cands)
        rows.append(CompareRow(
            label=label, values=values, raw=raw,
            direction=direction, winner_idx=winner, explain=explain,
        ))

    # Today's overall rank (lower wins). The home page sorts by this
    # — it's THE answer to "which stock does the bot pick first?".
    # Pulled from the latest full-scan run (skipping SEARCH_BOX +
    # snapshot rows). When a candidate is missing from that run we
    # fall back to its in-memory `priority_rank` (set by scan_one),
    # which the picker treats as a number so the row still renders.
    # Origin: 2026-05-14 user asked "Can we have an overall score so
    # we know which is better" — composite scores aren't directly
    # comparable across setup families (technical 0-10 vs dip-buy
    # 18-30%) so the unified rank is the only honest answer.
    from modes.swing.persistence import latest_full_scan_rank_by_symbol

    def _rank_for(c: SwingCandidate) -> float | None:
        info = latest_full_scan_rank_by_symbol(c.symbol)
        if info is not None:
            return float(info[0])
        # Fall back to the in-memory rank from the just-ran scan_one,
        # but only if it's > 0 (0 = "never ranked").
        r = int(getattr(c, "priority_rank", 0) or 0)
        return float(r) if r > 0 else None

    def _rank_fmt(v: float | None) -> str:
        if v is None:
            return "—"
        return f"#{int(v)}"

    _row("Today's overall rank (lower wins)", _rank_for, _rank_fmt,
         "low",
         "Single bot-wide ranking across BOTH technical and dip-buy "
         "candidates — lower number = bot picks this stock first. "
         "Composite scores below are NOT directly comparable across "
         "setup families; this row is.")

    # Status / setup
    _row("Status", lambda c: c.status,
         lambda v: str(v) if v else "—", "neutral")
    _row("Setup", lambda c: c.setup_type,
         lambda v: (v or "—").replace("_", " ").title(), "neutral")
    _row("Composite score (per-setup scale)",
         lambda c: float(c.score) if c.score else None,
         lambda v: _fmt_num(v, 1), "high",
         "Within a single setup family, higher = stronger signal. "
         "Across families the scales differ (technical 0-10 vs "
         "dip-buy 18-30+ %) — use the rank row above for the "
         "cross-family comparison.")
    _row("Sector", lambda c: c.sector,
         lambda v: str(v) if v else "—", "neutral")

    # Price & 52w high
    _row("Current price", lambda c: c.close_price or None,
         _fmt_rs, "neutral")
    _row("52w high (rolling)", lambda c: c.ath_price or None,
         _fmt_rs, "neutral")
    _row("% Below 52w high", lambda c: c.dip_from_ath_pct,
         _fmt_pct_unsigned, "dip_aware",
         "Setup-aware winner: for momentum setups (BREAKOUT, "
         "PULLBACK, TREND_CONT, SUPPORT_REV), CLOSER to the high "
         "(lower %) wins. For 52W dip-buy setups, DEEPER dip "
         "(higher %) wins. Mixed setups → no winner.")

    # Trend stack
    _row("Above SMA-200 (long-term up)",
         lambda c: (c.close_price > c.sma_200) if (c.sma_200 or 0) > 0 else None,
         _fmt_bool, "true")
    _row("Above SMA-50 (medium-term up)",
         lambda c: (c.close_price > c.sma_50) if (c.sma_50 or 0) > 0 else None,
         _fmt_bool, "true")
    _row("Above EMA-20 (short-term up)",
         lambda c: (c.close_price > c.ema_20) if (c.ema_20 or 0) > 0 else None,
         _fmt_bool, "true")
    _row("Weekly trend up", lambda c: bool(c.weekly_trend_up),
         _fmt_bool, "true",
         "10-week SMA rising — filters short-term noise.")

    # Momentum / RS
    _row("RSI(14)", lambda c: c.rsi_daily or None,
         lambda v: _fmt_num(v, 1), "rsi",
         "Closest to 50 = healthy. <30 oversold (risky), "
         ">70 overbought (extended).")
    _row("RS vs NIFTY (60d)",
         lambda c: c.relative_strength if c.relative_strength is not None else None,
         _fmt_pct, "high",
         "Higher = stock outperforming the index — momentum signal.")
    _row("Volume ratio (today vs 20d avg)",
         lambda c: c.volume_ratio or None,
         lambda v: _fmt_num(v, 2) + "x" if v is not None else "—", "high",
         "Higher = more institutional interest today.")

    # Risk plan (only meaningful for ACCEPTED candidates)
    _row("Suggested entry", lambda c: c.entry_price or None,
         _fmt_rs, "neutral")
    _row("Stop loss", lambda c: c.stop_price or None, _fmt_rs, "neutral")
    _row("Target", lambda c: c.target_price or None, _fmt_rs, "neutral")
    _row("Suggested qty", lambda c: c.suggested_qty or None,
         lambda v: f"{int(v)}" if v else "—", "neutral")
    _row("R:R ratio", lambda c: c.rr_ratio or None,
         lambda v: f"{float(v):.2f}x" if v is not None else "—", "high",
         "Reward divided by risk — higher = more favourable trade.")

    return rows


# ── CLI table renderer ────────────────────────────────────────


def render_text_table(result: CompareResult) -> str:
    """Pretty text table for terminal output. Used by the
    `--compare` / `--compare-sector` CLI sub-commands."""
    if not result.symbols:
        return "No symbols to compare."
    syms = result.symbols
    header_label = "Metric"
    # Column widths
    label_w = max(len(header_label),
                  max((len(r.label) for r in result.rows), default=0)) + 2
    col_w = max(14, max(len(s) for s in syms) + 2)
    out: list[str] = []
    if result.sector:
        out.append(f"\nCompare — sector {result.sector} (top "
                   f"{len(syms)} by SECTOR_MAP order)\n")
    else:
        out.append("\nCompare\n")
    # Header row
    out.append(f"  {header_label:<{label_w}}" +
               "".join(f"| {s:^{col_w-2}} " for s in syms))
    out.append("  " + "-" * (label_w + (col_w * len(syms))))
    # Body rows
    for r in result.rows:
        cells = []
        for i, v in enumerate(r.values):
            mark = " *" if r.winner_idx == i else "  "
            cells.append(f"| {(v or '')[:col_w-3]:^{col_w-3}}{mark}")
        out.append(f"  {r.label:<{label_w}}" + "".join(cells))
    # Winner summary
    overall = result.winner_overall()
    if overall is not None:
        wc = [(s, result.win_count(i)) for i, s in enumerate(syms)]
        wc.sort(key=lambda kv: -kv[1])
        out.append("")
        out.append("  Winner counts:")
        for s, n in wc:
            tag = " <- best overall" if s == overall else ""
            out.append(f"    {s:<14} {n} winning metric(s){tag}")
    if result.notes:
        out.append("")
        out.append("  Notes:")
        for n in result.notes:
            out.append(f"    - {n}")
    out.append("")
    return "\n".join(out)
