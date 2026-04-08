# V2 Trading Strategy — Complete Reference
<!-- ══════════════════════════════════════════════════════════════
  MAINTENANCE NOTE — Keep this document in sync with code changes.
  
  This is the single source of truth for the V2 (and V2 NoAI) intraday
  trading strategy. Anyone reviewing this document should be able to:
    1. Understand every decision the bot makes and why
    2. Identify gaps, risks, or improvements in the strategy
    3. Verify that code behaviour matches this spec
  
  When updating code that affects strategy (config, indicators, order
  engine, scanner), update this document in the same commit.
  
  Last sync: 2026-04-08 — R:R 1.5:1, SL-M exchange orders, dynamic
  MAX_POSITIONS, pre-trade profit check, SuperTrend 7/2.0, Fibonacci
  directional, ORB 2nd candle, short cutoff, trail step 50%, smart
  direction diversification (score-aware), --max budget CLI flag,
  periodic manual trade detection, entry count logging fix, fallback
  candidate pool (entry loop tries backup picks if primary fails
  sanity checks).
══════════════════════════════════════════════════════════════ -->

## Overview

V2 is an **intraday equity trading bot** for NSE (India) via Zerodha Kite Connect. It combines a free mathematical pre-filter (candlestick patterns + 14 technical indicators) with automatic stock selection by score. Optionally, Claude AI can be enabled for selection and reviews via `--ai`.

**Default:** `python main.py --mode trade` (NoAI — pure technical signals, zero Claude calls)
**With AI:** `python main.py --mode trade --ai` (Claude selects from pre-filtered candidates)

V2 inherits all risk management from V1 (ATR-based SL, trailing stops, circuit breaker, crash recovery). V1 is retired — use `--v1` only for testing.

---

## V2 NoAI (Default) vs V2 + Claude

| Aspect | V2 NoAI (Default) | V2 + Claude (`--ai`) |
|--------|-------------|---------|
| Stock selection | Auto-picks by score sign + magnitude | Claude picks from top 15 pre-filtered |
| Entry logic | Default SL/target from config + ATR overrides | Claude sets SL/target/rationale |
| Position review | Stagnant exit after 90 min | Claude reviews every 20 min |
| Score threshold raise | Yes — after day losses, V2_MIN_SCORE rises | No |
| Claude API cost | ₹0 | ~₹20-40/day (5-15 calls) |
| Mid-day re-scan | Yes (every 30 min, no Claude call) | Yes (every 30 min, 1 Claude call) |

Both modes share: pre-filter, risk management, SL-M orders, trailing stop, circuit breaker, time-decay, EOD exit, direction diversification.

---

## Strategy Flow

### Phase 1 — Pre-Market Scan (9:00 AM) — FREE

```
For each stock in NIFTY100 (~100 stocks):
  → Fetch 15-min candles (last 3 days) from Zerodha Historical API
  → Fetch daily candles (last 30 days) for trend context
  → Detect 14 candlestick patterns on 15-min data
      • Volume confirmation: strength ×1.3 if candle vol > 1.5× avg
      • Freshness decay: current = 1.0×, 1-ago = 0.7×, 2-ago = 0.4×
  → Compute 14 technical indicators → composite score (-24 to +24)
  → Add RVol bonus/penalty (-1 to +1)
  → Apply Nifty trend hard filter (against-trend needs |score| ≥ 3)
  → Sector diversification: max 2 per sector (12 sectors in SECTOR_MAP)
  → Filter: |score| ≥ V2_MIN_SCORE (default 2.0)
  → Take top 15 by |score|
```

Cost: ₹0 — pure computation on free Zerodha historical data.

### Phase 2 — Stock Selection — PAID (V2) / FREE (NoAI)

**V2 (Claude):** Sends enriched snapshot per candidate — price, RSI, EMA signal, VWAP, SuperTrend direction, detected patterns, prev-day S&R, RVol, composite score. The prompt includes:
- Time-phase context (Opening / Morning Trend / Midday Lull / Afternoon / Late Session)
- 14-indicator confluence checklist (SuperTrend, EMA, RSI, pattern, VWAP, VWAP Bands, MACD, ORB, Gap, RVol, Hourly EMA, BB Squeeze, ADX, Fib, Prev-Day S&R, Daily EMA Bias)
- Hard rejection filters (extended move >2%, RSI extremes, R:R <1:1.5, against-SuperTrend without reversal)
- Indian market awareness (NIFTY regime, F&O expiry, sector clustering)
- Common mistakes to avoid (chasing extended moves, all-same-direction)

