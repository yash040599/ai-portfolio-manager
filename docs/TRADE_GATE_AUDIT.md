# Trade Tool — Complete Strategy & Gate Audit

**Date**: 2026-05-25
**Purpose**: Comprehensive inventory of every decision point in the
trading tool. Each gate is backtested individually and set to
enable/disable with optimal parameter values.

**Approach**: Data-backed decisions only. No gate is enabled without
backtest evidence showing it improves after-cost profitability.

---

## Testing Plan

### Testing Order (by expected P&L impact)

As a quant PM, the order is driven by what moves the needle most,
not by category labels. Our core problem: PF 0.71 after costs,
37,777 trades with 0.013% edge vs 0.07% cost per trade.

**Phase 1: Stop the bleeding (reduce trades + improve per-trade edge)**

These gates directly address the #1 problem — too many trades
eating costs with too thin an edge.

| Order | Gate | Why first | Status |
|-------|------|-----------|--------|
| 1 | K1: Daily Trade Cap | Directly caps trade count. If 5 trades/day is better than 75, this alone could flip PF. | **DONE** -- cap=2 best, PF 0.71->0.81 |
| 2 | E1: ATR SL/Target | Determines every trade's P&L range. Wrong ATR mult = SL too tight (whipsawed) or too wide (big losses). | **DONE** -- ATR=2.0 + RR=2.5 optimal |
| 3 | E3: R:R Floor | Rejects trades where reward doesn't justify risk. Interacts with E1. | **DONE** -- keep 1.3, safety net only |
| 4 | E5: Charge-Aware Target | Rejects trades where expected profit < N x charges. Directly fights the cost problem. | **DONE** -- keep disabled, ineffective with ATR targets |
| 5 | D5: RVOL Floor | Filters low-volume noise trades that have bad fills and worse signals. | **DONE** -- keep 0.7, already optimal |
| 6 | L10: Loser-Exit Late | Cuts losers before EOD instead of bleeding to SL. Quick win on exit side. | **DONE** -- keep 14:45, harmful standalone (churn), re-test with K1 |

**Phase 2: Signal quality filters (individually, then overlapping pairs)**

| Order | Gate | Why | Status |
|-------|------|-----|--------|
| 7 | G1+G2: RSI Ceilings | Block chasing overbought/oversold extremes | Pending |
| 8 | G6+G7: VWAP filters (together) | Both VWAP-based, must test interaction | Pending |
| 9 | H1: ADX min | Block choppy low-conviction entries | Pending |
| 10 | M4: NIFTY Trend | Macro alignment — don't buy in a bearish market | Pending |
| 11 | M2: Tape-Breadth | Market-wide direction filter | Pending |
| 12 | M1 re-test | Re-sweep MIN_SCORE with all Phase 1+2 gates active | Pending |

**Phase 3: Exit optimization (competing philosophies tested head-to-head)**

| Order | Gate | Why | Status |
|-------|------|-----|--------|
| 13 | L3+L4: Trail + Partial (together) | Both modify the same exit path | Pending |
| 14 | L5+L6 vs L7+L8 | Stagnant exits vs signal exits — pick one philosophy | Pending |
| 15 | L11: Square-off time | Is 15:10 optimal or should we close earlier? | Pending |

**Phase 4: Day-level risk management**

| Order | Gate | Why | Status |
|-------|------|-----|--------|
| 16 | C2+C3: Soft stop + Peak DD (together) | Both brake the day — test interaction | Pending |
| 17 | C4: Loss-Streak Guard | Pause after N consecutive SLs | Pending |
| 18 | C1: Circuit Breaker | Hard daily loss cap | Pending |

**Phase 5: Time gates + intraday pauses**

