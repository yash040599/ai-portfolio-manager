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

### 17. ✅ Multi-Timeframe Alignment (Hourly)
- **Versions**: V2, NoAI
- **Gap**: 15-min candles + daily EMA = two timeframes. Missing intermediate (hourly).
- **Fix**: Build synthetic hourly candles from 15-min data. Compute hourly EMA(9/21). When 15-min and hourly both agree → +1. Conflict → 0 (no conviction).
- **Source**: Professional traders use 3 timeframes (higher for direction, middle for setup, lower for entry).

### 18. ✅ Bollinger Band Squeeze Detection
- **Versions**: V2, NoAI
- **Gap**: No volatility-based entry signal.
- **Fix**: BB(20,2) bandwidth below 75% of rolling average → squeeze. Price above middle band → +1 (bullish breakout). Below → -1 (bearish breakout).
- **Source**: Popular on Indian platforms. Zerodha's Karthik Rangappa calls BB a personal favorite for intraday.

---

## COMPLETED — NoAI Adaptive Strategy (When Things Go Wrong)

### 19. ✅ Max Circuit Breaker Trips Per Day
- **Versions**: All
- **Gap**: Circuit breaker could trip → cooldown → resume → trip again indefinitely, grinding through capital on a bad day.
- **Fix**: `MAX_CIRCUIT_BREAKER_TRIPS` (default 2). After N trips, the day is over. Prevents infinite cooldown loops.
- **Source**: Risk management — cap exposure on systematically bad days.

### 20. ✅ Consecutive SL Pause (Whipsaw Guard)
- **Versions**: All
- **Gap**: 3 consecutive SL hits across different stocks means the signal set doesn't match today's market. Each loss is small but they compound through 8-10 trades.
- **Fix**: After `CONSECUTIVE_SL_PAUSE_COUNT` (default 3) consecutive SL hits, pause new entries for `CONSECUTIVE_SL_PAUSE_MINUTES` (default 30). Resets on any profitable close.
- **Source**: "Death by a thousand cuts" — professional day traders have whipsaw rules.

### 21. ✅ Dynamic Score Threshold After Losses
- **Versions**: V2 NoAI
- **Gap**: After losses, the bot still picks marginal +2.5 candidates. Quality gating between 0% and circuit breaker.
- **Fix**: When day loss exceeds `LOSS_SCORE_BUMP_PCT` (1.5% of budget), raise MIN_SCORE by `LOSS_SCORE_BUMP_AMOUNT` (1.5). Only higher-conviction setups after losses.
- **Source**: Institutional practice — tighten entry criteria after drawdowns.

### 22. ✅ Regime-Shift SL Tightening
- **Versions**: V2, NoAI
- **Gap**: When Nifty regime flips (BULLISH→BEARISH), existing LONG positions get no adjustment. A sharp reversal can wipe out the morning's gains across all positions simultaneously.
- **Fix**: On regime shift, positions contradicting the new regime: in profit → lock 50%, near breakeven → SL to entry. SL only moves in protective direction.
- **Source**: Institutional traders reduce exposure on regime changes rather than hoping for recovery.

---

## MEDIUM PRIORITY — Proven, moderate effort

### 23. Volatility Regime Detection (India VIX)
- **Versions**: V1 (retired), V2, NoAI
- **Gap**: Every market day treated the same. Low-vol days and high-vol days need different strategies.
- **Fix**: Fetch India VIX at open. VIX < 13 → tighten targets, widen SL slightly. VIX > 22 → widen targets, reduce position size.
- **Source**: Institutional practice — volatility-adaptive position sizing.
- **Effort**: Medium | **Impact**: Medium

### 24. Backtesting Framework
- **Versions**: All (infrastructure)
- **Gap**: No way to measure which indicators actually contribute to winning trades. Flying blind.
- **Fix**: Replay V2 scoring on historical 15-min data, simulate ATR-based entries/exits, compute win rate per indicator combination.
- **Source**: Every professional quant desk backtests before going live.
- **Effort**: High | **Impact**: Highest (enables all other improvements to be measured)

### 25. Trade Journaling & Performance Analytics
- **Versions**: All (infrastructure)
- **Gap**: Daily reports exist but no systematic analysis of which patterns/indicators/times win.
- **Fix**: Write full indicator snapshot at entry to SQLite. Weekly script to compute stats: win rate by pattern, by time of day, by RVol bucket, by score range.
- **Source**: Professional trading discipline — data-driven parameter tuning.
- **Effort**: Medium | **Impact**: High

---

## HIGH PRIORITY — Next Implementation Batch (Deep Review Findings)

