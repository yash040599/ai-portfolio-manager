# Backtest Results: Gate D5 -- RVOL Floor

**Date run**: 2026-05-26
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: [BACKTEST_BASELINE.md](BACKTEST_BASELINE.md)

---

## What This Gate Does

`RVOL_FLOOR` rejects trades on stocks with low relative volume.
RVOL = today's volume / average daily volume. Low RVOL means less
liquidity, wider spreads, and less reliable signals.

**Current value**: 0.7 (was hardcoded, now config-driven)
**Config**: `RVOL_FLOOR` (new — previously hardcoded at 0.7)

---

## How It Works in the Code

In `order_engine.py::enter_trade()` (gate #7, line ~2200):

```python
rvol_floor = float(getattr(self.cfg, "RVOL_FLOOR", 0.7))
# Optional time-normalization scales floor by hour bucket
if self.cfg.RVOL_TIME_NORMALIZATION_ENABLED:
    bucket = self.cfg.RVOL_FLOOR_BY_HOUR.get(hour, 1.0)
    rvol_floor = round(rvol_floor * bucket, 2)
```

Two data sources:
1. **Live Kite quote** (primary) — but `average_volume` is always
   0 from Kite API, so this path never fires
2. **Scan-time RVOL** (fallback) — from `_indicator_snapshot`,
   computed by the scanner during candle analysis. This is what
   actually works in practice.

---

## Code Fix Made

**Changed**: Hardcoded `0.7` in `order_engine.py` replaced with
`float(getattr(self.cfg, "RVOL_FLOOR", 0.7))`.

**Added**: `RVOL_FLOOR: float = 0.7` config parameter with
backtest evidence comment.

The time-normalization path also updated from `round(0.7 * bucket)`
to `round(rvol_floor * bucket)` so it respects the config value.

---

## Backtest Results (with costs)

| RVOL_FLOOR | Trades | WR | PF | Exp/trade | Return |
|------------|--------|-----|-----|-----------|--------|
| 0 (disabled) | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% |
| 0.3 | 37,610 | 40.4% | 0.71 | -0.094% | -3,549% |
| 0.5 | 36,075 | 40.4% | 0.71 | -0.094% | -3,402% |
| **0.7 (current)** | **30,372** | **40.8%** | **0.72** | **-0.091%** | **-2,769%** |
| 1.0 | 17,552 | 40.8% | 0.72 | -0.095% | -1,668% |
| 1.3 | 8,436 | 41.4% | 0.73 | -0.098% | -826% |
| 1.5 | 5,170 | 40.5% | 0.70 | -0.114% | -588% |
| 2.0 | 1,760 | 40.2% | 0.70 | -0.126% | -222% |

---

## Analysis

**RVOL 0.7 is the optimal value** — best per-trade expectancy
at -0.091% (vs baseline -0.094%). It filters 20% of trades
(7,405 low-volume noise trades removed) while keeping all the
good-volume signals.

### Why 0.7 is optimal

- Below 0.7: too permissive, lets in low-volume noise
- At 0.7: sweet spot — filters the weakest volume stocks
- At 1.0: same PF but worse per-trade (-0.095%) — starts
  filtering decent stocks that happen to have slightly below-
  average volume
- At 1.3+: best PF (0.73) but per-trade gets worse (-0.098%)
  because it's just fewer trades, not better quality trades
- At 1.5+: actively harmful — rejects too many good trades

### Reality check

RVOL 0.7 means "today's volume must be at least 70% of the
recent average." For NIFTY 50 stocks, this filters:
- Pre-holiday thin sessions
- Lunch-hour entries when volume drops
- Stocks with unusual quiet days

This is sensible — a stock trading at 50% of normal volume has
wider spreads and less price discovery.

---

## Conclusion

**Verdict: KEEP at 0.7 (no change needed — value was correct)**

The only change was making it config-driven instead of hardcoded.
The value stays at 0.7.

---

## Code Review

**Status: FIXED — was hardcoded, now config-driven**

| Check | Result |
|-------|--------|
| Config | `RVOL_FLOOR: float = 0.7` (config.py) — **NEW** |
| Was | Hardcoded `0.7` in order_engine.py line 2200 |
| Now | `float(getattr(self.cfg, "RVOL_FLOOR", 0.7))` |
| Time norm | Also fixed: `round(rvol_floor * bucket)` instead of `round(0.7 * bucket)` |
| Logging | Yes — warns on rejection, confirms on pass |
| Fallback | If no volume data, proceeds with warning (graceful) |
| DRY_RUN | Check skipped in dry-run (no live quotes) |
| Known issue | Live Kite avg_volume always 0; relies on scan-time RVOL |

---

*Capital: Rs.50,000, Rs.15,000/trade*
*Raw data: `reports/backtest/gate_test_D5.json`*
