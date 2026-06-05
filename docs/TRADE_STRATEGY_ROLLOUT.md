# Trade Strategy Rollout

> **⚠️ STALE (2026-06-06):** This doc shows "Live trading: ENABLED" but the
> system verdict is **DO NOT GO LIVE** (OOS PF 0.82, negative expectancy).
> See [TRADE_ROADMAP.md](TRADE_ROADMAP.md) for current posture and
> [TRADE_NEXT_IDEAS.md](TRADE_NEXT_IDEAS.md) for next research steps.
> This file is kept because config.py and manager.py reference it.

> Updated 2026-05-25. Chan framework removed. Replaced with
> backtest-driven gate optimization approach.
>
> **New approach**: Each gate/strategy is backtested individually
> against 2 years of 15-min candle data. Gates are enabled/disabled
> based on whether they improve after-cost profitability.
> See [TRADE_GATE_AUDIT.md](TRADE_GATE_AUDIT.md) for the full
> gate-by-gate backtest results.

## Current State

- **Live trading**: ENABLED
- **Mode**: AI (Gemini 2.5 Flash, detailed plan) — selects 2 trades/day from NIFTY50
- **Strategy profile**: `NOAI_LEGACY_FULL` (blended score feeds AI pre-filter)
- **Stage name**: `BACKTEST_OPTIMIZED` (gates set by backtest evidence)
- **Config version**: `v2.0-2026-05-26-BACKTEST_OPTIMIZED`

## New Strategy Candidates (Backtested 2026-05-25)

| Strategy | Result | Win Rate | CAGR | Sharpe | Details |
|----------|--------|----------|------|--------|---------|
| VWAP Mean-Reversion | **FAIL** | 23.1% | -39.1% | -2.95 | [Report](backtest/BACKTEST_VWAP_MR.md) |
| ORB-15 Breakout | **MARGINAL** | 55.7% | -1.4% | -0.15 | [Report](backtest/BACKTEST_ORB15.md) |
| EMA Pullback Momentum | **PROMISING** | 42.8% | +151.5% | 4.03 | [Report](backtest/BACKTEST_EMA_PULLBACK.md) |

## Gate Optimization

Each of the 55 existing gates is being backtested to determine
optimal values. See [TRADE_GATE_AUDIT.md](TRADE_GATE_AUDIT.md)
for the complete table with enable/disable decisions.

## Cross-Cutting Gates (Always On — Safety)

| Setting | Why |
|---------|-----|
| `MAX_BUDGET_INR` | Capital cap per day |
| `MAX_POSITIONS` | Simultaneous position limit |
| `MIN_BALANCE_TO_TRADE` | Refuse to trade below charge hurdle |
| `USE_EXCHANGE_SL` | Exchange-level SL-M for instant execution |
| `MAX_SPREAD_PCT`, `MAX_IMPACT_COST_PCT` | Reject untradable books |
| `RR_HARD_FLOOR` | Minimum R:R sanity |
| `SQUARE_OFF_HOUR:MINUTE` | Intraday positions must close |
| NSE holiday list + expiry detection | Calendar correctness |
