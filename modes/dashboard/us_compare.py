# ================================================================
# modes/dashboard/us_compare.py
# ================================================================
# Side-by-side comparison of up to 4 US stocks, mirroring the
# Indian Swing compare (`modes/swing/compare.py`) so the dashboard
# can reuse the same JS renderer (`_renderCompareResult` in
# `swing_page.py`).
#
# Same payload shape (rows, winner_idx, winners_idx, win_counts,
# winner_overall, sector, notes), same row ordering convention,
# same directional winner picks ("high" / "low" / "true" / "rsi"
# / "neutral" / "dip_aware"). The US-specific differences:
#
#   - Sectors come from a hard-coded US100 sector map below
#     (Yahoo's sector labels are inconsistent enough that we
#     just curate by hand).
#   - Benchmark for "RS vs index" is SPY, not NIFTY.
#   - No "in Config.SCAN_UNIVERSE" / "ranked in latest scan" rows
#     because the US universe is the US100 list, not a per-run
#     audit set.
# ================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


MAX_COMPARE_STOCKS = 4


# ── Curated sector map for the US100 universe ─────────────────
#
# Order within each sector list matters — the dashboard's "pick a
# sector" dropdown auto-fills the top-N from this list, so the
# first 4 should be the highest-conviction peers in that sector.
US_SECTOR_MAP: dict[str, list[str]] = {
    "MEGACAP_TECH": ["AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "META", "AMZN"],
    "SEMICONDUCTORS": ["NVDA", "AVGO", "AMD", "QCOM", "TXN", "ADI", "MU",
                       "KLAC", "AMAT", "ANET"],
    "SOFTWARE": ["MSFT", "ORCL", "CRM", "ADBE", "NOW", "INTU", "PANW",
                 "PLTR", "IBM"],
    "BANKS": ["JPM", "BAC", "WFC", "C", "GS", "BLK", "SCHW"],
    "FINANCE_PAYMENTS": ["V", "MA", "SPGI", "ICE", "PGR", "CB"],
    "HEALTHCARE": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "PFE", "ABT",
                   "AMGN", "BMY", "GILD", "VRTX", "TMO", "DHR", "ISRG",
                   "SYK", "MDT", "ELV", "CI", "MCK"],
    "CONSUMER_STAPLES": ["PG", "KO", "PEP", "WMT", "COST", "PM", "MO",
                         "TGT"],
    "CONSUMER_DISC": ["AMZN", "HD", "MCD", "LOW", "SBUX", "TJX", "NKE",
                      "BKNG", "DIS"],
    "ENERGY": ["XOM", "CVX", "COP", "SLB"],
    "INDUSTRIALS": ["BA", "CAT", "DE", "GE", "HON", "RTX", "LMT", "UNP",
                    "ETN", "UPS"],
    "MEGACAP_INTERNET": ["GOOGL", "GOOG", "META", "AMZN", "NFLX", "DIS"],
    "UTILITIES": ["NEE", "DUK", "SO"],
    "TELECOM": ["T", "VZ", "CMCSA"],
    "MATERIALS": ["LIN", "SHW"],
    "REAL_ESTATE": ["PLD", "EQIX"],
    "ETFS": ["SPY", "QQQ", "DIA", "IWM"],
}


_ALIASES: dict[str, str] = {
    "TECH":     "MEGACAP_TECH",
    "BIGTECH":  "MEGACAP_TECH",
    "MEGACAP":  "MEGACAP_TECH",
    "SEMI":     "SEMICONDUCTORS",
    "SEMIS":    "SEMICONDUCTORS",
    "CHIPS":    "SEMICONDUCTORS",
    "SAAS":     "SOFTWARE",
    "BANK":     "BANKS",
    "BANKING":  "BANKS",
    "FIN":      "FINANCE_PAYMENTS",
    "FINANCE":  "FINANCE_PAYMENTS",
    "PAYMENTS": "FINANCE_PAYMENTS",
    "HEALTH":   "HEALTHCARE",
    "PHARMA":   "HEALTHCARE",
    "STAPLES":  "CONSUMER_STAPLES",
    "RETAIL":   "CONSUMER_DISC",
    "DISCRETIONARY": "CONSUMER_DISC",
    "OIL":      "ENERGY",
    "OILGAS":   "ENERGY",
    "INDUSTRIAL": "INDUSTRIALS",
    "INTERNET": "MEGACAP_INTERNET",
    "MEDIA":    "MEGACAP_INTERNET",
    "UTIL":     "UTILITIES",
    "UTILITY":  "UTILITIES",
    "TELCO":    "TELECOM",
    "REIT":     "REAL_ESTATE",
    "REITS":    "REAL_ESTATE",
    "ETF":      "ETFS",
}


# ── Data classes ──────────────────────────────────────────────


@dataclass
class CompareRow:
    label: str
    values: list[str]
    raw: list[Any]
    direction: str  # "high" | "low" | "true" | "rsi" | "neutral" | "dip_aware"
    winner_idx: int | None = None
    winners_idx: list[int] = field(default_factory=list)
    explain: str = ""