Claude returns: ENTRY / SL / TARGET / QTY / RATIONALE per trade.

**V2 NoAI:** Auto-generates trades from score sign. Budget allocated: `min(budget/max_trades, budget × MAX_POSITION_PCT/100)`. If day loss ≥ 1.5% of budget, MIN_SCORE rises by 1.5 points. Returns primary picks (top N) **plus up to 5 fallback candidates** — if a primary pick fails entry sanity checks (R:R too low after late-entry reduction, min profit, etc.), the entry loop tries the next candidate automatically.

### Phase 3 — Entry

1. Wait `ENTRY_DELAY_MINUTES` (5 min) after market open
2. Confirm `ENTRY_MIN_MOVE_PCT` (0.3%) directional move from open price
3. ATR-based SL/target calculation — uses **wider-of** ATR SL vs Claude SL
4. Pre-trade checks pass (12 checks — see Risk Management section)
5. **Fallback on rejection:** if a trade fails any check, the entry loop tries the next candidate from the plan (fallback candidates included). Loop stops when all position slots are filled or all candidates exhausted
6. Place entry order on Zerodha
7. Fetch actual fill price — scale SL/target proportionally around fill
8. Place SL-M counter-order on exchange (if `USE_EXCHANGE_SL = True`)

### Phase 4 — Monitor Loop (9:20 AM – 3:10 PM)

| Interval | Action | Cost |
|----------|--------|------|
| Every 10s (5s near SL/target) | SL/target check, trailing stop, time-decay | Free |
| Every 15 min | Sync with Zerodha to detect manual MIS positions. Adopted positions get ATR-based SL/targets and full bot management | Free |
| Every 15 min | Re-run candle analysis on open positions. **Auto-protect:** contrary score ≥ ±4 → tighten SL (50% profit lock or breakeven) | Free |
| Every 15 min | Nifty trend recheck (regime shift detection) | Free |
| Every 30 min (if free slots) | Opportunity re-scan for new trades | 1 Claude call (V2) / Free (NoAI) |
| Every 20 min (V2 only) | Claude reviews open positions with fresh 5-min candle data + pattern analysis | 1 Claude call |

### Phase 5 — Square Off & Report

- **2:45 PM (EOD exit):** Exit losing positions at market. Tighten breakeven SL to entry ±0.1%.
- **3:10 PM (Square off):** Close all remaining positions.
- Generate `trading_data_{date}.json` + `trading_report_{date}.txt`
- Record trades to `data/trades.db` (for Claude learning context)
- Fill intraday tax ledger

---

## Risk Management — Entry Pre-Checks

Every trade must pass these checks in order. If any fails, the trade is rejected:

| # | Check | Config | Behaviour |
|---|-------|--------|-----------|
| 1 | **Price validation** | — | If Claude's price deviates >5% from Zerodha live, use live price |
| 2 | **Bid-ask spread** | `MAX_SPREAD_PCT = 0.3` | Skip if spread > 0.3% |
| 3 | **ATR SL/target** | `ATR_MULTIPLIER = 1.5`, `TARGET_RR_MULTIPLIER = 1.5` | SL = wider-of(ATR, Claude). Target uses 1.5:1 R:R. SL capped at 2.5% |
| 4 | **Late-entry reduction** | After 1 PM: −20%, 2 PM: −35% | If R:R drops below 1.2:1 → skip |
| 5 | **Min profit check** | `MIN_EXPECTED_PROFIT = ₹50` | Skip if `|target − entry| × qty < ₹50` |
| 6 | **Budget check** | `MAX_POSITION_PCT = 40%` | Auto-reduce qty to fit. If qty < 1 → skip |
| 7 | **Max positions** | Dynamic (2-5 from budget) | Includes external/manual positions |
| 8 | **Duplicate guard** | — | No two positions in same stock |
| 9 | **Sector concentration** | Max 2 per sector | 12 sectors |
| 10 | **Direction diversification** | Dynamic (score-aware) | Score ≥5: all slots in same dir allowed. Score <5: max `N−1` in same direction. Prevents forcing weak counter-trend trades on trending days |
| 11 | **Short cutoff** | `SHORT_ENTRY_CUTOFF_HOUR = 13` | No new shorts after 1 PM |
| 12 | **Max re-entries** | `MAX_REENTRIES_PER_STOCK = 2` | Per stock per day |

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
| **EOD tighten / Claude ADJUST_SL** | Modify exchange SL-M trigger in sync |

