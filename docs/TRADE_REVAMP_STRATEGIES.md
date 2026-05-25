# Trade Revamp — Candidate Strategies

**Date**: 2026-05-25
**Capital**: ₹50,000 (config-adjustable)
**Risk per trade**: 1–2% (₹500–₹1,000)
**Market**: NSE (NIFTY 50/100 stocks)
**Timeframe**: Intraday (squared off by 15:05 IST)

---

## Strategy 1: VWAP Mean-Reversion (Rubber Band)

**Core idea:** NIFTY 50 large-caps revert to VWAP during range-bound
days. Enter when price stretches too far from VWAP, exit when it snaps
back.

### Indicators

| Indicator | Setting | Purpose |
|-----------|---------|---------|
| VWAP | Standard (reset daily) | Anchor — the "fair value" line |
| VWAP Bands | ±1.5σ and ±2.0σ | Stretch zones |
| RSI | 14-period, 15-min candles | Exhaustion confirmation |
| ADX | 14-period, 15-min candles | Regime filter (< 25 = range-bound) |
| RVOL | vs 20-day avg at same hour | Avoid low-liquidity traps |

### Entry Rules

**BUY when ALL are true:**
1. Price touches or pierces the **lower VWAP −1.5σ band**
2. RSI(14) < 35 (oversold)
3. ADX(14) < 25 (range-bound, NOT trending)
4. RVOL > 0.8 (not dead zone)
5. Time: **10:00–14:00 IST** only

**SELL (short) when:** mirror — price touches upper +1.5σ, RSI > 65,
ADX < 25.

### Exit Rules

| Exit Type | Rule |
|-----------|------|
| **Target** | VWAP line itself (typically 0.4–0.8% from entry) |
| **Trail** | After price crosses VWAP, trail SL at VWAP ± 0.1% |
| **Time** | Close by 15:05 IST regardless |

### Stop-Loss

- Hard SL at the **−2.0σ band** (or +2.0σ for shorts)
- Typical risk: 0.5–0.8% of entry price on NIFTY 50 stocks
- With ₹50K and 1% risk → ₹500 max loss → size = ₹500 / (entry − SL)

### Best Market Conditions

- **Range-bound days** (ADX < 25) — ~60% of NSE trading days
- Flat NIFTY, no major news, pre-earnings lull
- **Avoid:** gap days, budget day, RBI policy, global shocks (high ADX)

### Edge

- Institutional flows anchor to VWAP. When retail pushes price away,
  institutions buy/sell back to VWAP. You're trading with the bigger
  flow.
- ADX filter keeps you out on trending days where MR gets destroyed.
- Expected: **55–60% win rate**, **1.2–1.5:1 R:R**, **2–4 trades/day**.

---

## Strategy 2: Opening Range Breakout (ORB-15)

**Core idea:** The first 15 minutes establish a range. A breakout with
volume confirms the day's directional bias. Ride the trend.

### Indicators

| Indicator | Setting | Purpose |
|-----------|---------|---------|
| ORB High/Low | First 15-min candle (9:15–9:30 IST) | Defines the range |
| EMA | 9-period, 5-min candles | Trend confirmation post-breakout |
| ATR | 14-period, daily candles | Dynamic SL sizing |
| SuperTrend | Period 10, Multiplier 2.0, 15-min | Direction filter |
| Volume | Breakout candle vs 20-day avg | Validates the move |

### Entry Rules

**BUY when ALL are true:**
1. Price closes above ORB-High on a **5-min candle**
2. Close is also above EMA(9) on 5-min chart
3. Breakout candle volume > **1.5× average** for that time slot
4. SuperTrend(10, 2.0) on 15-min is **GREEN** (bullish)
5. Time: **9:30–10:15 IST** (breakout must happen early)

**SELL (short) when:** mirror — close below ORB-Low, below EMA(9),
SuperTrend RED.

### Exit Rules

| Exit Type | Rule |
|-----------|------|
| **Target 1** | 1.5× ORB range width from entry (exit 50% position) |
| **Target 2** | 2.5× ORB range width (exit remaining 50%) |
| **Trail after T1** | Move SL to entry (breakeven), then trail at EMA(9) on 5-min |
| **Time** | Close by 15:05 IST |

### Stop-Loss

- SL at the **opposite side of the ORB** (long → SL at ORB-Low)
- If ORB range > 1.5% of price → **skip trade** (risk too large)
- Typical risk: 0.5–1.0% of entry price

### Best Market Conditions

