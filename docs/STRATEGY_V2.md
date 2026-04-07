# V2 Trading Strategy — Candle Pattern + Technical Indicator Pre-Filter

## Overview

V2 adds a **mathematical pre-filtering layer** before Claude. Instead of sending 100 raw stock prices to Claude, V2 first runs candlestick pattern detection and technical indicator analysis on every stock (for free, using Zerodha's historical candle API). Only the top 15 stocks with the strongest technical setups are sent to Claude — along with their exact RSI, EMA, VWAP, SuperTrend, and detected candle patterns.

**Run with:** `python main.py --mode trade`

V2 is the **default** trading strategy. It inherits everything from V1 (ATR-based SL, trailing stops, circuit breaker, crash recovery, etc.). Use `--v1` for the retired legacy strategy.

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
  → Fetch 15-minute candles (last 3 days) from Zerodha Historical API
  → Fetch daily candles (last 30 days) for trend context
  → Run 14 candlestick pattern detectors on 15-min data
      • Volume confirmation: pattern strength ×1.3 if candle volume > 1.5× avg
      • Freshness decay: current candle = 1.0×, 1-ago = 0.7×, 2-ago = 0.4×
  → Compute technical indicators:
      • EMA(9) vs EMA(21) crossover — momentum direction
      • RSI(14) — overbought/oversold detection
      • VWAP — institutional fair value (today's candles only)
      • SuperTrend(10, 3.0) — ATR-based trend-following
      • Daily EMA(9/21) — higher timeframe trend bias
      • Previous day H/L/C — support/resistance proximity
      • MACD(12,26,9) histogram — momentum confirmation/divergence
      • Opening Range Breakout (ORB) — first candle breakout signal
      • Gap analysis — pre-market gap continuation vs fill
      • Hourly EMA(9/21) alignment — multi-timeframe confluence
      • Bollinger Band squeeze — volatility contraction breakout signal
      • ADX(14) — trend strength filter (modifies continuation signals)
      • Fibonacci retracement (38.2/50/61.8%) — prev day range S&R levels
      • VWAP SD bands (±1σ, ±2σ) — mean-reversion signals at price extremes
  → Calculate composite score (~-28 to +28)
  → Compute RVol (today's volume / 5-day average) — bonus/penalty
  → Nifty trend hard filter: against-trend signals need |score| >= 3
  → Sector diversification: max 2 stocks per sector (SECTOR_MAP)
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
  → Previous day S&R signal + pivot price
  → RVol (relative volume vs 5-day avg)
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
  → AUTO-PROTECT: if contrary signal score reaches ±4 against position:
      BUY pos + score ≤ -4  → tighten SL (lock 50% profit or move to breakeven)
      SELL pos + score ≥ +4 → tighten SL (lock 50% profit or move to breakeven)
    This is immediate, rule-based protection — no Claude cost, no 10-min wait

Every NIFTY_RECHECK_MINUTES (default: 15 min) — FREE:
  → Re-fetch NIFTY 50 index quote from Zerodha
  → Update market condition (BULLISH/BEARISH/NEUTRAL + volatility)
  → Log regime shifts (e.g. "BEARISH_NORMAL → NEUTRAL_NORMAL")
  → Updated condition feeds into subsequent re-scans and Claude reviews

Every OPPORTUNITY_RESCAN_MINUTES (default: 30 min) — PAID (1 Claude call):
  → Triggers ONLY when open_positions < MAX_POSITIONS (free slots exist)
  → Independent of position close events — proactively fills empty slots
  → Includes fresh market condition + day P&L in session context
  → If day P&L is negative, only picks high-conviction setups
  → Skipped if circuit breaker active or insufficient time remains

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

### Previous Day High/Low as Support/Resistance
- **What:** Yesterday's high, low, and pivot (H+L+C)/3 are natural support/resistance levels
- **Why it works:** Institutional traders and algorithms use these levels actively. A stock near yesterday's high faces selling pressure (resistance); near yesterday's low finds buyers (support)
- **Score contribution:** AT_RESISTANCE = -1, AT_SUPPORT = +1, ABOVE/BELOW_PIVOT = ±0.5

### Volume Confirmation (applied to candle patterns)
- **What:** Pattern candle volume compared to 10-candle rolling average
- **Why it works:** A hammer on high volume is a real reversal signal; on low volume it's noise. Investopedia and Zerodha Varsity both emphasise volume as the "single most important confirmation"
- **Effect:** High volume (>1.5× avg) → pattern strength ×1.3. Low volume (<0.5× avg) → strength ×0.5

### Pattern Freshness Decay
- **What:** Patterns detected on older candles carry less weight
- **Why it works:** "Pattern potency decreases rapidly 3-5 bars after completion" (Investopedia). A hammer forming right now is more actionable than one from 45 minutes ago
- **Decay:** Current candle = 1.0×, 1 candle ago = 0.7×, 2 candles ago = 0.4×

### Relative Volume (RVol)
- **What:** Today's volume so far compared to the 5-day daily average
- **Why it works:** RVol > 2.0 = unusual activity (news, institutional flow, catalyst). High RVol stocks are more likely to make meaningful moves
- **Score contribution:** RVol > 2.0× = +1 bonus, RVol < 0.3× = -1 penalty

### Nifty Trend Hard Filter
- **What:** When NIFTY 50 is BEARISH, against-trend BUY signals need |score| ≥ 3 (instead of ≥ 2). When BULLISH, against-trend SELL signals need |score| ≥ 3
- **Why it works:** Institutional practice: trade with the broader market. Weak counter-trend signals fail much more often than with-trend signals

### MACD(12,26,9) Histogram
- **What:** Measures the distance between the MACD line and its signal line. Positive histogram = bullish momentum, negative = bearish
- **On 15-min candles:** Fast EMA(12) = 3 hours, Slow EMA(26) = 6.5 hours, Signal EMA(9) = 2.25 hours
- **Signals:** BULLISH + GROWING = strongest buy confirmation, BEARISH + GROWING = strongest sell. SHRINKING = momentum fading (early warning)
- **Why it works:** MACD histogram captures momentum acceleration/deceleration. A growing histogram confirms the trend; a shrinking histogram warns of reversal before price shows it
- **Score contribution:** Bullish growing = +1, bearish growing = -1, fading warning = ±0.5

### Opening Range Breakout (ORB)
- **What:** Compares current price to the first 15-minute candle's high/low (the "opening range")
- **Signal:** Price above opening range high = breakout up (+2), below opening range low = breakout down (-2), inside range = no signal
- **Why it works:** The opening 15 minutes captures the initial battle between overnight orders, pre-market positioning, and opening trades. A decisive break above/below this range often sets the trend for the day. Widely used by professional Indian intraday traders
- **Score contribution:** ±2 (strong signal — directional breakout from opening range)

### Gap Analysis
- **What:** Measures the gap between today's open and yesterday's close, with volume confirmation
- **Signals:** Gap-up > 1% with high volume = continuation (+1), gap-up with low volume = fill risk (-1). Symmetric for gap-down
- **Volume check:** First candle volume vs expected (daily avg / 25) — confirms whether institutional money is backing the gap
- **Why it works:** Gaps represent overnight information asymmetry. Gap-ups with strong volume are typically institutional, likely to hold. Gap-ups on weak volume are often retail-driven gap fills
- **Score contribution:** ±1 (confirmation/warning signal)

### ADX(14) — Average Directional Index
- **What:** Measures trend strength regardless of direction. Uses Wilder's DI+/DI- system with smoothed true range
- **On 15-min candles:** 14-period lookback (~3.5 hours)
- **Signals:** ADX < 20 = WEAK (range-bound, trends unreliable), ADX 20-30 = MODERATE, ADX > 30 = STRONG (well-established trend)
- **How it modifies scoring:** In WEAK trends, halves the magnitude of EMA spread (±1) and SuperTrend continuation (±1) to avoid false trend signals. In STRONG trends, adds ±0.5 directional bonus aligned with DI+/DI-
- **Why it works:** Trend-following indicators give many false signals in range-bound markets. ADX acts as a meta-filter — only trusting continuation signals when a real trend exists. Standard professional practice
- **Score contribution:** ±0.5 (modifier on existing scores)

### Sector Diversification Filter
- **What:** Maximum 2 stocks per sector (BANKING, IT, PHARMA, AUTO, ENERGY, METALS, FMCG, INFRA, FINANCE, TELECOM, CAPGOODS, OTHER)
- **Why it works:** Prevents correlated risk. Without this filter, the scanner could pick 5 banking stocks that all drop together on a single RBI announcement. Sector-capping forces diversification across uncorrelated sectors
- **Implementation:** Applied after score filtering, before final candidate selection

### Partial Profit Taking
- **What:** At 1.5× risk profit (TRAIL_AFTER_RISK_MULTIPLE), automatically exits 33% of the position (1/3) and moves SL to breakeven for the remainder
- **Why it works:** Locks guaranteed profit on a third of the position while letting the remaining two-thirds run with a trailing stop (65% step). The 1.5× risk trigger avoids cutting winners too early — a 1× trigger was found to cap upside excessively in practice
- **Edge case:** Only triggers when qty >= 3 (can't split smaller). Only triggers once per position

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
Candle pattern score:  -6 to +6 (volume-adjusted, freshness-decayed)
Technical score:       -24 to +24
  (EMA ±2, RSI ±3, VWAP ±1, SuperTrend ±3, Daily EMA ±1,
   Prev Day S&R ±1, MACD ±1.5, ORB ±2, Gap ±1,
   Hourly EMA ±1, BB Squeeze ±1, ADX ±0.5,
   Fib +0.5, VWAP Bands ±1, Extended Move Penalty ±3)
  Note: When VWAP bands are active, basic VWAP position score is removed
  to prevent cancellation at extremes.
  Note: RSI extreme hard cap — if RSI ≥ 75, score capped at +3 max;
  if RSI ≤ 25, score capped at -3 min. Prevents trend indicators from
  overriding extreme overbought/oversold readings.
RVol bonus/penalty:    -1 to +1

Total range:           ~-28 to +28
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
Prev day: AT_SUPPORT               → +1
MACD: BULLISH, GROWING             → +1
ORB: BREAKOUT_UP                   → +2
Gap: GAP_UP_STRONG (high vol)      → +1
HAMMER pattern (high vol, fresh)   → +2.6  (2 × 1.3)
RVol = 2.5×                        → +1
                              Total: +17.6 → STRONG_BUY
```

**Example scoring for a strong SHORT setup:**
```
RSI(14) = 82 (overbought)         → -3
EMA(9) crossed below EMA(21)      → -2
SuperTrend flipped to DOWN         → -3
Price below VWAP                   → -1
Prev day: AT_RESISTANCE            → -1
MACD: BEARISH, GROWING             → -1
ORB: BREAKOUT_DOWN                 → -2
Gap: GAP_DOWN_STRONG (high vol)    → -1
SHOOTING_STAR (high vol, fresh)    → -2.6  (2 × 1.3)
EVENING_STAR (low vol, 1-ago)      → -1.05 (3 × 0.5 × 0.7)
RVol = 0.2×                        → -1
                              Total: -19.65 → STRONG_SELL
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
