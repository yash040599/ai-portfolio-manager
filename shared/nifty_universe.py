"""
shared/nifty_universe.py
========================

**Single source of truth for NIFTY index constituents** used by
every mode in the project. Was previously duplicated in
`modes/trade/stock_scanner.py`; centralised here on 2026-05-14
(roadmap S54) so a single NSE rebalance update propagates to:

    - modes/trade/stock_scanner.py  (intraday Nifty hard-filter)
    - modes/swing/scanner.py        (swing daily-candle universe)
    - modes/swing/ath_scanner.py    (52W dip-buy universe)
    - modes/swing/ath_backtest.py   (10y backtest seed list)
    - modes/dashboard/portfolio_page.py (sector-aware portfolio gaps)
    - modes/swing/compare.py        (Compare-by-sector dropdown)

Layout is incremental — each tier adds exactly 50 more symbols on
top of the previous tier so the CLI's `--nifty 50/100/150/200`
flag maps to a contiguous prefix of the same canonical list:

    NIFTY50  = 50 large-caps (official NSE NIFTY 50 index)
    NIFTY100 = NIFTY50 + NIFTY100_EXTRA   (50 next-tier large caps)
    NIFTY150 = NIFTY100 + NIFTY150_EXTRA  (50 mid caps; SYNTHETIC tier)
    NIFTY200 = NIFTY150 + NIFTY200_EXTRA  (50 mid caps; closes out NSE 200)

NIFTY 50, 100, and 200 are real NSE indices. **NIFTY 150 is a
synthetic intermediate tier** the project carries for the
`--nifty 150` CLI flag — it's the first 50 (alphabetically) of the
NSE NIFTY 200 names that aren't in NIFTY 100. The split is
arbitrary; what matters is the four tiers cover NSE's NIFTY 200
exactly.

Source of truth — NSE official CSVs:

    https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv
    https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv
    https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv

Last refresh: **2026-05-14** (S54). NSE rebalances the NIFTY
indices semi-annually (March + September). Re-run
`scripts/_refresh_nifty_universe.py` after each circular and
diff the result before committing — symbols that vanish must
be checked for open positions in `data/swing.db` /
`data/portfolio_analyses.db` first.

VEDL demerger placeholders (`DUMMYVEDL1..4`) appear in the raw
NSE feed for the four pending Vedanta carve-outs (Vedanta
Aluminium Metal, Talwandi Sabo Power, Malco Energy, Vedanta Iron
and Steel — board approved 2026-05-01, listings rolling out from
mid-May 2026). They're stripped here because the bot can't trade
unlisted tickers; once the real symbols list, they'll be added in
the next refresh + may push some current names out of NIFTY 100.

TATAMOTORS demerger (TMPV / TMCV) is already reflected — TMPV
sits in NIFTY 50, TMCV sits in NIFTY 100.
"""

from __future__ import annotations


# ── NIFTY 50 ────────────────────────────────────────────────────
# 50 largest NSE-listed companies by free-float market cap.
# Anchor of the entire scan universe.
NIFTY50: list[str] = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM",
    "TITAN", "TMPV", "TRENT", "ULTRACEMCO", "WIPRO",
]

# ── NIFTY 100 EXTRA ─────────────────────────────────────────────
# Next 50 large caps beyond the NIFTY 50 — together they form the
# official NSE NIFTY 100. Note: VEDL is still in this list but is
# in active demerger (record date 2026-05-01); on the ex-date the
# scanner's GAP_DOWN_* detection will fire — that gap is a
# corporate-action artifact, not a tradable signal. See module
# docstring for the demerger heads-up.
NIFTY100_EXTRA: list[str] = [
    "ABB", "ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM",
    "BAJAJHLDNG", "BANKBARODA", "BOSCHLTD", "BPCL", "BRITANNIA",
    "CANBK", "CGPOWER", "CHOLAFIN", "CUMMINSIND", "DIVISLAB",
    "DLF", "DMART", "ENRIN", "GAIL", "GODREJCP",
    "HAL", "HDFCAMC", "HINDZINC", "HYUNDAI", "INDHOTEL",
    "IOC", "IRFC", "JINDALSTEL", "LODHA", "LTM",
    "MAZDOCK", "MOTHERSON", "MUTHOOTFIN", "PFC", "PIDILITIND",
    "PNB", "RECLTD", "SHREECEM", "SIEMENS", "SOLARINDS",
    "TATACAP", "TATAPOWER", "TMCV", "TORNTPHARM", "TVSMOTOR",
    "UNIONBANK", "UNITDSPR", "VBL", "VEDL", "ZYDUSLIFE",
]

