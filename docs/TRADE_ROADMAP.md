# Trading Roadmap

Last updated: 2026-05-26.

## Current Posture

| Area | Status |
|---|---|
| Stage | **BACKTEST_OPTIMIZED** (62-gate backtest audit, 2026-05-26) |
| Mode | AI mode: Gemini 2.5 Flash selects 2 trades/day from NIFTY50 |
| Run command | python main.py --mode trade --ai |
| Live trading | ENABLED (TRADE_LIVE_TRADING_PAUSED = False) |
| Budget | Rs.50,000 |
| Config version | 2.0-2026-05-26-BACKTEST_OPTIMIZED |
| FY result (pre-audit) | Rs.-3,929 net on 184 trades (old NoAI baseline) |

## What Happened

1. **2026-03 to 2026-05-15**: NoAI mode with 60+ gates. PF 0.71, losing money.
2. **2026-05-15**: Chan-framework audit. Paused live trading for research reset.
3. **2026-05-25-26**: 62-gate backtest audit. Swept every parameter. PF 0.71 to 0.86.
4. **2026-05-26**: Switched to AI mode (Gemini). K1=2 daily cap. Ready for live.
5. **2026-05-26**: Edge-gap audit completed. Diagnosed: no regime awareness, cost drag, single strategy. Created 7-phase execution plan.

## Key Config (Post-Audit)

| Parameter | Value | Evidence |
|---|---|---|
| ATR multiplier | 2.0 | Backtest E1: best per-trade expectancy |
| R:R target | 1.8:1 | Backtest E1: practical optimum |
| R:R floor | 1.3:1 | Uniform all day |
| Daily trade cap | 2 | Backtest K1: PF 0.81 vs 0.71 |
| Square-off | 14:00 IST | Backtest L11: 14:00 optimal |
| Loser exit | 13:00 IST | Backtest L10: marginal benefit |
| SL range | 0.8% - 2.5% | Min floor prevents whipsaw |
| Entry floor | 9:30 IST (15min after open) | Avoids opening volatility |
| Signal reversal exit | Enabled (score >= 7 + pattern) | Pro decision |
| Consecutive SL pause | 3 losses -> 30 min | Pro decision |

## Execution Plan (Phased)

**Philosophy**: One change at a time. Each phase follows: Plan → Backtest → Decide → Live/Dry-Run → Measure. No phase starts until the previous one has a verdict.

See [audit/TRADE_AUDIT_2026-05-26_EDGE_GAP.md](audit/TRADE_AUDIT_2026-05-26_EDGE_GAP.md) for full edge-gap analysis.

---

### Phase 0: Baseline Live Runs (Current Strategy, PF 0.86)
**Goal**: Collect real-market AI-mode data. This is the control group.
**Status**: READY — starts 2026-05-27.

| Step | Action | Done? |
|---|---|---|
| 0.1 | Run `python main.py --mode trade --ai` live, Rs.50K, K1=2, sq-off 14:00 | |
| 0.2 | Accumulate 10+ sessions (~20+ trades) | |
| 0.3 | Run `promotion_check.py --window 20` — record baseline PF, WR, expectancy | |
| 0.4 | Verdict: if PF >= 1.15, proceed to capital scaling. If PF < 1.0, proceed to Phase 1. | |

**Exit criteria**: 20+ trades with live AI-mode metrics recorded.

---

### Phase 1: Quick Wins (No New Strategy Code)
**Goal**: Squeeze more from the existing system using data we already have.
**Trigger**: After Phase 0 baseline is recorded.

| Step | Action | Rationale | Done? |
|---|---|---|---|
| 1.1 | **Switch scanner to 5-min candles** for entry timing (keep 15-min for pre-market scan) | Tighter entries = smaller SL = better R:R. Free — data already available via Zerodha API. | |
| 1.2 | Backtest 5-min entry vs 15-min entry on same period | Measure PF delta | |
| 1.3 | **ORB-5 backtest** — rerun ORB strategy on 5-min opening range (was PF 0.97 on 15-min) | Closest strategy to profitable. Tighter timeframe + volume filter may cross 1.0. | |
| 1.4 | Verdict on each: keep/reject by backtest evidence | |

**Exit criteria**: Backtest verdict on 5-min entries and ORB-5.

---

### Phase 2: Regime Classifier
**Goal**: Stop running one strategy in all conditions. Route trades to the right approach.
**Trigger**: After Phase 1 verdicts.

| Step | Action | Rationale | Done? |
|---|---|---|---|
| 2.1 | Build regime labeler: classify each historical day as TREND / RANGE / VOLATILE using first-30-min VIX + ADX + gap% + breadth | The #1 diagnosed edge gap. Three strategies failed because they ran in wrong regimes. | |
| 2.2 | Analyze historical PF of current scorer broken down by regime | Quantify how much PF varies by regime | |
| 2.3 | Train simple classifier (XGBoost/Random Forest) to predict regime from morning data | Targets ~70% accuracy — even 60% helps | |
| 2.4 | Backtest regime-routed strategy: current scorer on TREND days, skip or use different params on RANGE/VOLATILE | Measure PF improvement | |
| 2.5 | If PF improves: implement regime gate in scanner. Dry-run for 5 sessions. | |
| 2.6 | Verdict: keep/reject | |

