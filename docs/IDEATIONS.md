# Ideations — V2 Enhancements, V3 Vision & Future Work

> **Status**: Planning / Research only — no code changes  
> **Date**: 2026-04-08  
> **Context**: V2 is stable and reviewed. This document categorises improvements into what fits V2, what defines V3, and what's future work.

---

## Version Distinction

| Version | Core Identity |
|---------|--------------|
| **V1** | Claude picks stocks from raw candle snapshots. Human intuition encoded as prompts. |
| **V2** | Quantitative scoring engine (14 indicators + candle patterns). Claude or NoAI selects from scored candidates. Hand-tuned weights. |
| **V3** | **ML scores, Options sense, Claude narrates.** ML learns weights from trade history. Options chain adds institutional sentiment. Claude shifts from number-picker to narrative analyst (news, earnings, macro). |

> **V3 Key Insight**: LLMs are best at text, context, and narrative. ML is best at numerical patterns. V2 uses Claude for the math part (picking from numbers). V3 flips this — **ML for numbers, Claude for narrative** — each does what it's best at.

---

## A) Add to V2 — Incremental Improvements (No Architectural Change)

These fit cleanly into V2's existing architecture. No new data pipelines or model training needed.

### A1. LIMIT Orders (Instead of MARKET)

**What it is**: Place entry orders at a specific price instead of market price.

**Benefits**:
- Avoid slippage entirely (V2 currently accounts for 0.1% slippage in its calculations).
- Better fills during volatile moments.
- Can place orders at support levels and wait for price to come to you.

**Trade-off**: Orders may not fill if price doesn't reach your level. Need a "cancel if not filled within X minutes" mechanism.

**Why V2**: This is a change inside OrderEngine. No new data source, no new model. It directly saves money on every trade. Simplest, highest-ROI improvement.

---

### A2. India VIX Integration

**What it is**: India VIX measures expected volatility of NIFTY over the next 30 days. High VIX = high fear/uncertainty.

**Use in V2**:
- VIX > 20: Tighten SLs, reduce position size, avoid new entries in low-conviction setups.
- VIX < 12: Market is complacent — breakout strategies work better.
- VIX spike (intraday jump > 10%): Pause new entries, protect existing positions.

**Data source**: NSE publishes India VIX. Zerodha provides it as an instrument — one API call.

**Why V2**: Single data point, simple threshold logic. Fits as another check in the entry gauntlet or a regime modifier in config. No new infrastructure.

---

### A3. Pre-Open Auction Analysis

**What it is**: Analyse the pre-open auction session (9:00-9:08 AM) data before regular trading begins.

**Signals**:
- Pre-open price vs previous close → gap direction and magnitude.
- Pre-open volume → institutional interest.
- Order book imbalance during auction → directional bias.

**Why V2**: V2 already waits until 9:20 AM. This fills the 9:00-9:20 gap with useful intelligence using existing Zerodha API data. Fits into the pre-market scan phase. No new pipeline needed.

---

### A4. WebSocket Tick Data

**What it is**: Instead of polling candle data at intervals, stream real-time tick data via Zerodha's WebSocket.

**Benefits**:
- Sub-second price updates for SL monitoring (V2 polls every 15-60 seconds).
- Real-time volume spikes detection.
- More accurate VWAP calculation.
- Faster reaction to breakouts / breakdowns.

**Why V2**: It's a transport upgrade, not a strategy change. Replaces polling with streaming inside the existing monitor loop. V2's logic stays the same, it just gets fresher data.

**Trade-off**: Higher data volume, more CPU, need persistent connection management. V2's polling approach is simpler and adequate for most cases. Implement only if polling latency is costing real money.

---

### A5. FII/DII Flow as Pre-Market Bias

**What it is**: Previous day's FII/DII net buy/sell data as a morning bias signal.

**Use in V2**:
- FII net buying + DII net buying = strong bullish institutional support → favour long setups.
- FII net selling + DII net buying = mixed — DII absorbing FII exit → neutral.
- Both selling = institutional risk-off → reduce position count, tighter SLs.

**Data source**: Published daily by NSE/BSE. Some provisional data available intraday.

**Why V2**: Single daily data point → one pre-market API fetch → a bias flag (bullish/neutral/bearish) that feeds into the existing scan. No new architecture.

---

## B) V3 — The Core Upgrade (New Architecture)

V3's defining leap: **Replace hand-tuned scoring with ML. Add options chain as a new data dimension. Redefine Claude's role from number-picker to narrative analyst.**

### B1. ML Scoring Model ⭐ (V3 Headline Feature)

**What it is**: Replace the hand-tuned composite score (14 indicators with manual weights) with a trained ML model.

**Approach**:
- **Model**: XGBoost or LightGBM — fast, handles tabular data well, interpretable feature importance.
- **Features**: Same 14 indicators V2 already computes, plus candle pattern scores, time-of-day, day-of-week, NIFTY regime, sector strength.
- **Label**: Trade outcome — profit/loss from V2's actual trades (available in trading reports).
- **Output**: Probability of profitable trade (0.0 to 1.0) instead of a composite score.

