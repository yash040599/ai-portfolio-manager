# Backtest Results: Gates L3+L4 -- Trailing SL + Partial Profit

**Date run**: 2026-05-26
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: PF 0.84 (optimized config: ATR 2.0, RR 1.8, K1=2)

---

## What These Gates Do

**L3 — Trailing SL** (`TRAIL_AFTER_RISK_MULTIPLE`, currently 0.0 = disabled):
After profit reaches X× initial risk, ratchet SL up to lock in
gains. `TRAIL_STEP_PCT` controls tightness (50 = midpoint).

**L4 — Partial Profit** (tied to L3 trigger):
At trail trigger, exit 1/3 qty as partial profit. Disabled when
L3 is disabled.

---

## L3 Trigger Sweep (step=50%)

| TRAIL_AFTER | Trades | WR | PF | Exp/trade | Sharpe |
|-------------|--------|-----|-----|-----------|--------|
| **0 (disabled)** | **972** | **42.5%** | **0.84** | **-0.069%** | **-1.40** |
| 0.5x | 972 | 52.4% | 0.78 | -0.084% | -1.88 |
| 0.8x | 972 | 46.9% | 0.84 | -0.067% | -1.42 |
| 1.0x | 972 | 44.4% | 0.83 | -0.074% | -1.56 |
| 1.2x | 972 | 43.2% | 0.84 | -0.071% | -1.46 |
| **1.5x** | **972** | **42.6%** | **0.85** | **-0.068%** | **-1.38** |
| 2.0x | 972 | 42.5% | 0.84 | -0.069% | -1.40 |

## L3 Step Sweep (trigger=1.0x)

| TRAIL_PCT | Trades | WR | PF | Exp/trade | Sharpe |
|-----------|--------|-----|-----|-----------|--------|
| 30% | 972 | 44.3% | 0.83 | -0.072% | -1.50 |
| 40% | 972 | 44.4% | 0.83 | -0.072% | -1.49 |
| 50% | 972 | 44.4% | 0.83 | -0.074% | -1.56 |
| 60% | 972 | 44.4% | 0.83 | -0.075% | -1.57 |
| 70% | 972 | 44.4% | 0.83 | -0.074% | -1.57 |
| 80% | 972 | 44.4% | 0.82 | -0.078% | -1.66 |

---

## Analysis

- 0.5x trigger: WR spikes to 52.4% (many small wins) but PF drops
  to 0.78 — classic over-trailing, chops winners before they reach
  target.
- 1.5x trigger: PF 0.85 (+1 tick), but only 1bp Exp improvement.
  On 972 trades this is noise-level.
- Step sweep at 1.0x: ALL step values make PF worse (0.82-0.83).
  Tighter steps (80%) are worst.
- 2.0x trigger: matches baseline exactly — trail never fires
  (trades hit target or SL before reaching 2x risk profit).

### Why trailing hurts this system

The ATR-based SL+target (ATR×2.0 SL, RR 1.8 target) already
defines a clean risk/reward box. Trailing either:
1. Chops winners early (tight trail), or
2. Never fires (loose trail)

There's no sweet spot where trailing adds value — the fixed exit
framework handles exits better.

---

## Conclusion

**L3 Verdict: KEEP DISABLED (TRAIL_AFTER_RISK_MULTIPLE = 0.0)**
**L4 Verdict: KEEP DISABLED (tied to L3)**

Best case (1.5x/50%) shows +1 PF tick — noise on 972 trades.

---

*Capital: Rs.50,000, Rs.15,000/trade*
*Raw data: `reports/backtest/gate_test_L3.json`, `gate_test_L3_STEP.json`*
