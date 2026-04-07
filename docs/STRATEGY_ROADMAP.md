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

## HIGH PRIORITY — Intraday Capital Efficiency

### 11. ✅ Periodic Opportunity Scanning (Free Slots)
- **Versions**: V2, NoAI
- **Gap**: After the initial scan, the bot only looks for new trades when a position **closes** (SL/target hit). If it starts the day with 2 trades and 3 empty slots, it never proactively fills those slots. If a partial re-scan finds nothing, it doesn't try again later even though market conditions change.
- **Fix**: Every `OPPORTUNITY_RESCAN_MINUTES` (default 30 min), if `open_positions < MAX_POSITIONS` and sufficient time remains, run a fresh V2 scan for available slots. Independent of position close events. Capped to avoid API spam.
- **Source**: Professional day traders continuously scan for setups, not just at market open.

### 12. ✅ Continuous Market Regime Monitoring
- **Versions**: V2, NoAI
- **Gap**: NIFTY trend is checked once at pre-market scan and again only during re-scans. If the market drops 1% at open but recovers 0.5% by 11 AM, the bot still thinks it's BEARISH. Claude gets stale market context during reviews.
- **Fix**: Re-fetch NIFTY quote and update `_market_condition` every `NIFTY_RECHECK_MINUTES` (15 min). Pass updated market condition to Claude reviews and re-scans. Log regime changes: "Market shifted BEARISH → NEUTRAL at 11:15 AM".
- **Source**: Institutional traders track index continuously for regime shifts — the most basic edge.

### 13. ✅ Minimum Capital Deployment Guidance
- **Versions**: V2 (Claude path)
- **Gap**: Claude prompt tells the budget but doesn't enforce minimum deployment. Claude can pick 2 tiny positions using 30% of capital, leaving 70% idle all day.
- **Fix**: Add prompt guidance: "Deploy at least 60% of budget across your trades. If you pick 2 trades, each should use ~₹X. Unused capital earns nothing intraday." Also add a code-level fallback that increases qty if Claude under-sizes.
- **Source**: Capital efficiency — idle capital is a drag on returns. Even 0% return is a cost when capital is locked in the trading account.

---

## COMPLETED — Defensive Gaps (Risk Management)

### 14. ✅ Stagnant Position Exit (NoAI)
- **Versions**: V2 NoAI
- **Gap**: In NoAI mode, no Claude reviews exist. Positions that don't hit SL or target sit idle until square-off, burning slots that could hold better trades. Claude would notice "momentum faded, exit" but NoAI has no equivalent.
- **Fix**: After `STAGNANT_EXIT_MINUTES` (default 90) minutes, if price hasn't moved at least `STAGNANT_EXIT_MIN_MOVE_PCT` (0.3%) toward target, auto-exit and free the slot. Checked every review interval (25 min).
- **Source**: Professional day traders cut dead-weight positions. Time is a resource — idle capital earning 0% is an opportunity cost.

### 15. ✅ Loss-Adjusted Position Sizing
- **Versions**: V1, V2, NoAI
- **Gap**: After SL hits, `_budget` stays at the initial day's budget (₹20K). The bot re-scans and enters new trades at full size, not accounting for realised losses. In live mode, `refresh_budget()` catches this via Zerodha API, but in dry-run mode the budget never adjusts.
- **Fix**: `loss_adjusted_budget()` reduces effective budget by realised losses (floor at 20% of original). `budget_remaining()` now uses this. New trades are automatically smaller after losses. Enabled by `LOSS_SIZING_ENABLED` config (default: True).
- **Source**: Universal risk management — don't bet the same size after losing. Professional prop desks reduce size after drawdowns.

### 16. ✅ Circuit Breaker Cooldown
- **Versions**: V1, V2, NoAI
- **Gap**: When circuit breaker trips (3% daily loss), the bot squares off all positions and shuts down for the day. No recovery possible even if the market turns. The day is always over after a morning drawdown.
- **Fix**: After circuit breaker fires, wait `CIRCUIT_BREAKER_COOLDOWN_MINUTES` (default 30) then resume monitoring with reduced (loss-adjusted) budget. Resets the P&L baseline so only NEW losses after resume can re-trip the breaker. Only resumes if enough time remains before square-off. Set to 0 for old behaviour (day over).
- **Source**: Institutional trading desks have "timeout" periods after drawdowns rather than full shutdowns. Allows participation in afternoon reversals.

---

## MEDIUM PRIORITY — Proven, moderate effort

### 17. Multi-Timeframe Alignment (Hourly)
- **Versions**: V2, NoAI
- **Gap**: 15-min candles + daily EMA = two timeframes. Missing intermediate (hourly).
- **Fix**: Compute hourly EMA(9/21) from 15-min candles. All 3 aligned → +1. Conflict → -1.
- **Source**: Professional traders use 3 timeframes (higher for direction, middle for setup, lower for entry).
- **Effort**: Medium | **Impact**: Medium

### 18. Bollinger Band Squeeze Detection
- **Versions**: V2, NoAI
- **Gap**: No volatility-based entry signal.
- **Fix**: BB(20,2) bandwidth below historical avg → squeeze → impending breakout.
- **Source**: Popular on Indian platforms. Zerodha's Karthik Rangappa calls BB a personal favorite for intraday.
- **Effort**: Medium | **Impact**: Medium-Low

### 19. Volatility Regime Detection (India VIX)
- **Versions**: V1 (retired), V2, NoAI
- **Gap**: Every market day treated the same. Low-vol days and high-vol days need different strategies.
- **Fix**: Fetch India VIX at open. VIX < 13 → tighten targets, widen SL slightly. VIX > 22 → widen targets, reduce position size.
- **Source**: Institutional practice — volatility-adaptive position sizing.
- **Effort**: Medium | **Impact**: Medium

### 20. Backtesting Framework
- **Versions**: All (infrastructure)
- **Gap**: No way to measure which indicators actually contribute to winning trades. Flying blind.
- **Fix**: Replay V2 scoring on historical 15-min data, simulate ATR-based entries/exits, compute win rate per indicator combination.
- **Source**: Every professional quant desk backtests before going live.
- **Effort**: High | **Impact**: Highest (enables all other improvements to be measured)

### 21. Trade Journaling & Performance Analytics
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
| 11 | Periodic opportunity scan | V2, NoAI | ✅ Done | `manager_v2.py`, `config.py` |
| 12 | Continuous market regime | V2, NoAI | ✅ Done | `manager_v2.py` |
| 13 | Min capital deployment | V2 | ✅ Done | `stock_scanner_v2.py`, `order_engine.py` |
| 14 | Stagnant position exit | NoAI | ✅ Done | `order_engine.py`, `manager_v2.py` |
| 15 | Loss-adjusted sizing | All | ✅ Done | `order_engine.py`, `config.py` |
| 16 | Circuit breaker cooldown | All | ✅ Done | `manager.py`, `manager_v2.py`, `config.py` |
| 17 | Multi-timeframe (hourly) | V2, NoAI | ⬜ Pending | — |
| 18 | BB squeeze | V2, NoAI | ⬜ Pending | — |
| 19 | VIX-based sizing | All | ⬜ Pending | — |
| 20 | Backtesting framework | All | ⬜ Pending | — |
| 21 | Trade journaling | All | ⬜ Pending | — |
