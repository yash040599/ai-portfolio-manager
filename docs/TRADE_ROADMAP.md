# Trading Roadmap

This roadmap was reset on 2026-05-15 after the Chan-framework audit:
[docs/audit/TRADE_AUDIT_2026-05-15_CHAN_FRAMEWORK.md](audit/TRADE_AUDIT_2026-05-15_CHAN_FRAMEWORK.md).

The old roadmap tried to be a backlog, a completed-features archive, a bug-fix log, and a research notebook at the same time. That made it hard to see what to do next. From now on this file is only the staged plan for intraday trading. Strategy history lives in [docs/TRADE_EVOLUTION.md](TRADE_EVOLUTION.md), and detailed old rows remain available in git history before this reset.

## Current Posture

| Area | Status |
|---|---|
| Supported live mode | Paused. No new live trades until the staged Chan-method process allows them. |
| Runtime reset status | `Stage 0 - Chan Research Reset` is surfaced in startup logs, reports, and dashboard status; live order placement is guarded by `TRADE_LIVE_TRADING_PAUSED = True`. |
| Capital scaling | Blocked until `scripts/trade/promotion_check.py` returns PASS on a fresh forward window. |
| Latest promotion check | FAIL: PF 0.839, expectancy Rs.-6.11/trade, day win rate 30.0%. |
| Current FY intraday result | About Rs.-3,928.68 net after charges, 184 tax-ledger rows. |
| Strategy version in config | `v1.0-2026-05-11`; Stage 0 status is operational metadata and does not alter the strategy hash. |
| Roadmap operating mode | Stage 1 Full-Fidelity Replay: T1.0 data contract/seed, T1.1 replay-safe scanner scoring, and T1.2 accepted/rejected candidate replay are shipped; T1.3 after-cost replay is next. |

## Ground Rules

1. No new live entry gate unless it fixes a verified bug or protects against a proven safety hole.
2. No score-weight tuning until full-fidelity replay exists.
3. No capital scale-up until promotion metrics pass after costs.
4. Every new strategy must have a strategy id, config hash, backtest result, and forward result.
5. Strategy families must be tested separately before they are blended.
6. If an exit gate changes, run `scripts/trade/exit_coverage_check.py`.
7. Backtest data must be versioned outside this main tool repo, then read locally through a gitignored cache path.
8. If a roadmap count or archive is needed, use `TRADE_EVOLUTION.md` and git history, not this file.

## Paused Or Deferred

| Feature family | Decision |
|---|---|
| Score-weighted sizing | Keep disabled. Current score edge is not stable enough to size bigger by score. |
| Rolling-PF full-day blackout | Keep disabled. It was previously net-negative after directional pause existed. |
| Late no-rescue score clamp | Keep disabled. Prior EV audit contradicted the premise. |
| Intraday volume baseline | Keep disabled until the baseline DB is built, inspected, and tested. |
| AI trade selection | Not part of the supported reset path. Keep optional, not evidence for live edge. |
| HFT/WebSocket work | Defer until expectancy is positive. Speed does not fix a losing strategy. |
| More combined-score gates | Defer. The current problem is unproven entry edge, not lack of guards. |

## Stage 0 - Research Reset

Purpose: stop the live tuning loop and make the current state explicit.

Deliverables:

| ID | Work | Done When |
|---|---|---|
| T0.1 | Add a visible reset label such as `Chan Research Reset` to runtime/report/dashboard status. | Shipped 2026-05-15: startup banner, trading report payload/text, and dashboard status surface the active reset phase. |
| T0.2 | Warn if candidate telemetry is unhealthy during reset mode. | Shipped 2026-05-15: startup logs print candidate telemetry health before any broker login. |
| T0.3 | Document the supported live posture in strategy/statistics docs. | Shipped 2026-05-15: docs agree that live trading is paused and evidence/replay comes first. |
| T0.4 | Keep promotion gate as the scale-up blocker. | `promotion_check.py` result is referenced before any capital/risk relaxation. |

