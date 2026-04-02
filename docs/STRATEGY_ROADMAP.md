# Strategy Roadmap — All Versions

Research-backed improvements based on Investopedia, Zerodha Varsity, Toby Crabel (ORB), and institutional intraday practices.

**Version guide:**
- **V1** — Claude-only stock selection (retired, kept for comparison via `--v1`)
- **V2** — Math pre-filter + Claude selection (default: `python main.py --mode trade`)
- **V2 NoAI** — Same math pre-filter, zero Claude calls (`--noai`)

---

## COMPLETED — Already Implemented

### 1. ✅ Volume Confirmation for Candle Patterns
- **Versions**: V2, NoAI
- **Gap**: Candle patterns scored without volume check. A hammer on low volume is unreliable.
- **Fix**: If pattern candle volume > 1.5× 10-candle avg → strength ×1.3. If < 0.5× avg → strength ×0.5.
- **Source**: Investopedia + Zerodha Varsity: *"Volume spike on the pattern candle is the single most important confirmation."*

### 2. ✅ Relative Volume (RVol) as Stock Filter
- **Versions**: V2, NoAI
- **Gap**: Stocks filtered only by candle score, not by unusual activity.
- **Fix**: RVol = today's pro-rated volume / 5-day avg. RVol > 2.0 → +1 bonus. RVol < 0.3 → -1 penalty. ≥4 candles required for reliable pro-rating.
- **Source**: Day trading fundamentals — *Liquidity, Volatility, Volume* are the 3 essentials.

### 3. ✅ Pattern Freshness Decay
- **Versions**: V2, NoAI
- **Gap**: All patterns (current candle or 3 candles ago) score equally.
- **Fix**: Decay multiplier: current = 1.0×, 1 candle ago = 0.7×, 2 candles ago = 0.4×.
- **Source**: Investopedia: *"Pattern potency decreases rapidly 3-5 bars after completion."*

### 4. ✅ Previous Day H/L/C as Support/Resistance
- **Versions**: V2, NoAI
- **Gap**: VWAP is the only reference price. Previous day's H/L are natural S&R levels.
- **Fix**: Near prev high (within 0.5%) → resistance (-1). Near prev low → support (+1). Pivot = (H+L+C)/3.
- **Source**: "Daily Pivots" strategy; Zerodha Varsity S&R; institutional traders use these levels.

### 5. ✅ Nifty Trend as Hard Filter
- **Versions**: V2, NoAI
- **Gap**: NIFTY context sent to Claude as text, but Claude can still pick against-trend trades.
- **Fix**: NIFTY BEARISH → require |score| ≥ 3 for BUY trades. BULLISH → require ≥ 3 for SELL.
- **Source**: Institutional practice: trade with the broader market.

---

## HIGH PRIORITY — Next to Implement

### 6. ✅ Opening Range Breakout (ORB)
- **Versions**: V2, NoAI
- **Gap**: Bot observes first 15 min but doesn't capture OR high/low as reference levels.
- **Fix**: Record first 15-min candle H/L per stock. Break above OR high → +2 score. Below OR low → -2 score. Used as entry confirmation alongside existing indicators.
- **Source**: Toby Crabel's ORB — widely used by institutional intraday traders.

### 7. ✅ MACD Histogram Confirmation
- **Versions**: V2, NoAI
- **Gap**: Only EMA crossover used for momentum. MACD (12,26,9) absent.
- **Fix**: MACD histogram positive & growing → +1. Negative & shrinking → -1. Catches momentum divergences that EMA crossover misses.
- **Source**: Zerodha Varsity covers MACD as one of two most important indicators alongside RSI.