@dataclass
class CompareResult:
    symbols: list[str]
    rows: list[CompareRow] = field(default_factory=list)
    sector: str = ""
    notes: list[str] = field(default_factory=list)

    def winner_overall(self) -> str | None:
        if not self.rows or not self.symbols:
            return None
        wins = [0] * len(self.symbols)
        total = 0
        for r in self.rows:
            ws = r.winners_idx or (
                [r.winner_idx] if r.winner_idx is not None else [])
            for w in ws:
                if 0 <= w < len(wins):
                    wins[w] += 1
                    total += 1
        if total == 0:
            return None
        best = max(range(len(wins)), key=lambda i: wins[i])
        return self.symbols[best]

    def win_count(self, idx: int) -> int:
        n = 0
        for r in self.rows:
            ws = r.winners_idx or (
                [r.winner_idx] if r.winner_idx is not None else [])
            if idx in ws:
                n += 1
        return n


# ── Public API ────────────────────────────────────────────────


def normalise_sector(sector_query: str) -> str:
    if not sector_query:
        return ""
    q = sector_query.strip().upper().replace(" ", "_").replace("-", "_")
    return _ALIASES.get(q, q)


def top_n_in_sector(sector: str, n: int = MAX_COMPARE_STOCKS) -> list[str]:
    if not sector:
        return []
    sector = sector.strip().upper()
    return list(US_SECTOR_MAP.get(sector, []))[:n]


def list_known_sectors() -> list[str]:
    """All distinct US_SECTOR_MAP keys, alphabetical. Used by the
    /us page's compare-sector dropdown."""
    return sorted(US_SECTOR_MAP.keys())


def compare_symbols(
    symbols: list[str],
    *,
    analyse_one: Callable[[str], dict],
    sector: str = "",
) -> CompareResult:
    """Run analyse_one for each symbol, build the comparison matrix.
    Mirrors `modes.swing.compare.compare_symbols` so the same JS
    renderer works on /us."""
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    seen: set[str] = set()
    syms = [s for s in syms if not (s in seen or seen.add(s))]
    if not syms:
        return CompareResult(symbols=[], sector=sector,
                             notes=["No symbols supplied."])
    if len(syms) > MAX_COMPARE_STOCKS:
        syms = syms[:MAX_COMPARE_STOCKS]
        notes_pre = [f"Truncated to {MAX_COMPARE_STOCKS} symbols."]
    else:
        notes_pre = []

    rows_data: list[dict] = []
    notes: list[str] = list(notes_pre)
    for sym in syms:
        try:
            row = analyse_one(sym)
            row["_symbol"] = sym  # ensure present even if analyser dropped it
            rows_data.append(row)
        except Exception as exc:
            rows_data.append({"_symbol": sym, "_error": str(exc)})
            notes.append(f"{sym}: {exc}")

    matrix_rows = _build_rows(rows_data)
    return CompareResult(symbols=syms, rows=matrix_rows,
                         sector=sector, notes=notes)


