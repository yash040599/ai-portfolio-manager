# Portfolio Analyser — Strategy Reference

User-facing reference for `python main.py --mode analyze`. Describes
exactly what the analyser does, what every field on a stock card
means, where each number comes from, how the rule-based action is
chosen, what the AI overlay adds, and how to read the report.

> **Sister docs.** Trading-strategy reference is
> [docs/TRADE_STRATEGY.md](TRADE_STRATEGY.md). Tax reference is
> [docs/TRADE_TAX_GUIDE.md](TRADE_TAX_GUIDE.md). The roadmap (Pending /
> Awaiting-Data / Removed / Completed) is
> [docs/ANALYZE_ROADMAP.md](ANALYZE_ROADMAP.md). Dashboard pages
> (`/portfolio`, `/portfolio/<symbol>`, `/login`) are tracked under
> [DASHBOARD_ROADMAP.md](../modes/dashboard/docs/DASHBOARD_ROADMAP.md).

---

## 0. One-line summary

> Reads your Zerodha demat holdings end-to-end, enriches each name
> with deterministic market data (live price, 52-week range, sector,
> daily-RSI / SMA-50 / SMA-200, beta vs NIFTY, dividend yield, P/E),
> computes industry-standard portfolio metrics (HHI, top-5
> concentration, weighted P/E, weighted dividend yield, Sharpe,
> volatility, max drawdown, CAGR, cash drag), surfaces structural
> gaps ("what's missing"), and emits a long-term action
> recommendation per stock. Optional `--ai` overlay adds qualitative
> thesis / risks / peer comparison / news context via Claude on top
> of the same NoAI base.

NoAI is the default. AI overlay is opt-in (`--ai`) and never
overwrites the deterministic numbers — it only adds qualitative
slots (`ai_thesis_long_term`, `ai_qualitative_risks`,
`ai_peer_comparison`, `ai_news_context`, `ai_change_vs_prior`,
`ai_action`, `ai_action_detail`).

