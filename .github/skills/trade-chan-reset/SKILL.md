---
name: trade-chan-reset
description: "Use when: reviewing intraday trading mode, trade strategy, TRADE_ROADMAP, TRADE_STATISTICS, TRADE_EVOLUTION, Chan audit, Ernest Chan Algorithmic Trading, promotion gate, backtest/replay, NoAI strategy reset, or deciding what trading work to do next."
argument-hint: "trading review, roadmap update, strategy decision, or next-step planning"
---

# Trade Chan Reset

This skill keeps the intraday trading work aligned with the 2026-05-15 Chan-framework reset. Use it whenever the user asks about trading-mode strategy, review findings, next steps, roadmap/statistics/evolution updates, or whether a new trading idea should be built.

## Current Journey State

As of 2026-05-19:

- The bot is not considered a validated profitable strategy.
- Live trading is paused (`TRADE_LIVE_TRADING_PAUSED = True`); broker-side execution testing is blocked unless the user recharges Zerodha dev APIs.
- Active stage on the rollout ladder: **`S0_PURE_MR`** — Simple MR alpha only.
  Every other gate (entry vetoes, mid-life exits, sizing modifiers, multi-day performance pauses,
  breadth/sector overlays, time-of-day filters, charge-aware R:R) is OFF at config level.
  The complete ladder, per-stage parameter sweeps, and promotion bars live in
  `docs/TRADE_STRATEGY_ROLLOUT.md`.
- `Config.TRADE_STAGE_NAME` is the single source of truth for which rung is active. It is stamped
  into every trading report (`config.trade_stage_name`), dry-run ledger row (notes column),
  and Chan evidence JSON/MD header so cohorts stay clean.
- Active strategy version: `v1.3-2026-05-19-S0`. Active NoAI profile: `NOAI_SIMPLE_MR_BASELINE`.
- Earlier dry-run sessions (including 2026-05-18 and 2026-05-19) were generated with non-MR gates
  active and were purged. The L0 sample restarts at zero from the next dry-run.
- Latest live promotion check failed: PF 0.839, expectancy Rs.-6.11/trade, day win rate 30.0%.
  FY intraday ledger is about Rs.-3,928.68 net after charges.
- Stage 1 plumbing (T1.0-T1.7) remains shipped: data contract, scanner-clock replay,
  config-hash candidate ledger, after-cost replay, live-vs-replay reports, dry-run DB separation,
  daily evidence files, simple MR profile isolation.
- Do not move to the next rung until the current rung passes its sample bar (default: 10 sessions
  and 30 closed simulated trades) AND a parameter sweep has been run on the variables listed in
  that rung of the rollout doc.
- Do not scale capital or relax major risk knobs until `scripts/trade/promotion_check.py --window 20`
  returns PASS on a fresh forward window.
- Do not add live alpha gates just because recent trades lost money. Prove the hypothesis through
  replay and forward evidence on the current rung first.

## Must-Read Docs

Before making or recommending intraday strategy changes, read these files:

1. `docs/TRADE_STRATEGY_ROLLOUT.md`  (active stage ladder, per-gate inventory, parameter sweeps, promotion bars)
2. `docs/audit/TRADE_AUDIT_2026-05-15_CHAN_FRAMEWORK.md`
3. `docs/TRADE_ROADMAP.md`
4. `docs/TRADE_STATISTICS.md`
5. `docs/TRADE_EVOLUTION.md`
6. `config.py` for current `TRADE_STAGE_NAME`, `STRATEGY_CONFIG_VERSION`, and per-gate flags

For implementation work, also inspect the relevant code path:

- `modes/trade/manager.py`
- `modes/trade/stock_scanner.py`
- `modes/trade/order_engine.py`
- `modes/trade/candidate_telemetry.py`
- `scripts/trade/backtest.py`
- `scripts/trade/promotion_check.py`

External research/data context:

- The user has a separate market-research repository: `https://github.com/yash040599/market-research`.
- It has now been inspected: it is a standalone daily ATH-dip research sandbox using `yfinance`, a hardcoded current NIFTY 50 list, and result matrices. Treat it as reference/seed material, not as the intraday replay runtime source.
- Keep this repo as the trade tool. Do not bloat it with large raw datasets unless the user explicitly approves that storage decision.
- Stage 1 data model: use the separate private repo `https://github.com/yash040599/ai-portfolio-backtest-data` for normalized replay-ready data. Clone/sync it beside the main repo at `../ai-portfolio-backtest-data` with `scripts/shared/sync_backtest_data.py`.
- Design for the Linux trading VM: use SSH pulls (`python scripts/shared/sync_backtest_data.py --ssh`) so the VM reads the same local dataset version as the dev machine. Do not fetch candles from GitHub at replay/runtime.
- Data contract lives in `docs/TRADE_BACKTEST_DATA.md`. First format is dependency-light: CSV metadata plus SQLite candle stores, not parquet-first.
- Seed/export script: `scripts/trade/export_backtest_data.py` converts local `data/candle_cache.db` into `../ai-portfolio-backtest-data/candles/intraday_15m.sqlite`, `../ai-portfolio-backtest-data/candles/daily.sqlite`, symbol CSVs, and a stamped `manifest.json` without broker/network calls.
- Backtest bridge: `scripts/trade/backtest.py` now reads `../ai-portfolio-backtest-data/candles/intraday_15m.sqlite` when present and only falls back to `data/candle_cache.db` if the Stage 1 data repo is absent. Use `--score-mode scanner` for replay-safe scanner-style scoring and `--score-mode simple` for the legacy comparison path. Replay JSON includes a config-hash-stamped `candidates` ledger for accepted/rejected decisions and after-cost metrics based on explicit trade-value/slippage/spread assumptions. Use `scripts/trade/live_vs_replay.py --data-source live` for actual live/tax evidence and `--data-source dryrun` for separated dry-run analysis evidence from `data/trade_analysis.db`. Use `scripts/trade/chan_daily_evidence.py --data-source dryrun --date <YYYY-MM-DD>` after forward dry-runs if the automatic report hook needs to be rerun.
- Matching copilot runbook: `copilot/trade-chan-reset.md` follows the repo's existing flat copilot skill-file convention and is synced with the operational data repo.

