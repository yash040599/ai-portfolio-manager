# Trading Roadmap

Last updated: 2026-05-29 (Phase 0 FAILED; Phase 1.1/1.2 done — regime is a real but insufficient signal).

## Current Posture

| Area | Status |
|---|---|
| Stage | **PHASE 1 IN PROGRESS** — OOS PF 0.82. Regime routing lifts OOS PF to 1.10 (VOLATILE-only) but straddles breakeven; not yet at 1.15 gate |
| Mode | AI mode: Gemini 2.5 Flash selects 2 trades/day from NIFTY50 |
| Run command | python main.py --mode trade --ai (use --dry-run only — not for real capital) |
| Live trading | **DO NOT GO LIVE** — Phase 0 walk-forward (2026-05-29) confirmed negative out-of-sample expectancy. Going live is expected to lose money. |
| Budget | Rs.50,000 |
| Config version | 2.0-2026-05-26-BACKTEST_OPTIMIZED |
| FY result (pre-audit) | Rs.-3,929 net on 184 trades (old NoAI baseline) |

> **Why not live?** Phase 0 walk-forward validation (2026-05-29) measured the
> frozen audit config on a held-out year it was never tuned on: **out-of-sample
> PF 0.82, expectancy -0.068%/trade**. The 0.86 figure came from optimizing 62
> gates *on the same data it was measured on* (in-sample). The true edge is
> negative in every window except one (2024-H2). A sub-1.0 system has negative
> expectancy — risking capital is expected to lose money. See Phase 0 results below.

## What Happened

1. **2026-03 to 2026-05-15**: NoAI mode with 60+ gates. PF 0.71, losing money.
2. **2026-05-15**: Chan-framework audit. Paused live trading for research reset.
3. **2026-05-25-26**: 62-gate backtest audit. Swept every parameter. PF 0.71 to 0.86.
4. **2026-05-26**: Switched to AI mode (Gemini). K1=2 daily cap. Ready for live.
5. **2026-05-26**: Edge-gap audit completed. Diagnosed: no regime awareness, cost drag, single strategy. Created 7-phase execution plan.
6. **2026-05-26b**: Code review pass. Fixed a real bug — the expiry daily-cap (`EXPIRY_MAX_TRADES_PER_DAY=0`) was silently *disabling* the K1 cap on expiry Thursdays (unlimited trades). Now regression-tested ([tests/test_expiry_cap.py](../tests/test_expiry_cap.py)). Stale docs (square-off, loser-exit, trade-cap values) corrected. Forward plan reworked to gate live trading behind out-of-sample validation.
7. **2026-05-29**: Phase 0 executed. Cost model reconciled to a single source of truth. Walk-forward validation ran ([scripts/trade/walk_forward.py](../scripts/trade/walk_forward.py)): **out-of-sample PF 0.82, negative expectancy → VERDICT FAIL, do not go live.** Proceeding to Phase 1 (regime classifier).

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

**Philosophy**: One change at a time. Each phase follows: Plan → Backtest → Decide → Dry-Run/Replay → Measure. No phase starts until the previous one has a verdict. **No real capital is risked until Phase 0 validation clears the promotion gate out-of-sample.**

See [audit/TRADE_AUDIT_2026-05-26_EDGE_GAP.md](audit/TRADE_AUDIT_2026-05-26_EDGE_GAP.md) for full edge-gap analysis.

### The forward plan in one paragraph

The 0.86 PF is in-sample and untrustworthy, so **the next milestone is not "go live" — it is "prove the edge survives out-of-sample."** Phase 0 splits history into a train window and an untouched test window (walk-forward), and additionally runs a dry-run/replay forward-test on recent unseen sessions. Capital is risked only if **both** the out-of-sample backtest **and** the dry-run forward-test clear PF >= 1.15 after real Zerodha costs. In parallel, Phase 0 verifies the cost model matches the actual Zerodha charge sheet to the paisa — because cost drag is the #1 diagnosed cause of PF < 1, a wrong cost model invalidates every backtest number. If validation fails (the likely outcome), we go straight to the highest-leverage fix — the **regime classifier (was Phase 2, now Phase 1)** — because the single biggest diagnosed gap is running one strategy in all market conditions. Cheap, data-free wins (5-min entries, ORB-5 retest) ride alongside. Microstructure/order-flow, Optuna, and new strategies (pairs trading is the most promising differentiated edge) stay deferred until the core system is provably positive.

---

### Phase 0: Out-of-Sample & Walk-Forward Validation (NO REAL CAPITAL)
**Goal**: Find the *true* edge of the current PF-0.86 system on data it was never tuned on. This is the gate that decides whether the system is allowed to touch real money at all.
**Status**: **DONE — VERDICT: FAIL. DO NOT GO LIVE.** (2026-05-29)