**Exit criteria**: Regime classifier either improves PF by >= 0.1 or is rejected.

---

### Phase 3: VWAP-as-Trailing-Stop (Trend Days Only)
**Goal**: Test VWAP twist — use VWAP as trailing stop on trend trades instead of MR target.
**Trigger**: After Phase 2 verdict.

| Step | Action | Rationale | Done? |
|---|---|---|---|
| 3.1 | Backtest: on TREND-regime days, replace fixed ATR target with VWAP trail | FIIs anchor to VWAP; price holding above VWAP post-10AM has strong follow-through on NSE | |
| 3.2 | Compare PF, avg winner size, avg hold time vs fixed target | |
| 3.3 | If better: dry-run for 5 sessions | |
| 3.4 | Verdict: keep/reject | |

**Exit criteria**: VWAP trail verdict on trend-day trades.

---

### Phase 4: Order Flow Data (External Data Source)
**Goal**: Add bid-ask imbalance as an entry quality signal.
**Trigger**: After Phase 3 verdict (only if still sub-1.15 PF).

| Step | Action | Rationale | Done? |
|---|---|---|---|
| 4.1 | Subscribe to TrueData or GlobalDataFeeds (~₹2K/mo) for Level-2 order book | Order flow imbalance (OFI) is the strongest short-term predictive signal per academic research | |
| 4.2 | Collect 2 weeks of OFI data alongside live trades (log only, no action) | |
| 4.3 | Analyze correlation between OFI at entry time and trade outcome | |
| 4.4 | If correlated: build OFI gate (reject entries where flow is against direction) | |
| 4.5 | Backtest + dry-run for 5 sessions | |
| 4.6 | Verdict: keep/reject | |

**Exit criteria**: OFI signal either improves PF or is rejected. Cancel TrueData if rejected.

---

### Phase 5: Optuna Auto-Tuning
**Goal**: Replace manual parameter sweeps with systematic Bayesian optimization.
**Trigger**: After Phase 4 verdict.

| Step | Action | Rationale | Done? |
|---|---|---|---|
| 5.1 | Define search space: ATR multiplier (1.0-3.0), R:R (1.0-3.0), SL% (0.5-3.0), sq-off time, K1 cap | Current 62-gate sweep was manual; Optuna finds interactions | |
| 5.2 | Run Optuna with walk-forward cross-validation (not single-period) to avoid overfit | |
| 5.3 | Compare Optuna-optimal params vs current params on held-out period | |
| 5.4 | Verdict: adopt new params or keep current | |

**Exit criteria**: Optuna either finds better params on held-out data or confirms current params are near-optimal.

---

### Phase 6: New Strategy (Only if PF still < 1.15)
**Goal**: Add a second strategy to the system (market-neutral or options-based).
**Trigger**: Only if Phases 1-5 have not achieved promotion-gate PF.

| Candidate | Prerequisites |
|---|---|
| Pairs trading (HDFCBANK/ICICIBANK, TCS/INFY) | Hedge-ratio modeling, two-leg cost calc |
| Expiry-day options selling (Thursday NIFTY/BANKNIFTY) | Options infra, Sensibull/Opstra integration |
| Intraday momentum (first-half → last-half) | Simple; can backtest on existing candle data |

**Decision on which candidate**: Made after Phase 5 based on what the data shows.

---

## Candidate Strategies (Backtested 2026-05-25)

| Strategy | Result | Verdict |
|---|---|---|
| VWAP Mean-Reversion | PF 0.80, WR 23% | FAIL - do not enable |
| ORB-15 Breakout | PF 0.97, WR 55.7% | MARGINAL - needs tighter SL / 5-min retest (Phase 1) |
| EMA Pullback | PF 0.65 after costs | FAIL - edge does not survive costs |

See [TRADE_REVAMP_STRATEGIES.md](TRADE_REVAMP_STRATEGIES.md) for full backtest data.

## Ground Rules

1. Gates enabled/disabled by backtest evidence only
2. No capital scaling until promotion metrics pass after costs
3. Each strategy tested separately before blending
4. AI selection is the quality gate, not proof of edge by itself
5. Evidence starts fresh from the first AI-mode live session
6. **One phase at a time. No skipping. No parallel experiments.**
7. **Each phase has a binary verdict (keep/reject) before moving on.**
8. **Minimum 5 dry-run sessions before any change goes live.**

## Promotion Gate

| Metric | Required |
|---|---:|
| Profit factor | >= 1.15 after costs |
| Expectancy | >= Rs.10/trade |
| Win rate | >= 40% |
| Profitable-day rate | >= 55% |
| Max drawdown | <= 3% of daily capital |
| Sample | >= 10 sessions, >= 20 trades |

## Deferred Work

| Feature | Reason |
|---|---|
| Score-weighted sizing | Anti-correlated with P&L at current edge |
| Budget regime deltas | Not needed at Rs.50K single tier |
| HFT/WebSocket | Speed does not fix a losing strategy |
| Pairs/stat-arb | Deferred to Phase 6 if needed |
| Options strategies | Deferred to Phase 6 if needed |
