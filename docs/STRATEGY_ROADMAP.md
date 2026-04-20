# Strategy Roadmap

Research-backed improvements for the V2 intraday trading bot. Sources: Investopedia, Zerodha Varsity, Toby Crabel (ORB), institutional intraday practices, and real trade data analysis (80+ live trades, April 2026).

---

## How to Read This Roadmap

This document is the **history log** of every strategy improvement, the **backlog** of pending items, and the **record** of deliberately rejected ideas. Someone new to the project should be able to read this document top-to-bottom and understand what has been built, why, and what is next.

### Sections

1. **Pending** — items still to be built. Each has priority + impact + effort estimates.
2. **Pending — Awaiting Trade Data** — promising ideas blocked on insufficient real-trade evidence. Each lists the trigger (minimum sample size + threshold) that would promote it to the main Pending list. Do NOT implement these speculatively.
3. **Removed** — items evaluated and rejected. The reason column explains why (saves us from re-proposing them).
4. **Completed** — everything shipped, grouped by category. The `#` column is the historical item number (don't renumber — it breaks references in commit messages and other docs).
5. **Pending — Details** — long-form explanations for complex pending items.

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

0. **Check first that the item does not already exist.** Search this file (Ctrl+F or `grep`) for keywords from the proposed change — in **Pending**, **Pending — Awaiting Trade Data**, **Completed**, AND **Removed**. If a similar idea is already present, update the existing row instead of creating a duplicate. Adding the same idea twice under different numbers wastes review time and corrupts the counts.
1. **Verify the gap is real against the current code, not against memory.** Open the file you intend to fix and confirm the assumed condition (config value, threshold, missing logic) actually matches what you observed. Many "gaps" turn out to be false alarms once the actual config / log / snapshot values are checked. Cite the exact line numbers in the entry's description.
2. Pick the next available number (currently 185 and above are free).
3. Add it under the matching **category** heading in **Completed** (Indicators / Risk Management / Execution / Market Intelligence / Infrastructure / Bug Fixes). If none fits, add a new category heading — do NOT create per-review/per-date sub-headings.
4. Keep the one-line description short but specific. If context matters, use a longer description on the same row (see items #137, #140, #146).
5. Bump the count in the category sub-heading and the top-line Completed count.
6. If it changes user-visible behaviour, also:
   - Update the relevant section in [STRATEGY_V2.md](STRATEGY_V2.md).
   - If it introduces a new technical term, add a glossary entry there.
7. If the idea is still planned, add it to **Pending**. The Pending table is sorted by **priority first** (HIGH → MEDIUM → LOW), then by **impact** (Highest → High → Medium → Low) descending, then by **effort** (Low → Medium → High) ascending. The `#` column is just the historical id — row position is by priority, not by number. Insert the new row at the correct sorted position; do NOT append blindly to the bottom.
8. Also add a long-form entry under **Pending — Details** in the same priority order as the table. Include: Priority, Today (the gap), Fix, Effort.
9. If the idea is explicitly rejected, add it to **Removed** with the reason (future-you will thank you).
10. If the idea is plausible but rests on too few real trades to justify shipping, add it to **Pending — Awaiting Trade Data** with a clear, measurable promotion trigger (sample size + threshold). Do NOT add it to the main Pending table until that trigger fires.

---

## Status Overview

### Pending (11 items)

Sorted by priority (HIGH → MEDIUM → LOW), then impact desc, then effort asc.

| # | Improvement | Priority | Impact | Effort |
|---|------------|----------|--------|--------|
| 181 | India VIX intraday-spike pause — when `(VIX_now - VIX_open)/VIX_open ≥ 10%` OR `VIX_now ≥ 25`, pause new entries 15 min; existing positions managed normally | MEDIUM | High | Low |
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

### Pending — Awaiting Trade Data (7 items)

These ideas look reasonable on paper but rest on too few data points to justify shipping. Each lists the **minimum sample size** that would let us promote it to the main Pending list (or move it to Removed). Until then, **do not implement** — collect the trades first, then re-evaluate.

| # | Idea | Trigger to revisit |
|---|------|--------------------|
| 175 | **Lunch-lull score floor raise** (6.0 → 7.0, or `RVol ≥ 1.5x`). Today the lunch-lull bypass admits any candidate with `\|score\| ≥ 6.0`. HDFCBANK 2026-04-20 entered at exactly 6.0 with `RVol 1.2x` and lost Rs.155. Raising the floor would have skipped it, but a single trade is not a population. | After **≥ 10 lunch-lull entries** (11:30-12:15 IST), compare hit-rate / R-multiple of those scoring 6.0-6.9 vs 7.0+. If 6.0-6.9 underperforms 7.0+ by ≥ 30% on hit-rate, promote to main Pending and tighten `LUNCH_LULL_SCORE_OVERRIDE` to 7.0. |
| 176 | **Bank/financial sector NIFTY-alignment filter.** Banks (HDFCBANK, ICICIBANK, SBIN, AXISBANK, KOTAKBANK) have ~1.0+ beta to NIFTY (financials are ~36% of the index weight). A BUY on a bank when NIFTY is trending DOWN bets against the index's own gravity (inverse for SELL when NIFTY is up). Today only one data point (HDFCBANK 2026-04-20). | After **≥ 20 bank-direction trades** (BANKING/FINANCE sector, both directions), compare hit-rate when entry direction is NIFTY-aligned vs contra-NIFTY. If contra-NIFTY underperforms aligned by ≥ 25% hit-rate, promote and add a per-sector NIFTY-alignment gate before entry. |
| 178 | **`RSI_BUY_BLOCK_THRESHOLD` lowered (75 → 70).** Today's UNITDSPR entered BUY at RSI 69.6 with score 6.2, sat 3 hours bleeding flat, and exited LOSER_EXIT at +Rs.6 net. The current RSI-contradiction gate ([order_engine.py:1685](../services/order_engine.py)) blocks BUY only at RSI > 75 — anything 70-75 sails through even though it is statistically overbought. Initially misdiagnosed as a VWAP-extension gate issue (`VWAP_EXT_SCORE_OVERRIDE` 6.0→7.0); UNITDSPR's actual `vwap_dev` was +0.45% (well below the 0.8% extension cap) so that gate never fired. The real root cause is the RSI ceiling. Single data point. | After **≥ 10 BUY entries with RSI 70-75** (or **≥ 10 SELL entries with RSI 25-30** for the symmetric SELL ceiling), compare hit-rate vs entries with RSI < 70 (or > 30). If 70-75 underperforms by ≥ 30% hit-rate or generates ≥ 2× more LOSER_EXIT outcomes, promote and lower `RSI_BUY_BLOCK_THRESHOLD` to 70 (mirror `RSI_SELL_BLOCK_THRESHOLD` 25→30). |
| 179 | **Per-window entry-burst cap.** Today opened 3 positions in 17 seconds (RECLTD 10:12:29, ABB 10:12:31, ENRIN 10:12:46) — all worked, but if 10:13 had reversed, all three would have gone red simultaneously and possibly tripped the soft-stop within minutes. No structural cap exists on entry pace. | After **≥ 5 trading days with ≥ 2 sub-60s entry bursts**, measure (a) drawdown when bursts occurred vs day average, (b) correlation of burst-entries' exit P&L (do they win/lose together?). If burst days show ≥ 1.5× the typical drawdown OR burst-entries are ≥ 70% correlated in outcome, promote and add `MAX_ENTRIES_PER_60S = 2`. |
| 182 | **Pre-open auction tape classification (gap fade vs follow-through).** We classify gap *magnitude* (`GAP_UP_STRONG` etc.) but not gap *quality*. A `GAP_UP_STRONG` with low pre-open auction volume tends to fade by 09:45; same gap with high auction volume tends to follow through. The 9:00-9:08 IST pre-open auction is available from Zerodha. Could refine the gap-coherence gate (#173) override threshold by auction-volume tier. | After **≥ 20 strong-gap entries** (`GAP_*_STRONG` either side), bucket by pre-open auction volume tertile (low/mid/high) and compare hit-rate per bucket. If low-volume gaps underperform high-volume by ≥ 25% hit-rate, promote and add a per-bucket override threshold. |
| 183 | **Advance-decline breadth filter on the pre-filter set.** Scanner produces top-N candidates; we don't compute the BUY/SELL ratio across that set. If 70%+ of pre-filtered candidates lean SELL, the broader tape is bearish — BUYs from that day's set probably underperform regardless of individual score. Currently no breadth-aware adjustment beyond NIFTY trend (which lags intraday). | After **≥ 15 trading days** with breadth metric logged (BUY count vs SELL count in scanner output), measure hit-rate of BUY entries on days when ≥ 70% of pre-filter set was SELL-leaning. If those BUY entries underperform same-direction-as-breadth entries by ≥ 30% hit-rate, promote and add a `BREADTH_CONTRA_SCORE_PENALTY = 0.5` (subtract from contra-breadth entries' score) or hard-block contra-breadth entries below score 6.5. |
| 184 | **Mid/small-cap liquidity discount on impact-cost cap.** `MAX_IMPACT_COST_PCT = 0.2` is one-size-fits-all. NIFTY50 names easily clear this; mid/small caps with ATR > 2% routinely sit at 0.25-0.4% impact cost and get rejected even on strong setups, OR squeeze through and slip badly on exit. A regime-based cap (NIFTY50: 0.2, NIFTY100: 0.3, others: 0.4) would let valid mid-cap entries through with realistic slippage budgets. | After **≥ 30 mid-cap-name entries** (outside NIFTY50, ATR > 1.5%), compare exit slippage vs entry slippage and net P&L vs a NIFTY50 baseline cohort. If mid-cap exit slippage averages > 2× entry slippage AND mid-cap net P&L underperforms NIFTY50 cohort by ≥ 20%, promote and add per-tier `MAX_IMPACT_COST_PCT_BY_INDEX_TIER`. |

These items are intentionally NOT in the main Pending table or Pending — Details list. Implementing them now would be guessing; we already have the data-collection path (every entry logs score / RVol / sector / NIFTY trend), so the right move is to wait.

**Instructions for Copilot (and any future reviewer):**

Whenever you review this roadmap (during a code review, end-of-day analysis, weekly retro, or when the user asks "what's next?"), you MUST:

1. **Check the trade ledger** for each item in this section. Use `scripts/view_trades.py`, `scripts/view_performance.py`, or query `data/trades.db` directly to count the relevant trades since the item was added.
2. **Compare actual trade count vs the trigger** stated in the "Trigger to revisit" column. The trigger is a hard gate — do not eyeball it.
3. **If the trigger has been met**, compute the metric the trigger asks for (hit-rate gap, R-multiple gap, etc.) and **proactively raise it with the user**. Use this exact format:

   > 🔔 **Awaiting-data item #NNN is ready for review.** We now have `<actual count>` trades (trigger was `<minimum>`). Measured `<metric>` is `<value>` vs threshold `<target>`. Recommendation: **promote to Pending / move to Removed** because `<one-line reason grounded in the numbers>`. Should I implement it?

4. **Do NOT silently implement** an awaiting-data item even if you believe the data supports it. The user must explicitly approve the promotion. The whole point of this section is to force evidence-based decisions instead of speculative coding.
5. **If the trigger has NOT been met**, do nothing — do not nag, do not partially implement, do not lower the threshold. Just note the current count in your review summary so progress is visible (e.g. "#175: 4 / 10 lunch-lull entries collected").
6. **If new evidence makes an item obviously wrong** (e.g. lunch-lull 6.0-6.9 trades outperform 7.0+), say so and propose moving it to **Removed** with the reason.

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

### Completed (154 items)

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
| **Risk Management (33)** | | |
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
| 180 | Circuit-limit (UC/LC) entry guard — reject BUY when intraday move ≥ +19% (within 1% of upper circuit) and SELL when ≤ -19% (within 1% of lower circuit). Near the ±20% freeze the order book becomes one-sided: SL-M sits dead, MIS auto-square at 15:20 takes whatever distressed price exists, and post-freeze unwinds slip 5-15 Rs/share. `CIRCUIT_LIMIT_BUFFER_PCT = 1.0`, kill-switch `CIRCUIT_LIMIT_GUARD_ENABLED`. Fail-open when `prev_close` missing in the live quote. | Risk |
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
| **Infrastructure (12)** | | |
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
| 177 | **Post-trade rejection audit.** Every entry the order engine SKIPPED (R:R, RVol, ADX, lunch-lull, gap-coherence, charge-floor, etc.) was previously fire-and-forget — the WARNING line went to the log and was never reviewed. After yesterday's HDFCBANK/UNITDSPR analysis showed 17 of today's 39 rejections actually saved real money (Rs.3,529 avoided losses vs Rs.377 missed profit), turned this into a recurring EOD review aid. New `scripts/rejection_audit.py` parses `logs/portfolio.log` for the date, fetches each rejected stock's 15:30 close from Zerodha (5-min candles, rate-limited), and computes a verdict per symbol: `AVOIDED_LOSS` / `AVOIDED_MILD` (gate saved money), `MISSED_PROFIT` / `MISSED_MILD` (gate may be too strict), or `NEUTRAL` (±0.5% drift). Per-symbol P&L assumes 1 hypothetical slot at `budget / max_positions`. Manager Step 12 calls `run_audit()` after Step 11 verification — output is logged live AND appended to `trading_report_DD.txt` between `<!-- REJECTION_AUDIT_BEGIN/END -->` markers (idempotent — re-runs replace, never duplicate). Read-only: never touches positions or the engine. Disabled in DRY_RUN; kill-switch `REJECTION_AUDIT_ENABLED=False`. CLI for back-fill: `python scripts/rejection_audit.py --date YYYY-MM-DD --append-report`. | Infra |
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
| 172 | **Two-tier stagnant exit (drift catcher).** Single 45-min directional check (adverse / dead-flat ±0.1%) was missing drifters that wiggled just outside the dead-flat band on the snapshot tick. UNITDSPR 2026-04-20 sat 183 min for +0.03% before LOSER_EXIT caught it — burned a slot for 3 hours on a ₹50K/3-slot portfolio while morning trades were turning ~₹200 each in 30-60 min. Fix: add Tier-2 hard-max check at `STAGNANT_HARD_MAX_MINUTES` (90 min) using `progress_pct = move_toward_target / (target-entry) * 100`. Exits if `progress_pct < STAGNANT_MIN_PROGRESS_PCT` (20% — lowered from initial 25% same day after re-review showed 25% projected target hit past close). Target-relative so it scales naturally with the trade's own R:R. Tier-1 unchanged — no regression on the Apr-17 directional-split benefit. progress_pct clamped ±100% to keep logs readable on extreme cases. Three new configs, all kill-switchable via `STAGNANT_HARD_MAX_ENABLED`. Same `_stagnant_exits` guard blocks same-side re-entry. Also removed the leftover `STAGNANT_EXIT_MIN_MOVE_PCT` field that was kept as a no-op since 2026-04-17. | Execution |
| 173 | **Gap-coherence entry gate.** Pre-trade check rejected nothing on opening-gap direction. Pro intraday desks treat a strong gap as the day's tape-print of overnight institutional positioning + opening flow; taking a BUY on `GAP_DOWN_STRONG` (or SELL on `GAP_UP_STRONG`) means trading against that flow, and intraday V-recoveries of strong gaps are the exception. HDFCBANK 2026-04-20: bot took BUY on `GAP_DOWN_STRONG` at the lunch-lull score floor (+6.0); the trade lost Rs.155, and the very next scanner tick scored it -10.0 STRONG_SELL. Fix: new `GAP_COHERENCE_GATE_ENABLED` block in `enter_trade` (after the ADX gate, uses the same `_indicator_snapshot` JSON path as the VWAP guard). Reads `gap` from the snapshot (newly added in `_build_indicator_snapshot`); rejects contradictory entries unless `\|score\| ≥ GAP_COHERENCE_OVERRIDE_SCORE` (default 7.5, well above the +6 lunch-lull floor and the +5 strong-direction threshold). Only acts on the high-conviction `GAP_*_STRONG` signals — `WEAK` and `NO_GAP` are unaffected. Fails open on missing/malformed snapshot (logs a WARNING, lets trade through; other gates remain active). Override path also logs success for visibility. | Risk |
| 174 | **Signal-reversal hard exit on held positions.** Static SL-M only catches price-side moves. The free 15-min candle re-scan was already running on every open position (V2_CANDLE_RESCAN_MINUTES) and `_auto_protect_on_contrary_signal` was tightening SL on score ≤ -4 / ≥ +4 — but a brutal opposite-direction flip with a confirming reversal candle still meant waiting for the price stop to fire. HDFCBANK 2026-04-20: bot held BUY from 11:31; price stop fired 13:26 for Rs.-155; the very next scanner tick (13:27) scored -10.0 STRONG_SELL with `EVENING_STAR + BEARISH_HARAMI`. The bearish patterns had been forming for ~30 min before the SL hit. Fix: new `_signal_reversal_exit()` method in `manager_v2.py` runs FIRST in the candle re-scan loop (before `_auto_protect_on_contrary_signal`). Triggers when held BUY scores ≤ `-SIGNAL_REVERSAL_SCORE` (default -7.0) AND a confirming bearish reversal pattern is present (`EVENING_STAR`, `BEARISH_ENGULFING`, `BEARISH_HARAMI`, `SHOOTING_STAR`, `HANGING_MAN`, `THREE_BLACK_CROWS`); mirrored bullish set for held SELLs. Exits via `engine.exit_position(..., "SIGNAL_REVERSAL")`. Skipped when position is in profit ≥ 1× initial risk (winners belong to the trailing stop — one bad 15-min candle shouldn't dump a paid-up trade) or live price is missing. Pattern names verified against `services/candle_patterns.py`. Three configs: `SIGNAL_REVERSAL_EXIT_ENABLED` (kill-switch), `SIGNAL_REVERSAL_SCORE` (threshold), `SIGNAL_REVERSAL_REQUIRE_PATTERN` (confirming-pattern requirement). Validates via `Config.validate_ranges()`. | Risk |

---

## Pending — Details

In priority order, matching the Pending table above.

### 181. India VIX Intraday Spike Pause
- **Priority**: MEDIUM
- **Today**: VIX *regime* is read at scanner level (#23) and adjusts thresholds, but there's no detector for an *intraday* VIX shock. A 12% VIX spike inside a single 15-min window means a black-swan move is in progress (RBI surprise, geopolitical headline, gap-down on a constituent that's dragging the index). New entries during that window have terrible risk/reward — the volatility gets priced into spreads before a trend establishes.
- **Fix**: Cache `vix_open` once per session. On each scan, fetch `vix_now`. If `(vix_now - vix_open) / vix_open >= VIX_SPIKE_THRESHOLD_PCT` (default 10) OR `vix_now >= VIX_SPIKE_ABSOLUTE_LEVEL` (default 25), set `_vix_pause_until = now + 15 min` and skip new entries until then. Existing positions managed normally (SL-M, trailing, exits all unaffected). Kill-switch via `VIX_SPIKE_PAUSE_ENABLED`.
- **Effort**: Low. ~25 lines in `_check_vix_spike()` helper + entry-pipeline call.

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

