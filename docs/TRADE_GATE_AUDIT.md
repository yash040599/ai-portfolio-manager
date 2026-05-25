# Trade Tool — Complete Strategy & Gate Audit

**Date**: 2026-05-25
**Purpose**: Comprehensive inventory of every decision point in the
trading tool. Each gate is backtested individually and set to
enable/disable with optimal parameter values.

**Approach**: Data-backed decisions only. No gate is enabled without
backtest evidence showing it improves after-cost profitability.

---

## Testing Plan

### Dependency Order

Gates are tested in signal-chain order — each layer depends on
the prior layer being settled first:

| Layer | Category | Gates | Why this order |
|-------|----------|-------|----------------|
| 1 | **Signal Generation** | M1-M4, N1-N3 | Defines what trade ideas exist | **M1 done** |
| 2 | **Entry Filters** | G1-G8, H1-H3, D2-D5 | Filters bad ideas before entry |
| 3 | **Position Sizing** | E1-E5, F1-F5 | Determines risk per trade |
| 4 | **Exit Logic** | L1-L11 | Determines P&L per trade |
| 5 | **Day-Level Risk** | C1-C4, K1, K6 | Controls daily loss exposure |
| 6 | **Time Gates** | B1-B3, I1-I4, K2-K3 | Time-of-day filters |
| 7 | **Multi-Day Pauses** | A2-A3, J1-J3 | Cross-session decisions |

### Overlap Combinations to Test

Some gates interact — testing them individually isn't enough:

| Combination | Why test together |
|-------------|-------------------|
| G6 + G7 | Both VWAP-based entry filters |
| L3 + L4 | Trailing SL and partial profit modify the same exit |
| L5+L6 vs L7+L8 | Stagnant exits vs signal exits — competing philosophies |
| C2 + C3 | Soft-stop and peak-DD both brake the day |
| E1 + E3 | ATR SL/target interacts with R:R floor |
| N3 + existing scanner | EMA Pullback adds signals on top of legacy blended score |

### Per-Gate Test Protocol

For each gate:
1. **Baseline**: Run backtester with gate OFF
2. **Gate ON**: Run with gate enabled at current default value
3. **Parameter sweep**: Test 3-5 values if gate is tunable
4. **After-cost check**: Apply regulatory charges (STT, brokerage, GST)
5. **Verdict**: ENABLE (with optimal value) or DISABLE (with reason)
6. **Update this doc**: Fill in the Backtest Verdict column

### Cost Model (per round-trip)

All backtest P&L is computed after these NSE intraday charges:

| Charge | Rate |
|--------|------|
| Brokerage (Zerodha) | Rs.20 or 0.03%, whichever lower |
| STT (sell side) | 0.025% |
| Exchange txn | 0.00345% |
| GST | 18% on brokerage + exchange |
| SEBI | 0.0001% |
| Stamp duty | 0.003% |
| **Approximate total** | **~0.05-0.10% per round-trip** |

---

## Progress

- [x] Chan framework removed (A1, A4, A5, A6)
- [x] 3 new strategies backtested and added as disabled config flags
- [ ] Baseline backtester built (1/1)
- [ ] Gate-by-gate testing (1/55 — M1 done)
- [ ] Final combined backtest with optimal config

### Baseline Results (2026-05-25)

| Metric | Raw (no costs) | With NSE intraday costs |
|--------|---------------|------------------------|
| Trades (2 years) | 37,777 | 37,777 |
| Win Rate | 46.8% | 40.5% |
| Profit Factor | **1.05** | **0.71** |
| Expectancy | +0.013% | -0.094% |
| Total Return | +493.87% | -3,553.58% |

**Key insight**: Raw signal has a tiny edge (PF 1.05) that is destroyed
by ~0.07% per-trade costs at 37,777 trades. The path to profitability
requires: (1) fewer, higher-conviction trades, (2) cost-aware gates,
(3) better scoring that produces wider per-trade edge.

---

## Master Gate Table

