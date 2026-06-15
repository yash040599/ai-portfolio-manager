# Trade Next Ideas — Intraday & Options Research

> **Created:** 2026-06-06 | **Updated:** 2026-06-15
> **Status:** Gap-and-Go **v1.2.0** (NIFTY100) — adaptive volume on broad-gap
> days. OOS PF **1.30**, Sharpe **1.29** (up from v1.1.1 baseline PF 1.17,
> Sharpe 0.71). Dry-run validation in progress.
> Phase 9 diversification research complete — 7 additional strategies tested,
> **all FAIL** (20 total). Intraday equity search space on NSE
> **conclusively exhausted**.
> **Context:** Phases 0-9 of intraday equity backtesting complete. Gap-and-Go
> v1.2.0 remains the only strategy to pass the 1.15 OOS promotion gate.

---

## Section A: INTRADAY EQUITY — Remaining Ideas to Try

These ideas stay within the existing intraday equity (MIS) framework. They do
NOT require options or futures. They represent the last credible levers before
declaring intraday equity unprofitable on NSE at this capital level.

### A.1 Order Flow Imbalance (OFI) Signal

**What:** Track bid-ask size imbalance at top-of-book levels as an entry
signal. Academic research (Cont, Kukanov & Stoikov 2014) shows OFI is the
strongest short-term price predictor — stronger than any candle-pattern or
indicator.

**Why it might work:** Our current system is blind to WHO is buying/selling and
at WHAT size. OFI captures institutional intent before it appears in candles.

**Data needed:** Level-1 real-time data — TrueData (₹1,500/mo) or
GlobalDataFeeds provide best bid/ask + sizes for NSE.

**How to integrate:**
- Add as a 15th indicator in the scanner composite score
- Or use as a standalone entry trigger (OFI surge → enter in direction)
- Or use as a quality filter (only enter if OFI confirms direction)

**Effort:** High — needs live data subscription + new data pipeline.

**Backtest feasibility:** Cannot backtest historically without tick data. Would
need forward-test (paper trade with live OFI for 30+ days).

**Expected impact:** Potentially high — this is what prop desks use. But we
cannot prove it without live data.

---

### A.2 Gap-and-Go with Volume Qualification — ✅ TESTED → PASS

> **Result (Phase 7.2, 2026-06-06):** OOS PF **1.28** (ALL, 228 trades), **1.66** (VOLATILE-only). **FIRST STRATEGY TO PASS the 1.15 OOS promotion gate.** Now live as v1.2.0 on NIFTY100 (OOS PF **1.30**, Sharpe **1.29**). See [TRADE_ROADMAP.md](TRADE_ROADMAP.md) Phase 7-8 and [TRADE_STATISTICS.md](TRADE_STATISTICS.md) for full results.

**What:** Dedicated strategy for stocks that gap >1% on open with >2x average
volume. Enter in gap direction within first 15 min, target 50-100% of gap
continuation.

**Why it worked:** Volume qualification filters false breakouts. v1.1.0 added
gap-hold 0.3% check + score-contradiction block, lifting PF 1.37→1.55. v1.1.1
expanded to NIFTY100 for better candidate quality (PF 1.55→1.62). v1.2.0
added adaptive volume on broad-gap days (≥25 stocks gapping → vol threshold
lowered 2.0x→1.25x), lifting PF 1.17→1.30 (+11%), Sharpe 0.71→1.29 (+82%).

---

### A.3 Cross-Sectional Momentum (Rank-Based Entry) — ❌ TESTED → FAIL

> **Result (Phase 7.1, 2026-06-06):** OOS PF **0.81** (ALL, 474 trades). VOLATILE-only PF 1.22 (100 trades) but thin sample. See [TRADE_ROADMAP.md](TRADE_ROADMAP.md) Phase 7.1.

**What:** Instead of scoring each stock independently, RANK all NIFTY50 stocks
by first-15-min return. Buy the top 2 strongest.

