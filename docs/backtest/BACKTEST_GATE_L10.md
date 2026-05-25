# Backtest Results: Gate L10 -- Loser-Exit Late

**Date run**: 2026-05-26
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: [BACKTEST_BASELINE.md](BACKTEST_BASELINE.md)

---

## What This Gate Does

`LOSER_EXIT_HOUR` / `LOSER_EXIT_MINUTE` auto-exits any losing
position after a specified time. Prevents holding losers into the
illiquid closing minutes. Winners with active trailing stops keep
running until square-off.

**Current value**: 14:45 (2:45 PM IST)
**Config**: `LOSER_EXIT_HOUR`, `LOSER_EXIT_MINUTE`

---

## How It Works in the Code

In `order_engine.py::check_loser_exit()` (line ~4227):

- Called during the monitor loop (not at entry)
- After LOSER_EXIT_HOUR:MINUTE, iterates all open positions
- If position P&L < 0 → exit at market price
- If position near breakeven (< 0.1%) → tighten SL to entry
- Skips adopted positions in grace window
- Logging: "LOSER EXIT: SYMBOL SIDE — losing Rs.X, exiting"

Code is clean, config-driven, graceful, well-logged.

---

## Backtest Results (with costs)

| LOSER_EXIT_HR | Trades | WR | PF | Exp/trade | Return |
|---------------|--------|-----|-----|-----------|--------|
| 0 (disabled) | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% |
| **12** | **52,144** | **26.8%** | **0.62** | **-0.098%** | **-5,084%** |
| 13 | 45,284 | 31.7% | 0.66 | -0.096% | -4,344% |
| 14 | 37,777 | 39.0% | 0.71 | -0.095% | -3,572% |
| 15 | 37,777 | 40.5% | 0.71 | -0.094% | -3,554% |

---

## Analysis

**This gate is HARMFUL in standalone testing.**

### Hour 12 is catastrophic

Trade count INCREASED from 37,777 to 52,144 (+38%):
1. Loser cut at noon → position slot freed
2. Scoring still generates signals → new entry on same stock
3. New entry is also likely a loser (base signal is weak)
4. Net effect: more losing trades + more cost drag

WR dropped from 40.5% to 26.8% because many trades that would
have recovered by EOD were cut at noon and re-entered.

### Hour 14 is neutral

Same trade count (37,777) because by 2 PM most positions have
already hit SL or target. The few losers cut at 14:00 don't free
slots fast enough for new entries before 14:30 entry cutoff.
Marginally worse (-0.095% vs -0.094%).

### Hour 15 is identical to disabled

Only 10 minutes before square-off at 15:10 — no difference.

### The churn problem

In isolation, this gate creates **churn**: exit loser → re-enter →
exit loser → re-enter. Each cycle costs ~Rs.36 in charges.

**However**, with K1 portfolio cap=2 active (Phase 7 combined
test), the churn would be blocked — only 2 trades/day total, so
loser-exit would just convert one EOD-square-off into an earlier
exit without allowing re-entry. This interaction needs testing.

---

## Conclusion

**Verdict: KEEP at 14:45 but RE-TEST in Phase 7 with K1 active**

Standalone, this gate is neutral (hour 14) to harmful (hour 12-13).
But combined with K1 cap=2, the churn effect is eliminated and
the gate may become slightly beneficial (cut losers 25 min before
square-off to avoid end-of-day slippage).

**No config change for now.** The current 14:45 value is already
in the neutral zone. Will re-evaluate in Phase 7 combined test.

---

## Code Review

**Status: SUPPORTED and FUNCTIONAL**

| Check | Result |
|-------|--------|
| Config | `LOSER_EXIT_HOUR: int = 14`, `LOSER_EXIT_MINUTE: int = 45` |
| Used in | `order_engine.py::check_loser_exit()` (line ~4227) |
| Called by | Manager monitor loop (not at entry time) |
| Logging | Yes — "LOSER EXIT: SYMBOL SIDE — losing Rs.X" |
| Grace window | Skips adopted/resumed positions in grace |
| Breakeven handling | Tightens SL on near-breakeven positions |
| Edge cases | current_price <= 0 skipped; checks `pnl < 0` correctly |
| Hardcoded? | No — fully config-driven |

**No config change. No code change.**

---

*Capital: Rs.50,000, Rs.15,000/trade*
*Raw data: `reports/backtest/gate_test_L10.json`*