Long-term horizon throughout. No intraday signals, no F&O, no order
placement. Capital-bucket isolation per
[IDEATIONS §1](IDEATIONS.md#hard-constraints-added-2026-04-28).

---

## 1. Pipeline (8 steps)

Implemented by [`modes/analyze/analyser.py`](../modes/analyze/analyser.py)
`PortfolioAnalyser.run()`. Each step is a separate module so a single
failure isolates to one stock or one metric.

| # | Step | Module | What it does |
|---|------|--------|--------------|
| 1 | Validate config | `config.py::validate` | Confirm Zerodha API key present; Claude API key present iff `--ai` |
| 2 | Login + account snapshot | `core/zerodha_client.py::login`, `print_account_snapshot` | Reuses today's cached token if valid (Kite tokens expire at midnight); shows available cash + holdings P&L |
| 3 | Fetch holdings | `core/zerodha_client.py::get_holdings` | Reads every demat row (qty, avg buy price, last price, day P&L) |
| 4 | NoAI enrichment | [`modes/analyze/enrich_noai.py`](../modes/analyze/enrich_noai.py) | Live quotes (single batch call) + 1-year daily candles per stock + reference-seed lookup. Stamps every field with `source` + `as_of` |
| 5 | Portfolio metrics | [`modes/analyze/metrics.py`](../modes/analyze/metrics.py) | HHI, top-5%, single-name max, group concentration, weighted P/E + div yield, beta, **annualised volatility, Sharpe, max-DD, CAGR, cash drag** |
| 6 | Gap analysis | [`modes/analyze/gaps.py`](../modes/analyze/gaps.py) | UNDER_ALLOCATED, MISSING_DEFENSIVE, CONCENTRATION, GROUP_RISK, CASH_DRAG flags + suggested additions from approved-candidate pool |
| 7 | AI overlay (optional) | [`modes/analyze/enrich_ai.py`](../modes/analyze/enrich_ai.py) | Claude prompt with NoAI numbers inlined as fixed context; only qualitative `ai_*` slots are written. Cost ~Rs.5/stock on Pro |
| 8 | Persist + render | [`modes/analyze/persistence.py`](../modes/analyze/persistence.py) + [`modes/analyze/report.py`](../modes/analyze/report.py) | Write `data/portfolio_analyses.db` (two tables) + `reports/portfolio/<YYYY>/<MM>/portfolio_report_DD.{txt,json}` |

Step 4 is the only step that hits live broker / live market data.
Steps 5-7 are pure-Python on the in-memory snapshot. Step 8 is a
single SQLite transaction + two file writes.

---

## 2. NoAI fields — what every number on a stock card means

Schema lives in [`modes/analyze/types.py`](../modes/analyze/types.py)
`StockAnalysis`. Every field is a `Field[T]` with `value`, `source`,
`as_of`, optional `note` — so the report can show "RSI 62.1
(candle_cache · 14 min ago)" without lying about freshness.

### 2.1 Position (live from Zerodha)

| Field | Meaning | Source |
|-------|---------|--------|
| `qty` | Quantity held | `zerodha_api` |
| `avg_buy_price` | FIFO average cost (Zerodha-reported) | `zerodha_api` |
| `current_price` | Last traded price | `zerodha_api` (paid plan) |
| `invested_value` | qty × avg_buy_price | `derived` |
| `current_value` | qty × current_price | `derived` |
| `pnl` | current_value − invested_value | `derived` |
| `pnl_pct` | pnl / invested_value × 100 | `derived` |

### 2.2 Market context

| Field | Meaning | Source |
|-------|---------|--------|
| `high_52w` / `low_52w` | High / low close in last 252 trading days | `candle_cache` |
| `price_vs_high_52w_pct` | (current − 52w_high) / 52w_high × 100 — negative means below high | `derived` |
| `sector` / `industry` | Mapped from [`modes/trade/stock_scanner.SECTOR_MAP`](../modes/trade/stock_scanner.py) — same map intraday uses | `sector_map` |
| `market_cap_tier` | One of `LARGE` / `MID` / `SMALL` / `ETF` / `UNKNOWN`. AMFI mcap-tier classification (top-100 = LARGE, 101-250 = MID, 251+ = SMALL). Refreshed from [`data/market_cap_tier.json`](../data/market_cap_tier.json) semi-annually | `sector_map` (seed) |
| `beta_vs_nifty` | Rolling 250-day daily-return covariance vs NIFTY 50, normalised by NIFTY variance. Falls back to 1.0 when NIFTY history < 30 days | `derived` |
| `dividend_yield_ttm` | DPS_TTM / current_price × 100. DPS pulled from hand-curated [`data/dividends_seed.json`](../data/dividends_seed.json), refreshed quarterly | `dividends_seed` |
| `weighted_pe` | TTM P/E from hand-curated [`data/fundamentals_seed.json`](../data/fundamentals_seed.json), refreshed quarterly. Loss-makers / ETFs return null | `fundamentals_seed` |

> **Why hand-curated seeds for fundamentals + dividends?** Because
> every free auto-fetch source we tried (yfinance, screener.in,
> moneycontrol) was either rate-limited, inconsistent on splits, or
> against the source's ToS. Quarterly hand refresh by editing the
> JSON is the honest path for a long-term tool. This is documented
> as `Removed P-X` in [ANALYZE_ROADMAP.md](ANALYZE_ROADMAP.md).

### 2.3 Long-term technical snapshot

| Field | Meaning | Source |
|-------|---------|--------|
| `sma_50` | Simple moving average of last 50 daily closes | `candle_cache` |
| `sma_200` | Same over 200 closes (the long-term trend filter) | `candle_cache` |
| `rsi_daily` | Wilder-smoothed 14-day RSI on daily closes (0-100, neutral at 50) | `candle_cache` |
| `above_sma_200` | True when current_price > sma_200 (trend intact) | `derived` |
| `weight_in_portfolio_pct` | current_value / Σ current_value × 100 (only set after the full snapshot is built) | `derived` |

---

## 3. Rule-based recommendation (NoAI deterministic)

Implemented in [`modes/analyze/recommendation_rules.py`](../modes/analyze/recommendation_rules.py).
A 7-branch decision tree on the per-stock fields above. Each branch
sets `rule_action`, `rule_conviction`, `rule_horizon`, `rule_target_price`,
`rule_reasoning`. Tunable thresholds at the top of the file:

| Constant | Default | Meaning |
|----------|---------|---------|
| `DEEP_LOSS_PCT` | -25.0% | P&L below this triggers AVERAGE-DOWN / PARTIAL-EXIT branches |
| `MILD_LOSS_PCT` | -10.0% | P&L below this AND broken trend triggers PARTIAL EXIT |
| `EXTENDED_GAIN_PCT` | +50.0% | P&L above this with overbought RSI triggers PARTIAL EXIT |
| `NEAR_52W_HIGH_PCT` | -5.0% | Within 5% of 52w high counts as "extended" |
| `RSI_OVERBOUGHT` | 70.0 | Standard Wilder threshold |
| `RSI_OVERSOLD` | 35.0 | Slightly looser than standard 30 to catch reversal-candidates earlier |
| `TREND_BROKEN_PCT` | -8.0% | Price > this much below SMA-200 = trend broken |

### 3.1 Branch order (top-down)

1. **PARTIAL EXIT (extended winner).** P&L ≥ +50% AND within 5% of
   52w high AND RSI ≥ 70. Trim partial; let the rest run.
2. **HOLD (winner with room).** P&L ≥ +25% AND above SMA-200 AND
   RSI < 70. Long-term trend intact.
3. **AVERAGE DOWN (deep loss + repairing trend).** P&L ≤ -25% AND
   above SMA-200 AND RSI ≥ 40. Add in tranches if thesis intact.
4. **PARTIAL EXIT (deep loss + broken trend).** P&L ≤ -25% AND below
   SMA-200 AND RSI ≤ 35. Trend broken; cut at least a third.
5. **PARTIAL EXIT (mild loss + broken trend).** P&L ≤ -10% AND below
   SMA-200. Trim ~25%.
6. **AVERAGE DOWN (oversold near 52w low + repairing trend).** Within
   10% of 52w low AND RSI ≤ 35 AND above SMA-200. Small add only.
7. **HOLD (default).** Anything else.

### 3.2 Output

Every action carries a `Field[str]` with source `rule_engine`. The
`rule_reasoning` field is a single human-readable sentence the
report renders verbatim (e.g. `"Down -28.4% but reclaimed SMA-200
(trend repairing) and RSI 47 above panic. Average in tranches if
thesis intact; do NOT add on a single red day."`).

---

## 4. AI overlay (`--ai` opt-in)

Implemented in [`modes/analyze/enrich_ai.py`](../modes/analyze/enrich_ai.py).
For each holding, sends Claude a prompt that **inlines the
deterministic NoAI numbers as fixed context** so the model can
cite them but cannot regenerate them. The model returns one
free-form answer per qualitative slot:

| AI slot | What Claude writes | Used by |
|---------|--------------------|---------|
| `ai_thesis_long_term` | 3-5 short bullets on the long-term (2-3 year) thesis | Drill-down + report |
| `ai_qualitative_risks` | 3-5 risk bullets specific to the name | Drill-down + report |
| `ai_peer_comparison` | One paragraph comparing to 2-3 closest sector peers | Drill-down + report |
| `ai_news_context` | One paragraph on material news/macro events from the LAST 30 days. "No material events" when nothing applies | Drill-down + report |
| `ai_change_vs_prior` | One paragraph on what has changed vs the prior analyse run for this symbol | Drill-down + report |
| `ai_action` | AI's own recommended action (HOLD / BUY MORE / AVERAGE DOWN / PARTIAL EXIT / FULL EXIT) | Drill-down + summary table |
| `ai_action_detail` | One sentence justifying the action choice | Drill-down + report |

Cost: `holdings × CLAUDE_COST_PER_CALL` (~Rs.5 on Pro, ~Rs.1 on
Free). Per-stock pause `PER_STOCK_PAUSE_SECONDS = 1.0` to stay
under the rate limit. Per-stock retry budget `MAX_RETRIES = 2` for
transient failures.

The dashboard's "Analyse all (AI)" button surfaces the cost
estimate up-front (`estimate_ai_cost(holdings_count)`) so a click
is never a surprise charge.

---

## 5. Portfolio metrics — what every number on the summary page means

Implemented in [`modes/analyze/metrics.py`](../modes/analyze/metrics.py)
`compute_metrics()`. Inputs: list of enriched `StockAnalysis`,
optional `cash_balance` (rupees), optional `prior_runs` list (from
`data/portfolio_analyses.db`).

### 5.1 Concentration

| Metric | Formula | Flag-worthy threshold |
|--------|---------|------------------------|
| `hhi_concentration` | Σ (weight_i × 100)² | > 2500 = concentrated |
| `top_5_concentration_pct` | Σ top-5 weights × 100 | > 60% suggests over-concentration |
| `single_name_max_pct` | max(weights) × 100 | > 25% triggers `CONCENTRATION` flag (gaps.py) |
| `group_concentration` | dict {group → Σ weights} for groups in [`data/promoter_groups.json`](../data/promoter_groups.json) | > 30% on any group triggers `GROUP_RISK` flag |

### 5.2 Sector diversification

`sector_weights` = list of `SectorWeight(sector, weight_pct,
holdings_count)` sorted by weight descending. Sectors come from
the same `SECTOR_MAP` intraday mode uses; "OTHER" absorbs unknowns.

### 5.2.1 Market-cap tier breakdown (P9)

`cap_tier_weights` = `{tier_name: weight_pct}` summed across
holdings. Tiers come from [`data/market_cap_tier.json`](../data/market_cap_tier.json)
(AMFI mcap-tier classification: top-100 = LARGE, 101-250 = MID,
251+ = SMALL; ETFs are tagged separately). A non-zero `UNKNOWN`
bucket is the operator's cue to refresh the seed file. Surfaced as
its own card on `/portfolio` and a column on the holdings table.

### 5.3 Income + valuation

| Metric | Formula | Notes |
|--------|---------|-------|
| `weighted_pe` | size-weighted Σ (w_i × P/E_i), positive PE rows only | Coverage % shown in note |
| `weighted_dividend_yield` | size-weighted Σ (w_i × yield_i), known yields only | Coverage % shown in note |
| `annual_dividend_estimate` | Σ (DPS_TTM_i × qty_i) | Rupees per year |

### 5.4 Risk + return (industry-standard)

| Metric | Formula | Conditions |
|--------|---------|------------|
| `volatility_30d_pct` | size-weighted daily-portfolio-return std × √252 × 100 | Needs ≥ 60 days of cached daily candles per held symbol. Window from `Config.ANALYZE_VOL_LOOKBACK_DAYS = 60` |
| `sharpe_ratio` | (mean_daily_return − rfr_daily) / daily_vol × √252 | Same data prerequisite. RFR from `Config.RISK_FREE_RATE_PCT = 7.0` (India 10y G-Sec) |
| `max_drawdown_pct` | peak-to-trough across prior `portfolio_runs` rows + current run | Needs ≥ 2 prior runs |
| `xirr_pct` | CAGR = (current/oldest)^(1/years) − 1 | Needs oldest prior run ≥ 30 days old. Two-point approximation; true XIRR (with cash flows) deferred until the analyser tracks deposits/withdrawals |
| `portfolio_beta_vs_nifty` | size-weighted Σ (w_i × beta_i) | Per-stock beta from candle_cache vs NIFTY 50 |

### 5.5 Cash position

| Metric | Formula | Flag |
|--------|---------|------|
| `cash_balance` | Zerodha `funds.live_balance` (equity segment) | — |
| `cash_drag_pct` | cash / (cash + invested) × 100 | > `Config.CASH_DRAG_FLAG_PCT = 25.0` triggers `CASH_DRAG` gap flag |

---

## 6. "What's missing" — gap engine

Implemented in [`modes/analyze/gaps.py`](../modes/analyze/gaps.py).
Compares the user's metrics against four reference files (all
hand-curated, refreshed quarterly / semi-annually):

