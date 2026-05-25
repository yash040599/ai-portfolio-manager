# Backtest Results: Strategy 1 — VWAP Mean-Reversion

**Date run**: 2026-05-25
**Strategy doc**: [TRADE_REVAMP_STRATEGIES.md](../TRADE_REVAMP_STRATEGIES.md#strategy-1-vwap-mean-reversion-rubber-band)

---

## Parameters Used

| Parameter | Value |
|-----------|-------|
| VWAP Band Entry | +/- 1.5 sigma |
| VWAP Band SL | +/- 2.0 sigma |
| RSI Period | 14 |
| RSI Buy Max | 35 |
| RSI Sell Min | 65 |
| ADX Period | 14 |
| ADX Max (range filter) | 25 |
| RVOL Min | 0.8 |
| Entry window | 10:00 - 14:00 IST |
| Target | VWAP line (mean) |
| Capital | Rs.50,000 |
| Risk per trade | 1% |

---

## Results: 15-min Intraday (May 2024 - May 2026)

| Metric | Value |
|--------|-------|
| Period | May 27, 2024 - May 22, 2026 (2.0 years) |
| Total trades | 3,393 |
| Wins / Losses | 784 / 2,609 |
| **Win rate** | **23.1%** |
| Avg win | +0.328% |
| Avg loss | -0.122% |
| **Profit factor** | **0.80** |
| Expectancy | -0.018% per trade |
| **Total return** | **-62.59%** |
| **CAGR** | **-39.07%** |
| **Max drawdown** | **69.19%** |
| **Sharpe ratio** | **-2.95** |
| Best month | Dec 2025 (+4.44%) |
| Worst month | May 2025 (-15.42%) |

### Exit Reason Breakdown

| Reason | Count | % |
|--------|-------|---|
| Stop-loss | 2,280 | 67.2% |
| EOD square-off | 1,037 | 30.6% |
| Target hit | 76 | 2.2% |

---

## Results: Daily Simulated (10 years)

| Metric | Value |
|--------|-------|
| Period | Aug 27, 2024 - Feb 26, 2026 (1.5 years)* |
| Total trades | 160 |
| Win rate | 4.4% |
| Profit factor | 0.09 |
| Total return | -42.29% |
| CAGR | -30.68% |
| Max drawdown | 42.29% |
| Sharpe ratio | -14.07 |

\* Daily simulation generated fewer signals because the VWAP proxy
from 20-day average typical price only fires on large-range days.

---

## Verdict: FAIL

**This strategy loses money consistently across both time horizons.**

### Why It Fails

1. **67% of trades hit the stop-loss** — the -2.0 sigma band is too
   close; price punches through it on even modest momentum moves
   within the day.

2. **Only 2.2% of trades hit the target** — price rarely snaps all
   the way back to VWAP within the same trading day once it stretches
   to 1.5 sigma. The target (VWAP itself) is too ambitious for a
   single-day mean-reversion on NIFTY 50 stocks.

3. **31% get squared off at EOD** — stuck in losing positions that
   neither hit SL nor target, contributing to cumulative drag.

4. **ADX < 25 filter is insufficient** — many days that appear
   "range-bound" by ADX still have strong directional intraday moves.

### When It Performs Best

- Genuinely choppy, directionless markets (Dec 2025 was the only
  profitable month at +4.4%)
- Pre-holiday / pre-result low-activity weeks
- Sideways NIFTY with no FII-driven sector rotation

### What Market Conditions Break It

- **Trending days disguised as range-bound** (ADX 22-24 but strong
  directional moves within the day)
- **Gap days** where VWAP anchors to the wrong level (open >> prev
  close)
- **Low-volume lunch hours** where the stretch to the band is noise,
  not a genuine exhaustion signal
- **Event days** (RBI policy, budget, quarterly results) with sudden
  directional acceleration

### Possible Improvements (Not Tested)

- Wider bands (2.0 sigma entry, 2.5 sigma SL)
- Partial target: 50% at VWAP -0.5 sigma, 50% at VWAP
- ADX filter tightened to < 18
- Exclude first hour (9:15-10:15) and last hour (14:15-15:15)
- Add NIFTY trend filter: only trade MR when NIFTY is flat for the day

---

*Raw trade data: `reports/backtest/vwap_mr_intraday_trades.json` and
`reports/backtest/vwap_mr_daily_trades.json`*

---

## Code Review

**Status: CONFIG FLAG ONLY — NO IMPLEMENTATION (by design)**

| Check | Result |
|-------|--------|
| Config flag | `STRATEGY_VWAP_MR_ENABLED: bool = False` (config.py line 177) |
| Trade mode code | NOT implemented — flag exists but no scanner/entry code reads it |
| Verdict | **PERMANENTLY DISABLED** — backtest proves it loses money |

No implementation will be written. The flag serves as documentation
that this strategy was evaluated and rejected.
