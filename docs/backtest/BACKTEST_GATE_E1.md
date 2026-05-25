# Backtest Results: Gate E1 -- ATR SL/Target

**Date run**: 2026-05-25
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: [BACKTEST_BASELINE.md](BACKTEST_BASELINE.md)

---

## What This Gate Does

Two parameters control every trade's stop-loss and target:

- `ATR_MULTIPLIER`: SL distance = ATR × multiplier. Higher = wider
  SL, fewer whipsaws but bigger losses when hit.
- `RR_TARGET_RATIO`: Target distance = SL distance × ratio. Higher
  = further target, fewer wins but bigger wins.

**Current values**: ATR_MULTIPLIER = 1.5, RR_TARGET_RATIO = 1.5
**Config**: `ATR_MULTIPLIER`, `RR_TARGET_RATIO`, `ATR_PERIOD` (14)

---

## Backtest Results — ATR Multiplier Sweep (with costs, RR fixed at 1.5)

| ATR_MULT | Trades | WR | PF | Exp/trade | Return |
|----------|--------|-----|-----|-----------|--------|
| 0.5 | 75,083 | 38.6% | 0.46 | -0.106% | -7,958% |
| 0.8 | 57,725 | 39.2% | 0.60 | -0.099% | -5,699% |
| 1.0 | 49,824 | 39.6% | 0.65 | -0.096% | -4,764% |
| 1.2 | 43,853 | 39.8% | 0.68 | -0.096% | -4,231% |
| **1.5 (current)** | **37,777** | **40.5%** | **0.71** | **-0.094%** | **-3,554%** |
| **2.0** | **31,760** | **41.6%** | **0.74** | **-0.092%** | **-2,931%** |
| 2.5 | 28,487 | 42.3% | 0.74 | -0.094% | -2,692% |
| 3.0 | 26,648 | 42.6% | 0.75 | -0.096% | -2,558% |

**Optimal ATR_MULT: 2.0** — best per-trade expectancy (-0.092%),
PF improves from 0.71 to 0.74, 16% fewer trades.

### Why wider SL is better

Tight SL (0.5-1.0) causes **whipsaw churn**: stopped out on noise,
re-enters, stopped out again. ATR 0.5 doubles trade count to 75K.
Each SL hit is small but the accumulation + costs is devastating.

Wider SL (2.0+) lets trades breathe through intraday noise. Fewer
SL hits means fewer re-entries, which means lower cost drag.

### Why ATR 2.0 beats 2.5 and 3.0

ATR 2.0 has the best per-trade expectancy (-0.092%). Above 2.0,
per-trade gets slightly worse because the wider SL means bigger
individual losses when SL IS hit, and the target (1.5x of a
larger SL) is further away and reached less often.

---

## Backtest Results — R:R Ratio Sweep (with costs, ATR fixed at 1.5)

| RR_RATIO | Trades | WR | PF | Exp/trade | Return |
|----------|--------|-----|-----|-----------|--------|
| 1.0 | 42,982 | 44.5% | 0.68 | -0.096% | -4,109% |
| 1.2 | 40,292 | 42.1% | 0.69 | -0.097% | -3,904% |
| **1.5 (current)** | **37,777** | **40.5%** | **0.71** | **-0.094%** | **-3,554%** |
| 1.8 | 36,143 | 39.8% | 0.73 | -0.090% | -3,251% |
| 2.0 | 35,396 | 39.5% | 0.73 | -0.089% | -3,145% |
| **2.5** | **34,224** | **39.3%** | **0.74** | **-0.087%** | **-2,989%** |
| 3.0 | 33,660 | 39.2% | 0.74 | -0.087% | -2,931% |

**Optimal RR_RATIO: 2.5** — best per-trade expectancy (-0.087%),
PF improves from 0.71 to 0.74.

### The classic WR vs R:R tradeoff

Low R:R (1.0): WR 44.5% but small wins. Target is close to entry
so it gets hit often, but each win is small. Combined with costs,
net is worse.

High R:R (2.5+): WR 39.3% but bigger wins. Fewer trades reach
target, but when they do the payoff covers multiple losses. This
is the classic trend-following profile.

### Why R:R 2.5 beats 3.0

Diminishing returns. R:R 3.0 has identical PF (0.74) and
expectancy (-0.087%) as 2.5 but lower WR (39.2% vs 39.3%).
Statistically indistinguishable. 2.5 is simpler.

---

## Combined Recommendation

| Parameter | Current | Backtest Optimal | **Set To** | Reason |
|-----------|---------|-----------------|-----------|--------|
| ATR_MULTIPLIER | 1.5 | 2.0 | **2.0** | Best per-trade expectancy, fewer whipsaws |
| RR_TARGET_RATIO | 1.5 | 2.5 (by PF) | **1.8** | 2.5 needs 2.5% move — unrealistic intraday. 1.8 needs ~1.8% — achievable on trending days. |

**Reality check (2026-05-25)**: With ATR=2.0 on RELIANCE (ATR ~Rs.7),
RR=2.5 needs a Rs.35 / 2.5% move to hit target. Typical daily range
is 1.5-2.5%, so target is at the extreme. Most trades would EOD
square-off, never hitting target. RR=1.8 needs ~Rs.25 / 1.8% — still
ambitious but achievable on good trending days.

| RR | Target % | Realistic? |
|----|----------|------------|
| 1.0 | 1.0% | Easy — hit most days |
| 1.5 | 1.5% | OK — hit on average days |
| 1.8 | 1.8% | Stretch — hit on trending days |
| 2.0 | 2.0% | Hard — needs strong directional move |
| 2.5 | 2.5% | Unrealistic — top of daily range |

---

## Conclusion

**Verdict: CHANGE values — ATR_MULTIPLIER=2.0, RR_TARGET_RATIO=2.5**

Both parameters show monotonic improvement in the direction of
wider SL and further target. This aligns with the well-known
quant principle: intraday noise is high, so SL must be wide
enough to survive it, and target must be far enough to compensate
for costs.

Still not profitable alone (PF ~0.74) — needs K1 cap + signal
quality filters to push above 1.0.

**Re-test required**: After E3 (R:R Floor) is tested, re-run E1
with optimal ATR+RR combination together.

---

## Code Review

**Status: SUPPORTED and FUNCTIONAL**

| Check | Result |
|-------|--------|
| Config: ATR_MULTIPLIER | `ATR_MULTIPLIER: float = 1.5` (config.py) |
| Config: RR_TARGET_RATIO | `RR_TARGET_RATIO: float = 1.5` (config.py) |
| Config: ATR_PERIOD | `ATR_PERIOD: int = 14` (config.py) |
| Used in | `order_engine.py::enter_trade()` — computes SL/target from ATR |
| Fallback | If ATR=0, uses `DEFAULT_STOP_LOSS_PCT` / `DEFAULT_TARGET_PCT` |
| Logging | Yes — logs computed SL/target for each entry |
| Edge cases | ATR=0 handled via fallback; negative ATR impossible (abs values) |

**Action**: Config values CHANGED (2026-05-25):
- `ATR_MULTIPLIER`: 1.5 -> **2.0** (backtest optimal)
- `RR_TARGET_RATIO`: 1.5 -> **1.8** (backtest said 2.5 but reality
  check shows 2.5% target unrealistic for intraday; 1.8 is practical)
- Backtest evidence in config.py comments
- May be re-tuned if E3 or later gates shift the optimal

---

*Raw data: `reports/backtest/gate_test_E1_ATR.json`,
`reports/backtest/gate_test_E1_RR.json`*
