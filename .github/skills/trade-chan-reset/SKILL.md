---
name: trade-chan-reset
description: "Use when: reviewing intraday trading mode, trade strategy, TRADE_ROADMAP, TRADE_STATISTICS, TRADE_EVOLUTION, Chan audit, Ernest Chan Algorithmic Trading, promotion gate, backtest/replay, NoAI strategy reset, or deciding what trading work to do next."
argument-hint: "trading review, roadmap update, strategy decision, or next-step planning"
---

# Trade Chan Reset

This skill keeps the intraday trading work aligned with the 2026-05-15 Chan-framework reset. Use it whenever the user asks about trading-mode strategy, review findings, next steps, roadmap/statistics/evolution updates, or whether a new trading idea should be built.

## Current Journey State

As of 2026-05-15:

- The bot is not considered a validated profitable strategy.
- Latest promotion check failed: PF 0.839, expectancy Rs.-6.11/trade, day win rate 30.0%.
- FY intraday ledger is net negative after charges, about Rs.-3,928.68.
- Supported posture is paused live trading: no new live trades until the Chan-method staged process allows them.
- Stage 0 runtime status is active: startup logs, reports, and dashboard surfaces show `Stage 0 - Chan Research Reset`, and live order placement is paused by config.
- Historical/live-read evidence collection can continue, but broker-side execution testing is blocked unless the user recharges Zerodha dev APIs.
- Do not scale capital or relax major risk knobs until `scripts/trade/promotion_check.py --window 20` returns PASS on a fresh forward window.
- Do not add live alpha gates just because recent trades lost money. First prove the hypothesis through replay and forward evidence.

## Must-Read Docs

Before making or recommending intraday strategy changes, read these files:

1. `docs/audit/TRADE_AUDIT_2026-05-15_CHAN_FRAMEWORK.md`
2. `docs/TRADE_ROADMAP.md`
3. `docs/TRADE_STATISTICS.md`
4. `docs/TRADE_EVOLUTION.md`
5. `config.py` for current feature flags and `STRATEGY_CONFIG_VERSION`

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
- Stage 1 data model: use the separate private repo `https://github.com/yash040599/ai-portfolio-backtest-data` for normalized replay-ready data. Clone/sync it into local gitignored `backtest_data/` with `scripts/shared/sync_backtest_data.py`.
- Design for the Linux trading VM: use SSH pulls (`python scripts/shared/sync_backtest_data.py --ssh`) so the VM reads the same local dataset version as the dev machine. Do not fetch candles from GitHub at replay/runtime.
- Data contract lives in `docs/TRADE_BACKTEST_DATA.md`. First format is dependency-light: CSV metadata plus SQLite candle stores, not parquet-first.
- Seed/export script: `scripts/trade/export_backtest_data.py` converts local `data/candle_cache.db` into `backtest_data/candles/intraday_15m.sqlite`, `backtest_data/candles/daily.sqlite`, symbol CSVs, and a stamped `manifest.json` without broker/network calls.

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
2. Stage 1 - Full-Fidelity Replay: make live scanner decisions replayable under historical candles and config hashes.
3. Stage 2 - Mean-Reversion V1: first isolated strategy family to test, likely VWAP-band stretch plus exhaustion confirmation.
4. Stage 3 - Momentum/ORB V1: reintroduce continuation only after replay proves where it works.
5. Stage 4 - Pairs/statistical-arbitrage research.
6. Stage 5 - Seasonality/calendar effects.
7. Stage 6 - Microstructure/HFT-style telemetry only after positive expectancy and replay support.

## What To Do First

Stage 0 has shipped and should remain behaviorally narrow:

- Keep the visible `Chan Research Reset` / research-phase label in startup logs, reports, and dashboard/status surfaces.
- Warn loudly if candidate telemetry is unhealthy during reset mode.
- Keep trade entry, exit, sizing, and risk behavior unchanged.
- Keep `STRATEGY_CONFIG_VERSION = "v1.0-2026-05-11"` unless a runtime config/status versioning change is intentionally shipped.
- Treat live trading as paused. Do not place new trades or require Zerodha paid/dev trading APIs unless the user explicitly recharges/enables them for broker-side testing.

After Stage 0, the first real strategy-engineering work is Stage 1: decide the backtest data contract, then build full-fidelity replay. Do not build Mean-Reversion V1 until the replay can evaluate it against the current NoAI baseline after costs.

The `market-research` repo should be used during Stage 1 data discovery, not as a reason to skip Stage 0. The immediate next decision is still to mark the live tool as being in research-reset mode, then wire replay against the best available 10-year data source.

## Required Commands For Evidence Checks

Prefer read-only/local evidence commands while Zerodha dev APIs are expired. If a test needs broker-side order placement or paid trading API access, ask the user before proceeding.

Use the project virtual environment:

```powershell
.\.venv\Scripts\python.exe scripts\trade\promotion_check.py --window 20
.\.venv\Scripts\python.exe scripts\shared\tax_summary.py --intraday
.\.venv\Scripts\python.exe scripts\trade\analyst_pulse.py
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