Exit criteria:

- The tool and docs say the same thing about the active phase.
- Candidate telemetry is healthy on trading days.
- No strategy behavior changes have been mixed into the reset-label change.

## Stage 1 - Full-Fidelity Replay

Purpose: make live decisions replayable before strategy tuning.

Data architecture decision for Stage 1:

- Keep `ai-portfolio-manager` as the trading/replay tool, not the raw data store.
- Keep the existing private `data` repo for current ignored operational data/reports.
- Use `https://github.com/yash040599/ai-portfolio-backtest-data` as the separate private backtest-data repo.
- Clone/sync that repo beside the main checkout at `../ai-portfolio-backtest-data`; replay reads the local copy at runtime on both Windows and the Linux VM.
- Use `scripts/shared/sync_backtest_data.py` for clone/pull/status/push. It is a Git snapshot sync, not the row-merge operational backup flow.
- Use `scripts/trade/export_backtest_data.py` to seed the first SQLite/CSV replay dataset from `data/candle_cache.db` without broker/network calls.
- `scripts/trade/backtest.py` now reads `../ai-portfolio-backtest-data/candles/intraday_15m.sqlite` when present, falling back to the old candle cache only when the Stage 1 data repo has not been cloned. It supports `--score-mode scanner` for replay-safe scanner-style scoring, keeps `--score-mode simple` as the legacy comparison path, and writes a config-hash-stamped candidate ledger for accepted/rejected replay decisions.
- Treat `market-research` as standalone ATH-dip research/reference material, not as an intraday replay runtime dependency.
- Full data contract: [docs/TRADE_BACKTEST_DATA.md](TRADE_BACKTEST_DATA.md).

Deliverables:

| ID | Work | Done When |
|---|---|---|
| T1.0 | Decide the normalized backtest data contract and sync model. | Shipped 2026-05-15: [docs/TRADE_BACKTEST_DATA.md](TRADE_BACKTEST_DATA.md) defines repo roles, sibling `../ai-portfolio-backtest-data/`, manifest fields, SQLite/CSV shape, Linux VM pull flow, sync/export scripts, seeded data repo, migration restore path, and the backtest data-root bridge. |
| T1.1 | Parameterise the scanner/replay clock so backtest can use live scoring logic instead of simplified scoring. | Shipped 2026-05-15: scanner/indicator scoring accepts an injected `as_of` time, replay can run `--score-mode scanner` from local candles without Zerodha calls, and `scripts/trade/replay_clock_check.py` guards against wall-clock leakage. |
| T1.2 | Replay accepted and rejected candidates by config hash. | Shipped 2026-05-17: backtest JSON includes a `candidates` ledger with `ENTERED`/`REJECTED` status, replay rejection reason, config version/hash, score fields, and entry/exit outcome when a synthetic trade enters. No-trade runs now still write an evidence artifact. |
| T1.3 | Add cost model to replay: charges, spread, slippage, square-off. | PF/expectancy are reported after costs. |
| T1.4 | Add live-vs-replay comparison report. | Recent sessions show comparable candidate count, entry count, and exit split. |

T1.1 implementation record:

| Step | Work | Boundary / Done When |
|---|---|---|
| T1.1a | Map wall-clock dependencies in the replay scoring path. | Done: audited scanner, indicator, and backtest scoring paths; manager/order-engine event loop clocks stayed out of scope. |
| T1.1b | Add an explicit replay time parameter. | Done: scanner analysis helpers accept optional `as_of` and default to live `now_ist()` when absent. Live behavior remains unchanged. |
| T1.1c | Make indicator helpers replay-safe. | Done: VWAP, ORB, gap, hourly/short-cutoff, pre-open tagging, and intraday-volume pro-rating use the injected replay time. |
| T1.1d | Let backtest call the real NoAI scoring path. | Done: `scripts/trade/backtest.py --score-mode scanner` scores local historical candles without Zerodha network calls; `--score-mode simple` remains available for comparison. |
| T1.1e | Add a small parity/guard test. | Done: `scripts/trade/replay_clock_check.py` verifies a fixed historical timestamp produces session VWAP/ORB features independent of the actual current date. |