### 26. ✅ Sector Cap Enforcement at Entry Time
- **Versions**: All
- **Gap**: Sector cap (max 2/sector) is only checked during pre-filter scan. Re-scans and opportunity scans can bypass this since they don't see existing open positions. 3 banking stocks could be open simultaneously.
- **Fix**: In `enter_trade()`, check the sector of the new stock against sectors of existing open positions. Reject if sector already has MAX_PER_SECTOR open positions.
- **Source**: Portfolio theory — correlated positions amplify drawdown when a sector drops.

### 27. ✅ End-of-Day Accelerated Exit (NoAI)
- **Versions**: NoAI, V2
- **Gap**: Claude prompt says "EXIT ALL underwater positions <30 min", but NoAI code doesn't enforce this. Losing positions sit until 15:10 square-off, risking slippage in low-liquidity closing minutes.
- **Fix**: After `EOD_EXIT_AFTER_HOUR:EOD_EXIT_AFTER_MINUTE` (default 14:45), auto-exit any position that is at a loss. Breakeven positions get SL tightened to entry - 0.1%.
- **Source**: Professional day trading — don't hold losers into the close.

### 28. ✅ ADX Trend Strength Filter
- **Versions**: V2, NoAI
- **Gap**: SuperTrend/EMA give whipsaw signals in range-bound markets. No way to distinguish trending vs ranging conditions per stock.
- **Fix**: ADX(14) on 15-min candles. ADX < 20: halve EMA crossover and SuperTrend continuation scores. ADX > 30: +0.5 trend-strength bonus. Feed ADX value to Claude snapshot.
- **Source**: ADX is the standard trend strength indicator. Avoids overtrading in choppy conditions.

### 29. ✅ Thursday F&O Expiry-Day Handling
- **Versions**: All
- **Gap**: Thursday is weekly F&O expiry — wider intraday swings. Claude prompt mentions this but code doesn't adjust parameters.
- **Fix**: On Thursdays: widen ATR_MULTIPLIER by +0.3 (wider SLs), reduce MAX_POSITIONS by 1, raise V2_MIN_SCORE by +0.5. Applied dynamically at start of trading day.
- **Source**: NSE F&O expiry drives massive intraday volatility, especially in banking stocks.

### 30. ✅ Increase Candle Lookback to 3 Days
- **Versions**: V2, NoAI
- **Gap**: MACD(12,26,9) needs 35+ candle warmup. 2-day lookback gives ~50 candles, only 15 real MACD values after warmup. Bollinger Bands similarly marginal.
- **Fix**: Increase `days_back` from 2 to 3 for 15-min candle fetches. Gives ~75 candles — enough for all indicators to stabilize.
- **Source**: Technical analysis data requirements — indicators need sufficient warm-up.

### 31. ✅ Today-Candle-Count Guard for Early Scans
- **Versions**: V2, NoAI
- **Gap**: At 9:30 AM (1 today candle), VWAP is computed on a single candle (meaningless), ORB can't detect breakouts yet, RVol is unreliable. Indicators silently produce garbage.
- **Fix**: In `compute_technical_score()`, suppress ORB and gap scores when fewer than 3 today candles exist. Let time-insensitive indicators (EMA, RSI, SuperTrend) still contribute.
- **Source**: Data quality — indicators need minimum data to be meaningful.

### 32. ✅ Late-Entry Target Reduction
- **Versions**: All
- **Gap**: A trade at 2 PM gets the same ATR target as one at 9:30 AM, but has only 70 min for target to hit vs 340 min. Probability of hitting target is dramatically lower.
- **Fix**: Apply target reduction at entry time (not just existing time-decay on open positions). After 1 PM: reduce target by 20%. After 2 PM: reduce by 35%.
- **Source**: Time-value decay in intraday positions — targets must be realistic for remaining time.

---

## FUTURE — Remaining Deep Review Findings

### 33. ✅ Fibonacci Retracement Levels
- **Versions**: V2, NoAI
- **Gap**: Only uses previous day H/L and VWAP as reference levels. Fibonacci retracements (38.2%, 50%, 61.8%) of the day's range provide additional support/resistance.
- **Fix**: Compute day's high-low Fib levels, add +0.5 (unsigned) when price is near a level. Feed Fib level proximity to Claude snapshot.
- **Effort**: Low | **Impact**: Medium

### 34. ✅ Volume Profile / VWAP Standard Deviation Bands
- **Versions**: V2, NoAI
- **Gap**: VWAP is used as a single line. Institutional traders also watch ±1σ and ±2σ bands — price touching -2σ VWAP band is a much stronger buy signal than simply "below VWAP".
- **Fix**: Compute VWAP SD bands. Price at ±2σ → ±1 score bonus. Price between ±1-2σ → ±0.5.
- **Effort**: Medium | **Impact**: Medium

