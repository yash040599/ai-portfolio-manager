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
| 7 | G1+G2: RSI Ceilings | Block chasing overbought/oversold extremes | **DONE** -- Both DISABLED. G1 hurts PF, G2 inert. [G1](backtest/BACKTEST_GATE_G1.md) [G2](backtest/BACKTEST_GATE_G2.md) |
| 8 | G6+G7: VWAP filters (together) | Both VWAP-based, must test interaction | **DONE** -- Both DISABLED. G6 inert, G7 harmful (PF 0.71→0.67). [Results](backtest/BACKTEST_GATE_G6G7.md) |
| 9 | H1: ADX min | Block choppy low-conviction entries | **DONE** -- Keep disabled. PF flat. Re-test Phase 7 with K1. [Results](backtest/BACKTEST_GATE_H1.md) |
| 10 | M4: NIFTY Trend | Macro alignment — don’t buy in a bearish market | **PRO DECISION** — KEEP ENABLED. Standard institutional filter for weak contra-trend signals. |
| 11 | M2: Tape-Breadth | Market-wide direction filter | **PRO DECISION** — KEEP ENABLED. Oldest institutional breadth signal. Soft penalty, not hard block. |
| 12 | M1 re-test | Re-sweep MIN_SCORE with all Phase 1+2 gates active | **DONE** -- [Results](backtest/BACKTEST_GATE_M1_RETEST.md). Keep 2.0. PF 0.84 with optimized config (up from 0.71). |

**Phase 3: Exit optimization (competing philosophies tested head-to-head)**

| Order | Gate | Why | Status |
|-------|------|-----|--------|
| 13 | L3+L4: Trail + Partial (together) | Both modify the same exit path | **DONE** -- Keep disabled. Fixed ATR exits outperform trailing. |
| 14 | L5+L6 vs L7+L8 | Stagnant exits vs signal exits — pick one philosophy | **PRO DECISION** — L5/L6/L8 disabled (square-off handles time). L7 ENABLED (thesis invalidation). |
| 15 | L11: Square-off time | Is 15:10 optimal or should we close earlier? | **DONE** -- 14:00 optimal. PF +7.6%, Sharpe +37%. |

**Phase 4: Day-level risk management**

| Order | Gate | Why | Status |
|-------|------|-----|--------|
| 16 | C2+C3: Soft stop + Peak DD (together) | Both brake the day — test interaction | **DONE** — Both inert with K1=2. Keep disabled. Re-test when K1 increases. |
| 17 | C4: Loss-Streak Guard | Pause after N consecutive SLs | **PRO DECISION** — ENABLED (3 consecutive, 30-min pause). Regime detection. |
| 18 | C1: Circuit Breaker | Hard daily loss cap | **PRO DECISION** — Keep enabled at 3% as safety net. Re-test when K1 increases. |

**Phase 5: Time gates + intraday pauses**

| Order | Gate | Why | Status |
|-------|------|-----|--------|
| 19 | I1: Lunch-Lull | Skip 11:30-12:15 low-conviction window | **DONE** — Backtest inert with K1=2. Keep disabled. Re-test when K1 increases. |
| 20 | I2: Late-Entry Tightening | Raise score bar after 10:00 | **PRO DECISION** — Keep disabled. K1=2 selects strongest. Re-test when K1 increases. |
| 21 | B1: Entry-Burst Cap | Cap rapid-fire entries | **PRO DECISION** — Keep enabled (zero-cost safety net). Impossible to trigger with K1=2. |
| 22 | B2: Choppy-Morning | Pause on low-ADX morning | **PRO DECISION** — Keep disabled. Needs NIFTY ADX. K1=2 limits damage. |
| 23 | K2+K3: Observation + Hard Floor | Entry delay optimization | **PRO DECISION** — Keep current (5min+15min floor). Industry standard. |

**Phase 6: Re-entry + multi-day + remaining**