T1.2 implementation record:

| Step | Work | Boundary / Done When |
|---|---|---|
| T1.2a | Add candidate-level replay rows. | Done: each non-zero replay score in the entry window becomes a candidate row stamped with config version/hash, score mode, score, side, indicator summary, and `ENTERED`/`REJECTED` status. |
| T1.2b | Preserve rejected-only evidence. | Done: high-threshold/no-trade runs write JSON with rejection counts instead of exiting without an artifact. |
| T1.2c | Make replay output filenames run-specific. | Done: default report names include date range, symbol scope, score mode, minimum score, and config hash so threshold/symbol sweeps do not overwrite each other. |
| T1.2d | Validate with local smoke runs. | Done: `--min-score 999 --score-mode scanner` produced 10,405 `SCORE_FLOOR` rejections; a RELIANCE scanner replay produced 95 candidates, 46 synthetic trades, and mixed `SCORE_FLOOR`/`REPLAY_TRADE_CAP` rejections. |

Next Stage 1 commands:

```powershell
git status --short --branch
git -C ../ai-portfolio-backtest-data status --short --branch
.\.venv\Scripts\python.exe scripts\shared\sync_backtest_data.py --pull --status
.\.venv\Scripts\python.exe -m py_compile scripts\trade\backtest.py scripts\trade\replay_clock_check.py modes\trade\stock_scanner.py shared\technical_indicators.py
.\.venv\Scripts\python.exe scripts\trade\replay_clock_check.py
.\.venv\Scripts\python.exe scripts\trade\backtest.py --from 2026-04-07 --to 2026-04-24 --min-score 999 --score-mode scanner
.\.venv\Scripts\python.exe scripts\trade\backtest.py --from 2026-04-07 --to 2026-04-24 --symbol RELIANCE --min-score 2 --score-mode scanner
```

T1.1 target files changed:

- `modes/trade/stock_scanner.py`: `_analyse_stock`, `_filter_today_candles`, `_prefilter_universe`, `scan_noai`, pre-open tag, scan timestamp, short cutoff, volume-baseline hour.
- `shared/technical_indicators.py`: `compute_technical_score`, `opening_range_score`, `gap_analysis_score`, and any helper that filters to today's candles or decays ORB by current hour.
- `scripts/trade/backtest.py`: bridge from candle rows to scanner-style feature/scoring call, with a fallback/simple mode until the live-score path is validated.
- `scripts/trade/replay_clock_check.py`: fixed historical timestamp guard for replay-safe session features.

Do not do these before T1.3 is complete:

- Do not add `MEAN_REVERSION_V1` yet.
- Do not tune score weights or entry thresholds.
- Do not touch broker order placement or require Zerodha paid/dev APIs.
- Do not remove the simplified replay score until the real-score path is producing stable, inspectable output.

Exit criteria:

- 30-60 sessions can be replayed under a named config hash.
- Replay reports WR, PF, expectancy, max drawdown, and exit reasons.
- Output distinguishes in-sample from out-of-sample.

## Stage 2 - Mean-Reversion V1

Purpose: test the first clean book-aligned strategy family.

Hypothesis:

Liquid NSE names that stretch to a statistically meaningful VWAP band and show exhaustion can revert enough intraday to beat charges, spread, and slippage when the broader regime is not strongly trending against the reversion.

Deliverables:

| ID | Work | Done When |
|---|---|---|
| T2.1 | Add strategy id `MEAN_REVERSION_V1`. | Every candidate/trade/report row can be filtered by strategy id. |
| T2.2 | Define mean-reversion entry rules from VWAP band stretch, RSI/exhaustion, and cost cushion. | Momentum/ORB components are not part of the entry reason. |
| T2.3 | Backtest against current NoAI baseline. | Report shows whether MR beats the all-in-one baseline after costs. |
| T2.4 | Dry-run forward sample. | At least 20 sessions collected before live pilot. |
| T2.5 | Live pilot only after dry-run passes. | Promotion gate PASS, with no capital scale-up yet. |

