# Strategy Roadmap — All Versions

Research-backed improvements based on Investopedia, Zerodha Varsity, Toby Crabel (ORB), and institutional intraday practices.

**Version guide:**
- **V1** — Claude-only stock selection (retired, kept for comparison via `--v1`)
- **V2 NoAI** — Math pre-filter + auto-select by score, zero Claude calls (default: `python main.py --mode trade`)
- **V2 + Claude** — Same math pre-filter, Claude selects from candidates (`--ai`)

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
- **Fix**: At 1.5× risk profit, exit 33% of qty (1/3) and move SL to breakeven on remainder. Trail remainder at 50% step. Locks in guaranteed profit while letting winners run. Min qty threshold: 3.
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
- **Fix**: Add prompt guidance: "Deploy at least 60% of budget across your trades. If you pick 2 trades, each should use ~Rs.X. Unused capital earns nothing intraday." Also add a code-level fallback that increases qty if Claude under-sizes.
- **Source**: Capital efficiency — idle capital is a drag on returns. Even 0% return is a cost when capital is locked in the trading account.

---

## COMPLETED — Defensive Gaps (Risk Management)

### 14. ✅ Stagnant Position Exit (NoAI)
- **Versions**: V2 NoAI
- **Gap**: In NoAI mode, no Claude reviews exist. Positions that don't hit SL or target sit idle until square-off, burning slots that could hold better trades. Claude would notice "momentum faded, exit" but NoAI has no equivalent.
- **Fix**: After `STAGNANT_EXIT_MINUTES` (default 45) minutes, if price hasn't moved at least `STAGNANT_EXIT_MIN_MOVE_PCT` (0.5%) toward target, auto-exit and free the slot. Checked every review interval (25 min).
- **Source**: Professional day traders cut dead-weight positions. Time is a resource — idle capital earning 0% is an opportunity cost.

### 15. ✅ Loss-Adjusted Position Sizing
- **Versions**: V1, V2, NoAI
- **Gap**: After SL hits, `_budget` stays at the initial day's budget (Rs.20K). The bot re-scans and enters new trades at full size, not accounting for realised losses. In live mode, `refresh_budget()` catches this via Zerodha API, but in dry-run mode the budget never adjusts.
- **Fix**: `loss_adjusted_budget()` reduces effective budget by realised losses (floor at 20% of original). `budget_remaining()` now uses this. New trades are automatically smaller after losses. Enabled by `LOSS_SIZING_ENABLED` config (default: True).
- **Source**: Universal risk management — don't bet the same size after losing. Professional prop desks reduce size after drawdowns.

### 16. ✅ Circuit Breaker Cooldown
- **Versions**: V1, V2, NoAI
- **Gap**: When circuit breaker trips (3% daily loss), the bot squares off all positions and shuts down for the day. No recovery possible even if the market turns. The day is always over after a morning drawdown.
- **Fix**: After circuit breaker fires, wait `CIRCUIT_BREAKER_COOLDOWN_MINUTES` (default 30) then resume monitoring with reduced (loss-adjusted) budget. Resets the P&L baseline so only NEW losses after resume can re-trip the breaker. Only resumes if enough time remains before square-off. Set to 0 for old behaviour (day over).
- **Source**: Institutional trading desks have "timeout" periods after drawdowns rather than full shutdowns. Allows participation in afternoon reversals.

---

## COMPLETED — Indicator Enhancements

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

## MEDIUM PRIORITY — Data-Driven Optimization

### 23. ✅ Volatility Regime Detection (India VIX)
- **Versions**: All
- **Gap**: Every market day treated the same. Low-vol days and high-vol days need different strategies.
- **Fix**: Fetch India VIX via Zerodha at startup and during periodic NIFTY rechecks. VIX ≥ 20 → reduce MAX_POSITIONS by 1, raise V2_MIN_SCORE by 1.0 (high fear, fewer/stronger trades). VIX ≤ 12 → informational (breakout-friendly). Intraday VIX spike ≥ 10% from open → pause new entries and log warning. VIX data feeds into NIFTY context string for Claude/NoAI prompts.
- **Source**: Institutional practice — volatility-adaptive position sizing.
- **Files**: `config.py`, `manager.py`

### 24. Backtesting Framework
- **Versions**: All (infrastructure)
- **Priority**: MEDIUM (moved to V3 in IDEATIONS.md)
- **Gap**: No way to measure which indicators actually contribute to winning trades. Flying blind.
- **Fix**: Replay V2 scoring on historical 15-min data, simulate ATR-based entries/exits, compute win rate per indicator combination.
- **Source**: Every professional quant desk backtests before going live.
- **Effort**: High | **Impact**: Highest (enables all other improvements to be measured)

### 25. ✅ Trade Journaling & Performance Analytics
- **Versions**: All (infrastructure)
- **Gap**: Daily reports exist but no systematic analysis of which patterns/indicators/times win.
- **Fix**: Write full indicator snapshot at entry to SQLite. `view_performance.py` script for analytics: daily P&L, win rate, exit reason stats, side breakdown, indicator correlation.
- **Status**: Done — indicator snapshot recorded to SQLite via `performance_tracker.py`. Analytics viewer: `scripts/view_performance.py` (--days, --date, --summary flags).
- **Source**: Professional trading discipline — data-driven parameter tuning.
- **Effort**: Medium | **Impact**: High

---

## COMPLETED — Deep Review Entry & Timing Fixes

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

## PENDING — Execution & Indicator Improvements

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