### 8. ✅ Sector Diversification Limit
- **Versions**: V2, NoAI (most impactful for NoAI which lacks Claude's natural diversification)
- **Gap**: NoAI can pick 5 correlated stocks from same sector. V2 relies on Claude to diversify (inconsistent).
- **Fix**: Tag each stock with sector. Max 2 positions per sector. Simple filter, no AI needed.
- **Source**: Portfolio theory — correlated positions amplify drawdown when a sector drops.

### 9. ✅ Pre-Market Gap Analysis
- **Versions**: V2, NoAI
- **Gap**: Gap between yesterday's close and today's open is a strong signal, but completely unused.
- **Fix**: Gap-up >1% + high RVol → +1 (continuation). Gap-up >1% + low RVol → -1 (gap fill likely). Same logic inverted for gap-down.
- **Source**: Gap analysis is one of the most basic yet powerful intraday techniques.

### 10. ✅ Partial Profit Taking (Scale-Out)
- **Versions**: V1 (retired), V2, NoAI
- **Gap**: All-or-nothing exits — full position until SL or target. Professional traders scale out.
- **Fix**: At 1× risk profit, exit 50% of qty and move SL to breakeven on remainder. Locks in guaranteed profit while letting winners run.
- **Source**: Universal risk management principle — "Take some off the table."

---

## MEDIUM PRIORITY — Proven, moderate effort

### 11. Multi-Timeframe Alignment (Hourly)
- **Versions**: V2, NoAI
- **Gap**: 15-min candles + daily EMA = two timeframes. Missing intermediate (hourly).
- **Fix**: Compute hourly EMA(9/21) from 15-min candles. All 3 aligned → +1. Conflict → -1.
- **Source**: Professional traders use 3 timeframes (higher for direction, middle for setup, lower for entry).
- **Effort**: Medium | **Impact**: Medium

### 12. Bollinger Band Squeeze Detection
- **Versions**: V2, NoAI
- **Gap**: No volatility-based entry signal.
- **Fix**: BB(20,2) bandwidth below historical avg → squeeze → impending breakout.
- **Source**: Popular on Indian platforms. Zerodha's Karthik Rangappa calls BB a personal favorite for intraday.
- **Effort**: Medium | **Impact**: Medium-Low

### 13. Volatility Regime Detection (India VIX)
- **Versions**: V1 (retired), V2, NoAI
- **Gap**: Every market day treated the same. Low-vol days and high-vol days need different strategies.
- **Fix**: Fetch India VIX at open. VIX < 13 → tighten targets, widen SL slightly. VIX > 22 → widen targets, reduce position size.
- **Source**: Institutional practice — volatility-adaptive position sizing.
- **Effort**: Medium | **Impact**: Medium

### 14. Backtesting Framework
- **Versions**: All (infrastructure)
- **Gap**: No way to measure which indicators actually contribute to winning trades. Flying blind.
- **Fix**: Replay V2 scoring on historical 15-min data, simulate ATR-based entries/exits, compute win rate per indicator combination.
- **Source**: Every professional quant desk backtests before going live.
- **Effort**: High | **Impact**: Highest (enables all other improvements to be measured)

### 15. Trade Journaling & Performance Analytics
- **Versions**: All (infrastructure)
- **Gap**: Daily reports exist but no systematic analysis of which patterns/indicators/times win.
- **Fix**: Write full indicator snapshot at entry to SQLite. Weekly script to compute stats: win rate by pattern, by time of day, by RVol bucket, by score range.
- **Source**: Professional trading discipline — data-driven parameter tuning.
- **Effort**: Medium | **Impact**: High

---

## Implementation Status

| # | Improvement | Versions | Status | Implemented In |
|---|------------|----------|--------|----------------|
| 1 | Volume confirmation | V2, NoAI | ✅ Done | `candle_patterns.py` |
| 2 | Relative Volume (RVol) | V2, NoAI | ✅ Done | `stock_scanner_v2.py` |
| 3 | Pattern freshness decay | V2, NoAI | ✅ Done | `candle_patterns.py` |
| 4 | Previous day H/L/C S&R | V2, NoAI | ✅ Done | `technical_indicators.py` |
| 5 | Nifty trend hard filter | V2, NoAI | ✅ Done | `stock_scanner_v2.py` |
| 6 | Opening Range Breakout | V2, NoAI | ✅ Done | `technical_indicators.py`, `stock_scanner_v2.py` |
| 7 | MACD histogram | V2, NoAI | ✅ Done | `technical_indicators.py` |
| 8 | Sector diversification | V2, NoAI | ✅ Done | `stock_scanner_v2.py` |
| 9 | Pre-market gap analysis | V2, NoAI | ✅ Done | `technical_indicators.py` |
| 10 | Partial profit taking | V1, V2, NoAI | ✅ Done | `order_engine.py` |
| 11 | Multi-timeframe (hourly) | V2, NoAI | ⬜ Pending | — |
| 12 | BB squeeze | V2, NoAI | ⬜ Pending | — |
| 13 | VIX-based sizing | All | ⬜ Pending | — |
| 14 | Backtesting framework | All | ⬜ Pending | — |
| 15 | Trade journaling | All | ⬜ Pending | — |
