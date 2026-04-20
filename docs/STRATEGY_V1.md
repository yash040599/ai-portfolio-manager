# V1 Trading Strategy — Claude AI Intraday Bot (DEPRECATED)
<!-- Last sync: 2026-04-17 — FINAL. No V1-specific changes; document refreshed to
     reflect shared OrderEngine improvements that V1 inherits passively (new entry
     filters, adoption grace, MIN_SL floor, entry-delay semantic fix). -->

> **Status: DEPRECATED.** V1 is frozen as a strategy. No new V1-specific features
> will be added. It still runs, and it passively inherits every improvement to the
> shared `OrderEngine` that V2 gets — so bug fixes and new entry filters reach V1
> automatically, but V1-only tuning or features are not on the roadmap.
>
> Use `python main.py --mode trade --v1` to run it. Use V2 (default) or `--noai`
> for the actively developed strategies.

## Overview

### What V1 does — in plain English

V1 is the **original version** of the bot. It takes a very different approach from V2:

- Instead of computing indicator scores in code, V1 hands Claude (an AI) a raw table of live stock prices and says "you're a professional day-trader, pick 3–5 stocks to trade right now with stop-loss and target prices".
- Claude reads the prices + basic market context (NIFTY trend, recent wins/losses) and returns its picks as structured JSON.
- The bot then runs those picks through the same risk management layers V2 uses (stop-loss on the exchange, trailing stop, circuit breaker, square-off at 3:10 PM).
- Every 20 minutes the bot shows Claude the open positions' profit/loss and asks "keep holding, exit now, or adjust the stop-loss / target?"

So V1 is "AI picks the trades, code enforces the safety rules". V2 reversed this: "code picks the trades, AI is optional and only for ranking". V1 is kept around because it still works and occasionally gives useful second opinions, but every strategy decision in the newer code targets V2.

### Technical summary

V1 is a **Claude-first** intraday trading strategy. Claude AI receives a table of live stock prices from the selected universe (Nifty 50/100/200) and picks trades entirely on its own judgment. Risk management is handled by rule-based systems (ATR-based SL, trailing stops, circuit breaker).

**Run with:** `python main.py --mode trade --v1`

---

## Glossary

V1 uses **the same market, order, and risk terminology as V2**. Rather than duplicate, see the master glossary:

> **→ [STRATEGY_V2.md § Glossary — Every Term Explained](STRATEGY_V2.md#glossary--every-term-explained)**

That section covers NSE / MIS / LTP / OHLC / spread / RVol, every order type (MARKET / LIMIT / SL-M), every risk term (SL / target / R:R / trailing stop / ATR), candlesticks, all technical indicators (EMA, RSI, VWAP, MACD, SuperTrend, Bollinger, ADX, StochRSI, Fibonacci, Prev-Day S&R, Daily EMA, Score Momentum), market-wide context (NIFTY trend, VIX, FII/DII, pre-open), and position-lifecycle terms (circuit breaker, whipsaw, time decay, adopted position, stagnant exit).

### V1-specific terms

| Term | Plain-English meaning | How V1 uses it |
|------|----------------------|----------------|
| **Claude prompt** | The structured text sent to Claude describing budget, universe, prices, trend, and rules. Claude reads it and replies with trade picks. | V1 sends one scan prompt at market open, then review prompts every 20 minutes for open positions. |
| **Rationale** | The free-text reason Claude gives for each pick ("bullish engulfing on HDFCBANK near prev-day support with NIFTY strong"). | Stored alongside each trade for later review. V2 NoAI auto-generates a similar string from indicator values. |
| **Periodic review** | Every 20 minutes, V1 sends open positions back to Claude asking HOLD / EXIT / ADJUST_SL / ADJUST_TARGET. | Claude can also suggest new trades mid-day. V2 replaces this with the stagnant-exit rule (NoAI) or 30-minute reviews (AI mode). |
| **Rank/veto role** (V2 concept) | In V2 Claude's job is narrowed to ranking a pre-filtered shortlist. V1 Claude generates picks from raw prices. | V1 = Claude is the scorer; V2 = code is the scorer, Claude is the tiebreaker. |

---

## Strategy Flow

### Phase 1 — Pre-Market Scan (9:00 AM)
```
Universe (50-200 stocks)
  → Fetch live quotes from Zerodha
  → Build compact price table (symbol, price, change%, volume, OHLC)
  → Fetch NIFTY 50 trend (BULLISH / BEARISH / NEUTRAL + volatility)
  → Fetch recent trade history from SQLite (win rates, losing streaks)
  → Send everything to Claude in a structured prompt
  → Claude picks 3-5 stocks with ENTRY / SL / TARGET / QTY / RATIONALE
  → Budget validation (cap per stock, cap total)
```

### Phase 2 — Observation Period (9:15–9:20 AM)
```
Wait for market open
  → Observation window aligned to market-open + delay
      (normal days: 5 min → entry at 9:20)
      (expiry Thursdays: 30 min → entry at 9:45)
  → Only enter stocks with > 0.3% directional move from day open
  → Drop stocks that didn't confirm direction
  → Late-start floor: if script started after the window,
    use EXPIRY_ENTRY_DELAY_LATE_FLOOR (15 min expiry) or 5 min normal
```

### Phase 3 — Position Entry
```
For each confirmed stock (primary picks + all fallback candidates):
  → Fetch 14-day ATR from Zerodha historical data
  → Calculate dynamic SL: entry ± (ATR × ATR_MULTIPLIER)        [1.5]
  → Calculate dynamic target: entry ± (ATR × 1.5 × TARGET_RR)   [1.5:1 R:R]
  → Uses wider-of ATR SL vs Claude SL (structural levels respected)
  → Late-entry target reduction: 13:00+ → −20%, 14:00+ → −25%
  → R:R floor: skip trade if R:R < RR_FLOOR_LATE (1.0:1) after late-entry reduction
  → Min profit check: skip if expected profit < Rs.50 (charges would eat it)
  → Cross-check Claude's entry price vs live quote (reject if >5% off)
  → Place MARKET order on Zerodha (or simulate in DRY_RUN)
  → Use actual fill price from Zerodha (not the estimate)
  → Recalculate SL and target on real fill price
  → Place SL-M counter-order on exchange (if USE_EXCHANGE_SL = True)
  → Entry loop stops when MAX_POSITIONS slots are filled
```

### Phase 4 — Monitor Loop (9:20 AM – 3:10 PM)
```
Every 10 seconds (PRICE_POLL_SECONDS):
  → Fetch live quotes for all open positions
  → Check SL hit → auto-exit at SL price
  → Check target hit → auto-exit at target price
  → Apply trailing stop-loss (after 1.5× risk profit, partial exit 33%, trail at 50%)
  → Apply time-decay target (after 2 PM, reduce by 25%)
  → Check circuit breaker (total day loss > 3% of budget → pause 30 min, max 2 trips)

Every 15 minutes:
  → Sync with Zerodha to detect manually-opened MIS positions
  → Adopted positions get ATR-based SL/targets, managed by bot

Every 30 minutes (POSITION_REVIEW_MINUTES):
  → Show Claude: open positions with P&L, R-multiples, closed trades
  → Claude recommends: HOLD / EXIT / ADJUST_SL / ADJUST_TARGET
  → Claude can also suggest NEW trades (with budget constraints)
  → Apply Claude's recommendations

If all positions close mid-day with time remaining:
  → Re-scan with session context (day P&L, already-traded symbols)
  → Claude adjusts risk appetite based on current day performance
  → Enter new positions if high-conviction setups exist
```

### Phase 5 — Square Off & Report (3:10 PM)
```
Close all remaining open positions at market price
  → Reconcile with Zerodha's actual position data
  → Generate P&L report with full charge breakdown
  → Save to reports/ and database
```

---

## Risk Management Layers

V1 shares `OrderEngine` with V2/NoAI — **every entry check and position-management rule listed below is identical across V1, V2, and NoAI.** New guards added to OrderEngine reach V1 automatically; V1 does not need (and does not get) V1-specific versions.

| Layer | Type | Description |
|-------|------|-------------|
| ATR-based SL | Rule-based | Dynamic stop-loss: entry ± (ATR × 1.5). Uses wider-of ATR vs Claude SL. Capped at MAX_INTRADAY_SL_PCT (2.5%) |
| **MIN SL distance floor** | Rule-based | `MIN_SL_DISTANCE_PCT` = 0.8% (1.0% on expiry). High-priced stocks can produce 0.4-0.6% SLs that wick on normal noise — this floor widens SL + target proportionally to preserve R:R. *(Added Apr 16 2026 — V1 inherits automatically.)* |
| SL-M exchange orders | Rule-based | Stop-loss sits on NSE exchange. Modified on trail, partial exit, and EOD tighten |
| Trailing stop-loss | Rule-based | At 1.5× initial risk profit, exits 33% of qty and trails SL at 50% of unrealised profit |
| Time-decay target | Rule-based | After 2 PM, reduce open targets by 25% to avoid holding into close with shrinking upside |
| Late-entry reduction | Rule-based | 13:00+ → −20% target, 14:00+ → −25% target. Skip if R:R < RR_FLOOR_LATE (1.0:1) after reduction. Time-based R:R floor is the primary gate |
| Min profit check | Rule-based | Skip trade if expected profit < Rs.75 (2× round-trip charges — raised from Rs.50 Apr 16) |
| Circuit breaker | Rule-based | Day loss > 3% of budget → pause 30 min. Resumes with loss-adjusted budget. Max 2 trips/day |
| Whipsaw guard | Rule-based | 3 consecutive SL hits → pause new entries for 30 min |
| Direction diversification | Rule-based | Max N−1 in same direction. Score ≥ 5 bypasses the limit (all slots in dominant direction) |
| Entry price validation | Rule-based | Reject Claude's entry if it differs >5% from live Zerodha quote |
| Re-entry limit | Rule-based | Max 2 entries per stock per day (prevents stop-loss chasing) |
| **Declining re-entry block** | Rule-based | If re-entering a stock already traded today, block when new \|score\| < previous \|score\| (setup weakening) |
| Budget cap | Rule-based | Max 40% of budget per stock, total capped at MAX_BUDGET_INR (overridable via `--max`) |
| Observation period | Rule-based | Default 5-min delay (30 min on expiry Thursdays). Entry time aligned to market-open + delay. Only enter stocks with >0.3% confirmed move |
| **RSI contradiction filter (symmetric)** | Rule-based | Block SELL at RSI > 70, BUY at RSI > 75, BUY at RSI < 30, SELL at RSI < 25. Prevents chasing extended moves in either direction |
| **VWAP trend + extension guard** | Rule-based | Activates after 10:15 AM. Block BUY below VWAP / SELL above VWAP (trend-fight). Block BUY > +0.8% / SELL < −0.8% from VWAP (extension-chase). Override at \|score\| ≥ 6 |
| **Fresh-reversal guard** | Rule-based | If score just swung hard (\|Δ\| ≥ 8 since last scan), wait one cycle — don't trade the first bar of a violent reversal |
| **Daily trade cap** | Rule-based | Max 12 trades/day (5 on expiry). Prevents overtrading churn |
| **Stagnant churn guard** | Rule-based | Don't re-enter the same stock+direction that was exited as stagnant earlier |
| **Net-of-charges R:R check** | Rule-based | Effective R:R (after round-trip charges) must be ≥ 1.0:1 |
| Fallback candidates | Rule-based | Entry loop tries backup picks if primary candidates fail sanity checks |
| Manual trade adoption | Rule-based | Zerodha MIS positions detected every 15 min, adopted with ATR SL/targets |
| **Adoption grace window** | Rule-based | Adopted / resumed positions skip TIME_DECAY and LOSER_EXIT for the first 10 minutes — software SL, target, trailing, and square-off still apply |
| NIFTY trend filter | Claude-informed | Market classified as BULLISH/BEARISH/NEUTRAL, biases Claude's picks |
| Anti-panic exit | Claude prompt rule | Claude told "don't EXIT just because a position shows a loss — only exit if SL is hit or pattern is broken" |
| Late entry guard | Rule-based | No new positions if < MIN_MINUTES_FOR_ENTRY (45 min) until square-off |
| Short cutoff | Rule-based | No new shorts after 1 PM (short delivery penalty risk) |
| Sector concentration | Rule-based | Max 2 positions per sector (12-sector SECTOR_MAP) |
| **Thursday expiry adjustments** | Rule-based | Wider SL (ATR +0.3), higher score bar (+1.0), longer observation (30 min), min-SL floor 1.0%, trade cap 5/day, position-reduction skipped if budget < Rs.1L |
| Order API protection | Rule-based | 3 consecutive API failures → stop Claude calls, square off, shutdown |
| Loss-adjusted sizing | Rule-based | Budget for new trades shrinks by realised losses. Floor at 20% of original |
| Crash recovery | Rule-based | Resumes monitoring existing positions on restart |

---

## What Claude Sees

### Pre-Market Scan Prompt
- Date, time, budget, max positions, max per stock
- Full price table: symbol, LTP, change%, volume, day OHLC
- NIFTY 50 index trend + volatility regime
- Recent trade history (win rates, P&L, losing stocks to avoid)
- 8 strict rules (don't chase >2% moves, time-based R:R floor, etc.)
- Strategy framework (ORB, VWAP mean-reversion, sector strength)

### Periodic Review Prompt
- Each position: entry, current, P&L, R-multiple, SL, target
- Closed trades (with exit reasons)
- Day P&L total
- Minutes remaining until square-off
- Time-based rules (under 60 min → lower target, under 30 min → exit)
- Anti-panic rule + re-entry blocking

---

## Strengths
- Simple architecture — one Claude call picks trades, rule-based SL does the rest
- Claude naturally understands market context, sector rotation, news
- ATR-based SL adapts to each stock's real volatility
- Full crash recovery — resumes monitoring existing positions on restart
- Shares all V2/NoAI risk management via common OrderEngine

## Limitations
- Claude sees only raw prices — no structured technical indicators
- Entire universe sent to Claude even if most stocks have no setup
- Fixed poll interval regardless of how close positions are to exits
- No mathematical pre-filtering — Claude's fund of knowledge is the only filter
- No candle re-scan auto-protect (V2/NoAI only)

---

> **This document is frozen for V1-specific strategy.** New entry filters,
> risk guards, and bug fixes added to the shared `OrderEngine` automatically
> apply to V1 and are documented above. V1-only prompts, scoring, or review
> logic will not be changed. For the actively developed strategy, see
> [STRATEGY_V2.md](STRATEGY_V2.md).