Only active when `USE_EXCHANGE_SL=True` AND `DRY_RUN=False`.

### Trailing Stop-Loss

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `TRAIL_AFTER_RISK_MULTIPLE` | 1.5 | Trail starts at 1.5× initial risk profit |
| `TRAIL_STEP_PCT` | 50% | SL locks 50% of unrealised profit |

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

### EOD Accelerated Exit (2:45 PM)

| Position State | Action |
|---------------|--------|
| Losing | Auto-exit at market |
| Near breakeven | Tighten SL to entry ±0.1% |
| Winning | Trail stop handles it |

### Circuit Breaker

- Trips when day loss > 3% of budget (`MAX_LOSS_PER_DAY_PCT`)
- 30-minute cooldown, then baseline resets (only new losses re-trip)
- Max 2 trips per day → trading stops entirely

### Whipsaw Guard

3 consecutive SL exits → pause new entries for 30 minutes. Counter resets on profitable close.

### Loss-Adjusted Budget (Dry Run)

`effective_budget = budget + day_losses` (floor at 20% of original). Prevents full-size re-entry after SL hits. In live mode, Zerodha's actual margin handles this.

---

## Dynamic MAX_POSITIONS

MAX_POSITIONS auto-scales with budget to keep per-position size viable:

| Budget | MAX_POSITIONS | Per-Position Size | Cost Drag |
|--------|---------------|-------------------|-----------|
| < ₹25K | 2 | ₹10-12K | ~0.4% |
| ₹25-60K | 3 | ₹8-20K | ~0.3% |
| ₹60K-1L | 4 | ₹15-25K | ~0.2% |
| > ₹1L | 5 | ₹20K+ | ~0.2% |

Goal: round-trip charges (₹40-50) stay < 0.5% of each position. Set `MAX_POSITIONS_OVERRIDE > 0` to lock manually.

---

## Technical Indicators (14)

All indicators on 15-min candles. Total score range: **-24 to +24**.

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
| **Extended Move Penalty** | ±1.5 to ±3 | >2% from open → -3 penalty opposing direction. Prevents chasing. |
| **RSI Extreme Hard Cap** | caps at ±3 | RSI ≥ 75 → max +3. RSI ≤ 25 → min -3. |

### Score Interpretation

| |Score| | Signal | Action |
|---------|--------|--------|
| ≥ 5 | STRONG | High conviction trade |
| 2-5 | MODERATE | Passes filter — Claude decides |
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
| Morning Star | BULLISH | 3 | 3-candle reversal: big bear → small body → big bull. |
| Evening Star | BEARISH | 3 | 3-candle reversal: big bull → small body → big bear. |
| Three White Soldiers | BULLISH | 3 | 3 consecutive up candles, each higher. Strong continuation. |
| Three Black Crows | BEARISH | 3 | 3 consecutive down candles, each lower. |
| Bullish Harami | BULLISH | 1 | Small bull inside prior large bear. Weak — needs confirmation. |
| Bearish Harami | BEARISH | 1 | Small bear inside prior large bull. |

All patterns: volume-confirmed (×1.3 high vol, ×0.5 low) and freshness-decayed (1.0× current, 0.7× 1-ago, 0.4× 2-ago).

---

## Configuration Quick Reference

### Core (config.py)

