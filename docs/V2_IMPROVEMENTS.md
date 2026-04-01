# V2 Strategy — Improvement Roadmap

Research-backed improvements based on Investopedia, Zerodha Varsity, Toby Crabel (ORB), and institutional intraday practices.

---

## HIGH PRIORITY — Easy + High Impact

### 1. ✅ Volume Confirmation for Candle Patterns
- **Gap**: Candle patterns scored without volume check. A hammer on low volume is unreliable.
- **Fix**: If pattern candle volume > 1.5× 10-candle avg → +1 score. If < 0.5× avg → discard pattern.
- **Source**: Investopedia + Zerodha Varsity: *"Volume spike on the pattern candle is the single most important confirmation."*
- **Effort**: Low | **Impact**: High

### 2. ✅ Relative Volume (RVol) as Stock Filter
- **Gap**: Stocks filtered only by candle score, not by unusual activity.
- **Fix**: RVol = today's volume / avg volume. RVol > 2.0 → +1 bonus. RVol < 0.5 → -1 penalty.
- **Source**: Day trading fundamentals — *Liquidity, Volatility, Volume* are the 3 essentials.
- **Effort**: Low | **Impact**: High

### 3. ✅ Pattern Freshness Decay
- **Gap**: All patterns (current candle or 3 candles ago) score equally.
- **Fix**: Decay multiplier: current = 1.0×, 1 candle ago = 0.7×, 2 candles ago = 0.4×.
- **Source**: Investopedia: *"Pattern potency decreases rapidly 3-5 bars after completion."*
- **Effort**: Low | **Impact**: Medium-High

---

## MEDIUM PRIORITY — Proven, moderate effort

### 4. Opening Range Breakout (ORB)
- **Gap**: Bot observes first 15 min but doesn't capture OR high/low as reference levels.
- **Fix**: Record first 15-min candle H/L. Break above OR high → +2 BUY. Below OR low → -2 SELL.
- **Source**: Toby Crabel's ORB — widely used by institutional intraday traders.
- **Effort**: Medium | **Impact**: High

### 5. MACD Histogram Confirmation
- **Gap**: Only EMA crossover used for momentum. MACD (12,26,9) absent.
- **Fix**: MACD histogram positive & growing → +1. Negative & falling → -1.
- **Source**: Zerodha Varsity covers MACD as one of two most important indicators alongside RSI.
- **Effort**: Low-Medium | **Impact**: Medium

### 6. ✅ Previous Day H/L/C as Support/Resistance
- **Gap**: VWAP is the only reference price. Previous day's H/L are natural S&R levels.
- **Fix**: Near prev high (within 0.5%) → resistance for longs (-1). Near prev low → support for shorts (-1). Also compute daily pivot = (H+L+C)/3.
- **Source**: "Daily Pivots" strategy; Zerodha Varsity S&R; institutional traders use these levels.
- **Effort**: Low | **Impact**: Medium

### 7. Multi-Timeframe Alignment (Hourly)
- **Gap**: 15-min candles + daily EMA = two timeframes. Missing intermediate (hourly).
- **Fix**: Hourly EMA(9) > EMA(21) = intermediate uptrend. Add +1 when 15-min aligns, -1 when conflicts.
- **Source**: Professional traders use 3 timeframes (higher for direction, middle for setup, lower for entry).
- **Effort**: Medium | **Impact**: Medium

---

## LOWER PRIORITY — Nice to have

### 8. Bollinger Band Squeeze Detection
- **Gap**: No volatility-based entry signal.
- **Fix**: BB(20,2) bandwidth below historical avg → squeeze → impending breakout.
- **Source**: Popular request on Indian platforms. Zerodha's Karthik Rangappa calls BB a personal favorite for intraday.
- **Effort**: Medium | **Impact**: Medium-Low

### 9. ✅ Nifty Trend as Hard Filter
- **Gap**: NIFTY context sent to Claude as text, but Claude can still pick against-trend trades.
- **Fix**: NIFTY SuperTrend DOWN → require higher score (≥5) for BUY trades. UP → higher for SELL.
- **Source**: Institutional practice: trade with the broader market.
- **Effort**: Low | **Impact**: Medium-Low

---

## Implementation Status

| # | Improvement | Status | Implemented In |
|---|------------|--------|----------------|
| 1 | Volume confirmation | ✅ Done | `stock_scanner.py` |
| 2 | Relative Volume (RVol) | ✅ Done | `stock_scanner.py` |
| 3 | Pattern freshness decay | ✅ Done | `stock_scanner.py` |
| 4 | ORB strategy | ⬜ Pending | — |
| 5 | MACD histogram | ⬜ Pending | — |
| 6 | Previous day H/L/C S&R | ✅ Done | `stock_scanner.py` |
| 7 | Multi-timeframe (hourly) | ⬜ Pending | — |
| 8 | BB squeeze | ⬜ Pending | — |
| 9 | Nifty trend hard filter | ✅ Done | `stock_scanner.py` |