Status legend:
- **ON** = currently active in config
- **OFF** = disabled in config
- **DONE** = backtested, verdict decided
- **REMOVED** = removed from codebase

### A. Session-Level Pauses

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| A1 | Live Trading Kill Switch | `TRADE_LIVE_TRADING_PAUSED` | False | **DONE** | **REMOVED** | Was Chan restriction. Now False (unpaused). |
| A2 | Rolling-PF Pause | `ROLLING_PF_PAUSE_ENABLED` | False | OFF | Pending | |
| A3 | Directional Auto-Pause | `DIRECTIONAL_PAUSE_ENABLED` | False | OFF | Pending | |
| A4 | Research Stage Gate | — | — | **DONE** | **REMOVED** | Chan restriction deleted from config |
| A5 | Strategy Profile Lock | `TRADE_STRATEGY_PROFILE` | NOAI_LEGACY_FULL | **DONE** | **UNLOCKED** | Was locked to Simple MR. Now full blended. |
| A6 | Stage Name | `TRADE_STAGE_NAME` | BACKTEST_OPTIMIZED | **DONE** | **RELABELED** | Audit-trail only, no longer gates behavior |

### B. Intraday Pauses

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| B1 | Entry-Burst Cap | `ENTRY_BURST_CAP_ENABLED` | True (3/60s) | ON | Pending | |
| B2 | Choppy-Morning Pause | `CHOPPY_MORNING_PAUSE_ENABLED` | False | OFF | Pending | |
| B3 | VIX Intraday-Spike | `VIX_SPIKE_ENTRY_PAUSE_ENABLED` | False | OFF | Pending | |

### C. Day-Level Performance Brakes

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| C1 | Circuit Breaker | `CIRCUIT_BREAKER_ENABLED` | True (3%) | ON | Pending | |
| C2 | Daily-Loss Soft Stop | `DAILY_LOSS_SOFT_STOP_PCT` | 0.0% | OFF | Pending | |
| C3 | Peak-Drawdown Stop | `PEAK_DRAWDOWN_STOP_PCT` | 0.0% | OFF | Pending | |
| C4 | Loss-Streak Guard | `SL_PAUSE_COUNT` | 3 | ON | Pending | |

### D. Live Market Validation

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| D1 | Live Price Fetch | 3 retries | Required | ON | Pending | |
| D2 | Circuit-Limit Guard | `CIRCUIT_LIMIT_GUARD_ENABLED` | False | OFF | Pending | |
| D3 | Bid-Ask Spread | `MAX_SPREAD_PCT` | 0.3% | ON | Pending | |
| D4 | Impact-Cost | `MAX_IMPACT_COST_PCT` | 0.2% | ON | Pending | |
| D5 | Relative Volume Floor | `RVOL_FLOOR` | 0.7 | ON | Pending | |

### E. Stop-Loss & Risk

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| E1 | ATR SL/Target | `ATR_MULTIPLIER`=1.5, `RR_TARGET_RATIO`=1.5 | ON | ON | Pending | |
| E2 | ATR Sizing | `ATR_SIZING_ENABLED` | False | OFF | Pending | |
| E3 | R:R Floor | `RR_HARD_FLOOR` | 1.3 | ON | Pending | |
| E4 | Min Expected Profit | `MIN_EXPECTED_PROFIT` | 0.0 | OFF | Pending | |
| E5 | Charge-Aware Target | `MIN_PROFIT_CHARGE_MULTIPLE` | 0.0 | OFF | Pending | |
| E6 | Slippage Simulation | `SLIPPAGE_PCT` | 0.15% | DRY-RUN | Pending | |

### F. Budget & Position Limits

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| F1 | Budget Cap | `MAX_BUDGET_INR` | 50,000 | ON | Pending | |
| F2 | Max Positions | `MAX_POSITIONS` | 3 | ON | Pending | |
| F3 | Duplicate Symbol Block | hardcoded | — | ON | Pending | |
| F4 | Sector Concentration | `MAX_PER_SECTOR` | 2 | ON | Pending | |
| F5 | Direction Diversity | derived | — | ON | Pending | |

