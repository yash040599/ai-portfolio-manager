# Backtest Results: Gate H1 -- ADX Minimum

**Date run**: 2026-05-26
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: [BACKTEST_BASELINE.md](BACKTEST_BASELINE.md)

---

## What This Gate Does

`ADX_ENTRY_GATE_ENABLED` + `ADX_MIN_THRESHOLD` blocks entries when
ADX is below a minimum, indicating no clear trend (chop). Has a
score override (`ADX_OVERRIDE_SCORE = 7.0`) — strong signals bypass.

**Current state**: DISABLED (`ADX_ENTRY_GATE_ENABLED = False`)
**Threshold**: 18.0 (would apply if enabled)
**Config**: `ADX_ENTRY_GATE_ENABLED`, `ADX_MIN_THRESHOLD`, `ADX_OVERRIDE_SCORE`

---

## Backtest Results (with costs)

| ADX_MIN | Trades | WR | PF | Exp/trade | Return | Sharpe |
|---------|--------|-----|-----|-----------|--------|--------|
| **0 (disabled)** | **37,777** | **40.5%** | **0.71** | **-0.094%** | **-3,554%** | **-19.26** |
| 12 | 35,336 | 40.3% | 0.71 | -0.094% | -3,308% | -18.58 |
| 15 | 34,355 | 40.2% | 0.71 | -0.092% | -3,172% | -18.07 |
| 18 (config) | 33,236 | 40.2% | 0.71 | -0.092% | -3,041% | -17.61 |
| 20 | 32,484 | 40.2% | 0.71 | -0.091% | -2,971% | -17.40 |
| 22 | 31,625 | 40.2% | 0.71 | -0.091% | -2,874% | -17.02 |
| 25 | 30,310 | 40.2% | 0.71 | -0.091% | -2,748% | -16.61 |

---

## Analysis

**PF unchanged at 0.71 across all levels.**

Positive signals (marginal):
- Per-trade Exp improves slightly: -0.094% → -0.091% (3bp at ADX≥20)
- Sharpe improves 14%: -19.26 → -16.61 at ADX≥25
- MaxDD improves: 3,624% → 2,851% at ADX≥25
- Removed trades have slightly worse expectancy than kept trades

Why not enough:
- PF unchanged — the win/loss profile is identical
- 3bp Exp improvement is within noise
- Removes 20% of trades at ADX≥25 — high filter cost for minimal gain
- All metrics still deeply negative (systemic cost drag)

### Phase 7 re-test candidate

With K1 (daily cap = 2) active, ADX filtering becomes more valuable
because it helps SELECT better trades from the daily candidate pool
rather than just removing bad ones from an unlimited stream. Should
re-test H1 with K1 active in Phase 7.

---

## Conclusion

**Verdict: KEEP DISABLED (for now)**

Standalone effect too weak — PF flat, marginal Exp improvement.
Flag for Phase 7 re-test with K1 active (interaction effect).

**Config**: No change. `ADX_ENTRY_GATE_ENABLED = False`, threshold 18.0.

---

## Code Review

| Check | Result |
|-------|--------|
| Config | `ADX_ENTRY_GATE_ENABLED: bool = False` (unchanged) |
| Threshold | `ADX_MIN_THRESHOLD: float = 18.0` (unchanged) |
| Override | `ADX_OVERRIDE_SCORE: float = 7.0` (high-conviction bypass) |
| Used in | `order_engine.py::enter_trade()` gate #27 (~line 2606) |
| DI check | Also checks +DI/-DI directional alignment (not in backtester) |
| Logging | Yes — detailed ADX/DI/score rejection logs |
| Fails open | Gracefully passes when ADX not available |
| Boost | Strong-gap ADX boost (#194) lowers threshold on gap days |

---

*Capital: Rs.50,000, Rs.15,000/trade*
*Raw data: `reports/backtest/gate_test_H1.json`*