### 37. ~~Correlation-Based Position Sizing~~ *(Removed)*
- **Reason**: Redundant — sector cap (#8, #26) + direction diversification (#53, #76) + max 3 positions (#54) already prevent correlated drawdowns. Adding intraday correlation tracking adds complexity for negligible benefit at this position count.

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
- **Versions**: V2 (`--ai` mode)
- **Priority**: LOW (only applies to --ai mode, which is no longer default)
- **Gap**: Claude doesn't know its historical accuracy. No feedback on which of its past picks won/lost.
- **Fix**: Prepend last 5-day win rate and common failure modes to Claude prompt: "Your last 5 days: 12W/8L (60%). Main failure: positions opened at already-extended prices."
- **Effort**: Medium | **Impact**: High

### 41. Automatic Holiday-Shifted Expiry Detection
- **Versions**: All
- **Gap**: Thursday expiry detection checks `weekday == 3` but when Thursday is an NSE holiday, expiry shifts to Wednesday. Misses ~2-3 days/year.
- **Fix**: Maintain a list of actual expiry dates (from NSE published calendar) alongside the holiday list. Fall back to Thursday check if list is empty.
- **Effort**: Low | **Impact**: Low (edge case)

### 42. ✅ Pre-Open Auction Data
- **Versions**: V2, NoAI
- **Gap**: Scan happens after market open. The 9:00-9:08 pre-open auction gives indicative open price, volume, and order imbalance — valuable signal for gap analysis before first candle forms.
- **Fix**: Fetch quotes for full universe at startup (post 9:08 when equilibrium prices are set). Compute gap% from prev close for every stock. Log stocks with significant gaps (≥PREOPEN_GAP_SIGNIFICANT_PCT). Feed significant gap data into NIFTY context string so Claude/NoAI scans see which stocks have institutional pre-open interest.
- **Files**: `config.py`, `manager.py`

### 43. ✅ Real-Time Trade Verification Script
- **Versions**: All (infrastructure)
- **Gap**: Trade verification currently requires downloading Zerodha Tax P&L xlsx the next day. No same-day verification using Zerodha's live API.
- **Fix**: Script that fetches `kite.trades()` and `kite.positions()` after market close, cross-references with internal data, and marks trades as verified.
- **Effort**: Medium | **Impact**: High (eliminates manual xlsx download step)

### 44. WebSocket Tick Data for Faster SL/Target Execution
- **Versions**: All
- **Priority**: MEDIUM (implement when polling latency causes real slippage)
- **Gap**: 10-second polling can miss rapid SL/target breaches. A stock can gap through SL in seconds during news events.
- **Fix**: Use Zerodha WebSocket (up to 3000 instruments) for real-time tick data on open position symbols. SL/target checks on every tick instead of every poll.
- **Effort**: High | **Impact**: High (faster execution, less slippage)

### 45. Multi-Day Score Trend
- **Versions**: V2, NoAI
- **Gap**: Score is computed only on today's candles. A stock that was +7 yesterday and +6 today is a sustained trend; +6 today from -2 yesterday is a fresh reversal. Different conviction levels.
- **Fix**: Cache daily scanner scores in DB. Compare today's pre-market score with yesterday's closing score. Sustained-trend bonus +0.5, fresh-reversal no adjustment.
- **Effort**: Medium | **Impact**: Medium

### 46. ~~Smart Square-Off Timing~~ *(Removed)*
- **Reason**: Marginal ±5 min difference. EOD accelerated exit (#27) already exits losers early. Risk of holding past close outweighs benefit of 5 extra minutes.

### 47. ~~Budget Auto-Scaling Based on Win Rate~~ *(Removed)*
- **Reason**: Over-engineering — loss-adjusted sizing (#15) already reduces budget after losses. Dynamic position sizing (#58) scales with available capital. Adding win-rate scaling on top adds complexity for minimal incremental benefit.

---

## COMPLETED — Profitability Deep Review Fixes (Loss Analysis)

These fixes were identified by analyzing 8 days of actual trade data showing -Rs.897 cumulative losses. Root causes: late entries chasing extended moves, SL too tight, targets unreachable, winners cut too early, too many small trades eating charges.

### 49. ✅ ATR-Only SL/Target (Pure ATR Mode)
- **Versions**: All
- **Gap**: Original code MERGED ATR SL with config SL ("wider of both"). When config SL (1.5%) > config target (1.2%), the merge always picked the wider SL from config + tighter target from config → 0.6-0.8:1 R:R → every trade rejected.
- **Fix**: When ATR is available, use ATR SL and ATR target directly. Config defaults (`DEFAULT_STOP_LOSS_PCT`, `DEFAULT_TARGET_PCT`) are pure fallbacks for when ATR candle data is unavailable. Pure ATR always gives `TARGET_RR_MULTIPLIER` (1.5:1) R:R.
- **Decision history**: V1 used tighter-of → too tight. #49 switched to wider-of → bad R:R. Now pure ATR (2026-04-10).
- **Files**: `order_engine.py`

### 50. ✅ Late-Entry + Time-Decay Mutual Exclusion
- **Versions**: All
- **Gap**: A trade entered at 2 PM got BOTH a late-entry target reduction AND time-decay reduction during monitoring. Double penalty made targets unreachable.
- **Fix**: Late-entry reduction marks `_late_entry_reduced=True`. Time-decay skips positions already reduced. Added R:R floor check — if R:R < 1.2:1 after late reduction, trade is skipped entirely.
- **Files**: `order_engine.py`

### 51. ✅ Extended Move Score Penalty
- **Versions**: V2, NoAI
- **Gap**: Bot entered stocks that had already moved 1-3% from today's open. These are chasing, not trading — the move is already done.
- **Fix**: In `compute_technical_score()`, calculate extended move % from today's open. |move| > 2%: penalty ±3.0. |move| 1.5-2%: penalty ±1.5. Penalty opposes the direction (penalizes entering in the direction of an already-exhausted move).
- **Files**: `technical_indicators.py`, `stock_scanner_v2.py` (Claude prompt warning)

### 52. ✅ RSI Extreme Hard Cap on Composite Score
- **Versions**: V2, NoAI
- **Gap**: RSI ≥ 75 (overbought) with strong trend indicators could still produce a high positive score, encouraging BUY entries at tops. Trend indicators overrode the mean-reversion signal.
- **Fix**: If RSI ≥ 75: cap composite score at +3 max. If RSI ≤ 25: cap at -3 min. Prevents trend indicators from overriding extreme overbought/oversold readings.
- **Files**: `technical_indicators.py`

### 53. ✅ Direction Diversification Cap (Score-Aware)
- **Versions**: All
- **Gap**: All positions could be in the same direction (all BUY or all SELL). A single market reversal wipes them all out simultaneously.
- **Fix**: Smart direction allocation: if score gap ≥3 between best BUY/SELL candidates, dominant direction gets all MAX_POSITIONS slots (don't force weak counter-trend trades). score < 5 → normal limit (MAX_POSITIONS - 1). scan_noai pre-filters candidates by direction before building trade plans.
- **Files**: `order_engine.py`, `stock_scanner_v2.py`

### 54. ✅ Fewer Trades, Bigger Size Config
- **Versions**: All
- **Gap**: MAX_POSITIONS=5 with Rs.18K budget = Rs.3.6K per trade. Transaction costs (brokerage, STT, GST) are ~Rs.25-30 per trade, eating 0.7-0.8% per round trip on small positions.
- **Fix**: MAX_POSITIONS 5→3. MIN_BUDGET_UTILISATION_PCT 60→0 (disabled — idle capital is better than forced trades). Concentrates capital into fewer, higher-conviction trades.
- **Files**: `config.py`

### 55. LIMIT Orders for Entry/Exit
- **Versions**: All
- **Priority**: MEDIUM (implement when slippage data confirms need)
- **Gap**: MARKET orders cause adverse fills (Rs.20-40/day slippage on Rs.18K budget). In liquid NSE stocks, LIMIT at LTP should fill within seconds.
- **Fix**: Change `place_order()` calls from `order_type="MARKET"` to `order_type="LIMIT"` with `price=ltp`. Add a 5-10s fill check; if not filled, cancel and retry at updated LTP. Fall back to MARKET after 2 LIMIT failures.
- **Files**: `order_engine.py`, `zerodha_client.py`

### 56. Scan Universe Price Filter
- **Versions**: V2, NoAI
- **Priority**: MEDIUM
- **Gap**: No guard against very low-price (Rs.10-50) or very high-price (Rs.3000+) stocks. Low-price stocks have high % spreads. High-price stocks need too much capital for proper sizing.
- **Fix**: Add MIN_STOCK_PRICE (default Rs.100) and MAX_STOCK_PRICE (default Rs.800) config. Filter during scan phase — skip stocks outside range.
- **Files**: `config.py`, `stock_scanner_v2.py`

### 57. ~~VWAP Exclude Incomplete Candle~~ *(Removed)*
- **Reason**: Negligible impact. The last incomplete candle contributes a tiny fraction of the cumulative VWAP calculation. VWAP SD bands (#34) already smooth out noise.

### 58. ✅ Dynamic Position Sizing by Budget
- **Versions**: All
- **Priority**: Done
- **Gap**: With Rs.18-20K budget, even 3 positions = Rs.6K each. Transaction costs still eat 0.4-0.5% per round trip.
- **Fix**: `dynamic_max_positions()` classmethod auto-scales with budget: <Rs.25K→2, 25-60K→3, 60-1L→4, >1L→5. Called from `set_budget()` at startup. `MAX_POSITIONS_OVERRIDE` for manual lock.
- **Files**: `config.py`, `order_engine.py`

---

## COMPLETED — Financial Audit Fixes (April 2026)

Bugs identified during expert code review. All fixed in commit that added this section.

### 69. ✅ SL Sanity Check After Entry Price Override
- **Versions**: All
- **Priority**: CRITICAL (was)
- **Gap**: When Claude's entry price is overridden to live Zerodha quote (>5% deviation), the SL calculated by Claude for the old entry could end up on the WRONG side of the new entry (e.g. SL above entry for a BUY). If ATR was unavailable, this invalid SL was used.
- **Fix**: After all SL calculations, validate SL is on correct side of entry. If BUY and SL >= entry, or SELL and SL <= entry → reset to DEFAULT_STOP_LOSS_PCT with proper R:R target.
- **Files**: `order_engine.py`

### 70. ✅ SL-M Partial Fill Verification
- **Versions**: All (live mode)
- **Priority**: CRITICAL (was)
- **Gap**: When SL-M triggers on exchange, code assumed full qty was filled. SL-M can partially fill if insufficient liquidity. Remaining shares would be UNPROTECTED.
- **Fix**: Query Zerodha for actual filled qty via `get_order_filled_qty()`. If partial fill, place MARKET exit for remaining shares immediately.
- **Files**: `order_engine.py`, `zerodha_client.py`

### 71. ✅ Fill Price Scaling Re-validates SL Cap
- **Versions**: All (live mode)
- **Priority**: HIGH (was)
- **Gap**: After order fills at a different price, SL/target are scaled proportionally. This scaling could push SL beyond MAX_INTRADAY_SL_PCT (e.g. from 2.5% to 2.7%).
- **Fix**: After proportional scaling, re-check SL distance %. If it exceeds MAX_INTRADAY_SL_PCT, re-cap SL and recalculate target.
- **Files**: `order_engine.py`

### 72. ✅ Store Initial SL at Entry Time
- **Versions**: All
- **Priority**: MEDIUM (was)
- **Gap**: `initial_sl` was set on first call to `_auto_trail_stop()`, not at entry. If Claude adjusted SL between entry and first trail event, trailing risk baseline was wrong (used the adjusted SL, not the original entry SL).
- **Fix**: Store `initial_sl` in position dict at entry time in `enter_trade()`.
- **Files**: `order_engine.py`

### 73. ✅ Fallback Candidate Pool
- **Versions**: All (NoAI primary beneficiary)
- **Gap**: `scan_noai` selected exactly N candidates. If any failed entry sanity checks (R:R too low after late-entry reduction, min profit, etc.), that slot was wasted — "selected 1, entered 0".
- **Fix**: Return primary picks + up to 5 fallback candidates. `_enter_positions` tries each in score order and stops when MAX_POSITIONS slots are full. Budget validation only on primary; fallbacks use per-slot sizing with dynamic budget enforcement in `enter_trade`.
- **Files**: `stock_scanner_v2.py`, `manager.py`, `manager_v2.py`

### 74. ✅ Periodic Manual Trade Sync
- **Versions**: All
- **Gap**: Manual MIS positions opened on Zerodha app weren't detected until the next full re-scan (~30 min). Meanwhile, they had no SL protection from the bot.
- **Fix**: External position sync every 15 min (aligned with candle rescan) in both V1 and V2 monitor loops. Runs BEFORE quote fetch so adopted positions immediately get SL/target monitoring. Adopted positions get ATR-based SL/targets.
- **Files**: `manager.py`, `manager_v2.py`

### 75. ✅ --max Budget CLI Flag
- **Versions**: All
- **Gap**: Budget was fixed at MAX_BUDGET_INR in config.py. No way to cap today's exposure without editing code.
- **Fix**: `--max 30000` (or `30_000` / `30,000`) overrides MAX_BUDGET_INR for the session. Validated as positive integer.
- **Files**: `main.py`

### 76. ✅ Smart Direction Diversification (Score-Aware)
- **Versions**: All (NoAI scanner + shared enter_trade guard)
- **Gap**: Fixed direction cap (MAX_POSITIONS - 1) forced counter-trend trades on strongly trending days. E.g. KOTAKBANK not entered because BUY slot was full, despite being the best setup.
- **Fix**: scan_noai compares best BUY vs SELL score. Gap ≥ 3 → dominant direction gets all slots. enter_trade: score ≥ 5 bypasses limit (safety net). Gap < 3 → normal N−1 limit for diversification.
- **Files**: `order_engine.py`, `stock_scanner_v2.py`

### 77. ✅ Entry Count Logging Fix
- **Versions**: All
- **Gap**: `_enter_positions` logged "Entered N positions" based on `len(open_positions())` which included pre-existing positions. Showed "Entered 1" when 0 were actually entered.
- **Fix**: Count `enter_trade()` return values instead.
- **Files**: `manager.py`

### 78. ✅ FII/DII Flow Bias (Pre-Market Intelligence)
- **Versions**: All
- **Gap**: No institutional flow context. FII and DII net buy/sell data is a strong indicator of institutional sentiment — the biggest money movers in Indian markets.
- **Fix**: Fetch previous day's FII/DII data from NSE at startup. Classify as BULLISH (both buying), BEARISH (both selling), or MIXED. Feed into NIFTY context string for Claude/NoAI scans. Graceful fallback if NSE blocks request — no impact on trading.
- **Files**: `config.py`, `manager.py`

---

## COMPLETED — Tax Infrastructure Fixes (April 2026)

### 79. ✅ Per-Trade Charge Calculation (Tax Ledger)
- **Versions**: All (infrastructure)
- **Gap**: `per_trade_charges()` in `fill_intraday_ledger.py` apportioned the day-level charge total across all trades by turnover share. When EXTERNAL positions inflated the day-level turnover, the apportionment silently gave wrong per-component breakdowns (brokerage, STT etc).
- **Fix**: Replaced apportionment with a direct `Config.calculate_charges(buy_val, sell_val, num_orders=2)` call per trade. Each trade now has its own mathematically correct charge breakdown.
- **Files**: `fill_intraday_ledger.py`

### 80. ✅ EXTERNAL Position Unique order_id
- **Versions**: All (infrastructure)
- **Gap**: Multiple external (user-entered) positions on the same day all got `order_id="EXTERNAL"`. The dedup check `WHERE date=? AND order_id=?` only inserted the first one and silently skipped the rest.
- **Fix**: Generate a deterministic unique ID per external position: `EXT_{date}_{symbol}_{side}_{counter}`. Counter resets per (date, symbol, side) group so re-runs are idempotent.
- **Files**: `fill_intraday_ledger.py`

### 81. ✅ Sheet Import Updates Charges on P&L Match
- **Versions**: All (infrastructure)
- **Gap**: `_verify_intraday()` only set `verified='verified'` when P&L matched. It did NOT update the charge breakdown. Our estimated charges remained in the DB instead of Zerodha's actuals.
- **Fix**: On P&L match, aggregate Zerodha's per-trade charges for the (date, symbol) group, apportion to each DB row by turnover share, and update all charge columns + net_pnl + sheet_verified.
- **Files**: `import_zerodha_taxpnl.py`

---

## COMPLETED — V2 Review Cycle (Performance Tuning, April 2026)

Identified by deep code review against industry standards for candle-based intraday trading. Analyzed 63 trades over 9 days (Rs.-585 total P&L, 48% win rate, 1.05:1 win:loss ratio). Root causes: targets too ambitious (1.5%), Claude exits too early, winners not filtering strong enough, missing confirmation indicators.

### 90. ✅ Reduce Default Target to 1.2%
- **Versions**: All (V2 NoAI + V2 AI)
- **Gap**: DEFAULT_TARGET_PCT was 1.5%. Only 33% of trades hit target in time (26/63 hit SQUARE_OFF). 1.2% is more achievable for intraday NSE moves.
- **Fix**: DEFAULT_TARGET_PCT 1.5 → 1.2. Decision history comment added.
- **Files**: `config.py`

### 91. ✅ Increase Claude Review Time to 30 Minutes
- **Versions**: V2 AI only
- **Gap**: CLAUDE_REVIEW_MINUTES was 20 min. Claude exits like "flat since entry, exit" came too early — the trade needed 25-30 min to develop. 19 REVIEW_EXIT trades had 32% win rate.
- **Fix**: CLAUDE_REVIEW_MINUTES 20 → 30. Gives trades more room to develop before first Claude review.
- **Files**: `config.py`

### 92. ✅ R:R Safety Floor (Adaptive)
- **Versions**: All (V2 NoAI + V2 AI)
- **Gap**: After ATR SL/target assignment, edge cases (ATR unavailable, SL capped, late-entry squeeze) could still create poor R:R. Need a catch-all safety net.
- **Fix**: After all SL/target adjustments, check R:R. Adaptive floor: starts at `POST_MERGE_RR_INITIAL` (1.2:1), relaxes to `POST_MERGE_RR_RELAXED` (1.0:1) after `RR_RELAX_AFTER_SCANS` (3) failed scans, stops trading after `RR_GIVEUP_AFTER_SCANS` (5) failures at floor. All values configurable.
- **Decision history**: 1.3:1 (2026-04-09) too aggressive. 1.0:1 fixed (2026-04-10) worked but no adaptive relaxation. Adaptive (2026-04-10) starts strict, relaxes on market reality. Merge removal (2026-04-10) fixes root cause — floor now catches only edge cases.
- **Files**: `config.py`, `order_engine.py`, `manager.py`, `manager_v2.py`

### 93. ✅ Volume Confirmation at Entry (RVol Gate)
- **Versions**: All (V2 NoAI + V2 AI)
- **Gap**: RVol was only used as a score bonus during scanning. At actual entry time, volume could have dried up (stock going quiet), making fills unreliable and breakouts unlikely.
- **Fix**: At entry time, if live RVol < 0.7× average, skip entry. Only in live mode (dry-run skips this check since no real-time volume).
- **Files**: `order_engine.py`

### 94. ✅ StochRSI Indicator for Entry Timing
- **Versions**: All (V2 NoAI + V2 AI)
- **Gap**: RSI alone doesn't capture momentum shifts at extremes. StochRSI (Stochastic of RSI) gives earlier crossover signals — bullish cross in oversold zone or bearish cross in overbought zone.
- **Fix**: Added `stoch_rsi()` function with %K/%D lines, signal detection (BULLISH_CROSS, BEARISH_CROSS, OVERBOUGHT, OVERSOLD). Integrated into `compute_technical_score()` return dict. Added to stock scanner enriched snapshot, NoAI rationale, and Claude prompt.
- **Files**: `technical_indicators.py`, `stock_scanner_v2.py`

### 95. ✅ Sector Momentum Filter (Score Boost)
- **Versions**: All (V2 NoAI + V2 AI)
- **Gap**: Stocks are scored individually. When 3+ stocks in the same sector agree on direction, the sector has institutional flow — individual picks within it deserve higher conviction.
- **Fix**: In `_prefilter_universe()`, compute per-sector score agreement. When ≥3 stocks in a sector agree on direction, each gets ±0.5 score boost. Applied before Nifty trend hard filter.
- **Files**: `stock_scanner_v2.py`

### 96. ✅ Claude Scan Prompt: Rank/Veto Role + StochRSI Confluence
- **Versions**: V2 AI only
- **Gap**: Claude prompt didn't clearly define its role (rank vs generate). Missing StochRSI interpretation guide. Confluence checklist had 13 items but missed StochRSI.
- **Fix**: Added role description ("YOUR ROLE: RANK and VETO from pre-filtered candidates"). Added StochRSI interpretation section. Updated confluence checklist to 14 items ("7/14 confluence").
- **Files**: `stock_scanner_v2.py`

### 97. ✅ Feed 15-Min Re-Scan Data to Claude Review Prompt
- **Versions**: V2 AI only
- **Gap**: Claude review prompt included 5-min RSI/EMA/VWAP/patterns but NOT the 15-min composite score. Claude couldn't see whether the broader technical picture had shifted.
- **Fix**: In `review_positions_v2()`, also compute 15-min composite score and StochRSI signal for each open position. Appended to tech_ctx: "15min Score: +X.X  StochRSI: SIGNAL".
- **Files**: `stock_scanner_v2.py`

### Bug Fixes (V2 Review Cycle)

### 98. ✅ Extended Move Penalty Direction Fix
- **Versions**: V2 NoAI
- **Gap**: Extended move penalty was applied regardless of score direction. A stock down -2.5% with a SELL score (contrarian = correct direction) still got penalized. The penalty should only apply when chasing an already-extended move, not when trading against it.
- **Fix**: Penalty only when `chasing = (extended_move_pct > 0 and score > 0) or (extended_move_pct < 0 and score < 0)`. Contrarian setups are not penalized.
- **Files**: `technical_indicators.py`

### 99. ✅ Morning/Evening Star Gap Validation
- **Versions**: V2 NoAI
- **Gap**: Morning Star and Evening Star patterns detected without gap check. A small-bodied candle in the middle of the range isn't a star — it needs to be near an extreme (lower 40% for Morning Star, upper 60% for Evening Star).
- **Fix**: Morning Star: `c2["close"] < c1["close"] + candle_range(c1) * 0.4`. Evening Star: `c2["close"] > c1["close"] - candle_range(c1) * 0.4`.
- **Files**: `candle_patterns.py`

### 100. ✅ Three White Soldiers / Three Black Crows Body Check
- **Versions**: V2 NoAI
- **Gap**: Three White Soldiers checked `c2["open"] > c1["open"]` but should check that each candle opens within the prior candle's body (per Nison's canonical definition).
- **Fix**: Changed to `c1["open"] <= c2["open"] <= c1["close"]` (Soldiers) and `c1["close"] <= c2["open"] <= c1["open"]` (Crows).
- **Files**: `candle_patterns.py`

### 101. ✅ Observation Filter Bypass Log Level
- **Versions**: All
- **Gap**: When quote fetch fails for a stock, the observation period filter is bypassed and logged at INFO level. This silent bypass could cause premature entries.
- **Fix**: Changed from `self.log.info()` to `self.log.warning()` for both quote failure paths.
- **Files**: `manager.py`

### 102. ✅ ATR Pure Mode — Remove ATR/Config SL Merge
- **Versions**: All
- **Gap**: `enter_trade()` merged ATR SL with config SL using "wider of both" logic (`min(atr_sl, sl)` for BUY). When `DEFAULT_STOP_LOSS_PCT` (1.5%) > `DEFAULT_TARGET_PCT` (1.2%), the merge always picked config's wider SL + config's tighter target → 0.6-0.8:1 R:R → every stock failed the R:R floor → 0 trades entered across 5 scan cycles.
- **Fix**: When ATR is available, use ATR SL and ATR target directly (no merge). Config defaults are pure fallbacks for when ATR candle data is unavailable. Pure ATR always produces exactly `TARGET_RR_MULTIPLIER` (1.5:1) R:R. Verified with all 11 failures from Apr 10 logs — all become 1.5:1 PASS.
- **Files**: `order_engine.py`, `config.py` (comment updates)

### 103. ✅ Candidate Pool — Return All Pre-Filtered Candidates
- **Versions**: NoAI
- **Gap**: `scan_noai()` returned only `max_trades + 5` candidates to the entry loop. With max_trades=2, only 7 candidates were tried. If the top picks failed R:R or other checks, the loop exhausted candidates quickly and triggered a wasteful full rescan.
- **Fix**: Return ALL pre-filtered candidates (`direction_filtered`) to the entry loop. The loop still stops once position slots are filled. More candidates = more chances to find one that passes all 14 entry checks.
- **Files**: `stock_scanner_v2.py`

---

## Implementation Status

| # | Improvement | Versions | Priority | Status | Implemented In |
|---|------------|----------|----------|--------|----------------|
| 1 | Volume confirmation | V2, NoAI | HIGH | ✅ Done | `candle_patterns.py` |
| 2 | Relative Volume (RVol) | V2, NoAI | HIGH | ✅ Done | `stock_scanner_v2.py` |
| 3 | Pattern freshness decay | V2, NoAI | HIGH | ✅ Done | `candle_patterns.py` |
| 4 | Previous day H/L/C S&R | V2, NoAI | HIGH | ✅ Done | `technical_indicators.py` |
| 5 | Nifty trend hard filter | V2, NoAI | HIGH | ✅ Done | `stock_scanner_v2.py` |
| 6 | Opening Range Breakout | V2, NoAI | HIGH | ✅ Done | `technical_indicators.py`, `stock_scanner_v2.py` |
| 7 | MACD histogram | V2, NoAI | HIGH | ✅ Done | `technical_indicators.py` |
| 8 | Sector diversification | V2, NoAI | HIGH | ✅ Done | `stock_scanner_v2.py` |
| 9 | Pre-market gap analysis | V2, NoAI | HIGH | ✅ Done | `technical_indicators.py` |
| 10 | Partial profit taking | All | HIGH | ✅ Done | `order_engine.py` |
| 11 | Periodic opportunity scan | V2, NoAI | HIGH | ✅ Done | `manager_v2.py`, `config.py` |
| 12 | Continuous market regime | V2, NoAI | HIGH | ✅ Done | `manager_v2.py` |
| 13 | Min capital deployment | V2 | HIGH | ✅ Done | `stock_scanner_v2.py`, `order_engine.py` |
| 14 | Stagnant position exit | NoAI | HIGH | ✅ Done | `order_engine.py`, `manager_v2.py` |
| 15 | Loss-adjusted sizing | All | HIGH | ✅ Done | `order_engine.py`, `config.py` |
| 16 | Circuit breaker cooldown | All | HIGH | ✅ Done | `manager.py`, `manager_v2.py`, `config.py` |
| 17 | Multi-timeframe (hourly) | V2, NoAI | MEDIUM | ✅ Done | `technical_indicators.py` |
| 18 | BB squeeze | V2, NoAI | MEDIUM | ✅ Done | `technical_indicators.py` |
| 19 | Max CB trips per day | All | HIGH | ✅ Done | `order_engine.py`, `config.py` |
| 20 | Consecutive SL pause | All | HIGH | ✅ Done | `order_engine.py`, `manager_v2.py` |
| 21 | Dynamic score after losses | NoAI | HIGH | ✅ Done | `stock_scanner_v2.py`, `config.py` |
| 22 | Regime-shift SL tightening | V2, NoAI | HIGH | ✅ Done | `manager_v2.py` |
| 23 | VIX-based sizing | All | MEDIUM | ✅ Done | `config.py`, `manager.py`, `manager_v2.py` |
| 24 | Backtesting framework | All | MEDIUM | ⬜ Pending | — |
| 25 | Trade journaling + analytics | All | MEDIUM | ✅ Done | `performance_tracker.py`, `view_performance.py` |
| 26 | Sector cap at entry time | All | HIGH | ✅ Done | `order_engine.py` |
| 27 | EOD accelerated exit | NoAI, V2 | HIGH | ✅ Done | `order_engine.py`, `manager_v2.py`, `config.py` |
| 28 | ADX trend strength | V2, NoAI | MEDIUM | ✅ Done | `technical_indicators.py` |
| 29 | Thursday expiry handling | All | MEDIUM | ✅ Done | `manager.py`, `config.py` |
| 30 | 3-day candle lookback | V2, NoAI | MEDIUM | ✅ Done | `stock_scanner_v2.py` |
| 31 | Today-candle-count guard | V2, NoAI | MEDIUM | ✅ Done | `technical_indicators.py` |
| 32 | Late-entry target reduction | All | HIGH | ✅ Done | `order_engine.py`, `config.py` |
| 33 | Fibonacci retracement levels | V2, NoAI | MEDIUM | ✅ Done | `technical_indicators.py`, `stock_scanner_v2.py` |
| 34 | VWAP SD bands | V2, NoAI | MEDIUM | ✅ Done | `technical_indicators.py`, `stock_scanner_v2.py` |
| 35 | Bid-ask spread check | All | MEDIUM | ✅ Done | `order_engine.py`, `config.py` |
| 36 | Intraday momentum (RoC) | V2, NoAI | MEDIUM | ⬜ Pending | — |
| 37 | ~~Correlation-based sizing~~ | — | — | ❌ Removed | Redundant w/ sector cap + direction diversification |
| 38 | Improved slippage model | Dry Run | LOW | ✅ Done | `order_engine.py` |
| 39 | ATR percentile ranking | V2, NoAI | MEDIUM | ⬜ Pending | — |
| 40 | Claude prompt feedback loop | V2 | LOW | ⬜ Pending | — |
| 41 | Holiday-shifted expiry | All | LOW | ⬜ Pending | — |
| 42 | Pre-open auction data | V2, NoAI | MEDIUM | ✅ Done | `config.py`, `manager.py` |
| 43 | Real-time trade verification | All | HIGH | ✅ Done | `scripts/verify_trades.py` |
| 44 | WebSocket tick data | All | MEDIUM | ⬜ Pending | — |
| 45 | Multi-day score trend | V2, NoAI | MEDIUM | ⬜ Pending | — |
| 46 | ~~Smart square-off timing~~ | — | — | ❌ Removed | EOD accelerated exit (#27) covers this |
| 47 | ~~Budget auto-scaling~~ | — | — | ❌ Removed | Loss-adjusted sizing (#15) + dynamic sizing (#58) cover this |
| 49 | ATR-only SL/target (pure ATR) | All | HIGH | ✅ Done | `order_engine.py` |
| 50 | Late-entry + time-decay exclusion | All | HIGH | ✅ Done | `order_engine.py` |
| 51 | Extended move penalty | V2, NoAI | HIGH | ✅ Done | `technical_indicators.py`, `stock_scanner_v2.py` |
| 52 | RSI extreme hard cap | V2, NoAI | HIGH | ✅ Done | `technical_indicators.py` |
| 53 | Direction diversification (score-aware) | All | HIGH | ✅ Done | `order_engine.py`, `stock_scanner_v2.py` |
| 54 | Fewer trades, bigger size | All | HIGH | ✅ Done | `config.py` |
| 55 | LIMIT orders for entry/exit | All | MEDIUM | ⬜ Pending | — |
| 56 | Scan universe price filter | V2, NoAI | MEDIUM | ⬜ Pending | — |
| 57 | ~~VWAP exclude incomplete candle~~ | — | — | ❌ Removed | Negligible impact on cumulative VWAP |
| 58 | Dynamic position sizing by budget | All | MEDIUM | ✅ Done | `config.py`, `order_engine.py` |
| 59 | R:R 1.5:1 + configurable multiplier | All | HIGH | ✅ Done | `config.py`, `order_engine.py` |
| 60 | Exchange SL-M orders | All | HIGH | ✅ Done | `zerodha_client.py`, `order_engine.py`, `config.py` |
| 61 | SuperTrend params configurable | V2, NoAI | LOW | ✅ Done | `config.py`, `technical_indicators.py` |
| 62 | Fibonacci directional score | V2, NoAI | MEDIUM | ✅ Done | `technical_indicators.py` |
| 63 | ORB use 2nd candle (9:30-9:45) | V2, NoAI | MEDIUM | ✅ Done | `technical_indicators.py` |
| 64 | Short position time cap | All | MEDIUM | ✅ Done | `order_engine.py`, `config.py` |
| 65 | Pre-trade minimum profit check | All | HIGH | ✅ Done | `order_engine.py`, `config.py` |
| 66 | Entry delay 15->5 min | All | MEDIUM | ✅ Done | `config.py` |
| 67 | Trail step 65->50% | All | MEDIUM | ✅ Done | `config.py` |
| 68 | Time-decay 40->25% | All | MEDIUM | ✅ Done | `config.py` |
| 69 | SL sanity check after entry override | All | HIGH | ✅ Done | `order_engine.py` |
| 70 | SL-M partial fill verification | All | HIGH | ✅ Done | `order_engine.py`, `zerodha_client.py` |
| 71 | Fill price SL cap re-validation | All | HIGH | ✅ Done | `order_engine.py` |
| 72 | Store initial_sl at entry | All | MEDIUM | ✅ Done | `order_engine.py` |
| 73 | Fallback candidate pool | All | MEDIUM | ✅ Done | `stock_scanner_v2.py`, `manager.py`, `manager_v2.py` |
| 74 | Periodic manual trade sync | All | MEDIUM | ✅ Done | `manager.py`, `manager_v2.py` |
| 75 | --max budget CLI flag | All | MEDIUM | ✅ Done | `main.py` |
| 76 | Smart direction diversification | All | HIGH | ✅ Done | `order_engine.py`, `stock_scanner_v2.py` |
| 77 | Entry count logging fix | All | LOW | ✅ Done | `manager.py` |
| 78 | FII/DII flow bias | All | MEDIUM | ✅ Done | `config.py`, `manager.py` |
| 79 | Per-trade charge calculation (tax ledger) | Infrastructure | MEDIUM | ✅ Done | `fill_intraday_ledger.py` |
| 80 | EXTERNAL position unique order_id | Infrastructure | MEDIUM | ✅ Done | `fill_intraday_ledger.py` |
| 81 | Sheet import updates charges on P&L match | Infrastructure | MEDIUM | ✅ Done | `import_zerodha_taxpnl.py` |
| 82 | Stagnant exit timeout 90->45 min | All | HIGH | ✅ Done | `config.py` |
| 83 | exit_position SL-M cancel error handling | All | HIGH | ✅ Done | `order_engine.py` |
| 84 | _replace_exchange_sl pending ID tracking | All | HIGH | ✅ Done | `order_engine.py` |
| 85 | _update_exchange_sl exception safety | All | HIGH | ✅ Done | `order_engine.py` |
| 86 | ADJUST_TARGET directional validation | V2 | HIGH | ✅ Done | `order_engine.py` |
| 87 | reconcile_with_zerodha API error handling | All | HIGH | ✅ Done | `order_engine.py` |
| 88 | market_data.py quote fetch error handling | All | HIGH | ✅ Done | `market_data.py` |
| 89 | Increase circuit breaker to 4% | All | MEDIUM | ⬜ Pending | `config.py` |
| 90 | Reduce default target to 1.2% | All | HIGH | ✅ Done | `config.py` |
| 91 | Increase Claude review time to 30 min | V2 AI | HIGH | ✅ Done | `config.py` |
| 92 | R:R safety floor (adaptive) | All | MEDIUM | ✅ Done | `config.py`, `order_engine.py`, `manager.py`, `manager_v2.py` |
| 93 | Volume confirmation at entry (RVol gate) | All | LOW | ✅ Done | `order_engine.py` |
| 94 | StochRSI indicator for entry timing | All | LOW | ✅ Done | `technical_indicators.py`, `stock_scanner_v2.py` |
| 95 | Sector momentum filter (score boost) | All | LOW | ✅ Done | `stock_scanner_v2.py` |
| 96 | Claude scan prompt: rank/veto + StochRSI | V2 AI | HIGH | ✅ Done | `stock_scanner_v2.py` |
| 97 | Feed 15-min re-scan data to Claude review | V2 AI | MEDIUM | ✅ Done | `stock_scanner_v2.py` |
| 98 | Extended move penalty direction fix (bug) | V2, NoAI | HIGH | ✅ Done | `technical_indicators.py` |
| 99 | Morning/Evening Star gap fix (bug) | V2, NoAI | HIGH | ✅ Done | `candle_patterns.py` |
| 100 | Three White Soldiers/Crows body fix (bug) | V2, NoAI | HIGH | ✅ Done | `candle_patterns.py` |
| 101 | Observation filter log level fix (bug) | All | MEDIUM | ✅ Done | `manager.py` |
| 102 | ATR pure mode (remove merge) | All | CRITICAL | ✅ Done | `order_engine.py`, `config.py` |
| 103 | Candidate pool: all pre-filtered | NoAI | HIGH | ✅ Done | `stock_scanner_v2.py` |

