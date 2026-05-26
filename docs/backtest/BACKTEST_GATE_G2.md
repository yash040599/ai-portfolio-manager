# Backtest Results: Gate G2 -- RSI Sell Ceiling

**Date run**: 2026-05-26
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: [BACKTEST_BASELINE.md](BACKTEST_BASELINE.md)

---

## What This Gate Does

`RSI_SELL_BLOCK_THRESHOLD` blocks SELL (short) entries when RSI is
above a ceiling. The idea: RSI > 70 means strong buying pressure,
so shorting is fighting the trend.

**Previous value**: 70
**New value**: 100 (effectively disabled)
**Config**: `RSI_SELL_BLOCK_THRESHOLD`

---

## Backtest Results (with costs)

| RSI_SELL_CEIL | Trades | WR | PF | Exp/trade | Return |
|---------------|--------|-----|-----|-----------|--------|
| **0 (disabled)** | **37,777** | **40.5%** | **0.71** | **-0.094%** | **-3,554%** |
| 60 | 37,365 | 40.4% | 0.71 | -0.094% | -3,518% |
| 65 | 37,437 | 40.4% | 0.71 | -0.094% | -3,522% |
| 70 (was current) | 37,449 | 40.5% | 0.71 | -0.094% | -3,516% |
| 75 | 37,701 | 40.4% | 0.71 | -0.094% | -3,546% |
| 80 | 37,765 | 40.5% | 0.71 | -0.094% | -3,552% |
| 85 | 37,775 | 40.4% | 0.71 | -0.094% | -3,552% |

---

## Analysis

**Gate is completely inert.**

- PF stays at 0.71 at every level — no measurable impact
- At RSI_SELL_CEIL=70 (was current), only 328 trades removed (0.9%)
- Even at the tightest ceiling (60), only 412 trades removed (1.1%)
- Win rate, expectancy, and Sharpe are identical across all levels

### Why so few trades affected

The scorer already avoids shorting into high-RSI conditions. Very
few SELL signals are generated when RSI > 70 because the scoring
model penalizes those setups. The hard gate is redundant.

### Consistency with G1

G1 (RSI Buy Ceiling) was also disabled — it actively hurt by
removing profitable momentum trades. G2 doesn't hurt but also
does nothing. Both RSI ceiling gates are now disabled for the
same reason: the scorer handles RSI filtering better than hard
thresholds.

---

## Conclusion

**Verdict: DISABLE (set to 100)**

Gate removes <1% of trades with zero impact on any metric.
It's pure dead code in practice. Disabling for simplicity.

**Config changed**: `RSI_SELL_BLOCK_THRESHOLD: 70 -> 100`

---

## Code Review

**Status: SUPPORTED and FUNCTIONAL (same code block as G1)**

| Check | Result |
|-------|--------|
| Config | `RSI_SELL_BLOCK_THRESHOLD: float = 100.0` (was 70) |
| Used in | `order_engine.py::enter_trade()` gate #23 (line ~2547) |
| Logging | Yes -- "RSI X > Y -- too overbought to short. Skipping." |
| Edge cases | `entry_rsi <= 0` skips check (graceful) |
| Simple MR bypass | `simple_mr_entry` bypasses this gate (no longer relevant) |

---

*Capital: Rs.50,000, Rs.15,000/trade*
*Raw data: `reports/backtest/gate_test_G2.json`*
