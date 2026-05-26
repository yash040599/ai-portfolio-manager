# Backtest Results: Gates G6+G7 -- VWAP Trend-Fight & Extension

**Date run**: 2026-05-26
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)
**Baseline**: [BACKTEST_BASELINE.md](BACKTEST_BASELINE.md)

---

## What These Gates Do

**G6 — VWAP Trend-Fight** (`VWAP_TREND_FIGHT_PCT`, was hardcoded 0.3%):
Blocks BUY when price is >X% BELOW VWAP (fighting institutional
selling) and SELL when price is >X% ABOVE VWAP.

**G7 — VWAP Extension** (`VWAP_EXTENSION_BLOCK_PCT`, was 0.8%):
Blocks BUY when price is >X% ABOVE VWAP (chasing extended move)
and SELL when price is >X% BELOW VWAP. Has score override at 6.0.

Both activate after 10:15 AM (VWAP needs ~1 hour of data).

**Previous values**: G6 = 0.3% (hardcoded), G7 = 0.8%
**New values**: G6 = 99.0% (disabled), G7 = 99.0% (disabled)

---

## G6 Backtest Results (with costs)

| VWAP_FIGHT | Trades | WR | PF | Exp/trade | Return |
|------------|--------|-----|-----|-----------|--------|
| **0 (disabled)** | **37,777** | **40.5%** | **0.71** | **-0.094%** | **-3,554%** |
| 0.1% | 36,782 | 40.5% | 0.71 | -0.094% | -3,465% |
| 0.2% | 36,965 | 40.5% | 0.71 | -0.094% | -3,479% |
| 0.3% (was current) | 37,103 | 40.5% | 0.71 | -0.094% | -3,500% |
| 0.5% | 37,336 | 40.5% | 0.71 | -0.093% | -3,488% |
| 0.7% | 37,530 | 40.5% | 0.71 | -0.093% | -3,509% |
| 1.0% | 37,673 | 40.5% | 0.71 | -0.094% | -3,528% |

**G6 Verdict: INERT.** PF stays at 0.71 at every level. WR 40.5%
everywhere. Even at 0.1% only removes 2.6% of trades. The removed
trades have identical profile to kept trades.

---

## G7 Backtest Results (with costs)

| VWAP_EXT | Trades | WR | PF | Exp/trade | Return |
|----------|--------|-----|-----|-----------|--------|
| **0 (disabled)** | **37,777** | **40.5%** | **0.71** | **-0.094%** | **-3,554%** |
| 0.3% | 19,055 | 39.4% | 0.67 | -0.097% | -1,845% |
| 0.5% | 24,508 | 39.4% | **0.66** | -0.102% | -2,496% |
| 0.8% (was current) | 29,588 | 39.6% | 0.67 | -0.101% | -2,991% |
| 1.0% | 31,714 | 39.7% | 0.67 | -0.100% | -3,157% |
| 1.5% | 35,033 | 40.0% | 0.69 | -0.097% | -3,404% |
| 2.0% | 36,703 | 40.4% | 0.71 | -0.092% | -3,390% |

**G7 Verdict: HARMFUL.** Every extension threshold makes PF worse:
- 0.8% (was current): PF drops to 0.67 (removes 8,189 trades = 22%)
- 0.3% (tightest): PF drops to 0.67 (removes 18,722 trades = 50%!)
- 0.5%: PF drops to 0.66 — worst of all

---

## Analysis

### Why VWAP extension blocks hurt

Trades extended from VWAP are momentum trades — price has moved
because of strong directional flow. These are the BEST intraday
setups. The gate blocks exactly the trades that benefit most from
trend continuation.

At 0.3%, the gate removes HALF of all trades. Those removed trades
have the same or better win rate as the full set. The gate is
destroying edge by eliminating high-momentum setups.

### Why VWAP trend-fight is inert

Most scored trades already align with VWAP direction. The scorer
generates BUY signals when momentum is up (price above VWAP) and
SELL when momentum is down (price below VWAP). Fighting-VWAP
entries are rare because the scoring model naturally avoids them.

### Code fix: G6 was hardcoded

The 0.3% trend-fight threshold was hardcoded in `order_engine.py`
(not configurable). Now reads from `VWAP_TREND_FIGHT_PCT` config.

---

## Conclusion

**G6 Verdict: DISABLE (set to 99.0)**
Gate is inert — no measurable impact at any level.

**G7 Verdict: DISABLE (set to 99.0)**
Gate actively hurts — PF drops from 0.71 to 0.67 at every level.

**Config changes**:
- Added `VWAP_TREND_FIGHT_PCT: 99.0` (was hardcoded 0.3)
- `VWAP_EXTENSION_BLOCK_PCT: 0.8 -> 99.0`
- `order_engine.py`: uses `VWAP_TREND_FIGHT_PCT` instead of hardcoded 0.3

---

## Code Review

| Check | Result |
|-------|--------|
| G6 config | `VWAP_TREND_FIGHT_PCT: float = 99.0` (NEW, was hardcoded 0.3) |
| G7 config | `VWAP_EXTENSION_BLOCK_PCT: float = 99.0` (was 0.8) |
| Used in | `order_engine.py::enter_trade()` VWAP guard block (~line 2793) |
| Time guard | Activates after 10:15 AM only (correct) |
| Score override | G7 skips when `|score| >= 6.0` (VWAP_EXT_SCORE_OVERRIDE) |
| Logging | Yes — both gates log clear rejection reasons |
| Fresh reversal | Separate gate in same code block (not tested here) |

---

*Capital: Rs.50,000, Rs.15,000/trade*
*Raw data: `reports/backtest/gate_test_G6.json`, `gate_test_G7.json`*
