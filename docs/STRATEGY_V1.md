# V1 Trading Strategy — Claude AI Intraday Bot

## Overview

V1 is a **Claude-first** intraday trading strategy. Claude AI receives a table of live stock prices from the selected universe (Nifty 50/100/200) and picks trades entirely on its own judgment. Risk management is handled by rule-based systems (ATR-based SL, trailing stops, circuit breaker).

**Run with:** `python main.py --mode trade`

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

### Phase 2 — Observation Period (9:15–9:30 AM)
```
Wait for market open
  → Watch prices for ENTRY_DELAY_MINUTES (default: 15 min)
  → Only enter stocks with > 0.3% directional move from day open
  → Drop stocks that didn't confirm direction
  → Smart delay: if bot starts after 9:30, reduces delay to 5 min
```

### Phase 3 — Position Entry
```
For each confirmed stock:
  → Fetch 14-day ATR from Zerodha historical data
  → Calculate dynamic SL: entry ± (ATR × 1.5)
  → Calculate dynamic target: entry ± (ATR × 3.0) — 2:1 R:R
  → Cross-check Claude's entry price vs live quote (reject if >5% off)
  → Place MARKET order on Zerodha (or simulate in DRY_RUN)
  → Use actual fill price from Zerodha (not the estimate)
  → Recalculate SL and target on real fill price
```

### Phase 4 — Monitor Loop (9:30 AM – 3:10 PM)
```
Every 10 seconds (PRICE_POLL_SECONDS):
  → Fetch live quotes for all open positions
  → Check SL hit → auto-exit at SL price
  → Check target hit → auto-exit at target price
  → Apply trailing stop-loss (after 1× risk profit)
  → Apply time-decay target (after 2 PM, reduce by 40%)
  → Check circuit breaker (total day loss > 3% of budget → stop all)

Every 25 minutes (CLAUDE_REVIEW_MINUTES):
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

| Layer | Type | Description |
|-------|------|-------------|
| ATR-based SL | Rule-based | Dynamic stop-loss based on 14-day volatility (tighter for calm stocks, wider for volatile ones) |
| Trailing stop-loss | Rule-based | After profit reaches 1× initial risk, SL moves to breakeven. Then locks 50% of unrealised profit |
| Time-decay target | Rule-based | After 2 PM, reduce open targets by 40% to avoid holding into close with shrinking upside |
| Circuit breaker | Rule-based | Day loss > 3% of budget → exit ALL positions, stop trading |
| Entry price validation | Rule-based | Reject Claude's entry if it differs >5% from live Zerodha quote (prevents hallucinated prices) |
| Re-entry limit | Rule-based | Max 2 entries per stock per day (prevents stop-loss chasing) |
| Budget cap | Rule-based | Max 40% of budget per stock, total capped at MAX_BUDGET_INR |
| Observation period | Rule-based | 15-min delay after open, only enter stocks with >0.3% confirmed move |
| NIFTY trend filter | Claude-informed | Market classified as BULLISH/BEARISH/NEUTRAL, biases Claude's picks |
| Anti-panic exit | Claude prompt rule | Claude told "don't EXIT just because a position shows a loss — only exit if SL is hit or pattern is broken" |
| Late entry guard | Rule-based | No new positions if <60 minutes until square-off |
| Order API protection | Rule-based | 3 consecutive API failures → stop Claude calls, square off, shutdown |

---

## What Claude Sees

### Pre-Market Scan Prompt
- Date, time, budget, max positions, max per stock
- Full price table: symbol, LTP, change%, volume, day OHLC
- NIFTY 50 index trend + volatility regime
- Recent trade history (win rates, P&L, losing stocks to avoid)
- 8 strict rules (don't chase >2% moves, min 1:1.5 R:R, etc.)
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

## Limitations
- Claude sees only raw prices — no structured technical indicators
- Entire universe sent to Claude even if most stocks have no setup
- Fixed poll interval regardless of how close positions are to exits
- No mathematical pre-filtering — Claude's fund of knowledge is the only filter
