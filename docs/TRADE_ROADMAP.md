# Trading Roadmap

Last updated: 2026-06-01 (Phase 1.4 + Phase 2 done — best achievable OOS PF 1.10; 5-min/ORB-5 a dead end; still below the 1.15 gate).

## Current Posture

| Area | Status |
|---|---|
| Stage | **PHASE 1+2 DONE — best OOS PF 1.10 (VOLATILE-only regime gate), still < 1.15.** 5-min entries (OOS 0.70) and ORB-5 (OOS 0.66) are worse than 15-min; tighter timeframe adds no edge. Next lever: a *different entry signal* to stack on the regime gate (Phase 3+/6), not finer timing |
| Mode | AI mode: Gemini 2.5 Flash selects 2 trades/day from NIFTY50 |
| Run command | python main.py --mode trade --ai (use --dry-run only — not for real capital) |
| Live trading | **DO NOT GO LIVE** — Phase 0 walk-forward (2026-05-29) confirmed negative out-of-sample expectancy. Going live is expected to lose money. |
| Budget | Rs.50,000 |
| Config version | 2.0-2026-05-26-BACKTEST_OPTIMIZED |
| FY result (pre-audit) | Rs.-3,929 net on 184 trades (old NoAI baseline) |

> **Where we are → where next (plain English):** The system has no edge yet (best honest out-of-sample PF is 1.10, target is 1.15). We proved two things: (1) *which market regime* you trade matters a lot — trading only VOLATILE days lifts PF from 0.82 to 1.10; (2) trading on a *faster clock* (5-min) does **not** help — it's worse. Conclusion: we don't need a faster timeframe, we need a **genuinely different entry signal** to stack on the regime gate. **Next = Phase 3 (VWAP-trailing-stop on trend days)**, then Phase 6 pairs trading if still short. No real money until a stacked system clears 1.15 out-of-sample.

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
8. **2026-06-01**: Phase 1.4 + Phase 2 executed. Regime routing (VOLATILE-only) lifts OOS PF 0.82 → **1.10** but stays below the 1.15 gate. Fetched 2yr of 5-min candles and tested finer entries: 5-min entry timing (OOS PF **0.70**) and ORB-5 (OOS PF **0.66**) are both **worse** than 15-min. **Verdict: tighter timeframe is a dead end; the regime gate is necessary but not sufficient. The edge gap is the entry *signal*, not its resolution.** Still do not go live.

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
| 1.3 | ~~Train simple classifier (XGBoost/Random Forest) to predict regime from morning data~~ **SKIPPED** | Not needed — see rationale below | ⏭️ skipped |
| 1.4 | Backtest regime-routed strategy (out-of-sample, per Phase 0 discipline): current scorer on TREND days, skip or use different params on RANGE/VOLATILE | Measure PF improvement on held-out data | ✅ |
| 1.5 | If PF improves out-of-sample: implement regime gate in scanner. Dry-run for 5 sessions. | Deferred — 1.4 best is OOS PF 1.10, below the 1.15 gate; do not ship a live gate yet | ⏸️ |
| 1.6 | Verdict: keep/reject | ✅ — regime gate is necessary but **not sufficient**; keep the labeler, do not go live | ✅ |

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

> **Update (2026-06-01)**: 1.4 + Phase 2 are now done. Phase 2 produced no better entry (5-min and ORB-5 are both *worse* than 15-min OOS), so the stack reduces to the regime-routed 15-min scorer at **OOS PF 1.10** — below the gate. See the Phase 1.4 and Phase 2 results below.

#### Why 1.3 (ML classifier) is skipped

The original plan called for an XGBoost/Random Forest classifier to *predict* the day's regime from morning data. We are skipping it for now because:

1. **The labeling is the easy part; the edge is the hard part.** 1.1/1.2 already produce a clean, no-lookahead regime label from a deterministic morning-only rule (terciles of morning range + directional efficiency). An ML classifier would, at best, reproduce that same label more opaquely. It does not add edge — it only changes *how* the existing label is computed.
2. **The label is not the bottleneck — the per-regime PF is.** Even with a *perfect* regime oracle (the actual realized label, zero prediction error), the best single-regime PF is VOLATILE-only at 1.10 OOS / 0.98 TRAIN. That is the ceiling a classifier could chase, and it is *already below the 1.15 gate*. A model that predicts regime at 70% accuracy can only erode that 1.10, never exceed it. So no classifier — however good — rescues the edge on its own.
3. **It adds a heavy dependency and overfitting risk for no proven payoff.** XGBoost/sklearn are not current dependencies; a tuned model on ~134/190/163 days per class is small-sample and prone to in-sample overfit, exactly the failure mode Phase 0 just caught. The rule labeler is transparent, reproducible, and survives out-of-sample.
4. **Sequencing**: a classifier is only worth building *after* a regime-routed + better-entry stack is proven to clear the gate out-of-sample (Phase 1.4 + Phase 2). If the stack works, *then* a probabilistic regime estimate could squeeze marginal gains and 1.3 can be revisited. Until then it is premature optimization.

**Decision**: keep the deterministic morning-rule labeler from 1.1; spend the effort on 1.4 + Phase 2 (stacking a better entry on the regime gate) instead. Revisit 1.3 only if the stacked system clears 1.15 OOS and a probabilistic regime call would add measurable lift.

#### Phase 1.4 Results (2026-06-01)

