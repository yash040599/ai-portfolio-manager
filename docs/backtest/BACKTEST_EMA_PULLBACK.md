# Backtest Results: Strategy 3 -- EMA Pullback Momentum

**Date run**: 2026-05-25
**Strategy doc**: [TRADE_REVAMP_STRATEGIES.md](../TRADE_REVAMP_STRATEGIES.md#strategy-3-ema-pullback-momentum)

---

## Parameters Used

| Parameter | Value |
|-----------|-------|
| EMA Fast | 9-period (continuous across days) |
| EMA Slow | 21-period (continuous across days) |
| MACD | 12, 26, 9 |
| ADX Min | 20 (must be trending) |
| ATR Period | 14 |
| Target | 1.5x ATR from entry |
| Trail | After +1x ATR profit, trail at EMA(9) |
| Signal exit | EMA(9) crosses EMA(21) |
| Pullback zone | Within 0.3% of EMA(9) |
| Entry window | 10:00 - 14:30 IST |
| Square off | 15:05 IST |
| Capital | Rs.50,000 |

---

## Results: 15-min Intraday (May 2024 - May 2026)

| Metric | Value |
|--------|-------|
| Period | May 28, 2024 - May 22, 2026 (2.0 years) |
| Total trades | 38,967 |
| Wins / Losses | 16,663 / 22,304 |
| **Win rate** | **42.8%** |
| Avg win | +0.449% |
| Avg loss | -0.312% |
| **Profit factor** | **1.07** |
| Expectancy | +0.013% per trade |
| **Total return** | **+521.98%** |
| **CAGR** | **+151.45%** |
| **Max drawdown** | **60.58%** |
| **Sharpe ratio** | **4.03** |
| Win months / Lose months | 20 / 5 |
| Best month | Jan 2025 (+115.69%) |
| Worst month | Feb 2026 (-45.33%) |

### Exit Reason Breakdown

| Reason | Count | % |
|--------|-------|---|
| Stop-loss | 16,173 | 41.5% |
| EOD square-off | 13,178 | 33.8% |
| Target hit | 9,262 | 23.8% |
| EMA cross exit | 354 | 0.9% |

---

## Results: Daily Simulated (10 years)

No trades generated. The daily simulation used the same strict
entry conditions which don't translate well to daily bars (the
pullback-to-EMA9 + bounce pattern is an intraday phenomenon).
The daily sim would need a different formulation.

---

## Verdict: PROMISING (but with caveats)

**This is the best of the three strategies tested.** It generates
positive returns with a Sharpe > 4, but has important caveats.

### Key Observations

1. **Profit factor 1.07** -- positive edge but thin. After real-world
   slippage, brokerage, and taxes (~0.05-0.1% per round-trip), the
   edge may evaporate. Needs careful cost modelling.

2. **42.8% win rate with avg win > avg loss** (+0.449% vs -0.312%)
   -- classic momentum profile. Wins less often but wins bigger.
   This is a healthy ratio (1.44:1 reward-to-risk).

3. **38,967 trades in 2 years** = ~78 trades/day across 50 stocks =
   ~1.5 trades/stock/day. Very high frequency -- may need to cap at
   1 trade per stock per day to reduce overtrading.

4. **Max drawdown 60.58%** -- unacceptable for live trading. This is
   the cumulative P&L drawdown from the all-time peak. Would need
   position sizing / daily loss limits to control.

5. **20 winning months vs 5 losing** -- good consistency. Only 5
   months lost money out of 25.

6. **Jan 2025: +115.69%** -- a strong trending month on NSE. The
   strategy captures momentum beautifully when markets trend.

7. **Feb 2026: -45.33%** -- a sharp reversal month. When trends
   break, the pullback entries get caught in false signals.

### When It Performs Best

- **Sustained trending markets** -- strong sector rotations, FII
  buying/selling waves, post-budget rallies
- **Stocks with clear multi-day momentum** -- tech rallies, banking
  sector moves, commodity cycles
- **10:00-12:00 window** -- best signals as the morning trend
  establishes and pullbacks form

### What Market Conditions Break It

- **Choppy, directionless markets** -- EMAs oscillate, generating
  false pullback signals that hit SL
- **Sudden reversals** (Feb 2026 type) -- the EMA trend says "up"
  but the stock gaps down, pullback entry becomes a falling knife
- **Very high ADX (>40)** -- overextended trends where pullbacks
  to EMA9 don't happen (the strategy misses the move entirely)
- **Low-volume stocks** -- EMA pullbacks in thin names are noise

### Possible Improvements (Not Tested)

- **Cap at 1 trade per stock per day** -- would reduce from ~39K to
  ~10-15K trades, improving quality
- **Add daily loss limit** -- stop trading after -2% cumulative
  intraday drawdown
- **Tighter ADX range** (20-35) -- avoid both choppy and overextended
- **Volume filter** -- require breakout candle volume > 1.2x average
- **Reduce to NIFTY 50** top 20 most liquid stocks only
- **After-cost modelling** -- apply 0.05% slippage + brokerage to
  see if the 0.013% expectancy survives

---

*Raw trade data: `reports/backtest/ema_pullback_intraday_trades.json`*

---

## Code Review

**Status: CONFIG FLAG ONLY — NO IMPLEMENTATION (pending cost validation)**

| Check | Result |
|-------|--------|
| Config flag | `STRATEGY_EMA_PULLBACK_ENABLED: bool = False` (config.py line 187) |
| Daily cap | `STRATEGY_EMA_PULLBACK_MAX_PER_STOCK_PER_DAY: int = 1` (config.py line 188) |
| Trade mode code | NOT implemented — flag exists but no scanner/entry code reads it |
| Verdict | **DISABLED** — promising (PF 1.07 raw) but thin edge may not survive costs |

**TODO**: If after-cost analysis in Layer 4 (exit logic optimization)
shows the edge survives, implement the EMA Pullback signal as an
additional scoring path in `modes/trade/stock_scanner.py`:
1. Add `_scan_noai_ema_pullback()` method computing EMA(9/21) +
   MACD + ADX on rolling multi-day 15-min candles
2. Add pullback detection logic (prev bar touches EMA9, current
   bar bounces back above)
3. Cap at 1 trade per stock per day via the config param
4. Integrate with existing sector/direction allocation
