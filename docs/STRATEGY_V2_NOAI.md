# NoAI Trading Strategy — Fully Automated, Zero Claude Calls

## Overview

NoAI is a **Claude-free variant of V2** that uses the same candle pattern + technical indicator pipeline for everything — stock selection, monitoring, and re-scans. It replaces every Claude call with rule-based math logic.

**Run with:** `python main.py --mode trade --noai`

NoAI inherits **everything** from V2 (candle pre-filter, dynamic polling, candle re-scan auto-protect) and V1 (ATR-based SL, trailing stops, circuit breaker, crash recovery, etc.). The only difference: no Claude API calls anywhere in the pipeline.

**Cost:** ₹0 per trading day (only Zerodha data API charges apply).

---

## What's Different from V2

| Aspect | V2 | NoAI |
|--------|----|----|
| Stock selection | Pre-filter → top 15 → Claude picks | Pre-filter → auto-select top N by score |
| Trade side | Claude decides BUY/SELL | Score sign: positive = BUY, negative = SELL |
| SL / Target | Claude sets, ATR may override | Config defaults, ATR overrides in `enter_trade` |
| Position sizing | Claude sets qty, budget-validated | Auto-sized to fit budget and per-stock limits |
| Rationale | Claude writes qualitative analysis | Auto-generated from indicator values |
| Position reviews | Claude reviews every 25 min | Stagnant position exit after 90 min (rule-based) |
| Mid-day re-scan | Claude picks from new candidates | Auto-select from new candidates (same as initial scan) |
| Candle re-scan | Every 15 min, auto-protect + Claude can see patterns | Every 15 min, auto-protect only (no Claude review) |
| NIFTY re-check | Every 15 min, updates market condition for re-scans | Every 15 min, same NIFTY monitoring |
| Opportunity scan | Every 30 min, fills free slots (1 Claude call) | Every 30 min, fills free slots (0 cost — uses scan_noai) |
| Min deployment | Claude prompted to deploy ≥60% + code boost | Code boost only (same _boost_underdeployed logic) |
| Loss-adjusted sizing | Yes — reduces budget after losses | Yes — same mechanism |
| Circuit breaker cooldown | Yes — resumes after 30 min | Yes — same mechanism |
| API cost | ~₹50-100/day (Claude) | ₹0 |
| Latency | 10-30s per Claude call | Instant |

---

## Strategy Flow

### Phase 1 — Pre-Filter (identical to V2) — FREE

```
For each stock in universe (50-200 stocks):
  → Fetch 15-minute candles (last 2 days) from Zerodha Historical API
  → Fetch daily candles (last 30 days) for trend context
  → Run 14 candlestick pattern detectors on 15-min data
      • Volume confirmation: pattern strength ×1.3 if candle volume > 1.5× avg
      • Freshness decay: current candle = 1.0×, 1-ago = 0.7×, 2-ago = 0.4×
  → Compute technical indicators:
      • EMA(9/21) crossover, RSI(14), VWAP, SuperTrend(10, 3.0)
      • Daily EMA(9/21) bias, Previous day S&R
      • MACD(12,26,9) histogram — momentum confirmation/divergence
      • Opening Range Breakout (ORB) — first candle breakout signal
      • Gap analysis — pre-market gap continuation vs fill
  → Calculate composite score (~-22 to +22)
  → RVol bonus/penalty
  → Nifty trend hard filter: against-trend signals need |score| >= 3
  → Sector diversification: max 2 stocks per sector (SECTOR_MAP)
  → Filter: only stocks with |score| >= V2_MIN_SCORE (default: 2.0)
  → Rank by absolute score (strongest signals first)
```

This phase is identical to V2 — same indicators, same scoring, same filters.

### Phase 2 — Auto-Selection (replaces Claude) — FREE

```
Take top N candidates (N = MAX_POSITIONS - open_positions):
  → For each candidate:
      • Side = BUY if score > 0, SELL if score < 0
      • SL = price × (1 ± DEFAULT_STOP_LOSS_PCT)
      • Target = price × (1 ± DEFAULT_TARGET_PCT)
      • Qty = min(budget_per_slot / price, max_position_size)
      • Rationale = auto-generated from indicators:
          "Score +8.3 | RSI 28 | EMA BULLISH_CROSS | ST UP | Patterns: HAMMER"
  → Skip symbols already traded today or currently held
  → Validate total budget allocation
```

**Key difference:** V2 sends 15 candidates to Claude and lets it pick the best 5 with nuanced reasoning. NoAI simply takes the top N by score — no qualitative judgment.

### Phase 3 — Entry (same as V1/V2)