### G. Technical Filters

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| G1 | RSI Buy Ceiling | `RSI_BUY_BLOCK_THRESHOLD` | 75 | ON | Pending | |
| G2 | RSI Sell Ceiling | `RSI_SELL_BLOCK_THRESHOLD` | 70 | ON | Pending | |
| G3 | RSI Floor (BUY) | hardcoded 30 | — | ON | Pending | |
| G4 | RSI Floor (SELL) | hardcoded 25 | — | ON | Pending | |
| G5 | Pattern-Direction Veto | `PATTERN_VETO_ENABLED` | False | OFF | Pending | |
| G6 | VWAP Trend-Fight | hardcoded 0.3% | — | ON | Pending | |
| G7 | VWAP Extension-Chase | `VWAP_EXTENSION_BLOCK_PCT` | 0.8% | ON | Pending | |
| G8 | Fresh-Reversal Guard | `FRESH_REVERSAL_DELTA_THRESHOLD` | 8.0 | ON | Pending | |

### H. Trend & ADX Filters

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| H1 | ADX Entry Gate | `ADX_ENTRY_GATE_ENABLED` | False | OFF | Pending | |
| H2 | Gap-Coherence Gate | `GAP_COHERENCE_GATE_ENABLED` | False | OFF | Pending | |
| H3 | NIFTY Trend Hard Filter | hardcoded | — | ON | Pending | |

### I. Time-of-Day Gates

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| I1 | Lunch-Lull Skip | `LUNCH_LULL_ENABLED` | False | OFF | Pending | |
| I2 | Late-Entry Tightening | `LATE_ENTRY_TIGHTENING_ENABLED` | False | OFF | Pending | |
| I3 | Short Entry Cutoff | `SHORT_ENTRY_CUTOFF_HOUR` | 16 | Inactive | Pending | |
| I4 | Min Time for Entry | `MIN_MINUTES_FOR_ENTRY` | 45 | ON | Pending | |

### J. Re-Entry Guards

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| J1 | Max Re-Entries | `MAX_REENTRIES_PER_STOCK` | 2 | ON | Pending | |
| J2 | Re-Entry Cooldown | `RE_ENTRY_COOLDOWN_ENABLED` | False | OFF | Pending | |
| J3 | Average-Down Prevention | `AVG_DOWN_PREVENTION_ENABLED` | False | OFF | Pending | |

### K. Trade Caps & Observation

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| K1 | Daily Trade Cap | `MAX_TRADES_PER_DAY` | 0 (unlimited) | OFF | Pending | |
| K2 | Observation Period | `ENTRY_DELAY_MINUTES`=5, `ENTRY_MIN_MOVE_PCT`=0.3% | ON | ON | Pending | |
| K3 | Hard Floor Time | `ENTRY_DECISION_FLOOR_MINUTES_AFTER_OPEN`=15 | ON | ON | Pending | |
| K4 | Stale-Score Guard | `FRESH_ENTRY_RECHECK_ENABLED` | False | OFF | Pending | |
| K5 | Stagnant Churn Guard | hardcoded | — | ON | Pending | |
| K6 | RR-Giveup | `RR_GIVEUP_ENABLED` | True (5 scans) | ON | Pending | |

### L. Exit Triggers

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| L1 | Static SL-M | `USE_EXCHANGE_SL` | True | ON | Pending | |
| L2 | Target Hit | rule-based | — | ON | Pending | |
| L3 | Trailing SL | `TRAILING_SL_ENABLED` | True | ON | Pending | |
| L4 | Partial Profit | `PARTIAL_PROFIT_ENABLED` | True | ON | Pending | |
| L5 | Stagnant Exit Tier 1 | `STAGNANT_EXIT_MINUTES` | 0 | OFF | Pending | |
| L6 | Stagnant Exit Tier 2 | `STAGNANT_HARD_MAX_ENABLED` | False | OFF | Pending | |
| L7 | Signal-Reversal Exit | `SIGNAL_REVERSAL_EXIT_ENABLED` | False | OFF | Pending | |
| L8 | Signal-Decay Exit | `SIGNAL_DECAY_EXIT_ENABLED` | False | OFF | Pending | |
| L9 | Sector-Cascade Exit | `SECTOR_CASCADE_EXIT_ENABLED` | False | OFF | Pending | |
| L10 | Loser-Exit Late | `LOSER_EXIT_HOUR` | 14:45 | ON | Pending | |
| L11 | Square-Off | `SQUARE_OFF_HOUR:MINUTE` | 15:10 | ON | Pending | |

