# V2 Trading Strategy — Candle Pattern + Technical Indicator Pre-Filter

## Overview

V2 adds a **mathematical pre-filtering layer** before Claude. Instead of sending 100 raw stock prices to Claude, V2 first runs candlestick pattern detection and technical indicator analysis on every stock (for free, using Zerodha's historical candle API). Only the top 15 stocks with the strongest technical setups are sent to Claude — along with their exact RSI, EMA, VWAP, SuperTrend, and detected candle patterns.

**Run with:** `python main.py --mode trade --v2`

V2 inherits **everything** from V1 (ATR-based SL, trailing stops, circuit breaker, crash recovery, etc.). The only differences are in stock selection and position monitoring.

---

## What's Different from V1

| Aspect | V1 | V2 |
|--------|----|----|
| Pre-market scan | Send all 100 stock prices to Claude | Math-filter → send top 15 with indicators to Claude |
| Claude context | Raw price table | RSI, EMA(9/21), VWAP, SuperTrend, candle patterns per stock |
| Claude API cost | Larger prompt (all stocks) | Smaller prompt (fewer stocks, more data per stock) |
| Poll interval | Fixed (10s) | Dynamic — halves to 5s when position is within 0.5% of SL/target |
| Position review | Price + P&L only | Fresh 5-min candle patterns + RSI + EMA + VWAP per position |
| Mid-day re-scan | Uses V1 scanner | Uses V2 candle-aware scanner |

---

## Strategy Flow

### Phase 1 — Pre-Market Candle Analysis (9:00 AM) — FREE

```
For each stock in universe (50-200 stocks):
  → Fetch 15-minute candles (last 2 days) from Zerodha Historical API
  → Fetch daily candles (last 30 days) for trend context
  → Run 14 candlestick pattern detectors on 15-min data
  → Compute technical indicators:
      • EMA(9) vs EMA(21) crossover — momentum direction
      • RSI(14) — overbought/oversold detection
      • VWAP — institutional fair value (today's candles only)
      • SuperTrend(10, 3.0) — ATR-based trend-following
      • Daily EMA(9/21) — higher timeframe trend bias
  → Calculate composite score (-10 to +10)
  → Filter: only stocks with |score| >= V2_MIN_SCORE (default: 2.0)
  → Rank by absolute score (strongest signals first)
  → Take top 15 candidates
```

This phase costs ₹0 — it's pure math on historical candle data from Zerodha.

### Phase 2 — Claude Selection from Pre-Filtered Set — PAID

```
Build enriched snapshot for each of the 15 candidates:
  → Symbol, price, change%, volume
  → VWAP value
  → RSI value (e.g., "RSI: 28" → oversold)
  → EMA(9/21) signal (BULLISH_CROSS / BEARISH_CROSS / NONE)
  → SuperTrend direction (UP / DOWN)
  → Detected candle patterns ([BULLISH_ENGULFING, HAMMER])
  → Composite score (+5.2)
  → Send to Claude with indicator guide

Claude picks trades with ENTRY / SL / TARGET / QTY / RATIONALE
  → Claude can reference specific indicators: "RSI(28) oversold + 
    HAMMER pattern + SuperTrend UP = strong BUY confluence"
```

### Phase 3 — Entry (same as V1)

Observation period, ATR-based SL/target, price validation, fill tracking — all from V1.

### Phase 4 — V2 Monitor Loop (9:30 AM – 3:10 PM)

```
Every 10 seconds (or 5 seconds when near SL/target):
  → Same as V1: SL/target check, trailing stop, time-decay, circuit breaker
  → NEW: Check if any position is within 0.5% of SL or target
    → If yes: double the poll frequency (10s → 5s) for faster reaction

Every V2_CANDLE_RESCAN_MINUTES (default: 15 min) — FREE:
  → Re-run candle pattern analysis on all open positions
  → Log positions with |score| >= 5 (strong signal forming)
  → This is early warning before Claude review (no API cost)

Every 25 minutes — PAID:
  → Fetch fresh 5-MINUTE candles for each open position
  → Run pattern detection + RSI + EMA + VWAP on fresh data
  → Claude review now sees:
    "RELIANCE: BUY 50 @ ₹2,400  Current: ₹2,420  P&L: ₹1,000 (+0.8R)
     5min patterns: [SHOOTING_STAR]  RSI(14): 72  EMA(9/21): BEARISH_CROSS
     VWAP: ₹2,410"
  → This tells Claude: "Hey, momentum is fading on this position —
    consider tightening SL or taking profits"
```

### Phase 5 — Square Off & Report (same as V1)

---

## Technical Indicators Explained

### EMA(9/21) Crossover
- **What:** 9-period EMA crosses above 21-period EMA = bullish momentum shift
- **On 15-min candles:** 9 × 15 = 2.25 hour fast, 21 × 15 = 5.25 hour slow
- **Why it works:** Captures short-term momentum changes within the trading day
- **Score contribution:** Crossover = ±2, trending spread = ±1

### RSI(14) — Relative Strength Index
- **What:** Measures speed and magnitude of price changes (0–100)
- **On 15-min candles:** 14 × 15 = 3.5 hour lookback
- **Signal:** RSI < 30 = oversold (potential bounce), RSI > 70 = overbought (potential drop)
- **Why it works:** Mean-reversion tendency in liquid large-caps. Extreme RSI on intraday = high probability reversal zone
- **Score contribution:** RSI 20-30 = +2, RSI < 20 = +3, RSI 70-80 = -2, RSI > 80 = -3

### VWAP — Volume Weighted Average Price
- **What:** Average price weighted by volume — represents institutional "fair value" for the day
- **Calculation:** Σ(typical_price × volume) / Σ(volume), where typical = (H+L+C)/3
- **Signal:** Price above VWAP = buyers in control, below = sellers in control
- **Why it works:** Large funds and algorithms execute relative to VWAP. A stock consistently above VWAP has sustained buying interest
- **Score contribution:** ±1 (confirmation signal, not primary)

### SuperTrend(10, 3.0)
- **What:** ATR-based trend-following indicator that plots a single support/resistance line
- **Parameters:** Period 10 (ATR lookback), Multiplier 3.0 (band width)
- **On 15-min candles:** 10 × 15 = 2.5 hour ATR lookback, bands = HL2 ± 3 × ATR
- **Signal:** Trend change (DOWN→UP or UP→DOWN) = strongest signal. Continuing trend = milder confirmation
- **Why it works:** Widely used in Indian algo trading. The locked-band mechanism prevents whipsaw in the trending direction
- **Score contribution:** Trend change = ±3 (strongest), continuing trend = ±1

### Daily EMA(9/21) Bias
- **What:** EMA crossover on daily candles — higher timeframe trend direction
- **Why it works:** Intraday trades that align with the daily trend have higher win rates
- **Score contribution:** ±1 (only if spread > 1%)

---

## Candlestick Patterns Detected

### Single-Candle Patterns

| Pattern | Signal | Strength | Description |
|---------|--------|----------|-------------|
| Doji | NEUTRAL | 1 | Body < 10% of range. Indecision — prior trend may reverse |
| Marubozu | BULLISH/BEARISH | 3 | Body > 90% of range. Pure conviction — no shadow means no opposition |
| Hammer | BULLISH | 2 | Small body at top, long lower shadow. Sellers pushed price down but buyers reclaimed. Requires prior downtrend |
| Inverted Hammer | BULLISH | 2 | Small body at bottom, long upper shadow. Buyers testing higher levels after decline |
| Shooting Star | BEARISH | 2 | Same shape as inverted hammer but after uptrend. Buyers failed to hold gains |
| Hanging Man | BEARISH | 2 | Same shape as hammer but after uptrend. Warning of distribution |

### Multi-Candle Patterns

| Pattern | Signal | Strength | Description |
|---------|--------|----------|-------------|
| Bullish Engulfing | BULLISH | 2-3 | Current bullish candle completely engulfs prior bearish candle. Stronger after a downtrend (strength 3) |
| Bearish Engulfing | BEARISH | 2-3 | Current bearish candle engulfs prior bullish candle. Stronger after an uptrend |
| Morning Star | BULLISH | 3 | 3-candle reversal: big bearish → small body → big bullish closing above midpoint of first candle |
| Evening Star | BEARISH | 3 | 3-candle reversal: big bullish → small body → big bearish closing below midpoint of first |
| Three White Soldiers | BULLISH | 3 | 3 consecutive bullish candles, each opening higher and closing higher. Strong continuation |
| Three Black Crows | BEARISH | 3 | 3 consecutive bearish candles, each opening lower and closing lower |
| Bullish Harami | BULLISH | 1 | Small bullish candle contained within prior large bearish candle. Weak reversal — needs confirmation |
| Bearish Harami | BEARISH | 1 | Small bearish candle contained within prior large bullish candle |

---

## Composite Scoring System

The composite score combines candle patterns + technical indicators:

```
Candle pattern score:  -6 to +6 (multiple patterns can stack)
Technical score:       -10 to +10

Total range:           ~-16 to +16
```

**Score interpretation:**
- `|score| >= 5` → Strong signal, high conviction
- `|score| >= 2` → Moderate signal (passes V2_MIN_SCORE filter)
- `|score| < 2`  → Weak/no signal (filtered out)
- Positive = net bullish, Negative = net bearish

**Example scoring for a strong BUY setup:**
```
RSI(14) = 25 (oversold)           → +3
EMA(9) crossed above EMA(21)       → +2
SuperTrend just flipped to UP      → +3
Price above VWAP                   → +1
HAMMER pattern detected            → +2
                              Total: +11 → STRONG_BUY
```

**Example scoring for a strong SHORT setup:**
```
RSI(14) = 82 (overbought)         → -3
EMA(9) crossed below EMA(21)      → -2
SuperTrend flipped to DOWN         → -3
Price below VWAP                   → -1
SHOOTING_STAR pattern detected     → -2
EVENING_STAR pattern detected      → -3
                              Total: -14 → STRONG_SELL
```

---

## Configuration (config.py)

| Setting | Default | Description |
|---------|---------|-------------|
| `V2_CANDLE_RESCAN_MINUTES` | 15 | How often to re-run candle analysis during monitoring (FREE, no Claude cost) |
| `V2_MIN_SCORE` | 2.0 | Minimum |score| to pass into Claude. Lower = more candidates but weaker signals |
| `V2_CANDLE_INTERVAL` | "15minute" | Primary candle interval for pattern detection |

All V1 settings (budget, timing, SL, trailing, circuit breaker) also apply to V2.

---

## Why This Combination Works for Intraday

1. **SuperTrend** is the Indian market algo-trading standard. Most institutional algo traders use it. Trading in the direction of SuperTrend = swimming with the current.

2. **RSI extremes** on intraday timeframes have mean-reversion tendency in liquid large-caps (Nifty 50/100). RSI(14) below 30 on 15-min = the selling was overdone, likely to bounce.

3. **EMA crossover** captures momentum shifts. When the 9-period EMA crosses the 21-period, it means recent price action is definitively stronger/weaker than the medium-term. On 15-min candles, this is a 2-hour vs 5-hour average — ideal for same-day trades.

4. **VWAP** is the institutional anchor. Hedge funds and algos execute relative to VWAP. A stock consistently above its VWAP has genuine buying interest (not just retail spike).

5. **Candle patterns** provide the "entry timing" layer. Indicators tell you the direction; patterns tell you *when* to enter. A HAMMER at an RSI oversold zone near VWAP support = textbook high-probability long entry.

6. **Confluence** is key. The scoring system rewards setups where multiple independent indicators agree. A stock with score ≥ 5 has at least 3 indicators pointing the same direction — statistically much higher win rate than any single indicator alone.

---

## Fallback Behaviour

- If the V2 pre-filter finds **no candidates** above V2_MIN_SCORE, it falls back to V1 behaviour (sends all prices to Claude)
- If candle data fetch fails for a stock, that stock is simply skipped (non-blocking)
- All V1 risk management (SL, trailing, circuit breaker) runs identically in V2
- If V2 has issues, just drop the `--v2` flag to run V1