| Step | Action | Done? |
|---|---|---|
| 0.1 | **Audit the cost model**: reconcile the backtester's per-trade charge against the live Zerodha contract note (brokerage, STT, exchange txn, GST, SEBI, stamp). Fix any mismatch before trusting any PF number. | ✅ |
| 0.2 | **Split history**: pick a train window and a strictly later, untouched test window. Re-derive the gate parameters on train only. | ✅ |
| 0.3 | **Walk-forward test**: measure PF / WR / expectancy on the test window with frozen params. Record the out-of-sample PF (this is the real number). | ✅ |
| 0.4 | **Dry-run / replay forward-test**: run `python main.py --mode trade --ai --dry-run` (or replay) over 10+ recent unseen sessions. Record live-path PF after real costs. | ⏭️ mooted — 0.3 already failed |
| 0.5 | **Verdict**: capital is risked ONLY if BOTH the out-of-sample backtest AND the dry-run forward-test clear **PF >= 1.15 after costs**. Otherwise → Phase 1 (regime classifier). | ✅ → Phase 1 |

**Exit criteria**: A recorded out-of-sample PF and a dry-run forward-test PF, with a binary go-live / iterate verdict. **No live capital before this passes.**

#### Phase 0 Results (2026-05-29)

- **0.1 — Cost model reconciled.** The backtester had a *duplicate* charge model that had drifted from the canonical `Config.calculate_charges` (exchange txn 0.00345% vs config 0.00307%; GST base omitted SEBI). Impact was tiny (~Rs.0.13/trade) and **conservative** (backtest over-charged), so PF 0.86 is *not* inflated by understated costs. Fixed: [scripts/trade/backtest_gates.py](../scripts/trade/backtest_gates.py) `compute_charges()` now delegates to `Config.calculate_charges` — single source of truth, no future drift. Config rates verified against current Zerodha equity-intraday rates (slightly conservative). No real contract notes exist yet to reconcile against (no live trades).
- **0.2 / 0.3 — Walk-forward run** via [scripts/trade/walk_forward.py](../scripts/trade/walk_forward.py) on 2024-05-27 → 2026-05-22 (50-symbol NIFTY50, frozen audit config, net of cost):

| Window | Trades | WR% | PF | Exp% | Ret% | MaxDD | Sharpe |
|---|--:|--:|--:|--:|--:|--:|--:|
| FULL (in-sample ref) | 970 | 37.8 | 0.86 | -0.057 | -55.4 | 71.3 | -1.20 |
| TRAIN (yr1) | 498 | 37.1 | 0.89 | -0.051 | -25.4 | 40.1 | -0.93 |
| **TEST (yr2, OOS)** | **468** | **38.2** | **0.82** | **-0.068** | **-31.7** | **34.7** | **-1.81** |
| 2024-H2 | 294 | 38.8 | 1.02 | +0.01 | +3.0 | 35.3 | +0.17 |
| 2025-H1 | 242 | 35.5 | 0.74 | -0.118 | -28.6 | 38.4 | -2.70 |
| 2025-H2 | 246 | 39.8 | 0.81 | -0.061 | -15.0 | 19.2 | -1.93 |
| 2026-H1 | 176 | 38.1 | 0.86 | -0.061 | -10.8 | 17.5 | -1.31 |

- **0.5 — VERDICT: FAIL.** Out-of-sample PF **0.82** with **negative expectancy** (-0.068%/trade). The PF is "stable" only in the sense that it is *consistently below 1.0* — the system loses money in every window except the single profitable patch (2024-H2, PF 1.02) the audit already flagged. **A PF-0.82 system is negative expectancy: going live is expected to lose money.** Skip the dry-run (it cannot rescue a negative edge) and proceed to **Phase 1 (Regime Classifier)** — the highest-leverage fix.

---

### Phase 1: Regime Classifier (highest-leverage fix)
**Goal**: Stop running one strategy in all conditions. Route trades to the right approach. This was the #1 diagnosed edge gap, so it moves ahead of everything else if Phase 0 fails.
**Trigger**: After Phase 0 verdict (or in parallel research while Phase 0 dry-run accumulates).