- [`data/benchmark_sector_weights.json`](../data/benchmark_sector_weights.json) — NIFTY100 sector benchmark
- [`data/analyse_candidates.json`](../data/analyse_candidates.json) — approved candidate pool per sector
- [`data/promoter_groups.json`](../data/promoter_groups.json) — promoter-group membership map
- [`data/market_cap_tier.json`](../data/market_cap_tier.json) — AMFI mcap-tier classification (read by enrich_noai for `market_cap_tier` field, then summed by `metrics.compute_metrics()` into `cap_tier_weights`)

### 6.1 Flag categories

| Category | Severity | When it fires |
|----------|----------|----------------|
| `UNDER_ALLOCATED` | INFO if sector held > 0%, WARN if 0% | Sector weight is `GAP_PP_THRESHOLD = 5.0` percentage points below benchmark (excludes "OTHER") |
| `MISSING_DEFENSIVE` | WARN | Combined FMCG + PHARMA < 10% AND cyclicals (AUTO + METALS + ENERGY + INFRA + CAPGOODS) ≥ 60% |
| `CONCENTRATION` | RISK | Single-name weight > `SINGLE_NAME_MAX_PCT = 25.0` |
| `GROUP_RISK` | RISK | Any promoter-group weight > `GROUP_MAX_PCT = 30.0` |
| `CASH_DRAG` | WARN | `cash / (cash + invested) > Config.CASH_DRAG_FLAG_PCT` |