| Order | Gate | Why | Status |
|-------|------|-----|--------|
| 19 | I1: Lunch-Lull | Skip 11:30-12:15 low-conviction window | Pending |
| 20 | I2: Late-Entry Tightening | Raise score bar after 10:00 | Pending |
| 21 | B1: Entry-Burst Cap | Cap rapid-fire entries | Pending |
| 22 | B2: Choppy-Morning | Pause on low-ADX morning | Pending |
| 23 | K2+K3: Observation + Hard Floor | Entry delay optimization | Pending |

**Phase 6: Re-entry + multi-day + remaining**

| Order | Gate | Why | Status |
|-------|------|-----|--------|
| 24 | J1-J3: Re-entry guards | Prevent chasing the same stock | Pending |
| 25 | A2+A3: Multi-day pauses | Pause after bad streaks | Pending |
| 26 | Remaining gates | D1-D4, E2, E4, E6, F1-F5, G3-G5, G8, H2-H3, I3-I4, K4-K6, B3, L1-L2, L9, M3 | Pending |

**Phase 7: Final combined backtest**

| Order | What | Why | Status |
|-------|------|-----|--------|
| 27 | All enabled gates together | Confirm combined config is profitable after costs | Pending |
| 28 | L10 re-test with K1 active | L10 was harmful standalone (churn) but K1 cap blocks re-entry — may become beneficial | Pending |
| 29 | M1 final re-test | Set MIN_SCORE with everything else locked in | Pending |
| 30 | Stress test | Run on worst months, high-VIX periods, crash periods | Pending |
| 31 | N3 re-evaluate | Re-test EMA Pullback with optimized gates to see if edge survives costs | Pending |

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
7. **Code review** (if ENABLED):
   - Verify the gate logic in the actual codebase matches backtest
   - Check edge cases: what happens with missing data, zero values,
     division by zero, None fields
   - Confirm graceful failure: gate must log a warning and skip
     (never crash the trading loop)
   - Confirm logging: every rejection must log the gate name, the
     value that triggered it, and the threshold
   - Confirm config: the gate must read from Config, not hardcode
   - Update config.py comments with the backtest verdict + optimal
     value and reasoning
8. **Set config value**: Update config.py to the optimal value
   determined by backtest

### Cost Model (per round-trip)

All backtests use:
- **Capital**: Rs.50,000
- **Per-trade value**: Rs.15,000 (50K / 3 max positions)
- **Qty**: Rs.15,000 / entry price

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
- [x] Baseline backtester built (1/1) -- [Results](backtest/BACKTEST_BASELINE.md)
- [ ] Gate-by-gate testing (1/55 -- M1 done) -- [M1 Results](backtest/BACKTEST_GATE_M1.md)
- [ ] Final combined backtest with optimal config

### Baseline Results

PF 1.05 raw, **PF 0.71 after costs** (37,777 trades). Raw signal
edge destroyed by costs. [Full analysis](backtest/BACKTEST_BASELINE.md).

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
| D5 | Relative Volume Floor | `RVOL_FLOOR` | 0.7 | ON | **DONE** -- [Results](backtest/BACKTEST_GATE_D5.md) | KEEP 0.7 (optimal). Fixed: was hardcoded, now config-driven. |

