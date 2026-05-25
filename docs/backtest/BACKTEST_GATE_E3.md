# Backtest Results: Gate E3 -- R:R Floor

**Date run**: 2026-05-25
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: [BACKTEST_BASELINE.md](BACKTEST_BASELINE.md)

---

## What This Gate Does

`RR_HARD_FLOOR` rejects trades where the actual risk:reward ratio
(`target_distance / sl_distance`) is below a minimum. It's the
last safety check before order placement.

**Current value**: 1.3
**Config**: `RR_HARD_FLOOR`

---

## How It Works in the Code

In `order_engine.py::enter_trade()` (gate #11, line ~2347):

```
sl_dist = abs(entry - sl)
tgt_dist = abs(target - entry)
if sl_dist > 0 and tgt_dist / sl_dist < rr_floor:
    REJECT
```

**Key interaction with E1**: The ATR path computes
`target = SL_distance * RR_TARGET_RATIO`. So for NoAI ATR trades,
the actual R:R is ALWAYS exactly `RR_TARGET_RATIO` (currently 1.8).
The floor only fires when:

1. Claude AI sets SL/target with bad R:R
2. SL gets capped by `MAX_INTRADAY_SL_PCT` (changes effective R:R)
3. SL gets floored by `MIN_SL_DISTANCE_PCT` (changes effective R:R)
4. External/adopted positions have arbitrary R:R

---

## Backtest Results (with costs)

| RR_FLOOR | Trades | WR | PF | Exp/trade | Return |
|----------|--------|-----|-----|-----------|--------|
| 0 (disabled) | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% |
| 0.5 | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% |
| 1.0 | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% |
| 1.3 (current) | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% |
| 1.5 | 37,012 | 40.5% | 0.71 | -0.094% | -3,497% |
| 1.8 | 0 | — | — | — | — |
| 2.0 | 0 | — | — | — | — |
| 2.5 | 0 | — | — | — | — |

---

## Analysis

**Floor 0-1.3**: Identical results. All backtester trades have
R:R = 1.5 (from `rr_ratio` parameter), so floors below 1.5 never
reject anything.

**Floor 1.5**: Drops 765 trades (2%) — these are edge cases where
ATR was unavailable and the fallback SL/target produced R:R
slightly below 1.5. No meaningful PF change.

**Floor 1.8+**: Zero trades. The backtester uses `rr_ratio=1.5`
for all trades, so a floor above 1.5 rejects everything. In the
real code with `RR_TARGET_RATIO=1.8`, a floor of 1.3 would never
block ATR trades (1.8 > 1.3 always).

**This gate is a safety net, not a filter.** It catches bad R:R
from non-standard paths (Claude AI, SL cap/floor edge cases,
external positions) but never affects normal ATR-computed trades.

---

## Conclusion

**Verdict: KEEP at 1.3 (unchanged) — it's a safety net**

With `RR_TARGET_RATIO = 1.8`, all normal ATR trades have R:R = 1.8
which always clears the 1.3 floor. The floor exists to catch:
- Claude AI trades with hallucinated SL/target
- SL cap/floor edge cases that distort R:R
- External positions adopted from Zerodha

Setting it higher than 1.3 but below 1.8 (e.g., 1.5) would only
add a marginal safety margin. Setting it at or above 1.8 would
block trades where SL got widened by `MIN_SL_DISTANCE_PCT`, which
is counterproductive.

**No config change needed.**

---

## Code Review

**Status: SUPPORTED and FUNCTIONAL**

| Check | Result |
|-------|--------|
| Config | `RR_HARD_FLOOR: float = 1.3` (config.py) |
| Used in | `order_engine.py::enter_trade()` gate #11 (line ~2347) |
| Method | `current_rr_floor()` returns `RR_HARD_FLOOR` |
| Logging | Yes — logs rejection with actual R:R and floor value |
| Logging (pass) | Yes — logs "R:R X.XX:1 OK (floor Y.Y:1)" |
| Edge cases | `sl_dist > 0` guard prevents division by zero |
| Hardcoded? | No — reads from `self.cfg.RR_HARD_FLOOR` |
| Interaction | Works correctly with E1 (ATR SL/Target) |

**No config change. No code change.**

---

*Capital: Rs.50,000, Rs.15,000/trade*
*Raw data: `reports/backtest/gate_test_E3.json`*