Suggestions are pulled from the candidate pool, **excluding any
symbol the user already holds**. Up to 3 names per under-allocated
sector. Order in the JSON is the priority — operator controls it.

---

## 7. Report layout

`modes/analyze/report.py` writes two files per run:

- **`reports/portfolio/<YYYY>/<MM>/portfolio_report_DD.txt`** — human
  read. Sections (top to bottom):
  1. Header — date, mode badge (`NOAI` / `AI`), most-stale `as_of`
     across all fields, holdings count, headline value + P&L.
  2. PORTFOLIO METRICS — sector weights with bar chart, HHI / top-5 /
     single-name-max, group concentration, weighted P/E + div yield +
     annual dividend estimate, beta. Risk/return block (volatility,
     Sharpe, max-DD, CAGR) when data permits. Cash position when
     available.
  3. WHAT'S MISSING — every flag with severity, headline, detail,
     suggested symbols.
  4. PER-STOCK ANALYSIS — one card per holding with position, P&L,
     52-week range, sector, beta, div yield, P/E, RSI, SMA-200, then
     RULE-BASED ACTION + Conviction + Horizon + Target + Why. AI
     overlay block when `--ai` was set; `[run with --ai to populate]`
     placeholder otherwise.

- **`reports/portfolio/<YYYY>/<MM>/portfolio_data_DD.json`** — full
  `PortfolioSnapshot` serialised. Used by the dashboard as a
  fallback when `data/portfolio_analyses.db` is unreachable, and by
  external tools (CA hand-off, audit).