# ── Formatting helpers ────────────────────────────────────────


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct_unsigned(v: float | None) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_num(v: float | None, places: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{places}f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_bool(v) -> str:
    if v is None:
        return "—"
    return "\u2713" if v else "\u2717"


# ── Winner-pickers (mirror the swing helpers) ──────────────────


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and v is not None


def _pick_winners_all(direction: str, raw: list,
                      rows: list[dict] | None = None) -> list[int]:
    """Return ALL indices that tie for the winning cell, per the
    given direction. Mirrors `_pick_winners_all` in swing/compare."""
    if direction == "neutral" or not raw:
        return []
    nums = [(i, v) for i, v in enumerate(raw)
            if _is_num(v) and v is not None]
    bools = [(i, bool(v)) for i, v in enumerate(raw)
             if isinstance(v, bool)]

    if direction == "true":
        return [i for i, v in bools if v]

    if direction == "high":
        if not nums:
            return []
        best = max(v for _, v in nums)
        return [i for i, v in nums if v == best]

    if direction == "low":
        if not nums:
            return []
        best = min(v for _, v in nums)
        return [i for i, v in nums if v == best]

    if direction == "rsi":
        # Closest to 50 wins.
        if not nums:
            return []
        best_diff = min(abs(v - 50.0) for _, v in nums)
        return [i for i, v in nums if abs(v - 50.0) == best_diff]

    if direction == "dip_aware":
        # For 52W_DIP setups, deeper dip wins; for momentum setups,
        # closer-to-high (smaller dip) wins. Mixed → no winner.
        if not rows:
            return []
        setups = [(r.get("setup_type") or "").upper() for r in rows]
        is_dip = ["52W_DIP" in s for s in setups]
        if all(is_dip):
            return _pick_winners_all("high", raw)
        if not any(is_dip):
            return _pick_winners_all("low", raw)
        return []

    return []


def _row(rows: list[CompareRow],
         label: str,
         getter: Callable[[dict], Any],
         fmt: Callable[[Any], str],
         direction: str,
         data: list[dict],
         explain: str = "") -> None:
    raw = [getter(d) for d in data]
    values = [fmt(v) for v in raw]
    winners = _pick_winners_all(direction, raw, data)
    winner = winners[0] if winners else None
    rows.append(CompareRow(
        label=label, values=values, raw=raw,
        direction=direction, winner_idx=winner,
        winners_idx=winners, explain=explain,
    ))


def _ind(d: dict, key: str) -> float | None:
    ind = d.get("indicators") or {}
    v = ind.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_rows(data: list[dict]) -> list[CompareRow]:
    """Build the metrics-x-stocks matrix. Row order mirrors the
    Indian Swing compare so users get the same scan layout on both
    pages."""
    rows: list[CompareRow] = []

    # Status / setup (neutral display rows up top, like swing).
    _row(rows, "Status",
         lambda d: ("ERROR" if d.get("_error")
                    else (d.get("action") or "—")),
         lambda v: str(v), "neutral", data)
    _row(rows, "Setup",
         lambda d: (d.get("setup_type") or "NONE"),
         lambda v: str(v).replace("_", " ").title(), "neutral", data)
    _row(rows, "Composite score",
         lambda d: (None if d.get("_error")
                    else float(d.get("score") or 0.0)),
         lambda v: _fmt_num(v, 1), "high", data,
         "Within a setup family, higher = stronger signal. Across "
         "families (trend vs 52w dip) the scales differ.")
    _row(rows, "Why this score (top reasons)",
         lambda d: list(d.get("reasons") or [])[:3],
         lambda v: " · ".join(v) if v else "—",
         "neutral", data,
         "The analyser's own reason list. Setup-specific signals "
         "may not always be visible in the boolean rows below.")

    # Price + 52w context.
    _row(rows, "Current price",
         lambda d: (float(d.get("current_price") or 0.0)
                    if d.get("current_price") else None),
         _fmt_usd, "neutral", data)
    _row(rows, "52w high (rolling)",
         lambda d: _ind(d, "high_52w"), _fmt_usd, "neutral", data)
    _row(rows, "% Below 52w high",
         lambda d: _ind(d, "dip_from_52w_high_pct"),
         _fmt_pct_unsigned, "dip_aware", data,
         "Setup-aware winner: for momentum setups (BREAKOUT, "
         "PULLBACK, TREND_CONT, SUPPORT_REV), CLOSER to the high "
         "(lower %) wins. For 52W_DIP setups, DEEPER dip "
         "(higher %) wins. Mixed setups -> no winner.")

    # Trend stack.
    def _above(d: dict, ind_key: str) -> bool | None:
        ind = d.get("indicators") or {}
        ref = ind.get(ind_key)
        price = d.get("current_price")
        if not ref or not price:
            return None
        try:
            return float(price) > float(ref)
        except (TypeError, ValueError):
            return None

    _row(rows, "Above SMA-200 (long-term up)",
         lambda d: _above(d, "sma_200"),
         _fmt_bool, "true", data)
    _row(rows, "Above SMA-50 (medium-term up)",
         lambda d: _above(d, "sma_50"),
         _fmt_bool, "true", data)
    _row(rows, "Above EMA-20 (short-term up)",
         lambda d: _above(d, "ema_20"),
         _fmt_bool, "true", data)

    # Momentum / RS.
    _row(rows, "RSI(14)",
         lambda d: _ind(d, "rsi"),
         lambda v: _fmt_num(v, 1), "rsi", data,
         "Closest to 50 = healthy. <30 oversold (risky), "
         ">70 overbought (extended).")
    _row(rows, "RS vs SPY (60d)",
         lambda d: _ind(d, "relative_strength"),
         _fmt_pct, "high", data,
         "Higher = stock outperforming the S&P benchmark.")
    _row(rows, "Volume ratio (today vs 20d avg)",
         lambda d: _ind(d, "volume_ratio"),
         lambda v: (_fmt_num(v, 2) + "x") if v is not None else "—",
         "high", data,
         "Higher = more institutional interest today.")

    # Risk plan.
    _row(rows, "Suggested entry",
         lambda d: (float(d.get("entry_price") or 0.0)
                    if d.get("entry_price") else None),
         _fmt_usd, "neutral", data)
    _row(rows, "Stop loss",
         lambda d: (float(d.get("stop_price") or 0.0)
                    if d.get("stop_price") else None),
         _fmt_usd, "neutral", data)
    _row(rows, "Target",
         lambda d: (float(d.get("target_price") or 0.0)
                    if d.get("target_price") else None),
         _fmt_usd, "neutral", data)
    _row(rows, "Suggested qty",
         lambda d: (float(d.get("suggested_qty") or 0.0)
                    if d.get("suggested_qty") else None),
         lambda v: _fmt_num(v, 2) if v is not None else "—",
         "neutral", data)
    _row(rows, "R:R ratio",
         lambda d: (float(d.get("rr_ratio") or 0.0)
                    if d.get("rr_ratio") else None),
         lambda v: f"{float(v):.2f}x" if v is not None else "—",
         "high", data,
         "Reward divided by risk - higher = more favourable trade.")

    return rows
