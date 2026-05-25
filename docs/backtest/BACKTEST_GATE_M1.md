# Backtest Results: Gate M1 — MIN_SCORE Threshold

**Date run**: 2026-05-25
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: [BACKTEST_BASELINE.md](BACKTEST_BASELINE.md)

---

## What This Gate Does

`V2_MIN_SCORE` is the minimum composite score a candidate must reach
to be considered for entry. Scores range from -10 to +10. BUY signals
are positive, SELL signals are negative. Only candidates with
`|score| >= MIN_SCORE` pass the pre-filter.

A higher threshold means fewer but higher-conviction trades.

**Current value**: 2.0
**Config**: `V2_MIN_SCORE`

---

## Backtest Results (with NSE intraday costs)

| MIN_SCORE | Trades | Win Rate | PF | Exp/trade | Return | Max DD |
|-----------|--------|----------|-----|-----------|--------|--------|
| 2.0 (baseline) | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% | 3,624% |
| 3.0 | 22,943 | 40.6% | 0.70 | -0.094% | -2,162% | 2,219% |
| 4.0 | 12,225 | 40.3% | 0.70 | -0.092% | -1,128% | 1,177% |
| 5.0 | 7,458 | 40.3% | 0.70 | -0.086% | -645% | 655% |
| **6.0** | **3,392** | **43.0%** | **0.80** | **-0.063%** | **-213%** | **230%** |
| 7.0 | 62 | 38.7% | 0.78 | -0.074% | -4.6% | 12.0% |

---

## Analysis

### What improves as MIN_SCORE rises

- **Trade count drops sharply**: 37,777 -> 3,392 at score 6.0
  (91% reduction). This is the biggest benefit — fewer trades =
  less cost drag.
- **Win rate improves slightly**: 40.5% -> 43.0% at score 6.0.
  Higher-score signals are genuinely better quality.
- **Profit factor improves**: 0.71 -> 0.80 at score 6.0. Still
  unprofitable but the gap is narrowing.
- **Per-trade loss shrinks**: -0.094% -> -0.063%. Closer to zero.

### What doesn't change

- **All values remain unprofitable after costs** (PF < 1.0 everywhere).
  The fundamental issue is the scoring function's thin edge, not
  the threshold level.
- **Win rate plateaus around 40-43%**. Even the highest-score signals
  don't win more than 43% of the time with this scorer.

### Score 6.0 vs 7.0

Score 6.0 is the sweet spot — best PF (0.80) and WR (43%) with
enough trades (3,392) for statistical significance. Score 7.0 drops
to 62 trades — too few to trust.

---

## Conclusion

**Verdict: KEEP at 2.0 for now, but plan to raise to 5.0-6.0 once
the scoring function is improved.**

Raising MIN_SCORE alone cannot make the strategy profitable — the
underlying signal is too weak (PF 1.05 raw). But MIN_SCORE = 6.0
reduces trade count by 91% and improves PF from 0.71 to 0.80,
which means it will be the right filter once the base signal is
stronger.

The real scanner uses 28 indicators (14 candle patterns + 14
technicals). When the backtester is upgraded to use the full
scanner scoring, MIN_SCORE = 5.0-6.0 is the likely optimal value.

**Action**: No config change yet. Revisit after improving the
scoring function accuracy.

**Re-test required**: When the backtester is upgraded to use the
real `analyse_candle_snapshot()` (28 indicators), re-run this sweep.
The optimal value will likely land at 5.0-6.0 with the full scorer.

**Both directions tested**: The backtester scores BUY (positive)
and SELL (negative) signals. `abs(score) >= MIN_SCORE` filters
both. All results include both BUY and SELL trades.

---

## Code Review

**Status: SUPPORTED and FUNCTIONAL**

| Check | Result |
|-------|--------|
| Config parameter | `MIN_SCORE: float = 2.0` in config.py (line 798) |
| Read from config | `self.cfg.MIN_SCORE` in `_prefilter_universe()` (scanner line 1469) |
| Hardcoded? | No — fully config-driven |
| Logging | Yes — `"Score filter: dropped N stocks below \|score\| X"` |
| Edge cases | Uses `abs(combined_score)` so works for both BUY/SELL |
| Graceful failure | If score is 0, candidate is dropped silently (correct) |
| Override support | `min_score_override` parameter for dynamic bumping after losses |
| Current value | 2.0 (backtest says keep for now, raise to 5-6 later) |

**Verdict**: Code is clean, config-driven, well-logged. No changes
needed. Value stays at 2.0 pending scorer improvement.

---

*Raw data: `reports/backtest/gate_test_M1.json`*