| Parameter | Value | Notes |
|-----------|-------|-------|
| `MAX_BUDGET_INR` | 20,000 | Daily capital cap (overridable via `--max` CLI flag) |
| `MAX_POSITIONS` | 3 (auto-scaled) | See Dynamic MAX_POSITIONS |
| `MAX_POSITIONS_OVERRIDE` | 0 | 0 = auto; >0 = fixed |
| `MAX_POSITION_PCT` | 40% | Per-stock cap |
| `DEFAULT_STOP_LOSS_PCT` | 1.5% | Fallback SL |
| `DEFAULT_TARGET_PCT` | 1.5% | Fallback target |
| `ATR_MULTIPLIER` | 1.5 | SL = 1.5×ATR |
| `TARGET_RR_MULTIPLIER` | 1.5 | 1.5:1 R:R |
| `MAX_INTRADAY_SL_PCT` | 2.5% | SL hard cap |
| `TRAIL_AFTER_RISK_MULTIPLE` | 1.5 | Trail trigger |
| `TRAIL_STEP_PCT` | 50% | Trail lock % |
| `MIN_EXPECTED_PROFIT` | ₹50 | Min viable profit |
| `USE_EXCHANGE_SL` | True | SL-M on NSE |
| `ENTRY_DELAY_MINUTES` | 5 | Post-open observe |
| `SHORT_ENTRY_CUTOFF_HOUR` | 13 | No shorts after 1 PM |
| `SUPERTREND_PERIOD` | 7 | Intraday-optimised |
| `SUPERTREND_MULTIPLIER` | 2.0 | Tighter bands |
| `V2_MIN_SCORE` | 2.0 | Pre-filter threshold |
| `TARGET_DECAY_PCT` | 25% | After 2 PM |
| `MAX_LOSS_PER_DAY_PCT` | 3% | Circuit breaker |

---

## Database & Verification

### Tables in `data/trades.db`

| Table | Purpose | Updated By |
|-------|---------|------------|
| `trades` | Intraday trade history (Claude learning) | `performance_tracker.py`, `verify_trades.py` |
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
| `python scripts/verify_trades.py` | Same-day API verification — corrects prices in reports + intraday_tax_ledger + trades table |
| `python scripts/import_zerodha_taxpnl.py` | Quarterly xlsx verification — imports intraday + capital gains |
| `python scripts/backup_data.py --ssh` | Two-way sync with private Git repo (row-level SQLite merge) |

---

## Why These Parameters

| Decision | Rationale |
|----------|-----------|
| **R:R 1.5:1** | NSE large-caps move 1-1.5% net/day. 2:1 targets (3% on 1.5% SL) almost never hit intraday. |
| **SuperTrend(7, 2.0)** | Default (10, 3.0) too slow for intraday. 7/2.0 = 1.75h lookback, tighter bands, more responsive. |
| **Entry delay 5 min** | 15-min delay missed morning momentum. 5 min lets auction settle while catching early moves. |
| **Trail step 50%** | 65% too tight — NSE pullbacks of 0.5-0.7% triggered trail exits, converting 1.5% winners to 0.3%. |
| **ORB 2nd candle** | 1st candle (9:15-9:30) includes auction noise. 2nd candle (9:30-9:45) is first market-driven range. |
| **Fibonacci directional** | Near support in uptrend = bounce (+0.5). Near resistance in downtrend = rejection (-0.5). Unsigned was ambiguous. |
| **Short cutoff 1 PM** | Short delivery penalties ₹500-5000+. 2+ hours buffer before Zerodha's 3:25 PM auto-square. |
| **Min profit ₹50** | Round-trip charges ~₹40-50. Trades below this threshold are guaranteed losers after costs. |

---

## Known Limitations

See [STRATEGY_ROADMAP.md](STRATEGY_ROADMAP.md) for full list. All remaining items are LOW or MEDIUM priority.

| # | Gap | Priority | Est. Impact |
|---|-----|----------|-------------|
| 55 | MARKET → LIMIT orders | MEDIUM | ₹20-40/day slippage |
| 24 | Backtesting framework | MEDIUM | Enables measured optimization (V3 infra) |
| 44 | WebSocket tick data | MEDIUM | Faster SL/target execution |
| 40 | Claude prompt feedback loop | LOW | Only applies to `--ai` mode |
| 56 | Stock price range filter | MEDIUM | Filters out poor-spread stocks |
| 57 | VWAP incomplete candle | LOW | Slight VWAP skew |
| 41 | Holiday-shifted expiry detection | LOW | ~2-3 days/year edge case |