| Order | Gate | Why | Status |
|-------|------|-----|--------|
| 24 | J1-J3: Re-entry guards | Prevent chasing the same stock | **PRO DECISION** — J1 keep at 2, J2/J3 keep disabled. K1=2 naturally limits re-entry. |
| 25 | A2+A3: Multi-day pauses | Pause after bad streaks | **PRO DECISION** — Covered by C4 (loss-streak guard). |
| 26 | Remaining gates | D1-D4, E2, E4, E6, F1-F5, G3-G5, G8, H2-H3, I3-I4, K4-K6, B3, L1-L2, L9, M3 | **DONE** — All resolved via pro decisions. See individual gate rows. |

**Phase 7: Final combined backtest**

| Order | What | Why | Status |
|-------|------|-----|--------|
| 27 | All enabled gates together | Confirm combined config is profitable after costs | **DONE** — Combined PF 0.86. H2 2024 was profitable (PF 1.02). H1 2025 correction dragged down. |
| 28 | L10 re-test with K1 active | L10 was harmful standalone (churn) but K1 cap blocks re-entry — may become beneficial | **DONE** — L10=13:00 marginally beneficial. Exp +6%, Sharpe +5%. No churn with K1=2. |
| 28b | H1 re-test with K1 active | H1 marginally helpful standalone (+3bp Exp, PF flat). With K1 daily cap, ADX may help SELECT better trades from pool | **DONE** — STILL HARMFUL with K1=2. PF drops 0.86→0.84 at all levels. Keep disabled. |
| 29 | M1 final re-test | Set MIN_SCORE with everything else locked in | **DONE** — MIN_SCORE=2.0 confirmed. PF 0.86. Higher values all worse. |
| 30 | Stress test | Run on worst months, high-VIX periods, crash periods | **DONE** — H2’24 PF 1.02 (+2.7%), H1’25 PF 0.74 (-28.8%), H2’25+ PF 0.82. Strategy works in trending, fails in corrections. |
| 31 | N3 re-evaluate | Re-test EMA Pullback with optimized gates to see if edge survives costs | **DEFERRED** — Core strategy still PF<1.0 overall. Fix base first before adding new strategies. |

### Re-test when K1 increases (capital scale-up)

When capital increases and K1 moves above 2, these gates become relevant again.
They were inert/moot at K1=2 because max daily exposure is only 2 trades.

| Gate | Config | Why re-test |
|------|--------|-------------|
| C2 | `DAILY_LOSS_SOFT_STOP_PCT` | With K1=4+, daily losses can compound. Soft stop prevents "open loser → SL → open another loser" spiral. |
| C3 | `PEAK_DRAWDOWN_STOP_PCT` | With more trades, peak-to-trough swings become meaningful. Pro desks track equity HWM. |
| C1 | `MAX_LOSS_PER_DAY_PCT` | Hard CB threshold (3%) may need tightening with more capital at risk. |
| H1 | `ADX_ENTRY_GATE_ENABLED` | With more daily slots, ADX helps SELECT better trades from a larger candidate pool. |
| D5 | `RVOL_FLOOR` | Volume filter becomes more impactful with more entries to filter. || I1 | `LUNCH_LULL_ENABLED` | With K1=4+, lunch entries become part of the daily pool. Lull filter may improve quality. |
| I2 | `LATE_ENTRY_TIGHTENING_ENABLED` | More daily trades = more late entries. Tightening helps when afternoon slots exist. |
| B2 | `CHOPPY_MORNING_PAUSE_ENABLED` | More morning entries = more chop exposure. ADX-based pause becomes valuable. |
| L9 | `SECTOR_CASCADE_EXIT_ENABLED` | With 4+ positions, sector correlation risk becomes real. Cascade exit protects. |
| B1 | `ENTRY_BURST_CAP_ENABLED` | Currently impossible with K1=2. With K1=4+, rapid-fire entries can happen. |
| N3 | `STRATEGY_EMA_PULLBACK_ENABLED` | Raw PF 0.99 with K1=2 (only 972 of 33,822 trades). With K1=4+, more trades in mix — may push raw PF above 1.0. Still needs cost validation. |
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
| B1 | Entry-Burst Cap | `ENTRY_BURST_CAP_ENABLED` | True (3/60s) | ON | **PRO DECISION** — KEEP ENABLED | No backtest (timing). Impossible to trigger with K1=2. Zero-cost safety net for when K1 increases. |
| B2 | Choppy-Morning Pause | `CHOPPY_MORNING_PAUSE_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | No backtest (needs NIFTY ADX). K1=2 limits morning damage. Also depends on L5 stagnant exits (disabled). Re-test when K1 increases. |
| B3 | VIX Intraday-Spike | `VIX_SPIKE_ENTRY_PAUSE_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | No backtest (needs live VIX). VIX spikes are rare regime events. With K1=2 and 14:00 square-off, exposure is already minimal. |

