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

1. Pick the next available number (currently 171 and above are free).
2. Add it under the matching **category** heading in **Completed** (Indicators / Risk Management / Execution / Market Intelligence / Infrastructure / Bug Fixes). If none fits, add a new category heading — do NOT create per-review/per-date sub-headings.
3. Keep the one-line description short but specific. If context matters, use a longer description on the same row (see items #137, #140, #146).
4. Bump the count in the category sub-heading and the top-line Completed count.
5. If it changes user-visible behaviour, also:
   - Update the relevant section in [STRATEGY_V2.md](STRATEGY_V2.md).
   - If it introduces a new technical term, add a glossary entry there.
6. If the idea is still planned, add it to **Pending**. The Pending table is sorted by **priority first** (HIGH → MEDIUM → LOW), then by **impact** (Highest → High → Medium → Low) descending, then by **effort** (Low → Medium → High) ascending. The `#` column is just the historical id — row position is by priority, not by number. Insert the new row at the correct sorted position; do NOT append blindly to the bottom.
7. Also add a long-form entry under **Pending — Details** in the same priority order as the table. Include: Priority, Today (the gap), Fix, Effort.
8. If the idea is explicitly rejected, add it to **Removed** with the reason (future-you will thank you).

---

## Status Overview

### Pending (10 items)

Sorted by priority (HIGH → MEDIUM → LOW), then impact desc, then effort asc.

| # | Improvement | Priority | Impact | Effort |
|---|------------|----------|--------|--------|
| 166 | Unrealised-MTM-aware circuit breaker — include open-position MTM in `day_pnl()` so CB fires before five bleeders all hit individual SLs | MEDIUM | High | Low |
| 144 | Bracket orders — atomic entry + SL + target as one linked order | MEDIUM | High | High |
| 44 | WebSocket tick data — real-time SL/target vs 10s polling | MEDIUM | High | High |
| 167 | Earnings/results-day blackout — skip stocks with corporate results announced today (Q1–Q4 season abnormal moves) | MEDIUM | Medium | Medium |
| 149 | Sector-cascade exit — breakeven-tighten all positions in a fast-falling sector | MEDIUM | Medium | Medium |
| 168 | Intraday equity-peak drawdown stop — pause new entries when day P&L drops X% from intraday high (give-back protection) | LOW | Medium | Low |
| 158 | Regime-shift opportunity window — after NIFTY flips, pause stagnant-exit for 30–60 min and allow aligned re-entries | LOW | Medium | Low |
| 147 | Session-time-aware RVol — hourly-bucket baseline, not daily average | LOW | Medium | Medium |
| 24 | Backtesting framework — replay V2 scoring on historical data | LOW | Highest | High |
| 41 | Holiday-shifted expiry detection — Wed instead of Thu, ~3 days/year | LOW | Low | Low |

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

### Completed (149 items)

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
| **Execution (36)** | | |
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
| 75 | --nifty universe CLI flag (50/100/150/200) | Infra |
| 79 | Per-trade charge calculation (tax ledger) | Infra |
| 80 | EXTERNAL position unique order_id | Infra |
| 81 | Sheet import updates charges on P&L match | Infra |
| 110–111 | SQLite WAL mode + trades dedup constraint | Infra |
| 120 | Next scan timestamps in monitor logs (candle + opportunity) | Infra |
| 121 | round_to_tick made public API, Kite avg_volume gap documented | Infra |
| 142 | `Config.validate_ranges()` — sanity-checks every numeric config value at startup. Catches typos like `ATR_MULTIPLIER=0` (div-by-zero), `MAX_LOSS_PER_DAY_PCT=-1`, `MIN_SL_DISTANCE_PCT >= MAX_INTRADAY_SL_PCT` before they corrupt live trades. | Infra |
| **Bug Fixes (32)** | | |
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
| 156 | **Directional stagnant-exit.** Stagnant-exit previously fired whenever `move_pct < STAGNANT_EXIT_MIN_MOVE_PCT` (0.3%). This lumped slow-positive trades (RECLTD +0.26%, TATAPOWER +0.25% on 2026-04-17) in with adverse/flat trades — locking in a sub-charge profit and wasting another Rs.15-20 round-trip to re-enter. Fix: new `STAGNANT_ADVERSE_PCT` (0.2%) and `STAGNANT_DEAD_FLAT_PCT` (0.1%) thresholds. Stagnant-exit now fires only if the trade is clearly adverse OR genuinely dead-flat. Slow-positive trades are allowed to continue toward target. *(Superseded by #172 two-tier; thresholds retained as Tier-1.)* | Execution |
| 148 | **Stale SL-M cleanup on restart.** After a crash `load_existing_positions()` rebuilt positions from Zerodha MIS but never reconciled pending SL-M orders. Result: orphan SL-M orders could fire against fresh positions, or the bot would place a fresh software SL while a hidden live SL-M still existed — two exit orders racing. Fix: new `zerodha.get_orders()` wrapper and `_reconcile_orphan_sl_m()` helper run unconditionally after `load_existing_positions` and `sync_external_positions`. For each live SL-M: attach to a matching (symbol, exit_side) position and resize if qty drifted; cancel today-orphans; preserve stray prior-day orders with loud warning. Orders already in `_pending_order_ids` are filtered out so periodic reconciles never cancel the bot's own valid SL-Ms. Qty-mismatch resize now inspects `_sl_order_id` before/after the helper call (which swallows exceptions) and logs `SL_M_UNPROTECTED` at ERROR level if cancel landed but place failed. DRY_RUN short-circuits; API failures are swallowed so startup never crashes. | Bug Fix |
| 150 | **Partial-qty exit truncation.** `_place_exit_order` for partial-profit taking requested `partial_qty` via MARKET but only returned the fill price; caller set `pos["qty"] = remaining_qty` assuming full fill. On illiquid MARKET fills (actual partial fill) the position qty drifted from the live share count, and the resized exchange SL-M was off by the unfilled shares. Fix: `_place_exit_order` now returns `(fill_price, actual_filled_qty)` using `get_order_filled_qty()`. Caller subtracts actual filled qty, logs a WARN if less than requested, skips SL-M resize when nothing filled. DRY_RUN returns the requested qty (no behavior change). | Bug Fix |
| 151 | **External partial close detection.** Previously `sync_external_positions()` compared only symbol-set membership. If the user closed *half* on Kite, the symbol was still present on Zerodha → bot silently kept tracking the old qty, broker-side SL-M still sized for the original qty. Fix: new per-(symbol, side) qty map. When Zerodha qty < tracked qty, reduce pos["qty"] to match, log `EXTERNAL_PARTIAL` with estimated slice P&L, and resize the exchange SL-M (`_replace_exchange_sl`). Qty is NEVER rolled back on resize failure (the user really did close the slice — rolling back would make the bot try to SL-exit shares that no longer exist); instead we log `EXTERNAL_PARTIAL_UNPROTECTED` at ERROR level so software SL continues to guard the reduced qty. External qty *increases* are still ignored (safe default). | Bug Fix |
| 157 | **ADX + DI directional entry gate.** Entries used combined_score + VWAP + patterns but never gated on ADX/DI. On chop days (range-bound NIFTY, ADX < ~18), trades fired and immediately hit stagnant-exit or candle-protect, burning ~Rs.40 round-trip each. Fix: in `enter_trade`, after RSI checks, require either (a) ADX ≥ `ADX_MIN_THRESHOLD` (18) AND DI aligned with side (+DI > −DI for BUY, reverse for SELL), or (b) `|score| ≥ ADX_OVERRIDE_SCORE` (7) for high-conviction overrides. ADX is passed through from scanner via new `_entry_adx / _entry_plus_di / _entry_minus_di` fields — populated on BOTH the rule-based PENDING trade path AND the AI/Claude enrichment path (`_enrich_trades_with_indicators`) so the gate applies uniformly. Fails open when ADX is missing. Kill-switch: `ADX_ENTRY_GATE_ENABLED=False`. | Execution |
| 145 | **ATR-based position sizing.** Old formula `qty = budget_per_slot / price` gave equal rupee exposure to every stock — a Rs.500 stock with Rs.1 SL risked Rs.50, while a Rs.2000 stock with Rs.20 SL risked Rs.500 on the same qty. Single SL on high-ATR name ate 5 winners on low-ATR names. Fix: after ATR/SL is computed in `enter_trade`, compute `risk_rupees = budget × RISK_PER_TRADE_PCT (0.5%)` and `risk_qty = risk_rupees / sl_distance`. Final qty = `min(price_qty, risk_qty)` — only REDUCES, never increases. If 1 share exceeds the per-trade risk budget, trade is rejected (too volatile for the account size). ATR missing → falls through to price-based sizing unchanged. Kill-switch: `ATR_SIZING_ENABLED=False`. | Execution |
| 160 | **Empty-API glitch guard on sync.** `sync_external_positions` previously mass-closed every tracked open position if Zerodha's `/positions` endpoint returned `net=[]` (transient API glitch). Each would be marked `EXTERNAL_CLOSE`, its SL-M cancelled, monitoring stopped — leaving the position unprotected until 3:20 auto-square-off. Fix: when the API returns empty `net` AND the bot tracks ≥1 OPEN position, log a warning and skip the close-detection cycle entirely (including the subsequent reconcile). Next sync cycle retries with fresh data. | Bug Fix |
| 161 | **Per-symbol re-entry cooldown.** After ANY exit (SL, target, stagnant, external), the same symbol+direction could be re-opened on the next scan tick if score conditions held — burning Rs.40 round-trip with no new information. Fix: new `_last_exit_time` map stamped on every exit path (`exit_position` + EXTERNAL_CLOSE). `enter_trade` rejects re-entries of same `SYMBOL_SIDE` within `RE_ENTRY_COOLDOWN_MINUTES` (default 30). Opposite direction (reversal setups) stays allowed; `RE_ENTRY_SCORE_OVERRIDE` (default 7.0) lets very high-conviction signals bypass. Kill-switch: `RE_ENTRY_COOLDOWN_ENABLED=False`. | Execution |
| 162 | **Charge-aware minimum target.** Net-of-charges R:R already demanded ≥1.0:1, but a trade with Rs.10 gross target on Rs.4 round-trip charges still passed (net 0.6:1 on the R:R side but a razor-thin absolute profit after any slippage). Fix: after the existing net R:R check in `enter_trade`, reject when `gross_target_profit < MIN_PROFIT_CHARGE_MULTIPLE × round_trip_charges` (default 2×). Ensures target has at least 1× charges as cushion. Uses the same `Config.calculate_charges(qty=2)` call already computed for the net R:R gate (no extra cost). Kill-switch: `MIN_PROFIT_CHARGE_MULTIPLE ≤ 0`. | Execution |
| 163 | **Daily-loss soft-stop hysteresis.** Only one loss threshold existed: the hard `MAX_LOSS_PER_DAY_PCT` (3%) circuit breaker, which closes ALL positions. This forced binary behaviour — trade freely or exit everything — so a typical -1.5% drawdown kept the bot opening fresh losers hoping to recover. Fix: new `DAILY_LOSS_SOFT_STOP_PCT` (default 1.5%). When day P&L ≤ -soft, `enter_trade` rejects NEW entries but existing positions continue to be managed. Hard CB still fires at 3%. Kill-switch: `DAILY_LOSS_SOFT_STOP_PCT = 0`. | Risk |
| 164 | **Lunch-lull entry skip.** 11:30-12:15 IST is the lowest-volume, lowest-ADX window on NSE — most bot churn trades fire here and immediately hit stagnant-exit or candle-protect. Fix: new `is_lunch_lull()` helper. `enter_trade` rejects new entries inside the window unless `abs(score) ≥ LUNCH_LULL_SCORE_OVERRIDE` (default 6.0). Window is boundary-exclusive on the right (12:15 is NOT lull). Configurable start/end hour+minute. Kill-switch: `LUNCH_LULL_ENABLED=False`. | Execution |
| 165 | **Dynamic budget-regime config.** Small accounts (<Rs.30k) need tighter gates — one losing trade hurts much more than on a Rs.5L account. Rather than re-tuning every constant when budget scales, regimes now apply deltas: TINY (<30k), SMALL (<1L), NORMAL (<5L), LARGE (≥5L). ADX threshold shifts `{+2, +1, 0, -1}`, trade cap `{-4, -2, 0, +3}`, MIN_SCORE `{+1.0, +0.5, 0, 0}`. New `Config.budget_regime()` + `OrderEngine.effective_*()` helpers; `enter_trade`'s ADX and trade-cap reads use them. Scanner's existing `min_score_override` logic takes the max of LOSS_SCORE_BUMP and regime delta. Kill-switch: `BUDGET_REGIME_ENABLED=False` → all reads fall back to base config. | Risk |
| 169 | **Defensive `score == 0` entry skip.** Three call sites in `stock_scanner_v2.py` and `portfolio/manager_v2.py` used `side = "BUY" if score > 0 else "SELL"`, which would force-short any zero-score candidate. `V2_MIN_SCORE` (≥2.0) prefilter normally blocks zeros, but a future tweak that lowered the floor (or a regime delta pushing it to zero) would expose the bug. Fix: explicit `if score > 0: BUY elif score < 0: SELL else: skip` at all three sites. Skips log a WARNING (scanner direction-split) so future regressions are visible. No behavioural change today — zero-score candidates have always been blocked upstream. | Bug Fix |
| 170 | **Centralised Claude model in `generate_sheet.py`.** Script hard-coded `claude-sonnet-4-20250514` (a snapshot id) in two `messages.create()` calls. When Anthropic deprecated that snapshot, the post-trade sheet generator silently broke even though the live analyser kept working off `Config._CLAUDE_RULES`. Fix: read `Config.claude()["model"]` once at import-time into `CLAUDE_MODEL`, use that everywhere. Single source of truth across the bot and all scripts. | Bug Fix |
| 171 | **Budget double-counting bug (CRITICAL).** `refresh_budget()` overwrote `_budget` with Zerodha's `available` funds (which Zerodha had ALREADY reduced by margin blocked on open positions). The downstream budget check then subtracted `_total_open_exposure()` from this already-shrunken `_budget`, double-counting the same blocked margin and blocking legitimate mid-day re-entries. On 2026-04-20 this rejected TRENT (₹4,500 share, ~₹218 expected profit) even though ≈₹11K was actually deployable — budget log showed only ₹3,303 remaining. Side-effects: `loss_adjusted_budget()` and ATR-based qty sizing both read the deflated `_budget`, shrinking risk allowance and causing further phantom rejections. Fix: keep `_budget` as the configured cap (immutable post-`set_budget`) and store live Zerodha available in a new `_available_funds` field. Budget check now takes `min(loss_adjusted_budget() - exposure, _available_funds)`. `budget_remaining()` mirrors the same clamp so Claude prompts and budget displays match what the broker will actually permit. `set_budget()` clears the stale live reading; manager seeds `_available_funds` at startup so day-1 entries also respect any pre-existing manual MIS positions. Also surfaces silent fund-fetch failures as WARNINGs (was bare `except: pass`). | Bug Fix |
| 172 | **Two-tier stagnant exit (drift catcher).** Single 45-min directional check (adverse / dead-flat ±0.1%) was missing drifters that wiggled just outside the dead-flat band on the snapshot tick. UNITDSPR 2026-04-20 sat 183 min for +0.03% before LOSER_EXIT caught it — burned a slot for 3 hours on a ₹50K/3-slot portfolio while morning trades were turning ~₹200 each in 30-60 min. Fix: add Tier-2 hard-max check at `STAGNANT_HARD_MAX_MINUTES` (90 min) using `progress_pct = move_toward_target / (target-entry) * 100`. Exits if `progress_pct < STAGNANT_MIN_PROGRESS_PCT` (25%). Target-relative so it scales naturally with the trade's own R:R. Tier-1 unchanged — no regression on the Apr-17 directional-split benefit. progress_pct clamped ±100% to keep logs readable on extreme cases. Three new configs, all kill-switchable via `STAGNANT_HARD_MAX_ENABLED`. Same `_stagnant_exits` guard blocks same-side re-entry. Also removed the leftover `STAGNANT_EXIT_MIN_MOVE_PCT` field that was kept as a no-op since 2026-04-17. | Execution |

---

## Pending — Details

In priority order, matching the Pending table above.

### 166. Unrealised-MTM-Aware Circuit Breaker
- **Priority**: MEDIUM
- **Today**: `OrderEngine.check_circuit_breaker()` uses `day_pnl()`, which sums only **CLOSED** positions plus already-booked partial profits on still-open positions. Open-position MTM is excluded. Five positions each bleeding -1.5% MTM = -7.5% real exposure, but CB at 3% will not fire until SLs actually hit — by which time real loss can far exceed the 3% cap. Soft-stop (#163) has the same blind spot.
- **Fix**: Add `unrealised_pnl(quotes)` (already exists) into both `check_circuit_breaker()` and `_check_daily_loss_soft_stop()` via a new `effective_day_pnl(quotes)` helper. Pass the live quote dict in from the monitor loop. Behaviour stays identical when there are no open positions.
- **Effort**: Low. ~30 lines, single helper, kill-switch via new `MTM_AWARE_CB_ENABLED`.

### 144. Bracket Orders (Atomic Entry + SL + Target)
- **Priority**: MEDIUM (safety upgrade, not a miss-profit fix)
- **Today**: For every trade we submit three separate things — the entry order, then a stop-loss order after the entry fills, then a software-side target watcher. If the bot crashes in between any of these, the position can be left un-protected. If the user manually closes the position in the Kite app, the SL-M can still be alive and mis-trigger later.
- **Fix**: Use Zerodha Bracket Order (BO). Entry + SL + target are submitted as one linked order. When the entry fills, the exchange itself arms the SL and target; when either fires, the other auto-cancels. One atomic state per trade, no orphans.
- **Effort**: ~2 weeks. Touches `order_engine`, reconciliation, and crash-recovery logic.

### 44. WebSocket Tick Data
- **Priority**: MEDIUM (implement when polling latency causes measurable slippage)
- **Today**: 10-second polling can miss rapid SL/target breaches during news events.
- **Fix**: Use Zerodha WebSocket (up to 3000 instruments) for real-time tick data on open position symbols. SL/target checks on every tick.
- **Note**: Exchange SL-M orders (#60) already handle instant SL execution. WebSocket mainly improves target hits and trailing SL responsiveness.

### 167. Earnings / Results-Day Blackout
- **Priority**: MEDIUM
- **Today**: NSE quarterly results clusters (mid-Jan, mid-Apr, mid-Jul, mid-Oct) routinely produce ±5–10% one-day moves on individual stocks the day of (or after) the announcement. The bot has no awareness of which stock reports today — it can size into INFY 30 minutes before the company drops Q-results. ATR is meaningless on those days.
- **Fix**: Maintain `EARNINGS_BLACKOUT` (date → list of symbols), populated either manually each quarter or scraped from BSE corporate-actions feed. Scanner drops symbols whose blackout date ∈ {today, today−1} (post-announcement gap day also dangerous). Possibly extend to −1 day for known volatile names.
- **Effort**: Medium. Data source + config + scanner filter. ~50 lines.

### 149. Sector-Cascade Exit
- **Priority**: MEDIUM (protects against correlated sector-wide drops)
- **Today**: If 3 banking positions are all bleeding because the banking sector is dropping 2% in 15 min, each position waits for its own individual SL. By the time the 3rd hits, the 1st has lost much more than necessary.
- **Fix**: Every scan, roll up per-sector open P&L. If a sector's exposure is ≤ −1% of budget in ≤ 15 min, tighten SLs on **all** positions in that sector to breakeven immediately — don't wait for individual SLs.
- **Effort**: Medium. Needs sector P&L rollup + a new "panic-tighten" path in the engine. Research the threshold first.

### 168. Intraday Equity-Peak Drawdown Stop
- **Priority**: LOW
- **Today**: Soft-stop (#163) and CB both measure loss vs the day's starting budget. If the bot is +2% by 11 AM and gives it all back to +0.2% by 1 PM, neither fires — the give-back is invisible to the loss gates because total day P&L never went negative. Pro intraday desks track equity high-water mark and pause new entries on a defined drawdown from peak.
- **Fix**: Track `_intraday_peak_pnl = max(_intraday_peak_pnl, day_pnl())` each scan. If `(_intraday_peak_pnl - day_pnl()) / budget > PEAK_DRAWDOWN_STOP_PCT` (default 1.5%), block new entries for the rest of the day (existing positions managed normally). Same hysteresis pattern as #163.
- **Effort**: Low. ~25 lines + config + kill-switch.

### 158. Regime-Shift Opportunity Window
- **Priority**: LOW (small but non-zero alpha)
- **Today**: When NIFTY flips (e.g., morning BEARISH → afternoon BULLISH), regime-shift-protect tightens SLs on contrary positions but we do not actively look for **aligned** new entries. Meanwhile, stagnant-exit keeps firing on slow-positive trades entered under the old regime.
- **Fix**: For `REGIME_OPPORTUNITY_WINDOW_MINUTES` (default 30-60) after a flip: (a) pause stagnant-exit on positions aligned with the new regime, (b) lower the score threshold slightly for same-direction re-entries, (c) skip sector-cap for one aligned entry. Log a clear "REGIME OPPORTUNITY" banner.
- **Effort**: Low. Config + a timestamp on regime change + gating in `check_stagnant_positions` and the entry path.

### 147. Session-Time-Aware RVol Baseline
- **Priority**: LOW (current RVol is "good enough" for the pre-filter)
- **Today**: RVol compares today's intraday volume to the 20-day **daily** average. But intraday volume has a U-shape (huge at open, dead 12–13:30, huge at close). Midday, almost every stock looks "quiet" by daily-average math — we may be skipping good trades.
- **Fix**: Build a 20-day average for each 30-minute bucket of the day. Compare today's 12:30 volume to the historical 12:30 volume.
- **Effort**: Medium. Needs a new cache of hourly-bucket volume history.

### 24. Backtesting Framework
- **Priority**: LOW (deferred — use live trade analytics first)
- **Gap**: No way to measure which indicators actually contribute to winning trades.
- **Fix**: Replay V2 scoring on historical 15-min data, simulate ATR-based entries/exits, compute win rate per indicator combination.
- **Source**: Every professional quant desk backtests before going live.
- **Note**: We have 80+ live trades with full indicator snapshots in SQLite. Use `python scripts/view_performance.py --summary` to identify patterns before building a full framework.

### 169. Defensive `score == 0` Entry Skip
- **Status**: ✅ Completed (see #169 in Completed table).

### 170. Move Claude Model String Out of `generate_sheet.py`
- **Status**: ✅ Completed (see #170 in Completed table).

### 41. Holiday-Shifted Expiry Detection
- **Priority**: LOW (~3 days/year edge case)
- **Gap**: Thursday expiry detection uses `weekday == 3`. When Thursday is an NSE holiday, expiry shifts to Wednesday. The bot then trades expiry-day volatility with non-expiry SL/score/position settings.
- **Fix**: Maintain a list of actual expiry dates from NSE published calendar alongside the holiday list. `manager._apply_expiry_day_adjustments()` reads that list instead of `weekday == 3`.
- **Effort**: Low. Config list + one helper.

