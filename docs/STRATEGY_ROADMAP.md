# Strategy Roadmap

Research-backed improvements for the V2 intraday trading bot. Sources: Investopedia, Zerodha Varsity, Toby Crabel (ORB), institutional intraday practices, and real trade data analysis (80+ live trades, April 2026).

---

## How to Read This Roadmap

This document is the **history log** of every strategy improvement, the **backlog** of pending items, and the **record** of deliberately rejected ideas. Someone new to the project should be able to read this document top-to-bottom and understand what has been built, why, and what is next.

### Sections

1. **Pending** — items still to be built. Each has priority + impact + effort estimates.
2. **Removed** — items evaluated and rejected. The reason column explains why (saves us from re-proposing them).
3. **Completed** — everything shipped, grouped by category. The `#` column is the historical item number (don't renumber — it breaks references in commit messages and other docs).
4. **Pending — Details** — long-form explanations for complex pending items.

### Category legend

| Category | What it means |
|----------|---------------|
| **Indicators** | A new signal the bot reads (e.g. RSI, VWAP, MACD — explained in [STRATEGY_V2 Glossary](STRATEGY_V2.md#glossary--every-term-explained)). |
| **Risk** | A guard that prevents bad trades or limits losses (stop-loss rules, exposure caps, circuit breaker). |
| **Execution** | How orders are placed, priced, or sequenced (LIMIT vs MARKET, entry timing, target compression). |
| **Market Intel** | External market context that biases decisions (NIFTY trend, VIX, FII/DII, expiry). |
| **Infra** | Plumbing — database schema, logging, backups, CLI flags. |
| **Bug Fix** | A defect corrected, as opposed to a new feature. |

### How to add a new item

1. Pick the next available number (currently 154 and above are free).
2. Add it under the matching **category** heading in **Completed** (Indicators / Risk Management / Execution / Market Intelligence / Infrastructure / Bug Fixes). If none fits, add a new category heading — do NOT create per-review/per-date sub-headings.
3. Keep the one-line description short but specific. If context matters, use a longer description on the same row (see items #137, #140, #146).
4. Bump the count in the category sub-heading and the top-line Completed count.
5. If it changes user-visible behaviour, also:
   - Update the relevant section in [STRATEGY_V2.md](STRATEGY_V2.md).
   - If it introduces a new technical term, add a glossary entry there.
6. If the idea is still planned, add it to **Pending** with priority / impact / effort.
7. If the idea is explicitly rejected, add it to **Removed** with the reason (future-you will thank you).

---

## Status Overview

### Pending (12 items)

| # | Improvement | Priority | Impact | Effort |
|---|------------|----------|--------|--------|
| 24 | Backtesting framework — replay V2 scoring on historical data | LOW | Highest | High |
| 41 | Holiday-shifted expiry detection — Wed instead of Thu, ~3 days/year | LOW | Low | Low |
| 44 | WebSocket tick data — real-time SL/target vs 10s polling | MEDIUM | High | High |
| 144 | Bracket orders — atomic entry + SL + target as one linked order | MEDIUM | High | High |
| 145 | Volatility-adjusted position sizing — qty scaled by ATR, not just budget | MEDIUM | High | Medium |
| 147 | Session-time-aware RVol — hourly-bucket baseline, not daily average | LOW | Medium | Medium |
| 148 | Stale SL-M cleanup on restart — reconcile or cancel orphan SL-M orders | HIGH | High | Low |
| 149 | Sector-cascade exit — breakeven-tighten all positions in a fast-falling sector | MEDIUM | Medium | Medium |
| 150 | [Bug] Partial-qty exits can lose 1 share to integer truncation | HIGH | Medium | Low |
| 151 | [Bug] External partial close misread as full close in reconciliation | HIGH | High | Medium |
| 157 | ADX + directional entry confirm — reject entries when ADX < threshold or DI crossover disagrees with side | MEDIUM | Medium | Medium |
| 158 | Regime-shift opportunity window — after NIFTY flips, pause stagnant-exit for 30–60 min and allow aligned re-entries | LOW | Medium | Low |

### Removed (8 items — not worth implementing)

| # | Item | Reason |
|---|------|--------|
| 37 | Correlation-based sizing | Redundant — sector cap + direction diversification already prevent correlated drawdowns |
| 39 | ATR percentile ranking | ATR already capped at MAX_INTRADAY_SL_PCT (2.5%). Percentile adds marginal value |
| 40 | Claude prompt feedback loop | AI mode not default. NoAI doesn't use Claude |
| 45 | Multi-day score trend | Score momentum (#36) handles intraday RoC. Multi-day adds complexity for marginal intraday gain |
| 46 | Smart square-off timing | Late-day loser exit (#27) already exits losers early |
| 47 | Budget auto-scaling by win rate | Loss-adjusted sizing (#15) + dynamic sizing (#58) already cover this |
| 57 | VWAP exclude incomplete candle | Negligible impact on cumulative VWAP. VWAP SD bands smooth noise |
| 89 | Increase circuit breaker to 4% | Config change, not a feature. Edit `MAX_LOSS_PER_DAY_PCT` in config.py |

### Completed (134 items)

> Grouped by category, not by review date. Items keep their original numbers (don't renumber — commit messages and other docs reference them).

| # | Improvement | Category |
|---|------------|----------|
| **Indicators (20)** | | |
| 1 | Volume confirmation for candle patterns | Indicators |
| 2 | Relative Volume (RVol) as stock filter | Indicators |
| 3 | Pattern freshness decay (1.0/0.7/0.4×) | Indicators |
| 4 | Previous day H/L/C support/resistance | Indicators |
| 6 | Opening Range Breakout (ORB, 2nd candle) | Indicators |
| 7 | MACD histogram confirmation | Indicators |
| 9 | Pre-market gap analysis | Indicators |
| 17 | Multi-timeframe alignment (hourly EMA) | Indicators |
| 18 | Bollinger Band squeeze detection | Indicators |
| 28 | ADX trend strength filter | Indicators |
| 30 | 3-day candle lookback (MACD/BB warmup) | Indicators |
| 31 | Today-candle-count guard for early scans | Indicators |
| 33 | Fibonacci retracement levels (directional) | Indicators |
| 34 | VWAP standard deviation bands (±1σ, ±2σ) | Indicators |
| 36 | Score momentum — RoC + deceleration penalty | Indicators |
| 51 | Extended move penalty (directional, >2% = -3) | Indicators |
| 52 | RSI extreme hard cap (≥75 caps at +3) | Indicators |
| 61 | SuperTrend configurable (7, 2.0 for intraday) | Indicators |
| 94 | StochRSI for entry timing (info-only) | Indicators |
| 95 | Sector momentum filter (±0.5 boost) | Indicators |
| **Risk Management (30)** | | |
| 5 | NIFTY trend hard filter (against-trend needs ≥3) | Risk |
| 8 | Sector diversification (max 2/sector) | Risk |
| 14 | Stagnant position exit (NoAI, 45 min) | Risk |
| 15 | Loss-adjusted position sizing | Risk |
| 16 | Circuit breaker cooldown (30 min resume, max 2 trips) | Risk |
| 19 | Max circuit breaker trips per day | Risk |
| 20 | Consecutive SL pause / whipsaw guard (3 SLs → 30 min) | Risk |
| 21 | Dynamic score threshold after losses (NoAI) | Risk |
| 22 | Regime-shift SL tightening | Risk |
| 26 | Sector cap enforcement at entry time | Risk |
| 27 | Late-day loser exit (2:45 PM) | Risk |
| 53 | Direction diversification (score-aware, gap ≥3 = all slots) | Risk |
| 64 | Short position cutoff (1 PM) | Risk |
| 65 | Pre-trade minimum profit check (Rs.50) | Risk |
| 115 | RSI contradiction filter (no SELL RSI>70, no BUY RSI<30) | Risk |
| 116 | Declining re-entry block (score delta < 0 → skip) | Risk |
| 119 | Expiry score bump raised 0.5 → 1.0 | Risk |
| 122 | Expiry entry delay (15 min vs 5 min normal) | Risk |
| 123 | Expiry max trades cap (5/day) | Risk |
| 124 | Daily trade cap (12/day) to prevent churn | Risk |
| 125 | VWAP trend block (no BUY below VWAP, no SELL above VWAP) | Risk |
| 128 | Stagnant churn guard (no re-enter stagnant exits same direction) | Risk |
| 129 | Net-of-charges R:R check (effective R:R ≥ 1.0:1 after costs) | Risk |
| 130 | RSI contradiction filter symmetric (also block BUY RSI>75, SELL RSI<25) | Risk |
| 131 | VWAP extension-chase block (BUY >+0.8%, SELL <−0.8%, override at \|score\|≥6) | Risk |
| 132 | Fresh-reversal guard (skip entry when \|score_delta\|≥8, wait one cycle) | Risk |
| 133 | Adoption grace window (10 min skip time-decay + loser-exit on RESUMED/EXTERNAL) | Risk |
| 134 | MIN_SL_DISTANCE_PCT floor (0.8% normal, 1.0% expiry) — preserves R:R by widening target | Risk |
| 138 | VWAP trend/extension guard activation raised from 10:00 → 10:15 (VWAP needs ≥1 hour of candles for stability) | Risk |
| 146 | Impact-cost / depth liquidity check — before entry, walk top-5 order-book levels and compute the weighted-average fill price for our full qty. Skip trade if slippage vs LTP exceeds `MAX_IMPACT_COST_PCT` (default 0.2%). Also skips when visible depth across top-5 is smaller than our qty. Fail-open on missing/malformed depth (logs a warning, lets trade through). Catches paper-thin book traps that spread-only checks miss. | Risk |
| **Execution (35)** | | |
| 10 | Partial profit taking (1/3 at 1.5R, trail 50%) | Execution |
| 11 | Periodic opportunity scanning (30 min, free slots) | Execution |
| 13 | Minimum capital deployment guidance | Execution |
| 32 | Late-entry target reduction (1 PM: -20%, 2 PM: -25%) | Execution |
| 35 | Bid-ask spread check (max 0.3%) | Execution |
| 49 | ATR-only SL/target (pure ATR, no merge) | Execution |
| 50 | Late-entry + time-decay mutual exclusion | Execution |
| 54 | Fewer trades, bigger size (MAX_POSITIONS 5→3) | Execution |
| 55 | LIMIT orders for entry (with MARKET fallback) | Execution |
| 56 | Scan universe price filter (Rs.100 min) | Execution |
| 58 | Dynamic position sizing by budget | Execution |
| 59 | R:R 1.5:1 configurable | Execution |
| 60 | Exchange SL-M orders (instant SL on NSE) | Execution |
| 66 | Entry delay 15→5 min | Execution |
| 67 | Trail step 65→50% | Execution |
| 68 | Time-decay 40→25% | Execution |
| 73 | Fallback candidate pool (all pre-filtered) | Execution |
| 74 | Periodic manual trade sync (every 15 min) | Execution |
| 82 | Stagnant exit 90→45 min | Execution |
| 90 | Default target 1.5→1.2% | Execution |
| 91 | Claude review time 20→30 min (AI only) | Execution |
| 92 | R:R floor (time-based + adaptive + mid-day retry) | Execution |
| 93 | Volume confirmation at entry (RVol ≥ 0.7x) | Execution |
| 96 | Claude: rank/veto role + StochRSI confluence (AI only) | Execution |
| 97 | Feed 15-min re-scan data to Claude review (AI only) | Execution |
| 103 | Candidate pool: return all pre-filtered | Execution |
| 104 | Fallback promotion on budget drop | Execution |
| 107 | Score-weighted position sizing (simplified Kelly) | Execution |
| 117 | Post-1pm SELL slot → BUY reallocation (score ≥ 4.0 guard) | Execution |
| 118 | Expiry-day stagnant timer extension (+15 min) | Execution |
| 126 | Midday lull stagnant timer extension (12:00-1:30 +15 min) | Execution |
| 127 | MIN_EXPECTED_PROFIT raised 50 → 75 (2× charges) | Execution |
| 135 | Expiry entry delay 15→30 min (market-open) with 15-min late-start floor | Execution |
| 136 | Expiry position reduction skipped when budget < Rs.1L (small-account flexibility) | Execution |
| 137 | Entry-delay semantic fix: observation window = `market_open + delay` (was `now + delay`). 9:30 script start with 30-min delay now correctly targets 9:45 entry instead of 10:00. | Execution |
| **Market Intelligence (6)** | | |
| 12 | Continuous NIFTY regime monitoring (every 15 min) | Market Intel |
| 23 | India VIX volatility regime detection | Market Intel |
| 29 | Thursday F&O expiry-day handling (+0.3 ATR, -1 pos, +0.5 score) | Market Intel |
| 42 | Pre-open auction data (9:08 gap detection) | Market Intel |
| 76 | Smart direction diversification (score-aware) | Market Intel |
| 78 | FII/DII flow bias (pre-market intelligence) | Market Intel |
| **Infrastructure (11)** | | |
| 25 | Trade journaling + performance analytics | Infra |
| 38 | Improved slippage model for dry run | Infra |
| 43 | Real-time trade verification script | Infra |
| 75 | --max budget CLI flag | Infra |
| 79 | Per-trade charge calculation (tax ledger) | Infra |
| 80 | EXTERNAL position unique order_id | Infra |
| 81 | Sheet import updates charges on P&L match | Infra |
| 110–111 | SQLite WAL mode + trades dedup constraint | Infra |
| 120 | Next scan timestamps in monitor logs (candle + opportunity) | Infra |
| 121 | round_to_tick made public API, Kite avg_volume gap documented | Infra |
| 142 | `Config.validate_ranges()` — sanity-checks every numeric config value at startup. Catches typos like `ATR_MULTIPLIER=0` (div-by-zero), `MAX_LOSS_PER_DAY_PCT=-1`, `MIN_SL_DISTANCE_PCT >= MAX_INTRADAY_SL_PCT` before they corrupt live trades. | Infra |
| **Bug Fixes (29)** | | |
| 69 | SL sanity check after entry price override | Bug Fix |
| 70 | SL-M partial fill verification | Bug Fix |
| 71 | Fill price SL cap re-validation | Bug Fix |
| 72 | Store initial_sl at entry time | Bug Fix |
| 77 | Entry count logging fix | Bug Fix |
| 83–88 | SL-M error handling, pending ID tracking, exception safety, directional validation, reconcile API handling, quote fetch handling | Bug Fix |
| 98 | Extended move penalty direction fix | Bug Fix |
| 99 | Morning/Evening Star gap validation | Bug Fix |
| 100 | Three White Soldiers/Crows body check (Nison) | Bug Fix |
| 101 | Observation filter bypass log level | Bug Fix |
| 102 | ATR pure mode (remove SL merge) | Bug Fix |
| 105–106 | Late-entry R:R floor + tier 2 reduction 35→25% | Bug Fix |
| 108 | Direction filter fallback fix | Bug Fix |
| 109 | R:R mid-day retry guard (morning excluded) | Bug Fix |
| 112 | Dynamic tick size for LIMIT orders (was hardcoded 0.05) | Bug Fix |
| 113 | Defensive tick rounding in place_order() | Bug Fix |
| 114 | fill_intraday_ledger skip dates with ZV_ rows (prevent VM/local dup) | Bug Fix |
| 139 | Defensive `abs(_entry_score or 0)` in declining re-entry block — explicit `None` no longer crashes | Bug Fix |
| 140 | Order-API recovery: `_order_api_broken` now clears on first successful order. A transient Zerodha glitch no longer kills the entire trading day — the bot retries, and the failure counter re-trips only if the API is genuinely down. | Bug Fix |
| 141 | VWAP guard exception now logs a WARNING (was silent `pass`). Malformed indicator snapshots are visible in logs instead of silently bypassing the VWAP protection. | Bug Fix |
| 143 | Removed sticky early-return on `_order_api_broken` in entry path. Rely on consecutive-failure counter to re-trip if the API is genuinely broken; allows recovery without manual restart. | Bug Fix |
| 152 | SL-M placement failure now raises an ERROR-level loud alert (was a subtle WARNING). The position is flagged `_sl_m_failed=True` and the log clearly states "exchange-side protection is NOT in place; restart on a later trading day is NOT safe for this position". User can no longer run naked positions without seeing it. | Bug Fix |
| 153 | **Stale SL-M double-booking fix (CRITICAL).** When candle-protect or regime-protect tightened the software SL below the exchange SL-M trigger, the software stop fired first — but `exit_position()` was assuming the exchange SL-M had already filled. It asked `get_order_filled_qty() or qty` (and `0 or qty == qty`), treated that as a full fill, placed no real exit order, and let the position stay live on Zerodha. Reconciliation then re-adopted the same short and triggered SL again — booking the loss twice. Fix: (a) new `get_order_status()` in zerodha_client; (b) `exit_position()` STOP_LOSS branch now verifies status == COMPLETE before trusting the SL-M, else cancels the stale order and places a market exit; (c) `_candle_protect()` and `_regime_shift_protect()` in manager_v2 now call `engine._update_exchange_sl()` so the broker-side order stays in sync with the software SL. | Bug Fix |
| 154 | **Candle-protect / regime-shift SL cushion.** Previously when a contrary signal hit a break-even or losing position, the tightened SL collapsed to exact entry. If the live price was already against entry (the typical case for a contrary signal!), the new SL fired on the very next tick — the INDIGO 2026-04-17 chain-reaction bug. Fix: new `_compute_protective_sl()` helper in manager_v2. SL is now clamped to stay at least `CANDLE_PROTECT_MIN_CUSHION_PCT` away from the live price AND from entry. Applied to both `_auto_protect_on_contrary_signal` and `_regime_shift_protect`. | Bug Fix |
| 155 | **Reconciled-day safeguard.** `import_zerodha_taxpnl.py` used to overwrite trades that had been manually reconciled (e.g. when the intraday Tax P&L sheet lagged reality for the current day). Fix: trading-day JSONs get a sticky `_reconciled: true` flag when the user reconciles. Three writers (`import_zerodha_taxpnl`, `performance_tracker.record_trades`, `report_writer.save_trading_day`) now honour the flag — they still cross-check sheet vs DB and print warnings on mismatch, but never delete/replace the authoritative rows. Also fixed a side-fixer bug that was flipping BUY↔SELL on protected days. | Bug Fix |
| 156 | **Directional stagnant-exit.** Stagnant-exit previously fired whenever `move_pct < STAGNANT_EXIT_MIN_MOVE_PCT` (0.3%). This lumped slow-positive trades (RECLTD +0.26%, TATAPOWER +0.25% on 2026-04-17) in with adverse/flat trades — locking in a sub-charge profit and wasting another Rs.15-20 round-trip to re-enter. Fix: new `STAGNANT_ADVERSE_PCT` (0.2%) and `STAGNANT_DEAD_FLAT_PCT` (0.1%) thresholds. Stagnant-exit now fires only if the trade is clearly adverse OR genuinely dead-flat. Slow-positive trades are allowed to continue toward target. | Execution |

---

## Pending — Details

### 24. Backtesting Framework
- **Priority**: LOW (deferred — use live trade analytics first)
- **Gap**: No way to measure which indicators actually contribute to winning trades.
- **Fix**: Replay V2 scoring on historical 15-min data, simulate ATR-based entries/exits, compute win rate per indicator combination.
- **Source**: Every professional quant desk backtests before going live.
- **Note**: We have 80+ live trades with full indicator snapshots in SQLite. Use `python scripts/view_performance.py --summary` to identify patterns before building a full framework.

### 41. Holiday-Shifted Expiry Detection
- **Priority**: LOW (~3 days/year edge case)
- **Gap**: Thursday expiry detection uses `weekday == 3`. When Thursday is an NSE holiday, expiry shifts to Wednesday.
- **Fix**: Maintain a list of actual expiry dates from NSE published calendar alongside the holiday list.

### 44. WebSocket Tick Data
- **Priority**: MEDIUM (implement when polling latency causes measurable slippage)
- **Gap**: 10-second polling can miss rapid SL/target breaches during news events.
- **Fix**: Use Zerodha WebSocket (up to 3000 instruments) for real-time tick data on open position symbols. SL/target checks on every tick.
- **Note**: Exchange SL-M orders (#60) already handle instant SL execution. WebSocket mainly improves target hits and trailing SL responsiveness.

### 144. Bracket Orders (Atomic Entry + SL + Target)
- **Priority**: MEDIUM (safety upgrade, not a miss-profit fix)
- **Today**: For every trade we submit three separate things — the entry order, then a stop-loss order after the entry fills, then a software-side target watcher. If the bot crashes in between any of these, the position can be left un-protected. If the user manually closes the position in the Kite app, the SL-M can still be alive and mis-trigger later.
- **Fix**: Use Zerodha Bracket Order (BO). Entry + SL + target are submitted as one linked order. When the entry fills, the exchange itself arms the SL and target; when either fires, the other auto-cancels. One atomic state per trade, no orphans.
- **Effort**: ~2 weeks. Touches `order_engine`, reconciliation, and crash-recovery logic.

### 145. Volatility-Adjusted Position Sizing
- **Priority**: MEDIUM (makes risk per trade consistent across calm vs wild stocks)
- **Today**: `qty = floor(budget_per_slot / price)`. A calm Rs.500 stock and a wild Rs.500 stock get the same qty, but the wild one moves much more — so we are silently risking more rupees on wild stocks.
- **Fix**: `qty = (budget × risk_pct) / (ATR × ATR_multiplier)`. Calm stocks get more shares, wild stocks get fewer, so every trade risks roughly the same rupees.
- **Effort**: Medium. Paper-test before live — needs interaction with `MAX_POSITION_PCT` cap checked carefully.

### 147. Session-Time-Aware RVol Baseline
- **Priority**: LOW (current RVol is "good enough" for the pre-filter)
- **Today**: RVol compares today's intraday volume to the 20-day **daily** average. But intraday volume has a U-shape (huge at open, dead 12–13:30, huge at close). Midday, almost every stock looks "quiet" by daily-average math — we may be skipping good trades.
- **Fix**: Build a 20-day average for each 30-minute bucket of the day. Compare today's 12:30 volume to the historical 12:30 volume.
- **Effort**: Medium. Needs a new cache of hourly-bucket volume history.

### 148. Stale SL-M Cleanup on Restart
- **Priority**: HIGH (pure crash-safety; prevents next-day mis-fires)
- **Today**: After a crash, `load_existing_positions()` rebuilds in-memory positions from Zerodha MIS, but pre-crash SL-M orders are still live on the exchange and we no longer know their order IDs. Price revisiting an old trigger level on the NEXT trading day could fire a stale SL-M against a fresh position.
- **Fix**: On startup, call `kite.orders()` and either (a) reconcile each open SL-M to a resumed position and re-register its ID, or (b) cancel any SL-M that doesn't correspond to a current position.
- **Effort**: Low — one new API call wrapper + a small reconciliation loop.

### 149. Sector-Cascade Exit
- **Priority**: MEDIUM (protects against correlated sector-wide drops)
- **Today**: If 3 banking positions are all bleeding because the banking sector is dropping 2% in 15 min, each position waits for its own individual SL. By the time the 3rd hits, the 1st has lost much more than necessary.
- **Fix**: Every scan, roll up per-sector open P&L. If a sector's exposure is ≤ −1% of budget in ≤ 15 min, tighten SLs on **all** positions in that sector to breakeven immediately — don't wait for individual SLs.
- **Effort**: Medium. Needs sector P&L rollup + a new "panic-tighten" path in the engine. Research the threshold first.

### 150. [Bug] Partial-Qty Exits Can Lose Shares to Integer Truncation
- **Priority**: HIGH (silent P&L/inventory bug)
- **Today**: When we exit 1/3 of a position, we compute `qty_to_exit = total // 3`. For 10 shares this is 3. Next partial becomes `7 // 3 = 2`. Over multiple partial steps we under-exit and leave fractional rounding dust behind — reconciliation then disagrees with broker.
- **Fix**: Track cumulative `exited_qty` and compute the next partial as `round(target_fraction × original_qty) − already_exited`. Ensures totals add up cleanly and the final partial closes to exactly zero.
- **Effort**: Low. Localized to `order_engine` partial-exit path; needs unit-level test coverage.

### 151. [Bug] External Partial Close Misread as Full Close
- **Priority**: HIGH (wrong P&L on reconcile)
- **Today**: If the user manually closes **half** the position in the Kite app, our reconciliation in `load_existing_positions()` / periodic sync logic can interpret the reduced Zerodha qty as a full close — wipes the in-memory position, logs wrong P&L, and leaves the remaining half un-tracked.
- **Fix**: Compare Zerodha qty against the bot's tracked qty. If Zerodha qty is `0`, close. If it is `> 0` but `< tracked`, treat it as an external partial close: update tracked qty, log the partial at the broker-average price, keep the remaining position live with its SL-M still valid.
- **Effort**: Medium. Careful because this interacts with SL-M re-sizing and partial-target state.

### 157. ADX + Directional Entry Confirmation
- **Priority**: MEDIUM (cuts whipsaw losses)
- **Today**: Entry relies on combined_score + VWAP + pattern confirmations. We compute ADX for observation but don't gate entries on it. On chop days (NIFTY ±0.2% range, ADX < 18), entries fire and then immediately hit stagnant-exit or candle-protect — every one of those loses charges (~Rs.40 round-trip) and adds nothing.
- **Fix**: At entry, require either (a) ADX ≥ 18 **and** DI crossover aligned with `side`, or (b) score ≥ late-entry R:R floor (for very-high-conviction trades) to override. Also consider per-sector ADX so we don't reject trades on a single index reading.
- **Effort**: Medium. ADX already computed in `technical_indicators`; need the gate, a config constant, and careful tuning to avoid starving the bot on quiet days.

### 158. Regime-Shift Opportunity Window
- **Priority**: LOW (small but non-zero alpha)
- **Today**: When NIFTY flips (e.g., morning BEARISH → afternoon BULLISH), regime-shift-protect tightens SLs on contrary positions but we do not actively look for **aligned** new entries. Meanwhile, stagnant-exit keeps firing on slow-positive trades entered under the old regime.
- **Fix**: For `REGIME_OPPORTUNITY_WINDOW_MINUTES` (default 30-60) after a flip: (a) pause stagnant-exit on positions aligned with the new regime, (b) lower the score threshold slightly for same-direction re-entries, (c) skip sector-cap for one aligned entry. Log a clear "REGIME OPPORTUNITY" banner.
- **Effort**: Low. Config + a timestamp on regime change + gating in `check_stagnant_positions` and the entry path.

