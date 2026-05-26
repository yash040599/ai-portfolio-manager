# Backtest Results: M1 Re-test -- MIN_SCORE with Optimized Config

**Date run**: 2026-05-26
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Previous**: [BACKTEST_GATE_M1.md](BACKTEST_GATE_M1.md) (raw baseline, PF 0.71)

---

## Purpose

Re-sweep MIN_SCORE with all Phase 1+2 optimized settings active:
- ATR_MULTIPLIER = 2.0 (from E1)
- RR_TARGET_RATIO = 1.8 (from E1)
- RR_FLOOR = 1.3 (from E3)
- MAX_TRADES_PER_DAY = 2 (from K1)
- G1, G2, G6, G7 DISABLED
- H1, E5, D5 unchanged (disabled/inert)

---

## Backtest Results (with costs)

| MIN_SCORE | Trades | WR | PF | Exp/trade | Return | Sharpe |
|-----------|--------|-----|-----|-----------|--------|--------|
| **1.5** | **972** | **42.5%** | **0.84** | **-0.069%** | **-66.7%** | **-1.40** |
| **2.0 (current)** | **972** | **42.5%** | **0.84** | **-0.069%** | **-66.7%** | **-1.40** |
| 2.5 | 972 | 42.4% | 0.80 | -0.085% | -82.5% | -1.86 |
| 3.0 | 972 | 42.4% | 0.80 | -0.085% | -82.5% | -1.86 |
| 3.5 | 972 | 41.9% | 0.75 | -0.106% | -102.6% | -2.60 |
| 4.0 | 972 | 41.9% | 0.75 | -0.106% | -102.6% | -2.60 |
| 5.0 | 971 | 40.3% | 0.68 | -0.130% | -126.3% | -3.47 |

---

## Key Findings

### Combined config delivers massive improvement

| Metric | Raw Baseline | Optimized Config | Improvement |
|--------|-------------|-----------------|-------------|
| PF | 0.71 | **0.84** | +18% |
| Trades | 37,777 | 972 | -97.4% |
| WR | 40.5% | 42.5% | +2.0pp |
| Exp/trade | -0.094% | -0.069% | +27% |
| Sharpe | -19.26 | -1.40 | +93% |
| MaxDD | 3,624% | 83.7% | -97.7% |

**K1=2 is the dominant factor** — reducing 37,777 trades to 972
eliminates the cost drag that destroyed the edge.

### MIN_SCORE = 2.0 is confirmed optimal

Higher thresholds make PF WORSE:
- MIN_SCORE 2.5: PF drops to 0.80
- MIN_SCORE 5.0: PF drops to 0.68

Trade count stays at 972 (K1=2 is the binding constraint). But
higher MIN_SCORE changes WHICH trades K1 selects each day — on
high-threshold days some candidates get filtered, shifting slots
to other days with worse setups.

### Still below profitability

PF 0.84 means for every Rs.100 lost, we recover Rs.84. We need
PF > 1.0 to be profitable. The remaining gap is ~16% — exit
optimization (Phase 3) and day-level risk (Phase 4) may close it.

---

## Conclusion

**Verdict: KEEP MIN_SCORE = 2.0**

Confirmed as optimal in the combined config. The improvement from
0.71 → 0.84 PF comes from Phase 1 gates (mainly K1).

**Config**: No change. `V2_MIN_SCORE = 2.0`.

---

*Capital: Rs.50,000, Rs.15,000/trade*
*Baseline updated: backtester now uses ATR 2.0, RR 1.8, RR_FLOOR 1.3, K1=2*
*Raw data: `reports/backtest/gate_test_M1.json`*