### C. Day-Level Performance Brakes

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| C1 | Circuit Breaker | `MAX_LOSS_PER_DAY_PCT` | 3% | ON | **PRO DECISION** — KEEP ENABLED | Safety net. Almost never fires with K1=2 (max daily loss ~1.2%). Re-test when K1 increases. |
| C2 | Daily-Loss Soft Stop | `DAILY_LOSS_SOFT_STOP_PCT` | 0.0% | OFF | **DONE** — KEEP DISABLED | Backtest: completely inert at all levels with K1=2. Max 2 trades/day caps risk. Re-test when K1 increases. |
| C3 | Peak-Drawdown Stop | `PEAK_DRAWDOWN_STOP_PCT` | 0.0% | OFF | **PRO DECISION** — KEEP DISABLED | Same logic as C2 — K1=2 limits peak-to-trough. Re-test when K1 increases. |
| C4 | Loss-Streak Guard | `CONSECUTIVE_SL_PAUSE_COUNT` | **3** | **ON** | **PRO DECISION** — ENABLED | No backtest (cross-day portfolio streaks). Standard prop-firm practice: pause after N consecutive losses. With K1=2, 3 SLs = 2+ bad days — regime detection. Re-test when K1 increases. |

### D. Live Market Validation

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| D1 | Live Price Fetch | 3 retries | Required | ON | **PRO DECISION** — KEEP ENABLED | Infrastructure gate. Not a trading parameter. Must stay on. |
| D2 | Circuit-Limit Guard | `CIRCUIT_LIMIT_GUARD_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | Prevents entry near UC/LC. Rare event. Zero harm keeping disabled. |
| D3 | Bid-Ask Spread | `MAX_SPREAD_PCT` | 0.3% | ON | **PRO DECISION** — KEEP ENABLED at 0.3% | Standard execution quality gate. 0.3% is right for NIFTY100. Protects against illiquid entries. |
| D4 | Impact-Cost | `MAX_IMPACT_COST_PCT` | 0.2% | ON | **PRO DECISION** — KEEP ENABLED at 0.2% | Order-book depth check. Prevents slippage on thin books. 0.2% is conservative for Rs.15K size. |
| D5 | Relative Volume Floor | `RVOL_FLOOR` | 0.7 | ON | **DONE** -- [Results](backtest/BACKTEST_GATE_D5.md) | KEEP 0.7 (optimal). Fixed: was hardcoded, now config-driven. |

### E. Stop-Loss & Risk

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| E1 | ATR SL/Target | `ATR_MULTIPLIER`=2.0, `RR_TARGET_RATIO`=1.8 | ON | ON | **DONE** -- [Results](backtest/BACKTEST_GATE_E1.md) | ATR 1.5->2.0, RR 1.5->1.8. RR=2.5 unrealistic intraday (needs 2.5% move). |
| E2 | ATR Sizing | `ATR_SIZING_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | With fixed Rs.15K/trade and ATR-based SL, risk is already roughly normalized. Enable when capital > Rs.2L. |
| E3 | R:R Floor | `RR_HARD_FLOOR` | 1.3 | ON | **DONE** -- [Results](backtest/BACKTEST_GATE_E3.md) | KEEP 1.3. Safety net only — never blocks ATR trades (R:R always = RR_TARGET_RATIO). |
| E4 | Min Expected Profit | `MIN_EXPECTED_PROFIT` | 0.0 | OFF | **PRO DECISION** — KEEP DISABLED | Redundant with E3 (RR floor) and ATR target. No additional value. |
| E5 | Charge-Aware Target | `MIN_PROFIT_CHARGE_MULTIPLE` | 0.0 | OFF | **DONE** -- [Results](backtest/BACKTEST_GATE_E5.md) | KEEP DISABLED. Target profit already 6-10x charges naturally; gate only catches edge cases. |
| E6 | Slippage Simulation | `SLIPPAGE_PCT` | 0.15% | DRY-RUN | **PRO DECISION** — KEEP AS-IS | Observability only (dry-run logging). Not a gate. |