The legacy `.tsv` "sheet" file is **dropped** as of P5. Nobody read
it; the dashboard's `/portfolio` summary table replaces its
use case.

---

## 8. Persistence model

`data/portfolio_analyses.db` (SQLite, WAL not required — writes
are once per `analyze` invocation). Schema in
[`modes/analyze/persistence.py`](../modes/analyze/persistence.py):

```sql
CREATE TABLE portfolio_runs (
    run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL,        -- ISO IST
    finished_at      TEXT,
    mode             TEXT NOT NULL,         -- 'NOAI' | 'AI'
    holdings_count   INTEGER,
    portfolio_value  REAL,
    portfolio_pnl    REAL,
    metrics_json     TEXT,                  -- PortfolioMetrics serialised
    gaps_json        TEXT,                  -- GapAnalysis serialised
    notes            TEXT
);
CREATE TABLE stock_analyses (
    run_id           INTEGER NOT NULL REFERENCES portfolio_runs(run_id),
    symbol           TEXT    NOT NULL,
    exchange         TEXT    NOT NULL,
    action           TEXT,
    conviction       TEXT,
    horizon          TEXT,
    target_price     TEXT,
    current_value    REAL,
    pnl              REAL,
    pnl_pct          REAL,
    most_stale_at    TEXT NOT NULL,
    analysis_json    TEXT NOT NULL,         -- full StockAnalysis
    PRIMARY KEY (run_id, symbol)
);
CREATE INDEX idx_stock_analyses_symbol_run ON stock_analyses(symbol, run_id DESC);
CREATE INDEX idx_portfolio_runs_started_at ON portfolio_runs(started_at DESC);
```