**Why it's better than hand-tuned scores**:
- Learns non-linear interactions (e.g., RSI + VWAP together matter more than either alone).
- Adapts weights from actual outcomes instead of guessing.
- Feature importance tells you which indicators actually predict profitability.

**Requirements**: Need enough trade history for training. V2's trading data files are the training set. Minimum ~200-500 trades for a basic model.

**Why V3 (not V2)**: This is a fundamental architectural change. V2's entire scoring system (technical_indicators.py weights, composite score, MIN_SCORE threshold) gets replaced by a model. It changes how candidates are ranked, how entry decisions are made, and how we evaluate what "good" looks like. It needs a backtesting framework to validate.

---

### B2. Options Chain Signals ⭐ (V3 Headline Feature)

**What it is**: Use NSE options chain data to gauge institutional sentiment — a completely new data dimension V2 doesn't touch.

**Signals**:
- **Put-Call Ratio (PCR)**: Volume of puts ÷ volume of calls. Baseline ~0.7 is neutral. PCR > 1.2 → bearish sentiment. PCR < 0.5 → bullish. Contrarian at extremes.
- **Open Interest (OI) Buildup**: Rising OI + rising price = strong trend. Rising OI + falling price = bearish pressure. Falling OI = unwinding / weak trend.
- **Max Pain**: The strike price where maximum options expire worthless. NIFTY/BANKNIFTY gravitate toward max pain on expiry day — useful for index-level bias.
- **OI-based Support/Resistance**: High put OI at a strike → support. High call OI at a strike → resistance. Institutional-grade S/R levels.

**Why V3 (not V2)**: This is an entirely new data pipeline (options chain API → parsing → signal extraction). It feeds into the ML model as additional features and creates a new pre-filter layer. V2 only knows price + volume. V3 adds the derivatives dimension.

**Implementation notes**: NSE provides options chain snapshots. Zerodha's API may have some access. Polling every 3-5 minutes is sufficient. Can serve as ML features, pre-filter (e.g., don't go long when PCR > 1.2), or score boost.

---

### B3. AI for News / Sentiment (Claude's New Role) ⭐ (V3 Headline Feature)

**What it is**: Instead of Claude picking stocks from numbers, use Claude to process text that ML can't handle.

**Use cases**:
- **Pre-market news scan**: Feed Claude morning headlines. "HDFC Bank Q4 results beat estimates" → bullish bias for HDFCBANK. "RBI raises rates" → bearish for rate-sensitive sectors.
- **Earnings calendar awareness**: Know which stocks report today. Avoid entering positions before results (high IV, unpredictable).
- **Corporate action detection**: Beyond V2's 35% gap detection — Claude can read corporate action text (splits, bonuses, buybacks) and adjust strategy.
- **Macro sentiment**: Budget day, election results, global cues — Claude reads the narrative and outputs a market-level bias (bullish/neutral/bearish).

**Why V3 (not V2)**: This redefines Claude's entire role. In V2, Claude picks from scored snapshots (a math task). In V3, Claude processes unstructured text (a language task). This requires new data sources (news APIs, earnings calendars), new prompts, and a different integration pattern — Claude outputs bias signals that feed into ML, not stock picks.

---

### B4. Order Flow / Market Depth

**What it is**: Analyse the live order book (bid-ask depth) to detect institutional intent before price moves.

**Signals**:
- **Bid-Ask Imbalance**: If total bid volume >> total ask volume across top 5 levels, buyers are more aggressive. Useful for timing entries.
- **Volume at Price (VAP)**: Where most volume traded today — high-volume price zones act as magnets / support.
- **Trade Flow Direction**: Classify each trade as buyer-initiated (at ask) or seller-initiated (at bid). Net buy/sell pressure in real-time.
- **Large Order Detection**: Unusual order sizes at specific levels signal institutional interest.

**Why V3 (not V2)**: Order book analysis is a new data pipeline (5-level depth → real-time processing → signal extraction). These signals become features for the ML model and entry timing confirmations. V2 uses OHLCV candles (aggregated). Order book is tick-level, pre-trade data — a different layer entirely.

**Implementation notes**: Zerodha provides 5-level market depth via API. Start with bid-ask imbalance ratio as a simple entry confirmation signal.

---

### B5. Backtesting Framework (V3 Infrastructure)

**What it is**: Replay scoring logic on historical candle data to validate strategies before deploying them live.

**Architecture**:
- **Data**: Historical 5-minute candles (Zerodha provides up to ~60 days, or use third-party data providers for longer history).
- **Engine**: Simulate the full pipeline — pre-filter → score → enter → SL/target → exit — on past data.
- **Metrics**: Win rate, average R:R achieved, max drawdown, Sharpe ratio, profit factor.
- **Uses**: Test parameter changes before going live. Validate ML model before deploying. Compare V2 scoring vs V3 ML scoring on same data.

