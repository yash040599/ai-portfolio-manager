# V2 Trading Strategy — Complete Reference
<!-- ══════════════════════════════════════════════════════════════
  MAINTENANCE NOTE — Keep this document in sync with code changes.
  
  This is the SINGLE source of truth for the V2 intraday trading
  strategy covering BOTH modes: NoAI (default) and Claude AI (--ai).
  
  Anyone reviewing this document should be able to:
    1. Understand every decision the bot makes and why
    2. Identify gaps, risks, or improvements in the strategy
    3. Verify that code behaviour matches this spec
  
  When updating code that affects strategy (config, indicators, order
  engine, scanner), update this document in the same commit.
  
  Last sync: 2026-05-07 — Loss-streak intervention pass. Three ships:
  #258 Risk — paused score-weighted sizing in live NoAI
  (`Config.SCORE_WEIGHTED_SIZING_ENABLED = False` default;
  `_score_weight_sizing()` short-circuits to equal sizing). Surfaced
  by `scripts/analyst_pulse_v2.py` over the 9-day rolling-loss window
  (n=55 trades, net −Rs.2,220) showing score-magnitude is
  **anti-correlated** with realised P&L for the score≥6 cohort
  (|score|≥9 = −Rs.51/trade; <6 = −Rs.0.28/trade) — the legacy #107
  sizing was concentrating MORE capital on the worst-performing
  buckets every session. Industry standard equal-weight (1/N) is the
  documented OOS-validated benchmark when factor confidence is low
  (DeMiguel/Garlappi/Uppal 2009). Re-enable trigger #258R.
  #262 Infra — scanner candidate log line now appends `[pre-open]`
  suffix when scan time is before the first 15-min candle close
  (09:30 IST). Reduces operator-debug overhead during the recovery
  window. New module-level `_is_pre_open_score_time()` helper in
  `services/stock_scanner_v2.py`. Single operator-facing surface
  shipped (Pending block proposed three; live code-walk found only
  one was meaningful — dashboard `entry_score` is post-pre-open by
  definition; report_writer has no candidate section). Pre-trade
  check count unchanged at 44.
  #268 Infra — broker session-VWAP drift sanity check. New
  `Config.VWAP_DRIFT_CHECK_ENABLED` (default True) +
  `Config.VWAP_DRIFT_WARN_PCT` (default 0.30 %). After every
  `_analyse_stock()` in `_prefilter_universe()`, compare Kite's
  exchange-truth `quote()['average_price']` to the candle-derived
  `result['vwap']`; emit a structured WARN line per drifted symbol
  plus one summary line at the end of the analyse loop when the
  per-scan counter is non-zero. **Pure observability — no entry
  gate logic touched.** Surfaces silent candle-cache gaps (network
  blip, late ingestion, symbol-add lag) that would otherwise corrupt
  the three downstream VWAP gates (#34 SD bands, #125 trend block,
  #228 statistical-band consolidation). Skips silently when either
  side is ≤ 0; defensive try/except around the entire check so a
  malformed quote payload can never break the analyse loop. Pre-trade
  check count unchanged at 44.

  Previous: 2026-05-07 — Phase-2 NoAI audit ship (#255 entry-path
  quote/depth retry-3 + fail-closed gates; #256 net R:R charge
  calculation side-aware; #257 phase-2 code/comment hygiene). Three
  Completed rows; pre-trade check count unchanged at 44 (none of the
  three shipped items added a new entry gate). Six new Pending items
  added (#258 pause score-weighted sizing, #259 per-candidate telemetry,
  #260 intraday volume baselines, #261 typed quote validator, #262
  pre-market score tagging, #263 docs cleanup) plus four Awaiting-Data
  triggers (#255R / #258R removal-triggers, #264 trend-cluster cap,
  #265 Scoring v3 bundle, #266 orders()-based EXTERNAL_CLOSE fill
  price). Same-pass review-cycle audit also corrected a long-standing
  Completed-table tally drift (Risk Management header 79→75, grand total
  214→209) and Awaiting-Data header (17→22), bringing every roadmap
  section header into line with its actual row count. See
  [docs/audit/NOAI_INTRADAY_AUDIT_2026-05-06.md](audit/NOAI_INTRADAY_AUDIT_2026-05-06.md)
  for the full audit input.

  Previous: 2026-05-07 — Multi-day directional pause + bypass paths (#251 / #251a fractional-Kelly opposing-thin cap / #251b NIFTY-bounce / #251c tape-breadth A/D divergence) + entry-burst cap retune (#179a).
  Added: per-symbol re-entry cooldown (30 min), charge-aware target multiple (2×),
  daily-loss soft-stop hysteresis (1.5%), lunch-lull entry skip (11:30-12:15),
  budget-regime gate deltas (TINY/SMALL/NORMAL/LARGE adjust ADX threshold,
  trade cap, min-score). New "Decision Timeline — Plain English" section at top
  of Strategy Flow walks through every decision from 9:00 AM to EOD with example
  log lines. Pre-trade check count: 22 → 29.
  
  2026-04-20 sync — HDFCBANK-driven safety upgrades (Roadmap #173-174).
  Added: gap-coherence entry gate (rejects BUY on GAP_DOWN_STRONG /
  SELL on GAP_UP_STRONG unless |score| ≥ 7.5) and signal-reversal hard
  exit (closes held positions when score flips to ±7 with confirming
  reversal candle, skipped if profit ≥ 1× initial risk).

  2026-04-22 sync — Pre-trade check count: 29 → 32. Added:
    • #190 Pattern-direction entry veto (row 14b) — rejects BUY when bearish
      reversal pattern present unless |score| ≥ 8.0; mirror for SELL.
    • #147 Session-time-aware RVol normalization — RVOL_FLOOR_BY_HOUR
      hour-bucket multiplier so midday lulls don't over-reject valid entries.
    • #191 Exchange-fired SL-M attribution fix — sync_external_positions
      now checks SL-M order status before labelling an exit EXTERNAL_CLOSE.
    • #168 Intraday equity-peak drawdown stop, #41 holiday-shifted expiry,
      #180 circuit-limit (UC/LC) entry guard (all already shipped earlier).
    • DRY refactor: BEARISH/BULLISH_REVERSAL_PATTERNS now live in
      services/candle_patterns (single source of truth shared by entry veto
      and exit-side signal-reversal).

  2026-04-22 sync (continued) — Pre-trade check count: 32 → 34. Added:
    • #192 Choppy-morning entry pause (row 0d) — pauses NEW entries when
      NIFTY ADX < 16 for 3 consecutive scans in the 09:30-10:30 window AND
      ≥2 STAGNANT/SIGNAL_DECAY exits occurred in the last 10 min. Sliding
      15-min pause; existing positions managed normally.
    • #195 Average-down prevention (row 16b) — after the 30-min cooldown,
      block re-entry of same SYMBOL_SIDE when |new_score - last_exit_score|
      ≤ 1.0 AND prior exit was STAGNANT/SIGNAL_DECAY within 120 min.
      Override at |score| ≥ 8.0.
    • #166 MTM-aware circuit-breaker / soft-stop / peak-drawdown — modify
      existing rows 0b, 0c (and the hard CB) to include open-position MTM
      via effective_day_pnl(); kill-switch MTM_AWARE_CB_ENABLED.
    • #194 Strong-gap ADX boost — when today's NIFTY gap is GAP_*_STRONG
      AND continues prior-day direction, raise effective_adx_threshold by
      +1 and effective_adx_override_score by +0.5 for fade-side trades only
      (BUY on a gap-DOWN day, SELL on a gap-UP day) for the rest of the day.
      Kill-switch STRONG_GAP_ADX_BOOST_ENABLED.

  2026-04-24 sync — Pre-trade check count: 34 → 38. Added:
    • #200 Pattern↔Tech contradiction penalty — scanner reduces final
      score when bullish pattern fires on bearish tech setup (or mirror)
      to filter false positives upstream of #190 entry veto. (gate 14c)
    • #201 VWAP statistical-band gate — adapts VWAP guard to intraday
      volatility using rolling SD bands instead of fixed % distance.
      (gate 17d)
    • #202 Late-entry tightening bundle — past LATE_ENTRY_HOUR, raises
      score floor by LATE_ENTRY_MIN_SCORE_BUMP (1.0 since #239, was
      0.5). (gate 18d only — the late-entry-only R:R floor and
      concurrent-position cap were removed by #225 because they were
      redundant with the always-on `RR_HARD_FLOOR` and the
      budget-scaled `dynamic_max_positions(budget)`.)
    • #203 Recover-prior-session-fills — synthetic CLOSED records built
      on restart from Zerodha order history when local DB is missing.
    • #204-208 Log-noise + naive-datetime cleanup (square-off SL clear,
      shutdown WARNING leading newline, R:R-rejection retry guard,
      rejection_audit IST timezone fix, cancel-order "does not exist"
      demoted to debug).
    • #209 Recovered-position rationale scrub (drop internal #203 tag).
    • #210 Order-placement double-fire guard — `_find_recent_matching_order`
      detects orders that reached Zerodha but whose response was lost,
      preventing the 3-retry loop from creating duplicate live orders.

  2026-04-24 sync (continued) — Pre-trade check count: 38 → 41. Added:
    • #211 VIX intraday-spike entry-pause unification — closes the
      hole left by #181: `OrderEngine.set_vix_spike()` + check inside
      `enter_trade()` so initial-scan and partial-rescan entries also
      honour the pause that the manager-level opportunity / re-scan
      paths already enforced. (gate 0e)
    • #212 Tape-breadth filter — scanner counts BUY vs SELL after the
      score floor; when the minority side is ≤ 30 % of {BUY+SELL},
      apply -0.5 to |score| of the counter-tape candidates so weak
      ones fall below the floor naturally. Captures the FII-sell-day
      pattern where individual scores look fine but the tape is
      bearish. (scanner-side; surfaces as gate 14d in the entry
      table because it operates on score before any entry gate runs)

  2026-04-25 sync — Pre-code-freeze profitability + hardening batch
  (#217–#226). Pre-trade check count: 41 → 41 (no new entry gates;
  changes are scanner-side, monitor-side, or simplification).
    • #217 NSE early-close calendar — `Config.NSE_EARLY_CLOSE_DATES_2026`
      (user-maintained `"YYYY-MM-DD" → (HH, MM)` map) plus
      `Config.apply_early_close_if_today()` invoked at manager startup
      tightens `SQUARE_OFF_HOUR/MINUTE` on Diwali-Muhurat / Good-Friday-eve
      / year-end half-sessions so MIS exits print before the forced-close
      auction. Idempotent — only tightens, never loosens. Empty by default.
    • #218 Sector-relative-strength directional bias — scanner ranks all
      sectors by average score at scan time, then biases each candidate
      `±SECTOR_RANK_BIAS_STEP × (mid_rank − sector_rank)` capped at
      `±SECTOR_RANK_BIAS_MAX`. Sign-aware (helps only when score sign
      already matches sector direction). Skipped when fewer than
      `SECTOR_RANK_MIN_SECTORS` distinct sectors. Surfaces as a score
      adjustment upstream of every entry gate (no new gate row).
    • #219 Earnings/results-day blackout (config framework) — scanner
      pre-filter (gate -1) drops symbols listed in
      `Config.EARNINGS_BLACKOUT_SYMBOLS_2026["YYYY-MM-DD"]` before scoring.
      Empty by default; user populates each Friday. Kill-switch
      `EARNINGS_BLACKOUT_ENABLED`.
    • #220 Sector-cascade exit (defensive SL tightening) — new
      `_sector_cascade_protect()` in `manager_v2` runs once per candle
      re-scan after per-position checks. When a sector's two-tick rolling
      avg-score drops by ≥ `SECTOR_CASCADE_DROP_THRESHOLD`, current avg
      is ≤ `−SECTOR_CASCADE_OPPOSITE_FLOOR`, and ≥
      `SECTOR_CASCADE_MIN_OPEN` open positions in that side, tighten SLs
      via `_compute_protective_sl()` + `engine._update_exchange_sl()`.
      Defensive only — never opens a new trade. Per-position try/except
      hardened by #222.
    • #221 Lunch-lull score-override LOWERED 6.0 → 5.7 (data-driven;
      3-day rejection-audit showed gate was net-negative at 6.0). Sister
      proposal #175 (raise to 7.0) moved to Removed.
    • #222/#223 SL-tightening try/except hardening — `_sector_cascade_protect`,
      `_auto_protect_on_contrary_signal`, and `_regime_shift_protect` now
      wrap `engine._update_exchange_sl()` in try/except so a transient
      broker error on position N doesn't crash the monitor loop and leave
      remaining positions unmonitored. Software SL is updated first
      (always succeeds); broker mismatch repaired on next sync via
      `_reconcile_orphan_sl_m()`.
    • #224/#225 Late-entry tightening recalibration + simplification —
      `LATE_ENTRY_RR_FLOOR` deleted; the always-on `RR_HARD_FLOOR = 1.3`
      now enforces `max(computed_floor, RR_HARD_FLOOR)` in
      `current_rr_floor()` (covers post-10am identically + protects pre-10am
      from over-relaxation). `LATE_ENTRY_MAX_POSITIONS` and
      `dynamic_late_entry_max_positions()` deleted — concurrency now fully
      owned by `dynamic_max_positions(budget)` all day. Late-entry score
      bump (gate 18d) retained.
    • #226 Dead-knob removal — `MIN_BUDGET_UTILISATION_PCT` (was 0.0,
      disabled) and its `_boost_underdeployed()` helper + 2 Claude-prompt
      fragments deleted. Behaviour unchanged.

  2026-04-26 sync — Pre-code-freeze review pass 2 (#230). Documentation
  accuracy fix: pre-trade gate count was claimed as 41 in 8 places
  (STRATEGY_V2 prose × 4, README × 3, OrderEngine.enter_trade docstring × 1)
  but the canonical inventory table in this doc only enumerates 40 rows.
  Most likely root cause: when #225 collapsed two late-entry sub-gates
  (R:R floor + concurrent-positions cap) into the always-on `RR_HARD_FLOOR`
  and `dynamic_max_positions(budget)`, the count headers were missed.
  All eight references reconciled to 40; cleaned a stray "rather than
  reaching the entry pipeline" table-cell artefact in row 14d. No
  behavioural change.

  2026-04-27 sync — Whipsaw guard scope broadening (#244). Pre-#244
  the consecutive-loss counter (#20) only fired on `STOP_LOSS` exits.
  Today's session-1 lost 4 consecutive morning trades (HAL/LODHA/GAIL/
  HDFCLIFE) to `MOMENTUM_KILL` (now noise-floored by #233) — the
  whipsaw guard never fired. Industry standard (prop-firm risk
  frameworks) is to count any losing exit. New kill-switch
  `LOSS_STREAK_INCLUDE_NON_SL_LOSSES = True` makes MOMENTUM_KILL /
  STAGNANT_EXIT / SIGNAL_DECAY / LOSER_EXIT with `pnl < 0` also feed
  the counter; EOD (SQUARE_OFF / CIRCUIT_BREAKER) and operator/external
  closes excluded. Log message updated `WHIPSAW GUARD` →
  `LOSS-STREAK GUARD`. Removal trigger logged as #244R: revert if the
  broader counter blocks net-positive trades on > 2 of 10 days.

  2026-05-05 sync — Multi-day side-skew + entry-burst clamp + #246
  rollback (Roadmap #179, #251, #253, #246-disable). Three new gates
  shipped after a 9-day cross-trade audit (2026-04-22 → 2026-05-05,
  n=52 logical trades) surfaced two structural failure modes:
  (a) entry bursts — 12 of 13 windows with ≥ 3 entries inside 60s
  ended with all members losing together (≈92% lose-together
  correlation); (b) BUY-side WR collapse to 12.5% across all three
  NIFTY regimes while SELL held 42.9%. Shipped: **#179 entry-burst
  cap** (`ENTRY_BURST_CAP_MAX_ENTRIES_PER_60S = 2`, deque[maxlen=32]
  of recent successful-entry timestamps, 3rd entry inside any rolling
  60s window rejected at top of `enter_trade()`); **#251 directional
  auto-pause** (`DIRECTIONAL_PAUSE_*`, session-startup arming via
  `manager._arm_multiday_pauses()` reading trailing 7d ledger; pause
  one side when n ≥ 10 AND WR ≤ 30% AND NIFTY 7d return is contra;
  default `DIRECTIONAL_PAUSE_ENABLED = True`); **#253 rolling-PF
  circuit breaker** (`ROLLING_PF_PAUSE_*`, full-session blackout when
  rolling-3d PF < 0.6 AND net ≤ −Rs.300 AND n ≥ 5; **shipped then
  disabled same day** — `ROLLING_PF_PAUSE_ENABLED = False` — after
  17-session counterfactual replay showed #251 alone gains Rs.+503
  vs baseline while #251+#253 only gains Rs.+387 (incremental −Rs.116);
  Kelly criterion advises bet-reduction not bet-zero). **#246 disabled**
  (`LATE_ENTRY_NO_RESCUE_FLOOR_ENABLED = False`) after phase-2 EV
  audit over 24 sessions / 157 positions: pre-ship counterfactual
  cohort (n=39, |score|<7 post-10:00) was net Rs.+618 at 53.8% WR
  with EVERY sub-bin net-positive; post-ship admitted cohort (n=9,
  |score|≥7 post-10:00) was net Rs.−451 at 33% WR. Re-enable trigger
  in Awaiting-Data #254. Pre-trade check count: 41 → 43 (entry-burst
  cap + directional pause; rolling-PF gate code present but skipped
  by kill-switch).

  2026-05-06 sync — Per-budget burst-cap delta + fractional-Kelly
  opposing-side cap (Roadmap #179a + #251a). Two follow-up gates
  shipped one day after #179/#251 in response to user financial-analyst
  questions on the live behaviour. **#179a per-budget burst-cap delta**
  (`BUDGET_BURST_CAP_DELTA = {"TINY": 0, "SMALL": 0, "NORMAL": 1,
  "LARGE": 2}`, new `OrderEngine.effective_burst_cap()` returns
  `base + delta` floored at 0; `is_burst_capped()` consumes it):
  the 92% lose-together evidence in #179 was sourced exclusively from
  a Rs.50K SMALL account, so SMALL/TINY stay at the audit-validated
  cap-2 while NORMAL/LARGE accounts that genuinely have 5-8 morning
  slots get +1 / +2 deltas (effective cap 3 / 4). Industry parallel:
  prop-firm risk frameworks (TopstepTrader, FTMO, MyForexFunds) tier
  max-concurrent caps by account size.
  **#251a fractional-Kelly opposing-side cap** (`DIRECTIONAL_PAUSE_OPPOSING_MIN_TRADES = 20`,
  `DIRECTIONAL_PAUSE_OPPOSING_THIN_MAX_ENTRIES = 5` (bumped 3 → 5 on 2026-05-07 after live SELL-side WR=67% under cap-3 left profit on the table); new
  `OrderEngine._maybe_arm_opposing_thin()` called from both BUY and
  SELL arming branches in `arm_multiday_pauses()`; new
  `is_opposing_thin_capped(side)` gate inserted in `enter_trade()`
  immediately after `is_directional_paused(side)`): when #251 arms
  against one side, the OPPOSING (un-paused) side may have thin
  evidence (e.g. SELL n=14 in the 04-23 → 05-05 trigger audit). Per
  Kelly criterion (Investopedia: typical lookback is 50-60 trades for
  win-prob estimation; binomial CI at n=14 with p≈0.5 is ±26pp —
  statistical noise) we cap entries on the surviving side at 5 per
  session whenever its history has < 20 trades. Reduces concentration
  risk on the un-validated side without disabling it. Pre-trade check
  count: 43 → 44 (opposing-thin gate inserted between directional-pause
  and burst-cap; burst-cap is now budget-tiered, count itself unchanged).
  **#251b intraday NIFTY-bounce bypass on directional pause**
  (`DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_PCT = 1.0`,
  `DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_MIN_SCANS = 2`; manager
  `_build_nifty_context()` pushes live NIFTY intraday return into the
  engine via new `record_nifty_intraday_return()`; engine
  `is_directional_paused(side)` consults new `_is_intraday_nifty_bouncing(side)`
  before returning True): closes the **BUY-pause loop trap** in which
  the base #251 gate's lagging-7d NIFTY return keeps BUY paused for
  weeks during a sustained-bear regime even when intraday NIFTY rallies,
  starving the bot of fresh BUY evidence. When the engine has accumulated
  ≥ 2 consecutive NIFTY readings whose sign favours the paused side
  (BUY paused → > +1%; SELL paused → < −1%), the gate-check returns
  False with a one-shot WARN. Pause STATE remains intact — only the
  gate bypasses. Self-limiting: if NIFTY pulls back, the deque drains
  and the pause re-engages on the next scan. Risk discipline preserved
  — opposing-thin (#251a), burst-cap (#179/#179a), R:R floor, score
  floor, RSI cap, ADX gate all still apply downstream. Industry parallel:
  directional-change algorithms (Adegboye, Kampouridis, Otero — *Artificial
  Intelligence Review* 2023) confirm trend transitions when "price
  moves beyond a threshold followed by a confirmation period (overshoot)".
  Pre-trade check count unchanged at 44 (no new gate row — this is a
  CONDITION on the existing 0ab gate, not a new gate).
══════════════════════════════════════════════════════════════ -->

---

## Table of Contents

1. [Overview](#overview)
2. [Glossary — Every Term Explained](#glossary--every-term-explained)
3. [Modes at a Glance](#modes-at-a-glance)
4. [Strategy Flow](#strategy-flow)
   - [**Decision Timeline — Plain English**](#decision-timeline--plain-english-start-of-day--eod)
   - [Phase 1 — Pre-Market Scan](#phase-1--pre-market-scan-900-am--free)
   - [Phase 2 — Stock Selection](#phase-2--stock-selection)
   - [Phase 3 — Entry](#phase-3--entry)
   - [Phase 4 — Monitor Loop](#phase-4--monitor-loop-930-am--310-pm-945-start-on-expiry-thursdays)
   - [Phase 5 — Square Off & Report](#phase-5--square-off--report)
5. [Technical Indicators (14)](#technical-indicators-14)
6. [Candlestick Patterns (14)](#candlestick-patterns-14)
7. [Risk Management — Entry Pre-Checks](#risk-management--entry-pre-checks)
8. [Risk Management — During Trade](#risk-management--during-trade)
   - [Exchange SL-M Orders](#exchange-sl-m-orders-use_exchange_sl--true)
   - [Trailing Stop-Loss](#trailing-stop-loss)
   - [Time-Decay Target Reduction](#time-decay-target-reduction)
   - [Late-Day Loser Exit](#late-day-loser-exit-245-pm)
   - [Circuit Breaker](#circuit-breaker)
   - [Whipsaw Guard](#whipsaw-guard)
   - [Loss-Adjusted Budget](#loss-adjusted-budget)
   - [Stagnant Position Exit (NoAI)](#stagnant-position-exit-noai-only)
   - [Contrary Signal Protection](#contrary-signal-protection)
   - [Signal-Reversal Exit](#signal-reversal-exit)
   - [Signal-Decay Exit](#signal-decay-exit)
9. [Market Intelligence](#market-intelligence)
   - [India VIX Adjustments](#india-vix-adjustments)
   - [VIX Spike Protection](#vix-spike-protection)
   - [NIFTY Regime Tracking](#nifty-regime-tracking)
   - [FII/DII Flow Bias](#fiidii-flow-bias)
   - [Pre-Open Auction Data](#pre-open-auction-data)
   - [Thursday Expiry Adjustments](#thursday-expiry-adjustments)
10. [Dynamic Position Sizing](#dynamic-position-sizing)
    - [MAX_POSITIONS Auto-Scaling](#max_positions-auto-scaling)
    - [Score-Weighted Sizing (NoAI)](#score-weighted-sizing-noai)
    - [Dynamic Score Threshold (NoAI)](#dynamic-score-threshold-noai)
11. [Configuration Quick Reference](#configuration-quick-reference)
12. [Database & Verification](#database--verification)
13. [Design Decisions & Rationale](#design-decisions--rationale)
14. [V2 Review Cycle Changes (April 2026)](#v2-review-cycle-changes-april-2026)
15. [V1 — Deprecated](#v1--deprecated)
16. [Known Limitations](#known-limitations)

---

## Overview

### What this bot does — in plain English

Think of the bot as an automated day-trader for the Indian stock market. Every morning:

1. **Before the market opens (9:00 AM)** it looks at ~100 large Indian stocks and scores each one from −24 (strong sell) to +24 (strong buy). The score is built from 14 chart patterns (like "hammer" or "engulfing" candles) and 14 technical indicators (trend, momentum, volume, support/resistance).
2. **Once the market has settled (9:30 AM, or 9:45 on Thursday F&O expiry days)** it picks the 2–7 highest-scoring stocks (count depends on your budget) and places actual orders on Zerodha — buying the strong-positive scores, short-selling the strong-negative ones. The bot deliberately skips the chaotic first 15 minutes after open (`ENTRY_DECISION_FLOOR_MINUTES_AFTER_OPEN = 15` HARD floor) where spreads are 1.5–3× wide, VWAP isn't yet usable, and HFT flow dominates. On expiry Thursdays the floor extends to 30 minutes (`EXPIRY_ENTRY_DELAY_MINUTES = 30`) to clear the F&O settlement-driven opening swings.
3. **Throughout the day** it watches prices every 10 seconds and automatically:
    - Exits at a pre-set loss price (stop-loss) so no trade can hurt too much.
    - Takes partial profit once a trade is nicely in the green, and slides the stop-loss up so you keep most of the gain even if the price reverses.
    - Re-checks every 15 minutes whether the setup that triggered the trade still looks good, and tightens the stop-loss if the chart flips against you.
4. **Before close (3:10 PM)** it closes every position, writes a report with full profit/loss and tax breakdown, and shuts down. All trades are **intraday** — the bot never holds a stock overnight.

Nothing is hand-entered. Your only job is to set a budget and decide whether you want AI (Claude) involved in picking (slower + costs a little) or pure math picking (free + instant).

### Technical summary

V2 is an **intraday equity trading bot** for NSE (India) via Zerodha Kite Connect. It combines a free mathematical pre-filter (14 candlestick patterns + 14 technical indicators) with automatic stock selection by composite score.

**Default mode (NoAI):** Pure technical signals — zero Claude API calls, zero cost, deterministic.
**Optional AI mode:** Claude ranks/vetos pre-filtered candidates and reviews open positions.

```
# Default — NoAI (recommended)
python main.py --mode trade

# With Claude AI
python main.py --mode trade --ai

# V1 (deprecated — Claude-first, no pre-filter)
python main.py --mode trade --v1
```

V2 inherits all risk management from V1 (ATR-based SL, trailing stops, circuit breaker, crash recovery) and adds: candle pattern detection, 14-indicator scoring, auto-protect on contrary signals, stagnant exit, VIX/expiry adjustments, direction diversification, and fallback candidate promotion.

---

## Glossary — Every Term Explained

This is the single place to look up any unfamiliar term used in the rest of the document. Each entry has: **what it means** in plain English → **how the bot uses it**. If you add a new indicator or concept to the code, add its entry here too.

### 1. Market Basics

| Term | Full form / Plain-English meaning | How the bot uses it |
|------|-----------------------------------|---------------------|
| **NSE** | National Stock Exchange of India. Where Indian stocks are traded. | Every trade goes to NSE via Zerodha's API. |
| **Zerodha / Kite Connect** | Zerodha is an Indian stock broker; Kite Connect is their API. | All order placement, live prices, and historical data come from Kite. |
| **NIFTY 50 / 100 / 150 / 200** | Index tiers of top Indian stocks (each tier adds 50 more names: 50 large → 100 large → 150 incl. mid → 200 incl. wider mid caps). | `SCAN_UNIVERSE` config picks one — the bot only considers stocks from this list. Override per-run with `--nifty 50\|100\|150\|200`. |
| **Intraday** | A trade you open **and** close on the same day. | Every bot trade is intraday — the bot never holds a stock overnight. |
| **MIS** | Margin Intraday Square-off. Zerodha's product type for intraday trades (lower margin, auto-closed end of day). | Bot places all orders as `product=MIS`. |
| **Long** | Buy now, sell later at a higher price to profit. | When composite score is **positive**, bot goes long ("BUY"). |
| **Short** | Sell first (borrowed shares), buy back later at a lower price to profit. Only allowed in MIS intraday. | When composite score is **negative**, bot goes short ("SELL"). |
| **Square-off** | Closing an open position (reverse trade). | Bot auto-squares all positions at 3:10 PM (`SQUARE_OFF_HOUR`). |
| **F&O expiry** | Futures & Options contracts expire every Thursday in India. Causes extra volatility. | Thursday triggers special rules (wider SL, fewer trades, longer observation). |

### 2. Price & Volume

| Term | Full form / Plain-English meaning | How the bot uses it |
|------|-----------------------------------|---------------------|
| **OHLC** | Open / High / Low / Close prices of a candle. | Every candle stored in the cache has these 4 values; most indicators read from them. |
| **LTP** | Last Traded Price — the current live price. | Used for live-quote checks, LIMIT order pricing, SL/target monitoring. |
| **Tick** | The smallest price increment allowed for a stock (Rs.0.05 for most, Rs.0.50 for high-priced). | LIMIT orders are rounded to the nearest valid tick. |
| **Bid / Ask** | Highest price a buyer will pay (bid) / lowest a seller will accept (ask). | Fetched from quote depth to compute spread. |
| **Spread** | `(ask − bid) / LTP × 100`. Cost of instantly entering + exiting. | If spread > `effective_max_spread()` (0.20% on TINY/SMALL accounts via #236, 0.30% on NORMAL/LARGE), skip trade — spread eats profit. |
| **Impact cost** | How much our *full order qty* moves the fill price vs LTP. Formula: `(weighted_avg_fill − LTP) / LTP × 100`, computed by walking the top-5 order-book levels on the side we'd hit. | If impact cost > `MAX_IMPACT_COST_PCT` (0.2%), or visible depth across top-5 levels is smaller than our qty, skip the trade. Catches cases where spread looks tight but only a handful of shares are at the top, with a big gap to the next level. |
| **Volume** | Number of shares traded in a candle. | High volume confirms patterns; low volume warns of weak signal. |
| **RVol** | Relative Volume = today's volume ÷ 20-period average volume. | `RVol ≥ 0.7` required for entry (low-volume entries often reverse). |
| **Gap** | Price difference between yesterday's close and today's open. | Gap ≥ `PREOPEN_GAP_SIGNIFICANT_PCT` (1%) with volume signals institutional interest. |

### 3. Order Types

| Term | Plain-English meaning | How the bot uses it |
|------|----------------------|---------------------|
| **MARKET order** | "Execute immediately at whatever price is available." Fast, but can get adverse fills. | Used for exits (guaranteed fill) and as LIMIT fallback. |
| **LIMIT order** | "Only fill at this price or better." Cheaper fills, but may not execute. | Used for entries — LTP ± 1 tick for 8 seconds, then MARKET fallback. |
| **SL-M order** | Stop-Loss Market. Sits on the exchange; auto-triggers a MARKET order when price breaches the stop. | Every entry has a matching SL-M so exits happen even if the bot disconnects. |

### 4. Risk Terms

| Term | Plain-English meaning | How the bot uses it |
|------|----------------------|---------------------|
| **Stop-Loss (SL)** | Pre-set "exit now" price to cap loss. | Every trade has an SL computed from ATR. |
| **Target** | Pre-set "take profit" price. | Every trade has a target; honoured uniformly across the trading day (#242 removed late-day target compression). |
| **R:R ratio** | Risk-to-Reward. `(target distance) / (SL distance)`. 1.5:1 = you risk Rs.1 to potentially win Rs.1.50. | Minimum R:R floor gates entries (uniform `RR_HARD_FLOOR = 1.3` all day; #243 collapsed the morning-vs-late split). |
| **Trailing stop** | An SL that moves up as the trade profits, locking in gains. | At 1.5× risk profit, take 33% off the table + move SL to lock 50% of gain. |
| **Drawdown** | Temporary dip in account value. | Circuit breaker stops trading if daily loss > 3% of budget. |
| **ATR** | Average True Range. Measures recent price swing size (volatility). | SL = entry ± `1.5 × ATR`; wider for volatile stocks, tighter for calm ones. |

### 5. Candlesticks

A **candle** represents price action over a fixed time window (we use 15-minute candles). It has a **body** (between open and close) and **wicks / shadows** (the high and low extremes).

- **Bullish candle** — close > open (green). Buyers won that window.
- **Bearish candle** — close < open (red). Sellers won.

A **candlestick pattern** is a specific shape (single-candle or multi-candle) that historically precedes a price move. Example: a *Hammer* has a tiny body near the top and a long lower wick, suggesting sellers pushed price down but buyers stepped in hard.

The bot detects 14 patterns (listed in [§6 Candlestick Patterns](#candlestick-patterns-14)), multiplies strength by volume confirmation, and decays strength with age (fresh = 1.0×, 1 candle old = 0.7×, 2 old = 0.4×).

### 6. Technical Indicators

These are math formulas computed on the last 20–30 candles. Each produces a signal that contributes to the composite score.

| Indicator | Full form | One-line meaning | How the bot uses it |
|-----------|-----------|------------------|---------------------|
| **EMA** | Exponential Moving Average | Smoothed average price weighted toward recent candles. | EMA(9) vs EMA(21) crossover = momentum shift. Score ±2 on cross, ±1 on spread. |
| **RSI** | Relative Strength Index (0–100) | Momentum oscillator. > 70 overbought (likely to pull back), < 30 oversold (likely to bounce). | Score boost near extremes; hard **block** on chasing extremes (BUY at RSI > 75, SELL at RSI > 70, etc.). |
| **VWAP** | Volume-Weighted Average Price | The "fair value" institutions trade around — average price weighted by volume since market open. | Above VWAP = buyers in control. Bot blocks trading *against* VWAP after 10:15 AM. |
| **VWAP SD Bands** | Standard-Deviation bands around VWAP | ±1σ and ±2σ bands. Price at ±2σ = stretched, likely mean-revert. | ±2σ = strong signal (±1), ±1σ = moderate (±0.5). |
| **MACD** | Moving Average Convergence Divergence | Short EMA − long EMA, with a signal line. Histogram shows momentum strength. | Growing histogram confirms trend; shrinking warns of exhaustion. ±0.5 to ±1. |
| **SuperTrend** | ATR-based trend-following line | Sits above price in downtrend, below in uptrend. Flips = trend change. | Main trend indicator. ±3 on fresh flip, ±1 on continuation. |
| **ORB** | Opening Range Breakout | The high/low of the day's early candles. Breaking either signals directional move. | Uses 2nd candle (9:30–9:45) to avoid opening auction noise. Score decays through the day. |
| **Bollinger Bands** | Price channel at ±2σ around 20-period mean | Narrow bands (**squeeze**) signal a big move is coming. | Breakout from a squeeze adds ±0.5 directionally. |
| **ADX** | Average Directional Index | Measures trend *strength* (not direction). 0–100. | ADX < 20 = weak trend (halve trend scores); > 30 = strong (+0.5 boost). |
| **StochRSI** | Stochastic of RSI | Faster oscillator — where RSI sits within its own recent range. | Info-only in NoAI; entry-timing signal in Claude prompts (BULLISH_CROSS, OVERSOLD, etc.). |
| **Fibonacci retracement** | 38.2% / 50% / 61.8% of previous-day range | Levels where price often bounces on pullbacks. | Near support in uptrend = +0.5; near resistance in downtrend = −0.5. |
| **Prev-Day S&R** | Previous-day High / Low / Close levels | Yesterday's extremes act as today's magnets / barriers. | AT_SUPPORT = +1, AT_RESISTANCE = −1, PIVOT = ±0.5. |
| **Daily EMA Bias** | 9/21 EMA on daily candles | Higher-timeframe trend context. | ±1 when daily EMA spread > 1% (clear trend). |
| **Score Momentum** | Δ (current score − previous scan's score) | Is the setup getting stronger or weaker? | Large positive Δ = accelerating (good). **Large \|Δ\| ≥ 8 blocks entry** — fresh reversal, wait one cycle. |

### 7. Market-Wide Context

| Term | Full form / Plain-English meaning | How the bot uses it |
|------|-----------------------------------|---------------------|
| **NIFTY trend** | Is the overall market going up, down, or sideways today? | Against-trend signals need \|score\| ≥ 3 (trades with the broader market get priority). |
| **VIX** | India Volatility Index. Measures expected 30-day NIFTY volatility. | VIX > 20 = fear → reduce positions + raise score bar. VIX spike > 10% intraday → pause entries. |
| **FII / DII** | Foreign / Domestic Institutional Investors. Big money movers. | Previous day's net buy/sell → morning bias (both buying = bullish, etc.). |
| **Pre-open auction** | 9:00–9:08 AM session that sets the opening price. | Gap ≥ 1% with high volume flagged as "institutional interest". |

### 8. Position-Lifecycle Terms

| Term | Plain-English meaning | How the bot uses it |
|------|----------------------|---------------------|
| **Circuit breaker** | Automatic "stop trading" trigger when daily loss exceeds 3% of budget. | Pauses 30 min, resumes with loss-adjusted budget. Max 2 trips, then day is done. |
| **Whipsaw** | Getting stopped out repeatedly by rapid reversals. | 3 consecutive losing exits (any reason except EOD/operator close, post-#244) → pause new entries for 30 min. |
| **Time decay** | The fact that late-day trades have less time to hit targets. | After 2 PM, open targets compressed by 25%. |
| **Adopted position** | A position the bot did **not** open (you opened it manually, or bot restarted mid-day). | Skips time-decay and loser-exit for 10 min (user's intent respected). |
| **Stagnant exit** | Closing a trade that hasn't moved meaningfully toward target. | NoAI-only, two-tier: at 45 min exit if adverse (>0.2% loss) or dead-flat (±0.1% band); at 90 min exit if progress to target <20%. See [§Stagnant Position Exit](#stagnant-position-exit-noai-only). |
| **Signal-reversal exit** (#174) | Hard-exit a held position when the periodic candle re-scan sees a strong opposite signal AND a confirming reversal candle pattern. | Both modes. Triggered when held BUY scores ≤ -7 (or held SELL ≥ +7) with a bearish/bullish reversal pattern present. Skipped on profitable winners (≥1× initial risk) — those are the trailing stop's job. See [§Signal-Reversal Exit](#signal-reversal-exit). |
| **Signal-decay exit** (#188) | Hard-exit a held position when the entry signal hasn't *flipped* but has *decayed* to a small fraction of its entry strength, AND the trade isn't yet a genuine winner (≥1R of initial risk). | Both modes. Triggered when entry score had |≥7| conviction, fresh re-scan score is same-direction but |< 40%| of entry magnitude, hold ≥ 30 min, and `pnl < 1R`. Book-and-go below 1R; winners ≥1R keep running on the trailing stop. See [§Signal-Decay Exit](#signal-decay-exit). |
| **Gap coherence** (#173) | Pre-trade check that rejects entries which contradict a STRONG opening gap. | Both modes. BUY blocked on `GAP_DOWN_STRONG` (and SELL blocked on `GAP_UP_STRONG`) unless `\|score\| ≥ 7.5`. Targets the rare overnight-flow setups where indicators look fine but opening flow is the wrong way. |

### 9. Bot-Specific Concepts

| Term | Plain-English meaning |
|------|----------------------|
| **Composite score** | Sum of all 14 indicator contributions. Range −24 to +24. Sign = direction, magnitude = conviction. |
| **Pre-filter** | The free computation step that shortlists ~15 candidates from ~100 scanned stocks. |
| **Fallback candidate** | Stocks beyond the top-N that get promoted if primary picks fail entry checks. |
| **Auto-protect** | Automatic SL tightening when the 15-min re-scan shows a position's score has flipped against it. |
| **NoAI mode** | Default mode — pure math picks trades, Rs.0 in API costs. |
| **Claude AI mode** | Optional `--ai` flag — Claude ranks pre-filtered candidates and reviews open positions. |

---

## Modes at a Glance

| Aspect | NoAI (Default) | Claude AI (`--ai`) |
|--------|----------------|---------------------|
| **Stock selection** | Auto-picks top N by score sign + magnitude | Claude picks from top 15 pre-filtered |
| **Trade side** | Score sign: positive = BUY, negative = SELL | Claude decides |
| **SL / Target** | Config defaults, ATR overrides | Claude sets, ATR may override |
| **Position sizing** | Score-weighted (higher conviction = more capital) | Claude sets qty, budget-validated |
| **Rationale** | Auto-generated from indicator values | Claude writes qualitative analysis |
| **Position reviews** | Stagnant exit after 45 min (rule-based) | Claude reviews every 30 min |
| **Mid-day re-scan** | Auto-select from new candidates (same as initial) | Claude picks from new candidates |
| **Candle re-scan** | Every 15 min, auto-protect only | Every 15 min, auto-protect + Claude sees patterns |
| **Score threshold raise** | Yes — after day losses, V2_MIN_SCORE rises | No |
| **API cost** | **Rs.0** | ~Rs.20-40/day (5-15 Claude calls) |
| **Latency** | Instant | 10-30s per Claude call |

**Shared across both modes:** pre-filter, entry pipeline (44 checks), SL-M exchange orders, trailing stop, circuit breaker + cooldown, time-decay, late-day loser exit, direction diversification, sector guard, VIX adjustments, expiry adjustments (incl. holiday-shifted Wednesday expiry, #41), NIFTY regime tracking, FII/DII bias, fallback candidate promotion, manual trade adoption with grace window, crash recovery, lunch-lull skip (#164), per-symbol re-entry cooldown (#161), charge-aware target (#162), daily-loss soft-stop (#163), peak-drawdown stop (#168), budget-regime gate deltas (#165).

---

## Strategy Flow

### Decision Timeline — Plain English (Start of Day → EOD)

This section walks through **every decision** the bot makes during one trading day. Each decision has the same format: *when* it happens, *what question* is asked, and *what happens next*. The AI callouts are marked 🤖 — those only fire in `--ai` mode.

> **Reading this:** If you want to trace a specific log line, ctrl-F the quoted phrase in brackets — every decision below corresponds to a real log message.

#### 🕘 8:55–9:00 AM — Warm-up

1. **Load session.** Log line: `"Budget regime: NORMAL (Rs.1,00,000) → ADX≥18.0, trade-cap 12, min-score 2.0"`. Tells you which regime gates are active (TINY / SMALL / NORMAL / LARGE) based on your Zerodha funds. [See §Budget Regime (#165)]
2. **Validate API keys** (Zerodha always, Claude only when `--ai`).
3. **Check if today is expiry** — if yes, stagnant-exit timer + score floor + SL bumps are applied. Log line: `"Expiry adjustments applied"`.

#### 🕤 9:00–9:15 AM — Pre-market scan (FREE, no orders yet)

4. **Scan ~100 NIFTY stocks.** For each stock, compute the composite score from 14 indicators + 14 candle patterns. Total range: −24 to +24.
5. **Hard filter** by price band, sector cap (2/sector), NIFTY trend alignment, and `V2_MIN_SCORE` (regime-adjusted). Example: on a TINY budget, min-score becomes 3.0 instead of 2.0 — weaker setups are silently dropped at the scanner level.
6. **Rank and shortlist top 15.** Log: `"NoAI scan: 15 candidates passed pre-filter"`.

#### 🕤 9:15 AM — Entry window opens

7. **Wait for `ENTRY_DELAY_MINUTES` (default 5)** past market open (9:15 IST). Prevents trading the opening auction noise.
8. **Confirm 0.3% directional move** from open price. If a stock hasn't moved in either direction, the signal isn't ripe. Log: `"{symbol}: no confirmed move yet"`.
9. 🤖 **(AI mode only)** Claude receives the shortlist with all 14 indicators + patterns + time context, and ranks/vetoes. Output: ENTRY / SL / TARGET / QTY / RATIONALE per trade.

#### 🕤 9:30 AM onward (9:45 on expiry Thursdays) — Entry pipeline (every candidate runs all 44 checks, in order)

For each candidate, the bot asks these questions. **The first "no" rejects the trade and moves to the next candidate.** Every rejection is logged as a warning with the symbol and reason.

> **Quick-rejection gates (cheap to evaluate, run first):**
>
> - 🆕 **Lunch lull?** Is it 11:30–12:15 and `|score| < 5.7`? → Skip. Example log: `"TATAMOTORS: lunch-lull window 11:30-12:15 — |score| 4.2 < 5.7 override. Skipping."` (Roadmap #164, override stepped down 6.0 → 5.7 by #221)
> - 🆕 **Soft-stop?** Has day P&L dropped ≥ 1.5% below budget? → Block all new entries (but existing positions keep running; hard CB at 3% still closes everything). Log: `"soft-stop active — day P&L Rs.-1,650.00 ≤ -1.5%. No new entries."` (#163)
> - 🆕 **Multi-day directional pause?** Did one side (BUY or SELL) lose ≥7 of last 10 trades over the trailing 7 sessions AND NIFTY 7d return is contra? → Side paused for the whole session (#251). **Two bypass paths can lift it for one scan:**
>     - **#251b NIFTY-bounce:** if NIFTY intraday return > +1% (BUY paused) or < −1% (SELL paused) for ≥ 2 consecutive scans, gate returns False with one-shot WARN. Pulls back → pause snaps back.
>     - **#251c tape-breadth:** if scanner finds ≥ 5 candidates total AND ≥ 3 on the paused side AND paused side is ≥ 40% of {BUY+SELL}, gate returns False. *Worked example:* scanner finds 4 BUY + 6 SELL = 10 total, BUY share = 40% → BUY pause bypasses for that scan. Scanner finds 2 BUY + 8 SELL → BUY share 20% < 40% → pause holds. The 30–40% band between the bearish-tape PENALTY (#212) and this BYPASS is an explicit "uncertain — neither rule fires" zone.
>
> **Price sanity gates:**
>
> - **Stale price?** If Claude/plan price differs from live Zerodha price by >5%, override with live. Log: `"Entry price override: ..."`.
> - **Wide spread?** Bid-ask gap > 0.3% → skip illiquid.
> - **Thin book?** Top-5 depth won't fill our qty without >0.2% slippage → skip.
> - **Low volume?** RVol < 0.7× average → skip.
>
> **Sizing & target gates:**
>
> - **Compute ATR SL/target.** If ATR available: SL = entry ± 1.5·ATR, target = entry ± 1.5·ATR·R:R. Otherwise fall back to config defaults. SL is then clamped to `MIN_SL_DISTANCE_PCT` (0.8%) floor so tight SLs don't wick on noise.
> - **R:R floor check.** Always-on `RR_HARD_FLOOR = 1.3` (#225) — uniform across the trading day. Time-of-day floors (`RR_FLOOR_MORNING/AFTERNOON/LATE`) all pinned to 1.3 since #242; the labels remain in logs (`morning/afternoon/late/relaxed/hard-floor`) only for traceability. The earlier auto-target compression after 1 PM / 2 PM was removed by #242 — pre-shrinking entry targets while the hard floor rejected the resulting R:R was a self-defeating loop. Drift on open positions is owned by stagnant-exit (#172), momentum kill (#198/#233), open-position time-decay (`TARGET_DECAY_PCT`), and the 3:10 PM hard square-off.
> - **Min profit.** `|target − entry| × qty ≥ effective_min_profit()` (Rs.135 on TINY/SMALL, Rs.200 NORMAL, Rs.400 LARGE — #237).
>
> **Portfolio-state gates:**
>
> - **Budget OK?** Position cost ≤ 40% of budget.
> - **Max positions?** Dynamic (2–7 from budget tier).
> - **Duplicate, sector (2/sector), direction (score-aware)** — any of these → skip.
> - **Short cutoff.** No new shorts after 1 PM.
> - **Max re-entries.** Per stock per day ≤ 2. Also rejects if new `|score| < previous |score|` ON THE SAME SIDE (setup weakening). A direction flip is treated as a fresh setup, not a weakening (#185).
> - **RSI extremes.** Block SELL at RSI > 70, BUY at RSI > 75, BUY at RSI < 30, SELL at RSI < 25.
> - **Daily trade cap.** Total trades (bot + external) ≥ regime-adjusted cap → stop.
> - **Stagnant churn guard.** Same stock+direction already exited as stagnant today → skip.
> - 🆕 **Per-symbol re-entry cooldown.** Same `SYMBOL_SIDE` exited in last 30 min → skip unless `|score| ≥ 7.0`. Opposite direction allowed (reversal setup). Example: `"RELIANCE: re-entry cooldown — exited 12.3 min ago (window 30 min), |score| 3.5 < 7.0 override. Skipping."` (#161)
>
> **Trend alignment gates:**
>
> - **ADX gate.** ADX ≥ regime-adjusted threshold (TINY 20, NORMAL 18, LARGE 17) AND DI aligned (+DI > −DI for BUY). Log: `"NATIONALUM: ADX 14.2 < 18.0 (chop, regime=NORMAL) and |score| 2.8 < 7.0 override — skipping."` (#157, #165)
> - **VWAP guard** (after 10:15): don't BUY below VWAP, don't SELL above, don't chase >0.8% extensions.
> - **Fresh reversal.** If score just flipped by ≥ 8 points, wait one more cycle for confirmation.
>
> **Charge-aware gates:**
>
> - **Net R:R ≥ 1.0:1** after round-trip charges. A gross 1.5:1 often becomes 0.9:1 on small qty.
> - 🆕 **Gross target ≥ 3× round-trip charges** (Roadmap #162, retuned by #238). Prevents the "Rs.10 target, Rs.4 charge → Rs.6 net" trap, and after #238 leaves ~2× charges as a slippage cushion on every trade. Log: `"IRCTC: gross target profit Rs.12.00 < 3.0× round-trip charges Rs.8.50 — target too thin after costs. Skipping."`
>
> **Acceptance:** When every check above returns "yes", you see `"✓ {symbol}: ALL CHECKS PASSED [regime=NORMAL] — BUY 5x @ Rs.1,234.50 | SL Rs.1,221.00 (1.1%) | Target Rs.1,255.00 (1.7%) | Cost Rs.6,173"`. The bot then places the LIMIT entry, waits up to 8s, falls back to MARKET if unfilled, and finally places the exchange SL-M.

#### 🔁 9:30 AM (9:45 expiry) – 2:45 PM — Monitor loop

Every 10 seconds (5s when price is near SL/target), for each open position, the bot asks:

10. **Hit SL?** → Cancel SL-M, exit at market, log `"STOP_LOSS"`. Sets cooldown timestamp (Roadmap #161).
10a. 🆕 **Momentum kill?** (Roadmap #198, retuned by #233 on 2026-04-27) — Runs BEFORE the SL check. For positions older than `MOMENTUM_KILL_GRACE_SECONDS` (180s — 3-min settlement window) but younger than `MOMENTUM_KILL_WINDOW_MINUTES` (5 min): if adverse move from entry `≥ MOMENTUM_KILL_MIN_ADVERSE_PCT` (0.40%, ≈4× typical NSE intraday spread) AND progress toward target `< MOMENTUM_KILL_MIN_PROGRESS_PCT` (25%) AND unrealised P&L is negative, exit at market with `exit_reason = MOMENTUM_KILL`. Skipped for `_external` and `_partial_taken` positions and for already-winning trades. Catches the slow-bleed pattern (MAZDOCK / BAJAJ-AUTO 2026-04-22) before SL is touched. The adverse-move noise floor was added after the original 60s / no-floor settings killed 4/4 morning entries on 2026-04-27 with adverse moves of −0.018% to −0.15% (inside or just outside the bid-ask spread).
11. **Hit target?** → Same as SL but `"TARGET_HIT"` and feeds into consecutive-SL reset.
12. **Trailing stop trigger?** At 1.5× risk profit, book 33% qty + move SL to lock 50% of gain. Logged as `"TRAIL_PARTIAL"` + `"TRAIL_SL_MOVE"`.
13. **Time-decay check (open positions only).** `TARGET_DECAY_PCT` shaves a small slice off the *target* of an already-open position as the day ages — never off entry math. Adopted positions get a 10-min grace window. (The earlier entry-time compression after 1 PM / 2 PM was removed by #242.)

**Every 15 minutes** (background):

14. **Zerodha sync.** Detect manually-opened MIS trades (adopt with ATR SL/target + full management) and manually-closed ones (log `EXTERNAL_CLOSE`, cancel our SL-M, stamp cooldown). Partial closes resize tracked qty + broker SL-M. Empty-`net` glitch guard: skip the cycle if API returns empty while we track ≥1 open position (Roadmap #160).
15. **Re-score + candle protect.** Re-run candle patterns + indicators on each open position. If the contrary score ≥ ±4, tighten SL toward breakeven — with a `CANDLE_PROTECT_MIN_CUSHION_PCT` (0.3%) buffer so the new SL never lands at or past the live price (Roadmap #154).
16. **NIFTY regime check.** If NIFTY flips against an open position, same tightening.
17. 🤖 **(AI mode every 30 min)** Claude reviews open positions with fresh 5-min candle snapshot. Can say HOLD / TIGHTEN_SL / EXIT / MOVE_TO_BREAKEVEN.

**Every 30 minutes** (if free slots):

18. **Opportunity re-scan.** Same pre-filter + entry pipeline on fresh quotes. If new candidates emerge, enter them. (Same 44-check pipeline as above.) In AI mode Claude ranks the new shortlist.

**Every 30 minutes** (NoAI only):

19. **Stagnant exit (two-tier).** **Tier 1** — positions open ≥ 45 min that are either losing > 0.2% (adverse) OR inside ±0.1% of entry (dead-flat) → exit at market, log `"STAGNANT EXIT"`, blacklist `{symbol}_{side}` for the rest of the day (Roadmap #156). **Tier 2** (Roadmap #172) — positions open ≥ 90 min that have covered < 20% of the entry→target distance → same exit + blacklist (`drift X% to target` reason tag). Tier 2 only runs if Tier 1 didn't already fire on this position.

20. **Signal-reversal exit (#174).** On every candle re-scan (every 15 min, free), each open position is rescored. If a held BUY's combined score flips to ≤ -7 with a confirming bearish reversal candle (or held SELL flips to ≥ +7 with a bullish one), exit immediately at market with reason `SIGNAL_REVERSAL`. Profitable positions (≥1× initial risk) are skipped — winners belong to the trailing stop. See [§Signal-Reversal Exit](#signal-reversal-exit).
21. **Signal-decay exit (#188).** Runs in the same re-scan loop, AFTER signal-reversal so a decisive flip still gets the reversal log. Catches *same-direction thesis decay*: if the entry score was high-conviction (|score| ≥ 7) but the re-scan score has shrunk to < 40% of entry magnitude (still same sign), held ≥ 30 min, and `pnl < 1R` of initial risk (book-and-go floor), exit at market with reason `SIGNAL_DECAY`. Winners ≥1R are skipped (trailing stop's job). See [§Signal-Decay Exit](#signal-decay-exit).

**At any time:**

20. **Circuit breaker** (hard). Day P&L < -3% of budget (measured since last baseline reset) → close ALL positions, pause 30 min, resume with loss-adjusted budget. Max 2 trips/day.
21. **Whipsaw pause.** 3 consecutive losing exits (STOP_LOSS, MOMENTUM_KILL, STAGNANT_EXIT, SIGNAL_DECAY, or LOSER_EXIT with `pnl < 0`; EOD/operator/external closes excluded) → pause new entries for 30 min. Pre-#244 only counted STOP_LOSS hits, which missed today's MOMENTUM_KILL streak.

#### 🕝 2:45 PM — Late-day loser exit

22. **Loser exit.** Any open position with P&L < 0 → exit at market. Breakeven positions get SL tightened to entry ±0.1%. Winners with active trails keep running.

#### 🕒 3:10 PM — Forced square off

23. **Square off all.** Cancel any pending SL-M, exit every remaining position at MARKET.

#### 🕓 3:20 PM onward — Reports

24. **Write `trading_data_{date}.json`** + `trading_report_{date}.txt` to [reports/trading/{year}/{month}/](reports/trading/).
25. **Import trades** to `data/trades.db` for future Claude context (AI mode) + tax ledger.
26. **Verify** every trade against Zerodha's order book (`scripts/verify_trades.py` invoked automatically) — corrects any avg-price drift and patches the tax ledger.
27. **Rejection audit** (`scripts/rejection_audit.py` invoked automatically). Parses today's portfolio.log for every entry the order engine SKIPPED (R:R, RVol, ADX, lunch-lull, gap-coherence, charge-floor, etc.), fetches each rejected stock's 15:30 close from Zerodha, and prints a verdict table per symbol — `AVOIDED_LOSS`, `MISSED_PROFIT`, or `NEUTRAL` — with hypothetical P&L assuming a 1-slot entry. Output is logged live AND appended to `trading_report_DD.txt` between `<!-- REJECTION_AUDIT_BEGIN/END -->` markers (idempotent). Read-only review aid; never touches positions. Disabled in DRY_RUN; kill-switch `REJECTION_AUDIT_ENABLED=False`.
28. **Backup.** `scripts/backup_data.py --ssh` (manual, user-run).

### Where AI Steps In (AI-mode summary)

Only three decisions change in `--ai` mode — everything else is identical:

| Step | NoAI | AI mode |
|------|------|---------|
| **Rank top-15 candidates (9:15 AM)** | Sort by `|score|`, take top N | Claude ranks + vetoes with qualitative analysis |
| **Review open positions (every 30 min)** | Stagnant-exit rule only | Claude sees 5-min candles + StochRSI, can HOLD / TIGHTEN / EXIT / BREAKEVEN |
| **Opportunity re-scan** | Auto-select from shortlist | Claude picks from shortlist |

Every entry/exit gate (all 44 pre-trade checks, trailing, circuit breaker, SL-M, cooldown, lunch-lull, soft-stop, peak-drawdown, charge-aware target, ADX/regime gates) runs **identically** in both modes. Claude can never bypass safety rails.

---

### Phase 1 — Pre-Market Scan (9:00 AM) — FREE

Identical in both modes. Pure computation on free Zerodha historical data.

```
For each stock in NIFTY100 (~100 stocks):
  → Price filter: skip stocks outside SCAN_MIN_PRICE–SCAN_MAX_PRICE
      (Rs.100 min; max auto-calculated from budget × MAX_POSITION_PCT)
  → Fetch 15-min candles (last 3 days) from Zerodha Historical API
  → Fetch daily candles (last 30 days) for trend context
  → Detect 14 candlestick patterns on 15-min data
      • Volume confirmation: strength ×1.3 if candle vol > 1.5× avg
      • Freshness decay: current = 1.0×, 1-ago = 0.7×, 2-ago = 0.4×
  → Compute 14 technical indicators → composite score (-24 to +24)
  → Add RVol bonus/penalty (-1 to +1)
  → Apply sector momentum filter: when ≥3 stocks in a sector agree
    on direction, boost each ±0.5
  → Apply Nifty trend hard filter (against-trend needs |score| ≥ 3)
  → Sector diversification: max 2 per sector (12 sectors in SECTOR_MAP)
  → Filter: |score| ≥ V2_MIN_SCORE (default 2.0)
  → Compute score momentum (Δ from previous scan): accelerating vs decelerating
  → Take top 15 by |score|
```

### Phase 2 — Stock Selection

#### NoAI (Default) — FREE

```
Take top N candidates (N = MAX_POSITIONS - open_positions)
  + all remaining pre-filtered candidates as fallback:
  → Side = BUY if score > 0, SELL if score < 0
  → SL = price × (1 ± DEFAULT_STOP_LOSS_PCT)
  → Target = price × (1 ± DEFAULT_TARGET_PCT)
  → Qty via score-weighted sizing (conviction-proportional, capped at MAX_POSITION_PCT)
  → Rationale auto-generated from indicators:
      "Score +8.3 | RSI 28 | EMA BULLISH_CROSS | ST UP | Patterns: HAMMER"
  → Skip symbols already traded today or currently held
  → Validate budget (primary picks only)
  → Promote fallbacks when _validate_budget drops primary stocks (e.g. price too high)
```

**Fallback promotion:** If a primary pick fails entry checks (R:R, min profit, etc.), the entry loop tries the next candidate automatically. When `_validate_budget` drops an expensive primary stock, the next fallback is promoted into the freed slot. No more "selected 1, entered 0".

#### Claude AI (`--ai`) — 1 Claude call

Sends enriched snapshot per candidate — price, RSI, EMA signal, VWAP, SuperTrend direction, StochRSI signal, detected patterns, prev-day S&R, RVol, composite score. The prompt includes:

- Time-phase context (Opening / Morning Trend / Midday Lull / Afternoon / Late Session)
- 14-indicator confluence checklist (SuperTrend, EMA, RSI, pattern, VWAP, VWAP Bands, MACD, ORB, Gap, RVol, Hourly EMA, BB Squeeze, ADX, Fib, StochRSI, Prev-Day S&R, Daily EMA Bias)
- All config values derived from `config.py` (R:R floor, SL range, trail params) — no hardcoded numbers in prompts
- Rank/veto role: Claude must rank and filter from pre-filtered candidates, not generate new ones
- Hard rejection filters (extended move >2%, RSI extremes, R:R below time-based floor, against-SuperTrend without reversal)
- Indian market awareness (NIFTY regime, F&O expiry, sector clustering)
- Common mistakes to avoid (chasing extended moves, all-same-direction)

Claude returns: ENTRY / SL / TARGET / QTY / RATIONALE per trade.

### Phase 3 — Entry

Identical in both modes. The entry loop processes candidates in score order (primary first, then fallback).

1. Wait `ENTRY_DELAY_MINUTES` (5 min) after market open
2. Confirm `ENTRY_MIN_MOVE_PCT` (0.3%) directional move from open price
2b. **Stale-score guard** (#196) — when wait ≥ `FRESH_ENTRY_RECHECK_MIN_WAIT_MINUTES` (5), re-run `_analyse_stock` on each survivor. Abort if sign flipped or `|fresh| < |entry| × FRESH_ENTRY_DECAY_FRACTION` (0.6). Otherwise refresh `_entry_score` so all downstream score-gated checks (lunch-lull, ADX override, gap-coherence, average-down) compare against the freshest value. Fail-open per-symbol; V1 path unaffected (no `_analyse_stock`)
3. ATR-based SL/target calculation — uses **pure ATR** when available (config defaults are fallback only). Computed via `_compute_atr_sl_target()` helper (single source of truth)
4. Pre-trade checks pass (44 checks — see [Risk Management — Entry Pre-Checks](#risk-management--entry-pre-checks))
5. **Fallback on rejection:** if a trade fails any check, the entry loop tries the next candidate from the plan. Loop stops when all position slots are filled or all candidates exhausted
6. Place entry order on Zerodha: LIMIT at LTP + 1 tick buffer (tick size fetched per instrument via `zerodha.get_tick_size()` — Rs.0.05 for most stocks, Rs.0.50 for high-priced scripts). Price is rounded to the nearest valid tick multiple. BUY bids 1 tick above LTP, SELL asks 1 tick below. Wait full `LIMIT_ORDER_TIMEOUT` (8s) polling filled qty every second — don't exit early on first partial fill. If fully filled → done. If partially filled after full timeout → cancel remainder, accept partial. If zero filled → cancel, retry with fresh LTP (up to `LIMIT_MAX_RETRIES`). Fall back to MARKET after all LIMIT attempts fail. Exits always MARKET for guaranteed fill. (DRY_RUN simulates without orders)
7. Fetch actual fill price — scale SL/target proportionally around fill
8. Place SL-M counter-order on exchange (if `USE_EXCHANGE_SL = True`)

### Phase 4 — Monitor Loop (9:30 AM – 3:10 PM, 9:45 start on expiry Thursdays)

| Interval | Action | Cost |
|----------|--------|------|
| Every 10s (5s near SL/target) | SL/target check, trailing stop, time-decay | Free |
| Every 15 min | Sync with Zerodha — detect manual MIS positions. Adopted positions get ATR-based SL/targets and full bot management | Free |
| Every 15 min | Re-run candle analysis on open positions. **Signal-reversal exit** (#174) → opposite-side score ≥ ±7 + reversal pattern, exit. **Signal-decay exit** (#188) → same-side score collapsed to <40% of entry conviction, hold ≥ 30 min, pnl < 1R of initial risk (book-and-go), exit. Otherwise **auto-protect** → contrary score ≥ ±4, tighten SL (50% profit lock or breakeven) | Free |
| Every 15 min | NIFTY trend recheck (regime shift detection) | Free |
| Every 30 min (if free slots) | Opportunity re-scan for new trades | 1 Claude call (`--ai`) / Free (NoAI) |
| Every 30 min (`--ai` only) | Claude reviews open positions with fresh 5-min candle data + StochRSI + 15-min composite score | 1 Claude call |
| Every 30 min (NoAI only) | Stagnant position check (two-tier): at 45 min exit if adverse/dead-flat; at 90 min exit if <20% progress to target | Free |

**On position close (any mode):** If free slots exist, 2-minute cooldown then partial re-scan to fill empty slots.

**Circuit breaker cooldown:** After CB trips, wait 30 min then resume with loss-adjusted budget. P&L baseline resets — only new losses after resume can re-trip. Max 2 trips/day.

### Phase 5 — Square Off & Report

- **2:45 PM (loser exit):** Exit losing positions at market. Tighten breakeven SL to entry ±0.1%. Winners with active trails keep running.
- **3:10 PM (square off):** Close all remaining positions.
- Generate `trading_data_{date}.json` + `trading_report_{date}.txt`
- Record trades to `data/trades.db` (for Claude learning context)
- Fill intraday tax ledger via `fill_intraday_ledger.py`
- Verify trades against Zerodha (`verify_trades.py`) — same-day price/charges sync
- **Rejection audit** (`rejection_audit.py`) — parses skipped-entry logs, fetches close prices, prints verdict table (`AVOIDED_LOSS` / `MISSED_PROFIT` / `NEUTRAL`) and appends to the trading report. Review aid only.

---

## Technical Indicators (14)

All indicators computed on 15-min candles. Total composite score range: **-24 to +24**.

### Primary Trend Indicators

| Indicator | Score | Description |
|-----------|-------|-------------|
| **SuperTrend(7, 2.0)** | ±3 (change), ±1 (cont.) | ATR trend-follower. Period 7 / multiplier 2.0 optimised for intraday (default 10/3.0 too slow). Indian algo trading standard. Configurable via `SUPERTREND_PERIOD`/`SUPERTREND_MULTIPLIER`. |
| **EMA(9/21) Crossover** | ±2 (cross), ±1 (spread) | 2.25h vs 5.25h fast/slow. Captures same-day momentum shifts. |
| **RSI(14)** | ±1 to ±3 | Wilder smoothing. <20 = +3 (oversold), 20-30 = +2, >80 = -3, 70-80 = -2. |

### Confirmation Indicators

| Indicator | Score | Description |
|-----------|-------|-------------|
| **VWAP** | ±1 | Institutional fair value (today's candles). Above = buyers in control. |
| **VWAP SD Bands** | ±0.5 to ±1 | ±2σ = strong mean-reversion (±1), ±1σ = moderate (±0.5). Overrides basic VWAP at extremes. |
| **MACD(12,26,9)** | ±0.5 to ±1 | Histogram direction + acceleration. Growing = confirm, shrinking = warning. |
| **ORB (Opening Range)** | ±2 | Uses **2nd candle (9:30-9:45)** — avoids auction noise in 1st candle. Decays through day (×0.5 after 10:30, 0 after noon). |
| **Gap Analysis** | ±1 | >1% gap + high volume = continuation. Low volume = fill risk. |
| **Hourly EMA Alignment** | ±1 | Synthetic hourly candles. Both timeframes aligned = confluence bonus. |
| **Bollinger Squeeze** | ±0.5 | BB width < 20-period average = squeeze. Breakout from squeeze adds ±0.5 directionally. |
| **StochRSI(14,14)** | info only | Stochastic of RSI: %K/%D crossover. Signals: BULLISH_CROSS, BEARISH_CROSS, OVERBOUGHT, OVERSOLD. Fed to Claude prompt and NoAI snapshot for entry timing. Not scored directly. |
| **Daily EMA(9/21) Bias** | ±1 | Higher timeframe trend (only if spread > 1%). |

### Modifier Indicators

| Indicator | Score | Description |
|-----------|-------|-------------|
| **ADX(14)** | ±0.5 modifier | <20 WEAK: halves trend continuation scores. >30 STRONG: +0.5 directional bonus. |
| **Fibonacci Retracement** | ±0.5 | 38.2/50/61.8% of prev-day range. **Directional**: +0.5 if SuperTrend UP (support), -0.5 if DOWN (resistance). |
| **Prev-Day S&R** | ±0.5 to ±1 | Yesterday's H/L/C. AT_RESISTANCE = -1, AT_SUPPORT = +1, PIVOT = ±0.5. |

### Penalty / Cap

| Indicator | Effect | Description |
|-----------|--------|-------------|
| **Extended Move Penalty** | ±1.5 to ±3 | >2% from open → -3 penalty. **Only penalises chasing** (score direction matches move direction). Contrarian setups (score opposes extended move) are not penalised. |
| **RSI Extreme Hard Cap** | caps at ±3 | RSI ≥ 75 → max +3. RSI ≤ 25 → min -3. |
| **RVol Bonus/Penalty** | ±1 | Relative volume vs 20-period average. High RVol = +1, low = -1. |

### Score Interpretation

| \|Score\| | Signal | Action |
|-----------|--------|--------|
| ≥ 5 | STRONG | High conviction trade |
| 2-5 | MODERATE | Passes filter (Claude decides in `--ai`; auto-entered in NoAI) |
| < 2 | WEAK | Filtered out |

---

## Candlestick Patterns (14)

### Single-Candle (6)

| Pattern | Signal | Strength | Key Feature |
|---------|--------|----------|-------------|
| Doji | NEUTRAL | 1 | Body < 10% of range. Indecision. |
| Marubozu | Directional | 3 | Body > 90% of range. Pure conviction. |
| Hammer | BULLISH | 2 | Small body at top, long lower shadow. Requires prior downtrend. |
| Inverted Hammer | BULLISH | 2 | Small body at bottom, long upper shadow. |
| Shooting Star | BEARISH | 2 | Inverted hammer but after uptrend. Buyers failed. |
| Hanging Man | BEARISH | 2 | Hammer shape after uptrend. Distribution warning. |

### Multi-Candle (8)

| Pattern | Signal | Strength | Key Feature |
|---------|--------|----------|-------------|
| Bullish Engulfing | BULLISH | 2-3 | Engulfs prior bearish candle. 3 if after downtrend. |
| Bearish Engulfing | BEARISH | 2-3 | Engulfs prior bullish candle. 3 if after uptrend. |
| Morning Star | BULLISH | 3 | 3-candle reversal: big bear → small body → big bull. Star must be in lower 40% of first candle range. |
| Evening Star | BEARISH | 3 | 3-candle reversal: big bull → small body → big bear. Star must be in upper 60% of first candle range. |
| Three White Soldiers | BULLISH | 3 | 3 consecutive up candles, each higher. Each opens within prior candle's body (Nison's definition). |
| Three Black Crows | BEARISH | 3 | 3 consecutive down candles, each lower. Each opens within prior candle's body. |
| Bullish Harami | BULLISH | 1 | Small bull inside prior large bear. Weak — needs confirmation. |
| Bearish Harami | BEARISH | 1 | Small bear inside prior large bull. |

**All patterns:** Volume-confirmed (×1.3 high vol, ×0.5 low vol) and freshness-decayed (1.0× current candle, 0.7× 1-ago, 0.4× 2-ago).

---

## Risk Management — Entry Pre-Checks

Every trade must pass these 44 checks in order. If any fails, the trade is rejected and the next fallback candidate is tried.

**Note on ordering** — gates are listed in the order they actually run in `enter_trade()`. The first four gates (`0aa`, `0ab`, `0abc`, `0ac`) are session-wide / cross-day risk circuit breakers added 2026-05-05 / 2026-05-06 in response to the 04-22 → 05-05 multi-day losing streak; they fire BEFORE the legacy intra-day gates (`0d` choppy-morning onward).

| # | Check | Config | Behaviour |
|---|-------|--------|-----------|
| -1 | **Earnings/results-day blackout** (#219, was #167) | `EARNINGS_BLACKOUT_ENABLED = True`, `EARNINGS_BLACKOUT_SYMBOLS_2026: dict[str, list[str]]` | Scanner-side pre-filter inside `_prefilter_universe()` — runs BEFORE scoring, so the symbol never reaches `enter_trade()` at all. Reads today's `"YYYY-MM-DD"` key and drops listed symbols from `price_filtered`. User-maintained dict (empty by default = zero behaviour change); user updates each Friday from the next week's NSE corp-action calendar. Earnings-day moves are dominated by surprise content, not technicals — our setups underperform on these days |
| 0aa | **Rolling-PF circuit breaker** (#253) — **DISABLED 2026-05-05** | `ROLLING_PF_PAUSE_ENABLED = False`, `ROLLING_PF_PAUSE_LOOKBACK_DAYS = 3`, `ROLLING_PF_PAUSE_THRESHOLD = 0.6`, `ROLLING_PF_PAUSE_NET_FLOOR = -300.0`, `ROLLING_PF_PAUSE_MIN_TRADES = 5` | Same-day post-ship audit found this gate is net-negative once #251 (directional pause) is also active. Counterfactual replay over 17 evaluable sessions: #251 alone gains Rs.+503 vs baseline; adding #253 on top yields Rs.+387 (incremental Rs.−6). The full-session blackout (a) costs +Rs.488 on the 04-10 false-pause where a single big-loss day on 04-09 armed the gate and blocked a Rs.+488 winner; (b) blocks the SELL side that's been profitable during the BUY collapse (e.g. 05-05 SELL net was +Rs.28). Industry standard (Kelly criterion, fractional Kelly): when uncertain about edge, REDUCE bet size, do NOT bet zero. Bet-zero is justified only when edge is provably ≤ 0, which 24 days of data cannot establish. Code, ledger reading, and PF computation are kept intact for future re-enable; current behaviour is no-op. To re-enable, validate thresholds against ≥ 60-90 days of post-#251 data and confirm there is incremental EV above #251. |
| 0ab | **Directional auto-pause** (#251 + intraday-bounce bypass #251b + tape-breadth bypass #251c) | `DIRECTIONAL_PAUSE_ENABLED = True`, `DIRECTIONAL_PAUSE_LOOKBACK_DAYS = 7`, `DIRECTIONAL_PAUSE_MIN_TRADES = 10`, `DIRECTIONAL_PAUSE_WR_THRESHOLD = 0.30`, `DIRECTIONAL_PAUSE_NIFTY_FLOOR_PCT = 0.0`, `DIRECTIONAL_PAUSE_RECOVER_WR = 0.40`, `DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_PCT = 1.0`, `DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_MIN_SCANS = 2`, `DIRECTIONAL_PAUSE_BREADTH_BYPASS_ENABLED = True`, `DIRECTIONAL_PAUSE_BREADTH_BYPASS_RATIO = 0.40`, `DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_PAUSED_SIDE = 3`, `DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_TOTAL = 5` | Session-wide BUY-only OR SELL-only pause armed at startup. Arms when, over the trailing 7 trading days for one side: n_trades ≥ 10 AND WR ≤ 30% AND NIFTY 7d return is on the contra side (BUY pause needs NIFTY 7d ≤ 0% / SELL pause needs ≥ 0%). Origin: 2026-04-23 → 2026-05-05 BUY-side WR collapsed to 12.5% (5/40) while SELL held 42.9% (6/14). Existing positions on the paused side are managed normally. The contra side trades unimpeded; this is intentionally a directional gate, not a global one. Fails open on DB or NIFTY-fetch failure. **Bypass paths (in priority order, both retain pause STATE for inspection):** **(a) #251b NIFTY-bounce** — ≥ MIN_SCANS consecutive NIFTY intraday-return readings whose sign favours the paused side returns False with one-shot WARN. **(b) #251c tape-breadth** — the scanner's post-V2_MIN candidate snapshot (forwarded each scan via `engine.set_tape_breadth(scanner.last_tape_breadth)`) shows the paused side holding ≥ RATIO of {BUY+SELL} with absolute floors met (A/D-line analogue — NIFTY is cap-weighted ~50% top-7, so a "flat NIFTY but mid-caps rally" day never trips bypass (a) but trips (b)). The 30-40% band between BREADTH_BEARISH_BUY_RATIO (#212) and BREADTH_BYPASS_RATIO is an explicit "uncertain — neither rule fires" zone so the two breadth-related gates never overlap. Both bypasses self-limit: snapshot is overwritten each scan, gate auto-re-engages once conditions revert. Closes the **BUY-pause loop trap** in sustained-bear regimes |
| 0abc | **Opposing-side fractional-Kelly cap** (#251a, 2026-05-06; tune 2026-05-07) | `DIRECTIONAL_PAUSE_OPPOSING_MIN_TRADES = 20`, `DIRECTIONAL_PAUSE_OPPOSING_THIN_MAX_ENTRIES = 5` | Fires only when 0ab armed against the OTHER side AND the un-paused (opposing) side had thin evidence (n < 20) at session start. Caps entries on the surviving side at 5 per session per Kelly criterion (typical lookback is 50-60 trades for win-prob estimation; binomial CI at n=14 with p≈0.5 is ±26pp — statistical noise). Bumped 3 → 5 on 2026-05-07 after live SELL-side WR=67% (n=3) under cap-3 left profit on the table. Reduces concentration tail-risk on the un-validated side without disabling it. Counter armed by `_maybe_arm_opposing_thin()` inside `arm_multiday_pauses()`; checked by `is_opposing_thin_capped(side)`; bumped by `record_entry(now, side=...)` on each successful opposing-side entry. Disabled implicitly when 0ab is disabled |
| 0ac | **Entry-burst cap** (#179, budget-tiered #179a) | `ENTRY_BURST_CAP_ENABLED = True`, `ENTRY_BURST_CAP_MAX_ENTRIES_PER_60S = 2`, `BUDGET_BURST_CAP_DELTA = {"TINY": 0, "SMALL": 0, "NORMAL": 1, "LARGE": 2}` | Rolling-60-second window cap with per-budget delta. Engine maintains a `deque[maxlen=32]` of recent successful-entry timestamps; reject any (cap+1)-and-later entry inside any 60-second window where `cap = effective_burst_cap() = base + BUDGET_BURST_CAP_DELTA[regime]` (floored at 0). Origin: 2026-04-22 → 2026-05-05 audit found that 12 of 13 entry-bursts (≥3 entries inside 60s) ended with all trades on the burst losing together — suggesting the burst itself is a regime signature (correlated tape pressure) rather than independent setups. The 92% lose-together evidence was sourced exclusively from a Rs.50K SMALL account, so SMALL/TINY stay at the audit-validated cap-2 while NORMAL/LARGE accounts get +1 / +2 deltas (effective cap 3 / 4) to avoid single-threading their genuine 5-8 morning slot patterns (#179a, 2026-05-06). Industry parallel: prop-firm risk frameworks tier max-concurrent caps by account size |
| 0d | **Choppy-morning entry pause** (#192) | `CHOPPY_MORNING_PAUSE_ENABLED = True`, `CHOPPY_PAUSE_ADX_THRESHOLD = 16`, `CHOPPY_PAUSE_MINUTES = 15` | Pauses NEW entries when NIFTY ADX < 16 for ≥3 consecutive scans inside the 09:30-10:30 IST window AND ≥2 STAGNANT_EXIT/SIGNAL_DECAY exits occurred in the last 10 min. Sliding 15-min pause; can re-arm multiple times per morning. Existing positions managed normally. Manager feeds NIFTY ADX via `engine.record_nifty_adx()` each NIFTY-recheck tick |
| 0a | **Lunch-lull skip** (#164) | `LUNCH_LULL_ENABLED = True`, 11:30-12:15 | Reject new entries inside the lowest-volume window unless \|score\| ≥ `LUNCH_LULL_SCORE_OVERRIDE` (currently 5.7, lowered from 6.0 by #221 then nudged 5.5 → 5.7 in the review-pass-1 fix; well above the V2_MIN_SCORE base of 2.0). Boundary-exclusive on the right |
| 0e | **VIX intraday-spike entry pause** (#211) | `VIX_SPIKE_ENTRY_PAUSE_ENABLED = True`, `VIX_SPIKE_PCT = 10.0` | Fires immediately after 0d. `OrderEngine.is_vix_spike_active()` returns True when manager's `_check_vix_spike()` (compares current INDIA VIX vs day-open) crossed the threshold on the latest NIFTY recheck. Closes the entry-path hole left by #181 — manager already paused the opportunity scan and the all-closed re-scan, but per-trade `enter_trade()` was bypassed by initial pre-market scan and partial re-scan paths. Existing positions managed normally |
| 0b | **Daily-loss soft stop** (#163, MTM-aware via #166) | `DAILY_LOSS_SOFT_STOP_PCT = 1.5`, `MTM_AWARE_CB_ENABLED = True` | Reject new entries when `effective_day_pnl()` (closed P&L + open-position MTM, when MTM-aware kill-switch on) ≤ -1.5% of budget. Existing positions still managed. Hard circuit breaker at 3% still closes all |
| 0c | **Peak-drawdown stop** (#168, MTM-aware via #166) | `PEAK_DRAWDOWN_STOP_PCT = 1.5`, `PEAK_DRAWDOWN_MIN_PEAK_PCT = 0.5`, `MTM_AWARE_CB_ENABLED = True` | Tracks intraday equity high-water mark using `effective_day_pnl()` so open-position MTM contributes to both peak and current. Reject new entries when (peak − current effective P&L) ≥ 1.5% of budget AND peak itself was ≥ 0.5% of budget (so tiny early swings don't trip). Catches the "+2% by 11 AM, give it back by 13:00" pattern that soft-stop misses (closed-only day P&L never crosses zero). Existing positions still managed |
| 1 | **Price validation** | — | If Claude's price deviates >5% from Zerodha live, use live price |
| 2 | **Bid-ask spread** | `MAX_SPREAD_PCT = 0.3` (base), regime-tightened via `BUDGET_SPREAD_DELTA` to 0.20% on TINY/SMALL (#236) | Skip if spread > `effective_max_spread()`. Smaller accounts have a tighter cap because spread eats a larger share of the per-trade charge hurdle |
| 2a | **Impact-cost / depth check** | `MAX_IMPACT_COST_PCT = 0.2` | Walk top-5 order-book levels on the side we'd hit (asks for BUY, bids for SELL); compute weighted-average fill for our full qty. Skip if slippage vs LTP > 0.2%, or if visible top-5 depth < our qty. Fail-open (log warning, let trade through) when depth data is missing/malformed. Catches paper-thin top-of-book traps that spread-only misses |
| 2b | **Volume confirmation** | RVol ≥ 0.7× avg | Live mode: skip if volume too low. Falls back to scan-time RVol when live average unavailable (Kite API doesn't provide average_volume) |
| 3 | **ATR SL/target** | `ATR_MULTIPLIER = 1.5`, `RR_TARGET_RATIO = 1.5` | Pure ATR when available (1.5:1 R:R). Config defaults fallback only. SL capped at 2.5% |
| 3b | **Min SL distance floor** | `MIN_SL_DISTANCE_PCT = 0.8`, expiry `1.0` | ATR on high-priced stocks can produce 0.4-0.6% SLs that wick on normal noise. Widens SL to floor and proportionally widens target to preserve R:R |
| 3c | **R:R safety floor** | Time-based + adaptive | See [R:R Floor System](#rr-floor-system) below |
| 4 | **Late-entry reduction** | After 1 PM: −20%, 2 PM: −25% | Target compressed. R:R floor per time period ensures compressed R:R is still worth trading |
| 5 | **Min profit check** | `MIN_EXPECTED_PROFIT = Rs.135` (base), regime-bumped via `BUDGET_MIN_PROFIT_DELTA` (#237) | Skip if `\|target − entry\| × qty < effective_min_profit()` (3× round-trip charges; budget-adaptive: Rs.135 TINY/SMALL, Rs.200 NORMAL, Rs.400 LARGE) |
| 6 | **Budget check** | `MAX_POSITION_PCT = 40%` | Auto-reduce qty to fit. If qty < 1 → skip |
| 7 | **Max positions** | Dynamic (2-5 from budget) | Includes external/manual positions |
| 8 | **Duplicate guard** | — | No two positions in same stock |
| 9 | **Sector concentration** | Max 2 per sector | 12 sectors in SECTOR_MAP |
| 10 | **Direction diversification** | Dynamic (score-aware) | Score ≥5: all slots in same dir allowed. Score <5: max `N−1` in same direction. Prevents forcing weak counter-trend trades on trending days |
| 11 | **Short cutoff** | `SHORT_ENTRY_CUTOFF_HOUR = 13` | No new shorts after 1 PM. Post-cutoff SELL slots reallocated to BUY (if BUY candidates with score ≥4.0 exist) |
| 12 | **Max re-entries** | `MAX_REENTRIES_PER_STOCK = 2` | Per stock per day |
| 13 | **Declining re-entry block** | — | If re-entering a stock on the SAME SIDE already traded today, block when new \|score\| < previous \|score\| (setup weakening). Opposite-side re-entries (a real reversal) bypass this gate AND the per-symbol cooldown (16a, keyed by SYMBOL_SIDE); they are protected by the standard entry gates — ADX, RSI, VWAP, gap-coherence (#185) |
| 14 | **RSI contradiction filter (symmetric)** | `RSI_SELL_BLOCK_THRESHOLD = 70`, `RSI_BUY_BLOCK_THRESHOLD = 75` | Block SELL when RSI > 70 (buying pressure). Block BUY when RSI > 75 (overbought extension). Block BUY when RSI < 30. Block SELL when RSI < 25 (oversold extension) |
| 14b | **Pattern-direction entry veto** (#190) | `PATTERN_VETO_ENABLED = True`, override `PATTERN_VETO_OVERRIDE_SCORE = 8.0` | Mirror of SIGNAL_REVERSAL exit (#174) at ENTRY. If entry-tick patterns include an opposite-side reversal (BUY with `BEARISH_ENGULFING`/`EVENING_STAR`/`BEARISH_HARAMI`/`SHOOTING_STAR`/`HANGING_MAN`/`THREE_BLACK_CROWS`, mirror set for SELL) AND `\|score\| < 8.0`, skip. Catches PNB/TRENT-style 2026-04-22 stagnant losers where score absorbed pattern weight but the chart was printing a flip pattern |
| 14c | **Pattern↔tech contradiction penalty** (#200, scanner-side) | `PATTERN_CONTRADICTION_PENALTY_ENABLED = True`, `PATTERN_CONTRADICTION_PENALTY = 2.0`, `PATTERN_INDECISION_PENALTY = 0.5` | Applied at scanner combine before any entry gates run. Subtracts 2.0 from `\|combined_score\|` when patterns include an opposite-side reversal (e.g. BUY candidate showing `BEARISH_*`) and 0.5 when patterns include indecision (`DOJI`). Penalties stack and are clamped at 0 (sign never flips). Operates on magnitude so symmetric for BUY/SELL. Same-direction patterns are not boosted (zero is the floor). Closes the gap where high tech score outvoted contradicting visual structure |
| 14d | **Tape-breadth filter** (#212, scanner-side) | `BREADTH_FILTER_ENABLED = True`, `BREADTH_BEARISH_BUY_RATIO = 0.30`, `BREADTH_BULLISH_SELL_RATIO = 0.30`, `BREADTH_PENALTY = 0.5`, `BREADTH_MIN_CANDIDATES = 5` | After the V2_MIN_SCORE filter, count BUYs vs SELLs in `passed_score`. If `buy_ratio ≤ 0.30` (bearish tape), subtract 0.5 from `\|combined_score\|` of every remaining BUY (mirror for bullish tape and SELLs). Operates on magnitude so sign is preserved. Re-applies the score floor afterwards so penalised-below-floor candidates drop out naturally before the entry pipeline ever sees them. Skipped when `len(passed_score) < 5`. Closes the FII-heavy-sell day pattern where individual scores look fine but the broader tape is one-directional |
| 15 | **Daily trade cap** | `MAX_TRADES_PER_DAY = 12` (regime-adjusted) | Prevent overtrading churn. Expiry: capped at `EXPIRY_MAX_TRADES_PER_DAY = 5`. Budget-regime deltas (#165): TINY -4, SMALL -2, NORMAL 0, LARGE +3 |
| 16 | **Stagnant churn guard** | — | If a stock+direction was exited as stagnant today, don't re-enter it |
| 16a | **Per-symbol re-entry cooldown** (#161) | `RE_ENTRY_COOLDOWN_MINUTES = 30` | Block re-entry of same SYMBOL_SIDE within 30 min after ANY exit (SL / target / stagnant / external). Opposite direction still allowed. Override at \|score\| ≥ `RE_ENTRY_SCORE_OVERRIDE` (7.0) |
| 16b | **Average-down prevention** (#195) | `AVG_DOWN_PREVENTION_ENABLED = True`, `AVG_DOWN_SCORE_DELTA = 1.0`, `AVG_DOWN_LOOKBACK_MINUTES = 120`, override `AVG_DOWN_OVERRIDE_SCORE = 8.0` | Runs AFTER cooldown 16a. When the prior exit of the same SYMBOL_SIDE was `STAGNANT_EXIT` or `SIGNAL_DECAY` within the last 120 min AND `\|new_score - last_exit_score\| ≤ 1.0`, reject the re-entry as same-magnitude false signal. Override at `\|score\| ≥ 8.0` for genuine reversal-strength signals. SIGNAL_DECAY callers stamp `pos['_exit_score'] = fresh_score` so the gate compares against the decayed score; STAGNANT_EXIT falls back to `_entry_score` |
| 17 | **VWAP trend block** | ±0.3% deviation | After 10:15 AM only (VWAP needs ≥1 hour of candles for stability). Block BUY when price > 0.3% below VWAP. Block SELL when price > 0.3% above VWAP (fighting institutional flow) |
| 17b | **VWAP extension block** | `VWAP_EXTENSION_BLOCK_PCT = 0.8`, override `VWAP_EXT_SCORE_OVERRIDE = 6.0` | Block BUY when price > +0.8% above VWAP / SELL when > 0.8% below VWAP (chasing extended move). Override allowed when \|score\| ≥ 6.0 |
| 17d | **VWAP statistical-band gate** (#201) | `VWAP_BAND_GATE_ENABLED = True`, override `VWAP_BAND_OVERRIDE_SCORE = 7.0` | Reads `vwap_band` classification (`AT_UPPER_2SD` / `AT_UPPER_1SD` / `INSIDE` / `AT_LOWER_1SD` / `AT_LOWER_2SD`) from the entry-tick indicator snapshot. Block BUY when price sits at upper 1σ/2σ band and SELL when at lower 1σ/2σ. Stricter than 17b's % distance check because bands adapt to today's realised volatility; complementary defence (both can act). Override at \|score\| ≥ 7.0 (intentionally above 17b's 6.0 — only the strongest convictions justify chasing a statistical extension). Fails open if snapshot/band field missing |
| 17c | **Fresh reversal guard** | `FRESH_REVERSAL_DELTA_THRESHOLD = 8.0` | If \|score_delta since last scan\| ≥ 8, wait one more cycle for confirmation. Avoids trading the first bar of a violent reversal |
| 18 | **Net-of-charges R:R** | Net R:R ≥ 1.0:1 | Computes round-trip charges; ensures profit after costs ≥ risk after costs |
| 18a | **Charge-aware target multiple** (#162, retuned by #238) | `MIN_PROFIT_CHARGE_MULTIPLE = 3.0` | After net R:R passes, reject when gross target profit < 3× round-trip charges. Ensures ~2× charges as cushion for slippage |
| 18b | **Gap-coherence gate** (#173) | `GAP_COHERENCE_GATE_ENABLED = True`, override `GAP_COHERENCE_OVERRIDE_SCORE = 7.5` | Reject `BUY` on `GAP_DOWN_STRONG` and `SELL` on `GAP_UP_STRONG` (entry direction contradicts overnight institutional flow) unless `\|score\| ≥ 7.5`. Only acts on the high-conviction STRONG gaps; WEAK / `NO_GAP` not gated. Fails open when the indicator snapshot is missing/malformed |
| 18c | **Circuit-limit (UC/LC) entry guard** (#180) | `CIRCUIT_LIMIT_GUARD_ENABLED = True`, `CIRCUIT_LIMIT_BUFFER_PCT = 1.0` | Reject `BUY` when intraday move ≥ +(20 - buffer)% from prev close, `SELL` when ≤ -(20 - buffer)%. Within 1% of the ±20% daily freeze the order book becomes one-sided — SL-M can't fill, MIS auto-square at 15:20 takes a distressed price. Fails open when `ohlc.close` missing in the live quote |
| 18d | **Late-entry score-floor bump + no-rescue-zone clamp** (#202, simplified by #225, retuned by #239, coupled by #246) | `LATE_ENTRY_TIGHTENING_ENABLED = True`, `LATE_ENTRY_HOUR = 10`, `LATE_ENTRY_MIN_SCORE_BUMP = 1.0`, `LATE_ENTRY_NO_RESCUE_FLOOR_ENABLED = True` (reuses `SIGNAL_DECAY_MIN_ENTRY_SCORE = 7.0`) | After `LATE_ENTRY_HOUR` (10:00 IST), reject when `\|score\| < max(effective_min_score() + 1.0, SIGNAL_DECAY_MIN_ENTRY_SCORE)`. The bump still stacks on regime/loss adjustments; the new clamp (#246, 2026-04-28) ensures the entry floor is never lower than the rescue-gate floor — the in-trade rescue paths (`_signal_decay_exit`, `_signal_reversal_exit`) both refuse to act below 7.0, so any sub-7 entry after 10:00 lives in a no-rescue zone. The threshold is REUSED from the rescue gate — no new `*_MIN_SCORE` constant — so the two stay coupled by code review. Rejection log tags the binding floor (`NO_RESCUE_ZONE` vs `late-entry tightening`) so the daily rejection-audit grades them separately. (Bump raised 0.5 → 1.0 by #239 after first live day showed +0.5 was too gentle; the original #202 also added a late-only R:R floor and a late-only concurrent-positions cap, both removed by #225 — the R:R guard now lives in always-on `RR_HARD_FLOOR`, and concurrency is fully owned by `dynamic_max_positions(budget)` all day.) |

### R:R Floor System

The R:R (Risk:Reward) floor is **a single uniform value all day** as of #243:

| Setting | Value | Config | Why |
|---------|-------|--------|-----|
| Always-on hard floor | 1.3:1 | `RR_HARD_FLOOR` | Single floor; no time-tiered or adaptive variants |

**Why single-floor now (#243):** the previous time-tiered floors (`RR_FLOOR_MORNING/AFTERNOON/LATE`), the adaptive relaxation knobs (`RR_FLOOR_RELAXED`, `RR_RELAX_AFTER_FAILS`), and the mid-day retry step (`RR_RETRY_STEP`) were already neutralised by #235 and #242. After those two changes every code path resolved to `RR_HARD_FLOOR=1.3`. #243 collapsed the dead complexity: deleted the seven decorative knobs, deleted `_time_based_rr_floor()`, simplified `current_rr_floor()` to a single `return RR_HARD_FLOOR`, deleted the manager's mid-day-retry pass (re-running with the same floor was guaranteed to reject the same candidates), and removed the `_rr_retry_active` engine state. The `RR_GIVEUP_AFTER_FAILS=5` keeper remains: after 5 consecutive zero-entry scans the bot stops trading for the day ("today is not a trading day").

**Industry rationale:** Pro intraday desks treat *"the market won't give us our edge"* as a signal, not a problem to solve by lowering thresholds. Adaptive relaxation ("we haven't traded in an hour, drop the bar") is the same instinct that bankrupts retail traders. The single hard floor enforces that discipline structurally.

**Always-on hard floor (`RR_HARD_FLOOR = 1.3`).** Computed gross R:R must be ≥ 1.3, and net-of-charges R:R must be ≥ 1.0 (the second gate is charge-aware so cheap-charge trades clear it easily; #227 in Awaiting-Data tracks whether the two gates can be merged). Default ATR geometry produces R:R = `RR_TARGET_RATIO = 1.5`, leaving ~0.2 of headroom for tick-rounding noise. Custom-target Claude trades (AI flow) and rule-based ATR trades (no-AI flow) hit the **same** gate inside `enter_trade()` — no asymmetric enforcement.

---

## Risk Management — During Trade

### Exchange SL-M Orders (`USE_EXCHANGE_SL = True`)

SL-M (stop-loss market) orders sit on the NSE exchange. When the trigger price is breached, the exchange executes the exit instantly — no polling delay.

| Event | Action |
|-------|--------|
| **Entry** | Place SL-M counter-order with trigger at SL price |
| **Trail SL** | `modify_order()` updates trigger on exchange |
| **Partial exit** | Cancel old SL-M, place new one with reduced qty |
| **SL hit** | Exchange already triggered → skip duplicate exit order |
| **Non-SL exit** | Cancel pending SL-M first, then place exit order |
| **Loser exit / Claude ADJUST_SL** | Modify exchange SL-M trigger in sync |

Only active when `USE_EXCHANGE_SL=True` AND `DRY_RUN=False`.

### Trailing Stop-Loss

| Parameter | Value | Config | Rationale |
|-----------|-------|--------|-----------|
| Trail trigger | 1.5× initial risk | `TRAIL_AFTER_RISK_MULTIPLE` | Trail starts at 1.5× initial risk profit |
| Trail lock | 50% of unrealised profit | `TRAIL_STEP_PCT` | SL sits halfway between entry and current price |

**How it works:**
1. `initial_risk = |entry − initial_sl|`
2. When `profit ≥ 1.5 × initial_risk`:
   - **First time, if qty ≥ 3:** Exit 1/3 shares (partial profit). Update SL-M for reduced qty.
   - Move SL to `entry + 50% × profit` (BUY) or `entry − 50% × profit` (SELL)
3. SL ratchets in the protective direction only (never loosens)

**TRAIL_STEP_PCT Decision History:**

| Value | Date | Commit | Rationale |
|-------|------|--------|-----------|
| 65% | 2026-03-16 | `4444248` | Set after winning trades reversed into losses. Locks more profit. |
| 50% | 2026-04-08 | `418d668` | Indian analyst review: 65% too tight for NSE's 0.5-0.7% normal pullbacks. Converted 1.5% winners to 0.3% winners. |

**Optimal range: 40-60%.** Backtest on ≥50 trades before changing.

### Time-Decay Target Reduction

After 2 PM (`TARGET_DECAY_AFTER_HOUR`), reduce target by 25% (`TARGET_DECAY_PCT`) of entry-to-target distance. Applied once per position. Skipped if late-entry reduction was already applied (prevents stacking).

### Late-Day Loser Exit (2:45 PM)

| Position State | Action |
|---------------|--------|
| Losing | Auto-exit at market |
| Near breakeven | Tighten SL to entry ±0.1% |
| Winning with trail | Trail stop handles it — keep running until square-off |

**Note:** This is NOT the full square-off. Renamed from `EOD_EXIT` to `LOSER_EXIT` because it only exits losers. Real square-off is at 3:10 PM (`SQUARE_OFF_HOUR:SQUARE_OFF_MINUTE`).

### Adoption Grace Window

When the bot picks up a position it did not originate (via `load_existing_positions` → `RESUMED`, or `sync_external_positions` → `EXTERNAL`), the position gets a `ADOPTED_POSITION_GRACE_MINUTES` (default 10 min) grace window. During grace:

- **`TIME_DECAY_TARGET` is skipped** — the bot doesn't compress targets on a trade it didn't open.
- **`LOSER_EXIT` is skipped** — the user deliberately opened this position; the bot shouldn't force-close immediately after inheriting it.

Normal risk management (software SL/target, trailing stop, stagnant exit, square-off) still applies. Grace is measured from the adoption/resume timestamp, not from the user's actual original entry time (which is unknown).

### Circuit Breaker

- Trips when day loss > 3% of budget (`MAX_LOSS_PER_DAY_PCT`)
- 30-minute cooldown (`CIRCUIT_BREAKER_COOLDOWN_MINUTES`), then baseline resets — only new losses re-trip
- Max 2 trips per day (`MAX_CIRCUIT_BREAKER_TRIPS`) → trading stops entirely
- Only resumes if enough time remains before square-off
- Set `CIRCUIT_BREAKER_COOLDOWN_MINUTES = 0` for old behaviour (circuit breaker = day over)

### Whipsaw Guard

3 consecutive losing exits (`CONSECUTIVE_SL_PAUSE_COUNT`) → pause new entries for 30 minutes (`CONSECUTIVE_SL_PAUSE_MINUTES`). Counter resets on profitable close.

Post-#244 (2026-04-27) the counter is fed by ANY losing exit — STOP_LOSS, MOMENTUM_KILL, STAGNANT_EXIT, SIGNAL_DECAY, or LOSER_EXIT with `pnl < 0`. EOD reasons (SQUARE_OFF / CIRCUIT_BREAKER) and operator/external closes are excluded — they are not strategy failures. Kill-switch `LOSS_STREAK_INCLUDE_NON_SL_LOSSES` (default `True`); flip to `False` for one-line revert to STOP_LOSS-only behaviour. Logs as `LOSS-STREAK GUARD: N consecutive losing exits`.

### Loss-Adjusted Budget

`effective_budget = budget + day_losses` (floor at 20% of original). Prevents full-size re-entry after SL hits. Only active when `LOSS_SIZING_ENABLED = True`. In live mode, Zerodha's actual margin API handles this naturally; mainly helps dry-run mode stay realistic.

### Stagnant Position Exit (NoAI Only)

Replaces Claude's position reviews. Two checkpoints — a directional check at `STAGNANT_EXIT_MINUTES` (45 min, extended +15 min in the 12:00-13:30 midday lull and on expiry days) and a hard-max progress check at `STAGNANT_HARD_MAX_MINUTES` (90 min). Either can fire; both can be disabled.

**Tier 1 — directional (45 min):**
- **Adverse**: `move_pct < -STAGNANT_ADVERSE_PCT` (default 0.2%) — trade is meaningfully losing.
- **Dead-flat**: `|move_pct| < STAGNANT_DEAD_FLAT_PCT` (default 0.1%) — trade is going nowhere.

A **slow-positive** trade (e.g., +0.25%) is allowed to continue toward target. Exiting it would lock in a sub-charge profit and waste another ~Rs.15-20 round-trip entering a replacement.

**Tier 2 — progress-to-target hard-max (#172, 90 min):**
Exits when `progress_pct < STAGNANT_MIN_PROGRESS_PCT` (default 20%), where `progress_pct = move_toward_target / (target - entry) * 100`. Catches drifters that survived Tier 1 by sitting just outside the dead-flat band on the snapshot tick (UNITDSPR 2026-04-20: 183 min for +0.03%). Target-relative so it scales naturally with the trade's own R:R — a 1.0% target trade is judged the same way as a 4% expiry-day target trade.

Decision history:
- 0.5% (original): Too aggressive with 1.2% target.
- 0.3% (2026-04-15): Still exited slow-positive winners (RECLTD +0.26%, ONGC +0.42%).
- Directional (2026-04-17): Split into adverse/dead-flat thresholds. Retired the single `STAGNANT_EXIT_MIN_MOVE_PCT` gate.
- Two-tier (2026-04-20, #172): Added 90-min progress-to-target hard-max to catch drifters that the band check missed.

In `--ai` mode, Claude reviews every 30 min instead and can recommend HOLD / EXIT / ADJUST_SL / ADJUST_TARGET with qualitative reasoning.

### Contrary Signal Protection

Every 15 min (`V2_CANDLE_RESCAN_MINUTES`), re-run candle pattern analysis on open positions. The re-scan now drives **three** layered protections (signal-reversal exit runs first; if it doesn't fire, signal-decay catches same-direction collapse; if neither fires, the SL-tightening kicks in):

**1. Signal-reversal hard exit (#174).** If a held position's combined score flips strongly in the OPPOSITE direction AND a confirming reversal candle is present, exit immediately rather than wait for the price stop. See [§Signal-Reversal Exit](#signal-reversal-exit).

**2. Signal-decay hard exit (#188).** If the score is still SAME-direction but has decayed to a small fraction of entry conviction (and the trade isn't yet in profit), exit at market. Catches the BHARTIARTL-style 5-hour drift trap. See [§Signal-Decay Exit](#signal-decay-exit).

**3. SL tightening on weaker contrary signals.** If the score flips to ±4 or stronger in the **opposite** direction (but doesn't meet the reversal-exit bar):
- If in profit: tighten SL to lock 50% of unrealised gains.
- If at breakeven or losing: tighten SL toward entry, **but never closer than `CANDLE_PROTECT_MIN_CUSHION_PCT`** (default 0.3%) from the live price.

The cushion matters: on a contrary signal the live price is already moving against us, so setting the new SL to exact entry would trigger on the very next tick. The cushion keeps the stop at arm's length from noise. The same rule applies to regime-shift-protect.

Both the software SL and the exchange SL-M trigger are updated together (see bug-fix #153).

This is automatic in both modes. In `--ai` mode, Claude additionally sees the patterns and can act on weaker contrary signals.

### Sector-Cascade Defensive SL Tightening (#220)

Runs once per candle re-scan, after per-position contrary checks. Reads the scanner's per-sector AVERAGE-score snapshot from the last two scan ticks (`scanner.last_sector_momentum` vs `scanner._prev_sector_momentum`) and tightens SLs on positions inside a fast-collapsing sector — **never opens a new trade.**

**Trigger (held BUYs; mirrored for SELLs):**
- Sector avg-score dropped by ≥ `SECTOR_CASCADE_DROP_THRESHOLD` (default 2.0) in one scan window
- AND new sector avg ≤ `−SECTOR_CASCADE_OPPOSITE_FLOOR` (default −1.5) — cross-zero, not just "less positive"
- AND we hold ≥ `SECTOR_CASCADE_MIN_OPEN` positions (default 2) on the hostile side in that sector

**Action:** software SL bumped to `_compute_protective_sl()` output (typically breakeven plus the `CANDLE_PROTECT_MIN_CUSHION_PCT` 0.3% buffer); exchange SL-M replaced via `engine._update_exchange_sl()`. Per-position try/except (#222) so a transient broker error on position N can't leave positions N+1, N+2 un-tightened — software SL is updated first (always succeeds), broker mismatch repaired on next sync via `_reconcile_orphan_sl_m()`.

**Why it matters:** sector waves move stocks together. If two of our three IT-sector BUYs are silently bleeding while the third is at SL, waiting for each individual stop to fire compounds the damage. The cascade detector tightens all open exposure in that sector to lock the still-positive ones at breakeven before the wave reaches them.

Kill-switch: `SECTOR_CASCADE_EXIT_ENABLED = True` (default).

### Signal-Reversal Exit

The static SL-M only catches **price-side** moves. A momentum reversal — large opposite combined_score plus a confirming reversal candle — is **signal-side** information that typically arrives BEFORE the price stop. Acting on it cuts losses earlier than the fixed SL would.

**Trigger (BUY position; mirrored for SELL):**
- `combined_score <= -SIGNAL_REVERSAL_SCORE` (default `-7.0`)
- AND (when `SIGNAL_REVERSAL_REQUIRE_PATTERN = True`, the default) at least one bearish reversal pattern is present: `EVENING_STAR`, `BEARISH_ENGULFING`, `BEARISH_HARAMI`, `SHOOTING_STAR`, `HANGING_MAN`, `THREE_BLACK_CROWS`. The mirrored bullish set applies for held SELLs.

**Skipped when:**
- Disabled via `SIGNAL_REVERSAL_EXIT_ENABLED = False`.
- Position is in profit ≥ 1× initial risk — winners belong to the trailing stop; one bad 15-min candle shouldn't dump a paid-up trade.
- Live price is missing/zero (no way to compute the exit P&L).

**Motivating case (HDFCBANK, 2026-04-20):** Bot held a BUY from 11:31; SL-M fired at 13:26 for Rs.-155. The very next scanner tick at 13:27 scored HDFCBANK -10.0 STRONG_SELL with `EVENING_STAR + BEARISH_HARAMI`. Held positions weren't being rescored at all — the bearish patterns had been forming for ~30 min before the price stop hit. With this exit in place, the bot would have closed the position when the patterns first crystallised, saving most of the loss.

Reason tag in trade history: `SIGNAL_REVERSAL`. Logged at WARNING level.

### Signal-Decay Exit

Companion gate to the signal-reversal exit. Where signal-reversal catches **opposite-direction flips** (BUY scoring -7, SELL scoring +7), signal-decay catches **same-direction thesis collapse** — the entry conviction has evaporated but the score hasn't crossed zero. Without this gate, weak-but-not-flipped trades sit in the slow-positive corridor (above the ±0.1% dead-flat band but below the 20% target-progress threshold) for hours and only exit on `LOSER_EXIT` at 14:45, burning a slot the whole time.

**Trigger (BUY position; mirrored for SELL):**
- `abs(_entry_score) >= SIGNAL_DECAY_MIN_ENTRY_SCORE` (default `7.0`) — only act on trades that started with real conviction. A +3 → +1 drift is statistical noise, not decay.
- AND `fresh_score` has the SAME sign as `_entry_score` — opposite-side flips are signal-reversal's domain (#174).
- AND `abs(fresh_score) < abs(_entry_score) × SIGNAL_DECAY_FRACTION` (default `0.4`) — e.g. +10.0 → +3.9 fires; +10.0 → +4.1 doesn't.
- AND elapsed since entry ≥ `SIGNAL_DECAY_MIN_HOLD_MINUTES` (default `30`) — skip the very first re-scan after entry where tick-noise dominates.
- AND `pnl < initial_risk × SIGNAL_DECAY_WINNER_SKIP_R_MULTIPLE` (default `1.0`) — **book-and-go below 1R.** Sub-1R profit has no trailing-stop cushion (the stop is at-or-below entry), so any pullback on a decayed signal bleeds the profit back to flat. Winners ≥1R keep running on the trailing stop. Fallback when `initial_sl` is missing (legacy / restart-rehydrated positions): the conservative `pnl > 0` skip so we never dump a profitable trade without a known risk reference.

**Skipped when:**
- Disabled via `SIGNAL_DECAY_EXIT_ENABLED = False`.
- `_entry_score` is missing (legacy / restart-rehydrated positions; `load_existing_positions` doesn't preserve the original score).
- `combined_score` from the re-scan is missing or non-numeric.
- Live price missing/zero.

**Motivating case (BHARTIARTL, 2026-04-21):** Entered BUY @ score +10.1 at 09:42:09. The 10:31 candle re-scan dropped the score to +3.6 (Δ-6.5 — a 64% conviction collapse). Actual price arc (from Zerodha 5-min candles): peak +Rs.109 at 11:05 (0.72R, still below the 1R winner floor), drifted back to -Rs.16 at 14:45 when `LOSER_EXIT` finally caught it. Stagnant Tier-1 (#172) couldn't fire because the price stayed outside the ±0.1% dead-flat band on every snapshot tick; Tier-2 needed ≥20% target progress, which the trade kept barely clearing. Signal-reversal (#174) requires an opposite-side flip + confirming reversal pattern — neither was present. With the 1R book-and-go, the position would have closed at the 10:31 re-scan (pnl +Rs.41 = 0.27R < 1R floor) — slot freed in 49 min instead of 303 min, booked +Rs.41 instead of drifting to -Rs.16.

**Why `< 1R` rather than `<= 0`?** Below 1R of profit, the trailing stop has no real cushion — it sits at or below entry price, so on a pullback the stop gives all the profit back. Above 1R, the trailing stop is comfortably above entry (by design: the trailer activates at `TRAIL_TRIGGER_PCT` and locks in partial profit) and can protect the winner on its own. The 1R boundary matches the natural risk unit of the trade, not an arbitrary rupee amount.

Reason tag in trade history: `SIGNAL_DECAY`. Logged at WARNING level.

---

## Market Intelligence

### India VIX Adjustments

India VIX (Volatility Index) measures 30-day expected volatility of NIFTY. Fetched once at startup, rechecked during NIFTY rechecks.

| VIX Level | Classification | Adjustments |
|-----------|---------------|-------------|
| > `VIX_HIGH_THRESHOLD` (20) | High fear | MAX_POSITIONS reduced by `VIX_HIGH_POSITION_REDUCTION` (1). V2_MIN_SCORE raised by `VIX_HIGH_SCORE_BUMP` (1.0). Demands stronger signals, limits exposure. |
| < `VIX_LOW_THRESHOLD` (12) | Low / complacent | No adjustment. Calm market, breakout strategies work better. |
| 12-20 | Normal | No adjustment. |

### VIX Spike Protection

If VIX jumps > `VIX_SPIKE_PCT` (10%) intraday vs its day open:
- Pause new entries until VIX settles
- Existing positions protected by normal SL/trailing

### NIFTY Regime Tracking

Every `NIFTY_RECHECK_MINUTES` (15 min), re-fetch NIFTY 50 index and classify market as BULLISH / BEARISH / NEUTRAL.

- **Pre-filter impact:** Against-trend signals need |score| ≥ 3 to pass the Nifty trend hard filter
- **Regime shifts:** Morning dip → afternoon recovery is detected, updated condition feeds into subsequent re-scans
- **Claude prompt:** In `--ai` mode, NIFTY regime + VIX level are included in the prompt context

### FII/DII Flow Bias

Previous day's FII (Foreign Institutional Investors) and DII (Domestic Institutional Investors) net buy/sell data from NSE. Fetched once at startup (`FII_DII_ENABLED = True`).

| FII | DII | Bias |
|-----|-----|------|
| Buying | Buying | BULLISH |
| Selling | Selling | BEARISH |
| Mixed | Mixed | NEUTRAL |

Used as a morning bias signal. If fetch fails (NSE blocking, timeout), silently skipped — no impact on trading.

### Pre-Open Auction Data

NSE pre-open session runs 9:00-9:08 AM. After 9:08, equilibrium (opening) prices are set. Fetching quotes at ~9:08 gives gap direction, magnitude, and pre-open volume before the first candle forms.

- `PREOPEN_ENABLED = True`
- `PREOPEN_GAP_SIGNIFICANT_PCT = 1.0` — gaps > 1% with high volume flagged as significant (institutional interest)

### Thursday Expiry Adjustments

On weekly F&O expiry Thursdays, NIFTY stocks see wider swings due to options settlement:

| Adjustment | Config | Effect |
|-----------|--------|--------|
| ATR bump | `EXPIRY_ATR_BUMP = 0.3` | Added to ATR_MULTIPLIER → wider SLs |
| Min SL floor | `EXPIRY_MIN_SL_DISTANCE_PCT = 1.0` | Tighter floor raised from 0.8% → 1.0% (bigger expiry swings need more room) |
| Position reduction | `EXPIRY_POSITION_REDUCTION = 1` | MAX_POSITIONS reduced by 1. Skipped when budget < `EXPIRY_POSITION_REDUCTION_MIN_BUDGET` (Rs.1L) — small accounts keep normal slot count for rotation capacity |
| Score bump | `EXPIRY_SCORE_BUMP = 1.0` | Added to V2_MIN_SCORE → demand stronger signals |
| Stagnant timer | `EXPIRY_STAGNANT_EXTRA_MINUTES = 15` | Extends stagnant exit timer to reduce churn on fewer slots |
| Entry delay | `EXPIRY_ENTRY_DELAY_MINUTES = 30` | Wait until 9:45 AM on market-open starts (ORB candle complete, F&O settlement calmed). Late-start smart reduction floors at `EXPIRY_ENTRY_DELAY_LATE_FLOOR = 15` min |
| Trade cap | `EXPIRY_MAX_TRADES_PER_DAY = 5` | Caps total trades on expiry to prevent churn (each cycle costs ~Rs.36) |

---

## Dynamic Position Sizing

### MAX_POSITIONS Auto-Scaling

MAX_POSITIONS auto-scales with budget to keep per-position size viable:

| Budget | MAX_POSITIONS | Per-Position Size | Cost Drag |
|--------|---------------|-------------------|-----------|
| < Rs.25K | 2 | Rs.10-12K | ~0.4% |
| Rs.25-60K | 3 | Rs.8-20K | ~0.3% |
| Rs.60K-1L | 4 | Rs.15-25K | ~0.2% |
| Rs.1-3L | 5 | Rs.20-60K | ~0.2% |
| Rs.3-5L | 6 | Rs.50-83K | ~0.1% |
| > Rs.5L | 7 | Rs.70K+ | ~0.1% |

Goal: round-trip charges (Rs.40-50) stay < 0.5% of each position. Set `MAX_POSITIONS_OVERRIDE > 0` to lock manually.

### Score-Weighted Sizing (NoAI)

In NoAI mode, budget is allocated proportionally to conviction (composite score magnitude). Higher-scoring candidates get more capital, capped at `MAX_POSITION_PCT` (40%) per stock. This is a simplified Kelly criterion — bet more on higher-conviction setups.

In `--ai` mode, Claude sets qty directly (budget-validated by the engine).

### Dynamic Score Threshold (NoAI)

After day loss ≥ `LOSS_SCORE_BUMP_PCT` (1.5%) of budget, the minimum score for new trades rises by `LOSS_SCORE_BUMP_AMOUNT` (1.5 points). Only takes trades with stronger signals after losses — reduces chasing.

This only applies in NoAI mode. In `--ai` mode, Claude adjusts risk appetite via its session context.

---

## Configuration Quick Reference

### Core

| Parameter | Value | Notes |
|-----------|-------|-------|
| `MAX_BUDGET_INR` | 20,000 | Daily capital cap (overridable via `--max` CLI flag) |
| `MAX_POSITIONS` | 3 (auto-scaled) | See Dynamic Position Sizing |
| `MAX_POSITIONS_OVERRIDE` | 0 | 0 = auto; >0 = fixed |
| `MAX_POSITION_PCT` | 40% | Per-stock cap |

### SL / Target / R:R

| Parameter | Value | Notes |
|-----------|-------|-------|
| `DEFAULT_STOP_LOSS_PCT` | 1.5% | Fallback SL (ATR overrides when available) |
| `DEFAULT_TARGET_PCT` | 1.2% | Fallback target (was 1.5%, reduced after 63-trade review) |
| `ATR_MULTIPLIER` | 1.5 | SL = 1.5×ATR |
| `RR_TARGET_RATIO` | 1.5 | Base R:R from ATR |
| `MAX_INTRADAY_SL_PCT` | 2.5% | SL hard cap |
| `RR_HARD_FLOOR` | 1.3 | Always-on R:R floor (uniform all day since #243) |
| `RR_GIVEUP_AFTER_FAILS` | 5 | Zero-entry scans before stopping for the day |
| ~~`RR_FLOOR_MORNING/AFTERNOON/LATE/RELAXED`~~ | — | REMOVED by #243 — collapsed into `RR_HARD_FLOOR` |
| ~~`RR_RETRY_STEP`, `RR_RELAX_AFTER_FAILS`~~ | — | REMOVED by #243 — retry/relax branches were no-ops since #235 |
| ~~`RR_AFTERNOON_HOUR`, `RR_LATE_HOUR`~~ | — | REMOVED by #243 — only consumer was the deleted log-label selector |
| ~~`LATE_TARGET_CUT_PCT_1`~~ | ~~20%~~ | REMOVED by #242 — entry-time target compression abandoned as self-defeating |
| ~~`LATE_TARGET_CUT_PCT_2`~~ | ~~25%~~ | REMOVED by #242 — entry-time target compression abandoned as self-defeating |

### Trailing / Exit

| Parameter | Value | Notes |
|-----------|-------|-------|
| `TRAIL_AFTER_RISK_MULTIPLE` | 1.5 | Trail trigger (1.5× initial risk) |
| `TRAIL_STEP_PCT` | 50% | Trail lock % of unrealised profit |
| `TARGET_DECAY_PCT` | 25% | After 2 PM target reduction |
| `TARGET_DECAY_AFTER_HOUR` | 14 | 2:00 PM IST |
| `MIN_EXPECTED_PROFIT` | Rs.135 | Base absolute floor (3× typical round-trip charges, #237). Read via `OrderEngine.effective_min_profit()` to apply `BUDGET_MIN_PROFIT_DELTA` |
| `USE_EXCHANGE_SL` | True | SL-M on NSE |
| `USE_LIMIT_ORDERS` | True | LIMIT at LTP + 1 tick buffer for entries |
| `LIMIT_ORDER_TIMEOUT` | 8s | Wait for LIMIT fill before cancel |
| `LIMIT_MAX_RETRIES` | 2 | LIMIT attempts before MARKET fallback |
| `LOSER_EXIT_HOUR:MINUTE` | 14:45 | 2:45 PM loser exit |

### Timing / Entry

| Parameter | Value | Notes |
|-----------|-------|-------|
| `ENTRY_DELAY_MINUTES` | 5 | Post-open observation period |
| `ENTRY_MIN_MOVE_PCT` | 0.3% | Min directional move to confirm |
| `SHORT_ENTRY_CUTOFF_HOUR` | 13 | No shorts after 1 PM |
| `MIN_MINUTES_FOR_ENTRY` | 45 | Late entry guard |
| `MAX_REENTRIES_PER_STOCK` | 2 | Per stock per day |
| `MAX_SPREAD_PCT` | 0.3% | Base bid-ask spread filter. Read via `OrderEngine.effective_max_spread()` to apply `BUDGET_SPREAD_DELTA` (TINY/SMALL get 0.20%, #236) |
| `MAX_IMPACT_COST_PCT` | 0.2% | Impact-cost / depth filter (walks top-5 levels, skips thin books) |

### Scanner / Indicators

| Parameter | Value | Notes |
|-----------|-------|-------|
| `V2_MIN_SCORE` | 2.0 | Pre-filter threshold |
| `V2_CANDLE_INTERVAL` | "15minute" | Primary candle interval |
| `V2_CANDLE_RESCAN_MINUTES` | 15 | Candle re-scan frequency |
| `SUPERTREND_PERIOD` | 7 | Intraday-optimised (default 10 too slow) |
| `SUPERTREND_MULTIPLIER` | 2.0 | Tighter bands (default 3.0 too wide) |
| `SCAN_UNIVERSE` | "NIFTY100" | Stock universe (NIFTY50/100/150/200/CUSTOM, overridable via `--nifty` CLI flag) |
| `SCAN_MIN_PRICE` | Rs.100 | Skip penny stocks with wide spreads |
| `SCAN_MAX_PRICE` | 0 (auto) | 0 = budget × MAX_POSITION_PCT; skip stocks too expensive to size |

### Monitoring / Cooldowns

| Parameter | Value | Notes |
|-----------|-------|-------|
| `PRICE_POLL_SECONDS` | 10 | Quote poll interval (halved near SL/target) |
| `POSITION_REVIEW_MINUTES` | 30 | Position-review cadence — drives Claude review (`--ai`) AND NoAI stagnant-exit check |
| `OPPORTUNITY_RESCAN_MINUTES` | 30 | Re-scan for free slots |
| `NIFTY_RECHECK_MINUTES` | 15 | NIFTY regime recheck |
| `MAX_LOSS_PER_DAY_PCT` | 3% | Circuit breaker threshold |
| `CIRCUIT_BREAKER_COOLDOWN_MINUTES` | 30 | CB cooldown (0 = day over) |
| `MAX_CIRCUIT_BREAKER_TRIPS` | 2 | Max CB trips/day |
| `CONSECUTIVE_SL_PAUSE_COUNT` | 3 | SLs before whipsaw pause |
| `CONSECUTIVE_SL_PAUSE_MINUTES` | 30 | Whipsaw pause duration |
| `STAGNANT_EXIT_MINUTES` | 45 | Stagnant exit (NoAI only) |
| `STAGNANT_ADVERSE_PCT` | 0.2% | Stagnant-exit fires if trade is losing more than this |
| `STAGNANT_DEAD_FLAT_PCT` | 0.1% | Stagnant-exit fires if |move| is inside this band (truly flat) |
| `STAGNANT_HARD_MAX_ENABLED` | True | Kill-switch for the Tier-2 progress-to-target check (#172) |
| `STAGNANT_HARD_MAX_MINUTES` | 90 | Tier-2 checkpoint (catches drifters Tier-1 missed) |
| `STAGNANT_MIN_PROGRESS_PCT` | 20% | Exit at Tier-2 if progress toward target is below this |
| `CANDLE_PROTECT_MIN_CUSHION_PCT` | 0.3% | Minimum gap between tightened SL and live price (candle/regime protect) |
| `SIGNAL_REVERSAL_EXIT_ENABLED` | True | Kill-switch for hard-exit on strong opposite signal (#174) |
| `SIGNAL_REVERSAL_SCORE` | 7.0 | `\|combined_score\|` threshold (in the opposite direction) that triggers the exit |
| `SIGNAL_REVERSAL_REQUIRE_PATTERN` | True | Require a confirming bearish/bullish reversal candle alongside the score flip |
| `GAP_COHERENCE_GATE_ENABLED` | True | Kill-switch for the pre-trade gap-coherence gate (#173) |
| `GAP_COHERENCE_OVERRIDE_SCORE` | 7.5 | `\|score\|` that bypasses the gate (BUY-on-gap-down / SELL-on-gap-up) |
| `ADX_ENTRY_GATE_ENABLED` | True | Kill-switch for the ADX + DI entry gate (#157). When `STRONG_GAP_ADX_BOOST_ENABLED = True` (#194) AND today's NIFTY gap is `GAP_*_STRONG` continuing prior-day direction, the effective threshold is raised by `STRONG_GAP_ADX_DELTA` (+1) and the override score by `STRONG_GAP_OVERRIDE_DELTA` (+0.5) for **fade-side trades only** (BUY on a gap-DOWN day, SELL on a gap-UP day) for the rest of the day. Aligned trades (BUY on gap-UP, SELL on gap-DOWN) ride the institutional flow and don't get the boost |
| `ADX_MIN_THRESHOLD` | 18.0 | Minimum ADX for entry (chop filter) |
| `ADX_OVERRIDE_SCORE` | 7.0 | `|score|` threshold that overrides a weak ADX reading |
| `ATR_SIZING_ENABLED` | True | Kill-switch for ATR-based position sizing (#145) |
| `RISK_PER_TRADE_PCT` | 0.5% | Fraction of budget risked per trade when ATR sizing is active |
| `LOSS_SIZING_ENABLED` | True | Loss-adjusted sizing |
| `LOSS_SCORE_BUMP_PCT` | 1.5% | Loss threshold for score bump (NoAI) |
| `LOSS_SCORE_BUMP_AMOUNT` | 1.5 | Score increase after losses (NoAI) |
| `RE_ENTRY_COOLDOWN_ENABLED` | True | Kill-switch for per-symbol re-entry cooldown (#161) |
| `RE_ENTRY_COOLDOWN_MINUTES` | 30 | Block re-entry of same SYMBOL_SIDE within this window after any exit |
| `RE_ENTRY_SCORE_OVERRIDE` | 7.0 | `|score|` that bypasses the cooldown |
| `MIN_PROFIT_CHARGE_MULTIPLE` | 3.0 | Gross target profit must be ≥ this × round-trip charges (#162, retuned by #238 from 2.0). Set 0 to disable |
| `DAILY_LOSS_SOFT_STOP_PCT` | 1.5% | Soft-stop new entries when day loss crosses this; existing positions still managed (#163). Set 0 to disable |
| `LUNCH_LULL_ENABLED` | True | Kill-switch for lunch-lull entry skip (#164) |
| `CHOPPY_MORNING_PAUSE_ENABLED` | True | Kill-switch for choppy-morning entry pause (#192) — NIFTY ADX < 16 for 3 consecutive 09:30-10:30 scans + ≥2 recent stagnant exits → 15-min pause |
| `MTM_AWARE_CB_ENABLED` | True | Kill-switch for unrealised-MTM-aware circuit breaker / soft-stop / peak-drawdown (#166) — when on, `effective_day_pnl()` adds open-position MTM to the safety-gate math |
| `STRONG_GAP_ADX_BOOST_ENABLED` | True | Kill-switch for strong-gap ADX boost (#194) — raises effective ADX threshold + override score on continuation-strong-gap days **for fade-side trades only** (boost is direction-aware) |
| `AVG_DOWN_PREVENTION_ENABLED` | True | Kill-switch for average-down prevention (#195) — blocks same-magnitude re-entry within 120 min of a STAGNANT/SIGNAL_DECAY exit |
| `FRESH_ENTRY_RECHECK_ENABLED` | True | Kill-switch for post-observation score recheck (#196) — re-runs `_analyse_stock` after the entry-delay wait and aborts trades whose score sign-flipped or decayed below `FRESH_ENTRY_DECAY_FRACTION` |
| `FRESH_ENTRY_DECAY_FRACTION` | 0.6 | Min `|fresh| / |entry|` to allow entry after observation; e.g. +9.9 → +5.9 fires, +9.9 → +6.0 doesn't |
| `FRESH_ENTRY_RECHECK_MIN_WAIT_MINUTES` | 5 | Skip the recheck when wait was shorter than this (no new candle has closed) |
| `FRESH_ENTRY_REQUIRE_MONOTONIC` | True | Roadmap #199 — in addition to the decay-fraction floor (#196), require `\|fresh\| + tolerance ≥ \|entry\|`. Stops trades whose magnitude has dropped even within the 60% retention window |
| `FRESH_ENTRY_MONOTONIC_TOLERANCE` | 0.3 | Score points of jitter allowed before the monotonic gate trips |
| `PATTERN_CONTRADICTION_PENALTY_ENABLED` | True | Roadmap #200 — kill-switch for the scanner-side pattern↔tech contradiction penalty |
| `PATTERN_CONTRADICTION_PENALTY` | 2.0 | Magnitude subtracted from `\|combined_score\|` when patterns include an opposite-direction reversal |
| `PATTERN_INDECISION_PENALTY` | 0.5 | Magnitude subtracted from `\|combined_score\|` when patterns include `DOJI` (stacks with the contradiction penalty) |
| `VWAP_BAND_GATE_ENABLED` | True | Roadmap #201 — kill-switch for the VWAP statistical-band entry gate (17d) |
| `VWAP_BAND_OVERRIDE_SCORE` | 7.0 | `\|score\|` that bypasses the VWAP band gate (deliberately higher than the % extension override at 6.0) |
| `LATE_ENTRY_TIGHTENING_ENABLED` | True | Roadmap #202 — master kill-switch for the late-entry score-floor bump (gate 18d). After #225, this no longer governs an R:R floor or position cap; those are owned by `RR_HARD_FLOOR` and `dynamic_max_positions` respectively |
| `LATE_ENTRY_HOUR` | 10 | IST hour after which late-entry rules activate |
| `LATE_ENTRY_MIN_SCORE_BUMP` | 1.0 | Score points added to `effective_min_score()` past `LATE_ENTRY_HOUR` (gate 18d). Raised 0.5 → 1.0 by #239 after first live day showed +0.5 didn't materially change which trades passed |
| `LATE_ENTRY_NO_RESCUE_FLOOR_ENABLED` | True | #246 (2026-04-28). When True, the late-entry floor in gate 18d is clamped to `max(effective_min_score() + LATE_ENTRY_MIN_SCORE_BUMP, SIGNAL_DECAY_MIN_ENTRY_SCORE)` so the entry side never admits trades the in-trade rescue gates cannot save (the "no-rescue zone"). The threshold is REUSED from `SIGNAL_DECAY_MIN_ENTRY_SCORE` rather than introducing a new constant, so the entry/rescue floors stay coupled by code review |
| `RR_HARD_FLOOR` | 1.3 | Always-on R:R floor. `current_rr_floor()` returns `max(computed_floor, RR_HARD_FLOOR)` so adaptive relaxation, mid-day retry, and afternoon/late time-floors can never undercut it. Replaces the late-entry-only `LATE_ENTRY_RR_FLOOR` (#225 simplification) |
| `MOMENTUM_KILL_ENABLED` | True | Roadmap #198 — kill-switch for the post-entry momentum-kill exit. Runs in `check_stops_and_targets()` BEFORE the SL check |
| `MOMENTUM_KILL_GRACE_SECONDS` | 180 | Don't fire within this many seconds of entry (3-min settlement window — first 15-min candle of a trade is sacred, only hard SL fires). Raised 60 → 180 by #233 after first live day showed 60s wasn't enough to clear bid-ask spread + first-minute fade |
| `MOMENTUM_KILL_WINDOW_MINUTES` | 5 | After grace expires, the kill window stays open for this many minutes from entry. Raised 3 → 5 by #233 to keep the kill window the same width (~2 min) once grace was extended |
| `MOMENTUM_KILL_MIN_ADVERSE_PCT` | 0.40 | Roadmap #233 — adverse move (`|entry − current| / entry × 100`, only on the red side) must exceed this percentage before the kill is even considered. Set to 4× typical NSE intraday spread so the rule cannot fire on sub-spread micro-moves. On 2026-04-27 data this gate alone would have prevented all 4 morning false-positive kills |
| `MOMENTUM_KILL_MIN_PROGRESS_PCT` | 25.0 | Inside the window, exit at market with `MOMENTUM_KILL` if progress toward target is below this percentage AND `MOMENTUM_KILL_MIN_ADVERSE_PCT` has tripped AND the position is unrealised-loss. Skipped for `_external` and `_partial_taken` positions and for already-winning trades |
| `REALISED_PNL_RECOVERY_ENABLED` | True | Roadmap #203 — on init, scan Zerodha net-positions for already-closed MIS round-trips not in our session and import them as synthetic CLOSED records (`exit_reason = RECOVERED_FROM_ZERODHA`, `_external = True`, `entry_time/exit_time = None`). Side defaults to BUY (true direction is unrecoverable from net-positions; `pnl` is authoritative) |
| `LUNCH_LULL_START_HOUR` / `_MINUTE` | 11:30 | Lunch-lull window start (inclusive) |
| `LUNCH_LULL_END_HOUR` / `_MINUTE` | 12:15 | Lunch-lull window end (exclusive) |
| `LUNCH_LULL_SCORE_OVERRIDE` | 5.7 | `|score|` that bypasses the lunch skip (lowered 6.0 → 5.7 by #221 after rejection-audit showed 6.0 was net-negative) |
| `BUDGET_REGIME_ENABLED` | True | Master kill-switch for regime-adjusted gates (#165) |
| `BUDGET_TIER_SMALL` / `_NORMAL` / `_LARGE` | 30k / 1L / 5L | Regime boundaries |
| `BUDGET_ADX_THRESHOLD_DELTA` | {TINY: +2, SMALL: +1, NORMAL: 0, LARGE: -1} | Regime delta on `ADX_MIN_THRESHOLD` |
| `BUDGET_TRADE_CAP_DELTA` | {TINY: -4, SMALL: -4, NORMAL: 0, LARGE: +3} | Regime delta on `MAX_TRADES_PER_DAY` (floor at 1). SMALL tightened −2 → −4 by #240 — at Rs.50K budget the per-trade charge hurdle (~0.27%) makes 10+ trades/day unsustainable without >55% win rate |
| `BUDGET_SPREAD_DELTA` | {TINY: −0.10, SMALL: −0.10, NORMAL: 0, LARGE: 0} | Regime delta on `MAX_SPREAD_PCT` (#236). TINY/SMALL get a 0.20% effective cap so spread cannot rival the per-trade charge hurdle |
| `BUDGET_MIN_PROFIT_DELTA` | {TINY: 0, SMALL: 0, NORMAL: +65, LARGE: +265} | Regime delta on `MIN_EXPECTED_PROFIT` (#237). Preserves the 3×-round-trip-charges ratio as slot value (and therefore charges) grows: Rs.135 → Rs.200 → Rs.400 |
| `BUDGET_MIN_SCORE_DELTA` | {TINY: +1.0, SMALL: +0.5, NORMAL: 0, LARGE: 0} | Regime delta on `V2_MIN_SCORE` (stacks with LOSS bump; max wins) |

### VIX / Expiry

| Parameter | Value | Notes |
|-----------|-------|-------|
| `VIX_HIGH_THRESHOLD` | 20.0 | High VIX → reduce exposure |
| `VIX_LOW_THRESHOLD` | 12.0 | Low VIX → calm market |
| `VIX_SPIKE_PCT` | 10.0% | Intraday VIX jump → pause entries |
| `VIX_HIGH_POSITION_REDUCTION` | 1 | Reduce positions in high VIX |
| `VIX_HIGH_SCORE_BUMP` | 1.0 | Raise score threshold in high VIX |
| `EXPIRY_ATR_BUMP` | 0.3 | Wider SLs on expiry Thursdays |
| `EXPIRY_POSITION_REDUCTION` | 1 | Fewer positions on expiry |
| `EXPIRY_POSITION_REDUCTION_MIN_BUDGET` | Rs.1L | Skip position reduction when budget below this |
| `EXPIRY_SCORE_BUMP` | 1.0 | Higher score threshold on expiry |
| `EXPIRY_STAGNANT_EXTRA_MINUTES` | 15 | Extend stagnant timer on expiry days |
| `EXPIRY_ENTRY_DELAY_MINUTES` | 30 | Wait until 9:45 on expiry (ORB complete) |
| `EXPIRY_ENTRY_DELAY_LATE_FLOOR` | 15 | Late-start floor on expiry |
| `EXPIRY_MAX_TRADES_PER_DAY` | 5 | Cap trades on expiry |
| `EXPIRY_MIN_SL_DISTANCE_PCT` | 1.0% | Override MIN_SL floor on expiry |
| `FII_DII_ENABLED` | True | Fetch FII/DII flow data |
| `PREOPEN_ENABLED` | True | Fetch pre-open auction data |
| `PREOPEN_GAP_SIGNIFICANT_PCT` | 1.0% | Significant gap threshold |

### Entry Filters (every day)

| Parameter | Value | Notes |
|-----------|-------|-------|
| `MIN_SL_DISTANCE_PCT` | 0.8% | Floor — widens SL + target proportionally to preserve R:R |
| `RSI_SELL_BLOCK_THRESHOLD` | 70 | Block SELL when RSI > this |
| `RSI_BUY_BLOCK_THRESHOLD` | 75 | Block BUY when RSI > this |
| `VWAP_EXTENSION_BLOCK_PCT` | 0.8% | Block entries chasing beyond this VWAP deviation |
| `VWAP_EXT_SCORE_OVERRIDE` | 6.0 | Skip VWAP extension block when \|score\| ≥ this |
| `FRESH_REVERSAL_DELTA_THRESHOLD` | 8.0 | Skip entry when \|score_delta\| ≥ this (wait one cycle) |
| `ADOPTED_POSITION_GRACE_MINUTES` | 10 | Grace window for adopted/resumed positions (skips time-decay + loser-exit) |

---

## Database & Verification

### Tables in `data/trades.db`

| Table | Purpose | Updated By |
|-------|---------|------------|
| `trades` | Intraday trade history (Claude learning context) | `performance_tracker.py`, `verify_trades.py` |
| `portfolio_analyses` | Portfolio analysis records | `performance_tracker.py` |
| `intraday_tax_ledger` | Tax-ready ledger with charges | `fill_intraday_ledger.py`, `verify_trades.py` |
| `capital_gains_ledger` | Delivery capital gains | `import_zerodha_taxpnl.py` |

### Viewer Scripts

| Command | Purpose |
|---------|---------|
| `python scripts/view_trades.py` | Raw trade records |
| `python scripts/view_performance.py` | Performance analytics (daily P&L, win rate, exit stats, indicator correlation) |
| `python scripts/view_analyses.py` | Portfolio analysis records |
| `python scripts/view_candle_cache.py` | Candle cache data |
| `python scripts/view_intraday_ledger.py` | Tax ledger records |
| `python scripts/view_capital_gains_ledger.py` | Capital gains (STCG/LTCG) |

### Verification & Backup

| Script | Purpose |
|--------|---------|
| `python scripts/verify_trades.py` | Same-day API verification — corrects prices, recomputes charges, and syncs reports + intraday_tax_ledger + trades table |
| `python scripts/rejection_audit.py --append-report` | Post-trade rejection audit — parses today's `portfolio.log`, fetches close prices for every skipped entry, prints verdict table (`AVOIDED_LOSS` / `MISSED_PROFIT` / `NEUTRAL`), appends to trading report. Auto-invoked at EOD; CLI for back-fill: `--date YYYY-MM-DD` |
| `python scripts/import_zerodha_taxpnl.py` | Quarterly xlsx verification — imports intraday + capital gains |
| `python scripts/backup_data.py --ssh` | Two-way sync with private Git repo (row-level append-merge by default; `--prefer local\|remote` for UPSERT after manual data fixes; `--all-local\|--all-remote` for full overwrite) |

---

## Design Decisions & Rationale

| Decision | Rationale |
|----------|-----------|
| **R:R 1.5:1** | NSE large-caps move 1-1.5% net/day. 2:1 targets (3% on 1.5% SL) almost never hit intraday. |
| **SuperTrend(7, 2.0)** | Default (10, 3.0) too slow for intraday. 7/2.0 = 1.75h lookback, tighter bands, more responsive. |
| **Entry delay 5 min** | 15-min delay missed morning momentum. 5 min lets auction settle while catching early moves. |
| **Trail step 50%** | 65% too tight — NSE pullbacks of 0.5-0.7% triggered trail exits, converting 1.5% winners to 0.3%. |
| **ORB 2nd candle** | 1st candle (9:15-9:30) includes auction noise. 2nd candle (9:30-9:45) is first market-driven range. |
| **Fibonacci directional** | Near support in uptrend = bounce (+0.5). Near resistance in downtrend = rejection (-0.5). Unsigned was ambiguous. |
| **Short cutoff 1 PM** | Short delivery penalties Rs.500-5000+. 2+ hours buffer before Zerodha's 3:25 PM auto-square. |
| **Min profit Rs.135** | Round-trip charges ~Rs.40-50 on Rs.16K slots. Threshold = 3× charges ensures 2× charges of cushion for slippage (industry retail-intraday rule of thumb). Raised Rs.75 → Rs.135 by #237 after 2026-04-27 analyst pass found 2× cushion was thin once first-tick slippage was factored in. Budget-adaptive via `effective_min_profit()`. |
| **DEFAULT_TARGET 1.2%** | Was 1.5%. 26/63 trades hit SQUARE_OFF (target never reached). 1.2% more achievable for NSE intraday. |
| **NoAI as default** | Zero cost, instant execution, deterministic. Claude adds marginal value for position reviews but costs Rs.20-40/day. |
| **Stagnant exit 45 min** | Replaces Claude reviews in NoAI. Dead positions waste slots — exit them to try stronger setups. |
| **Score-weighted sizing** | Simple Kelly criterion: higher conviction → more capital. Better capital allocation than equal-weight. |
| **Fallback promotion** | Budget validation can drop expensive primary picks. Next fallback fills the freed slot — no capital left idle. |

---

## V2 Review Cycle Changes (April 2026)

Based on deep code review of 63 trades over 9 days (Rs.-585 total P&L, 48% win rate, 1.05:1 win:loss ratio).

### Shared (Both Modes)

| Change | Detail | File |
|--------|--------|------|
| **DEFAULT_TARGET_PCT 1.5→1.2%** | 26/63 trades hit SQUARE_OFF (target never reached). 1.2% more achievable. | `config.py` |
| **R:R floor (time-based + adaptive)** | Morning 1.3:1, afternoon 1.2:1, late 1.0:1. Relaxes to 1.1 after 3 zero-entry scans. Gives up after 5. Mid-day retry with floor - 0.1. | `config.py`, `order_engine.py`, `manager.py` |
| **Volume confirmation at entry** | Live mode: skip if RVol < 0.7× average. | `order_engine.py` |
| **StochRSI(14,14) indicator** | Stochastic of RSI with %K/%D crossover signals. | `technical_indicators.py`, `stock_scanner_v2.py` |
| **Sector momentum filter** | ≥3 stocks in sector agree → ±0.5 boost. | `stock_scanner_v2.py` |
| **Extended move penalty fix** | Only penalises chasing, not contrarian setups. | `technical_indicators.py` |
| **Morning/Evening Star gap check** | Star candle position validated (lower 40% / upper 60%). | `candle_patterns.py` |
| **Three Soldiers/Crows body fix** | Per Nison's definition: opens within prior body. | `candle_patterns.py` |
| **Direction filter fallback fix** | Extras in allowed directions kept as fallbacks. | `stock_scanner_v2.py` |
| **R:R mid-day retry guard** | Step-down only on mid-day rescans, not morning. | `manager.py` |
| **SL/target helper extraction** | `_compute_atr_sl_target()` + `_default_sl_target()` — DRY. | `order_engine.py` |
| **Config-derived AI prompts** | All R:R floors, compression %, SL range, trail params from config. | `stock_scanner.py`, `stock_scanner_v2.py` |
| **LOSER_EXIT rename** | From EOD_EXIT. Only exits losers at 2:45 PM, not all positions. | `config.py`, `order_engine.py`, `manager*.py` |

### AI Mode Only

| Change | Detail | File |
|--------|--------|------|
| **POSITION_REVIEW_MINUTES 20→30** | 20 min cut winners short. 30 min gives trades room. Same knob now also gates NoAI stagnant-exit cadence (renamed from CLAUDE_REVIEW_MINUTES on 2026-04-20). | `config.py` |
| **Claude scan prompt: rank/veto role** | Explicit: rank and filter, don't generate. | `stock_scanner_v2.py` |
| **StochRSI in Claude prompt** | Interpretation guide + 14-item confluence checklist. | `stock_scanner_v2.py` |
| **15-min data in review prompt** | Reviews see both 5-min granular + 15-min composite score. | `stock_scanner_v2.py` |
| **Prompt values config-derived** | All hardcoded numbers in prompts replaced with `self.cfg.X`. | `stock_scanner.py`, `stock_scanner_v2.py` |
| **V1 partial profit note fixed** | Said "50% at 1×" but code does "33% at 1.5×". | `stock_scanner.py` |
| **getattr wrappers removed** | All `getattr(self.cfg, ...)` → `self.cfg.X` for guaranteed dataclass fields. | `order_engine.py` |

---

## V1 — Deprecated

> **V1 is frozen as of 2026-04-08.** No new features, indicators, or strategy improvements.

V1 is a **Claude-first** strategy: Claude receives raw price tables (no pre-filter) and picks trades entirely on its own judgment. Risk management is rule-based (same OrderEngine as V2).

```
python main.py --mode trade --v1
```

V1 shares the same `OrderEngine` as V2/NoAI, so it passively inherits entry checks, trailing stop, circuit breaker, whipsaw guard, and manual trade sync. However, V1 is not tested against new OrderEngine changes.

**Key differences from V2:**
- No technical pre-filter — Claude sees raw prices only
- Entire universe sent to Claude (vs top 15 pre-filtered in V2)
- No candle re-scan auto-protect
- No VIX/expiry adjustments
- Fixed poll interval (no dynamic near-SL acceleration)
- Higher Claude costs (Claude does all selection + reviews)

For full V1 details, see [STRATEGY_V1.md](STRATEGY_V1.md).

---

## Known Limitations

See [STRATEGY_ROADMAP.md](STRATEGY_ROADMAP.md) for the full list. Only 3 items remain pending.

| # | Gap | Priority | Est. Impact |
|---|-----|----------|-------------|
| 24 | Backtesting framework | LOW | Enables measured optimization |
| 44 | WebSocket tick data | MEDIUM | Faster target/trail execution |
| 41 | Holiday-shifted expiry detection | LOW | ~3 days/year edge case |