- **Trending/volatile days** — gap opens, news-driven, post-earnings
- Days with ADX > 20 and clear NIFTY directional bias
- **Avoid:** narrow-range indecision days, lunch-hour fakeouts

### Edge

- First 15 min captures ~30–40% of the day's range on NSE. Breakout
  with volume signals institutional commitment.
- Volume confirmation eliminates ~40% of false breakouts.
- Partial profit at 1.5× + trail gives blended R:R of ~2:1 even at
  45% win rate.
- Expected: **40–50% win rate**, **1.8–2.5:1 R:R**, **0–1 trades/day**.

---

## Strategy 3: EMA Pullback Momentum

**Core idea:** In a trending stock, wait for a pullback to a fast EMA,
confirm momentum hasn't died, and enter in the trend direction.

### Indicators

| Indicator | Setting | Purpose |
|-----------|---------|---------|
| EMA Fast | 9-period, 15-min candles | Pullback zone |
| EMA Slow | 21-period, 15-min candles | Trend direction |
| MACD | 12, 26, 9 on 15-min candles | Momentum confirmation |
| RSI | 14-period, 15-min candles | Not overbought/oversold |
| Stochastic RSI | 14, 14, 3, 3 | Pullback exhaustion timing |
| ADX | 14-period, 15-min candles | Trend strength (> 20) |

### Entry Rules

**BUY when ALL are true:**
1. EMA(9) > EMA(21) — **uptrend confirmed**
2. Price pulls back and **touches or crosses below EMA(9)**
3. Price stays **above EMA(21)** (trend intact)
4. Next candle **closes back above EMA(9)** — pullback over
5. MACD histogram positive (or just crossed above zero)
6. StochRSI < 30 on pullback candle (oversold within uptrend)
7. ADX > 20 (trending, not choppy)
8. Time: **10:00–14:30 IST**

**SELL (short) when:** mirror — EMA(9) < EMA(21), pullback to EMA(9),
rejection below.

### Exit Rules

| Exit Type | Rule |
|-----------|------|
| **Target** | 1.5× ATR(14) from entry |
| **Trail** | After +1× ATR profit, trail SL at EMA(9) on 15-min |
| **Signal exit** | EMA(9) crosses below EMA(21) → exit immediately |
| **Time** | Close by 15:05 IST |

### Stop-Loss

- SL at the **pullback low** (candle that touched EMA(9)) minus 0.1%
- Alternatively: SL at EMA(21) — the "trend alive" line
- Typical risk: 0.3–0.6% of entry price

### Best Market Conditions

- **Trending days with clear sector momentum** — IT rally, banking
  breakout, etc.
- Best in 10:00–12:00 window (strongest trends on NSE)
- **Avoid:** choppy low-ADX days, lunchtime (12:00–13:30), news
  reversals

### Edge

- Trading WITH the trend and entering at a discount (the pullback).
- Multi-indicator confirmation filters out 70%+ of low-quality setups.
- Risk is naturally small (pullback low is close) while reward is full
  trend continuation.
- Expected: **50–55% win rate**, **1.5–2.0:1 R:R**, **1–2 trades/day**.

---

## Comparison Matrix

| Attribute | VWAP Mean-Reversion | ORB-15 Breakout | EMA Pullback Momentum |
|-----------|--------------------|-----------------|-----------------------|
| **Type** | Mean-reversion | Breakout | Trend-following |
| **Best market** | Range-bound (ADX < 25) | Volatile/trending | Trending (ADX > 20) |
| **Win rate** | 55–60% | 40–50% | 50–55% |
| **R:R** | 1.2–1.5:1 | 1.8–2.5:1 | 1.5–2.0:1 |
| **Trades/day** | 2–4 | 0–1 | 1–2 |
| **Time window** | 10:00–14:00 | 9:30–10:15 | 10:00–14:30 |
| **Complexity** | Medium | Low | Medium |
| **Existing code** | VWAP+bands, RSI, ADX ✅ | ORB, SuperTrend ✅ | EMA, MACD, StochRSI ✅ |

---

## Next Steps

1. **Backtest each strategy** against `data/candle_cache.db` (40MB of
   15-min + daily candles for NIFTY 50/100 stocks)
2. Compare: win rate, profit factor, max drawdown, Sharpe ratio
3. Pick the best 1–2 strategies for the revamped trade mode
4. Implement in `modes/trade/` with clean entry/exit/risk rules
5. Paper-trade for 2 weeks before going live

---

*Generated 2026-05-25. To be validated by backtesting before any live
deployment.*
