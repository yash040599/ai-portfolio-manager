# Edge Gap Audit — 2026-05-26

**Trigger**: Post 62-gate backtest audit. PF improved 0.71 → 0.86 but still sub-1.0.
**Question**: Why is the system not profitable and what is the missing edge?

## 1. Current System Summary

| Metric | Value |
|---|---|
| Profit Factor | 0.86 (backtest, after 62-gate audit) |
| Win Rate | 37.8% |
| R:R Target | 1.8:1 |
| Break-even WR (raw) | 35.7% |
| Break-even WR (after costs) | ~42-45% |
| Daily trade cap | 2 (K1) |
| Budget | Rs.50,000 |
| AI mode | Gemini 2.5 Flash selects 2/day from NIFTY50 |
| FY net (pre-audit, NoAI) | Rs.-3,929 on 184 trades |
| H2 2024 PF (post-audit) | 1.02 (only profitable half) |

**Core approach**: Blended 14-indicator + 14-pattern technical scorer → AI quality gate → ATR-based SL/target.

## 2. Diagnosed Edge Gaps

### Gap A: No Microstructure Data
- System uses only candle OHLCV (15-min).
- Competing against HFTs and prop desks with Level-2 order book depth, order flow imbalance, and trade-by-trade prints.
- **Impact**: Entries are blind to who is buying/selling and at what size.

### Gap B: No Regime Awareness
- ADX distinguishes trending vs range-bound, but there is no classification of *which* intraday regime is active (gap-and-go, opening drive, midday chop, closing auction).
- The same strategy runs in all conditions.
- **Impact**: Strategies that work in trending regimes get destroyed in choppy regimes and vice versa.

### Gap C: Single Strategy for All Conditions
- One blended scorer runs all day.
- Three alternative strategies were backtested (VWAP MR, ORB-15, EMA Pullback) — all failed *when run all the time*.
- However, each has conditions where it shows promise (ORB-15 was PF 0.97).
- **Impact**: No regime-routing means the system bleeds in conditions where its single approach has negative expectancy.

### Gap D: Transaction Cost Drag
- Break-even WR is 35.7% raw but 42-45% after Zerodha STT + brokerage + GST.
- At Rs.50K capital, per-trade charges are proportionally massive.
- 37.8% WR is above raw break-even but below after-cost break-even.
- **Impact**: Even marginal edge gets eaten by costs. This is the #1 reason PF < 1.0.

### Gap E: No Order Flow Signal
- Institutional entries show up in trade-by-trade data (large block prints, iceberg orders).
- System has no access to this signal layer.
- **Impact**: Missing the strongest short-term predictive signal available.

### Gap F: No Options Overlay
- Equity intraday MIS has high STT (₹0.025% per side).
- F&O equity options have lower buy-side STT and allow defined-risk structures.
- **Impact**: Cost structure disadvantage vs options-based strategies.

## 3. Strategies Evaluated

### Already Backtested (2026-05-25/26)