# ── NIFTY 150 EXTRA (synthetic intermediate tier) ───────────────
# First 50 (alphabetical) names from the NSE NIFTY 200 that aren't
# in NIFTY 100. NIFTY 150 isn't an NSE index — it's a project
# convention so `--nifty 150` lands at exactly 150 stocks for the
# scanner. The choice of "first 50 alphabetical" is arbitrary;
# what matters is the four tiers together cover NSE's NIFTY 200
# exactly without gaps or duplicates.
NIFTY150_EXTRA: list[str] = [
    "360ONE", "ABCAPITAL", "ALKEM", "APLAPOLLO", "ASHOKLEY",
    "ASTRAL", "ATGL", "AUBANK", "AUROPHARMA", "BANKINDIA",
    "BDL", "BHARATFORG", "BHEL", "BIOCON", "BLUESTARCO",
    "BSE", "COCHINSHIP", "COFORGE", "COLPAL", "CONCOR",
    "COROMANDEL", "DABUR", "DIXON", "EXIDEIND", "FEDERALBNK",
    "FORTIS", "GLENMARK", "GMRAIRPORT", "GODFRYPHLP", "GODREJPROP",
    "GROWW", "GVT&D", "HAVELLS", "HEROMOTOCO", "HINDPETRO",
    "HUDCO", "ICICIAMC", "ICICIGI", "IDEA", "IDFCFIRSTB",
    "INDIANB", "INDUSINDBK", "INDUSTOWER", "IRCTC", "IREDA",
    "JSWENERGY", "JUBLFOOD", "KALYANKJIL", "KEI", "KPITTECH",
]

# ── NIFTY 200 EXTRA ─────────────────────────────────────────────
# Final 50 (alphabetical) names from the NSE NIFTY 200 that aren't
# in NIFTY 100 or NIFTY150_EXTRA. Together with the three tiers
# above this completes the official NSE NIFTY 200.
NIFTY200_EXTRA: list[str] = [
    "LAURUSLABS", "LENSKART", "LGEINDIA", "LICHSGFIN", "LTF",
    "LUPIN", "M&MFIN", "MANKIND", "MARICO", "MCX",
    "MFSL", "MOTILALOFS", "MPHASIS", "MRF", "NATIONALUM",
    "NAUKRI", "NHPC", "NMDC", "NYKAA", "OBEROIRLTY",
    "OFSS", "OIL", "PAGEIND", "PATANJALI", "PAYTM",
    "PERSISTENT", "PHOENIXLTD", "PIIND", "POLICYBZR", "POLYCAB",
    "POWERINDIA", "PREMIERENE", "PRESTIGE", "RADICO", "RVNL",
    "SAIL", "SBICARD", "SRF", "SUPREMEIND", "SUZLON",
    "SWIGGY", "TATACOMM", "TATAELXSI", "TATAINVEST", "TIINDIA",
    "UPL", "VMM", "VOLTAS", "WAAREEENER", "YESBANK",
]


# ── Public helpers ──────────────────────────────────────────────

def get_universe(name: str) -> list[str]:
    """Return the symbol list for a named tier.

    Accepts: ``"NIFTY50"`` | ``"NIFTY100"`` | ``"NIFTY150"`` |
    ``"NIFTY200"`` (case-insensitive). Anything else returns an
    empty list — callers should treat that as "use CUSTOM list".
    """
    n = (name or "").strip().upper()
    if n == "NIFTY50":
        return list(NIFTY50)
    if n == "NIFTY100":
        return list(NIFTY50) + list(NIFTY100_EXTRA)
    if n == "NIFTY150":
        return (list(NIFTY50) + list(NIFTY100_EXTRA)
                + list(NIFTY150_EXTRA))
    if n == "NIFTY200":
        return (list(NIFTY50) + list(NIFTY100_EXTRA)
                + list(NIFTY150_EXTRA) + list(NIFTY200_EXTRA))
    return []


def in_universe(symbol: str, universe: str) -> bool:
    """True when `symbol` is in the named tier."""
    return symbol.strip().upper() in set(get_universe(universe))


# Tier sizes — used by tests + `_self_check()` below.
EXPECTED_SIZES: dict[str, int] = {
    "NIFTY50":  50,
    "NIFTY100": 100,
    "NIFTY150": 150,
    "NIFTY200": 200,
}


def _self_check() -> None:
    """Cheap import-time sanity check that catches accidental copy-
    paste duplicates or missed migrations. Raises AssertionError
    on first failure so a bad commit fails fast on `import`."""
    seen: set[str] = set()
    for tier in (NIFTY50, NIFTY100_EXTRA, NIFTY150_EXTRA, NIFTY200_EXTRA):
        for s in tier:
            if s in seen:
                raise AssertionError(
                    f"shared/nifty_universe.py: duplicate symbol {s!r} "
                    f"across tiers — each NIFTY*_EXTRA list must be "
                    f"strictly disjoint from the lower tiers."
                )
            seen.add(s)
    for name, expected in EXPECTED_SIZES.items():
        actual = len(get_universe(name))
        if actual != expected:
            raise AssertionError(
                f"shared/nifty_universe.py: {name} has {actual} symbols, "
                f"expected exactly {expected}. Either a rebalance was "
                f"applied incompletely or an EXTRA tier is the wrong size."
            )


_self_check()


__all__ = [
    "NIFTY50", "NIFTY100_EXTRA", "NIFTY150_EXTRA", "NIFTY200_EXTRA",
    "get_universe", "in_universe", "EXPECTED_SIZES",
]
