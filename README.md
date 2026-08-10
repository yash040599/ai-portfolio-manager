# AI Portfolio Manager

Automated intraday trading bot for the **Indian stock market (NSE)**. Uses
technical indicators + candlestick patterns for stock selection, **Zerodha
Kite** for data and execution, and optionally **AI** (Gemini / GPT / Claude)
for selection and reviews via `--ai`.


<!-- ══════════════════════════════════════════════════════════════
README MAINTENANCE CONTRACT (read before editing this file).

Purpose: keep the README short, scannable, and honest about scope.
Copilot/automation should follow this contract so updates are
consistent across edits.

Structure (do NOT reorder, do NOT merge):
  1. What it does       — 5 modes (Phase 1 analyse, Phase 2 trade, Phase 3 dashboard, Phase 4 swing, Phase 5 options). One paragraph + bullets each.
  2. Quick start        — install + first run, max 7 commands.
  3. Documentation map  — table of links to docs/* (single source of truth).
  4. Prerequisites      — bullets only.
  5. Setup              — numbered steps with copy-paste commands.
  6. Run modes          — table of CLI flags.
  7. Project structure  — tree (truncated; full layout in docs/).
  8. Run on a VM        — Azure Ubuntu instructions.
  9. Reports & data     — where files land + sync.
  10. Taxation          — link to TAX_GUIDE; brief script table.
  11. Cost / safety     — bullets only.
  12. Disclaimer        — fixed text.

Style rules:
  • Prefer BULLETS over paragraphs. Max 3 sentences per bullet.
  • Use TABLES for: scripts, CLI flags, env vars, doc links, costs.
  • NEVER duplicate TRADE_STRATEGY.md content — link, don't copy.
  • Each section starts with a 1-line "what is this" sentence.
  • Use third-level headings (###) for sub-sections, not bold paragraphs.
  • All file/directory references use markdown links to actual paths.
  • Code blocks: shell (no language tag if mixed prompts) or `python`.
  • If a feature is shipped, this doc reflects it; if not, it does NOT.

When updating:
  • Find the right section by structure number above; edit IN PLACE.
  • Bump version stamps where present (e.g. "V1 is FROZEN (April 2026)").
  • Cross-check the docs map (section 3) before adding new doc references.
  • Run `python main.py --help` after any CLI flag change to verify the
    flag table matches reality.

When in doubt: terser is better. Long-form belongs in docs/TRADE_STRATEGY.md.
══════════════════════════════════════════════════════════════ -->

---

## 1. What it does

Three surfaces, one CLI. Pick a mode at the CLI.

### Phase 1 — Portfolio analysis (read-only)

- Logs into Zerodha, reads your demat holdings.
- Default flow is **NoAI**: deterministic enrichment from Zerodha
  (positions, quotes, 52-week range), candle cache (long-term technicals
  — SMA-50, SMA-200, RSI-daily, beta vs NIFTY), and hand-curated
  reference files (sector map, dividends, fundamentals seed). Every
  field carries `source` + `as_of` so you know exactly how stale each
  number is.
- **Quant profile** — 30+ metrics per holding computed from the cached
  daily candles by [shared/quant_metrics.py](shared/quant_metrics.py):
  1/3/6/12-month returns, 12-1 momentum, relative strength vs NIFTY at
  four horizons, annualised volatility (30d/90d), one-year max drawdown,
  drawdown from the 52-week high, Sharpe, Sortino, beta, correlation,
  up/down capture, 50/200 trend state with days-since-cross, 52-week
  range position, ATR%, average daily turnover and volume trend.
- **Six-pillar factor scorecard**
  ([modes/analyze/scoring.py](modes/analyze/scoring.py)) turns those
  metrics into two independent verdicts:
  - **Rating** — STRONG BUY / BUY / HOLD / REDUCE / SELL from a 0-100
    composite over trend (22), momentum (24), risk-adjusted return (14),
    quality (14), valuation (14) and position fit (12).
  - **Risk grade** — LOW / MODERATE / HIGH / VERY HIGH from volatility,
    drawdown, beta, downside capture, cap tier, liquidity and
    single-name concentration.
  Pillars with no data are dropped and the rest re-weighted, so a missing
  P/E never scores as a zero; `coverage_pct` reports how much of the
  model actually had data.
- `--ai` adds an AI overlay on top of the same NoAI base — long-term
  thesis, qualitative risks, peer comparison, news context — without
  regenerating any of the deterministic numbers. Provider controlled
  by `AI_PROVIDER` in config.py (default: Gemini).
- Per-stock **action** (HOLD / BUY MORE / AVERAGE DOWN / PARTIAL EXIT /
  FULL EXIT) is a separate layer: it combines the rating with your
  position size and cost basis, because a STRONG BUY you already hold at
  22% of the book is still a trim. Plus a portfolio-wide review with
  sector gaps, AMFI market-cap tier breakdown, concentration risks,
  risk-bucket and weak-rating concentration checks, and "what's missing"
  suggestions.
- Long-term horizon throughout. No orders placed.
- Surfaced live on the **Dashboard** (`/portfolio` page) — see Phase 3.

```
python main.py --mode analyze         # NoAI (default)
python main.py --mode analyze --ai    # with AI (Gemini by default)
python main.py --mode portfolio                 # same as --mode analyze
python main.py --mode portfolio --type stocks   # explicit stock book
python main.py --mode portfolio --type mf       # mutual-fund book
```

Full plan: [docs/ANALYZE_ROADMAP.md](docs/ANALYZE_ROADMAP.md) (P1-P7
foundation in flight; D24-D29 dashboard surface in flight).

### Phase 1b — Mutual funds (Coin + other brokers)

Mutual funds are tracked separately from equities because an
open-ended scheme has no intraday price — it has an end-of-day NAV
published by the AMC. Every rupee on this surface is marked to that
NAV and stamped with the date it belongs to, so it never reads as a
live number.

- **Coin holdings** come from `kite.mf_holdings()`, along with the SIP
  book (active *and* paused) and recent orders. P&L is derived locally
  because Kite returns `pnl` and `xirr` as zero.
- **Funds held elsewhere** are hand-entered into `data/mf.db`. You pick
  the scheme from the Coin catalogue (~7.6k schemes), so the NAV
  resolves automatically even though the units sit at another broker.
- **The same scheme at two brokers** merges into one position with a
  unit-weighted average NAV — the view no broker statement can give you.
- Allocation by asset class, AMC, plan (direct vs regular) and broker,
  plus concentration HHI and NAV history charts (AMFI scheme map +
  MFapi daily series).
- **Structural review** (`--insights`, and a Review section on `/mf`):
  exposure map showing how many *distinct bets* the book holds, funds
  whose NAVs correlate above 0.90 (the honest stand-in for holdings
  overlap), the accumulation-vs-dormant split, per-fund CAGR/volatility/
  drawdown from NAV history, and the LTCG-exemption cost of
  consolidating duplicates. No buy/sell calls — for a long-held book,
  ranking funds on recent return mostly measures when you bought.
- Externally-held funds get the **same analysis** as Coin funds, and can
  record their own monthly SIP so they are not misread as dormant.
- **Every Coin fetch is stored locally**, so the page and the home
  net-worth open on the last known book without calling the broker. A
  sync that fails (expired token) falls back to that stored book and
  shows the error, instead of blanking to zero.
- Adds to net worth and unrealised P&L on the home page. Coin units are
  not in the demat equity list, so nothing is double-counted.

```
python main.py --mode portfolio --type mf                    # full book
python main.py --mode portfolio --type mf --insights         # structural review
python main.py --mode portfolio --type mf --insights --refresh-history
python main.py --mode portfolio --type mf --offline          # stored book, no broker call
python main.py --mode portfolio --type mf --sips             # active + paused SIPs
python main.py --mode portfolio --type mf --search "parag parikh"
python main.py --mode portfolio --type mf --add --scheme INF879O01027 \
               --units 120.5 --nav 84.9 --broker "Groww"
python main.py --mode portfolio --type mf --list-external
python main.py --mode portfolio --type mf --remove 3
```

### Phase 2 — Intraday trading (V2, default)

Fully automated NSE intraday loop. **NoAI is the default** (zero AI API
calls, pure indicators); add `--ai` to put the active AI provider in the
selection loop. Supports multiple strategy profiles via `TRADE_STRATEGY_PROFILE`
(default: `NOAI_GAP_AND_GO_1.1.1` — gap-and-go with volume qualification,
gap-hold confirmation, score-contradiction filter on NIFTY100. OOS PF 1.62, Sharpe 1.80).

Loop, in plain English:

1. **Pre-market scan** — fetch candles for every stock in `SCAN_UNIVERSE`,
   apply price filter, run candlestick + indicator detectors, score, pick
   the best candidates. Gap-and-Go profile scans for stocks gapping >1%
   with >2× average volume, confirms gap is holding, and rejects entries
   where technical score contradicts gap direction.
2. **Execute** — LIMIT entry at LTP + 1 tick (MARKET fallback), ATR-based
   SL/target with min-distance floor (gap-and-go uses gap-candle SL),
   **44-check pre-trade pipeline** (see
   [TRADE_STRATEGY.md](docs/TRADE_STRATEGY.md#risk-management--entry-pre-checks)
   for the full table), risk-budget position sizing.
3. **Monitor** — adaptive polling, auto-trail SL, partial profits,
   two-tier stagnant exit, three score-driven exits (signal-reversal,
   signal-decay, auto-protect SL-tighten) on the free 15-min re-scan.
4. **Risk** — circuit breaker (3% hard) + soft-stop (1.5%), peak-drawdown
   stop, whipsaw guard, sector caps, regime-shift protection, India VIX,
   crash recovery, manual-trade adoption with grace window, Thursday +
   holiday-shifted expiry adjustments, dynamic budget regimes.
5. **EOD** — square off, generate P&L + tax report, auto-verify trades
   against Zerodha, run rejection audit (verdict on every skipped entry).

```
python main.py --mode trade           # NoAI (default)
python main.py --mode trade --ai      # with AI
```

### Phase 3 — Dashboard (tool-wide operator surface)

The dashboard is the project's **single tool-wide operator surface**.
It hosts pages for every mode the project exposes — Portfolio analysis,
Swing (India + US), Intraday trading P&L, Dry-run, Tax filing, Theory &
strategy reference — and is independent of every mode's order path. It
never places broker orders. It may poll Zerodha live quotes for
displayed prices/P&L and may write local workflow ledgers such as swing
confirmations. Default launch starts a local web server and opens the
home page in your browser; the webpage itself is the config surface for
date range / source toggles / per-stock drill-down / "Analyse now"
buttons, so the CLI is just an entry point.

All pages share one design system
([modes/dashboard/theme.py](modes/dashboard/theme.py)) — one token set
for colour, radius and shadow, plus a **light / dark theme** toggle in
the nav that persists in `localStorage` and follows the OS preference by
default.

Pages:

- **`/`** (default landing) — command centre. Net worth, unrealised
  P&L, India (Zerodha), mutual-fund and US books, a book-mix doughnut,
  Indian sector weights, the top rows of all four books, realised P&L
  across swing / US / intraday, and **inline Zerodha login** (TOTP
  quick-login plus paste-back) so a re-auth never needs a detour. First
  paint is snapshot-only — SQLite plus cached quotes, no broker calls;
  the browser then fetches `/api/home/summary?live=1` once, with opt-in
  auto-refresh.
  Book boundaries are enforced: net worth = Zerodha holdings + mutual
  funds + US book. The India swing open book is a *tracking* ledger over
  shares that already sit inside the Zerodha holdings, so it is shown
  separately and never summed into net worth.
- **`/portfolio`** — Phase 1 analyser surface.
  Reads the latest `--mode analyze` run from `data/portfolio_analyses.db`,
  shows holdings (with **Rating** and **Risk** columns from the factor
  scorecard) + portfolio metrics + "what's missing" panel + a per-stock
  drill-down carrying the six-pillar breakdown and the full quant
  profile, plus on-demand "Analyse now (NoAI / AI)" buttons. Header
  carries the most-stale `as_of` across the run so you can see how fresh
  the analysis is. Displayed current price/P&L polls Zerodha live quotes
  when a valid token exists.
- **`/mf`** — mutual-fund book. Coin holdings, funds held at other
  brokers (add / edit / remove inline, with a scheme picker driven by
  the Coin catalogue), and a combined table that merges the same
  scheme across brokers into one unit-weighted position. Asset-class
  and AMC doughnuts, direct-vs-regular and per-broker splits, SIP table
  showing active *and* paused instalments with the monthly commitment,
  recent Coin orders, and a click-through NAV history chart per fund.
  Everything is marked to the last published NAV, shown as a `NAV as of`
  chip; funds with no resolvable NAV are flagged and held at cost rather
  than valued at zero. First paint replays the last stored Coin fetch
  (`Coin synced <ts>` chip) so the page never opens empty — the Refresh
  button is for pulling a newer NAV, not for making the page work.
- **`/swing`** — India delivery swing dashboard. Entry recommendations on
  top with **Conviction** and **Risk** grade columns, watchlist and open
  book below with live prices (Zerodha 5s), per-stock detail pages, and
  Add+ / Mark-Exit controls for manual broker actions.
- **`/us`** — **US long-term portfolio.** This is not a trading book:
  US positions are long-term holdings meant to compound (RSU lots plus
  deliberate long-horizon buys), so ideas are scored by
  [`modes/us/longterm.py`](modes/us/longterm.py) — a six-factor
  buy-and-hold model (quality & profitability 24, valuation vs sector 18,
  growth durability 17, 12-1 momentum 16, financial strength 13,
  risk & drawdown 12) rather than by chart setups. Ratings run
  HIGH CONVICTION / ACCUMULATE / NEUTRAL / WEAK / AVOID; there are no
  ATR stops, R-multiples or price targets, because those assume a
  planned exit in weeks. Fundamentals come from yfinance and are cached
  for a fortnight in `data/us_fundamentals.json`. Holdings show weight
  and a current rating so a business that has decayed surfaces there
  instead of in a stop-loss. Live prices via yfinance (15s), per-stock
  detail page with the full pillar breakdown, USD/INR toggle.
  It shares the swing SQLite schema (partitioned by `exchange`) purely
  as a storage detail — that says nothing about the holding period.
- **`/trading`** — intraday-trading profitability view (the original
  Phase 3 SPA from D1.1). Two charts (Chart.js via CDN, zero new
  Python deps): cumulative net P&L (line, daily) + per-bucket P&L
  (bar, daily/weekly/monthly switchable). Cumulative chart overlays a
  thin dashed vertical line at every trading day where the bot's git
  SHA changed (D13); hover shows the commit subject. Capital-ladder
  traffic-light verdict (GREEN / AMBER / RED / GREY). Source toggle:
  all trades (default) or verified only (T+1 frozen, tax-grade).
  `% of budget` is computed against the per-day budget actually
  deployed (read from each day's `reports/trading/.../trading_data_DD.json`).
  Quick-range dropdown (This FY / Previous FY / Last 7d / Last 30d /
  All time / from-to date pickers). Pending-verification banner lists
  trading days awaiting Zerodha sheet import.
- **`/tax`** — FY-summary + projection. Enter your other FY income;
  computes which slab you fall into under Budget-2025 new-regime
  rules, applies Section 87A rebate + 4% cess, shows the headline
  "tax attributable to intraday this FY". Click-to-copy ITR-3
  Schedule BP fields, documents checklist, cross-link to Tax Guide.
  Backed by versioned slabs in [`modes/dashboard/tax/slabs.py`](modes/dashboard/tax/slabs.py)
  — adding a future FY is a one-line config.
- **`/theory/<slug>`** — four reference docs rendered live from
  `docs/` with KaTeX math + dropdown nav: Statistical Analysis (with a
  theoretical-vs-live snapshot card on top), Trade Strategy reference,
  Strategy Evolution log, India Tax Guide.
- **`/login`** — standalone Zerodha auth page (the same two flows the
  home page embeds). Both accept a `next` field so you land back where
  you started; the value is whitelisted server-side.

Lives in its own [modes/dashboard/](modes/dashboard/) folder, isolated
from every mode's runtime. Touches no strategy/order code; broker access
from the dashboard is read-only quotes.

```
python main.py --mode dashboard                    # interactive (server + browser)
python main.py --mode dashboard --no-open          # static HTML snapshot
python main.py --mode dashboard --text             # legacy plain-text
python main.py --mode dashboard --port 8765        # fixed port
```

Full plan: [modes/dashboard/docs/DASHBOARD_ROADMAP.md](modes/dashboard/docs/DASHBOARD_ROADMAP.md)
(D1 + D1.1 + D13 + theory/tax pages + D24-D29 + D30-D31 done;
D2-D12, D14, D15, D18-D23 pending).

### Phase 4 — Swing trading (delivery, report-only by design)

A separate engine for **multi-day delivery (CNC) trades** held 2 trading
days to 8 weeks. **Report-only by permanent design** — the bot never
places broker orders; the operator trades manually on Zerodha Kite and
clicks **Add+** on the dashboard with the actual fill numbers. See
[docs/SWING_GUIDE.md](docs/SWING_GUIDE.md) for the operator-facing
walkthrough; [docs/SWING_STRATEGY.md](docs/SWING_STRATEGY.md) for the
design spec; [docs/SWING_ROADMAP.md](docs/SWING_ROADMAP.md) for the
change log (37 items shipped, 2 pending, 5 awaiting-data).

What ships today:

- **5 setup detectors** — BREAKOUT, PULLBACK_UPTREND, TREND_CONTINUATION,
  SUPPORT_REVERSAL, plus the **52W_DIP** dip-buy strategy (buy when a
  stock falls X% below its rolling 52-week high; sell on Y% gain).
  Defaults X=10, Y=20, Rs.20k ticket are retuned from the 2026-05-16
  finite-capital V2 backtest in the standalone
  [market-research](https://github.com/yash040599/market-research)
  repo: Rs.1L start, Rs.20k lots, recycled proceeds, and +1.29% CAGR
  alpha over the equal-weight NIFTY 50 benchmark. The live scanner uses
  the rolling 52-week high as a stricter reference than that ATH study.
- **Cross-setup scoring modifiers** — 52w-high proximity (bonus for
  continuation setups, penalty for mean-reversion), NR7 volume
  contraction (BREAKOUT bonus), sector-rotation bonus (top-3 sectors
  by RS get +0.5).
- **Conviction + risk grades** — the raw setup score is not comparable
  across setup families and says nothing about downside, so every
  candidate also carries two 0-100 grades from
  [modes/swing/conviction.py](modes/swing/conviction.py):
  **Conviction A/B/C/D** (setup strength, trend quality, relative
  strength, volume participation, ADX trend strength, volatility fit)
  and **Risk LOW..VERY HIGH** (ATR%, stop distance, volatility,
  drawdown, beta, liquidity, distance below the 52-week high). The same
  engine grades Indian and US candidates, so the two are directly
  comparable.
- **Hard gates** — earnings-blackout filter (T+0..2 calendar days),
  weekly-trend-up requirement on SUPPORT_REVERSAL ("no falling-knife
  entries"), portfolio-level risk + sector caps.
- **Three operator surfaces** — `/swing` dashboard with live 5-second
  Zerodha price polling, per-stock detail page (`/swing/<symbol>`)
  with full health-check + AI overlay panel + per-stock "Analyse with
  AI" button (~Rs.3 per call), and a single-stock search box +
  side-by-side compare-up-to-4 card with auto-populate by sector.
- **Optional AI overlay** — capped at 15 candidates per scan
  (~Rs.45 max on Pro plan); responses sticky for 7 days via
  carry-forward so a one-stock Rs.3 spend doesn't get lost when you
  re-scan tomorrow. AI prompt asks for VERDICT / THESIS / RECENT
  NEWS / FUNDAMENTAL CONTEXT / PEER COMPARISON / RISKS /
  CORPORATE-ACTION SANITY CHECK / WHY IT MIGHT FAIL.
- **Full CLI parity** — every dashboard action (scan, list pending,
  list open positions, confirm, skip, compare, sector-compare,
  backtest) has a `--mode swing --xxx` CLI sub-command with the
  same persistence guarantees. Documented in
  [docs/SWING_GUIDE.md §3](docs/SWING_GUIDE.md).
- **Permanent report-only stance** — items that previously tracked
  execution automation (broker order wrappers, GTT/OCO,
  reconciliation, ledger isolation) are in the SWING_ROADMAP
  Removed section. The bot can only ever recommend; the operator
  always fills the trade.

```
python main.py --mode swing                       # daily NoAI scan
python main.py --mode swing --ai                  # + AI overlay (capped)
python main.py --mode swing --compare A,B,C,D     # side-by-side
python main.py --mode swing --compare-sector BANKING
python main.py --mode swing --actions             # list pending
python main.py --mode swing --positions           # list open book
```

Full operator walkthrough: [docs/SWING_GUIDE.md](docs/SWING_GUIDE.md).

### Phase 5 — Options trading (NIFTY, research stage)

A separate engine for **NIFTY index option buying** on weekly expiries.
Currently in **research stage** — code is complete but the v1.0 strategy
(regime-gated directional buying) does not pass the 1.15 PF gate in
backtesting (PF 0.42). No live or dry-run trading until the strategy
improves.

See [docs/OPTIONS_ROADMAP.md](docs/OPTIONS_ROADMAP.md) for the phased
rollout plan; [docs/OPTIONS_GUIDE.md](docs/OPTIONS_GUIDE.md) for the
plain-English options primer.

What ships today:

- **Full mode scaffold** — `modes/options/` with manager, scanner, order
  engine, performance tracker, report writer. Same lifecycle pattern as
  equity (login → scan → enter → monitor → square-off → report).
- **NFO support in ZerodhaClient** — `load_nfo_instruments()`,
  `place_option_order()`, NFO token cache.
- **Regime-gated scanner** — classifies day as VOLATILE/TREND/RANGE using
  prior 5-day range + gap signal. Only trades on VOLATILE/TREND days.
- **Safety hard blocks** — naked sell forbidden in code (always), buy-only
  in Phase O-4, circuit breaker at 3% daily loss, DRY_RUN default.
- **Backtest engine** — `scripts/trade/backtest_options.py` with
  Brenner-Subrahmanyam premium model, Parkinson volatility estimator,
  full NSE option charge model, walk-forward validation.
- **Dashboard integration** — `/dryrun` page has Intraday/Options mode
  switcher showing backtest results.
- **Separate DB** — `data/options.db` for option trades + candidate audit.

```
python main.py --mode options                     # dry-run (default)
python main.py --mode options --live              # live (future)
python scripts/trade/backtest_options.py          # run backtest
python scripts/trade/backtest_options.py --sweep  # parameter sweep
```

### Historical candle cache

- `data/candle_cache.db` (SQLite) keeps prior days' candles to avoid
  re-fetching from Zerodha.
- Auto-cleanup, weekend/holiday-aware lookback, corporate-action
  detection, rate limiting.
- Committed to Git (pure market data).
- Pre-warm by running `--test` the evening before.

---

## 2. Quick start

```bash
git clone https://github.com/<you>/ai-portfolio-manager.git
cd ai-portfolio-manager
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                  # then fill in keys
python main.py --mode login                           # one-time Zerodha login
python main.py --mode trade                           # start the bot
```

---

## 3. Documentation map

Single-source-of-truth lives in `docs/`. The README never duplicates
their content.

| Doc | What it covers |
|-----|----------------|
| [docs/TRADE_STRATEGY.md](docs/TRADE_STRATEGY.md) | Complete Trade strategy — NoAI + AI modes, 44-check pre-trade pipeline, all indicators/patterns, scoring, risk layers, glossary |
| [docs/TRADE_STRATEGY_ROLLOUT.md](docs/TRADE_STRATEGY_ROLLOUT.md) | Gate optimization approach — backtest-driven gate enable/disable decisions. |
| [docs/TRADE_ROADMAP.md](docs/TRADE_ROADMAP.md) | Operating plan, current posture, next steps, promotion gate, deferred work |
| [docs/TRADE_EVOLUTION.md](docs/TRADE_EVOLUTION.md) | Chronological one-line history of every shipped strategy item (auto-regenerated from the Roadmap) |
| [docs/TRADE_STATISTICS.md](docs/TRADE_STATISTICS.md) | Current backtest results, active config, break-even math, promotion metrics. Rendered at the dashboard's `/theory/statistics` page. |
| [docs/ANALYZE_STRATEGY.md](docs/ANALYZE_STRATEGY.md) | Complete Portfolio-Analyser reference — what every field on a stock card means, how rule-based actions are chosen, what the AI overlay adds, the report layout, the persistence schema |
| [docs/ANALYZE_ROADMAP.md](docs/ANALYZE_ROADMAP.md) | **P1-P9 shipped** — Portfolio-Analyser foundation: typed `StockAnalysis` with per-field `source`/`as_of`, NoAI + AI enrichment split, persistence DB, industry-standard metrics (HHI, Sharpe, vol, max-DD, CAGR, cash drag, AMFI mcap-tier breakdown), "what's missing" engine |
| [docs/SWING_STRATEGY.md](docs/SWING_STRATEGY.md) | Swing trading strategy reference — 4 setup types, risk model, position review, exit stack, AI overlay semantics, broker-entry instructions, dashboard surface spec |
| [docs/SWING_GUIDE.md](docs/SWING_GUIDE.md) | **Operator-facing walkthrough for Phase 4 swing** — dashboard surface, full CLI reference, 5 setup detectors, 52W dip-buy strategy + 10y backtest evidence, AI overlay (cost / sticky cache / prompt), Compare-up-to-4, Add+ flow, HTTP API, persistence, tuning knobs, FAQ |
| [docs/SWING_ROADMAP.md](docs/SWING_ROADMAP.md) | Swing change log — Pending / Awaiting-Data / Removed / Completed (S1-S48 to date). Read this before touching any swing knob to confirm you're not undoing a calibrated decision. |
| [modes/dashboard/docs/DASHBOARD_ROADMAP.md](modes/dashboard/docs/DASHBOARD_ROADMAP.md) | **Tool-wide operator surface** — D1/D1.1/D13/D16/D17 + **D24-D29 (Portfolio-Analyser pages) shipped 2026-05-12** + **D30-D31 (live quotes + /swing page) shipped 2026-05-13** |
| [docs/IDEATIONS.md](docs/IDEATIONS.md) | Future money-engine ideation: A1 V3 AI intraday (superseded), ~~A2 swing~~ (✅ done), A3 ETF rotation (planning), A4 options (✅ shipped). No-F&O constraint lifted 2026-06-09. |
| [docs/OPTIONS_GUIDE.md](docs/OPTIONS_GUIDE.md) | **Plain-English options primer** — what options are, how P&L works, the Greeks (delta/theta/vega/gamma), buying vs selling mechanics |
| [docs/OPTIONS_STRATEGY.md](docs/OPTIONS_STRATEGY.md) | **Options strategy reference** — regime-gated directional buying v1.0, premium model (Brenner-Subrahmanyam + Parkinson), NSE charges, backtest results, improvement ideas, config reference, module architecture |
| [docs/OPTIONS_ROADMAP.md](docs/OPTIONS_ROADMAP.md) | **Options mode phased rollout** — O-0 to O-6, capital plan, promotion gates, backtest results (v1.0 PF 0.42 FAIL), strategy plans |
| [docs/TRADE_TAX_GUIDE.md](docs/TRADE_TAX_GUIDE.md) | India intraday tax guide (FY 2026-27 ready) |

---

## 4. Prerequisites

- **Python 3.10+** (uses modern type syntax).
- **Windows / Linux / macOS** — works on headless Linux VMs (manual login
  mode for SSH-only setups).
- **Zerodha account** with [Kite Connect](https://developers.kite.trade)
  subscription (Rs.500/month).
- **AI API key** — only needed for `--ai` mode. Default provider is
  **Gemini** (free tier: 500 req/day). Alternatively GPT (OpenAI) or
  Claude (Anthropic). Set `AI_PROVIDER` in config.py.

---

## 5. Setup

### 5.1 Install

```bash
cd ai-portfolio-manager
pip install -r requirements.txt
```

| Package | Purpose |
|---------|---------|
| `google-genai` | Gemini API client (default AI provider) |
| `openai` | OpenAI GPT API client (optional provider) |
| `anthropic` | Claude API client (optional provider) |
| `kiteconnect` | Zerodha Kite trading API (≥ 5.1.0 required for `market_protection`) |
| `python-dotenv` | Loads keys from `.env` |
| `openpyxl` | Reads Zerodha Tax P&L xlsx files |
| `requests` | Programmatic Kite login (AUTO / ASSISTED modes — §5.4) |
| `pyotp` | Optional, only if you opt-in to AUTO login (§5.4) |

### 5.2 API keys

Create `.env` in the project root:

```env
# Required
ZERODHA_API_KEY=...
ZERODHA_API_SECRET=...

# AI provider keys — only ONE is needed, matching AI_PROVIDER in config.py.
# Default provider is Gemini (free tier: 500 req/day, no credit card).
GEMINI_API_KEY=...              # https://aistudio.google.com/apikey
# OPENAI_API_KEY=...            # https://platform.openai.com/api-keys (if using GPT)
# CLAUDE_API_KEY=...            # https://console.anthropic.com (if using Claude)

# Optional — enable streamlined login (§5.4)
KITE_USER_ID=AB1234              # your Zerodha client id
KITE_PASSWORD=your_kite_password # web login password (NOT the API secret)

# Optional — only if you want fully unattended login (security trade-off!)
KITE_TOTP_SECRET=JBSWY3DPEHPK...  # base32 TOTP seed (§5.4)
```

#### Zerodha Kite Connect

1. [zerodha.com/open-account](https://zerodha.com/open-account) → fund your account.
2. [developers.kite.trade](https://developers.kite.trade) → subscribe Kite Connect.
3. Create app → name = anything, redirect URL = `http://localhost:8080`,
   type = `default`. Copy **API Key** and **API Secret**.
4. **Whitelist your public IP** (mandatory from 1 April 2026 per SEBI).
   On the app page, add the IP of every machine that runs the bot. For a
   VM use its **static** public IP. Multiple IPs are comma-separated.
5. Tokens expire daily at midnight; the bot re-prompts. On SSH-only VMs
   pick **manual mode** (paste the redirect URL from your phone).

#### AI Provider API Keys

Set `AI_PROVIDER` in config.py (default: `gemini`). Only the key for
your chosen provider is required.

**Gemini (recommended — has free tier)**

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. Sign in with your Google account → Create API Key.
3. Add `GEMINI_API_KEY=...` to `.env`.
4. Free tier: **500 requests/day**, 1M tokens/min. No credit card needed.
   Well within typical bot usage (~50-100 calls/day).

**GPT (OpenAI) — optional alternative**

1. Go to [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
2. Create key, add credits ($5-10 is plenty to start; ~Rs.1-2/run).
3. Add `OPENAI_API_KEY=...` to `.env`, set `AI_PROVIDER = "gpt"` in config.py.

**Claude (Anthropic) — optional alternative**

1. Sign up at [console.anthropic.com](https://console.anthropic.com).
2. Settings → Billing → add credits (Rs.500–1000; ~Rs.5-8/run on Pro).
3. Settings → API Keys → Create Key.
4. Add `CLAUDE_API_KEY=...` to `.env`, set `AI_PROVIDER = "claude"` in config.py.

### 5.3 Configure

Open [config.py](config.py). Common settings:

| Setting | Default | Controls |
|---------|---------|----------|
| `MAX_BUDGET_INR` | 50,000 | Max capital deployed per day |
| `SCAN_UNIVERSE` | NIFTY100 | Stock pool (overridable per-run with `--nifty 50/100/150/200`) |
| `MAX_POSITIONS` | 3 | Simultaneous trades |
| `DRY_RUN` | False | Simulate without real orders (or use `--dryrun`) |
| `AI_PROVIDER` | gemini | AI backend: `gemini`, `gpt`, `claude` |
| `AI_PLAN` | basic | Depth: `basic`, `detailed`, `full` (scales prompt depth + tokens) |
| `RR_TARGET_RATIO` | 1.5 | Base R:R from ATR |
| `RR_HARD_FLOOR` | 1.3 | Always-on R:R floor — uniform across the trading day (collapsed from the deprecated time-tiered floors by #243) |

---

### 5.4 Zerodha login modes

Kite access tokens expire daily at midnight, so the bot has to re-login
once a day. Four flows are supported — **the bot picks the most
automated one your `.env` allows, then falls back automatically**:

| Mode | Trigger | Human action per day | Security |
|---|---|---|---|
| **AUTO** | `KITE_USER_ID` + `KITE_PASSWORD` + `KITE_TOTP_SECRET` set | none | ⚠️ password **and** TOTP seed both on disk — effectively single-factor (§5.4.3) |
| **ASSISTED** | `KITE_USER_ID` + `KITE_PASSWORD` set, no seed | type the 6-digit code from your authenticator app or Kite mobile app | password on disk; TOTP stays on phone (§5.4.4) |
| **Browser (`b`)** | none of the above, you press `b` | log in via the browser tab the bot opens; redirect is auto-caught on `localhost:8080` | nothing on disk; native browser flow |
| **Manual (`m`)** | press `m` (default for SSH-only / VM setups) | open the printed URL on a phone/laptop, log in, paste the **full** redirect URL back into the terminal | nothing on disk; works headless |

The order of attempts is: cached token → AUTO/ASSISTED (if env permits) →
on failure or missing env, the legacy `b/m/q` prompt.

#### 5.4.1 ASSISTED setup (recommended for most users)

Add two lines to `.env`:

```env
KITE_USER_ID=AB1234
KITE_PASSWORD=your_kite_web_password
```

Next run will detect them, drive the login form itself, and prompt:

```
  Open your authenticator app (Apple Passwords / Authy / Google Auth)
  or read the 6-digit code from your Kite mobile app.

  Enter 6-digit code: ______
```

Works whether you have External TOTP enabled (code from Authy / Apple
Passwords / Google Auth) or not (PIN from the Kite mobile app login
screen). Total user input: 6 digits.

#### 5.4.2 AUTO setup (zero-touch — read the security note first)

AUTO needs the base32 **TOTP seed** that Zerodha shows once at
enrollment. If you used the QR-scan path (Apple Passwords, Authy etc.
scanning the QR directly), the seed is buried inside the QR image and
you have to re-enroll to see it as text:

1. Kite web → **Profile → Password & Security → Disable External TOTP**
   (asks for password + current TOTP).
2. **Enable External TOTP** again.
3. On the QR screen click **“Can’t scan? Copy the key”** — a long
   base32 string (letters A–Z + digits 2–7, no spaces) appears.
4. **Copy it into `.env`** as `KITE_TOTP_SECRET=...` immediately. You
   only see it once.
5. Then re-add the same secret to your phone authenticator (Apple
   Passwords / Authy support a “manual entry” option that takes the
   same string). This keeps your phone working as a backup.
6. Verify everything wired up:

   ```
   python main.py --mode login
   ```

   With all three env vars present the bot logs `Attempting Kite AUTO
   login (env-driven)…` and finishes without prompting.

#### 5.4.3 Security trade-offs of AUTO mode

> 🚨 **AUTO mode reduces 2FA to single-factor.** Anyone who can read
> `.env` (malware, stolen laptop, accidental git commit, OneDrive
> sync, screen share) can place trades on your account. The TOTP seed
> is **non-rotating** — a leak is silent until trades start happening.

Only opt-in if you also do at minimum:

- Confirm `.env` is gitignored: `git check-ignore -v .env` should print `.gitignore`.
- **BitLocker** on the laptop drive (Win Pro built-in, free).
- **Exclude the project folder from OneDrive / iCloud** (Settings →
  Choose folders).
- Restrict `.env` ACL to your Windows user only (Properties → Security
  → disable inheritance, remove all but your account).
- Keep your Zerodha bank-withdrawal whitelist set to **only your
  primary account** so a hijacker can’t move funds out cleanly.

If any of those feel like too much hassle, **stay on ASSISTED** —
you’ve given up almost nothing and kept real 2FA.

#### 5.4.4 Security trade-offs of ASSISTED mode

Mild: your Kite **password** sits in `.env` next to your existing
`ZERODHA_API_SECRET`. The TOTP factor still requires your phone, so a
`.env` leak alone cannot log in. Same minimum hygiene applies
(gitignore + BitLocker + no cloud sync).

For maximum hygiene store the password in Windows Credential Manager
via the `keyring` package instead of `.env` — a future enhancement.

### 5.5 New machine restore

Use this when replacing the laptop or bringing up a fresh Linux VM. The
goal is to restore the code, private operational data, `.env`, Copilot
runbooks, and replay datasets without manually copying folders.

| Repo / data | Restored by | Default local path |
|---|---|---|
| Main code | `git clone` | `ai-portfolio-manager/` |
| Operational data, reports, logs, runbooks | [scripts/shared/backup_data.py](scripts/shared/backup_data.py) | `../ai-portfolio-manager-data/` plus local `data/`, `reports/`, `logs/`, `copilot/` |
| `.env` | [scripts/shared/backup_data.py](scripts/shared/backup_data.py) with `--include-env` | project root |
| Replay/backtest datasets | [scripts/shared/sync_backtest_data.py](scripts/shared/sync_backtest_data.py) | `../ai-portfolio-backtest-data/` |

> The replay dataset repo uses **Git LFS** for its `*.sqlite` candle
> stores. Install `git-lfs` on the new machine *before* the first sync
> (`git lfs install`; on Windows `winget install GitHub.GitLFS`, on the
> Linux VM `sudo apt-get install git-lfs`). `sync_backtest_data.py` will
> refuse to run without it. See [Backtest/replay data sync](#backtestreplay-data-sync-private-repo).

On the old laptop, do the final private-data push first:

```powershell
.\.venv\Scripts\python.exe scripts\shared\backup_data.py --include-env --all-local --dry-run
.\.venv\Scripts\python.exe scripts\shared\backup_data.py --include-env --all-local --yes
.\.venv\Scripts\python.exe scripts\shared\sync_backtest_data.py --pull --status
.\.venv\Scripts\python.exe scripts\shared\sync_backtest_data.py --push --commit --message "sync replay data before machine move"
```

On the new Windows machine:

```powershell
git clone <repo-url> ai-portfolio-manager
cd ai-portfolio-manager
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit the temporary `.env` so it contains at least one operational data
repo URL:

```env
BACKUP_REPO_URL_HTTPS=https://github.com/<user>/<private-data-repo>.git
# or
BACKUP_REPO_URL_SSH=git@github.com:<user>/<private-data-repo>.git
```

Then restore the private data and replay dataset:

```powershell
.\.venv\Scripts\python.exe scripts\shared\backup_data.py --include-env --all-remote --yes
.\.venv\Scripts\python.exe scripts\shared\sync_backtest_data.py --pull --status
```

On the Linux VM, use the same flow with `python3 -m venv venv`,
`source venv/bin/activate`, and add `--ssh` to both sync commands.

After restore, run read-only smoke checks:

```powershell
.\.venv\Scripts\python.exe -c "from config import Config; print(Config.snapshot_hash())"
.\.venv\Scripts\python.exe scripts\trade\export_backtest_data.py --dry-run
.\.venv\Scripts\python.exe scripts\trade\backtest.py --from 2026-04-07 --to 2026-04-24 --min-score 999
.\.venv\Scripts\python.exe scripts\trade\promotion_check.py --window 20
```

`promotion_check.py` runs the codified PASS/FAIL gate for capital scaling;
it reads the last N trading sessions and tests PF, expectancy, WR, and drawdown.
Full migration notes are mirrored in [copilot/machine-migration.md](copilot/machine-migration.md).

---

## 6. Run modes

| Command | What it does |
|---------|--------------|
| `python main.py --mode analyze` | Phase 1 — long-term portfolio analyser, NoAI default (no AI cost) |
| `python main.py --mode analyze --ai` | Phase 1 + AI qualitative overlay (thesis/risks/news) |
| `python main.py --mode trade` | Phase 2 NoAI (default) |
| `python main.py --mode trade --ai` | Phase 2 with AI |
| `python main.py --mode trade --noai` | Same as default; explicit |
| `python main.py --mode trade --dryrun` | Full strategy, no real orders |
| `python main.py --mode trade --test` | See pipeline only (no AI, no trades, no cost) |
| `python main.py --mode trade --max 30000` | Cap today's capital at Rs.30,000 |
| `python main.py --mode trade --nifty 150` | Override scan universe |
| `python main.py --mode swing` | Phase 4 — swing scan (NoAI). Report-only by permanent design — see SWING_ROADMAP. Best run after market close (3:30 PM IST). |
| `python main.py --mode swing --ai` | Same scan + AI qualitative overlay (capped). See [Swing CLI reference](#swing-cli-reference) below for the full sub-command list. |
| `python main.py --mode options` | Phase 5 — NIFTY option buying (dry-run default). Research stage — strategy not yet profitable. |
| `python main.py --mode options --live` | Phase 5 — enable live orders (BLOCKED until strategy passes 1.15 gate) |
| `python main.py --mode login` | Test Zerodha login only |
| `python main.py --mode dashboard` | Launch interactive profitability dashboard (local server + browser). `--no-open` writes a static HTML snapshot; `--text` prints plain text; `--port N` pins a port. See [modes/dashboard/docs/DASHBOARD_ROADMAP.md](modes/dashboard/docs/DASHBOARD_ROADMAP.md) |

**Ctrl+C** triggers graceful shutdown — squares off all positions first.
Phase 2 can be started any time (handles weekends / NSE holidays / late
starts / token expiry automatically).

### Swing CLI reference

Swing mode is **permanently report-only** — the CLI commands cover every
state-changing dashboard action so you can run the entire workflow from
the terminal too. (Same service layer powers both surfaces.)

> **For the full operator walkthrough** — dashboard surface, the
> 5 setup detectors, 52W dip-buy strategy + 10y backtest evidence,
> AI-overlay cost / sticky cache / prompt structure, Compare-up-to-4,
> Add+ flow, HTTP API, persistence, tuning knobs, and FAQ —
> see [docs/SWING_GUIDE.md](docs/SWING_GUIDE.md).

| Command | What it does |
|---------|--------------|
| `python main.py --mode swing` | Run today's NoAI swing scan. Prints accepted candidates + open swing book. Refuses to scan before market close (uses yesterday's completed daily candle when run pre-close). |
| `python main.py --mode swing --ai` | Same scan + AI overlay capped at the top `SWING_AI_MAX_CANDIDATES` (default 15) accepted candidates by `priority_rank`. Pre-AI snapshot is written first so a Ctrl+C still leaves a usable report. |
| `python main.py --mode swing --nifty 100` | Override the scan universe (`50` / `100` / `150` / `200`). |
| `python main.py --mode swing --actions` | List all PENDING swing actions (entry recommendations not yet confirmed/skipped). Prints action_id, symbol, qty, suggested entry/stop. |
| `python main.py --mode swing --positions` | List all OPEN swing positions (entries you've confirmed via Done). Prints position_id, symbol, managed_qty, entry, stop, entry date. |
| `python main.py --mode swing --confirm <ID> --qty N --price P` | Confirm a PENDING ENTRY action — same flow as the dashboard's "Done" button. Mandatory: `--qty` (executed share count), `--price` (executed fill price). Optional: `--stop X` (overrides `action.suggested_stop`). Creates the position in the open swing book. |
| `python main.py --mode swing --skip <ID>` | Skip a PENDING action. Optional: `--reason "..."`. Idempotent — re-skipping an already-skipped action returns success rather than an error. |
| `python main.py --mode swing --compare HDFCBANK,SBIN,ICICIBANK,KOTAKBANK` | Compare up to 4 NSE symbols side-by-side (S45). Prints a metrics-x-stocks table marking the winning value per row (composite score, % below 52w high, RSI, RS vs NIFTY, volume, R:R, weekly trend up, etc.) plus a "X of N winning metrics" tally per stock so you can see WHY one stock outranks another. Truncates input >4. |
| `python main.py --mode swing --compare-sector BANKING` | Auto-pick the top 4 stocks in a sector (per `SECTOR_MAP` order) and run the same comparison. Sector aliases accepted: `BANK`/`BANKING`, `IT`/`TECH`, `PHARMA`/`HEALTH`, `AUTO`, `ENERGY`/`OIL`, `METALS`, `FMCG`/`CONSUMER`, `INFRA`/`POWER`, `TELECOM`, `CAPGOODS`/`DEFENCE`, `FINANCE`/`NBFC`. |
| `python main.py --mode swing --backtest` | Run the X/Y dip-buy parameter sweep on the cached candle history. Writes `reports/backtest/ath_backtest.{txt,json}` with the full XIRR matrix. Pure-offline; never touches the broker. |

**Read-only inspection from the CLI** (no separate flag — just SQL via
the persistence helpers):

```powershell
# Last full-scan run summary (skips SEARCH_BOX + snapshot rows)
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from modes.swing.persistence import latest_run; r = latest_run(); print(r)"

# All pending actions across runs
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from modes.swing.persistence import pending_actions; [print(a.action_id, a.symbol, a.action_type, a.suggested_price) for a in pending_actions()]"

# All open positions with realised P&L
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from modes.swing.persistence import open_positions, realised_pnl_summary; [print(p.position_id, p.symbol, p.managed_qty, p.entry_price) for p in open_positions()]; print(realised_pnl_summary())"

# AI overlay for a specific symbol (with timestamp)
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from modes.swing.persistence import latest_ai_overlay_for_symbol; r = latest_ai_overlay_for_symbol('SBIN'); print(r[1] if r else None); print(r[0][:500] if r else 'no overlay')"
```

The same compare flow is also reachable via HTTP when the dashboard
server is running:

```
GET /api/swing/compare?symbols=HDFCBANK,SBIN,ICICIBANK,KOTAKBANK
GET /api/swing/compare?sector=BANKING
GET /api/swing/sectors          # list of known SECTOR_MAP keys
```

Reports written by every swing run live under
`reports/swing/<YYYY>/<MM>/swing_report_<DD>.txt` (plus the JSON twin) —
plain-text and grep-able for any external tooling. Same data also flows
through the dashboard's `/swing` page; the two surfaces never disagree
because both call the same persistence helpers.

---

## 7. Project structure

```
ai-portfolio-manager/
├── main.py                          # entry point
├── config.py                        # all settings
├── requirements.txt
├── .env                             # API keys (gitignored)
├── core/                            # shared infrastructure
│   ├── claude_client.py             # backward-compat shim → llm_client.py
│   ├── llm_client.py                # unified AI client (Gemini / GPT / Claude)
│   ├── zerodha_client.py            # Kite wrapper
│   └── logger.py                    # coloured terminal + rotating file log
├── shared/                          # cross-mode services
│   ├── candle_cache.py              # SQLite cache for candles
│   ├── candle_patterns.py           # 14 pure-math pattern detectors
│   ├── market_data.py               # Live prices + history enrichment
│   ├── quant_metrics.py             # returns / momentum / vol / drawdown / Sharpe / beta / capture
│   ├── technical_indicators.py      # Indicators + composite scoring
│   └── tax_db.py                    # tax-ledger DB helpers
├── modes/                           # one folder per CLI mode
│   ├── analyze/                     # `--mode analyze` (read-only long-term review)
│   │   ├── analyser.py              # 8-step orchestrator (NoAI default; --ai overlay)
│   │   ├── types.py                 # Field[T] + StockAnalysis + PortfolioMetrics + GapAnalysis + PortfolioSnapshot
│   │   ├── enrich_noai.py           # deterministic Zerodha + cache + reference-seed enrichment + quant profile
│   │   ├── enrich_ai.py             # AI qualitative overlay (only ai_* slots)
│   │   ├── scoring.py               # six-pillar factor scorecard → rating + risk grade
│   │   ├── recommendation_rules.py  # rating + position context → action (legacy tree as fallback)
│   │   ├── metrics.py               # HHI / top-5 / Sharpe / vol / max-DD / CAGR / cash drag / mcap tier
│   │   ├── gaps.py                  # what's-missing engine + risk/rating concentration checks
│   │   ├── persistence.py           # data/portfolio_analyses.db (two tables, six read helpers)
│   │   └── report.py                # .txt + .json output (drops the legacy .tsv)
│   ├── swing/                       # `--mode swing` (delivery, report-only)
│   │   ├── scanner.py               # universe scan → setup detection → risk sizing → grading
│   │   ├── signals.py               # 5 setup detectors + scoring modifiers
│   │   ├── conviction.py            # conviction A-D + risk grade (shared with the US path)
│   │   ├── risk.py                  # stop / target / position sizing + portfolio limits
│   │   └── persistence.py           # data/swing.db (runs, candidates, actions, positions)
│   ├── trade/                       # `--mode trade` (default; --noai or --ai)
│   │   ├── manager.py               # day orchestrator (run / run_noai / run_test)
│   │   ├── stock_scanner.py         # candle + indicator scanner
│   │   ├── order_engine.py          # 44-check entry pipeline + monitoring
│   │   ├── performance_tracker.py   # SQLite trades + analyses
│   │   ├── report_writer.py         # txt + json reports
│   │   ├── analysis_queue.py        # per-stock AI analysis (--ai)
│   │   ├── candidate_telemetry.py   # `intraday_candidates` writer
│   │   └── volume_baseline.py       # per-symbol intraday RVol baselines
│   └── dashboard/                   # `--mode dashboard` (read-only, tool-wide)
│       ├── cli.py                   # argparse entry
│       ├── server.py                # stdlib HTTP server SPA backend
│       ├── data_layer.py            # DB reads, sheet-verified filtering, FY window
│       ├── metrics.py               # headline P&L, cumulative series (intraday)
│       ├── budget_history.py        # per-day budget from trading_data_*.json
│       ├── verdict.py               # capital-ladder traffic-light engine
│       ├── theme.py                 # shared design tokens + light/dark theme
│       ├── nav.py                   # shared top nav + theme toggle
│       ├── home_page.py             # / command centre (net worth, books, inline login)
│       ├── home_summary.py          # cross-book aggregation for the home page
│       ├── render_html.py           # /trading Chart.js SPA shell
│       ├── render_text.py           # plain-text mode (--text)
│       ├── portfolio_page.py        # /portfolio + /portfolio/<symbol> + /login (D24-D29)
│       ├── portfolio_actions.py     # background "Analyse now" worker (D26/D27)
│       ├── swing_page.py            # /swing + /swing/<symbol>
│       ├── us_page.py               # /us + /us/<symbol> (USD/INR toggle)
│       ├── us_analysis.py           # yfinance scan + grading for the US book
│       ├── dryrun_page.py           # /dryrun per-strategy P&L
│       ├── theory_page.py           # /theory/<slug> renderer
│       ├── tax_page.py              # /tax FY-summary + projection
│       ├── tax/                     # FY tax sub-package (slabs, fy_summary)
│       └── docs/DASHBOARD_ROADMAP.md # D1+D1.1+D13+D16+D17+D24-D31 done; D2-D23 pending
├── scripts/
│   ├── trade/                       # trade-mode CLIs (see Section 9)
│   └── shared/                      # cross-mode CLIs (see Section 10)
├── docs/                            # see Section 3 doc map
├── data/                            # gitignored (trades.db, tokens, etc.)
├── reports/                         # generated; gitignored
└── logs/                            # rotating logs; gitignored
```

---

## 8. Running on a VM (Azure Ubuntu)

A cloud VM with a **static public IP** sidesteps SEBI's IP whitelist
hassle (whitelist once on Kite, never again). Azure B1s free tier covers
the first month.

### One-time setup

```bash
ssh azureuser@<vm-ip>
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git
```

Authenticate with GitHub via one of:

- **GitHub CLI:** `sudo apt install -y gh && gh auth login`
- **Personal Access Token:** clone with `https://USER:TOKEN@github.com/...`
- **SSH key:** `ssh-keygen -t ed25519` → paste `~/.ssh/id_ed25519.pub`
  into GitHub → Settings → SSH keys.

```bash
git clone <repo-url> ai-portfolio-manager
cd ai-portfolio-manager
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cat > .env <<'EOF'
ZERODHA_API_KEY=...
ZERODHA_API_SECRET=...
GEMINI_API_KEY=...     # default provider; or use OPENAI_API_KEY / CLAUDE_API_KEY
# Optional but recommended on a VM — enables ASSISTED login so you only
# type a 6-digit code once a day (vs pasting the full redirect URL).
KITE_USER_ID=AB1234
KITE_PASSWORD=your_kite_web_password
# Optional — fully unattended login (read §5.4.3 first; on a VM the
# .env risk is similar but the blast radius is the VM, not your laptop).
# KITE_TOTP_SECRET=JBSWY3DPEHPK...
EOF
chmod 600 .env                      # restrict to your VM user
python scripts/shared/backup_data.py --ssh   # pull data from your private backup repo
python main.py --mode login           # picks ASSISTED if KITE_USER_ID+PASSWORD set,
                                       # else falls back to manual mode (option 'm')
```

### Daily operation

One-command bring-up (recommended) — the script `cd`s into the repo,
activates the venv, runs `git pull`, pulls the latest data from the
backup repo (`--all-remote`, auto-confirmed), and starts
`--mode trade --noai --max 50000`. From your VM home directory:

```bash
ssh azureuser@<vm-ip>
tmux new -s bot                              # detach with Ctrl+B, D
./ai-portfolio-manager/scripts/trade/start_trade_vm.sh
# overrides: ./ai-portfolio-manager/scripts/trade/start_trade_vm.sh --ai --max 30000
```

Step-by-step (if you want to see each phase):

```bash
ssh azureuser@<vm-ip>
cd ai-portfolio-manager
source venv/bin/activate
tmux new -s bot                       # detach with Ctrl+B, D
python main.py --mode trade
```

To re-attach after disconnect: `tmux attach -t bot`. After a VM reboot
the tmux session is gone; start fresh with `tmux new -s bot`.

After the trading day:

```bash
python scripts/shared/backup_data.py --ssh
```

> **Tokens are IP-specific.** Delete `data/access_token.json` when
> switching machines so the bot prompts for fresh login.

---

## 9. Reports & data

### Report layout

- Phase 1 → `reports/modes/trade/<year>/<month>/portfolio_report_DD.txt` + `.json`.
- Phase 2 → `reports/trading/<year>/<month>/trading_report_DD.txt` + `.json`.

Multiple Phase-2 sessions on the same day merge into one combined report
with cumulative P&L. Reports include full tax breakdown and Claude API
costs.

### Database (SQLite)

`data/trades.db` (auto-created) and `data/candle_cache.db`. `data/` is
gitignored except `candle_cache.db` (public market data is committed).

| Table | Stores |
|-------|--------|
| `trades` | Intraday trades — symbol, side, entry/exit, qty, P&L, exit reason |
| `intraday_tax_ledger` | Per-trade charges for ITR-3 |
| `capital_gains_ledger` | Short-term + long-term capital gains |
| `portfolio_analyses` | Phase 1 results — action, conviction, reasoning, targets |

### Utility scripts

| Script | Purpose |
|--------|---------|
| `scripts/trade/view_trades.py` | All intraday trades with P&L summary |
| `scripts/trade/view_performance.py` | Daily P&L, win rate, exit stats, indicator correlation |
| `scripts/shared/view_analyses.py` | Phase 1 analyses with action status |
| `scripts/shared/generate_sheet.py` | TSV spreadsheet from a portfolio report (1 Claude call) |
| `scripts/shared/view_candle_cache.py` | Inspect candle cache contents |
| `scripts/trade/verify_trades.py` | EOD trade verification vs Zerodha API |
| `scripts/trade/rejection_audit.py --append-report` | Verdict on every skipped entry |
| `scripts/trade/exit_coverage_check.py` | Truth-table guard — fails if any thesis-broken in-loss `(entry, fresh, pattern)` cell is uncovered by both `_signal_reversal_exit` and `_signal_decay_exit`. Run as part of the smoke triple after any exit-pipeline change to catch cross-gate dead zones (the 2026-04-28 sign-flip class) before they ship. |
| `scripts/trade/strategy_stability_check.py [--lookback N] [--window-days N]` | Reads `git log` and reports (a) currently-open 10-trading-day no-tune windows opened by recent strategy commits, (b) any tuning commit that landed inside another commit's window without an exempt token. Informational only — never blocks a commit or push. Roadmap #245. **Bug-fix commits that touch tracked strategy files MUST include `bugfix-during-stability-window` in the subject** so the script doesn't spuriously open a fresh window; `#NNNR` removal commits use `removal-trigger-fired`. See `copilot/review-cycle.md` Wrap-up table for the full classification rules. |
| `scripts/trade/view_candidates.py [--date YYYY-MM-DD] [--since YYYY-MM-DD] [--symbol STK] [--side BUY/SELL] [--status STATUS] [--summary] [--hash]` | Read-only viewer for the `intraday_candidates` telemetry table (Roadmap #259). Filters by date / symbol / side / status (`SCORED`, `ENTERED`, `REJECTED`); `--summary` totals; `--hash` lists distinct config hashes seen in the window. |
| `scripts/trade/build_volume_baseline.py [--lookback N] [--universe UNIV] [--symbol STK] [--dry-run]` | Rebuilds `data/volume_baseline.db` from the trailing N trading days of 15-min candles in `data/candle_cache.db` (Roadmap #260). Computes per-symbol, per-hour mean cumulative-volume share. After build, set `Config.INTRADAY_VOLUME_BASELINE_ENABLED = True` to switch the scanner's RVol denominator from linear pro-rating to baseline-aware. |
| `scripts/trade/backtest.py --from YYYY-MM-DD --to YYYY-MM-DD [--symbol STK] [--min-score N] [--max-trades-per-day N]` | Offline replay harness (Roadmap #24). Walks 15-min cached candles, applies a simplified directional score (EMA-cross + RSI + 1h momentum), and simulates synthetic trades using ATR-derived SL / target geometry and `Config.SQUARE_OFF_*`. Output: per-trade JSON in `reports/backtest/` plus a stdout summary (WR / PF / expectancy / max-DD), each row stamped with `Config.snapshot_hash()` for replay-vs-live comparison. **Do not read absolute P&L as a forecast** — see the script docstring "Scoring fidelity" note. |
| `scripts/trade/walk_forward.py [--window {FULL,TRAIN,TEST}]` | Out-of-sample / walk-forward validator over the 2-year `../ai-portfolio-backtest-data` candle store (Phase 0). Runs the frozen audit config net-of-cost across FULL, TRAIN (year 1), TEST (year 2 OOS) and half-year slices, then prints a TRAIN-vs-TEST promotion verdict. Used to prove an edge holds out-of-sample **before** any live/dry deployment. |
| `scripts/trade/regime_analysis.py [--universe NIFTY50] [--window {FULL,TRAIN,TEST}]` | Phase 1 regime classifier + per-regime PF breakdown. Builds a synthetic equal-weight market proxy (no index/VIX exists in the data), labels each day TREND / RANGE / VOLATILE from **morning-only** features (no lookahead), tags trades by entry-day regime, and prints per-regime metrics plus routing scenarios (Trade ALL / Skip RANGE / VOLATILE-only). |
| `scripts/trade/promotion_check.py [--window N] [--json]` | Codified PASS / FAIL gate for capital scale-ups. Reads the last N (default 20) trading sessions from `data/trades.db` and tests profit factor, expectancy, day-WR, trade-WR and max-drawdown against fixed thresholds. Exit codes: `0` = PASS, `1` = FAIL, `2` = INSUFFICIENT_DATA. Run BEFORE any major risk-knob relax or capital scale; the script is the single source of truth on whether the live edge is positive enough to justify the change. |

All scripts support `--help`.

### Data sync (private repo)

`data/`, `reports/`, `logs/`, and `copilot/` are personal — keep them in a **separate
private repo** so they're portable across machines. The default sync
is a glob walk of those folders so any new file (e.g. a new DB, report
subfolder, or private runbook) is picked up automatically with no code
change. The top-level `.env` is opt-in via `--include-env` for trusted
private-repo machine migration only.

**Synced as of 2026-05-11 (everything in these locations):**
- `data/trades.db` — trades, intraday_tax_ledger, capital_gains_ledger, **`intraday_candidates`** (Roadmap #259, full SCORED → ENTERED/REJECTED → OUTCOME chain stamped with `Config.snapshot_hash()`)
- `data/intraday_tax.db`, `data/tax.db` — tax DBs
- **`data/volume_baseline.db`** (Roadmap #260) — per-(symbol, hour) cumulative volume share, built by `scripts/trade/build_volume_baseline.py`
- `data/zerodha_authoritative_*.json` — quarterly Zerodha truth snapshots
- `data/candle_cache.db` — git-tracked alongside the code repo (already identical across machines, NOT in the data backup)
- `reports/dashboard/`, `reports/modes/trade/`, `reports/trading/`, **`reports/backtest/`** (Roadmap #24, per-trade JSON stamped with `Config.snapshot_hash()` so two machines with the same config produce comparable runs)
- `logs/portfolio.log*`
- `copilot/` — private runbooks/checklists that should follow you to a new laptop/VM
- `.env` — only when `--include-env` is explicitly passed

**Never synced by default (operator secrets / local-only):** `.env`, `data/access_token.json`, `data/access_token.json.bak`, `data/ZerodhaTaxPL/`, `__pycache__/`.

```bash
python scripts/shared/backup_data.py            # two-way append-merge + push (HTTPS)
python scripts/shared/backup_data.py --ssh      # SSH (Linux VMs)
python scripts/shared/backup_data.py --dry-run  # preview, no writes
python scripts/shared/backup_data.py --include-env --dry-run  # preview one-time .env migration

# Manual-fix flow (you edited a row/report on this machine — make it the truth)
python scripts/shared/backup_data.py --prefer local    # local wins, edits propagate via UPSERT
python scripts/shared/backup_data.py --prefer remote   # remote wins (rare — adopt VM's version)

# Nuclear reset (also DELETES files not on the chosen side; prompts unless --yes)
python scripts/shared/backup_data.py --all-local       # full overwrite of remote
python scripts/shared/backup_data.py --all-remote      # full overwrite of local
python scripts/shared/backup_data.py --include-env --all-local --yes
python scripts/shared/backup_data.py --include-env --all-remote --yes
```

| Scenario | Action |
|----------|--------|
| File only one side | Copied across |
| File both sides, identical | Skipped |
| `.db` in both, different (default) | **Append-merge** — new rows from each side added; nothing overwritten or deleted |
| `.db` in both with `--prefer X` | **Row UPSERT** — X's values win on key collisions; rows only on the OTHER side preserved |
| Other file in both, different (default) | Asks `l/r` |
| Other file in both, different with `--prefer X` | X's copy kept (no prompt) |
| Log files (`logs/portfolio.log`) | Always line-merged (chronological union) |

**Two normal flows**

1. **EOD VM → coding machine** (no flag needed):
   - VM: `python scripts/shared/backup_data.py --ssh` after market close.
   - Dev machine: `python scripts/shared/backup_data.py` next morning.
   - DBs append-merge cleanly because both sides only added new rows.

2. **Manual data fix on coding machine → VM** (use `--prefer local`):
   - Edit a DB row or report .txt to correct bad data.
   - `python scripts/shared/backup_data.py --prefer local` — your edits become the truth.
   - VM picks up corrections on its next pull.

  Important: row-level sync does not delete remote-only ghost DB rows yet. If a repair deliberately removes rows from `trades.db`, use the new deletion-aware path:

  ```powershell
  python scripts/shared/backup_data.py --canonical-trades --dry-run   # shows local sha256 + remote sha256 + per-table row deltas, no writes
  python scripts/shared/backup_data.py --canonical-trades             # backs up the remote DB to a timestamped file then bit-for-bit replaces it with the local DB
  ```

  This propagates row deletions correctly (Roadmap #270). Use the dry-run first whenever you’re about to overwrite the remote DB so you see exactly which tables differ. The legacy nuclear `--all-local` still works but copies *all* files; `--canonical-trades` is the surgical option for canonical DBs only. As of 2026-05-11 the canonical set is `data/trades.db` + `data/volume_baseline.db` — both will be diffed and replaced together in a single pass when you use the flag, with one timestamped backup per file.

**Moving to a new machine**

Use [Section 5.5](#55-new-machine-restore) as the canonical restore
checklist. This data-sync section documents the mechanics behind that
flow; the setup section keeps the actual old-laptop and new-machine
commands in one place.

> The data repo MUST be **Private**. The main code repo has no link to
> it — only the sync script knows the URL.

### Backtest/replay data sync (private repo)

The backtest audit uses a separate private repository for
normalized historical replay data:
`https://github.com/yash040599/ai-portfolio-backtest-data`.

This is intentionally separate from the operational data repo above.
Operational data is mutable and needs row-level SQLite merges; replay
datasets should be versioned snapshots. The repo is cloned beside the
main checkout at `../ai-portfolio-backtest-data` by default on both the
Windows dev machine and the Linux trading VM. The old in-checkout
`backtest_data/` path is still supported only when `BACKTEST_DATA_PATH`
or `--path` points there.

> **Git LFS required.** The candle stores in this repo (notably
> `candles/intraday_15m.sqlite`, ~220 MB) exceed GitHub's 100 MB file
> limit, so all `*.sqlite` files are stored via **Git LFS**. You must
> have `git-lfs` installed before cloning/pulling, otherwise you only get
> small text pointer files instead of the real databases.
>
> ```bash
> # one-time, per machine
> git lfs install
> # Windows:        winget install GitHub.GitLFS
> # Debian/Ubuntu:  sudo apt-get install git-lfs
> # macOS:          brew install git-lfs
> ```
>
> `sync_backtest_data.py` checks for `git-lfs` and runs `git lfs pull`
> automatically on clone/pull, so the script is the recommended way to
> fetch the data. If you cloned manually and see tiny `*.sqlite` files,
> run `git -C ../ai-portfolio-backtest-data lfs pull`.

Set these in `.env`:

```bash
BACKTEST_DATA_REPO_URL_HTTPS=https://github.com/yash040599/ai-portfolio-backtest-data.git
BACKTEST_DATA_REPO_URL_SSH=git@github.com:yash040599/ai-portfolio-backtest-data.git
BACKTEST_DATA_PATH=../ai-portfolio-backtest-data
```

Common commands:

```bash
python scripts/shared/sync_backtest_data.py              # clone/pull + status using HTTPS/env default
python scripts/shared/sync_backtest_data.py --ssh        # Linux VM flow using SSH
python scripts/shared/sync_backtest_data.py --status     # show data repo status
python scripts/shared/sync_backtest_data.py --push --commit --message "update replay dataset"
```

Seed the first replay dataset from the local candle cache:

```bash
python scripts/trade/export_backtest_data.py --dry-run
python scripts/trade/export_backtest_data.py
python scripts/shared/sync_backtest_data.py --push --commit --message "seed replay data from candle cache"
```

The VM should pull the repo before replay or strategy-research workflows
need historical data, then read local files from `../ai-portfolio-backtest-data`. Do not
fetch candles directly from GitHub during replay/trading runtime.

Full contract: [docs/TRADE_BACKTEST_DATA.md](docs/TRADE_BACKTEST_DATA.md).

**GitHub 100 MB file limit**

GitHub rejects any single file > 100 MB on push.

For the **backtest-data repo**, the candle stores are tracked with **Git
LFS** (see the LFS callout above), so large `*.sqlite` files push fine.
`sync_backtest_data.py` only blocks a large file if it is *not* yet
LFS-tracked — fix that by adding the pattern in the data repo:

```bash
git -C ../ai-portfolio-backtest-data lfs track "*.sqlite"
git -C ../ai-portfolio-backtest-data add .gitattributes
git -C ../ai-portfolio-backtest-data commit -m "track sqlite via LFS"
```

For the **operational data repo** (no LFS), the sync script will fail
with a clear error from `git push` if a file is too big. Two protections
are in place:

- **Dedup key uses null-safe `IS` comparison** so the `trades` table
  doesn't double on every sync (a single bug here previously inflated
  `data/trades.db` from <1 MB to 135 MB over ~20 syncs).
- **Periodic check.** If `data/trades.db` ever grows unexpectedly
  (>10 MB for normal usage), inspect with:
  ```bash
  python -c "import sqlite3; c=sqlite3.connect('data/trades.db'); print('trades rows:', c.execute('SELECT COUNT(*) FROM trades').fetchone()[0])"
  ```
  Real row count for a few months of trading should be in the low
  hundreds. If you see >10 000, the dedup is broken — bisect by date
  and rebuild the table (see `_dedup` pattern in commit history).

`--all-local` and `--all-remote` are NOT immune to the 100 MB limit —
they `git push` the chosen side as-is. If the file is already too big,
you must shrink it first (DELETE + VACUUM) before any sync flag will
succeed.

---

## 10. Taxation

Intraday is **speculative business income** in India (ITR-3). Bot tracks
brokerage, STT, GST, stamp duty per trade, separates short-term and
long-term capital gains.

Full guide: **[docs/TRADE_TAX_GUIDE.md](docs/TRADE_TAX_GUIDE.md)** (slabs, advance
tax dates, loss carry-forward).

| Script | Purpose |
|--------|---------|
| `scripts/trade/fill_intraday_ledger.py` | Build intraday ledger from trade JSONs (auto-runs EOD) |
| `scripts/trade/verify_trades.py` | Verify trades vs Zerodha; correct prices in reports + ledger + DB |
| `scripts/shared/import_zerodha_taxpnl.py [--fy YYYY]` | Import Zerodha Tax P&L xlsx (intraday + capital gains) |
| `scripts/shared/tax_summary.py [--intraday] [--fy YYYY]` | Combined tax summary — speculative + STCG + LTCG + estimated tax |
| `scripts/trade/view_intraday_ledger.py [--fy YYYY] [--list]` | Intraday ledger view |
| `scripts/shared/view_capital_gains_ledger.py [--list]` | Capital gains ledger view |

Tax rates configurable in [config.py](config.py): `TAX_RATE_PCT`,
`STCG_TAX_RATE_PCT`, `LTCG_TAX_RATE_PCT`, etc.

---

## 11. Cost & safety

### Cost

| Cost | Amount | Frequency |
|------|--------|-----------|
| Zerodha Kite Connect | Rs.500 | Monthly |
| AI API (Gemini free tier) | Rs.0 | Per trading day (`--ai` only; NoAI = Rs.0) |
| AI API (GPT / Claude paid) | ~Rs.10–100 | Per trading day (if using paid provider) |
| Brokerage + charges | ~0.05–0.15% of turnover | Per trade |

### Safety features

- **Dry-run** — simulated P&L with time-of-day slippage modelling.
- **Circuit breaker** — daily loss hard-stop with cooldown + max trips.
- **Daily-loss soft-stop** — at -1.5% blocks new entries, manages existing.
- **Peak-drawdown stop** — blocks new entries when day P&L gives back ≥1.5% from intraday peak.
- **MTM-aware circuit breaker** — circuit breaker, soft-stop, and peak-drawdown all include open-position unrealised MTM (not just closed P&L), so blowups are caught while positions are still open.
- **Choppy-morning entry pause** — auto-pauses new entries (15 min, sliding) when NIFTY 15-min ADX prints weak (<16) for 3 consecutive scans in 09:30–10:30 IST AND ≥2 recent exits were STAGNANT/SIGNAL_DECAY. Re-arms each session.
- **Whipsaw guard** — pauses entries after 3 consecutive losing exits (post-#244 broadening: any of STOP_LOSS, MOMENTUM_KILL, STAGNANT_EXIT, SIGNAL_DECAY, or LOSER_EXIT with `pnl < 0`; EOD/operator closes excluded).
- **Per-symbol re-entry cooldown** — 30 min on same `SYMBOL_SIDE`.
- **Stale-score guard** — after the post-open observation wait, re-runs the scoring and aborts entries whose conviction sign-flipped, decayed below 60% of the scan-time score, OR (#199) lost magnitude (`|fresh| + 0.3 < |entry|`) — catches the slow-bleed setups the magnitude-only floor missed.
- **Post-entry momentum kill (#198, retuned by #233)** — exits at market between 3 and 5 min after fill if the trade is unrealised-loss, has moved adversely by ≥0.40% (≈4× typical NSE intraday spread), AND has covered <25% of the entry→target distance. Caps slow-bleed losers at ~-0.4% instead of waiting for the -1.1% SL hit. The 3-min grace + adverse-move floor were added on 2026-04-27 after the original 60s/no-floor settings killed 4/4 morning entries on sub-spread micro-moves.
- **Pattern↔tech contradiction penalty (#200)** — at the scanner combine, subtracts 2.0 from `|combined_score|` when patterns include an opposite-side reversal (e.g. BUY candidate showing `BEARISH_ENGULFING`) and 0.5 when patterns include `DOJI` indecision; weak-conviction conflict setups fall below `MIN_SCORE` naturally.
- **VWAP statistical-band gate (#201)** — blocks BUY at the upper 1σ/2σ VWAP band and SELL at the lower 1σ/2σ; complements the existing % VWAP-extension check with a volatility-adaptive band classifier. Override at `|score| ≥ 7.0`.
- **Late-entry tightening (#202, retuned by #239, coupled by #246)** — after 10:00 IST: `MIN_SCORE` bumped by +1.0 (raised 0.5 → 1.0 by #239 after first live day showed +0.5 was too gentle), then clamped to `>= SIGNAL_DECAY_MIN_ENTRY_SCORE = 7.0` by #246 so the entry floor is never below the rescue-gate floor (no-rescue-zone alignment, motivated by JIOFIN 2026-04-28). R:R floor and concurrency are owned by always-on `RR_HARD_FLOOR` + `dynamic_max_positions(budget)` (simplified by #225).
- **Realised-P&L recovery on restart (#203)** — on init, scans Zerodha net-positions for already-closed MIS round-trips not in our session and imports them as synthetic CLOSED records so the MTM-aware safety gates and adaptive budget reason from the correct realised baseline after a mid-session restart.
- **Lunch-lull skip** — 11:30-12:15 IST unless `|score| ≥ 5.7`.
- **Charge-aware target (retuned by #238)** — gross target ≥ 3× round-trip charges (was 2×), so every trade carries 2× charges of slippage cushion.
- **Budget-adaptive minimum profit (#237)** — `effective_min_profit()` floor: Rs.135 on TINY/SMALL (3× typical round-trip charges), Rs.200 NORMAL, Rs.400 LARGE. Auto-scales when you raise `--max`.
- **Budget-adaptive spread cap (#236)** — `effective_max_spread()`: 0.20% on TINY/SMALL, 0.30% NORMAL/LARGE. Tighter cap on small budgets where spread eats a large share of the per-trade charge hurdle.
- **Budget-regime trade cap** — `MAX_TRADES_PER_DAY` can be regime-tightened via `BUDGET_TRADE_CAP_DELTA`, but `BUDGET_REGIME_ENABLED = False` post-2026-05-26 audit, so the live cap is a flat **2 trades/day** (gate K1). Forces fewer-and-better trades where the per-trade charge hurdle is high.
- **Budget-regime gates** — ADX, score floor, and trade-cap auto-tighten on TINY/SMALL accounts (#165).
- **Loss-adjusted sizing** — shrinks position size after losses.
- **ATR-based SL/target** — pure ATR with structural-level cap.
- **Bid-ask spread + impact-cost check** — skips paper-thin books.
- **Crash recovery** — re-adopts orphaned positions and orphan SL-M orders on restart.
- **Loud SL-M failure alert** — never silently runs naked.
- **`market_protection` on every order** — Zerodha-side circuit safeguard.
- **Existing demat holdings are READ-ONLY** — only the managed budget pool is traded.
- **Graceful shutdown** — Ctrl+C squares off everything before exit.

Full risk architecture: **[docs/TRADE_STRATEGY.md](docs/TRADE_STRATEGY.md#risk-management--entry-pre-checks)**.

---

## 12. Disclaimer

This software is for educational and experimental purposes. Stock market
trading involves substantial risk of loss. Past performance (including
dry-run results) does not guarantee future results. Use at your own
risk. The authors are not responsible for any financial losses incurred
from using this software.
