# Backtest Results: Strategy 2 -- ORB-15 Breakout

**Date run**: 2026-05-25
**Strategy doc**: [TRADE_REVAMP_STRATEGIES.md](TRADE_REVAMP_STRATEGIES.md#strategy-2-opening-range-breakout-orb-15)

---

## Parameters Used

| Parameter | Value |
|-----------|-------|
| ORB candle | First 15-min (9:15-9:30 IST) |
| Breakout window | 9:30-10:15 IST |
| Volume confirmation | Breakout candle > 1.5x 20-day avg |
| ORB max range | 1.5% (skip if wider) |
| EMA trend confirm | EMA(9) on 15-min |
| Target 1 (partial) | 1.5x ORB range (exit 50%) |
| Target 2 (full) | 2.5x ORB range (exit remaining) |
| SL | Opposite side of ORB |
| Trail after T1 | SL to breakeven, then EMA(9) |
| Capital | Rs.50,000 |
| Risk per trade | 1% |

---

## Results: 15-min Intraday (May 2024 - May 2026)

| Metric | Value |
|--------|-------|
| Period | May 28, 2024 - May 21, 2026 (2.0 years) |
| Total trades | 307 |
| Wins / Losses | 171 / 136 |
| **Win rate** | **55.7%** |
| Avg win | +0.547% |
| Avg loss | -0.709% |
| **Profit factor** | **0.97** |
| Expectancy | -0.009% per trade |
| **Total return** | **-2.82%** |
| **CAGR** | **-1.43%** |
| **Max drawdown** | **16.66%** |
| **Sharpe ratio** | **-0.15** |
| Win months / Lose months | 13 / 12 |
| Best month | Sep 2024 (+8.53%) |
| Worst month | Dec 2025 (-5.98%) |

### Exit Reason Breakdown

| Reason | Count | % |
|--------|-------|---|
| EOD square-off | 181 | 59.0% |
| Stop-loss | 59 | 19.2% |
| Target 1 (partial) | 49 | 16.0% |
| Target 2 (full) | 18 | 5.9% |

---

## Results: Daily Simulated (10 years)

| Metric | Value |
|--------|-------|
| Period | May 23, 2024 - May 7, 2026 (2.0 years)* |
| Total trades | 1,182 |
| Win rate | 88.8% |
| Profit factor | 11.69 |
| Total return | 847.63% |
| CAGR | 215.94% |
| Max drawdown | 1.8% |
| Sharpe ratio | 37.11 |

\* The daily simulation is **overly optimistic** -- it assumes perfect
ORB breakout detection from daily OHLC which overfits heavily. The
daily numbers should be heavily discounted. Trust the 15-min results.

---

## Verdict: MARGINAL (near break-even)

**The ORB-15 strategy is close to break-even on real 15-min data but
doesn't generate consistent profit after costs.**

### Key Observations

1. **55.7% win rate is decent** -- better than VWAP MR's 23.1%.
   The strategy correctly identifies directional days more often
   than not.

2. **Avg loss > avg win** (-0.71% vs +0.55%) -- this is the fatal
   flaw. When the breakout fails, the SL at the opposite ORB side
   is too far, creating losses that eat the wins.

3. **59% of trades EOD square-off** -- the breakout happens but
   doesn't reach either target, just drifts sideways. These trades
   are small winners/losers that drag the average down.

4. **Only 5.9% hit T2** -- the 2.5x ORB range target is rarely
   reached intraday on NIFTY 50 stocks.

5. **307 trades in 2 years** -- low frequency (~0.6/day across 50
   stocks), which is expected for ORB (max 1 trade per stock per day
   and most days don't trigger the volume filter).

### When It Performs Best

- **Strong directional days** with clear gap-up/down opens (Sep 2024
  was a strong trending month on NSE)
- Days with high opening volume (FII-driven sector moves)
- Post-earnings gap days where the first 15 min sets the tone

### What Market Conditions Break It

- **Fakeout breakouts** -- price breaks ORB, reverses, hits SL (19%
  of trades). Common on low-conviction days.
- **Narrow ORB** with wide SL -- small ORB range means close SL to
  entry but the opposite ORB side is still relatively far, creating
  bad R:R.
- **Lunch-hour fades** -- breakout happens at 9:30 but by 12:00 the
  move has exhausted, leading to EOD square-off near entry.
- **Event days** where ORB is very wide (> 1.5%) and the filter
  correctly skips these, but the filtered set still has noise.

### Possible Improvements (Not Tested)

- Tighter SL: use 0.5x ORB range below entry instead of full ORB-Low
- Require NIFTY direction alignment (NIFTY gap same direction)
- T1 at 1.0x range instead of 1.5x (faster partial)
- Only take BUY breakouts in a bullish NIFTY week, SELL in bearish
- Add ADX > 20 filter (confirm trending, not just noisy breakout)

---

*Raw trade data: `reports/backtest/orb15_intraday_trades.json` and
`reports/backtest/orb15_daily_trades.json`*
