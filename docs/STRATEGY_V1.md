# V1 Trading Strategy — Claude AI Intraday Bot (DEPRECATED)
<!-- Last sync: 2026-04-08 — FINAL. No further changes will be made to V1. -->

> **Status: DEPRECATED.** V1 is frozen as of 2026-04-08. It will continue to work
> with the current codebase but will **not receive any new features, indicators,
> or strategy improvements**. All future development targets V2 and V2 NoAI.
>
> Use `python main.py --mode trade --v1` to run it. Use V2 (default) or
> `--noai` for the actively maintained strategies.
>
> V1 shares the same `OrderEngine` as V2/NoAI, so it inherits all entry-time
> safety checks, trailing stop, circuit breaker, whipsaw guard, manual trade
> sync, and fallback candidate support. These shared components may evolve
> with V2 development — V1 benefits passively but is not tested against
> new OrderEngine changes.

## Overview

V1 is a **Claude-first** intraday trading strategy. Claude AI receives a table of live stock prices from the selected universe (Nifty 50/100/200) and picks trades entirely on its own judgment. Risk management is handled by rule-based systems (ATR-based SL, trailing stops, circuit breaker).

**Run with:** `python main.py --mode trade --v1`

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
  → Watch prices for ENTRY_DELAY_MINUTES (default: 5 min)
  → Only enter stocks with > 0.3% directional move from day open
  → Drop stocks that didn't confirm direction
  → Smart delay: if bot starts after 9:30, reduces delay to 5 min
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

Every 20 minutes (CLAUDE_REVIEW_MINUTES):
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

V1 shares `OrderEngine` with V2/NoAI — all entry checks and position management are identical.

| Layer | Type | Description |
|-------|------|-------------|
| ATR-based SL | Rule-based | Dynamic stop-loss: entry ± (ATR × 1.5). Uses wider-of ATR vs Claude SL. Capped at MAX_INTRADAY_SL_PCT (2.5%) |
| SL-M exchange orders | Rule-based | Stop-loss sits on NSE exchange. Modified on trail, partial exit, and EOD tighten |
| Trailing stop-loss | Rule-based | At 1.5× initial risk profit, exits 33% of qty and trails SL at 50% of unrealised profit |
| Time-decay target | Rule-based | After 2 PM, reduce open targets by 25% to avoid holding into close with shrinking upside |
| Late-entry reduction | Rule-based | 13:00+ → −20% target, 14:00+ → −25% target. Skip if R:R < RR_FLOOR_LATE (1.0:1) after reduction. Time-based R:R floor is the primary gate |
| Min profit check | Rule-based | Skip trade if expected profit < Rs.50 (charges would eat it) |
| Circuit breaker | Rule-based | Day loss > 3% of budget → pause 30 min. Resumes with loss-adjusted budget. Max 2 trips/day |
| Whipsaw guard | Rule-based | 3 consecutive SL hits → pause new entries for 30 min |
| Direction diversification | Rule-based | Max N−1 in same direction. Score ≥ 5 bypasses the limit (all slots in dominant direction) |
| Entry price validation | Rule-based | Reject Claude's entry if it differs >5% from live Zerodha quote |
| Re-entry limit | Rule-based | Max 2 entries per stock per day (prevents stop-loss chasing) |
| Budget cap | Rule-based | Max 40% of budget per stock, total capped at MAX_BUDGET_INR (overridable via `--max`) |
| Observation period | Rule-based | 5-min delay after open, only enter stocks with >0.3% confirmed move |
| Fallback candidates | Rule-based | Entry loop tries backup picks if primary candidates fail sanity checks |
| Manual trade adoption | Rule-based | Zerodha MIS positions detected every 15 min, adopted with ATR SL/targets |
| NIFTY trend filter | Claude-informed | Market classified as BULLISH/BEARISH/NEUTRAL, biases Claude's picks |
| Anti-panic exit | Claude prompt rule | Claude told "don't EXIT just because a position shows a loss — only exit if SL is hit or pattern is broken" |
| Late entry guard | Rule-based | No new positions if < MIN_MINUTES_FOR_ENTRY (60 min) until square-off |
| Short cutoff | Rule-based | No new shorts after 1 PM (short delivery penalty risk) |
| Sector concentration | Rule-based | Max 2 positions per sector (12-sector SECTOR_MAP) |
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

> **This document is frozen.** Do not add new sections or update parameter
> values for V1-specific changes. V1 inherits shared OrderEngine changes
> passively. For the actively maintained strategy, see
> [STRATEGY_V2.md](STRATEGY_V2.md) or [STRATEGY_V2_NOAI.md](STRATEGY_V2_NOAI.md).