| Strategy | PF | WR | Verdict |
|---|---|---|---|
| VWAP Mean-Reversion | 0.80 | 23.1% | FAIL |
| ORB-15 Breakout | 0.97 | 55.7% | MARGINAL (closest to profitable) |
| EMA Pullback Momentum | 0.65 after costs | 42.8% | FAIL (edge doesn't survive costs) |
| Current blended scorer | 0.86 | 37.8% | BEST SO FAR but still losing |

### Not Yet Tested — Research Pipeline

| # | Strategy | Source | Rationale | Indian-specific |
|---|---|---|---|---|
| 1 | ORB-5 (5-min instead of 15-min) | Crabel, Connors & Raschke | ORB-15 was PF 0.97; tighter timeframe + volume filter may cross 1.0 | Strong first-30-min directional bias on NSE |
| 2 | VWAP Twist (breakout with VWAP trail, not MR) | Institutional algo standard | Use VWAP as trailing stop on trend trades, not mean-reversion target | FIIs anchor to VWAP on NSE |
| 3 | Regime-switching ensemble | Lopez de Prado; AQR Research | Classify day as Trend/Range/Volatile, route to matching sub-strategy | VIX + ADX + gap% available from existing data |
| 4 | Order Flow Imbalance (OFI) | Cont, Kukanov & Stoikov 2014 | Bid-ask imbalance at top levels predicts short-term price | Needs Level-2 data (TrueData, ~₹2K/mo) |
| 5 | Pairs Trading (stat-arb) | Gatev, Goetzmann & Rouwenhorst (Yale) | Trade correlated pairs (HDFCBANK/ICICIBANK). Market-neutral. | Indian banking pairs are highly cointegrated |
| 6 | Expiry-Day Options Selling | NSE data, QuantInsti research | Thursday expiry theta decay; India = world's largest options market | Documented retail edge |
| 7 | Intraday Momentum (first-half → last-half) | Gao, Han, Li & Zhou 2018 | First-half-hour return predicts last-half-hour return | Strong on NSE due to FII patterns |
| 8 | Post-News Momentum (event-driven) | Jegadeesh & Titman 1993 (adapted) | Enter on earnings surprise / block deal with short hold | NSE block deal data is public real-time |

## 4. Available Tools & Data Sources

### Data Providers (Indian Market)

| Tool | What it provides | Cost | Priority |
|---|---|---|---|
| Zerodha Historical API | 1-min candles (currently using 15-min) | Free (existing) | **Use immediately** — switch to 5-min |
| TrueData / GlobalDataFeeds | Tick-by-tick, Level-2 order book depth | ₹1,500-3,000/mo | HIGH — needed for OFI strategy |
| NSE Bhav Copy | End-of-day data | Free | Already sufficient |
| Chartink Screener | Pre-built NSE scan alerts | Free/Pro ₹1K/mo | LOW — validation tool |

### ML / AI Tools

| Tool | Use Case | Cost |
|---|---|---|
| XGBoost / LightGBM | Regime classifier, entry quality scorer | Free |
| Optuna | Auto-tune ATR, R:R, SL% across regimes | Free |
| FinRL | RL agent for position sizing / exit timing | Free |
| AlphaLens + Zipline | Factor analysis on the 14 indicators | Free |
| Gemini 2.5 Flash (current) | Trade selection quality gate | Pay-per-use |

### Indian-Specific Platforms

| Resource | Use |
|---|---|
| Streak by Zerodha | No-code backtester for quick validation |
| Sensibull / Opstra | Options analytics (if options strategy pursued) |
| AlgoTest | Backtest options strategies on NSE data |
| QuantInsti (EPAT) | Indian-market algo trading research |

## 5. AI Assessment

| AI Application | Impact | Note |
|---|---|---|
| Regime classification (ML, not LLM) | **HIGH** — #1 lever | Random Forest / XGBoost on VIX+ADX+gap+breadth |
| Trade quality gate (LLM) | **MEDIUM** — already deployed | Marginal returns from here |
| Order flow features (ML on tick data) | **HIGH** — if data acquired | scikit-learn on OFI features |
| Adaptive parameter tuning (Optuna) | **MEDIUM** | Auto-tune instead of manual sweeps |
| News sentiment (LLM) | **LOW for intraday** | Too slow; move happens before LLM parses |

**Verdict**: LLMs will not generate alpha. Classical ML on tabular data (regime, OFI) is the right tool. LLMs are useful as orchestrators and quality filters, not predictors.

## 6. Key Insight

The three backtested strategies failed because they were deployed in ALL market conditions. A regime classifier that routes trades to the right sub-strategy is the single highest-impact improvement. This is supported by:
- ORB-15 at PF 0.97 (near-profitable on trending days)
- VWAP MR at PF 0.80 (could work if restricted to ADX < 20 range days)
- H2 2024 showing PF 1.02 (proving the edge exists in some conditions)

The system doesn't lack strategies — it lacks the intelligence to know *when* to use each one.

---

*Audit date: 2026-05-26. Next action: Create phased execution plan.*
