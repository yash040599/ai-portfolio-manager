# Backtest Results: Gate K1 -- Daily Trade Cap

**Date run**: 2026-05-25 (re-tested — first run had per-stock cap
which was ineffective, and second run had lookahead bias)
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: [BACKTEST_BASELINE.md](BACKTEST_BASELINE.md)

---

## What This Gate Does

Limits total number of trades across ALL stocks per day (portfolio
level). Takes the first N signals chronologically as they fire
during the trading window. This is realistic — in live trading you
take signals as they arrive, you don't wait to see all of them.

**Current value**: 0 (disabled / unlimited)
**Config**: `MAX_TRADES_PER_DAY`
**Note**: The existing config is per-stock. A portfolio-level cap
would need implementation in the manager (not scanner).

---

## Test History

| Run | Approach | Issue |
|-----|----------|-------|
| Run 1 | Per-stock cap | Ineffective — cap=1 still gave 23K trades across 50 stocks |
| Run 2 | Portfolio cap, sorted by abs(PnL) | **Lookahead bias** — sorted by realized outcome |
| **Run 3** | Portfolio cap, first N by entry time | **Correct** — no future knowledge, realistic |

---

## Backtest Results — Run 3 (with NSE intraday costs, no bias)

| Cap/Day | Trades | Win Rate | PF | Exp/trade | Return | Max DD |
|---------|--------|----------|-----|-----------|--------|--------|
| OFF (baseline) | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% | 3,624% |
| **2** | **972** | **42.3%** | **0.81** | **-0.077%** | **-75%** | **87%** |
| 3 | 1,458 | 41.0% | 0.74 | -0.102% | -149% | 159% |
| 4 | 1,944 | 41.0% | 0.73 | -0.103% | -201% | 212% |
| 5 | 2,430 | 41.2% | 0.74 | -0.099% | -240% | 253% |
| 6 | 2,916 | 41.0% | 0.72 | -0.108% | -316% | 330% |
| 7 | 3,402 | 40.7% | 0.71 | -0.111% | -379% | 395% |
| 8 | 3,888 | 40.3% | 0.70 | -0.117% | -453% | 472% |
| 10 | 4,860 | 40.8% | 0.71 | -0.114% | -552% | 569% |
| 15 | 7,290 | 40.8% | 0.71 | -0.111% | -809% | 830% |
| 20 | 9,720 | 40.7% | 0.71 | -0.109% | -1,061% | 1,089% |

---

## Analysis

### Cap=2 is the clear winner

- **PF improvement**: 0.71 -> 0.81 (+14%)
- **WR improvement**: 40.5% -> 42.3%
- **Damage reduction**: -3,554% -> -75% (98% less total loss)
- **Trade count**: 37,777 -> 972 (97% fewer trades)
- **Per-trade loss**: -0.094% -> -0.077% (18% less per-trade drag)

### Why cap=2 works best

The first 2 signals of the day benefit from:
1. The observation period (10:00 entry means 45 min of price
   discovery since open)
2. Stronger momentum — early signals catch the morning trend
3. Less crowding — later signals are often in the same sector/
   direction as earlier ones (correlated)

### Why it's still not profitable

PF 0.81 means the edge is still negative after costs. The cap
reduces the number of losing trades but doesn't change the
fundamental signal quality. We need other gates (E1 ATR tuning,
E5 charge-aware target, signal quality filters) to push per-trade
expectancy into positive territory.

### Diminishing returns

Cap 7+ is identical to baseline — most stocks only fire 1 signal/
day, so a cap above 7 doesn't filter anything.

---

## Conclusion

**Verdict: ENABLE at cap=2 (portfolio-level)**

This is a "damage reduction" gate, not an "alpha" gate. It improves
PF from 0.71 to 0.81 by eliminating the ~97% of trades that are
low-quality noise. Combined with other gates in Phase 1-2, this
should contribute to pushing PF above 1.0.

**Implementation note**: The current `MAX_TRADES_PER_DAY` config is
per-stock in the scanner. A portfolio-level cap requires changes in
`modes/trade/manager.py` to track total entries per day and stop
entering after the cap is reached. This is a code TODO.

---

## Code Review

**Status: SUPPORTED and FUNCTIONAL — portfolio-level cap already exists**

| Check | Result |
|-------|--------|
| Config parameter | `MAX_TRADES_PER_DAY: int = 0` in config.py |
| Used in | `order_engine.py::enter_trade()` line ~2690 |
| Scope | **PORTFOLIO-LEVEL** — counts `len(self.positions)` = all open+closed across ALL stocks |
| Regime-aware | Yes — `effective_trade_cap()` adjusts by budget regime |
| Logging | Yes — "daily trade cap reached (N/N)" when hit |
| Expiry override | Yes — `EXPIRY_MAX_TRADES_PER_DAY` can tighten on expiry days |
| Edge cases | 0 = disabled (unlimited); external/adopted positions counted too |

**Important correction**: The initial backtest (Run 1) tested
per-stock caps in the backtester, which was wrong. The real code
already implements a PORTFOLIO-level cap. Run 3 tested this
correctly. The code is already correct — just needs the config
value set to 2.

**Action**: Set `MAX_TRADES_PER_DAY = 2` in config.py.

**Capital context**: All backtests use Rs.50,000 capital with
Rs.15,000 per trade (50K / 3 max positions).

---

*Raw data: `reports/backtest/gate_test_K1.json`*