**Why V3 (not V2)**: The ML model (B1) *requires* backtesting to validate. You can't train a model and deploy it without testing on held-out data. Backtesting is the foundation that makes B1, B2, and B4 trustworthy. Also, historical data collection is a new infrastructure piece.

---

## C) Future Work — Needs More Data / Research

These are powerful but require either significantly more trade history, new infrastructure, or academic-level research. Park for after V3 is stable.

### C1. Reinforcement Learning for Position Management

**What it is**: Train an RL agent to manage open positions — when to trail, when to partial exit, when to add to a winning position.

**Why it's powerful**: V2's trailing logic is rule-based (trail 50% at 1.5R). RL could learn optimal trailing/exit policies from historical outcomes. This is state-of-the-art in quant trading.

**Why future (not V3)**:
- Requires a simulation environment (which backtesting provides, but much more).
- Needs reward shaping — defining what "good" position management means is non-trivial.
- Needs thousands of trades for meaningful training.
- Academic research shows promise but production implementations are rare.
- V3's ML model must exist first — RL builds on top of it.

---

### C2. Pairs Trading / Statistical Arbitrage

**What it is**: Trade the spread between two correlated stocks. When the spread widens beyond historical norms, go long the underperformer and short the outperformer.

**Classic pairs in India**: HDFCBANK-ICICIBANK, SBIN-PNB, TCS-INFY, RELIANCE-ONGC.

**Why it's interesting**: Market-neutral strategy — profits regardless of market direction. Reduces directional risk that V2/V3 are fully exposed to.

**Why future (not V3)**:
- Requires cointegration testing on historical data (months of price history).
- Separate position management logic (paired entries/exits, spread monitoring).
- Different risk model — not SL/target per stock, but spread-based exits.
- It's a fundamentally different trading strategy, not an enhancement to the current one.
- Build V3's directional strategy first, add market-neutral as a second strategy later.

---

### C3. Multi-Asset Signal Correlation

**What it is**: Use signals from related instruments to confirm equity trades.

**Examples**:
- Gold rising + NIFTY falling → risk-off regime, reduce long exposure.
- USD/INR spike → negative for IT stocks (export earners benefit, but sentiment is risk-off).
- Crude oil move → impacts OMCs (BPCL, IOC, HPCL) and airlines.
- Bond yields rising → negative for growth stocks, positive for banks.

**Why future (not V3)**:
- Requires data feeds for commodities, forex, bonds — separate from equity API.
- Correlations are regime-dependent (what works in bull markets breaks in bear markets).
- Needs significant historical analysis to determine which correlations are tradeable vs noise.
- V3's ML model could eventually incorporate these as features, but only after we understand which correlations are stable.

---

## Summary Table

| # | Feature | Category | Impact | Effort | Depends On |
|---|---------|----------|--------|--------|------------|
| A1 | LIMIT Orders | **V2** | Medium | Low | Nothing |
| A2 | India VIX | **V2** | Medium | Low | Nothing |
| A3 | Pre-Open Auction | **V2** | Low-Med | Low | Nothing |
| A4 | WebSocket | **V2** | Medium | Medium | Nothing |
| A5 | FII/DII Flow Bias | **V2** | Low-Med | Low | Nothing |
| B1 | ML Scoring Model | **V3 ⭐** | High | High | B5 (backtesting) |
| B2 | Options Chain | **V3 ⭐** | High | Medium | Nothing |
| B3 | Claude for News | **V3 ⭐** | High | Medium | News data source |
| B4 | Order Book Depth | **V3** | High | Medium | WebSocket (A4) ideally |
| B5 | Backtesting Framework | **V3** | High | High | Historical data |
| C1 | RL Position Mgmt | **Future** | High | Very High | B1 + B5 + 1000+ trades |
| C2 | Pairs Trading | **Future** | Medium | High | Historical cointegration data |
| C3 | Multi-Asset Signals | **Future** | Medium | High | Multi-asset data feeds |

---

## Implementation Order

**V2 (now, incremental)**:
1. A1 — LIMIT Orders (biggest bang for least effort)
2. A2 — India VIX (one API call, simple thresholds)
3. A5 — FII/DII bias (one daily fetch)
4. A3 — Pre-Open Auction (fills 9:00-9:20 gap)
5. A4 — WebSocket (only if polling latency is a real problem)

**V3 (after V2 additions are stable)**:
1. B5 — Backtesting framework FIRST (validates everything else)
2. B2 — Options chain signals (new data, no ML dependency)
3. B1 — ML scoring model (needs B5 to validate, needs trade history)
4. B3 — Claude for news/sentiment (redefines Claude's role)
5. B4 — Order book depth (entry timing refinement)

**Future (after V3 is stable and generating data)**:
1. C1 — RL position management
2. C3 — Multi-asset correlation
3. C2 — Pairs trading
