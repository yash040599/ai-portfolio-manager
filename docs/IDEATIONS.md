# Ideations -- V3 Vision & Future Work

> **Status**: Planning / Research only -- no code changes.
> **Context**: V2 improvements are tracked in STRATEGY_ROADMAP.md. This file contains V3 architectural changes and future research ideas.

---

## In Plain English — Where V3 Is Going

V2 is "a calculator that picks trades". Every score is computed from fixed formulas a human wrote in code. V3 shifts the bot in three big ways:

1. **The scorer becomes a learner.** Instead of us saying "RSI under 30 = +2 points", a machine-learning model looks at every past trade, figures out what combinations of indicators actually led to profit, and assigns its own weights. The indicators themselves stay the same — only the formula that combines them changes.
2. **A new data source: options.** Today V2 only reads the stock's own price and volume. Options data tells you what **other traders are betting** — how many people bought insurance (puts) vs. speculation (calls), where the "pain point" is for option sellers, where institutions put their largest bets. This is sentiment data the bot is currently blind to.
3. **AI gets a new job.** Today Claude's role (in `--ai` mode) is picking stocks from a short list — a math-heavy task. In V3, the pure-math work goes to machine learning, and Claude is promoted to reading **text**: news headlines, earnings announcements, budget-day rhetoric, macro context. Each tool does what it's naturally best at.

None of this breaks V2. V3 is additive — you'd still be able to run V2 (the fast, deterministic, Rs.0/day mode) while V3 experiments in parallel.

Beyond V3, "Future Work" below covers bigger ideas (reinforcement learning, pairs trading, interactive terminal) that need much more data or research before they're worth building.

---

## Version Distinction

| Version | Core Identity |
|---------|--------------|
| **V1** | Claude picks stocks from raw candle snapshots. Human intuition encoded as prompts. **Retired.** |
| **V2** | Quantitative scoring engine (14 indicators + candle patterns). Claude or NoAI selects from scored candidates. Hand-tuned weights. **Current.** |
| **V3** | **ML scores, Options sense, Claude narrates.** ML learns weights from trade history. Options chain adds institutional sentiment. Claude shifts from number-picker to narrative analyst (news, earnings, macro). |

> **V3 Key Insight**: LLMs are best at text, context, and narrative. ML is best at numerical patterns. V2 uses Claude for the math part (picking from numbers). V3 flips this -- **ML for numbers, Claude for narrative** -- each does what it is best at.

---

## V3 -- Core Architecture Changes

### 1. ML Scoring Model (V3 Headline Feature)

**What it is**: Replace the hand-tuned composite score (14 indicators with manual weights) with a trained ML model.

**Approach**:
- **Model**: XGBoost or LightGBM -- fast, handles tabular data well, interpretable feature importance.
- **Features**: Same 14 indicators V2 already computes, plus candle pattern scores, time-of-day, day-of-week, NIFTY regime, sector strength.
- **Label**: Trade outcome -- profit/loss from V2 actual trades (available in trading reports).
- **Output**: Probability of profitable trade (0.0 to 1.0) instead of a composite score.

**Why it is better than hand-tuned scores**:
- Learns non-linear interactions (e.g., RSI + VWAP together matter more than either alone).
- Adapts weights from actual outcomes instead of guessing.
- Feature importance tells you which indicators actually predict profitability.

**Requirements**: Need enough trade history for training. V2 trading data files are the training set. Minimum ~200-500 trades for a basic model.