Phase 1.4 was meant to stack the regime gate on top of *Phase 2's better entry*. Phase 2 produced **no** better entry (5-min and ORB-5 are both worse than 15-min — see Phase 2 results), so 1.4 reduces to the regime-routed **15-min** scorer, measured out-of-sample via [scripts/trade/regime_analysis.py](../scripts/trade/regime_analysis.py) `--window TEST`:

| Routing rule | OOS PF | OOS exp% | OOS Sharpe | Trades/yr |
|---|--:|--:|--:|--:|
| Trade ALL regimes (baseline) | 0.82 | −0.068 | −1.81 | 468 |
| Skip RANGE | 0.96 | −0.014 | −0.26 | 262 |
| **VOLATILE only (best)** | **1.10** | **+0.039** | **+0.38** | **98** |

**Verdict — FAIL the promotion gate, but the regime signal is real.** The best routed config (VOLATILE-only) is **OOS PF 1.10, +0.039%/trade, Sharpe 0.38** — marginally positive but **below the 1.15 gate**, on a thin 98-trade/yr sample (<1 trade/day) and only 0.98 in TRAIN. Routing lifts OOS PF a full 0.28 (0.82 → 1.10), which **confirms regime is the #1 edge lever**, but it is **necessary, not sufficient**. The gate cannot be cleared by routing the *existing* entry — it needs a genuinely *different* entry signal to route into VOLATILE days (Phase 3 VWAP-trail, Phase 4 order flow, or Phase 6 new strategy). **Do not implement a live regime gate (1.5) or go live until a stacked system clears 1.15 OOS.**

---

### Phase 2: Quick Wins (No New Strategy Code)
**Goal**: Squeeze more from the existing system using data we already have. Cheap, can run alongside Phase 1 research.
**Trigger**: After Phase 1 verdict (or in parallel).

| Step | Action | Rationale | Done? |
|---|---|---|---|
| 2.1 | **Switch scanner to 5-min candles** for entry timing (keep 15-min for pre-market scan) | Tighter entries = smaller SL = better R:R. Free — data already available via Zerodha API. | ✅ |
| 2.2 | Backtest 5-min entry vs 15-min entry on same period (out-of-sample) | Measure PF delta | ✅ |
| 2.3 | **ORB-5 backtest** — rerun ORB strategy on 5-min opening range (was PF 0.97 on 15-min) | Closest strategy to profitable. Tighter timeframe + volume filter may cross 1.0. | ✅ |
| 2.4 | Verdict on each: keep/reject by out-of-sample backtest evidence | ✅ — both rejected | ✅ |

**Exit criteria**: Backtest verdict on 5-min entries and ORB-5.

#### Phase 2 Results (2026-06-01) — REJECTED, finer timeframe adds no edge

Fetched 2 years of NIFTY50 **5-min** candles (1,846,189 rows, `intraday_5m.sqlite`, LFS) via the paid Zerodha API ([scripts/trade/fetch_backtest_candles.py](../scripts/trade/fetch_backtest_candles.py) `--interval 5minute`). All backtests OOS (TRAIN yr1 / TEST yr2), net of cost via the canonical `Config.calculate_charges`.

**2.1 / 2.2 — 5-min entry timing** ([scripts/trade/walk_forward_5m.py](../scripts/trade/walk_forward_5m.py), same frozen config as 15-min walk-forward):

| Window | 5-min PF | 15-min PF (baseline) |
|---|--:|--:|
| FULL (in-sample) | 0.70 | 0.86 |
| TRAIN (yr1) | 0.71 | 0.89 |
| **TEST (yr2, OOS)** | **0.70** | **0.82** |

The 5-min entry is **worse** out-of-sample (0.70 vs 0.82) and *stable* (train→test drift +0.01) — a consistent negative edge, not bad luck. Finer timeframe → ~2× the trades (468 → 982) → more noise and more cost drag with no edge gain.

**2.3 — ORB-5** ([scripts/trade/backtest_orb5.py](../scripts/trade/backtest_orb5.py), 5-min opening range 09:15–09:20):

| Window | ORB-5 net PF | ORB-15 net PF |
|---|--:|--:|
| TRAIN (yr1) | 1.02 | — |
| **TEST (yr2, OOS)** | **0.66** | ~0.97 |
| FULL (2yr) | 0.82 | — |

ORB-5 collapses out-of-sample (TRAIN 1.02 → TEST 0.66) — classic overfit, and **worse** than ORB-15's ~0.97. The tighter opening range did not cross 1.0.

**2.4 — VERDICT: both rejected.** Tighter entry timing is a **dead end**. More candles buy more noise and cost drag, never more edge. The 15-min timeframe remains the best available. **The edge gap is the entry *signal*, not its *resolution*** — confirming the Phase 1.4 conclusion that a genuinely different setup (Phase 3/4/6) is required, not finer timing. Keep all 15-min infra; the 5-min store stays for any future signal that genuinely needs sub-15-min resolution.

---

### Phase 3: VWAP-as-Trailing-Stop (Trend Days Only)
**Goal**: Test VWAP twist — use VWAP as trailing stop on trend trades instead of MR target.
**Trigger**: After Phase 2 verdict. **← THIS IS THE ACTIVE NEXT STEP (2026-06-01).** Phase 1+2 proved the regime gate is the only working lever so far (OOS PF 1.10) and that finer timeframes are a dead end — so the next move is a *different exit/entry signal*, not more timing. VWAP-trail is the cheapest such signal (data already on hand, no new deps), tested specifically on the TREND/VOLATILE regimes where the scorer already holds up.

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