### 35. ✅ Order Book Depth / Bid-Ask Spread Check
- **Versions**: All
- **Gap**: Entry assumes tight spreads. Illiquid stocks can have wide bid-ask (0.5-1%), eating into the already-tight ATR target.
- **Fix**: Before placing an order, check top 5 bid-ask levels via Zerodha's depth data. Skip stocks with spread > 0.3%.
- **Effort**: Low | **Impact**: Medium

### 36. Intraday Momentum Score (Rate of Change)
- **Versions**: V2, NoAI
- **Gap**: Score is a snapshot at scan time. A stock scored +8 might already be decelerating. Rate of change (RoC) of score over 2-3 scans would detect momentum fade early.
- **Fix**: Cache previous scan scores in memory. Compute delta_score. Penalize entries where score is falling fast.
- **Effort**: Medium | **Impact**: Medium

### 37. Correlation-Based Position Sizing
- **Versions**: All
- **Gap**: Sector cap prevents 3+ stocks in one sector, but 2 highly-correlated stocks (e.g. HDFCBANK + ICICIBANK) still act as a single position during sector drops.
- **Fix**: Track intraday correlation between open positions. If new entry has >0.7 correlation with an existing position, reduce qty by 50%.
- **Effort**: High | **Impact**: Medium

### 38. ✅ Improved Slippage Model for Dry Run
- **Versions**: Dry Run
- **Gap**: Fixed 0.15% slippage doesn't reflect real-world patterns. Slippage is higher at open (0.3-0.5%), near market close (0.2%), and for illiquid stocks.
- **Fix**: Time-of-day adjusted slippage: opening hour ×2, last hour ×1.5. Applied on both entry and exit.
- **Effort**: Low | **Impact**: Low (only affects dry-run realism)

### 39. ATR Percentile Ranking
- **Versions**: V2, NoAI
- **Gap**: ATR-based SL uses absolute value but doesn't consider how today's ATR compares to the stock's typical range. Unusually high ATR means SL may be too wide for intraday.
- **Fix**: Rank today's 15-min ATR against a 10-day lookback. If ATR is >80th percentile, cap SL tighter. If <20th, allow wider SL (quiet day).
- **Effort**: Medium | **Impact**: Medium

### 40. Claude Prompt Feedback Loop
- **Versions**: V2
- **Gap**: Claude doesn't know its historical accuracy. No feedback on which of its past picks won/lost.
- **Fix**: Prepend last 5-day win rate and common failure modes to Claude prompt: "Your last 5 days: 12W/8L (60%). Main failure: positions opened at already-extended prices."
- **Effort**: Medium | **Impact**: High

### 41. Automatic Holiday-Shifted Expiry Detection
- **Versions**: All
- **Gap**: Thursday expiry detection checks `weekday == 3` but when Thursday is an NSE holiday, expiry shifts to Wednesday. Misses ~2-3 days/year.
- **Fix**: Maintain a list of actual expiry dates (from NSE published calendar) alongside the holiday list. Fall back to Thursday check if list is empty.
- **Effort**: Low | **Impact**: Low (edge case)

### 42. Pre-Open Auction Data
- **Versions**: V2, NoAI
- **Gap**: Scan happens after market open. The 9:00-9:08 pre-open auction gives indicative open price, volume, and order imbalance — valuable signal for gap analysis before first candle forms.
- **Fix**: Fetch pre-open snapshot from Zerodha at 9:08 AM. Use indicative open vs prev close for early gap detection and bias.
- **Effort**: Medium | **Impact**: Medium

### 43. ✅ Real-Time Trade Verification Script
- **Versions**: All (infrastructure)
- **Gap**: Trade verification currently requires downloading Zerodha Tax P&L xlsx the next day. No same-day verification using Zerodha's live API.
- **Fix**: Script that fetches `kite.trades()` and `kite.positions()` after market close, cross-references with internal data, and marks trades as verified.
- **Effort**: Medium | **Impact**: High (eliminates manual xlsx download step)

### 44. WebSocket Tick Data for Faster SL/Target Execution
- **Versions**: All
- **Gap**: 10-second polling can miss rapid SL/target breaches. A stock can gap through SL in seconds during news events.
- **Fix**: Use Zerodha WebSocket (up to 3000 instruments) for real-time tick data on open position symbols. SL/target checks on every tick instead of every poll.
- **Effort**: High | **Impact**: High (faster execution, less slippage)