| Step | Action | Rationale | Done? |
|---|---|---|---|
| 1.1 | Build regime labeler: classify each historical day as TREND / RANGE / VOLATILE using first-30-min VIX + ADX + gap% + breadth | The #1 diagnosed edge gap. Three strategies failed because they ran in wrong regimes. | ✅ |
| 1.2 | Analyze historical PF of current scorer broken down by regime | Quantify how much PF varies by regime | ✅ |
| 1.3 | Train simple classifier (XGBoost/Random Forest) to predict regime from morning data | Targets ~70% accuracy — even 60% helps | |
| 1.4 | Backtest regime-routed strategy (out-of-sample, per Phase 0 discipline): current scorer on TREND days, skip or use different params on RANGE/VOLATILE | Measure PF improvement on held-out data | |
| 1.5 | If PF improves out-of-sample: implement regime gate in scanner. Dry-run for 5 sessions. | |
| 1.6 | Verdict: keep/reject | |

**Exit criteria**: Regime classifier either improves out-of-sample PF by >= 0.1 or is rejected.

#### Phase 1.1 / 1.2 Results (2026-05-29)

Built [scripts/trade/regime_analysis.py](../scripts/trade/regime_analysis.py). No NIFTY index / VIX exists in the data, so regimes are labeled from a **synthetic equal-weight market proxy** built from the 50 constituents, using **morning-only** features (first 6×15-min bars ≈ 09:15–10:45 — live-realizable, no lookahead): gap%, morning realized range (VIX proxy), breadth, directional efficiency. Terciles → TREND (27.5% of days), RANGE (39.0%), VOLATILE (33.5%).

Scorer PF by regime, **net of cost**, frozen audit config:

| Regime | TRAIN PF | TEST (OOS) PF | TEST trades | TEST exp% |
|---|--:|--:|--:|--:|
| TREND | 0.73 | 0.88 | 164 | −0.046 |
| RANGE | 0.79 | **0.62** | 206 | −0.136 |
| VOLATILE | 0.98 | **1.10** | 98 | +0.039 |
| ALL (baseline) | 0.89 | 0.82 | 468 | −0.068 |

Out-of-sample routing scenarios:

| Rule | TEST PF | TEST exp% | TEST Sharpe | Trades |
|---|--:|--:|--:|--:|
| Trade ALL regimes | 0.82 | −0.068 | −1.81 | 468 |
| Skip RANGE | 0.96 | −0.014 | −0.26 | 262 |
| VOLATILE only | **1.10** | **+0.039** | **+0.38** | 98 |

**Verdict — regime is a real, stable signal, but routing alone does not clear the gate.**
- **Stable rank in every window**: VOLATILE best, RANGE worst. RANGE days (39% of days) are where the system bleeds (PF 0.62–0.79). This validates the #1 diagnosed edge gap.
- **Routing materially lifts PF**: 0.82 → 0.96 (skip RANGE) → 1.10 (VOLATILE-only) out-of-sample.
- **But it is not sufficient.** VOLATILE-only is PF 1.10 OOS yet only **0.98 in TRAIN** — it straddles breakeven, is well short of the 1.15 promotion gate, and is a thin sample (98 trades/yr ≈ <1/day). A regime gate is **necessary but not sufficient**: it must be stacked with a better entry signal (Phase 2 quick-wins / different setup), not shipped alone.
- **Next (1.3/1.4)**: rather than a heavy XGBoost classifier, the morning-only rule labeler already works — proceed to 1.4 and backtest the routed strategy OOS *combined with* Phase 2's 5-min/ORB-5 entries to see if the stack crosses 1.15. Do not implement a live regime gate (1.5) until the combined system clears the gate out-of-sample.

---

### Phase 2: Quick Wins (No New Strategy Code)
**Goal**: Squeeze more from the existing system using data we already have. Cheap, can run alongside Phase 1 research.
**Trigger**: After Phase 1 verdict (or in parallel).

| Step | Action | Rationale | Done? |
|---|---|---|---|
| 2.1 | **Switch scanner to 5-min candles** for entry timing (keep 15-min for pre-market scan) | Tighter entries = smaller SL = better R:R. Free — data already available via Zerodha API. | |
| 2.2 | Backtest 5-min entry vs 15-min entry on same period (out-of-sample) | Measure PF delta | |
| 2.3 | **ORB-5 backtest** — rerun ORB strategy on 5-min opening range (was PF 0.97 on 15-min) | Closest strategy to profitable. Tighter timeframe + volume filter may cross 1.0. | |
| 2.4 | Verdict on each: keep/reject by out-of-sample backtest evidence | |

**Exit criteria**: Backtest verdict on 5-min entries and ORB-5.

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
5. **No real capital until Phase 0 out-of-sample validation clears the promotion gate.** In-sample PF does not count.
6. **All backtest PF claims must be out-of-sample / walk-forward** — never measured on the same window that set the parameters.
7. **One phase at a time. No skipping. No parallel experiments** (research that risks no capital may overlap).
8. **Each phase has a binary verdict (keep/reject) before moving on.**
9. **Minimum 5 dry-run sessions before any change goes live.**

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