Read helpers (`latest_run`, `latest_snapshot`, `latest_for_symbol`,
`history_for_symbol`, `stocks_for_run`, `runs_between`) are pure
SQL + JSON decode. Microseconds.

The dashboard reads exclusively from this DB (never re-runs the
enrichment pipeline on page load). Live runs are triggered ONLY by
the "Analyse now" buttons, which spawn a background worker via
[`modes/dashboard/portfolio_actions.py`](../modes/dashboard/portfolio_actions.py).

---

## 9. CLI

```
python main.py --mode analyze         # NoAI (default; no Claude cost)
python main.py --mode analyze --ai    # NoAI base + Claude qualitative overlay
```

Outputs printed at end of run:

```
================================================================
  Analyse run complete
  Holdings : 32  ·  Invested Rs.5,63,246  ·  Current Rs.6,18,924  ·  P&L Rs.+55,678 (+9.89%)
  Most stale field across all enrichment: 2026-05-12 21:46 IST

  Report : reports/portfolio/2026/05/portfolio_report_12.txt
  Data   : reports/portfolio/2026/05/portfolio_data_12.json

  Re-run with --ai for Claude qualitative overlay
================================================================
```

The same data appears live on the dashboard `/portfolio` page; the
dashboard auto-refreshes after each "Analyse now" click.

---

## 10. Dashboard surface