```
Observation period (ENTRY_DELAY_MINUTES from market open):
  → Wait for price direction to confirm
  → Validate: BUY only if price > day open, SELL only if price < day open
  → ATR-based SL/target override (15-min candles, capped at MAX_INTRADAY_SL_PCT)
  → Uses tighter of ATR SL vs config SL
  → Smart position sizing (reduce qty if budget insufficient)
```

ATR override, observation filter, and position sizing are all identical to V1/V2.

### Phase 4 — Monitor Loop (9:30 AM – 3:10 PM)

```
Every 10 seconds (or 5s when near SL/target):
  → SL/target check for each position
  → Trailing stop adjustment (after TRAIL_AFTER_RISK_MULTIPLE reached)
  → Time-decay target reduction (after TARGET_DECAY_AFTER_HOUR)
  → Circuit breaker check (MAX_LOSS_PER_DAY_PCT)
  → Dynamic poll: halve interval when any position within 0.5% of SL/target

Every CLAUDE_REVIEW_MINUTES (default: 25 min) — FREE in NoAI:
  → Stagnant position check: exit positions open > STAGNANT_EXIT_MINUTES (90 min)
    that haven't moved > STAGNANT_EXIT_MIN_MOVE_PCT (0.3%) toward target
  → Frees slots for stronger setups (replaces Claude's "momentum faded, exit" judgment)

Every V2_CANDLE_RESCAN_MINUTES (default: 15 min) — FREE:
  → Re-run candle pattern analysis on all open positions
  → AUTO-PROTECT: contrary signal score ±4 → tighten SL
  → No Claude review call (skipped in NoAI mode)

Every NIFTY_RECHECK_MINUTES (default: 15 min) — FREE:
  → Re-fetch NIFTY 50 and update market condition
  → Detects intraday regime shifts (e.g. morning dip → recovery)
  → Updated condition feeds into subsequent re-scans

Every OPPORTUNITY_RESCAN_MINUTES (default: 30 min) — FREE (NoAI):
  → Triggers when open_positions < MAX_POSITIONS (free slots exist)
  → Uses scan_noai() — zero Claude cost
  → Proactively fills empty slots without waiting for position closes
  → Skipped if circuit breaker active or insufficient time remains

Circuit breaker cooldown (CIRCUIT_BREAKER_COOLDOWN_MINUTES, default: 30 min):
  → After circuit breaker triggers, wait 30 min then resume with loss-adjusted budget
  → Resets P&L baseline — only NEW losses after resume can re-trip the breaker
  → Only resumes if enough time remains before square-off
  → Set to 0 for old behaviour (circuit breaker = day over)

Loss-adjusted position sizing (LOSS_SIZING_ENABLED, default: True):
  → Budget for new trades shrinks by realised losses
  → Prevents full-size re-entry after consecutive SL hits
  → Floor at 20% of original budget to keep bot active

Partial re-scan (when slots free up):
  → When a position closes via SL/target and empty slots exist
  → 2-minute cooldown between re-scans
  → Uses scan_noai() — same auto-selection as initial scan
  → Session context includes already-traded symbols and current holdings
```

**What NoAI adds vs pure rule-based:** Stagnant position exit (exits dead positions after 90 min), loss-adjusted sizing (reduces trade size after losses), and circuit breaker cooldown (resumes after 30 min instead of shutting down for the day).

### Phase 5 — Square Off & Report (same as V1/V2)

```
At SQUARE_OFF_HOUR:SQUARE_OFF_MINUTE (default 3:10 PM):
  → Square off all open positions
  → Generate P&L report with taxes, charges, net profit
  → Save trading_report_DD.txt and trading_data_DD.json
```

---

## Risk Management Layers

All V1/V2 risk management is preserved. Claude position reviews are replaced by rule-based stagnant exit.

