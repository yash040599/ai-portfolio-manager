# Portfolio-Analyser Roadmap

This document is the **history log**, **backlog**, and **rejection
log** for the `--mode analyze` portfolio analyser, plus its
dashboard surface (`/portfolio`). It is the analyse-mode counterpart
of [TRADE_ROADMAP.md](TRADE_ROADMAP.md) and uses the same conventions
(see "How to add a new item" in TRADE_ROADMAP for the format and the
dual-direction trigger rules; they apply here verbatim).

Scope:

- Read-only review of the user's Zerodha demat holdings.
- **Long-term lens** throughout: 6–24 month horizon for
  `MEDIUM`, 2-3 year for `LONG`. No intraday. No F&O. No order
  placement (per [IDEATIONS.md §1](IDEATIONS.md#hard-constraints-added-2026-04-28)).
- Default flow is **NoAI** (zero Claude cost). `--ai` is the opt-in
  upgrade that adds qualitative / narrative fields on top of the
  same NoAI base. Symmetric with `--mode trade`.
- Surfaced on the dashboard as the new default landing page
  (`/portfolio`); per-stock drill-down (`/portfolio/<symbol>`)
  exposes "Analyse with AI / without AI" buttons. Dashboard items
  live in [`modes/dashboard/docs/DASHBOARD_ROADMAP.md`](../modes/dashboard/docs/DASHBOARD_ROADMAP.md)
  with the `D` prefix; cross-references between the two roadmaps
  are explicit.
- Dashboard current-price and P&L display may use the shared live
   Zerodha quote overlay planned as Dashboard D30. That overlay is
   display-only and does not replace explicit analyse runs.

---

## Current Posture (2026-08-10)

| Area | Status |
|---|---|
| Stage | **Working and in regular use.** 17 analyse runs, 613 stock analyses recorded in `data/portfolio_analyses.db`. |
| Trading cost | **Zero** — read-only, places no orders, needs no subscription beyond the data plan already paid for. |
| Standing | Across the four modes this is the one with a positive contribution and no open blocker. |

Worth stating plainly, because the trading modes absorb most of the attention:
this mode's leverage scales with the size of the existing holding, not with a
trade edge. A 1% better allocation decision on the demat book is worth more than
a year of intraday Gap-and-Go at Rs.50K (see
[TRADE_ROADMAP.md](TRADE_ROADMAP.md) unit economics), and it costs nothing to
run. Keep it current.

Known limitation recorded 2026-08-10 while writing
[`tests/test_scoring.py`](../tests/test_scoring.py): `coverage_pct` is measured
per **pillar**, not per input. The valuation pillar counts as fully covered on
dividend yield alone with no P/E, so "100% coverage" overstates how much data
the model actually had. Not yet fixed — changing it moves displayed scores.

---

## Design Principles

These principles bind every Pending and Completed item below.

### 1. NoAI is the floor; AI is an overlay

The NoAI run produces a complete `StockAnalysis` for every holding
using only deterministic data (Zerodha API + cached candles +
public NSE/BSE feeds). The AI run **adds fields to that same
record** — it does not replace it. Numbers that can be measured
deterministically (price, P&L, 52w high/low, dividend yield,
sector, beta, weighted P/E, technical-indicator snapshot) come
from NoAI even when AI is enabled, because they are more accurate
and reproducible. AI fills only what AI is uniquely good at:
qualitative thesis, narrative risk read, news context, peer/
competitive framing, comparison to prior analysis.

This split means AI cost is bounded (no AI call is wasted on
something we already know deterministically) and the report is
honest about which numbers come from where.

### 2. Every field is stamped `{value, source, as_of}`

Static-business data (sector / industry / promoter holding) and
fundamental aggregates (P/E, ROE, dividends) can be days or weeks
old by the time the user reads the report. Every field carries:

- `source` — `zerodha_api`, `candle_cache`, `nse_public`,
  `claude_pro_2026-05-11`, etc.
- `as_of` — ISO timestamp when the value was last fetched.

The report and the dashboard render the **most stale** `as_of`
prominently at the top of each per-stock card so the user knows
how fresh the analysis actually is. Stale fields are auto-refreshed
when the user clicks "Analyse now" on the dashboard.

### 3. Persistence is the contract

Every analyse run is written to `data/portfolio_analyses.db` (a new
SQLite store, schema in P2). The dashboard reads from this DB; it
does NOT re-run analysis on every page load. "Analyse now"
explicitly triggers a new run; the page then re-reads the DB.

Live current price/current value/P&L on `/portfolio` are a separate
dashboard overlay: the page may poll Zerodha quotes for visible symbols
and recompute displayed P&L with an `as_of` timestamp, but it must not
mutate the persisted analysis or recommendation history.

This keeps the dashboard responsive (DB read is microseconds,
analysis is seconds-to-minutes) and gives free historical
diffing — the dashboard can show "Action changed from HOLD to BUY
MORE on 2026-05-08" by reading the prior row.

### 4. Reports are read once, used once

The `.txt` and `.json` reports written today
(`reports/portfolio/<YYYY>/<MM>/portfolio_report_DD.txt` etc.)
remain — they are useful for offline review and CA hand-off. The
`.tsv` "sheet" file is dropped (P5) — nobody reads it, the
dashboard fully replaces its summary-table use case, and keeping
it in lockstep with the new field schema is busywork.

### 5. Long-term horizon throughout

Recommendation actions stay deliberately coarse:
HOLD / BUY MORE / AVERAGE DOWN / PARTIAL EXIT / FULL EXIT / NEW IDEA.
Conviction stays Low / Medium / High. Horizon stays
SHORT (<6 months) / MEDIUM (6-18) / LONG (2-3 years). No intraday
score, no entry/exit time, no SL/target — those belong to
`--mode trade`. The long-term lens is what makes the analyser
useful as a separate tool.

### 6. "What's missing" is part of the deliverable

A portfolio analyser that only describes existing holdings is a
review, not an analyser. Industry-standard analysers also surface
**gaps**: sector under-allocation vs benchmark, asset-class missing,
single-stock concentration > 25%, group-concentration risk
(e.g. all Adani names), missing defensive ballast (FMCG / pharma)
for a cyclically-tilted book. P7 is the engine for this.

---

## Status Overview

### Pending (0 items)

Foundation P1-P7 all shipped 2026-05-12. Next wave will land here as
operational use surfaces gaps (e.g. quarterly seed-refresh tooling,
multi-account aggregation, alerting). Add new items in priority order
following the same convention as TRADE_ROADMAP.

### Pending — Awaiting Data (0 items)

Items will land here once we have ≥ 30 days of live `--mode analyze`
runs in `data/portfolio_analyses.db` and can measure things like
"recommendation hit-rate by conviction tier".

### Removed (1 item — not worth implementing)

| # | Item | Reason |
|---|------|--------|
| P-X | **Per-stock fundamental auto-fetch (P/E, ROE, debt-to-equity) from a free public source.** | No free source is reliable enough across the NIFTY100 universe (yfinance fundamentals lag and miss splits, screener.in scraping violates ToS, MoneyControl HTML changes weekly). P3 ships with a hand-maintained `data/fundamentals_seed.json` instead. Re-evaluate when an exchange-grade free feed exists. |

### Completed (9 items)

Shipped 2026-05-12 in a single sweep across P1-P9 (the `P8`
industry-standard risk-metrics block + `P9` market-cap tier landed
during the same review pass that closed P1-P7). Dashboard surface
(D24-D29) shipped in lockstep — see [DASHBOARD_ROADMAP.md](../modes/dashboard/docs/DASHBOARD_ROADMAP.md).

| # | Improvement | Category | Date |
|---|-------------|----------|------|
| P1 | `Field[T]` + `StockAnalysis` + `PortfolioMetrics` + `SectorWeight` + `GapFlag` + `GapAnalysis` + `PortfolioSnapshot` dataclasses with per-field `{value, source, as_of, note}` and full JSON round-trip. [`modes/analyze/types.py`](../modes/analyze/types.py). | Infra | 2026-05-12 |
| P2 | SQLite store at `data/portfolio_analyses.db` (two tables `portfolio_runs` + `stock_analyses`, indexes on `(symbol, run_id DESC)` + `(started_at DESC)`). Read helpers `latest_run` / `latest_snapshot` / `latest_for_symbol` / `history_for_symbol` / `stocks_for_run` / `runs_between`. [`modes/analyze/persistence.py`](../modes/analyze/persistence.py). | Infra | 2026-05-12 |
| P3 | NoAI enrichment pipeline. Live Zerodha quotes (single batch), 1y daily candles per stock, sector from existing `SECTOR_MAP`, 250-day beta vs NIFTY 50, dividend yield from `data/dividends_seed.json`, P/E from `data/fundamentals_seed.json`, SMA-50/200/RSI-daily from candle history. Each field stamped with `source` + `as_of`. [`modes/analyze/enrich_noai.py`](../modes/analyze/enrich_noai.py) + [`modes/analyze/recommendation_rules.py`](../modes/analyze/recommendation_rules.py) (7-branch deterministic rule engine). | Indicators | 2026-05-12 |
| P4 | AI overlay (`--ai` opt-in). Claude prompt inlines NoAI numbers as fixed context — model writes only the qualitative `ai_*` slots (thesis / risks / peer comparison / news / change-vs-prior / action). [`modes/analyze/enrich_ai.py`](../modes/analyze/enrich_ai.py). | Indicators | 2026-05-12 |
| P5 | Report-writer rewrite. Clean `.txt` + `.json` output with most-stale `as_of` header, per-field source tags, mode badge. Dropped `.tsv` sheet entirely (nobody read it; dashboard summary table fully replaces). [`modes/analyze/report.py`](../modes/analyze/report.py). | Infra | 2026-05-12 |
| P6 | Industry-standard portfolio metrics — HHI, top-5 concentration, single-name max, group concentration (Adani / Tata / Bajaj / etc.), weighted P/E, weighted dividend yield, portfolio beta vs NIFTY. [`modes/analyze/metrics.py`](../modes/analyze/metrics.py). | Indicators | 2026-05-12 |
| P7 | "What's missing" engine. UNDER_ALLOCATED (sector vs benchmark gap > 5pp), MISSING_DEFENSIVE (cyclicals-heavy book without FMCG/pharma ballast), CONCENTRATION (single-name > 25%), GROUP_RISK (group > 30%). Suggestions from `data/analyse_candidates.json` (held names blocked from suggestions). [`modes/analyze/gaps.py`](../modes/analyze/gaps.py). | Risk | 2026-05-12 |
| P8 | Risk / return / cash-position metrics on top of P6 — annualised volatility (60-day window × √252), Sharpe ratio (RFR=7%), max drawdown across prior runs, CAGR (two-point approximation), annual dividend estimate (Σ DPS×qty), cash balance + cash drag % with `CASH_DRAG` gap flag at > 25% of total account value. New config knobs: `RISK_FREE_RATE_PCT`, `CASH_DRAG_FLAG_PCT`, `ANALYZE_VOL_LOOKBACK_DAYS`. | Risk | 2026-05-12 |
| P9 | Market-cap tier (LARGE / MID / SMALL / ETF / UNKNOWN) classification per AMFI definitions. New optional `market_cap_tier` field on `StockAnalysis` populated from [`data/market_cap_tier.json`](../data/market_cap_tier.json) (semi-annual refresh). New `cap_tier_weights` portfolio metric sums weights per tier. Surfaced as a card on `/portfolio`, a column on the holdings table, a row on the drill-down market context, and a banner in the .txt report. Fail-open: missing seed → `UNKNOWN` bucket flagged in the UI as "refresh seed". | Indicators | 2026-05-12 |
| Bug fix | Tz-aware vs naive datetime crash in `PortfolioSnapshot.most_stale_at()` aborted the persist + report step every time the dashboard's "Analyse Now" ran end-to-end with real Zerodha data. Kite's `historical_data()` returns tz-aware IST datetimes; everything else uses naive IST via `now_ist()`. Fix: a `_to_naive()` helper in [`modes/analyze/enrich_noai.py`](../modes/analyze/enrich_noai.py) strips tzinfo at the source. Belt-and-braces in [`modes/analyze/types.py`](../modes/analyze/types.py): `Field.staleness_minutes`, `StockAnalysis.most_stale_at`, `PortfolioSnapshot.most_stale_at` all normalise tz-aware timestamps to naive before any subtraction or `min()`. | Bug fix | 2026-05-12 |

### Reference seed files (data/, hand-curated, quarterly refresh)

All carry a `_meta` block with `as_of` + refresh cadence so a
reviewer can see at a glance how stale the inputs are.

| File | Purpose | Refresh cadence |
|------|---------|-----------------|
| [`data/fundamentals_seed.json`](../data/fundamentals_seed.json) | TTM P/E per symbol; loss-makers / ETFs are explicitly null. | Quarterly (after results) |
| [`data/dividends_seed.json`](../data/dividends_seed.json) | TTM dividend per share — used for yield computation. | Quarterly |
| [`data/benchmark_sector_weights.json`](../data/benchmark_sector_weights.json) | NIFTY100 sector benchmark used by the gap engine. | Quarterly (after NIFTY rebalance) |
| [`data/analyse_candidates.json`](../data/analyse_candidates.json) | Approved candidate pool per sector with one-line rationales (used for "Suggested additions"). | Quarterly |
| [`data/promoter_groups.json`](../data/promoter_groups.json) | Promoter-group membership map (Adani / Tata / Bajaj / HDFC / ...) used by group-concentration metric. | Quarterly |
| [`data/market_cap_tier.json`](../data/market_cap_tier.json) | AMFI mcap-tier classification (LARGE / MID / SMALL / ETF). | Semi-annually (after AMFI publication, Jan / Jul) |

---

## Pending — Details

Long-form context for each pending item. Same priority order as the
table above.

### P1 — `PortfolioSnapshot` + `StockAnalysis` dataclasses

**Today.** [`modes/analyze/analyser.py:96-100`](../modes/analyze/analyser.py)
returns `portfolio` as a `list[dict]` of free-form keys filled by
`shared/market_data.py::enrich`. The Claude analysis is then a
parallel `list[dict]` keyed by symbol. There is no schema, no
provenance, no AI/NoAI split, no `as_of`. Adding a field means
hoping every consumer remembers to read it.

**Fix.** Two dataclasses in a new `modes/analyze/types.py`:

```python
@dataclass(frozen=True)
class Field[T]:
    value: T | None
    source: str          # "zerodha_api" | "candle_cache" | "claude_pro" | ...
    as_of: datetime      # IST naive
    note: str = ""       # optional — e.g. "manual seed 2026-05-01"

@dataclass
class StockAnalysis:
    symbol: str
    exchange: str
    # Position
    qty: Field[int]
    avg_buy_price: Field[float]
    current_price: Field[float]
    invested_value: Field[float]
    current_value: Field[float]
    pnl: Field[float]
    pnl_pct: Field[float]
    # Market context
    high_52w: Field[float]
    low_52w: Field[float]
    sector: Field[str]
    industry: Field[str]
    beta_vs_nifty: Field[float]
    dividend_yield_ttm: Field[float]
    weighted_pe: Field[float]
    # Technical (long-term flavour: SMA50, SMA200, RSI-daily)
    sma_50: Field[float]
    sma_200: Field[float]
    rsi_daily: Field[float]
    above_sma_200: Field[bool]
    # Rule-based recommendation (NoAI)
    rule_action: Field[str]               # HOLD / BUY MORE / ...
    rule_conviction: Field[str]           # Low / Medium / High
    rule_horizon: Field[str]
    rule_target_price: Field[str]
    rule_reasoning: Field[str]
    # AI overlay (None when NoAI)
    ai_thesis_long_term: Field[str] | None = None
    ai_qualitative_risks: Field[list[str]] | None = None
    ai_peer_comparison: Field[str] | None = None
    ai_news_context: Field[str] | None = None
    ai_change_vs_prior: Field[str] | None = None
    ai_action: Field[str] | None = None    # AI's own action call

@dataclass
class PortfolioSnapshot:
    timestamp: datetime
    mode: str                # "NOAI" | "AI"
    holdings: list[StockAnalysis]
    metrics: PortfolioMetrics  # P6
    gaps: GapAnalysis          # P7
```

`Field.staleness_minutes` and `PortfolioSnapshot.most_stale_as_of`
are computed properties so the report and the dashboard can render
"Last refreshed: 2 hours 14 min ago".

**Effort.** Medium. The data classes themselves are an afternoon;
the consumer migration (`analysis_queue`, `report_writer`,
`performance_tracker`) is the bulk.

### P2 — SQLite persistence (`data/portfolio_analyses.db`)

**Today.** Each run writes `portfolio_data_DD.json`; reading prior
runs requires globbing the filesystem. The dashboard cannot
efficiently fetch "latest analysis for SYMBOL" without parsing
every JSON in the tree. The existing `data/trades.db` solves the
same problem for the trade ledger; same pattern applies here.

**Fix.** Two tables, defined in `modes/analyze/persistence.py`:

```sql
CREATE TABLE portfolio_runs (
    run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL,        -- ISO IST
    finished_at      TEXT,
    mode             TEXT NOT NULL,         -- 'NOAI' | 'AI'
    holdings_count   INTEGER,
    portfolio_value  REAL,
    portfolio_pnl    REAL,
    metrics_json     TEXT,                  -- PortfolioMetrics (P6)
    gaps_json        TEXT,                  -- GapAnalysis (P7)
    notes            TEXT
);
CREATE TABLE stock_analyses (
    run_id           INTEGER NOT NULL REFERENCES portfolio_runs(run_id),
    symbol           TEXT NOT NULL,
    exchange         TEXT NOT NULL,
    -- Indexed columns for fast list queries
    action           TEXT,                  -- rule_action OR ai_action when AI
    conviction       TEXT,
    horizon          TEXT,
    target_price     TEXT,
    current_value    REAL,
    pnl              REAL,
    pnl_pct          REAL,
    most_stale_at    TEXT NOT NULL,
    -- Full record
    analysis_json    TEXT NOT NULL,         -- StockAnalysis serialised
    PRIMARY KEY (run_id, symbol)
);
CREATE INDEX idx_stock_analyses_symbol_run ON stock_analyses(symbol, run_id DESC);
```

Read helpers:

- `latest_run() -> PortfolioRun`
- `latest_for_symbol(symbol) -> StockAnalysis | None`
- `history_for_symbol(symbol, limit=10) -> list[StockAnalysis]`
- `runs_between(d_from, d_to) -> list[PortfolioRun]`

**Effort.** Medium — the DB shape is small; the migration logic
mirrors `shared/tax_db.py`.

### P3 — NoAI enrichment pipeline

**Today.** `shared/market_data.py::enrich` already fetches live
quotes and a 52-week range via `Zerodha.get_quotes()` +
`get_historical()`. Sector / industry / beta / dividend yield /
fundamentals are not collected at all. The Claude prompt currently
asks Claude to fill those by guessing, which is exactly the
anti-pattern this rework targets.

**Fix.** `modes/analyze/enrich_noai.py` orchestrates four small
fetchers, each independently testable and each writing into a
typed `StockAnalysis`:

1. **Position** — from `Zerodha.get_holdings()` (qty, avg, P&L).
2. **Quote + 52w** — from `Zerodha.get_quotes()` and 1-year
   `get_historical()` (cached). `as_of` = quote timestamp.
3. **Sector / industry** — from a static `data/sector_map.json`
   that we already maintain for the trade-mode sector boost. Each
   row carries `as_of` = file mtime so users see when the map was
   last edited.
4. **Beta vs NIFTY** — rolling 250-day daily-return covariance
   between the symbol's candle-cache series and NIFTY's. Computed
   in `modes/analyze/metrics.py`. `as_of` = the latest candle
   close used.
5. **Dividend yield TTM** — sum of last-4-quarters' dividends ÷
   current price. Source: `data/dividends.json` (manually
   maintained list, refreshed monthly). `as_of` = file mtime.
6. **Weighted P/E** — pulled from `data/fundamentals_seed.json`
   (manual). `as_of` = file mtime. Auto-fetch is rejected; see
   the Removed table.
7. **Technical snapshot (long-term)** — SMA-50, SMA-200,
   RSI-daily-14 from `shared/technical_indicators.py` against the
   daily-candle cache. `as_of` = latest candle close.

**Rule-based recommendation.** A small deterministic engine in
`modes/analyze/recommendation_rules.py`:

```
if pnl_pct < -25 and above_sma_200 == False and rsi_daily < 35:
    rule_action = "AVERAGE DOWN"
    rule_conviction = "Medium"
    rule_horizon = "LONG"
elif pnl_pct > 50 and rsi_daily > 70 and current_price > 0.95 * high_52w:
    rule_action = "PARTIAL EXIT"
    ...
else:
    rule_action = "HOLD"
    ...
```

This makes the NoAI report useful by itself even without Claude.

**Effort.** High — many small fetchers each with retry / cache /
provenance plumbing.

### P4 — AI overlay

**Today.** `modes/trade/analysis_queue.py::run()` sends a per-stock
prompt that asks Claude for everything: the price, the P&L, the
horizon, the action, the rationale. Claude regenerates numbers
the deterministic pipeline already knows.

**Fix.** New `modes/analyze/enrich_ai.py` accepts a list of
NoAI-enriched `StockAnalysis` and:

1. Builds a per-stock prompt that **inlines** the deterministic
   numbers as fixed context (so Claude can cite them but cannot
   change them).
2. Asks for only: long-term thesis (3-5 bullets), qualitative
   risks (3-5 bullets), peer comparison (one paragraph), news /
   macro context (one paragraph if any material item in the last
   30 days), AI's own action recommendation + conviction + brief
   why.
3. Returns a partial `StockAnalysis` containing only the AI-only
   fields, which the caller merges into the existing record.

CLI: default flow is now NoAI for `--mode analyze` (matches
`--mode trade`). `--ai` enables P4. `--noai` is the explicit-NoAI
flag for symmetry.

**Cost target.** Pro plan ~Rs.5/stock × 30 holdings ≈ Rs.150/run.
Free plan (Haiku) target ~Rs.1/stock. AI cost shown in run
summary so the user sees the trade-off.

### P5 — Report-writer rewrite

**Today.** `modes/trade/report_writer.py` writes both trading and
portfolio reports — the file mixes two unrelated layouts. The
analyse `.tsv` sheet is generated and never consumed (we have
`reports/portfolio/2026/03/portfolio_sheet_*.tsv` with no
script that reads them).

**Fix.** New `modes/analyze/report.py` owns the analyse output
exclusively. Layout:

```
================================================================
  PORTFOLIO ANALYSIS — 2026-05-12 14:31 IST   [NOAI run]
  Most stale field: 2026-05-10 (sector_map.json) · 2 days old
  Holdings: 32  ·  Value: Rs.5.59 L  ·  P&L: Rs.+37,366 (+7.2%)
================================================================

PORTFOLIO METRICS (P6)
  Top sector:        Banks/Financials  (38.2%)  ← over-allocated
  HHI concentration: 1840  (healthy, < 2500)
  Top 5 by value:    HDFCBANK 12% · ICICIBANK 9% · ...
  Beta vs NIFTY:     1.07
  Weighted dividend yield: 1.4%

WHAT'S MISSING (P7)
  ⚠ No FMCG defensive ballast (current 0.0% vs benchmark 9.2%).
    Suggested: HINDUNILVR / NESTLEIND / ITC.
  ⚠ Pharma under-weight (2.1% vs benchmark 6.8%).
    Suggested: SUNPHARMA / DRREDDY.
  ⚠ Single-stock concentration: HDFCBANK 12% — within tolerance,
    monitor.

PER-STOCK ANALYSIS

  ───────────────────────────────────────────────────────────
  HDFCBANK (NSE)               2 hr 14 min ago
  ───────────────────────────────────────────────────────────
  Position:    180 shares  Avg Rs.1,520  Current Rs.1,612  [zerodha · live]
  P&L:         Rs.+16,560  (+6.05%)
  52-week:     Rs.1,402 – Rs.1,795  [zerodha · live]
  Sector:      BANKS — PRIVATE  [sector_map.json · 12 days ago]
  Beta:        0.98  [candle_cache 250d · today]
  Div yield:   1.7%  [dividends.json · 5 days ago]
  P/E:         18.9  [fundamentals_seed.json · 5 days ago]
  RSI(14d):    62.1  ·  Above SMA200: yes  [candle_cache · today]

  RULE-BASED ACTION:  HOLD  ·  Conviction Medium  ·  Horizon LONG
  ──────────────────────────────────────────────────────────
  AI-only fields below this line (run with --ai to populate):
  Thesis (long-term):  [run with --ai]
  Risks:               [run with --ai]
  Peer comparison:     [run with --ai]
  News context:        [run with --ai]
```

When run with `--ai`, the bottom block fills in. When run with
`--noai` (default), the bottom block stays as a placeholder so
the user knows what `--ai` would add.

**Effort.** Medium — the layout is straightforward; the bulk of
the work is wiring `Field.source` / `Field.as_of` into every line.

### P6 — Industry-standard portfolio metrics

Module: `modes/analyze/metrics.py`. Exposes `compute_metrics(holdings)
-> PortfolioMetrics` dataclass with all the fields listed in the
table above. Formulas follow standard definitions:

- **HHI** = Σ (weight_i)² × 10000.
- **Top-5 concentration** = sum of top-5 weights.
- **Group concentration** = sum of weights for any group with > 1
  holding (group map static; refresh quarterly).
- **Portfolio beta** = Σ (weight_i × beta_i) — using the per-stock
  beta from P3.
- **Weighted P/E** = Σ (weight_i × pe_i) when pe_i is positive.
- **Simple volatility 30d** = std-dev of daily portfolio returns
  over the last 30 trading days, scaled to annualised
  (× √252).

Each metric carries its own `as_of` (the most stale input).

### P7 — "What's missing" engine

Module: `modes/analyze/gaps.py`. Two reference files (under
`data/`, hand-maintained, refreshed quarterly):

- `data/benchmark_sector_weights.json` — NIFTY100 sector weights.
- `data/analyse_candidates.json` — per-sector approved candidate
  list with one-line rationale each.

Algorithm:

1. Compute `your_weights = sector_weights(holdings)`.
2. For each sector in benchmark, compute `gap = bench - your`.
3. Flag any gap > `GAP_PP_THRESHOLD` (default 5 percentage
   points) as `UNDER_ALLOCATED`.
4. Flag missing defensive ballast: if `your[FMCG] + your[PHARMA]
   + your[CONSUMER] < 10%` AND `your[CYCLICAL_TOTAL] > 60%`,
   raise `MISSING_DEFENSIVE`.
5. Flag concentration: any single name > `SINGLE_NAME_MAX_PCT`
   (default 25%); any group > `GROUP_MAX_PCT` (default 30%).
6. Build `Suggestion` objects: each `UNDER_ALLOCATED` gap pulls
   2-3 names from `analyse_candidates.json` for that sector.

When `--ai`, P4 adds a rationale paragraph per Suggestion. When
NoAI, the suggestion is the bare list with the gap framing.

---

## Cross-roadmap dependency map

```
  ANALYZE_ROADMAP            DASHBOARD_ROADMAP
  ───────────────            ─────────────────
                              D24 (reframe + new default)
                                ↓ depends on nothing
  P1 (types)  ─────────────►  D25 (/portfolio reads DB)
  P2 (DB)     ─────────────►  D25
  P3 (NoAI)   ─────────────►  D25, D26
  P4 (AI)     ─────────────►  D26 (drill-down + AI button), D27
  P5 (report) — independent of dashboard
  P6 (metrics) ────────────►  D25 (summary card)
  P7 (gaps)    ────────────►  D25 (what's-missing panel)
                              D28 (login flow on dashboard)
                              D29 (latest-vs-prior diff)
```

---

## Conventions (recap, mirrored from TRADE_ROADMAP)

1. **Verify the gap is real before adding a Pending item.** Open
   the file, confirm the assumed condition matches the actual
   code. Cite line numbers.
2. **Pick the next available number.** P-prefix; current free is
   P8 and above (P-X is the only Removed slot used so far).
3. **Update Pending → Pending — Details together.** A row in the
   table without a Details block is a planning hole.
4. **Ship-now-with-removal-trigger** and
   **Remove-with-readd-trigger** rules apply (see TRADE_ROADMAP
   "Dual-direction triggers" section). Every shipped change carries a
   falsifiable rollback condition; every removal carries a
   re-add condition.
5. **Don't touch trading code from analyse-mode work.** The
   isolation rule from `modes/dashboard/` applies here too:
   `modes/analyze/` may not import from `modes/trade/order_engine`,
   `modes/trade/manager`, or any module that places orders.