**Why it failed:** Relative outperformance doesn't persist intraday after costs on NSE. SELL-side also negative (PF 0.88).

---

### A.4 NIFTY Futures (Single Instrument, Zero STT) — ❌ TESTED → FAIL

> **Result (Phase 9.3, 2026-06-12):** OOS PF **0.25** (13 trades). Index gaps are tiny (0.2-0.5%) and don't follow through — institutional arbitrage absorbs gaps instantly. Even with zero STT (futures cost model), no edge. See [TRADE_ROADMAP.md](TRADE_ROADMAP.md) Phase 9.3.

**What:** Trade NIFTY50 futures instead of individual stocks.

**Why it failed:** The index is too efficient. NIFTY doesn't produce the 1-2% gaps with volume conviction that individual stocks do. Cost reduction alone cannot rescue a signal that doesn't exist.

---

### A.5 RSI Divergence Strategy (Not RSI Threshold)

**What:** RSI divergence (price makes new low, RSI doesn't) is fundamentally
different from RSI floor/ceiling filters. Our audit showed RSI thresholds were
HARMFUL. But divergence is a pattern, not a level.

**How to integrate:**
- Detect bullish divergence: price lower-low + RSI higher-low on 15-min
- Detect bearish divergence: price higher-high + RSI lower-high
- Combine with VWAP band extremes (price at -2σ + RSI divergence = strong)
- Entry on confirmation candle after divergence

**Effort:** Medium — divergence detection logic needs new code.

**Backtest feasibility:** Fully backtestable with existing data.

**Expected impact:** Moderate — mean-reversion signal that specifically targets
exhaustion points rather than arbitrary RSI levels.

---

### A.6 Previous Day High/Low Breakout — ❌ TESTED → FAIL

> **Result (Phase 7.3, 2026-06-06):** OOS PF **0.87** (ALL, 472 trades). VOLATILE-only PF 1.18 (98 trades, marginal). TREND-only is worst at PF 0.73 — breakouts into trend days are false breakouts. See [TRADE_ROADMAP.md](TRADE_ROADMAP.md) Phase 7.3.

**What:** Breakout of previous day's high/low with ADX≥25 + volume≥1.5× filters.

**Why it failed:** Well-arbitraged in NIFTY50 liquid names. Breakout signals produce false breakouts into established trends (TREND-only PF 0.73).

---

### A.7 Volatility Squeeze Breakout (Bollinger inside Keltner)

**What:** When Bollinger Bands contract inside Keltner Channel, it predicts a
large directional move. Enter on squeeze release with volume.

**How to integrate:**
- Scanner has Bollinger Squeeze (±0.5 score) but no Keltner comparison
- Add Keltner Channel (20 EMA ± 2×ATR) computation
- Squeeze = BB inside KC. Release = BB expands outside KC
- Enter in direction of first release candle if volume >1.5x

**Effort:** Medium — needs Keltner indicator added to technical_indicators.py.

**Backtest feasibility:** Fully backtestable.

**Expected impact:** Moderate — squeeze events are rarer but higher conviction.
May improve quality of the 2 daily trades selected.

---

### A.8 ML Entry Classifier (XGBoost on Existing Features)

**What:** Train a binary classifier on trade outcome (profitable Y/N after
costs) using the 28 existing features (14 indicators + 14 candle patterns) plus
regime label + time-of-day + VIX.

**Why it might work:** Our hand-tuned composite score assigns equal-ish weights.
ML can find nonlinear interactions — maybe SuperTrend + gap + high ADX together
is much stronger than each alone, but our linear scorer can't express that.

**How to integrate:**
- Train on the 970 trades from walk-forward backtest
- Walk-forward CV: train on year 1, test on year 2
- Use as a **filter** on top of scanner — only enter if ML says >60% win prob
- Does NOT replace scanner — augments it

**Effort:** Medium — scikit-learn/XGBoost, existing feature export.

**Backtest feasibility:** Can use existing backtest data. Risk of overfitting
with only 970 samples — need careful CV.

**Expected impact:** Unknown — depends on whether nonlinear interactions exist
in the data. If the signal is weak-everywhere (as opposed to strong-sometimes),
ML won't rescue it.

---

### A.9 Intraday Viability Assessment

**Honest verdict after Phases 0-9 (2026-06-15):**

| What We Tried | Result | Phase |
|---|---|---|
| 62-gate backtest optimization | PF 0.71 → 0.86 (still <1.0) | Audit |
| Walk-forward OOS validation | PF 0.82 (negative expectancy) | 0 |
| Regime classification + routing | VOLATILE-only PF 1.10 (best, <1.15 gate) | 1 |
| 5-min timeframe | PF 0.70 (worse) | 2 |
| ORB-5 | PF 0.66 (overfit collapse) | 2 |
| VWAP trailing stop | PF 0.78 (clips winners) | 3 |
| Intraday pairs trading | PF 0.48-0.69 (horizon mismatch) | 6 |
| VWAP mean-reversion | PF 0.80 (doesn't work intraday NSE) | Pre-audit |
| EMA pullback | PF 0.65 (edge doesn't survive costs) | Pre-audit |
| ORB-15 breakout | PF 0.97 (closest, still not enough) | Pre-audit |
| Cross-sectional momentum | PF 0.81 (ALL), 1.22 (VOL thin) | 7 |
| **Gap-and-Go** | **PF 1.30 (NIFTY100 v1.2.0) — PASS** | **7-8** |
| Previous-day breakout | PF 0.87 (ALL), 1.18 (VOL marginal) | 7 |
| First Hour Range Breakout | PF 0.81 | 9 |
| Opening Candle Momentum | PF 0.87 (ALL), 1.15 (VOL thin) | 9 |
| NIFTY Index Momentum | PF 0.25 (13 trades) | 9 |
| Sector Rotation Intraday | PF 0.78 (472 trades) | 9 |
| Gap Fade (weak-volume) | PF 0.70 (469 trades) | 9 |
| EOD VWAP Reversion | PF 0.55 (472 trades) | 9 |
| Auction Alpha (candle shape) | PF 0.74 (467 trades) | 9 |
| Options directional buying | PF 0.42-0.64 | Options v1 |
| Budget scaling (Rs.1L) | Saves only 0.024%/trade — signal problem, not cost | 9 |

**Is intraday equity conclusively dead?**

**For all strategies except Gap-and-Go — yes.** 20 strategies/variants tested
across 9 phases. Only Gap-and-Go clears the OOS 1.15 gate. The structural
NSE cost floor (0.05-0.10% round-trip) kills every strategy with <0.10%
expectancy, and budget scaling doesn't help (signal problem, not cost).

**Remaining untested ideas (diminishing returns):**

1. **OFI (A.1)** — requires paid data, can't backtest historically — LOW priority
2. **RSI Divergence (A.5)** — mean-reversion variant, untested — LOW priority
3. **Volatility Squeeze (A.7)** — BB inside KC, untested — LOW priority
4. **ML Classifier (A.8)** — only 970 samples, overfitting risk — LOW priority

**Recommendation:** Gap-and-Go v1.2.0 is the system. Continue dry-run
validation. The remaining A.x ideas have low expected value. Research effort
is better spent on **entirely different domains** (options selling,
swing/delivery, or non-equity asset classes) rather than more intraday equity
variants on NSE.

---

## Section B: OPTIONS TRADING — Potential New Mode

> **Note:** Options are NOT part of the codebase today. This section documents
> research for a potential future `--mode options` that would be a completely
> separate engine with its own capital bucket, risk rules, and execution path.
> The 2026-04-28 "No F&O" constraint in IDEATIONS.md is being revisited based
> on evidence that equity intraday has structural cost disadvantages that
> options may solve.

### B.0 Why Consider Options Now?

The intraday equity audit revealed two structural problems:

1. **Equity STT is expensive** — 0.025% per side on turnover (both buy+sell).
   On Rs.15K/trade that's ~Rs.7.50 per round trip just in STT.
2. **India is the world's #1 options market by volume** — the liquidity is
   massive, especially NIFTY/BANKNIFTY weekly options.
3. **Our regime classifier already gives directional bias** — we just need to
   express it through options instead of equity.
4. **Options cost structure can be more favorable** — STT on option buying is
   0.0625% on premium (not on notional). On a Rs.200 premium that's Rs.0.125
   per unit.
5. **Theta selling on RANGE days** — our worst equity regime (PF 0.62 OOS)
   becomes our BEST regime for selling premium.

### B.1 Directional Option Buying (NIFTY/BANKNIFTY Weekly)

**What:** Instead of buying equity shares, buy ATM or slightly OTM weekly
NIFTY/BANKNIFTY calls or puts to express the same directional view.

**How it would work:**
- Scanner detects NIFTY trend direction (already built)
- On TREND/VOLATILE day: buy ATM weekly call (bullish) or put (bearish)
- Define SL as % of premium paid (e.g., 30% of premium = max loss)
- Define target as % of premium (e.g., 50-100% gain)
- Square off before expiry (same-day or within 1-2 days)

**Why it might work:**
- **Defined risk** — maximum loss is the premium paid, no margin surprise
- **Lower STT on buy side** — option buying STT is per lot, much lower than
  equity % on turnover
- **Leverage** — one lot of NIFTY (~Rs.20K margin) controls notional of
  ~Rs.5L+, so a 0.5% NIFTY move = much larger % return on premium
- **Our regime gate is the key edge** — VOLATILE days have bigger moves that
  justify option premiums

**Risks:**
- **Time decay (theta)** — options lose value every second. Holding a losing
  position "hoping it recovers" is much more expensive than in equity
- **Volatility crush** — if VIX drops, option premiums shrink even if direction
  is correct
- **Bid-ask spread on options can be wide** — especially on OTM or less liquid
  strikes
- **Requires understanding of Greeks** — delta, theta, vega, gamma

**Capital needed:** 1 lot NIFTY option ≈ Rs.5,000-25,000 premium depending on
strike. Rs.50K budget is feasible.

**Data needed:** NSE option chain data (Zerodha API provides this).

---

### B.2 Expiry Day Theta Selling (Weekly Options)

**What:** Sell OTM (out-of-the-money) NIFTY strangles or iron condors on
Thursday expiry morning. Collect premium that decays rapidly through the day.

**How it would work:**
- Thursday morning: sell OTM call + OTM put (strangle) at safe distance
- Theta decay accelerates massively on expiry day (premium melts to zero by
  3:30 PM if NIFTY stays in range)
- Use our regime classifier: ONLY sell on RANGE days (39% of days)
- On VOLATILE/TREND days: skip (gamma risk too high)
- Iron condor variant: add far-OTM bought options as protection (capped risk)

**Why it might work:**
- **RANGE days are perfect for selling** — price stays in a band, premium
  decays to zero, seller keeps the credit
- **Our regime classifier already identifies RANGE days** — this is the natural
  complement to equity intraday (we AVOID range days for equity, we SEEK them
  for options selling)
- **Theta crush on expiry day is extreme** — OTM options can lose 80%+ of
  value on Thursday if NIFTY stays flat
- **India's weekly options expiry is the most active options market globally**

**Risks:**
- **Unlimited loss on naked selling** — a black swan move can cause losses
  many times the premium collected. Iron condors cap this.
- **Margin requirements** — selling options requires margin (~Rs.1-1.5L for
  NIFTY strangle). This may exceed our Rs.50K budget unless we use iron condors.
- **Gamma risk on expiry** — if NIFTY moves sharply, short options move against
  you very fast near expiry
- **Need fast execution** — if NIFTY breaks range, must exit quickly

**Capital needed:** Iron condor on NIFTY ≈ Rs.25K-50K margin per position.
Naked strangle ≈ Rs.1L-1.5L (may need more capital).

**Data needed:** Options chain with Greeks (available from Zerodha/Sensibull).

---

### B.3 Volatility-Based Options Strategies

**What:** Use VIX levels and implied vs realized volatility mismatch to decide
whether to buy or sell options.

**How it would work:**
- When INDIA VIX is high (>18) and realized volatility is lower → sell options
  (overpriced)
- When INDIA VIX is low (<12) and a catalyst is expected → buy options
  (underpriced)
- Combine with regime gate: high VIX + RANGE day = premium selling; low VIX +
  TREND day = directional buying

**Why it might work:** Implied volatility on Indian options is systematically
overpriced (well-documented globally — the "variance risk premium"). Selling
premium in high-IV environments has a structural edge.

**Risks:**
- Black swans — volatility sellers get wiped out in tail events
- Requires disciplined position sizing and risk management
- Requires understanding of IV vs RV dynamics

---

### B.4 Calendar Spread / Diagonal Spread

**What:** Buy a far-expiry option, sell a near-expiry option at the same or
different strike. Profits from time-decay differential.

**How it would work:**
- Sell this week's expiry call/put (fast theta decay)
- Buy next week's expiry call/put (slower decay, retains value)
- Net premium is low; profit comes from the sold option decaying faster
- Close when sold option expires or is near worthless

**Why it might work:**
- Market-neutral-ish
- NIFTY weekly options have enough term structure anomaly
- Defined risk (max loss = net debit paid)
- Zerodha Varsity's Trading Systems module covers this

**Risks:**
- Requires precise strike selection
- Pin risk near expiry
- Lower absolute returns than directional trading

---

### B.5 Dispersion Trading (Advanced)

**What:** Exploit the gap between NIFTY index implied volatility and the
realized volatility of NIFTY components.

**How it would work:**
- When NIFTY implied vol is high relative to average component vol → sell NIFTY
  straddle + buy component straddles
- When NIFTY implied vol is low relative to components → buy NIFTY straddle +
  sell component straddles
- Profits from the structural overpricing of index volatility due to
  correlation risk premium

**Why it might work:** This is a well-known institutional strategy. Indian
retail massively sells index puts (NIFTY), systematically overpricing index
implied vol.

**Risks:**
- Complex multi-leg execution
- Requires significant capital for multiple positions
- Correlation can break during stress events
- Advanced — not for first options mode

---

### B.6 Options Mode — Build Roadmap (If Approved)

**Phase 0: Education & Paper Trading (4-8 weeks)**
- Understand Greeks (delta, theta, gamma, vega)
- Paper trade 20+ option trades on Sensibull/Opstra
- Track results in a spreadsheet
- Understand margin requirements on Zerodha

**Phase 1: Data Pipeline (1-2 weeks)**
- Fetch NIFTY/BANKNIFTY option chain from Zerodha API
- Store in SQLite (strikes, expiries, premiums, Greeks, OI)
- Build historical option premium database

**Phase 2: Backtest Theta Selling on RANGE Days (2-4 weeks)**
- Simulate selling OTM strangles / iron condors on expiry days
- Use our regime classifier to filter only RANGE days
- Calculate P&L net of all option charges
- Walk-forward: train on year 1, test on year 2

**Phase 3: Backtest Directional Buying on VOLATILE Days (2-4 weeks)**
- Simulate buying ATM calls/puts on VOLATILE/TREND days
- Use existing NIFTY trend signal as direction indicator
- Calculate P&L net of premium decay and charges

**Phase 4: Paper Trading (4-8 weeks minimum)**
- Run strategies in paper mode with live data
- Track every trade with full audit trail
- Minimum 30 completed paper trades before live

**Phase 5: Live with Minimum Capital**
- Start with 1 lot NIFTY options only
- Maximum Rs.25K at risk per trade
- Circuit breakers and daily loss limits

**Promotion gate (same rigor as equity):**

| Metric | Target |
|---|---|
| Paper period | 30+ days |
| Completed paper trades | 30+ |
| Profit factor (after costs) | ≥ 1.20 |
| Max single-day loss | ≤ 3% of options capital |
| Win rate | ≥ 50% (for buying), ≥ 70% (for selling) |

---

### B.7 Options vs Equity Cost Comparison

| Cost Component | Equity MIS (Buy+Sell) | Option Buy (Buy+Sell) | Option Sell (Buy+Sell) |
|---|---|---|---|
| Brokerage | Rs.0 (Zerodha) | Rs.40 (Rs.20/order) | Rs.40 (Rs.20/order) |
| STT | 0.025% × turnover (both sides) | 0.0625% × premium (sell side only) | 0.0625% × premium (sell side only) |
| Exchange txn | 0.00345% × turnover | 0.05% × premium | 0.05% × premium |
| GST | 18% on (brokerage + exchange + SEBI) | 18% on (brokerage + exchange + SEBI) | 18% on (brokerage + exchange + SEBI) |
| SEBI | Rs.10/crore | Rs.10/crore | Rs.10/crore |
| Stamp duty | 0.003% (buy side) | 0.003% (buy side) | 0.003% (buy side) |
| **Typical total on Rs.15K trade** | **~Rs.10-15** | **~Rs.45-55** | **~Rs.45-55** |
| **Cost as % of trade** | **~0.07-0.10%** | **Depends on premium** | **Depends on premium** |

**Key insight:** Option brokerage is flat Rs.20/order (not %). So for LARGE
premium trades, cost % is low. For SMALL premium trades (OTM), cost % is high.
The advantage of options is NOT lower absolute cost — it's **defined risk** and
**leverage** (control larger notional with smaller capital).

---

## Priority Ranking Across Both Sections

| Rank | ID | Idea | Section | Effort | Expected Impact | Can Backtest? |
|---|---|---|---|---|---|---|
| 1 | A.3 | Cross-sectional momentum | INTRADAY | Low | Unknown (new signal) | Yes |
| 2 | A.2 | Gap-and-Go volume | INTRADAY | Medium | Moderate (ORB was 0.97) | Yes |
| 3 | A.6 | Prev-day H/L breakout | INTRADAY | Low | Moderate | Yes |
| 4 | A.4 | NIFTY Futures | INTRADAY | Medium | Moderate-High (cost fix) | Yes (need data) |
| 5 | A.7 | Volatility squeeze | INTRADAY | Medium | Moderate | Yes |
| 6 | A.5 | RSI divergence | INTRADAY | Medium | Moderate | Yes |
| 7 | A.8 | ML entry classifier | INTRADAY | Medium | Unknown | Yes (small sample) |
| 8 | A.1 | Order flow imbalance | INTRADAY | High | Potentially high | No (need live data) |
| 9 | B.1 | Directional option buying | OPTIONS | High | High | Needs option data |
| 10 | B.2 | Expiry day theta selling | OPTIONS | High | High | Needs option data |
| 11 | B.3 | Volatility strategies | OPTIONS | High | Medium-High | Needs option data |
| 12 | B.4 | Calendar/diagonal spreads | OPTIONS | High | Medium | Needs option data |
| 13 | B.5 | Dispersion trading | OPTIONS | Very High | Medium | Advanced |

**Recommended next step:** Backtest A.3, A.2, A.6 (all quick, existing data).
If all fail OOS → conclude intraday equity is dead → pivot to options (B.1/B.2).
