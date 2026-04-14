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
  
  Last sync: 2026-04-15 — Roadmap cleanup, removed 8 items, 3 pending.
══════════════════════════════════════════════════════════════ -->

---

## Table of Contents

1. [Overview](#overview)
2. [Modes at a Glance](#modes-at-a-glance)
3. [Strategy Flow](#strategy-flow)
   - [Phase 1 — Pre-Market Scan](#phase-1--pre-market-scan-900-am--free)
   - [Phase 2 — Stock Selection](#phase-2--stock-selection)
   - [Phase 3 — Entry](#phase-3--entry)
   - [Phase 4 — Monitor Loop](#phase-4--monitor-loop-920-am--310-pm)
   - [Phase 5 — Square Off & Report](#phase-5--square-off--report)
4. [Technical Indicators (14)](#technical-indicators-14)
5. [Candlestick Patterns (14)](#candlestick-patterns-14)
6. [Risk Management — Entry Pre-Checks](#risk-management--entry-pre-checks)
7. [Risk Management — During Trade](#risk-management--during-trade)
   - [Exchange SL-M Orders](#exchange-sl-m-orders-use_exchange_sl--true)
   - [Trailing Stop-Loss](#trailing-stop-loss)
   - [Time-Decay Target Reduction](#time-decay-target-reduction)
   - [Late-Day Loser Exit](#late-day-loser-exit-245-pm)
   - [Circuit Breaker](#circuit-breaker)
   - [Whipsaw Guard](#whipsaw-guard)
   - [Loss-Adjusted Budget](#loss-adjusted-budget)
   - [Stagnant Position Exit (NoAI)](#stagnant-position-exit-noai-only)
   - [Contrary Signal Protection](#contrary-signal-protection)
8. [Market Intelligence](#market-intelligence)
   - [India VIX Adjustments](#india-vix-adjustments)
   - [VIX Spike Protection](#vix-spike-protection)
   - [NIFTY Regime Tracking](#nifty-regime-tracking)
   - [FII/DII Flow Bias](#fiidii-flow-bias)
   - [Pre-Open Auction Data](#pre-open-auction-data)
   - [Thursday Expiry Adjustments](#thursday-expiry-adjustments)
9. [Dynamic Position Sizing](#dynamic-position-sizing)
   - [MAX_POSITIONS Auto-Scaling](#max_positions-auto-scaling)
   - [Score-Weighted Sizing (NoAI)](#score-weighted-sizing-noai)
   - [Dynamic Score Threshold (NoAI)](#dynamic-score-threshold-noai)
10. [Configuration Quick Reference](#configuration-quick-reference)
11. [Database & Verification](#database--verification)
12. [Design Decisions & Rationale](#design-decisions--rationale)
13. [V2 Review Cycle Changes (April 2026)](#v2-review-cycle-changes-april-2026)
14. [V1 — Deprecated](#v1--deprecated)
15. [Known Limitations](#known-limitations)

---

## Overview

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

**Shared across both modes:** pre-filter, entry pipeline (12 checks), SL-M exchange orders, trailing stop, circuit breaker + cooldown, time-decay, late-day loser exit, direction diversification, sector guard, VIX adjustments, expiry adjustments, NIFTY regime tracking, FII/DII bias, fallback candidate promotion, manual trade adoption, crash recovery.

---

## Strategy Flow

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
- All config values derived from `config.py` (R:R floors, target compression %, SL range, trail params) — no hardcoded numbers in prompts
- Rank/veto role: Claude must rank and filter from pre-filtered candidates, not generate new ones
- Hard rejection filters (extended move >2%, RSI extremes, R:R below time-based floor, against-SuperTrend without reversal)
- Indian market awareness (NIFTY regime, F&O expiry, sector clustering)
- Common mistakes to avoid (chasing extended moves, all-same-direction)

Claude returns: ENTRY / SL / TARGET / QTY / RATIONALE per trade.

### Phase 3 — Entry

Identical in both modes. The entry loop processes candidates in score order (primary first, then fallback).

1. Wait `ENTRY_DELAY_MINUTES` (5 min) after market open
2. Confirm `ENTRY_MIN_MOVE_PCT` (0.3%) directional move from open price
3. ATR-based SL/target calculation — uses **pure ATR** when available (config defaults are fallback only). Computed via `_compute_atr_sl_target()` helper (single source of truth)
4. Pre-trade checks pass (12 checks — see [Risk Management — Entry Pre-Checks](#risk-management--entry-pre-checks))
5. **Fallback on rejection:** if a trade fails any check, the entry loop tries the next candidate from the plan. Loop stops when all position slots are filled or all candidates exhausted
6. Place entry order on Zerodha: LIMIT at LTP with `LIMIT_ORDER_TIMEOUT` (8s) wait, cancel + retry up to `LIMIT_MAX_RETRIES` (2) times, then MARKET fallback. Exits always MARKET for guaranteed fill. (DRY_RUN simulates without orders)
7. Fetch actual fill price — scale SL/target proportionally around fill
8. Place SL-M counter-order on exchange (if `USE_EXCHANGE_SL = True`)

### Phase 4 — Monitor Loop (9:20 AM – 3:10 PM)

| Interval | Action | Cost |
|----------|--------|------|
| Every 10s (5s near SL/target) | SL/target check, trailing stop, time-decay | Free |
| Every 15 min | Sync with Zerodha — detect manual MIS positions. Adopted positions get ATR-based SL/targets and full bot management | Free |
| Every 15 min | Re-run candle analysis on open positions. **Auto-protect:** contrary score ≥ ±4 → tighten SL (50% profit lock or breakeven) | Free |
| Every 15 min | NIFTY trend recheck (regime shift detection) | Free |
| Every 30 min (if free slots) | Opportunity re-scan for new trades | 1 Claude call (`--ai`) / Free (NoAI) |
| Every 30 min (`--ai` only) | Claude reviews open positions with fresh 5-min candle data + StochRSI + 15-min composite score | 1 Claude call |
| Every 30 min (NoAI only) | Stagnant position check: exit positions open > 45 min that haven't moved > 0.5% toward target | Free |

**On position close (any mode):** If free slots exist, 2-minute cooldown then partial re-scan to fill empty slots.

**Circuit breaker cooldown:** After CB trips, wait 30 min then resume with loss-adjusted budget. P&L baseline resets — only new losses after resume can re-trip. Max 2 trips/day.

### Phase 5 — Square Off & Report

- **2:45 PM (loser exit):** Exit losing positions at market. Tighten breakeven SL to entry ±0.1%. Winners with active trails keep running.
- **3:10 PM (square off):** Close all remaining positions.
- Generate `trading_data_{date}.json` + `trading_report_{date}.txt`
- Record trades to `data/trades.db` (for Claude learning context)
- Fill intraday tax ledger via `fill_intraday_ledger.py`

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

Every trade must pass these 12 checks in order. If any fails, the trade is rejected and the next fallback candidate is tried.

| # | Check | Config | Behaviour |
|---|-------|--------|-----------|
| 1 | **Price validation** | — | If Claude's price deviates >5% from Zerodha live, use live price |
| 2 | **Bid-ask spread** | `MAX_SPREAD_PCT = 0.3` | Skip if spread > 0.3% |
| 2b | **Volume confirmation** | RVol ≥ 0.7× avg | Live mode only: skip if volume too low for reliable fills |
| 3 | **ATR SL/target** | `ATR_MULTIPLIER = 1.5`, `RR_TARGET_RATIO = 1.5` | Pure ATR when available (1.5:1 R:R). Config defaults fallback only. SL capped at 2.5% |
| 3b | **R:R safety floor** | Time-based + adaptive | See [R:R Floor System](#rr-floor-system) below |
| 4 | **Late-entry reduction** | After 1 PM: −20%, 2 PM: −25% | Target compressed. R:R floor per time period ensures compressed R:R is still worth trading |
| 5 | **Min profit check** | `MIN_EXPECTED_PROFIT = Rs.50` | Skip if `\|target − entry\| × qty < Rs.50` |
| 6 | **Budget check** | `MAX_POSITION_PCT = 40%` | Auto-reduce qty to fit. If qty < 1 → skip |
| 7 | **Max positions** | Dynamic (2-5 from budget) | Includes external/manual positions |
| 8 | **Duplicate guard** | — | No two positions in same stock |
| 9 | **Sector concentration** | Max 2 per sector | 12 sectors in SECTOR_MAP |
| 10 | **Direction diversification** | Dynamic (score-aware) | Score ≥5: all slots in same dir allowed. Score <5: max `N−1` in same direction. Prevents forcing weak counter-trend trades on trending days |
| 11 | **Short cutoff** | `SHORT_ENTRY_CUTOFF_HOUR = 13` | No new shorts after 1 PM |
| 12 | **Max re-entries** | `MAX_REENTRIES_PER_STOCK = 2` | Per stock per day |

### R:R Floor System

The R:R (Risk:Reward) floor ensures every trade has adequate upside vs risk considering time of day:

| Period | R:R Floor | Config | Why |
|--------|-----------|--------|-----|
| Morning (<1 PM) | 1.3:1 | `RR_FLOOR_MORNING` | Full ATR target available, be selective |
| Afternoon (1-2 PM) | 1.2:1 | `RR_FLOOR_AFTERNOON` | 20% target compression → R:R drops to ~1.2 |
| Late (>2 PM) | 1.0:1 | `RR_FLOOR_LATE` | 25% compression → R:R ~1.1, safety net only |

**Adaptive relaxation:** After `RR_RELAX_AFTER_FAILS` (3) zero-entry scans, the floor drops to `min(time_floor, RR_FLOOR_RELAXED=1.1)`. This helps when all candidates are borderline (e.g. morning 1.25:1 fails 1.3 floor). After `RR_GIVEUP_AFTER_FAILS` (5) failures, stop trying.

**Mid-day retry:** On mid-day rescans (all-closed, slot-freed, opportunity), if first pass finds 0 entries, retry with floor reduced by `RR_RETRY_STEP` (0.1). Example: morning 1.3 → 1.2. Morning scan is excluded (it has observation period + multiple candidates + adaptive relaxation).

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

### Circuit Breaker

- Trips when day loss > 3% of budget (`MAX_LOSS_PER_DAY_PCT`)
- 30-minute cooldown (`CIRCUIT_BREAKER_COOLDOWN_MINUTES`), then baseline resets — only new losses re-trip
- Max 2 trips per day (`MAX_CIRCUIT_BREAKER_TRIPS`) → trading stops entirely
- Only resumes if enough time remains before square-off
- Set `CIRCUIT_BREAKER_COOLDOWN_MINUTES = 0` for old behaviour (circuit breaker = day over)

### Whipsaw Guard

3 consecutive SL exits (`CONSECUTIVE_SL_PAUSE_COUNT`) → pause new entries for 30 minutes (`CONSECUTIVE_SL_PAUSE_MINUTES`). Counter resets on profitable close.

### Loss-Adjusted Budget

`effective_budget = budget + day_losses` (floor at 20% of original). Prevents full-size re-entry after SL hits. Only active when `LOSS_SIZING_ENABLED = True`. In live mode, Zerodha's actual margin API handles this naturally; mainly helps dry-run mode stay realistic.

### Stagnant Position Exit (NoAI Only)

Replaces Claude's position reviews. After `STAGNANT_EXIT_MINUTES` (45 min), if a position hasn't moved ≥ `STAGNANT_EXIT_MIN_MOVE_PCT` (0.5%) toward its target, auto-exit to free the slot for a stronger setup.

In `--ai` mode, Claude reviews every 30 min instead and can recommend HOLD / EXIT / ADJUST_SL / ADJUST_TARGET with qualitative reasoning.

### Contrary Signal Protection

Every 15 min (`V2_CANDLE_RESCAN_MINUTES`), re-run candle pattern analysis on open positions. If a position's 15-min composite score flips to ±4 or stronger in the **opposite** direction:
- If in profit: tighten SL to lock 50% of unrealised gains
- If at breakeven or losing: tighten SL to breakeven (entry ± 0.1%)

This is automatic in both modes. In `--ai` mode, Claude additionally sees the patterns and can act on weaker contrary signals.

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
| Position reduction | `EXPIRY_POSITION_REDUCTION = 1` | MAX_POSITIONS reduced by 1 |
| Score bump | `EXPIRY_SCORE_BUMP = 0.5` | Added to V2_MIN_SCORE → demand stronger signals |

---

## Dynamic Position Sizing

### MAX_POSITIONS Auto-Scaling

MAX_POSITIONS auto-scales with budget to keep per-position size viable:

| Budget | MAX_POSITIONS | Per-Position Size | Cost Drag |
|--------|---------------|-------------------|-----------|
| < Rs.25K | 2 | Rs.10-12K | ~0.4% |
| Rs.25-60K | 3 | Rs.8-20K | ~0.3% |
| Rs.60K-1L | 4 | Rs.15-25K | ~0.2% |
| > Rs.1L | 5 | Rs.20K+ | ~0.2% |

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
| `RR_FLOOR_MORNING` | 1.3 | R:R floor before 1 PM |
| `RR_FLOOR_AFTERNOON` | 1.2 | R:R floor 1-2 PM |
| `RR_FLOOR_LATE` | 1.0 | R:R floor after 2 PM |
| `RR_FLOOR_RELAXED` | 1.1 | R:R floor after 3 failed scans |
| `RR_RETRY_STEP` | 0.1 | Mid-day retry step-down (0 = off) |
| `RR_RELAX_AFTER_FAILS` | 3 | Scans before relaxing |
| `RR_GIVEUP_AFTER_FAILS` | 5 | Scans before giving up |
| `LATE_TARGET_CUT_PCT_1` | 20% | Target reduction after 1 PM |
| `LATE_TARGET_CUT_PCT_2` | 25% | Target reduction after 2 PM |

### Trailing / Exit

| Parameter | Value | Notes |
|-----------|-------|-------|
| `TRAIL_AFTER_RISK_MULTIPLE` | 1.5 | Trail trigger (1.5× initial risk) |
| `TRAIL_STEP_PCT` | 50% | Trail lock % of unrealised profit |
| `TARGET_DECAY_PCT` | 25% | After 2 PM target reduction |
| `TARGET_DECAY_AFTER_HOUR` | 14 | 2:00 PM IST |
| `MIN_EXPECTED_PROFIT` | Rs.50 | Min viable profit (charges ~Rs.40-50) |
| `USE_EXCHANGE_SL` | True | SL-M on NSE |
| `USE_LIMIT_ORDERS` | True | LIMIT at LTP for entries (less slippage) |
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
| `MAX_SPREAD_PCT` | 0.3% | Bid-ask spread filter |

### Scanner / Indicators

| Parameter | Value | Notes |
|-----------|-------|-------|
| `V2_MIN_SCORE` | 2.0 | Pre-filter threshold |
| `V2_CANDLE_INTERVAL` | "15minute" | Primary candle interval |
| `V2_CANDLE_RESCAN_MINUTES` | 15 | Candle re-scan frequency |
| `SUPERTREND_PERIOD` | 7 | Intraday-optimised (default 10 too slow) |
| `SUPERTREND_MULTIPLIER` | 2.0 | Tighter bands (default 3.0 too wide) |
| `SCAN_UNIVERSE` | "NIFTY100" | Stock universe |
| `SCAN_MIN_PRICE` | Rs.100 | Skip penny stocks with wide spreads |
| `SCAN_MAX_PRICE` | 0 (auto) | 0 = budget × MAX_POSITION_PCT; skip stocks too expensive to size |

### Monitoring / Cooldowns

| Parameter | Value | Notes |
|-----------|-------|-------|
| `PRICE_POLL_SECONDS` | 10 | Quote poll interval (halved near SL/target) |
| `CLAUDE_REVIEW_MINUTES` | 30 | Claude review interval (`--ai` only) |
| `OPPORTUNITY_RESCAN_MINUTES` | 30 | Re-scan for free slots |
| `NIFTY_RECHECK_MINUTES` | 15 | NIFTY regime recheck |
| `MAX_LOSS_PER_DAY_PCT` | 3% | Circuit breaker threshold |
| `CIRCUIT_BREAKER_COOLDOWN_MINUTES` | 30 | CB cooldown (0 = day over) |
| `MAX_CIRCUIT_BREAKER_TRIPS` | 2 | Max CB trips/day |
| `CONSECUTIVE_SL_PAUSE_COUNT` | 3 | SLs before whipsaw pause |
| `CONSECUTIVE_SL_PAUSE_MINUTES` | 30 | Whipsaw pause duration |
| `STAGNANT_EXIT_MINUTES` | 45 | Stagnant exit (NoAI only) |
| `STAGNANT_EXIT_MIN_MOVE_PCT` | 0.5% | Min move to avoid stagnant exit |
| `LOSS_SIZING_ENABLED` | True | Loss-adjusted sizing |
| `LOSS_SCORE_BUMP_PCT` | 1.5% | Loss threshold for score bump (NoAI) |
| `LOSS_SCORE_BUMP_AMOUNT` | 1.5 | Score increase after losses (NoAI) |

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
| `EXPIRY_SCORE_BUMP` | 0.5 | Higher score threshold on expiry |
| `FII_DII_ENABLED` | True | Fetch FII/DII flow data |
| `PREOPEN_ENABLED` | True | Fetch pre-open auction data |
| `PREOPEN_GAP_SIGNIFICANT_PCT` | 1.0% | Significant gap threshold |

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
| `python scripts/import_zerodha_taxpnl.py` | Quarterly xlsx verification — imports intraday + capital gains |
| `python scripts/backup_data.py --ssh` | Two-way sync with private Git repo (row-level SQLite merge) |

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
| **Min profit Rs.50** | Round-trip charges ~Rs.40-50. Trades below this threshold are guaranteed losers after costs. |
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
| **CLAUDE_REVIEW_MINUTES 20→30** | 20 min cut winners short. 30 min gives trades room. | `config.py` |
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