Exit criteria:

- Backtest PF >= 1.15 after costs over at least 60 sessions.
- Walk-forward or out-of-sample segment remains positive.
- Forward dry-run has at least 20 sessions and passes promotion criteria.

## Stage 3 - Momentum / ORB V1

Purpose: reintroduce trend-following only where replay proves it works.

Deliverables:

| ID | Work | Done When |
|---|---|---|
| T3.1 | Add strategy id `MOMENTUM_ORB_V1`. | Momentum candidates are separable from mean reversion. |
| T3.2 | Use ORB, ADX, EMA/SuperTrend/MACD, VWAP alignment, and breadth as a continuation hypothesis. | The rule states when continuation is expected and when it is not. |
| T3.3 | Backtest regime filters. | False-breakout windows are identified before live use. |
| T3.4 | Forward dry-run. | Momentum is promoted only if it passes alone. |

Exit criteria:

- Momentum works in a clearly named regime after costs.
- It passes separately before any combined allocation is considered.

## Stage 4 - Pairs / Statistical Arbitrage Research

Purpose: explore relative-value strategies only after replay discipline exists.

Deliverables:

| ID | Work | Done When |
|---|---|---|
| T4.1 | Build pair-selection research from historical relationships. | Pair choice is based on data, not sector intuition. |
| T4.2 | Test spread stationarity and hedge ratio. | Candidate pair has measurable reversion behavior. |
| T4.3 | Model both legs, costs, slippage, square-off, and short-cover risk. | Backtest is realistic enough for an intraday broker setup. |

Exit criteria:

- Pair strategy clears after-cost backtest and forward paper sample.

## Stage 5 - Seasonality And Calendar Effects

Purpose: test recurring NSE effects without assuming they transfer from other markets.

Deliverables:

| ID | Work | Done When |
|---|---|---|
| T5.1 | Study expiry, opening, lunch, and close windows as data features. | Calendar effects are measured independently. |
| T5.2 | Promote only effects with standalone after-cost edge. | Calendar rule has its own replay and forward sample. |

Exit criteria:

- Calendar edge is positive after costs and not explained by one outlier month.

## Stage 6 - Microstructure / HFT-Style Telemetry

Purpose: use order-book data only when infrastructure supports it.

Deliverables:

| ID | Work | Done When |
|---|---|---|
| T6.1 | Log order-book pressure and top-of-book imbalance for every candidate/entry. | Microstructure features exist in telemetry, not as blind gates. |
| T6.2 | Add tick or quote-stream replay before WebSocket execution. | Streaming data can be tested before it is trusted live. |
| T6.3 | Consider WebSocket execution only after expectancy is positive. | Speed work is justified by a measured edge. |

Exit criteria:

- Microstructure feature improves fill or entry quality out of sample.

## Promotion Gate

Before any strategy moves from dry-run to live, run:

```powershell
.\.venv\Scripts\python.exe scripts\trade\promotion_check.py --window 20
```

Minimum live/dry-run promotion bar:

| Metric | Required |
|---|---:|
| Profit factor | >= 1.15 |
| Expectancy | >= Rs.10/trade |
| Profitable-day rate | >= 55% |
| Trade win rate | >= 40% |
| Max drawdown | <= 3% of average daily capital |
| Minimum sample | >= 30 trades, ideally >= 20 sessions |

If this fails, the strategy does not scale and major risk knobs do not relax.

## Update Protocol

When adding work to this roadmap:

1. Put it under a stage, not a generic pending list.
2. State the hypothesis in plain English.
3. State the exit criteria before implementation.
4. Link the audit, backtest, or forward sample that justifies the work.
5. Add shipped strategy changes to [docs/TRADE_EVOLUTION.md](TRADE_EVOLUTION.md) with newest rows at the top.