| Layer | Source | Kept in NoAI? |
|-------|--------|---------------|
| ATR-based SL/target | Order engine (15-min candles) | Yes |
| SL cap (MAX_INTRADAY_SL_PCT) | Order engine | Yes |
| Tighter-of ATR vs config SL | Order engine | Yes |
| Trailing stop-loss | Order engine | Yes |
| Time-decay targets | Monitor loop | Yes |
| Circuit breaker (daily loss limit) | Monitor loop | Yes |
| Circuit breaker cooldown (resume after 30 min) | Monitor loop | Yes (new) |
| Loss-adjusted position sizing | Order engine | Yes (new) |
| Stagnant position exit (90 min) | Monitor loop | Yes (new — replaces Claude reviews) |
| Dynamic poll rate | V2 monitor | Yes |
| Candle re-scan auto-protect | V2 monitor (every 15 min) | Yes |
| Partial re-scan | Monitor loop (2-min cooldown) | Yes |
| Observation period filter | Entry logic | Yes |
| Direction validation (BUY=up, SELL=down) | Entry logic | Yes |
| Anti-momentum (no shorting stocks already down >2%) | Pre-filter | Yes |
| Nifty trend hard filter | Pre-filter (score ≥ 3 for against-trend) | Yes |
| Late entry guard (60 min before close) | Monitor loop | Yes |
| Max re-entry limit (2×/day per stock) | Order engine | Yes |
| Order API failure circuit breaker | Order engine | Yes |
| Partial profit taking (50% at 1×risk) | Order engine | Yes |
| Sector diversification (max 2/sector) | Pre-filter | Yes |
| Crash recovery (resume open positions) | Startup | Yes |
| **Claude position reviews** | **V2 monitor (every 25 min)** | **No (replaced by stagnant exit)** |
| **Claude re-scan stock selection** | **V1/V2 scanner** | **No** |

---

## Auto-Generated Rationale

Since Claude isn't producing qualitative analysis, NoAI builds a machine-readable rationale string from the indicator values:

```
Score +8.3 | RSI 28 | EMA BULLISH_CROSS | ST UP | MACD BULLISH/GROWING | ORB BREAKOUT_UP | Gap GAP_UP_STRONG | Patterns: HAMMER, BULLISH_ENGULFING | RVol 2.3x
```

This rationale is logged in the trade report and trading data JSON, so you can review what signals drove each trade.

---

## Configuration

NoAI uses the same config settings as V2. No additional configuration required.

| Setting | Default | Relevance to NoAI |
|---------|---------|-------------------|
| `V2_MIN_SCORE` | 2.0 | Minimum score to pass pre-filter (same as V2) |
| `V2_CANDLE_INTERVAL` | "15minute" | Candle interval for pattern detection (same as V2) |
| `V2_CANDLE_RESCAN_MINUTES` | 15 | Candle re-scan frequency — auto-protect for open positions |
| `STAGNANT_EXIT_MINUTES` | 90 | Exit positions stagnating longer than this (NoAI only) |
| `STAGNANT_EXIT_MIN_MOVE_PCT` | 0.3% | Minimum move toward target to avoid stagnant exit |
| `LOSS_SIZING_ENABLED` | True | Reduce position sizes after realised losses |
| `CIRCUIT_BREAKER_COOLDOWN_MINUTES` | 30 | Resume after circuit breaker with reduced budget |
| `DEFAULT_STOP_LOSS_PCT` | 1.5% | Used as initial SL before ATR override |
| `DEFAULT_TARGET_PCT` | 2.0% | Used as initial target before ATR override |
| `ATR_INTERVAL` | "15minute" | ATR candle interval (overrides default SL/target) |
| `MAX_INTRADAY_SL_PCT` | 2.5% | Hard cap on ATR SL width |

---

## Trade-Offs vs V2

### Advantages
- **Zero cost** — no Claude API charges (~₹50-100/day saved)
- **Faster execution** — no 10-30s wait per Claude call. Scan completes in seconds
- **Deterministic** — same inputs always produce the same trades. No Claude variability
- **Simpler failure modes** — no API timeouts, rate limits, or Claude hallucinations

### Disadvantages
- **No qualitative reasoning** — can't consider sector rotation, news catalysts, or earnings proximity
- **No position management** — relies entirely on rule-based SL/target/trailing/candle-protect. Claude sometimes spots momentum fading or suggests tightening SL before a reversal pattern fully forms
- **Mechanical selection** — takes top N by score. Sector diversification filter (max 2 per sector) prevents correlated picks, but Claude adds nuanced qualitative reasoning that math can't replicate
- **No session awareness** — doesn't adjust risk appetite based on day's P&L or recent performance. Session context is limited to skip-symbols

### When to Use NoAI vs V2
- **Use NoAI** when: testing the technical pipeline, running on days with low conviction, minimising costs, or when Claude API is down
- **Use V2** when: trading live with real capital and wanting the best risk-adjusted returns. Claude's qualitative layer adds meaningful value for position management

---

## Fallback Behaviour

- If the pre-filter finds **no candidates** above V2_MIN_SCORE, no trades are taken (no V1 fallback — there's no Claude to fall back to)
- If candle data fetch fails for a stock, that stock is skipped (non-blocking)
- All V1/V2 risk management (SL, trailing, circuit breaker, crash recovery) runs identically
- If NoAI has issues, switch to V2 (`--v2`) or V1 (no flags) for Claude-assisted trading
