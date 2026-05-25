# Backtest Results: Gate E5 -- Charge-Aware Target

**Date run**: 2026-05-26
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: [BACKTEST_BASELINE.md](BACKTEST_BASELINE.md)

---

## What This Gate Does

`MIN_PROFIT_CHARGE_MULTIPLE` rejects trades where the gross target
profit doesn't clear N times the round-trip charges. If a trade's
expected profit on target hit is Rs.50 and charges are Rs.20, with
multiple=3.0 it needs Rs.60 to pass (3x Rs.20).

**Current value**: 0 (disabled)
**Config**: `MIN_PROFIT_CHARGE_MULTIPLE`

---

## How It Works in the Code

In `order_engine.py::enter_trade()` (gate #13, line ~2905):

```python
gross_profit = abs(target - entry) * qty
charges = Config.calculate_charges(buy_val, sell_val, 2)
if gross_profit < charges * multiple:
    REJECT
```

Uses the exact `Config.calculate_charges()` with real STT, GST,
exchange, SEBI, stamp duty rates. Correctly handles BUY vs SELL
turnover.

---

## Backtest Results (with costs)

| CHARGE_MULT | Trades | WR | PF | Exp/trade | Return |
|-------------|--------|-----|-----|-----------|--------|
| 0 (disabled) | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% |
| 1.0x | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% |
| 1.5x | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% |
| 2.0x | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% |
| 2.5x | 37,774 | 40.5% | 0.71 | -0.094% | -3,553% |
| 3.0x | 37,757 | 40.5% | 0.71 | -0.094% | -3,552% |
| 4.0x | 37,471 | 40.6% | 0.71 | -0.094% | -3,517% |
| 5.0x | 36,241 | 40.9% | 0.72 | -0.093% | -3,387% |

---

## Analysis

**This gate is nearly ineffective for our setup.**

With ATR-based SL/target on NIFTY 50 stocks:
- Typical gross target profit: Rs.100-200 per trade
- Round-trip charges: Rs.15-25 per trade
- Natural ratio: 6-10x

Even at multiple=5.0, only 1,536 trades (4%) are rejected. The
remaining 36,241 easily clear the threshold. PF barely moves
(0.71 -> 0.72).

### Why it doesn't help

The gate answers: "Does the TARGET profit cover charges?"
But the right question is: "Does the EXPECTED profit cover charges?"

Expected profit = (target_dist × WR) - (sl_dist × (1 - WR))

With WR=40%, a trade with Rs.157 target profit and Rs.105 SL loss:
- Expected = 157 × 0.40 - 105 × 0.60 = 62.8 - 63.0 = **-Rs.0.20**

The expected profit is near-zero BEFORE charges. Adding Rs.18
charges makes it -Rs.18.20 per trade. The charge-aware gate can't
fix this because the target profit looks fine — it's the WIN RATE
that's the problem.

### When this gate WOULD help

- If we use Claude AI mode which can set bad SL/target ratios
- If stocks have very small price (Rs.20-50) where ATR produces
  tiny Rs. targets that don't cover charges
- Combined with higher MIN_SCORE (fewer trades, better WR)

---

## Conclusion

**Verdict: KEEP DISABLED (0) for now**

The gate is well-implemented in the code but ineffective with our
current ATR-based SL/target setup. The natural target profit is
already 6-10x charges, so the gate never triggers meaningfully.

It may become useful IF:
1. We add Claude AI mode back (non-standard SL/target)
2. We expand to sub-Rs.100 stocks where targets are small
3. We implement expected-profit-based filtering (not just target)

**No config change.**

---

## Code Review

**Status: SUPPORTED and FUNCTIONAL**

| Check | Result |
|-------|--------|
| Config | `MIN_PROFIT_CHARGE_MULTIPLE: float = 0.0` (config.py) |
| Used in | `order_engine.py::enter_trade()` gate #13 (line ~2905) |
| Charges | Uses `Config.calculate_charges()` — exact NSE rates |
| Logging | Yes — logs rejection with gross profit, charges, multiple |
| Edge cases | `multiple <= 0` disables gate; `qty <= 0` or `entry <= 0` skipped |
| Kill-switch | `multiple = 0` = disabled (current) |

**No config change. No code change.**

---

*Capital: Rs.50,000, Rs.15,000/trade*
*Raw data: `reports/backtest/gate_test_E5.json`*
