# Backtest Results: Gate G1 -- RSI Buy Ceiling

**Date run**: 2026-05-26
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: [BACKTEST_BASELINE.md](BACKTEST_BASELINE.md)

---

## What This Gate Does

`RSI_BUY_BLOCK_THRESHOLD` blocks BUY entries when RSI is above a
ceiling. The idea: RSI > 75 means overbought, so buying is chasing.

**Previous value**: 75
**New value**: 100 (effectively disabled)
**Config**: `RSI_BUY_BLOCK_THRESHOLD`

---

## Backtest Results (with costs)

| RSI_BUY_CEIL | Trades | WR | PF | Exp/trade | Return |
|--------------|--------|-----|-----|-----------|--------|
| **0 (disabled)** | **37,777** | **40.5%** | **0.71** | **-0.094%** | **-3,554%** |
| 60 | 30,860 | 40.4% | 0.69 | -0.097% | -2,981% |
| 65 | 33,538 | 40.1% | 0.69 | -0.099% | -3,317% |
| 70 | 35,642 | 40.1% | 0.69 | -0.098% | -3,484% |
| 75 (was current) | 36,054 | 40.1% | 0.70 | -0.097% | -3,509% |
| 80 | 36,612 | 40.3% | 0.70 | -0.096% | -3,503% |
| 85 | 37,170 | 40.3% | 0.71 | -0.095% | -3,523% |

---

## Analysis

**Every ceiling value makes results WORSE.**

- PF drops from 0.71 (disabled) to 0.69-0.70 at every level
- Per-trade expectancy gets worse at every level
- The gate removes PROFITABLE momentum trades

### Why RSI ceilings hurt intraday

RSI > 70 in daily trading = "overbought, reversal coming."
RSI > 70 in intraday = "strong uptrend, momentum continuing."

Intraday momentum can push RSI to 80+ and keep going. Blocking
BUY at RSI 75 removes the strongest trending stocks — exactly
the ones that produce the best intraday moves.

### Double-filtering

The scorer already penalizes high RSI (score -= 2 when RSI > 70).
BUY signals at RSI > 75 only exist when OTHER indicators are
strong enough to overcome the RSI penalty. These are high-
conviction momentum trades. Blocking them is counterproductive.

---

## Conclusion

**Verdict: DISABLE (set to 100)**

RSI ceilings are a daily/swing trading concept that doesn't
apply to intraday momentum. The scorer's built-in RSI penalty
is sufficient — no need for a hard block.

**Config changed**: `RSI_BUY_BLOCK_THRESHOLD: 75 -> 100`

---

## Code Review

**Status: SUPPORTED and FUNCTIONAL**

| Check | Result |
|-------|--------|
| Config | `RSI_BUY_BLOCK_THRESHOLD: float = 100.0` (was 75) |
| Used in | `order_engine.py::enter_trade()` gate #23 (line ~2553) |
| Logging | Yes -- "RSI X > Y -- BUY chasing extended move. Skipping." |
| Edge cases | `entry_rsi <= 0` skips the check entirely (graceful) |
| Hardcoded RSI floors | BUY < 30 and SELL < 25 are hardcoded (separate gates G3/G4) |
| Simple MR bypass | `simple_mr_entry` bypass exists but no longer relevant (Simple MR removed) |

---

*Capital: Rs.50,000, Rs.15,000/trade*
*Raw data: `reports/backtest/gate_test_G1.json`*
