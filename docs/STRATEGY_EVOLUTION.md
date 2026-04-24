# Strategy Evolution
Chronological log of every strategy item shipped, in order of work-item number (numbers are issued in time order). One line per item. Use this to skim how the system evolved from day 1 to today.
> **Maintenance:** This file is regenerated from [STRATEGY_ROADMAP.md](STRATEGY_ROADMAP.md) (Completed section). Do NOT hand-edit rows. Add new items to the Roadmap; the regeneration recipe lives in [copilot/code-map.md](../copilot/code-map.md). Keep this file up to date after every shipped item or review-cycle pass.
> Last regenerated: 2026-04-24. Items: 178 (#1 → #212).

## Timeline

| # | Category | What shipped |
|---|----------|--------------|
| 1 | Indicators | Volume confirmation for candle patterns |
| 2 | Indicators | Relative Volume (RVol) as stock filter |
| 3 | Indicators | Pattern freshness decay (1.0/0.7/0.4×) |
| 4 | Indicators | Previous day H/L/C support/resistance |
| 5 | Risk | NIFTY trend hard filter (against-trend needs ≥3) |
| 6 | Indicators | Opening Range Breakout (ORB, 2nd candle) |
| 7 | Indicators | MACD histogram confirmation |
| 8 | Risk | Sector diversification (max 2/sector) |
| 9 | Indicators | Pre-market gap analysis |
| 10 | Execution | Partial profit taking (1/3 at 1.5R, trail 50%) |
| 11 | Execution | Periodic opportunity scanning (30 min, free slots) |
| 12 | Market Intel | Continuous NIFTY regime monitoring (every 15 min) |
| 13 | Execution | Minimum capital deployment guidance |
| 14 | Risk | Stagnant position exit (NoAI, 45 min) |
| 15 | Risk | Loss-adjusted position sizing |
| 16 | Risk | Circuit breaker cooldown (30 min resume, max 2 trips) |
| 17 | Indicators | Multi-timeframe alignment (hourly EMA) |
| 18 | Indicators | Bollinger Band squeeze detection |
| 19 | Risk | Max circuit breaker trips per day |
| 20 | Risk | Consecutive SL pause / whipsaw guard (3 SLs → 30 min) |
| 21 | Risk | Dynamic score threshold after losses (NoAI) |
| 22 | Risk | Regime-shift SL tightening |
| 23 | Market Intel | India VIX volatility regime detection |
| 25 | Infra | Trade journaling + performance analytics |
| 26 | Risk | Sector cap enforcement at entry time |
| 27 | Risk | Late-day loser exit (2:45 PM) |
| 28 | Indicators | ADX trend strength filter |
| 29 | Market Intel | Thursday F&O expiry-day handling (+0.3 ATR, -1 pos, +0.5 score) |
| 30 | Indicators | 3-day candle lookback (MACD/BB warmup) |
| 31 | Indicators | Today-candle-count guard for early scans |
| 32 | Execution | Late-entry target reduction (1 PM: -20%, 2 PM: -25%) |
| 33 | Indicators | Fibonacci retracement levels (directional) |
| 34 | Indicators | VWAP standard deviation bands (±1σ, ±2σ) |
| 35 | Execution | Bid-ask spread check (max 0.3%) |
| 36 | Indicators | Score momentum — RoC + deceleration penalty |
| 38 | Infra | Improved slippage model for dry run |
| 41 | Risk | Holiday-shifted expiry detection — when Thursday is an NSE holiday (Holi, Eid, etc., ~3 days/year), expiry-day adjustments fire on |
| 42 | Market Intel | Pre-open auction data (9:08 gap detection) |
| 43 | Infra | Real-time trade verification script |
| 49 | Execution | ATR-only SL/target (pure ATR, no merge) |
| 50 | Execution | Late-entry + time-decay mutual exclusion |
| 51 | Indicators | Extended move penalty (directional, >2% = -3) |
| 52 | Indicators | RSI extreme hard cap (≥75 caps at +3) |
| 53 | Risk | Direction diversification (score-aware, gap ≥3 = all slots) |
| 54 | Execution | Fewer trades, bigger size (MAX_POSITIONS 5→3) |
| 55 | Execution | LIMIT orders for entry (with MARKET fallback) |
| 56 | Execution | Scan universe price filter (Rs.100 min) |
| 58 | Execution | Dynamic position sizing by budget |
| 59 | Execution | R:R 1.5:1 configurable |
| 60 | Execution | Exchange SL-M orders (instant SL on NSE) |
| 61 | Indicators | SuperTrend configurable (7, 2.0 for intraday) |
| 64 | Risk | Short position cutoff (1 PM) |
| 65 | Risk | Pre-trade minimum profit check (Rs.50) |
| 66 | Execution | Entry delay 15→5 min |
| 67 | Execution | Trail step 65→50% |
| 68 | Execution | Time-decay 40→25% |
| 69 | Bug Fix | SL sanity check after entry price override |
| 70 | Bug Fix | SL-M partial fill verification |
| 71 | Bug Fix | Fill price SL cap re-validation |
| 72 | Bug Fix | Store initial_sl at entry time |
| 73 | Execution | Fallback candidate pool (all pre-filtered) |
| 74 | Execution | Periodic manual trade sync (every 15 min) |
| 75 | Infra | --max budget CLI flag |
| 75 | Infra | --nifty universe CLI flag (50/100/150/200) |
| 76 | Market Intel | Smart direction diversification (score-aware) |
| 77 | Bug Fix | Entry count logging fix |
| 78 | Market Intel | FII/DII flow bias (pre-market intelligence) |
| 79 | Infra | Per-trade charge calculation (tax ledger) |
| 80 | Infra | EXTERNAL position unique order_id |
| 81 | Infra | Sheet import updates charges on P&L match |
| 82 | Execution | Stagnant exit 90→45 min |
| 83-88 | Bug Fix | SL-M error handling, pending ID tracking, exception safety, directional validation, reconcile API handling, quote fetch handling |
| 90 | Execution | Default target 1.5→1.2% |
| 91 | Execution | Claude review time 20→30 min (AI only) |
| 92 | Execution | R:R floor (time-based + adaptive + mid-day retry) |
| 93 | Execution | Volume confirmation at entry (RVol ≥ 0.7x) |
| 94 | Indicators | StochRSI for entry timing (info-only) |
| 95 | Indicators | Sector momentum filter (±0.5 boost) |
| 96 | Execution | Claude: rank/veto role + StochRSI confluence (AI only) |
| 97 | Execution | Feed 15-min re-scan data to Claude review (AI only) |
| 98 | Bug Fix | Extended move penalty direction fix |
| 99 | Bug Fix | Morning/Evening Star gap validation |
| 100 | Bug Fix | Three White Soldiers/Crows body check (Nison) |
| 101 | Bug Fix | Observation filter bypass log level |
| 102 | Bug Fix | ATR pure mode (remove SL merge) |
| 103 | Execution | Candidate pool: return all pre-filtered |
| 104 | Execution | Fallback promotion on budget drop |
| 105-106 | Bug Fix | Late-entry R:R floor + tier 2 reduction 35→25% |
| 107 | Execution | Score-weighted position sizing (simplified Kelly) |
| 108 | Bug Fix | Direction filter fallback fix |
| 109 | Bug Fix | R:R mid-day retry guard (morning excluded) |
| 110-111 | Infra | SQLite WAL mode + trades dedup constraint |
| 112 | Bug Fix | Dynamic tick size for LIMIT orders (was hardcoded 0.05) |
| 113 | Bug Fix | Defensive tick rounding in place_order() |
| 114 | Bug Fix | fill_intraday_ledger skip dates with ZV_ rows (prevent VM/local dup) |
| 115 | Risk | RSI contradiction filter (no SELL RSI>70, no BUY RSI<30) |
| 116 | Risk | Declining re-entry block (score delta < 0 → skip) |
| 117 | Execution | Post-1pm SELL slot → BUY reallocation (score ≥ 4.0 guard) |
| 118 | Execution | Expiry-day stagnant timer extension (+15 min) |
| 119 | Risk | Expiry score bump raised 0.5 → 1.0 |
| 120 | Infra | Next scan timestamps in monitor logs (candle + opportunity) |
| 121 | Infra | round_to_tick made public API, Kite avg_volume gap documented |
| 122 | Risk | Expiry entry delay (15 min vs 5 min normal) |
| 123 | Risk | Expiry max trades cap (5/day) |
| 124 | Risk | Daily trade cap (12/day) to prevent churn |
| 125 | Risk | VWAP trend block (no BUY below VWAP, no SELL above VWAP) |
| 126 | Execution | Midday lull stagnant timer extension (12:00-1:30 +15 min) |
| 127 | Execution | MIN_EXPECTED_PROFIT raised 50 → 75 (2× charges) |
| 128 | Risk | Stagnant churn guard (no re-enter stagnant exits same direction) |
| 129 | Risk | Net-of-charges R:R check (effective R:R ≥ 1.0:1 after costs) |
| 130 | Risk | RSI contradiction filter symmetric (also block BUY RSI>75, SELL RSI<25) |
| 131 | Risk | VWAP extension-chase block (BUY >+0.8%, SELL <−0.8%, override at \\|score\\|≥6) |
| 132 | Risk | Fresh-reversal guard (skip entry when \\|score_delta\\|≥8, wait one cycle) |
| 133 | Risk | Adoption grace window (10 min skip time-decay + loser-exit on RESUMED/EXTERNAL) |
| 134 | Risk | MIN_SL_DISTANCE_PCT floor (0.8% normal, 1.0% expiry) — preserves R:R by widening target |
| 135 | Execution | Expiry entry delay 15→30 min (market-open) with 15-min late-start floor |
| 136 | Execution | Expiry position reduction skipped when budget < Rs.1L (small-account flexibility) |
| 137 | Execution | Entry-delay semantic fix: observation window = `market_open + delay` (was `now + delay`) |
| 138 | Risk | VWAP trend/extension guard activation raised from 10:00 → 10:15 (VWAP needs ≥1 hour of candles for stability) |
| 139 | Bug Fix | Defensive `abs(_entry_score or 0)` in declining re-entry block — explicit `None` no longer crashes |
| 140 | Bug Fix | Order-API recovery: `_order_api_broken` now clears on first successful order |
| 141 | Bug Fix | VWAP guard exception now logs a WARNING (was silent `pass`) |
| 142 | Infra | `Config.validate_ranges()` — sanity-checks every numeric config value at startup |
| 143 | Bug Fix | Removed sticky early-return on `_order_api_broken` in entry path |
| 145 | Execution | **ATR-based position sizing.** Old formula `qty = budget_per_slot / price` gave equal rupee exposure to every stock — a Rs.500 sto |
| 146 | Risk | Impact-cost / depth liquidity check — before entry, walk top-5 order-book levels and compute the weighted-average fill price for o |
| 147 | Risk | Session-time-aware RVol normalization |
| 148 | Bug Fix | **Stale SL-M cleanup on restart.** After a crash `load_existing_positions()` rebuilt positions from Zerodha MIS but never reconcil |
| 150 | Bug Fix | **Partial-qty exit truncation.** `_place_exit_order` for partial-profit taking requested `partial_qty` via MARKET but only returne |
| 151 | Bug Fix | **External partial close detection.** Previously `sync_external_positions()` compared only symbol-set membership |
| 152 | Bug Fix | SL-M placement failure now raises an ERROR-level loud alert (was a subtle WARNING) |
| 153 | Bug Fix | **Stale SL-M double-booking fix (CRITICAL).** When candle-protect or regime-protect tightened the software SL below the exchange S |
| 154 | Bug Fix | **Candle-protect / regime-shift SL cushion.** Previously when a contrary signal hit a break-even or losing position, the tightened |
| 155 | Bug Fix | **Reconciled-day safeguard.** `import_zerodha_taxpnl.py` used to overwrite trades that had been manually reconciled (e.g |
| 156 | Execution | **Directional stagnant-exit.** Stagnant-exit previously fired whenever `move_pct < STAGNANT_EXIT_MIN_MOVE_PCT` (0.3%) |
| 157 | Risk | **ADX + DI directional entry gate.** Entries used combined_score + VWAP + patterns but never gated on ADX/DI |
| 160 | Bug Fix | **Empty-API glitch guard on sync.** `sync_external_positions` previously mass-closed every tracked open position if Zerodha's `/po |
| 161 | Execution | **Per-symbol re-entry cooldown.** After ANY exit (SL, target, stagnant, external), the same symbol+direction could be re-opened on |
| 162 | Execution | **Charge-aware minimum target.** Net-of-charges R:R already demanded ≥1.0:1, but a trade with Rs.10 gross target on Rs.4 round-tri |
| 163 | Risk | **Daily-loss soft-stop hysteresis.** Only one loss threshold existed: the hard `MAX_LOSS_PER_DAY_PCT` (3%) circuit breaker, which  |
| 164 | Execution | **Lunch-lull entry skip.** 11:30-12:15 IST is the lowest-volume, lowest-ADX window on NSE — most bot churn trades fire here and im |
| 165 | Risk | **Dynamic budget-regime config.** Small accounts (<Rs.30k) need tighter gates — one losing trade hurts much more than on a Rs.5L a |
| 166 | Risk | Unrealised-MTM-aware safety gates |
| 168 | Risk | Intraday equity-peak drawdown stop — tracks `_intraday_peak_pnl = max(peak, day_pnl())` each entry attempt; blocks new entries whe |
| 169 | Bug Fix | **Defensive `score == 0` entry skip.** Three call sites in `stock_scanner_v2.py` and `portfolio/manager_v2.py` used `side = "BUY"  |
| 170 | Bug Fix | **Centralised Claude model in `generate_sheet.py`.** Script hard-coded `claude-sonnet-4-20250514` (a snapshot id) in two `messages |
| 171 | Bug Fix | **Budget double-counting bug (CRITICAL).** `refresh_budget()` overwrote `_budget` with Zerodha's `available` funds (which Zerodha  |
| 172 | Execution | **Two-tier stagnant exit (drift catcher).** Single 45-min directional check (adverse / dead-flat ±0.1%) was missing drifters that  |
| 173 | Risk | **Gap-coherence entry gate.** Pre-trade check rejected nothing on opening-gap direction |
| 174 | Risk | **Signal-reversal hard exit on held positions.** Static SL-M only catches price-side moves |
| 177 | Infra | **Post-trade rejection audit.** Every entry the order engine SKIPPED (R:R, RVol, ADX, lunch-lull, gap-coherence, charge-floor, etc |
| 180 | Risk | Circuit-limit (UC/LC) entry guard — reject BUY when intraday move ≥ +19% (within 1% of upper circuit) and SELL when ≤ -19% (within |
| 185 | Bug Fix | **Direction-aware declining re-entry block.** Check #13 in `enter_trade` ([order_engine.py:1723](../services/order_engine.py#L1723 |
| 186 | Bug Fix | **Live SL-M exit P&L matches broker fill.** When the exchange SL-M completed and our software SL also detected the breach on the s |
| 187 | Bug Fix | **One outer retry on SL-M / top-up fill-price fetch.** `get_order_fill_price()` already retries internally for its `timeout` windo |
| 188 | Execution | **Same-direction signal-decay exit (book-and-go at <1R).** Companion to signal-reversal (#174): catches positions whose entry sign |
| 190 | Risk | Pattern-direction entry veto |
| 191 | Risk | Exchange-fired SL-M attribution fix |
| 192 | Risk | Choppy-morning entry pause |
| 194 | Risk | Strong-gap ADX threshold boost |
| 195 | Risk | Average-down prevention via `_last_exit_score` |
| 196 | Risk | **Post-observation score recheck (stale-score guard at entry).** `_observe_and_enter()` ([portfolio/manager.py:514](../portfolio/m |
| 197 | Bug Fix | **MTM-aware safety gate now resilient to just-opened positions.** `effective_day_pnl()` ([order_engine.py:3759](../services/order_ |
| 198 | Risk | Post-entry momentum kill |
| 199 | Risk | Score-direction monotonic gate on stale-score recheck (#196 follow-up) |
| 200 | Risk | Pattern↔tech contradiction penalty at scanner combine |
| 201 | Risk | VWAP statistical-band entry gate |
| 202 | Risk | Late-entry tightening (R:R floor + score-floor + max-positions cap) |
| 203 | Bug Fix | Realised-P&L recovery from prior-session fills |
| 204 | Bug Fix | Square-off log-noise cleanup |
| 205 | Bug Fix | Shutdown-handler empty-WARNING fix + roadmap-number scrubbing |
| 206 | Performance | Skip wasted R:R retry pass when no R:R-floor rejections |
| 207 | Bug Fix | Rejection audit: IST-aware default date |
| 208 | Bug Fix | Demote "order does not exist" to debug |
| 209 | Bug Fix | Recovery rationale: drop internal #203 tag |
| 210 | Risk | Order-placement double-fire guard |
| 211 | Risk | VIX intraday-spike entry-pause unification (closes hole left by #181) |
| 212 | Risk | Tape-breadth filter on pre-filter set (BUY/SELL ratio penalty) |