## Chan-Framework Decision Rule

Every strategy decision must follow this order:

1. State the trading rationale in plain English.
2. Identify the strategy family: mean reversion, momentum/ORB, pairs/stat-arb, seasonality, or microstructure.
3. Make the implementation replayable.
4. Include transaction costs, spread, slippage, and square-off behavior.
5. Separate in-sample, out-of-sample, and forward/live evidence.
6. Require promotion metrics before scaling or relaxing risk.
7. Update the trade docs with measured results, not hopeful theory.

Do not treat a clever gate as alpha. Risk controls reduce damage; they do not prove the entry edge.

## Current Stage Order

Follow the staged roadmap unless the user explicitly changes direction:

1. Stage 0 - Research Reset: add visible runtime/report/dashboard status such as `Chan Research Reset`, plus telemetry-health warning. No entry behavior change.
2. Stage 1 - Full-Fidelity Replay and Strategy Isolation: make live scanner decisions replayable under historical candles/config hashes, then run the first isolated dry-run profile.
3. Stage 2 - Mean-Reversion V1: promote only after `NOAI_SIMPLE_MR_BASELINE` has enough dry-run/replay evidence; add one confirmation layer at a time.
4. Stage 3 - Momentum/ORB V1: reintroduce continuation only after replay proves where it works.
5. Stage 4 - Pairs/statistical-arbitrage research.
6. Stage 5 - Seasonality/calendar effects.
7. Stage 6 - Microstructure/HFT-style telemetry only after positive expectancy and replay support.

## What To Do Next

Continue Stage 1.7, not Stage 2 promotion:

- Keep live trading paused. Do not place new trades or require Zerodha paid/dev trading APIs unless the user explicitly recharges/enables them for broker-side testing.
- Current strategy config is `STRATEGY_CONFIG_VERSION = "v1.2-2026-05-18"` with `TRADE_STRATEGY_PROFILE = "NOAI_SIMPLE_MR_BASELINE"`.
- Before editing data code, check both repos: `git status --short --branch` and `git -C ../ai-portfolio-backtest-data status --short --branch`.
- Run the data-safety smoke tests from `copilot/trade-chan-reset.md` when data scripts change.
- Next work is to run NoAI dry-run forward sessions using `NOAI_SIMPLE_MR_BASELINE`, then compare those dry-run analysis rows against replay under the same config hash. Keep actual dashboard/tax P&L sourced only from live, verified `intraday_tax_ledger` rows.
- Sample ladder: L0 baseline sanity needs at least 5 dry-run sessions and 30 simulated closed trades, or continue to 10 sessions if sparse; L1 pattern confirmation needs 20 additional sessions or 30 new trades plus replay; any strategy replay promotion needs at least 60 historical sessions after costs; live pilot needs at least 10 sessions and 20 closed trades before scale consideration.

Do not promote Mean-Reversion V1 until `NOAI_SIMPLE_MR_BASELINE` has telemetry-bearing dry-run/replay comparison evidence after costs. The first strategy rollout should be staged by family, not the full complicated blended system.

## Required Commands For Evidence Checks

Prefer read-only/local evidence commands while Zerodha dev APIs are expired. If a test needs broker-side order placement or paid trading API access, ask the user before proceeding.

Use the project virtual environment:

```powershell
.\.venv\Scripts\python.exe scripts\trade\promotion_check.py --window 20
.\.venv\Scripts\python.exe scripts\shared\tax_summary.py --intraday
.\.venv\Scripts\python.exe scripts\trade\analyst_pulse.py
.\.venv\Scripts\python.exe scripts\trade\fill_dryrun_analysis.py
.\.venv\Scripts\python.exe scripts\trade\chan_daily_evidence.py --data-source dryrun --date <YYYY-MM-DD>
.\.venv\Scripts\python.exe scripts\trade\live_vs_replay.py --data-source dryrun --replay <replay-json>
```

When exit logic changes, also run:

```powershell
.\.venv\Scripts\python.exe scripts\trade\exit_coverage_check.py
```

## Documentation Update Rules

Keep the trade docs synchronized as work ships:

- `docs/TRADE_ROADMAP.md`: staged plan only. Do not recreate a giant pending/completed archive.
- `docs/TRADE_STATISTICS.md`: current measured posture, promotion metrics, disabled features, and strategy-level metrics by config hash.
- `docs/TRADE_EVOLUTION.md`: newest-first history of shipped strategy/risk/execution/evidence changes. No bug fixes, no docs-only rows.
- `docs/audit/`: dated audits for major reviews and resets.

When a strategy experiment ships, record:

- strategy id;
- config version/hash;
- backtest/replay result;
- forward/dry-run result;
- live result if promoted;
- whether promotion gate passed or failed.

## Guardrails

- Do not use pirated book text. Use high-level principles and legitimate public references.
- Do not claim the bot is profitable until live/forward evidence proves it after costs.
- Do not mix strategy families and then pretend the result proves one family worked.
- Do not scale capital, re-enable score-weighted sizing, or relax risk because of a theory-only argument.
- When a subagent or review claims a high-severity trading issue, verify the exact code and data before changing anything.