### E. Stop-Loss & Risk

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| E1 | ATR SL/Target | `ATR_MULTIPLIER`=2.0, `RR_TARGET_RATIO`=1.8 | ON | ON | **DONE** -- [Results](backtest/BACKTEST_GATE_E1.md) | ATR 1.5->2.0, RR 1.5->1.8. RR=2.5 unrealistic intraday (needs 2.5% move). |
| E2 | ATR Sizing | `ATR_SIZING_ENABLED` | False | OFF | Pending | |
| E3 | R:R Floor | `RR_HARD_FLOOR` | 1.3 | ON | **DONE** -- [Results](backtest/BACKTEST_GATE_E3.md) | KEEP 1.3. Safety net only — never blocks ATR trades (R:R always = RR_TARGET_RATIO). |
| E4 | Min Expected Profit | `MIN_EXPECTED_PROFIT` | 0.0 | OFF | Pending | |
| E5 | Charge-Aware Target | `MIN_PROFIT_CHARGE_MULTIPLE` | 0.0 | OFF | **DONE** -- [Results](backtest/BACKTEST_GATE_E5.md) | KEEP DISABLED. Target profit already 6-10x charges naturally; gate only catches edge cases. |
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
| K1 | Daily Trade Cap | `MAX_TRADES_PER_DAY` | 0 (unlimited) | OFF | **DONE** -- [Results](backtest/BACKTEST_GATE_K1.md) | ENABLE at 2/day (portfolio-level). PF 0.71->0.81. Needs manager.py implementation (currently per-stock only). |
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
| L10 | Loser-Exit Late | `LOSER_EXIT_HOUR` | 14:45 | ON | **DONE** -- [Results](backtest/BACKTEST_GATE_L10.md) | KEEP 14:45. Harmful standalone (churn). Re-test with K1 in Phase 7. |
| L11 | Square-Off | `SQUARE_OFF_HOUR:MINUTE` | 15:10 | ON | Pending | |

### M. Scoring & Selection (Scanner)

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| M1 | MIN_SCORE threshold | `V2_MIN_SCORE` | 2.0 | ON | **DONE** -- [Results](backtest/BACKTEST_GATE_M1.md) | Keep 2.0 now; raise to 5-6 after scorer improvement |
| M2 | Tape-Breadth Penalty | hardcoded | — | ON | Pending | |
| M3 | Sector Diversity Cap | `MAX_PER_SECTOR` | 2 | ON | Pending | |
| M4 | NIFTY Trend Hard Filter | hardcoded | — | ON | Pending | |

### N. New Strategies (from revamp backtest)

| # | Strategy | Config | Backtest Result | Status | Verdict |
|---|----------|--------|----------------|--------|---------|
| N1 | VWAP Mean-Reversion | `STRATEGY_VWAP_MR_ENABLED` | **FAIL** | **DONE** -- [Results](backtest/BACKTEST_VWAP_MR.md) | **DISABLED** — loses money consistently |
| N2 | ORB-15 Breakout | `STRATEGY_ORB15_ENABLED` | **MARGINAL** | **DONE** -- [Results](backtest/BACKTEST_ORB15.md) | **DISABLED** — near break-even, avg loss > avg win |
| N3 | EMA Pullback Momentum | `STRATEGY_EMA_PULLBACK_ENABLED` | **PROMISING** | **DONE** -- [Results](backtest/BACKTEST_EMA_PULLBACK.md) | **DISABLED** pending cost validation + daily trade cap |

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

| Phase | Focus | Gates | Done | Status |
|-------|-------|-------|------|--------|
| 0 | Chan cleanup + new strategies | A1,A4-A6, N1-N3 | 7/7 | **DONE** |
| 0 | Baseline + M1 sweep | Baseline, M1 | 2/2 | **DONE** |
| 1 | Stop the bleeding | K1, E1, E3, E5, D5, L10 | 6/6 | **DONE** |
| 2 | Signal quality | G1-G2, G6-G7, H1, M4, M2, M1 re-test | 0/7 | Pending |
| 3 | Exit optimization | L3-L4, L5-L8, L11 | 0/5 | Pending |
| 4 | Day-level risk | C2-C3, C4, C1 | 0/3 | Pending |
| 5 | Time + pauses | I1, I2, B1, B2, K2-K3 | 0/6 | Pending |
| 6 | Re-entry + multi-day + remaining | J1-J3, A2-A3, + remaining | 0/8 | Pending |
| 7 | Final combined + stress | All together, L10+K1, M1 final, stress, N3 re-eval | 0/5 | Pending |
| **Total** | | | **9/48** | |

**Current**: Phase 1, Order #1 — K1 (re-testing with fixed portfolio cap, no lookahead bias).

---

*This table is updated after each gate is backtested. Each gate gets
a verdict row with: optimal value, expected impact on PF/WR/DD, and
the reasoning for enable/disable.*