### M. Scoring & Selection (Scanner)

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| M1 | MIN_SCORE threshold | `V2_MIN_SCORE` | 2.0 | ON | **DONE** | Sweep 2-7: PF 0.70-0.80 after costs (all negative). Higher scores (6.0+) improve quality (WR 43% vs 40%) but don't create profitable edge with simplified scorer. Need real scanner scoring for accurate test. Keep at 2.0 for now. |
| M2 | Tape-Breadth Penalty | hardcoded | — | ON | Pending | |
| M3 | Sector Diversity Cap | `MAX_PER_SECTOR` | 2 | ON | Pending | |
| M4 | NIFTY Trend Hard Filter | hardcoded | — | ON | Pending | |

### N. New Strategies (from revamp backtest)

| # | Strategy | Config | Backtest Result | Status | Verdict |
|---|----------|--------|----------------|--------|---------|
| N1 | VWAP Mean-Reversion | `STRATEGY_VWAP_MR_ENABLED` | **FAIL** (-62.6%, WR 23%, PF 0.80) | **DONE** | **DISABLED** — loses money consistently |
| N2 | ORB-15 Breakout | `STRATEGY_ORB15_ENABLED` | **MARGINAL** (-2.8%, WR 55.7%, PF 0.97) | **DONE** | **DISABLED** — near break-even, avg loss > avg win |
| N3 | EMA Pullback Momentum | `STRATEGY_EMA_PULLBACK_ENABLED` | **PROMISING** (+522%, WR 42.8%, PF 1.07) | **DONE** | **DISABLED** pending cost validation + daily trade cap |

All three are in config.py as disabled flags. N1 and N2 will NOT be
enabled — their code implementation is permanently skipped. N3 may be
enabled after Layer 4 (exit logic) optimization confirms the thin
edge survives after costs — code implementation is in TODO pending
that verdict.

**Note**: Only config flags exist today. The actual scanning/entry
code for N1-N3 is NOT implemented yet. Code will only be written
for strategies that pass the full backtest audit. If a strategy
stays disabled, no code is written (avoids dead code).

---

## Backtest Audit Progress

| Layer | Gates | Completed | Status |
|-------|-------|-----------|--------|
| Chan cleanup | A1, A4, A5, A6 | 4/4 | **DONE** |
| New strategies | N1, N2, N3 | 3/3 | **DONE** |
| 1. Signal Generation | M1-M4 | 0/4 | **NEXT** |
| 2. Entry Filters | G1-G8, H1-H3, D2-D5 | 0/15 | Pending |
| 3. Position Sizing | E1-E5, F1-F5 | 0/10 | Pending |
| 4. Exit Logic | L1-L11 | 0/11 | Pending |
| 5. Day-Level Risk | C1-C4, K1, K6 | 0/6 | Pending |
| 6. Time Gates | B1-B3, I1-I4, K2-K3 | 0/9 | Pending |
| 7. Multi-Day Pauses | A2-A3, J1-J3 | 0/5 | Pending |
| **Total** | | **7/62** | |

**Next step**: Build baseline backtester, then start Layer 1 (Signal
Generation) with gate M1 (MIN_SCORE threshold sweep).

---

*This table is updated after each gate is backtested. Each gate gets
a verdict row with: optimal value, expected impact on PF/WR/DD, and
the reasoning for enable/disable.*