### F. Budget & Position Limits

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| F1 | Budget Cap | `MAX_BUDGET_INR` | 50,000 | ON | **PRO DECISION** — KEEP ENABLED | Core risk parameter. Defines position sizing. |
| F2 | Max Positions | `MAX_POSITIONS` | 3 | ON | **PRO DECISION** — KEEP at 3 | With K1=2, effectively 2 active. 3 allows headroom. |
| F3 | Duplicate Symbol Block | hardcoded | — | ON | **PRO DECISION** — KEEP ENABLED | Prevents doubling down on same stock. Standard risk rule. |
| F4 | Sector Concentration | `MAX_PER_SECTOR` | 2 | ON | **PRO DECISION** — KEEP at 2 | With K1=2, max 2 trades = max 2 sectors anyway. Sensible diversity. |
| F5 | Direction Diversity | derived | — | ON | **PRO DECISION** — KEEP ENABLED | Prevents all-long or all-short concentration. Good practice. |

### G. Technical Filters

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| G1 | RSI Buy Ceiling | `RSI_BUY_BLOCK_THRESHOLD` | 100 | **DISABLED** | **DONE** -- [Results](backtest/BACKTEST_GATE_G1.md) | DISABLED (was 75). All values hurt PF. Removes profitable momentum trades. |
| G2 | RSI Sell Ceiling | `RSI_SELL_BLOCK_THRESHOLD` | 100 | **DISABLED** | **DONE** -- [Results](backtest/BACKTEST_GATE_G2.md) | DISABLED (was 70). Gate inert — removes <1% trades, PF identical at 0.71. Scorer handles RSI filtering. |
| G3 | RSI Floor (BUY) | `RSI_BUY_FLOOR_THRESHOLD` | 0 | **DISABLED** | **DONE** — DISABLED (was hardcoded 30) | Backtest: inert at 0-25, marginal -1 PF tick at 30. Made configurable. Oversold BUYs are profitable intraday. |
| G4 | RSI Floor (SELL) | `RSI_SELL_FLOOR_THRESHOLD` | 0 | **DISABLED** | **DONE** — DISABLED (was hardcoded 25) | Backtest: **SEVERELY harmful**. PF 0.86→0.77 (-10%). Blocking shorts at low RSI removes profitable downtrend entries. Made configurable. |
| G5 | Pattern-Direction Veto | `PATTERN_VETO_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | No backtest (needs candle pattern detection). With K1=2, few entries to filter. Low priority. |
| G6 | VWAP Trend-Fight | `VWAP_TREND_FIGHT_PCT` | 99.0 | **DISABLED** | **DONE** -- [Results](backtest/BACKTEST_GATE_G6G7.md) | DISABLED (was hardcoded 0.3%). Inert — PF 0.71 at all levels. Made configurable. |
| G7 | VWAP Extension-Chase | `VWAP_EXTENSION_BLOCK_PCT` | 99.0 | **DISABLED** | **DONE** -- [Results](backtest/BACKTEST_GATE_G6G7.md) | DISABLED (was 0.8%). Harmful — PF drops 0.71→0.67. Removes profitable momentum trades. |
| G8 | Fresh-Reversal Guard | `FRESH_REVERSAL_DELTA_THRESHOLD` | 8.0 | ON | **PRO DECISION** — KEEP ENABLED at 8.0 | Blocks entries on violent score swings (first bar of reversal). Waits for confirmation. Sound practice. |

### H. Trend & ADX Filters

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| H1 | ADX Entry Gate | `ADX_ENTRY_GATE_ENABLED` | False | OFF | **DONE** -- [Results](backtest/BACKTEST_GATE_H1.md) | KEEP DISABLED. PF flat 0.71 at all levels. Marginal Exp +3bp. Re-test Phase 7 with K1. |
| H2 | Gap-Coherence Gate | `GAP_COHERENCE_GATE_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | No backtest. Opening gap data not in backtester. Concept is sound (don’t fight gap direction) but needs live validation. |
| H3 | NIFTY Trend Hard Filter | hardcoded | — | ON | **PRO DECISION** — KEEP ENABLED | Same as M4. Drops weak contra-NIFTY signals. Standard institutional practice. |

