# Trade Next Ideas — Intraday & Options Research

> **Created:** 2026-06-06 | **Updated:** 2026-06-09
> **Status:** Gap-and-Go **v1.1** passes OOS PF 1.55, Sharpe 1.66. Dry-run
> validation started (v1.0 failed day 1; v1.1 fixes deployed).
> **Context:** Phases 0-7 of intraday equity backtesting + Phase 8 (v1.1
> hardening) are complete. All other intraday ideas deferred until v1.1
> dry-run validation (10+ sessions) completes.

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

### A.2 Gap-and-Go with Volume Qualification

**What:** Dedicated strategy for stocks that gap >1% on open with >2x average
volume. Enter in gap direction within first 15 min, target 50-100% of gap
continuation.

**Why it might work:** Our ORB-15 was PF 0.97 — the closest any strategy came
to breakeven. Gap-and-go is the same family but adds a **volume filter** that
should filter out false breakouts. Academic evidence (Caginalp & Laurent 1998)
supports gap follow-through when accompanied by volume.

**How to integrate:**
- Regime-routed: only active on TREND + VOLATILE days (skip RANGE)
- Reuses existing gap-analysis indicator (+1 score today) but makes it primary
- Volume filter: first-15-min volume > 2x same-period 20-day average
- SL: below gap candle low (for BUY gaps)
- Target: 50-100% of gap size beyond the open

**Effort:** Medium — can reuse existing candle data + regime labels.

**Backtest feasibility:** Fully backtestable with existing 15-min data.

**Expected impact:** Moderate — ORB-15 was 0.97 so this COULD cross 1.0 with a
better filter. But even 1.0 is below the 1.15 gate.

---

### A.3 Cross-Sectional Momentum (Rank-Based Entry)

**What:** Instead of scoring each stock independently, RANK all NIFTY50 stocks
by first-15-min return. Buy the top 2 strongest. This exploits relative
strength, not absolute score.

**Why it might work:** Our current scorer evaluates each stock in isolation.
Cross-sectional momentum (Jegadeesh & Titman) shows that relative outperformers
persist for 1-3 hours intraday. The stock that's #1 at 9:30 has elevated
probability of staying strong until 12:00.

**How to integrate:**
- At 9:30, compute first-15-min return for all 50 stocks
- Rank by return, pick top 2 (BUY) or bottom 2 (SELL)
- No technical scoring needed — pure momentum ranking
- Still apply safety gates (spread, impact cost, circuit, budget)

**Effort:** Low — simple to code, fully backtestable.

**Backtest feasibility:** Fully backtestable with existing 15-min data.

**Expected impact:** Unknown — this is a fundamentally different signal family.
Worth backtesting as it has zero overlap with current approach.

---

### A.4 NIFTY Futures (Single Instrument, Zero STT)

**What:** Instead of trading 50 individual stocks, trade NIFTY50 futures only.

**Why it might work:**
- **Zero STT** for intraday futures (CTT is 0.01% vs equity STT 0.025%/side)
- Single instrument eliminates stock-specific risk, sector analysis, spread
  concerns across 50+ symbols
- Our NIFTY trend signal is already built and working
- Much lower total cost per round-trip

**How to integrate:**
- Reuse NIFTY trend signal from scanner (already tracking NIFTY ADX, EMA,
  direction)
- Add futures product type (NRML/MIS) to ZerodhaClient
- Single instrument means much simpler monitoring loop
- Same regime gate applies (VOLATILE-only or skip-RANGE)

**Effort:** Medium — needs futures support in Zerodha client + separate
backtest on NIFTY futures data.

**Backtest feasibility:** Need NIFTY futures historical data (available free
from Zerodha API).

**Expected impact:** Moderate-High — cost reduction alone could flip PF from
0.82 → potentially above 1.0. The question is whether the NIFTY trend signal
has enough edge.

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

### A.6 Previous Day High/Low Breakout

**What:** Dedicated strategy for breakout of previous day's high or low level.
One of the oldest and most robust intraday signals globally.

**How to integrate:**
- Our scanner already tracks "Prev-Day S&R" (±0.5 to ±1 score) but as one of
  14 factors
- Make it primary: BUY when price breaks above yesterday's high with volume
  confirmation
- SELL when price breaks below yesterday's low
- ADX >25 filter (breakout needs trend strength — our audit showed ADX flat
  with K1=2, may help here)
- Regime-routed: TREND days only

**Effort:** Low — simple to code, fully backtestable.

**Backtest feasibility:** Fully backtestable with existing daily + 15-min data.

**Expected impact:** Moderate — well-documented edge in academic literature but
may be arbitraged in NIFTY50 liquid names.

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

**Honest verdict after Phases 0-6:**

| What We Tried | Result |
|---|---|
| 62-gate backtest optimization | PF 0.71 → 0.86 (still <1.0) |
| Walk-forward OOS validation | PF 0.82 (negative expectancy) |
| Regime classification + routing | VOLATILE-only PF 1.10 (best, <1.15 gate) |
| 5-min timeframe | PF 0.70 (worse) |
| ORB-5 | PF 0.66 (overfit collapse) |
| VWAP trailing stop | PF 0.78 (clips winners) |
| Intraday pairs trading | PF 0.48-0.69 (horizon mismatch) |
| VWAP mean-reversion | PF 0.80 (doesn't work intraday NSE) |
| EMA pullback | PF 0.65 (edge doesn't survive costs) |
| ORB-15 breakout | PF 0.97 (closest, still not enough) |

**Is intraday equity conclusively dead?**

Not 100% — but the remaining levers (A.1-A.8 above) are increasingly
speculative. The strongest untested ideas are:

1. **OFI (A.1)** — but requires paid data and can't be backtested historically
2. **NIFTY Futures (A.4)** — cost reduction alone may flip the PF
3. **Cross-sectional momentum (A.3)** — different signal family, quick to test
4. **Gap-and-Go (A.2)** — ORB-15 was 0.97, this could push it over

**The structural problem remains:** Equity MIS on NSE has ~0.05-0.10%
round-trip costs. At Rs.15K/trade, that's Rs.8-15 per trade. With typical
winner size of Rs.150-300, costs eat 5-10% of every winner. This cost floor is
fixed by regulation and cannot be optimized away.

**Recommendation:** Backtest A.2 (gap-and-go), A.3 (cross-sectional), and A.6
(prev-day breakout) — they're quick, use existing data, and test genuinely
different signals. If all three fail OOS, the intraday equity tool is
conclusively dead at this capital level and cost structure.

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