### 45. Multi-Day Score Trend
- **Versions**: V2, NoAI
- **Gap**: Score is computed only on today's candles. A stock that was +7 yesterday and +6 today is a sustained trend; +6 today from -2 yesterday is a fresh reversal. Different conviction levels.
- **Fix**: Cache daily scanner scores in DB. Compare today's pre-market score with yesterday's closing score. Sustained-trend bonus +0.5, fresh-reversal no adjustment.
- **Effort**: Medium | **Impact**: Medium

### 46. Smart Square-Off Timing
- **Versions**: All
- **Gap**: Fixed 3:10 PM square-off regardless of market conditions. On trending days, holding 5 more minutes captures more profit. On choppy days, earlier exit avoids EOD chop.
- **Fix**: Adaptive square-off: if portfolio is profitable and trend is intact (ADX>25, positions in-profit), delay to 3:15. If losing and trend fading, advance to 3:05.
- **Effort**: Medium | **Impact**: Medium

### 47. Budget Auto-Scaling Based on Win Rate
- **Versions**: All
- **Gap**: Budget is fixed at MAX_BUDGET_INR. After a winning streak, the bot should deploy more; after losses, less.
- **Fix**: Track 5-day rolling win rate. >65% → allow budget ×1.2 (max 120% of MAX_BUDGET_INR). <40% → cap at 80%. Resets weekly.
- **Effort**: Medium | **Impact**: Medium

### 48. Zerodha API Calls Real-Time Verification
- **Versions**: All (infrastructure)
- **Gap**: No same-day verification using Zerodha API — relies on next-day xlsx download.
- **Note**: Merged with #43 (duplicate). See #43 for implementation plan.
- **Status**: Duplicate → #43

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
| 17 | Multi-timeframe (hourly) | V2, NoAI | ✅ Done | `technical_indicators.py` |
| 18 | BB squeeze | V2, NoAI | ✅ Done | `technical_indicators.py` |
| 19 | Max CB trips per day | All | ✅ Done | `order_engine.py`, `config.py` |
| 20 | Consecutive SL pause | All | ✅ Done | `order_engine.py`, `manager_v2.py` |
| 21 | Dynamic score after losses | NoAI | ✅ Done | `stock_scanner_v2.py`, `config.py` |
| 22 | Regime-shift SL tightening | V2, NoAI | ✅ Done | `manager_v2.py` |
| 23 | VIX-based sizing | All | ⬜ Pending | — |
| 24 | Backtesting framework | All | ⬜ Pending | — |
| 25 | Trade journaling | All | ⬜ Pending | — |
| 26 | Sector cap at entry time | All | ✅ Done | `order_engine.py` |
| 27 | EOD accelerated exit | NoAI, V2 | ✅ Done | `order_engine.py`, `manager_v2.py`, `config.py` |
| 28 | ADX trend strength | V2, NoAI | ✅ Done | `technical_indicators.py` |
| 29 | Thursday expiry handling | All | ✅ Done | `manager.py`, `config.py` |
| 30 | 3-day candle lookback | V2, NoAI | ✅ Done | `stock_scanner_v2.py` |
| 31 | Today-candle-count guard | V2, NoAI | ✅ Done | `technical_indicators.py` |
| 32 | Late-entry target reduction | All | ✅ Done | `order_engine.py`, `config.py` |
| 33 | Fibonacci retracement levels | V2, NoAI | ✅ Done | `technical_indicators.py`, `stock_scanner_v2.py` |
| 34 | VWAP SD bands | V2, NoAI | ✅ Done | `technical_indicators.py`, `stock_scanner_v2.py` |
| 35 | Bid-ask spread check | All | ✅ Done | `order_engine.py`, `config.py` |
| 36 | Intraday momentum (RoC) | V2, NoAI | ⬜ Pending | — |
| 37 | Correlation-based sizing | All | ⬜ Pending | — |
| 38 | Improved slippage model | Dry Run | ✅ Done | `order_engine.py` |
| 39 | ATR percentile ranking | V2, NoAI | ⬜ Pending | — |
| 40 | Claude prompt feedback loop | V2 | ⬜ Pending | — |
| 41 | Holiday-shifted expiry | All | ⬜ Pending | — |
| 42 | Pre-open auction data | V2, NoAI | ⬜ Pending | — |
| 43 | Real-time trade verification | All | ✅ Done | `scripts/verify_trades.py` |
| 44 | WebSocket tick data | All | ⬜ Pending | — |
| 45 | Multi-day score trend | V2, NoAI | ⬜ Pending | — |
| 46 | Smart square-off timing | All | ⬜ Pending | — |
| 47 | Budget auto-scaling | All | ⬜ Pending | — |