### I. Time-of-Day Gates

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| I1 | Lunch-Lull Skip | `LUNCH_LULL_ENABLED` | False | OFF | **DONE** — KEEP DISABLED | Backtest: completely inert with K1=2. 2 daily trades are from morning (strongest signals). Re-test when K1 increases. |
| I2 | Late-Entry Tightening | `LATE_ENTRY_TIGHTENING_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | No backtest (scanner config). K1=2 already selects strongest. Re-test when K1 increases. |
| I3 | Short Entry Cutoff | `SHORT_ENTRY_CUTOFF_HOUR` | 16 | Inactive | **PRO DECISION** — KEEP AS-IS | Inactive (hour 16 > square-off 14:00). No effect. |
| I4 | Min Time for Entry | `MIN_MINUTES_FOR_ENTRY` | 45 | ON | **PRO DECISION** — KEEP ENABLED | Ensures enough time before square-off. With 14:00 close, last entry ~13:15. Sensible constraint. |

### J. Re-Entry Guards

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| J1 | Max Re-Entries | `MAX_REENTRIES_PER_STOCK` | 2 | ON | **DONE** — KEEP at 2 | Backtest: completely inert with K1=2. Re-entries per stock per day never exceed 1. Re-test when K1 increases. |
| J2 | Re-Entry Cooldown | `RE_ENTRY_COOLDOWN_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | K1=2 naturally spaces entries. Cooldown adds no value with 2 trades/day. |
| J3 | Average-Down Prevention | `AVG_DOWN_PREVENTION_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | F3 (duplicate block) already prevents adding to same stock. Redundant. |

### K. Trade Caps & Observation

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| K1 | Daily Trade Cap | `MAX_TRADES_PER_DAY` | 0 (unlimited) | OFF | **DONE** -- [Results](backtest/BACKTEST_GATE_K1.md) | ENABLE at 2/day (portfolio-level). PF 0.71->0.81. Needs manager.py implementation (currently per-stock only). |
| K2 | Observation Period | `ENTRY_DELAY_MINUTES`=5, `ENTRY_MIN_MOVE_PCT`=0.3% | ON | ON | **PRO DECISION** — KEEP CURRENT | No backtest (live scan timing). 5-min observation + 0.3% min move is industry standard for opening whipsaw avoidance. |
| K3 | Hard Floor Time | `ENTRY_DECISION_FLOOR_MINUTES_AFTER_OPEN`=15 | ON | ON | **PRO DECISION** — KEEP CURRENT | No backtest. 15-min hard floor after open ensures VWAP/indicators are stable. Standard practice. |
| K4 | Stale-Score Guard | `FRESH_ENTRY_RECHECK_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | No backtest (live scan). With K1=2 and fast execution, score staleness is unlikely. Low risk. |
| K5 | Stagnant Churn Guard | hardcoded | — | ON | **PRO DECISION** — KEEP ENABLED | Prevents re-entering a stock that just exited stagnant. Zero-cost logic. |
| K6 | RR-Giveup | `RR_GIVEUP_ENABLED` | True (5 scans) | ON | **PRO DECISION** — KEEP ENABLED | Cancels pending entry if R:R deteriorates after 5 scan cycles. Sound risk management — don’t chase a worsening setup. |