| Page | What it shows |
|------|---------------|
| `/portfolio` | Latest snapshot summary: holdings table, sector weights with bars, concentration + valuation table, risk/return table, cash position, what's-missing panel with suggestions. "Analyse all (NoAI)" + "Analyse all (AI)" buttons spawn background runs |
| `/portfolio/<symbol>` | Per-stock drill-down: position, market context, rule-based recommendation, AI overlay (or placeholder), 5-run history strip with action-drift banner. Re-analyse buttons re-run the FULL portfolio (single-stock-only is intentionally not supported because it would give wrong portfolio metrics) |
| `/login` | Zerodha login page. Shows current token validity. Manual paste-back flow only — paste the redirect URL after Kite OAuth and the dashboard exchanges it for an access token. AUTO/ASSISTED env-driven flows are CLI-only by design (browser context isn't safe for password storage) |

Roadmap items behind these pages: D24-D29 in
[DASHBOARD_ROADMAP.md](../modes/dashboard/docs/DASHBOARD_ROADMAP.md).

---

## 11. Hard rules + design principles

1. **NoAI is the floor; AI is an overlay.** Numbers that can be
   measured deterministically come from NoAI even when AI is
   enabled. AI fills only what AI is uniquely good at.
2. **Every field is stamped `{value, source, as_of}`.** No silent
   freshness loss; the report shows every age tag.
3. **Persistence is the contract.** Every analyse run writes
   `data/portfolio_analyses.db`. The dashboard reads from this DB,
   never re-runs the pipeline on page load.
4. **Long-term lens throughout.** No intraday signals; actions are
   coarse (HOLD / BUY MORE / AVERAGE DOWN / PARTIAL EXIT / FULL
   EXIT); horizons are SHORT / MEDIUM / LONG.
5. **"What's missing" is part of the deliverable.** A portfolio
   analyser that only describes existing holdings is a review, not
   an analyser.
6. **Read-only contract.** The analyser NEVER places an order.
   Imports of `modes/trade/order_engine.py` or `modes/trade/manager.py`
   from `modes/analyze/` are forbidden by the
   [analyze-review.md](../copilot/analyze-review.md) skill.
7. **Reference data is hand-curated and dated.** `_meta` blocks in
   every JSON seed include `as_of` and refresh cadence. Auto-fetch
   was tried and rejected (P-X in
   [ANALYZE_ROADMAP.md](ANALYZE_ROADMAP.md)).

---

## 12. Glossary

- **HHI** — Herfindahl-Hirschman Index. Sum of squared weights × 10000.
  Range 0-10000 (10000 = single-stock portfolio). Concentrated above
  2500. Industry-standard concentration metric.
- **CAGR** — Compound Annual Growth Rate. The constant return that
  would have produced the same end value over the same period.
- **XIRR** — Money-weighted return that accounts for cash flows. The
  analyser uses a two-point CAGR approximation since deposit /
  withdrawal tracking isn't yet wired (deferred follow-up).
- **Beta vs NIFTY** — Sensitivity to NIFTY moves. Beta of 1.0 = moves
  in lockstep; > 1 = more volatile than NIFTY; < 1 = less.
- **TTM** — Trailing Twelve Months. The standard window for
  fundamentals + dividend yield.
- **DPS** — Dividend Per Share. Sum of last 4 quarters of dividends
  per share (TTM).
- **RFR** — Risk-Free Rate. Used in Sharpe denominator. India 10-year
  G-Sec yield (~7%) is the conventional choice.
- **Cash drag** — Idle cash as a % of total account value. Industry
  rule of thumb: > 25% of total = under-invested for a long-term
  portfolio (cash erodes against inflation).
- **Most stale `as_of`** — The oldest `as_of` across every populated
  field on a stock or in the snapshot. The rendered "freshness" badge
  is conservative — it shows the worst-case staleness. All `as_of`
  values are **naive IST** by contract (`now_ist()` returns naive,
  candle-cache reads are stripped via `_to_naive()` in
  `enrich_noai.py`); mixing tz-aware and naive datetimes through
  `min()` would crash, so the `Field` / `StockAnalysis` /
  `PortfolioSnapshot` accessors normalise defensively.
- **AMFI mcap tier** — The Association of Mutual Funds in India
  publishes a half-yearly classification of every listed company:
  top-100 by full market cap = LARGE, 101-250 = MID, 251+ = SMALL.
  The `market_cap_tier` field on each holding and the
  `cap_tier_weights` portfolio metric both come from this list, so
  the analyser's "you're 78% large-cap heavy" reading uses the same
  definitions every Indian mutual-fund factsheet uses.

---

## 13. Where to look in the code

| You want to change | Edit |
|--------------------|------|
| What deterministic data is fetched per stock | [`modes/analyze/enrich_noai.py`](../modes/analyze/enrich_noai.py) |
| The rule-based recommendation logic | [`modes/analyze/recommendation_rules.py`](../modes/analyze/recommendation_rules.py) |
| What Claude is asked for | [`modes/analyze/enrich_ai.py`](../modes/analyze/enrich_ai.py) `_build_prompt()` |
| A portfolio metric formula | [`modes/analyze/metrics.py`](../modes/analyze/metrics.py) |
| A new gap-flag type | [`modes/analyze/gaps.py`](../modes/analyze/gaps.py) |
| The report layout | [`modes/analyze/report.py`](../modes/analyze/report.py) |
| The DB schema | [`modes/analyze/persistence.py`](../modes/analyze/persistence.py) |
| Dashboard summary page | [`modes/dashboard/portfolio_page.py`](../modes/dashboard/portfolio_page.py) |
| Background-job runner | [`modes/dashboard/portfolio_actions.py`](../modes/dashboard/portfolio_actions.py) |
| A reference seed (P/E, dividend, sector benchmark, candidate pool, promoter group, market-cap tier) | The matching JSON under `data/` (each has a `_meta` block with refresh cadence) |
| A risk threshold (RFR, cash drag %, vol lookback) | `Config.RISK_FREE_RATE_PCT`, `Config.CASH_DRAG_FLAG_PCT`, `Config.ANALYZE_VOL_LOOKBACK_DAYS` |