**Depends on**: Backtesting framework (V2 Roadmap #24) to validate before deploying.

---

### 2. Options Chain Signals (V3 Headline Feature)

**What it is**: Use NSE options chain data to gauge institutional sentiment -- a completely new data dimension V2 does not touch.

**Signals**:
- **Put-Call Ratio (PCR)**: Volume of puts / volume of calls. Baseline ~0.7 is neutral. PCR > 1.2 = bearish. PCR < 0.5 = bullish. Contrarian at extremes.
- **Open Interest (OI) Buildup**: Rising OI + rising price = strong trend. Rising OI + falling price = bearish pressure.
- **Max Pain**: The strike price where maximum options expire worthless. NIFTY/BANKNIFTY gravitate toward max pain on expiry day.
- **OI-based Support/Resistance**: High put OI at a strike = support. High call OI at a strike = resistance.

**Data source**: NSE provides options chain snapshots. Zerodha API may have some access. Polling every 3-5 minutes is sufficient.

---

### 3. AI for News / Sentiment (Claude New Role)

**What it is**: Instead of Claude picking stocks from numbers, use Claude to process text that ML cannot handle.

**Use cases**:
- **Pre-market news scan**: Feed Claude morning headlines. "HDFC Bank Q4 results beat estimates" = bullish bias for HDFCBANK.
- **Earnings calendar awareness**: Know which stocks report today. Avoid entering positions before results.
- **Corporate action detection**: Beyond V2 gap detection -- Claude reads corporate action text (splits, bonuses, buybacks).
- **Macro sentiment**: Budget day, election results, global cues -- Claude reads narrative and outputs market-level bias.

**Why V3**: This redefines Claude entire role. In V2, Claude picks from scored snapshots (a math task). In V3, Claude processes unstructured text (a language task). Requires new data sources (news APIs, earnings calendars) and new prompts.

---

### 4. Order Flow / Market Depth

**What it is**: Analyse the live order book (bid-ask depth) to detect institutional intent before price moves.

**Signals**:
- **Bid-Ask Imbalance**: If total bid volume >> total ask volume across top 5 levels, buyers are more aggressive.
- **Volume at Price (VAP)**: Where most volume traded today -- high-volume price zones act as magnets / support.
- **Trade Flow Direction**: Classify each trade as buyer-initiated (at ask) or seller-initiated (at bid).
- **Large Order Detection**: Unusual order sizes at specific levels signal institutional interest.

**Data source**: Zerodha provides 5-level market depth via API. Start with bid-ask imbalance ratio as a simple entry confirmation signal.

**Ideally depends on**: WebSocket (V2 Roadmap #44) for real-time data.

---

## Future Work -- Needs More Data / Research

These require significantly more trade history, new infrastructure, or academic-level research. Park for after V3 is stable.

### F1. Reinforcement Learning for Position Management

**What it is**: Train an RL agent to manage open positions -- when to trail, when to partial exit, when to add to a winning position.

**Why it is powerful**: V2 trailing logic is rule-based (trail 50% at 1.5R). RL could learn optimal trailing/exit policies from historical outcomes.

**Why future**: Requires a simulation environment, reward shaping, thousands of trades for training, and V3 ML model must exist first.

### F2. Interactive Terminal Mode

**What it is**: Keyboard listener in the monitor loop allowing real-time user commands during trading.

**Design**:
- Use `msvcrt` (Windows) / `select` (Linux) for non-blocking key reads in the poll loop
- Commands:
  | Key/Command | Action |
  |---|---|
  | `r` or `Enter` | Trigger immediate rescan (skip waiting for next interval) |
  | `s` | Show current status summary (positions, P&L, next scan time) |
  | `q` | Graceful shutdown (square off + EOD) |
  | `c <KEY> <VALUE>` | Change config at runtime (e.g. `c MAX_POSITIONS 4`) |
  | `p` | Pause new entries (toggle) |

**Considerations**:
- Must not block the monitor loop — keyboard polling, not a prompt
- Config changes need a safe subset (position limits, SL%, timeouts — not things that break mid-session)
- Must play nicely with ANSI status line overwriting
- Start with just `r` key for manual rescan (highest value, lowest risk), add rest incrementally

**Why future**: Thread safety concerns, cross-platform key handling complexity.

### F3. Options Chain Intelligence (Expiry Days)

**What it is**: Use Zerodha Kite options chain data to improve Thursday expiry-day decisions.

**Signals**:
- **Put-Call Ratio (PCR)**: Volume of puts / calls. PCR > 1.2 = bearish. PCR < 0.5 = bullish. Contrarian at extremes.
- **Max Pain**: Strike price where maximum options expire worthless. NIFTY gravitates toward max pain on expiry. If NIFTY is far from max pain, direction trades are riskier.
- **OI Buildup**: Rising OI + rising price = strong trend. Rising OI + falling price = bearish pressure.

**Data source**: Kite Connect instruments API + quotes for option strikes. Need to fetch ~40-60 strikes for NIFTY CE+PE.

**Usage**: On Thursday, compute max-pain and PCR before first scan. If NIFTY is within 0.5% of max-pain → reduce positions further or skip trading. If PCR is extreme → bias direction accordingly.

**Why future**: New data pipeline (options module), only relevant 1 day/week. Higher ROI to fix other gaps first.

### F4. High-OI Stock Filter (Expiry Days)

**What it is**: On expiry Thursdays, stocks with massive open interest in near-strike options get "pinned" — price oscillates near the strike as option writers defend their positions. This creates fake breakouts that trap directional traders.

**Data source**: Same Kite options API. Check individual stock F&O OI levels.

**Filter**: If a stock has OI > threshold at a strike within 1% of current price → skip on expiry day. Only ~30 of NIFTY100 have active F&O, so the filter is narrow.

**Why future**: Requires determining what OI level counts as "high" (needs historical context). Dependent on F3 options module.

---

### F5. Pairs Trading / Statistical Arbitrage

**What it is**: Trade the spread between two correlated stocks. When the spread widens beyond historical norms, go long the underperformer and short the outperformer.

**Classic pairs in India**: HDFCBANK-ICICIBANK, SBIN-PNB, TCS-INFY, RELIANCE-ONGC.

**Why future**: Requires cointegration testing on months of historical data, separate position management logic, and a fundamentally different risk model. Build V3 directional strategy first.

---

### F6. Multi-Asset Signal Correlation

**What it is**: Use signals from related instruments to confirm equity trades.

**Examples**:
- Gold rising + NIFTY falling = risk-off regime, reduce long exposure.
- USD/INR spike = negative for IT stocks (sentiment is risk-off).
- Crude oil move = impacts OMCs and airlines.
- Bond yields rising = negative for growth stocks, positive for banks.

**Why future**: Requires multi-asset data feeds. Correlations are regime-dependent. Needs significant historical analysis first.

---

## Summary Table

| # | Feature | Category | Impact | Effort | Depends On |
|---|---------|----------|--------|--------|------------|
| 1 | ML Scoring Model | **V3** | High | High | Backtesting (Roadmap #24) |
| 2 | Options Chain Signals | **V3** | High | Medium | -- |
| 3 | Claude for News/Sentiment | **V3** | High | Medium | News data source |
| 4 | Order Book Depth | **V3** | High | Medium | WebSocket (Roadmap #44) ideally |
| F1 | RL Position Management | **Future** | High | Very High | V3 ML + 1000+ trades |
| F2 | Interactive Terminal Mode | **Future** | Medium | Medium | Cross-platform key handling |
| F3 | Options Chain Intelligence (Expiry Days) | **Future** | Medium | Medium | Kite options API |
| F4 | High-OI Stock Filter (Expiry Days) | **Future** | Low | Medium | Depends on F3 |
| F5 | Pairs Trading | **Future** | Medium | High | Historical cointegration data |
| F6 | Multi-Asset Signals | **Future** | Medium | High | Multi-asset data feeds |

---

## Implementation Order

**V3 (after V2 is stable and generating trade data)**:
1. Backtesting framework FIRST (V2 Roadmap #24 -- validates everything else)
2. Options chain signals (new data, no ML dependency)
3. ML scoring model (needs backtesting to validate, needs trade history)
4. Claude for news/sentiment (redefines Claude role)
5. Order book depth (entry timing refinement)

**Future (after V3 is stable)**:
1. RL position management (F1)
2. Interactive terminal mode (F2)
3. Options-chain intelligence + high-OI filter for expiry days (F3, F4)
4. Pairs trading (F5)
5. Multi-asset correlation (F6)