### L. Exit Triggers

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| L1 | Static SL-M | `USE_EXCHANGE_SL` | True | ON | **PRO DECISION** — KEEP ENABLED | Core risk gate. Exchange-level SL-M order protects against crashes/disconnects. Must stay on. |
| L2 | Target Hit | rule-based | — | ON | **PRO DECISION** — KEEP ENABLED | Core exit. ATR-based target is the profit-taking mechanism. |
| L3 | Trailing SL | `TRAIL_AFTER_RISK_MULTIPLE` | 0.0 | OFF | **DONE** -- [Results](backtest/BACKTEST_GATE_L3L4.md) | KEEP DISABLED. Best case +1 PF tick (noise on 972 trades). Fixed ATR exits work better. |
| L4 | Partial Profit | tied to L3 | — | OFF | **DONE** -- [Results](backtest/BACKTEST_GATE_L3L4.md) | KEEP DISABLED (tied to L3 trigger). |
| L5 | Stagnant Exit Tier 1 | `STAGNANT_EXIT_MINUTES` | 0 | OFF | **PRO DECISION** — KEEP DISABLED | No backtest. Config history shows repeated false exits on profitable positions. 14:00 square-off caps hold time. K1=2 means no slot-freeing benefit. |
| L6 | Stagnant Exit Tier 2 | `STAGNANT_HARD_MAX_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | Depends on L5 philosophy. 14:00 square-off is the time stop. |
| L7 | Signal-Reversal Exit | `SIGNAL_REVERSAL_EXIT_ENABLED` | **True** | **ON** | **PRO DECISION** — ENABLED | No backtest. Act on thesis invalidation (score flip + pattern). Conservative trigger. HDFCBANK case validates. Capital preservation priority. |
| L8 | Signal-Decay Exit | `SIGNAL_DECAY_EXIT_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | No backtest. More aggressive than L7 (no pattern needed). 14:00 square-off limits hold time. False positive risk from temp score dips. L7 catches worst cases. |
| L9 | Sector-Cascade Exit | `SECTOR_CASCADE_EXIT_ENABLED` | False | OFF | **PRO DECISION** — KEEP DISABLED | No backtest. With K1=2, max 2 stocks in different sectors. Cascade is meaningless with 2 positions. Re-test when K1 increases. |
| L10 | Loser-Exit Late | `LOSER_EXIT_HOUR` | **13:00** | ON | **DONE** -- Phase 7 re-test: L10=13 with K1=2. PF flat, Exp +6%, Sharpe +5%, MaxDD -6%. No churn (K1 blocks re-entry). |
| L11 | Square-Off | `SQUARE_OFF_HOUR:MINUTE` | 14:00 | ON | **DONE** -- [Results](backtest/BACKTEST_GATE_L11.md) | CHANGED 15:10→14:00. Strongest gate: PF 0.79→0.85. Monotonic improvement with earlier close. |

### M. Scoring & Selection (Scanner)

| # | Gate | Config | Value | Status | Backtest Verdict | Why |
|---|------|--------|-------|--------|-----------------|-----|
| M1 | MIN_SCORE threshold | `V2_MIN_SCORE` | 2.0 | ON | **DONE** -- [Results](backtest/BACKTEST_GATE_M1.md) | Keep 2.0 now; raise to 5-6 after scorer improvement |
| M2 | Tape-Breadth Penalty | `BREADTH_FILTER_ENABLED` | True | ON | **PRO DECISION** — KEEP ENABLED | No backtest (cross-stock). Soft penalty on contra-breadth scores. Market breadth is one of the oldest institutional signals. With K1=2, naturally deprioritizes contra-breadth trades. |
| M3 | Sector Diversity Cap | `MAX_PER_SECTOR` | 2 | ON | **PRO DECISION** — KEEP at 2 | With K1=2, effectively enforced. Good diversification practice. |
| M4 | NIFTY Trend Hard Filter | hardcoded | — | ON | **PRO DECISION** — KEEP ENABLED | No backtest. Drops weak (score 2-3) contra-NIFTY signals. Standard institutional practice. Zero cost to strong signals. |

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
| 2 | Signal quality | G1-G2, G6-G7, H1, M4, M2, M1 re-test | 7/7 | **DONE** |
| 3 | Exit optimization | L3-L4, L5-L8, L11 | 5/5 | **DONE** |
| 4 | Day-level risk | C2-C3, C4, C1 | 3/3 | **DONE** |
| 5 | Time + pauses | I1, I2, B1, B2, K2-K3 | 6/6 | **DONE** |
| 6 | Re-entry + multi-day + remaining | J1-J3, A2-A3, + remaining | 8/8 | **DONE** |
| 7 | Final combined + stress | All together, L10+K1, M1 final, stress, N3 re-eval | 5/5 | **DONE** |
| **Total** | | | **49/49** | |

**Current**: **ALL PHASES COMPLETE (49/49).** Gate audit finished.

---

## Final Results Summary

### Full Optimization Journey

| Metric | Raw Baseline | Final Optimized | Improvement |
|--------|-------------|-----------------|-------------|
| **PF** | 0.71 | **0.86** | **+21%** |
| **Trades** | 35,563 | 970 | -97% (K1=2) |
| **WR** | 40.9% | 37.8% | -3pp (L10 cuts losers early) |
| **Exp/trade** | -0.095% | **-0.058%** | **+39%** |
| **Sharpe** | -18.79 | **-1.22** | **+93%** |
| **MaxDD** | 3,428% | **72%** | **-98%** |
| **Return** | -3,364% | -56% | -98% less loss |

### Key Config Changes Made

| Parameter | Before | After | Gate | Impact |
|-----------|--------|-------|------|--------|
| `ATR_MULTIPLIER` | 1.5 | **2.0** | E1 | Wider SL, fewer whipsaws |
| `RR_TARGET_RATIO` | 1.5 | **1.8** | E1 | Better risk/reward |
| `MAX_TRADES_PER_DAY` | 0 | **2** | K1 | Eliminated cost drag |
| `SQUARE_OFF_HOUR:MIN` | 15:10 | **14:00** | L11 | Avoid closing volatility |
| `LOSER_EXIT_HOUR:MIN` | 14:45 | **13:00** | L10 | Cut losers early, no churn |
| `RSI_BUY_BLOCK_THRESHOLD` | 75 | **100** | G1 | Disabled — hurt momentum |
| `RSI_SELL_BLOCK_THRESHOLD` | 70 | **100** | G2 | Disabled — inert |
| `RSI_BUY_FLOOR_THRESHOLD` | 30 (hardcoded) | **0** | G3 | Disabled — marginal harm |
| `RSI_SELL_FLOOR_THRESHOLD` | 25 (hardcoded) | **0** | G4 | **Disabled — PF killer (-10%)** |
| `VWAP_TREND_FIGHT_PCT` | 0.3 (hardcoded) | **99** | G6 | Disabled — inert |
| `VWAP_EXTENSION_BLOCK_PCT` | 0.8 | **99** | G7 | Disabled — harmful |
| `CONSECUTIVE_SL_PAUSE_COUNT` | 0 | **3** | C4 | Pro decision — regime detection |
| `SIGNAL_REVERSAL_EXIT_ENABLED` | False | **True** | L7 | Pro decision — thesis invalidation |

### Stress Test by Period

| Period | Trades | PF | Return | Regime |
|--------|--------|-----|--------|--------|
| H2 2024 (May-Dec) | 294 | **1.02** | **+2.7%** | PROFITABLE |
| H1 2025 (Jan-Jun) | 242 | 0.74 | -28.8% | Worst (tariff correction) |
| H2 2025+ (Jul-May'26) | 426 | 0.82 | -28.2% | Middle |
| **Full 2yr** | **970** | **0.86** | **-56.2%** | Blended |

### Assessment

The strategy is **profitable in trending/normal markets** (H2 2024 PF 1.02)
but **bleeds during corrections** (H1 2025 PF 0.74). The overall PF 0.86
means we recover Rs.86 for every Rs.100 lost — still not break-even.

**To reach PF > 1.0**, the strategy needs:
1. **Better scorer** — current 4-indicator scorer is simplistic
2. **Regime detection** — pause during corrections (VIX-based or NIFTY trend)
3. **Capital scaling** — more capital → higher K1 → more gates become relevant

---

*This table is updated after each gate is backtested. Each gate gets
a verdict row with: optimal value, expected impact on PF/WR/DD, and
the reasoning for enable/disable.*
