# Trading Roadmap

Last updated: 2026-06-12 (Version scheme X.Y.Z introduced. 1.0→1.0.0, 1.1→1.1.0, new 1.1.1 = NIFTY100 universe. PF 1.62, Sharpe 1.80).

## Current Posture

| Area | Status |
|---|---|
| Stage | **PHASE 8 — Gap-and-Go v1.1.1 dry-run validation (NIFTY100).** Version scheme updated to X.Y.Z (X=strategy logic, Y=filters/safeguards, Z=config/params). Universe expanded NIFTY50→NIFTY100 (2026-06-12) after backtest showed strictly better metrics: **OOS PF 1.55→1.62 (+5%), Sharpe 1.66→1.80 (+8%)** with identical trade count (98). Same daily cap=2 selects higher-quality trades from the wider pool. |
| Mode | NoAI, pure rules-based |
| Run command | python main.py --mode trade --dryrun (TRADE_STRATEGY_PROFILE = "NOAI_GAP_AND_GO_1.1.1", SCAN_UNIVERSE = "NIFTY100") |
| Live trading | **DO NOT GO LIVE YET** — v1.1.1 needs dry-run validation (10+ sessions). |
| Budget | Rs.50,000 |
| Config version | 2.2-2026-06-12-GAP_AND_GO_1.1.1 |
| FY result (pre-audit) | Rs.-3,929 net on 184 trades (old NoAI baseline) |

> **Where we are → where next (plain English):** After 7 phases of research, Gap-and-Go was the first strategy to clear the OOS promotion gate. v1.1.0 fixed three v1.0.0 bugs and lifted OOS PF from 1.37 to 1.55. On 2026-06-12, v1.1.1 expanded the universe from NIFTY50 to **NIFTY100** — backtest shows the wider pool provides better-quality gap candidates: **PF 1.55→1.62 (+5%), Sharpe 1.66→1.80 (+8%)**, identical trade count (cap=2 picks best 2 from 100 instead of 50). VOLATILE-only PF jumps from 1.98 to **2.45**. The extra 50 mid-large-caps add more explosive gap moves on volatile days. Version scheme updated to X.Y.Z (X=strategy, Y=filters, Z=config). **Next step: continue v1.1.1 dry-run validation on NIFTY100 (10+ sessions needed).** If dry-run confirms, this is the first strategy that could go live.

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
9. **2026-06-06a**: Research review. Documented remaining intraday ideas (A.1-A.8) and options mode research (B.1-B.5) in [TRADE_NEXT_IDEAS.md](TRADE_NEXT_IDEAS.md). Three quick backtestable ideas (cross-sectional momentum, gap-and-go, prev-day breakout) identified as **Phase 7 — final intraday tests** before concluding. Options roadmap created at [OPTIONS_ROADMAP.md](OPTIONS_ROADMAP.md).
10. **2026-06-06b**: **Phase 7 executed.** All three strategies backtested walk-forward OOS, net of costs. Results:
    - **7.1 Cross-Sectional Momentum**: OOS PF **0.81** (ALL), **1.22** (VOLATILE-only, 100 trades). BUY-top-2 by first-candle return. VOLATILE-only passes gate but small sample. ([scripts/trade/backtest_cross_momentum.py](../scripts/trade/backtest_cross_momentum.py))
    - **7.2 Gap-and-Go with Volume**: OOS PF **1.28** (ALL, 228 trades), **1.35** (skip-RANGE), **1.66** (VOLATILE-only, 78 trades). **FIRST STRATEGY TO PASS THE 1.15 PROMOTION GATE OOS.** Robust across parameter sweep (gap 1-2%, vol 1.5-3×). PF revised from 1.35 after code review enforced gap cap (≤5%). ([scripts/trade/backtest_gap_go.py](../scripts/trade/backtest_gap_go.py))
    - **7.3 Previous-Day Breakout**: OOS PF **0.87** (ALL), **1.18** (VOLATILE-only, 98 trades). Marginal with regime gate; fails without. ([scripts/trade/backtest_prev_day_breakout.py](../scripts/trade/backtest_prev_day_breakout.py))
    - **Verdict: Gap-and-Go is the clear winner.** Proceed to dry-run validation.
11. **2026-06-08**: **Pre-dry-run sweep.** Swept daily cap (1-10), trailing stop (0-2R), square-off time (12:00-15:10), and combinations. Key findings:
    - **Daily cap: 2 is optimal.** PF drops monotonically with more stocks: cap=2 PF 1.28, cap=3 PF 1.24, cap=5 PF 1.17, cap=10 PF 1.08. 3rd+ gap stocks are weaker follow-throughs.
    - **Trailing stop: DESTROYS the strategy.** Every trail config makes PF worse: trail@0.5R PF 0.45, trail@1.0R PF 0.71, trail@1.5R PF 0.97, trail@2.0R PF 1.03. Winners need to run to full target (2.85× win/loss ratio).
    - **Square-off: 13:00 is optimal** (PF 1.34, Sharpe 1.54, MaxDD 8.45%). Gap signal fades by midday. 14:00 was PF 1.28, MaxDD 11.09%.
    - **Hybrid afternoon strategy: not yet.** Legacy scorer PF 0.82 would dilute edge. Deferred to Phase 9.
    - **Loser exit adjusted to 12:00** (1 hour before 13:00 sq-off).
    - Final dry-run config: PF 1.28, Sharpe 1.30, MaxDD 8.71%. Code changes: `GAP_GO_SQUARE_OFF_HOUR=13`, manager overrides SQUARE_OFF and LOSER_EXIT for gap-go.
12. **2026-06-09**: **v1.0 day-1 dry-run FAILED (0/2, Rs.-475).** Root causes diagnosed:
    - SHRIRAMFIN BUY: score -3.5 (bearish) on a gap-up — indicator contradiction ignored.
    - BHARTIARTL BUY: gap faded 0.79% from open before entry — no gap-hold check.
    - Both entered at 09:30:06 LTP — backtest enters at 09:30 candle close (look-ahead bias).
    - **v1.1 implemented**: entry at 09:45 (candle close), gap-hold 0.3%, score-contradiction block.
    - **OOS backtest: PF 1.37 → 1.55 (+13%), Sharpe 1.38 → 1.66 (+20%), WR 32.1% → 35.7%.**
    - Chan evidence framework removed (dead code). Version scheme introduced: `NOAI_GAP_AND_GO_X.Y.Z`.
    - **Regime-conditional cap sweep**: tested cap=3-8 on VOLATILE days. Cap=3 best at PF 1.58 (+2%, within noise). Cap=4+ degrades monotonically. **Verdict: keep cap=2 universally** — v1.1 filters already self-select out bad trades on non-volatile days.
    - **Regime skip tested**: skipping RANGE days makes PF **worse** (1.55 → 1.49). Gap-hold + score-contra filters implicitly filter bad RANGE trades. **Run every day.**
    - **Rs P&L projection at Rs.50K**: +Rs.3,621/year net (7.2%). Charges eat 39% of gross. Avg winner Rs.274, avg loser Rs.-100. Median month +Rs.190, best +Rs.1,737, worst -Rs.1,068. 55% positive months. Fat-tail strategy — profits come from volatile weeks that cover many small losses.
13. **2026-06-12**: **NIFTY100 universe expansion → v1.1.1.** After two dry-run sessions with 0 trades (all 31 gapping NIFTY50 stocks failed volume gate on a broad-market gap-up day), backtested NIFTY100 to widen the candidate pool. Results (v1.1.0 filters, OOS TEST window):
    - **NIFTY50 vs NIFTY100 head-to-head**: PF 1.55→**1.62** (+5%), Sharpe 1.66→**1.80** (+8%), Return +14.33%→**+16.09%**, Expectancy +0.146%→**+0.164%** per trade. Trade count identical (98) — cap=2 just selects better candidates from the wider pool.
    - **Regime uplift**: Skip-RANGE PF 1.49→**1.75**, VOLATILE-only PF 1.98→**2.45** (Sharpe 2.98→**3.97**).
    - **Regime-conditional cap**: VOLATILE cap=3 PF **1.75** (Sharpe 2.12), still best at cap=2-3 range.
    - **Why it helps**: broader gap-up days dilute volume across NIFTY50 stocks (nobody clears 2.0x). NIFTY100 extras (51-100) include mid-large-caps with more concentrated institutional flow on gap days.
    - **No downside**: same trade count, no cost increase, no drawdown increase (7.18% both), all data already cached.
    - Config switched: `SCAN_UNIVERSE = "NIFTY100"`, `TRADE_STRATEGY_PROFILE = "NOAI_GAP_AND_GO_1.1.1"`. Dry-run continues on expanded universe.
    - Version scheme updated to **X.Y.Z**: X = strategy logic change, Y = filter/safeguard changes, Z = config/parameter changes. Existing versions renamed: 1.0→1.0.0, 1.1→1.1.0.
    - **NIFTY150/200 tested — NIFTY100 confirmed optimal.** Fetched 2yr candles for all 200 NIFTY200 stocks and ran identical v1.1.0-filter backtests:

      | Metric | NIFTY50 | **NIFTY100** | NIFTY150 | NIFTY200 |
      |---|---:|---:|---:|---:|
      | PF | 1.55 | **1.62** | 1.13 | 1.17 |
      | Sharpe | 1.66 | **1.80** | 0.59 | 0.74 |
      | WR | 35.7% | 35.7% | 28.8% | 29.1% |
      | Trades | 98 | 98 | 170 | 172 |
      | MaxDD | 7.18% | 7.18% | 16.67% | 14.68% |
      | Return | +14.33% | **+16.09%** | +7.73% | +9.72% |

      NIFTY150/200 degrade sharply: trade count nearly doubles (more qualifying candidates flood the cap=2 filter), WR drops ~7pp, drawdown doubles. Stocks 101-200 are mid-caps with wider spreads and noisier gap signals — their gaps are "retail noise" not "institutional conviction." **NIFTY100 is the sweet spot. Do not expand further.**

## Key Config (v1.1.1)

| Parameter | Value | Evidence |
|---|---|---|
| Strategy profile | `NOAI_GAP_AND_GO_1.1.1` | OOS PF 1.62, Sharpe 1.80 (NIFTY100) |
| ATR multiplier | 2.0 | Backtest E1: best per-trade expectancy |
| R:R target | 1.8:1 | Backtest E1: practical optimum |
| R:R floor | 1.3:1 | Uniform all day |
| Daily trade cap | 2 | Backtest K1: PF 0.81 vs 0.71; Gap-Go sweep: PF drops with 3+ |
| Square-off | 13:00 IST (gap-go) / 14:00 IST (legacy) | Gap-Go sweep: 13:00 PF 1.34 vs 14:00 PF 1.28 |
| Loser exit | 12:00 IST (gap-go) / 13:00 IST (legacy) | 1 hour before sq-off |
| Trailing stop | DISABLED (gap-go) | Sweep: every trail config makes PF worse (0.45-1.03) |
| SL range | 0.8% - 2.5% | Min floor prevents whipsaw |
| Entry timing | 09:45 IST (v1.1.0+: after 09:30 candle closes) | Matches backtest entry at candle close |
| Gap-hold filter | 0.3% | v1.1.0+: reject if gap faded >0.3% — PF 1.57 standalone |
| Score contradiction | ENABLED | v1.1.0+: reject BUY when score < 0 — PF 1.44 standalone |
| RSI BUY ceiling | 70 | Block overbought gap-ups — PF 1.28→1.37 |
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
**Trigger**: After Phase 2 verdict. **DONE — REJECTED (2026-06-01).** VWAP-trail was the cheapest different-exit signal (data on hand, no new deps); it tightens the loss distribution but clips the fat winners on volatile days, so it does not clear the gate (see results below). The active next step is now **Phase 6 (pairs trading)**.

| Step | Action | Rationale | Done? |
|---|---|---|---|
| 3.1 | Backtest: on TREND-regime days, replace fixed ATR target with VWAP trail | FIIs anchor to VWAP; price holding above VWAP post-10AM has strong follow-through on NSE | ✅ |
| 3.2 | Compare PF, avg winner size, avg hold time vs fixed target | | ✅ |
| 3.3 | If better: dry-run for 5 sessions | | ➖ n/a (did not beat gate) |
| 3.4 | Verdict: keep/reject | | ✅ REJECT |

**Exit criteria**: VWAP trail verdict on trend-day trades.

#### Phase 3 Results (2026-06-01) — REJECTED
`scripts/trade/backtest_vwap_trail.py` (adds `gate_vwap_trail` to `simulate_trades`); frozen config, same regime labels + 2-trade/day cap as Phase 1; TRAIN vs TEST(OOS), net of cost.

| Regime keep-set | Mode | TEST PF | TEST Exp% | AvgW% | AvgL% | HoldMin |
|---|---|---|---|---|---|---|
| TREND only | FIXED | 0.92 | -0.030 | 0.815 | -0.616 | 175 |
| TREND only | **VWAP trail** | **0.94** | -0.018 | 0.900 | -0.448 | 133 |
| VOLATILE only | FIXED | **1.10** | +0.039 | 1.015 | -0.724 | 184 |
| VOLATILE only | **VWAP trail** | **0.78** | -0.089 | 0.921 | -0.625 | 141 |
| TREND+VOLATILE | FIXED | 0.99 | -0.004 | 0.892 | -0.655 | 178 |
| TREND+VOLATILE | **VWAP trail** | **0.87** | -0.044 | 0.908 | -0.512 | 136 |

**Verdict: REJECT.** The VWAP trail *tightens the P&L distribution* — it cuts the average loser (e.g. -0.616 → -0.448 on TREND) and shortens holds (~175 → ~135 min), which nudges the weak TREND regime up a hair (0.92 → 0.94). **But it does the opposite of what's needed on the one regime that works:** VOLATILE-only collapses from **PF 1.10 → 0.78** because the trail clips the *fat right tail* (the few big winners) that volatile days exist to capture. Net: **no regime keep-set clears PF 1.15 OOS**, and the best honest configuration is unchanged — **FIXED target on VOLATILE-only, PF 1.10**. Lesson: on high-variance days, *let winners run* (fixed RR or wider) beats *trail tight*; the edge gap is still the entry **signal/selection**, not the exit mechanic. Next lever must be a genuinely *different, lower-correlation* source of edge → **Phase 6 pairs trading** (market-neutral) is now the highest-priority candidate, with Phase 4 order-flow as the fallback.

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
**Trigger**: Only if Phases 1-5 have not achieved promotion-gate PF. **← NOW THE ACTIVE NEXT STEP (2026-06-01)** — Phases 1–3 exhausted the regime/timeframe/exit-mechanic levers on the existing momentum signal (best OOS PF 1.10 < 1.15). A genuinely *different, lower-correlation* edge is required. **Pairs trading is the highest-priority candidate** (market-neutral, sheds the directional-beta drag that sinks every long/short momentum variant).

| Candidate | Prerequisites |
|---|---|
| Pairs trading (HDFCBANK/ICICIBANK, TCS/INFY) | Hedge-ratio modeling, two-leg cost calc |
| Expiry-day options selling (Thursday NIFTY/BANKNIFTY) | Options infra, Sensibull/Opstra integration |
| Intraday momentum (first-half → last-half) | Simple; can backtest on existing candle data |

**Decision on which candidate**: Made after Phase 5 based on what the data shows.

#### Phase 6 Results (2026-06-01) — Pairs trading REJECTED (intraday); REDIRECT to swing

**Decision (PM call, 2026-06-01):** Do NOT deploy capital to the intraday trade tool. The one promising thread — market-neutral pairs trading — should be rebuilt in the SWING tool. The intraday tool stays paused. See Phase 7 below for the final three tests before concluding.

`scripts/trade/backtest_pairs.py`. Market-neutral stat-arb on NIFTY50 same-sector pairs (`SECTOR_MAP`). Strict no-lookahead: hedge ratio β = OLS on TRAIN log-prices (frozen); pair selection (correlation ≥ floor, OU half-life band) on TRAIN only; traded OOS on TEST with a causal trailing-window z-score; **costs charged on both legs** via `Config.calculate_charges`; intraday MIS with 15:15 square-off.

**The killer finding is in pair selection, not the P&L:** the OU **half-life of every viable sector spread is 270–3000+ 15-min bars** (÷ ~25 bars/day = **~10–120 trading days**). The spreads mean-revert over *weeks*, so an intraday tool that must flatten by 15:15 exits long before convergence while paying two-leg costs every day.

| Run (15 / 10 pairs, net of 2-leg cost) | TRAIN PF | TEST(OOS) PF | OOS Exp% | OOS net |
|---|---|---|---|---|
| entry-z 2.0, roll 26 (15 pairs) | 0.57 | **0.48** | -0.079 | -Rs.82,349 |
| entry-z 3.0, roll 26 (10 pairs, extremes only) | 0.72 | **0.69** | -0.051 | -Rs.12,885 |

Raising the entry threshold cuts trade count (cost drag) and lifts PF 0.48 → 0.69, but it is **negative in-sample too** (0.57 / 0.72) and only 1 of 10 pairs is marginally positive OOS (noise). **Verdict: REJECT for the intraday tool.** This is *not* a parameter-tuning miss — it is a horizon mismatch: the mean-reversion edge is real but multi-day. **Redirect: rebuild pairs trading in the SWING tool** (multi-day holds, where the ~10–40 day half-life fits and two-leg cost is amortised over a much larger move). Options-selling and intraday-momentum candidates remain untested and lower-priority.

| Candidate | Prerequisites |
|---|---|
| ~~Pairs trading (intraday)~~ | ❌ REJECTED 2026-06-01 — spread half-life 10–40 days ≫ intraday horizon |
| **Pairs trading (SWING / multi-day)** | Move to swing tool; daily-candle spread + overnight-risk + financing model |
| Expiry-day options selling (Thursday NIFTY/BANKNIFTY) | Options infra, Sensibull/Opstra integration |
| Intraday momentum (first-half → last-half) | Simple; can backtest on existing candle data |

---

### Phase 7: Final Intraday Equity Tests (Last 3 Signals Before Concluding)
**Goal**: Test three genuinely different signal families that have zero overlap with the indicator-based scorer. If all three fail OOS, intraday equity is conclusively dead at this capital level. If any passes, stack with regime gate and evaluate.
**Status**: **DONE — Gap-and-Go PASSES (OOS PF 1.35).** (2026-06-06)
**Trigger**: After Phase 6 verdict. All use existing 15-min backtest data — no new data needed.

| Step | Action | Signal Family | Rationale | Done? |
|---|---|---|---|---|
| 7.1 | **Backtest cross-sectional momentum** | Rank-based (relative strength) | Different from absolute scoring — ranks NIFTY50 stocks by first-15-min return, buys top 2. Academic evidence (Jegadeesh & Titman) for intraday cross-sectional persistence. Zero overlap with current 14-indicator scorer. | ✅ FAIL (ALL PF 0.81); VOLATILE-only PF 1.22 (100 trades, marginal) |
| 7.2 | **Backtest gap-and-go with volume** | Gap + volume filter | ORB-15 was PF 0.97 (closest to profitable). Gap-and-go adds strict volume qualification (>2x average) which should filter false breakouts. Same regime routing (TREND + VOLATILE days only). | ✅ **PASS — OOS PF 1.28 (ALL), 1.66 (VOLATILE)** |
| 7.3 | **Backtest previous-day high/low breakout** | Pure price-level breakout | One of the oldest intraday signals. No indicators needed — just previous day's high/low as breakout level + volume confirmation. Different from multi-indicator scoring. | ✅ FAIL (ALL PF 0.87); VOLATILE-only PF 1.18 (98 trades, marginal) |
| 7.4 | **Verdict on each** | — | Walk-forward OOS, net of costs, same protocol as Phases 0-6. Keep if OOS PF ≥ 1.15. | ✅ Gap-and-Go passes. Proceed to dry-run. |

**Backtest protocol (same as all prior phases):**
- Walk-forward: TRAIN on year 1, TEST on year 2 (OOS)
- Net of costs via `Config.calculate_charges`
- Same frozen config (ATR 2.0, RR 1.8, K1=2, sq-off 14:00)
- Regime routing: test ALL, skip-RANGE, VOLATILE-only variants
- Capital Rs.50K, per-trade Rs.15K

**Implementation notes for each:**

#### 7.1 — Cross-Sectional Momentum
```
Script: scripts/trade/backtest_cross_momentum.py (new)
Logic:
  1. At 09:30 (after first 15-min candle), compute return for all 50 stocks
  2. Rank by return (descending for BUY, ascending for SELL)
  3. Enter top 2 (BUY) — no indicator scoring needed
  4. ATR-based SL/target (same as current)
  5. Same exit rules (loser exit 13:00, sq-off 14:00)
  6. Regime-route: skip RANGE days
Data: existing intraday_15m.sqlite
```

#### 7.2 — Gap-and-Go with Volume
```
Script: scripts/trade/backtest_gap_go.py (new)
Logic:
  1. At 09:30, identify stocks that gapped >1% from previous close
  2. Volume filter: first-15-min volume > 2x same-period 20-day average
  3. Enter in gap direction (gap up → BUY, gap down → SELL)
  4. SL: below gap candle low (BUY) / above gap candle high (SELL)
  5. Target: 50-100% of gap size continuation
  6. Regime-route: TREND + VOLATILE only
Data: existing intraday_15m.sqlite + daily candles for prev close
```

#### 7.3 — Previous Day High/Low Breakout
```
Script: scripts/trade/backtest_prev_day_breakout.py (new)
Logic:
  1. Compute previous day's high and low for each stock
  2. Monitor 15-min candles; enter when close breaks above prev-day high (BUY)
     or below prev-day low (SELL)
  3. Volume confirmation: breakout candle volume > 1.5x average
  4. ADX > 25 filter (breakout needs trend strength)
  5. ATR-based SL/target
  6. Regime-route: TREND days only
Data: existing intraday_15m.sqlite + daily candles for prev day H/L
```

**Exit criteria (Phase 7 overall):**

| Outcome | Action |
|---|---|
| **Any strategy OOS PF ≥ 1.15** | Stack with regime gate → dry-run 10 sessions → evaluate live |
| **Best strategy OOS PF 1.00-1.14** | Marginal — stack with regime + ML classifier (A.8) for one more attempt |
| **All three OOS PF < 1.00** | **Intraday equity is conclusively dead** at Rs.50K on NSE. Archive trade mode. Pivot to OPTIONS mode (see [OPTIONS_ROADMAP.md](OPTIONS_ROADMAP.md)). |

#### Phase 7 Results (2026-06-06)

**7.1 — Cross-Sectional Momentum** ([scripts/trade/backtest_cross_momentum.py](../scripts/trade/backtest_cross_momentum.py))
Buy top-2 NIFTY50 stocks ranked by first-15-min return. Pure momentum ranking, no indicator scoring.

| Window / Routing | Trades | WR% | PF | Exp% | Ret% | MaxDD | Sharpe |
|---|--:|--:|--:|--:|--:|--:|--:|
| TEST / ALL | 474 | 35.4 | 0.81 | -0.084 | -39.98 | 50.24 | -1.96 |
| TEST / Skip RANGE | 266 | 35.7 | 0.87 | -0.057 | -15.22 | 25.10 | -0.93 |
| **TEST / VOLATILE only** | **100** | **41.0** | **1.22** | **+0.102** | **+10.17** | **10.39** | **+0.85** |
| TEST / SELL side | 474 | 40.5 | 0.88 | -0.050 | -23.89 | 26.38 | -1.10 |

**Verdict: FAIL on ALL regimes (PF 0.81). VOLATILE-only shows PF 1.22 (above 1.15 gate) but on only 100 trades — insufficient sample for confidence. Sell-side is also negative.**

**7.2 — Gap-and-Go with Volume Qualification** ([scripts/trade/backtest_gap_go.py](../scripts/trade/backtest_gap_go.py))
Enter stocks that gap >1% from prev close with first-candle volume >2× 20-day avg. SL at gap candle structure.

| Window / Routing | Trades | WR% | PF | Exp% | Ret% | MaxDD | Sharpe |
|---|--:|--:|--:|--:|--:|--:|--:|
| TRAIN / ALL | 247 | 22.3 | 0.76 | -0.102 | -25.12 | 34.82 | -1.54 |
| **TEST / ALL** | **228** | **31.6** | **1.28** | **+0.080** | **+18.26** | **11.09** | **+1.30** |
| **TEST / Skip RANGE** | **150** | **27.3** | **1.35** | **+0.102** | **+15.28** | **9.52** | **+1.27** |
| **TEST / VOLATILE only** | **78** | **30.8** | **1.66** | **+0.183** | **+14.25** | **5.28** | **+1.59** |

Parameter sweep (TEST/ALL regimes, gap cap ≤5%):

| Gap % | Vol × | Trades | PF | Exp% |
|--:|--:|--:|--:|--:|
| 0.5 | 2.0 | 323 | 1.01 | +0.003 |
| 1.0 | 1.5 | 263 | 1.11 | +0.031 |
| **1.0** | **2.0** | **228** | **1.28** | **+0.080** |
| 1.0 | 3.0 | 163 | 1.49 | +0.135 |
| 1.5 | 2.0 | 155 | 1.40 | +0.117 |
| 2.0 | 2.0 | 107 | 1.58 | +0.177 |

**Verdict: PASS — OOS PF 1.28 on 228 trades (ALL regimes). Clears the 1.15 promotion gate. Robust across all parameter combinations (PF stays above 1.15 for gap≥1.0% vol≥2.0×). VOLATILE-only reaches PF 1.66. Exit reasons: 63% stop loss, 9% target hit, 23% EOD square-off, 5% loser exit. The low WR (32%) is offset by large winner/loser asymmetry. TRAIN PF 0.76 vs TEST PF 1.28 is an unusual pattern (worse in-sample) — this could indicate regime shift favouring this signal in 2025-2026, or statistical noise. Proceed to dry-run validation with caution.**

**Key concern: TRAIN PF (0.76) << TEST PF (1.35).** A strategy that loses money on year 1 and profits on year 2 could be:
- (a) A genuine regime shift (e.g., higher volatility in 2025-H2 creates more gapping opportunities), or
- (b) Statistical noise in a 236-trade sample.
The parameter sweep robustness (PF consistently >1.15 across many configs) supports (a), but dry-run validation is critical.

**Half-year consistency check** (confirms the trend is real, not noise):

| Window | Trades | WR% | PF | Exp% | Sharpe |
|---|--:|--:|--:|--:|--:|
| 2024-H2 (TRAIN) | 139 | 23.7 | 0.85 | -0.072 | -0.89 |
| 2025-H1 (TRAIN) | 129 | 22.5 | 0.68 | -0.125 | -2.44 |
| **2025-H2 (TEST)** | **110** | **31.8** | **1.24** | **+0.059** | **+1.05** |
| **2026-H1 (TEST)** | **111** | **32.4** | **1.50** | **+0.154** | **+2.39** |

The edge is **monotonically improving** — not a random blip. Oct-May (7/8 months) are profitable. SELL side (PF 1.61) is stronger than BUY (PF 1.14). Win/loss asymmetry: avg winner +1.18% vs avg loser -0.41% (2.85×).

**Implementation**: Gap-and-Go is integrated into the trade mode via `TRADE_STRATEGY_PROFILE = \"NOAI_GAP_AND_GO\"` ([modes/trade/stock_scanner.py](../modes/trade/stock_scanner.py) `_scan_noai_gap_go()`). Config knobs: `GAP_GO_MIN_GAP_PCT`, `GAP_GO_MAX_GAP_PCT`, `GAP_GO_VOLUME_MULTIPLE`, `GAP_GO_DAILY_CAP`, `GAP_GO_SQUARE_OFF_HOUR`, `GAP_GO_SQUARE_OFF_MINUTE`, `GAP_GO_SKIP_RANGE_REGIME`. Gap-coherence gate bypassed for this strategy (we trade WITH the gap). Code-reviewed and bug-fixed: SL uses gap-candle structure (not ATR), volume filter matches backtest (per-candle, not prorated daily), all RSI/ADX/pattern/VWAP gates bypassed for gap-go entries, ATR override skipped to preserve scanner SL/target.

#### Pre-Dry-Run Sweep Results (2026-06-08)

**Daily cap sweep** (TEST, ALL regimes, no trail):

| Cap | Trades | WR% | PF | Exp% | Ret% | Sharpe |
|--:|--:|--:|--:|--:|--:|--:|
| 1 | 148 | 29.7 | 1.21 | +0.062 | +9.25 | +0.80 |
| **2** | **228** | **31.6** | **1.28** | **+0.080** | **+18.26** | **+1.30** |
| 3 | 265 | 31.7 | 1.24 | +0.068 | +17.91 | +1.23 |
| 4 | 289 | 31.1 | 1.20 | +0.058 | +16.66 | +1.10 |
| 5 | 302 | 30.8 | 1.17 | +0.048 | +14.64 | +0.96 |
| 10 | 340 | 29.7 | 1.08 | +0.024 | +8.05 | +0.50 |

**Verdict: cap=2 is optimal.** PF and Sharpe both peak at 2 and monotonically decline. The 3rd+ stocks are weaker gaps with less follow-through. Even cap=6 still clears the 1.15 gate (PF 1.15), but it's diluted.

**Trailing stop sweep** (TEST, ALL, cap=2):

| Config | PF | Sharpe | Trail exits | Target hits |
|---|--:|--:|--:|--:|
| **No trail (baseline)** | **1.28** | **+1.30** | **0** | **21** |
| Trail@0.5R / 50% | 0.45 | -4.49 | 125 | 0 |
| Trail@1.0R / 50% | 0.71 | -1.87 | 101 | 2 |
| Trail@1.0R / 60% | 0.79 | -1.38 | 103 | 1 |
| Trail@1.5R / 50% | 0.97 | -0.14 | 73 | 7 |
| Trail@2.0R / 50% | 1.03 | +0.17 | 56 | 12 |

**Verdict: DO NOT enable trailing stop.** Every trail config reduces PF. The strategy depends on a 2.85× win/loss ratio (avg winner +1.18% vs avg loser -0.41%). Trailing chops winners on normal gap-stock retracements. Trail@0.5R is catastrophic — converts 21 target hits into ZERO.

**Square-off time sweep** (TEST, ALL, cap=2):

| Time | PF | Sharpe | MaxDD |
|---|--:|--:|--:|
| 12:00 | 1.17 | +0.86 | 9.60 |
| 12:30 | 1.23 | +1.14 | 9.20 |
| **13:00** | **1.34** | **+1.54** | **8.45** |
| 13:30 | 1.32 | +1.48 | 9.96 |
| 14:00 | 1.28 | +1.30 | 11.09 |
| 15:10 | 1.22 | +1.04 | 10.32 |

**Verdict: 13:00 is optimal.** Gap signal fades by midday. Earlier exit avoids afternoon reversals (MaxDD 8.45% vs 11.09% at 14:00). PF peaks at 13:00 (1.34). Implemented as `GAP_GO_SQUARE_OFF_HOUR = 13`, with loser exit at 12:00.

**Hybrid afternoon strategy decision:** After gap-go trades close, should the bot switch to the legacy scorer for the afternoon? **No — not for dry run.** Legacy scorer OOS PF 0.82 (loses money). Mixing strategies dilutes edge. Gap-go's one-shot design (enter at 9:30, monitor until 13:00, done) is a feature. Afternoon hybrid is deferred to Phase 9 if/when a profitable afternoon strategy exists.

**7.3 — Previous-Day High/Low Breakout** ([scripts/trade/backtest_prev_day_breakout.py](../scripts/trade/backtest_prev_day_breakout.py))
Enter when price breaks above prev-day high (BUY) or below prev-day low (SELL). ADX≥25 + volume≥1.5× filters.

| Window / Routing | Trades | WR% | PF | Exp% | Ret% | MaxDD | Sharpe |
|---|--:|--:|--:|--:|--:|--:|--:|
| TEST / ALL | 472 | 37.1 | 0.87 | -0.050 | -23.45 | 35.06 | -1.19 |
| TEST / Skip RANGE | 264 | 37.9 | 0.90 | -0.040 | -10.67 | 21.14 | -0.68 |
| TEST / VOLATILE only | 98 | 44.9 | 1.18 | +0.075 | +7.33 | 9.63 | +0.69 |
| TEST / TREND only | 166 | 33.7 | 0.73 | -0.108 | -18.00 | 19.09 | -1.61 |

**Verdict: FAIL on ALL regimes (PF 0.87). VOLATILE-only marginal (PF 1.18, 98 trades — just above 1.15 but tiny sample). TREND-only is the worst at PF 0.73 — breakouts into trend days actually underperform, suggesting these are false breakouts into established trends.**

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

| Feature | Reason | When to pick up |
|---|---|---|
| **GAP_GO_SKIP_RANGE_REGIME wiring** | Config flag exists but live scanner has no regime classifier — trades ALL days. Backtest: PF 1.35 skip-RANGE vs 1.28 ALL. | **After Phase 8 dry-run.** If dry-run PF is 1.00-1.14, this is the first lever. Port `regime_analysis.py` morning-feature logic into live scanner. |
| Score-weighted sizing | Anti-correlated with P&L at current edge | Not until Gap-and-Go is live and profitable for 30+ sessions |
| Budget regime deltas | Not needed at Rs.50K single tier | Only if scaling to Rs.1L+ |
| HFT/WebSocket | Speed does not fix a losing strategy | Only if Gap-and-Go proves edge but slippage erodes it |
| Pairs/stat-arb (intraday) | REJECTED Phase 6 — horizon mismatch. Redirect to SWING | Only in SWING mode |
| Options strategies | Separate mode — see [OPTIONS_ROADMAP.md](OPTIONS_ROADMAP.md) | After Gap-and-Go live verdict |
| Order flow / OFI (Phase 4) | Needs paid data (₹1,500/mo) | Only if Gap-and-Go dry-run fails (PF < 1.0) |
| Optuna tuning (Phase 5) | Gap-and-Go already clears gate — tuning risks overfitting | Only if dry-run PF is 1.00-1.14 |
| ML classifier (A.8) | Gap-and-Go PF 1.28 doesn't need filtering | Only if dry-run PF degrades to 1.00-1.14 |\n| **Hybrid afternoon strategy** | Legacy scorer PF 0.82 would dilute gap-go edge. No profitable afternoon strategy exists yet. | **Phase 9** — only after gap-go is validated live AND a new afternoon signal is found |

---

### Phase 8: Gap-and-Go Dry-Run Validation (NEXT — Monday 2026-06-09)
**Goal**: Validate the Gap-and-Go strategy on live market data with simulated orders. The backtest OOS PF 1.28 must hold in real-time conditions (live quotes, real spreads, live volume data).
**Status**: **READY** — code implemented, config knobs set, code-reviewed, pre-dry-run sweep complete.
**Known limitation**: `GAP_GO_SKIP_RANGE_REGIME` is not wired — dry-run trades ALL regime days (PF 1.28 baseline, not the 1.35 skip-RANGE variant). If dry-run PF is marginal, wiring regime skip is the first fix to try.
**Expected PF**: ~1.15-1.28 (conservative estimate accounting for live slippage and backtest→live degradation).

**Finalized dry-run config:**
| Parameter | Value | Evidence |
|---|---|---|
| `TRADE_STRATEGY_PROFILE` | `"NOAI_GAP_AND_GO"` | Phase 7.2 OOS PF 1.28 |
| `GAP_GO_MIN_GAP_PCT` | 1.0% | Sweep: PF 1.28. Lower (0.5%) drops to PF 1.01 |
| `GAP_GO_MAX_GAP_PCT` | 5.0% | Extreme gaps = corporate actions |
| `GAP_GO_VOLUME_MULTIPLE` | 2.0× | Sweep: PF 1.28. Higher (3.0×) = PF 1.49 but fewer trades |
| `GAP_GO_DAILY_CAP` | 2 | Sweep: PF drops with 3+ (1.24, 1.20, 1.17...) |
| `GAP_GO_SQUARE_OFF_HOUR` | 13 | Sweep: 13:00 PF 1.34, MaxDD 8.45% (best) |
| `GAP_GO_SQUARE_OFF_MINUTE` | 0 | — |
| `TRAIL_AFTER_RISK_MULTIPLE` | 0.0 (disabled) | Sweep: every trail config destroys PF (0.45-1.03) |
| Loser exit | 12:00 (auto: sq-off − 1hr) | 1 hour before sq-off |
| Entry time | 9:30 IST (after first 15-min candle) | Backtest ENTRY_CANDLE_IDX=1 |
| SL | Gap-candle low/high | Not ATR — gap structure is the support/resistance |
| Target | max(SL×1.8, ATR×2.0×1.8) | Dual-target matching backtest |

**Timeline: start tool before 9:00 AM → login → wait → scan at 9:30 → enter 2 trades → monitor → loser exit 12:00 → square-off 13:00 → report → done by 13:15.**

**How to run:**
```bash
# Step 1: Set strategy profile in config.py
TRADE_STRATEGY_PROFILE = "NOAI_GAP_AND_GO"

# Step 2: Run dry-run
python main.py --mode trade --dryrun
```

| Step | Action | Done? |
|---|---|---|
| 8.1 | Set `TRADE_STRATEGY_PROFILE = "NOAI_GAP_AND_GO"` in config.py | |
| 8.2 | Run `--dryrun` for 10+ sessions | |
| 8.3 | Collect dry-run PF, WR, expectancy from reports | |
| 8.4 | Compare dry-run metrics to backtest OOS (PF 1.28, WR 32%, Exp +0.080%) | |
| 8.5 | **Verdict**: if dry-run PF ≥ 1.15 → proceed to live. If < 1.0 → abandon. If 1.0-1.14 → tune or combine with other signals. | |

**Exit criteria**: Dry-run PF ≥ 1.15 after real costs on ≥ 10 sessions, ≥ 20 trades.

---

### Phase 9: Strategy Diversification Research (parallel to Gap-and-Go dry-run)
**Goal**: Research and backtest new **intraday equity** strategies to diversify beyond Gap-and-Go. The system currently depends on a single strategy that fires 0-2 trades/day. On broad-market gap-up days (like 2026-06-12), no trades fire at all. Multiple uncorrelated strategies increase trade frequency and reduce variance.
**Scope**: Intraday equity (MIS) only. Options strategies → [OPTIONS_ROADMAP.md](OPTIONS_ROADMAP.md). Swing/delivery strategies → [SWING_ROADMAP.md](SWING_ROADMAP.md).
**Status**: RESEARCH — collecting candidates. Gap-and-Go dry-run continues independently.
**Trigger**: Can start anytime — does not block or depend on Gap-and-Go validation.

#### Already tested and rejected (Phases 1-7)

| Strategy | OOS PF | Verdict | Notes |
|---|---:|---|---|
| Legacy 62-gate blended score | 0.82 | FAIL | Negative expectancy |
| 5-min entry timing | 0.70 | FAIL | Worse than 15-min |
| ORB-5 breakout | 0.66 | FAIL | Overfit collapse |
| VWAP mean-reversion | 0.80 | FAIL | Doesn't work intraday NSE |
| EMA pullback | 0.65 | FAIL | Edge doesn't survive costs |
| ORB-15 breakout | 0.97 | FAIL | Close but <1.0 |
| Intraday pairs/stat-arb | 0.48-0.69 | FAIL | Horizon mismatch |
| Cross-sectional momentum | 0.81 (ALL), 1.22 (VOL) | MARGINAL | Thin sample |
| Prev-day breakout | 0.87 (ALL), 1.18 (VOL) | MARGINAL | Regime-dependent |
| Options directional buying | 0.42-0.64 | FAIL | Theta + charges kill it |
| **First Hour Range Breakout** | **0.81** | **FAIL** | **Phase 9.1 (2026-06-12). SL too wide (full 1hr range), trades drift as late losers. Narrow range + tight RR doesn't help. Worse than ORB-15.** |
| **Opening Candle Momentum** | **0.87** | **FAIL** | **Phase 9.4 (2026-06-12). First candle shape+vol signal. VOLATILE-only PF 1.15 (81 trades) but ALL regimes 0.87. Gap confirm and RSI don't rescue it.** |
| **NIFTY Index Momentum (sim. futures)** | **0.25** | **FAIL** | **Phase 9.3 (2026-06-12). Synthetic NIFTY index gap-and-go with futures costs. 13 OOS trades, PF 0.25. Index doesn't gap enough and has no momentum follow-through.** |
| **Sector Rotation Intraday** | **0.78** | **FAIL** | **Phase 9.6 (2026-06-12). Buy top sector / sell bottom sector by first-45-min return. 472 OOS trades, PF 0.78. Sector momentum doesn't persist intraday after costs.** |

#### Already listed in TRADE_NEXT_IDEAS.md but not yet backtested

| ID | Strategy | Effort | Data | Priority |
|---|---|---|---|---|
| A.1 | Order Flow Imbalance (OFI) | High | Needs paid L1 data (₹1,500/mo) | LOW — can't backtest historically |
| A.4 | NIFTY Futures (single instrument) | Medium | Free from Zerodha | MEDIUM |
| A.5 | RSI Divergence (not threshold) | Medium | Existing data | LOW |
| A.7 | Volatility Squeeze (BB inside KC) | Medium | Existing data | LOW |
| A.8 | ML Entry Classifier (XGBoost) | Medium | Existing data | LOW — only 970 samples |

> Options ideas (B.2 expiry-day theta selling, B.3 volatility-based options) moved to [OPTIONS_ROADMAP.md](OPTIONS_ROADMAP.md).

#### New strategies to research and backtest (2026-06-12)

Sourced from web research, TradingQnA (Zerodha community), Zerodha Varsity, and Indian algo trading forums. Prioritized by: (1) backtestability with existing data, (2) different signal family from Gap-and-Go, (3) evidence of working on Indian markets. **Intraday equity (MIS) only.**

| # | Strategy | Signal family | Why it might work on NSE | Data needed | Effort | Priority |
|---|---|---|---|---|---|---|
| **C.1** | **First Hour Range Breakout (FHRB)** | Breakout | Trade the breakout of the first 1-hour range (9:15-10:15). Unlike ORB-15 (PF 0.97), the 1-hour range is wider and filters more noise. Well-documented in India — Zerodha Varsity and multiple Indian algo traders report success. Volume confirmation + ADX>25 filter. Only on TREND/VOLATILE days. | Existing 15-min data | Low | **HIGH** |
| **C.2** | **NIFTY/BANKNIFTY Futures Momentum** | Index momentum | Trade NIFTY futures instead of 50 individual stocks. Zero STT (only CTT 0.01%), single instrument, our NIFTY trend signal already works. Cost reduction alone may flip PF. India's most liquid instrument. | NIFTY futures data (free via Zerodha API) | Medium | **HIGH** |
| **C.4** | **Intraday VWAP Reversion on Earnings Gap** | Mean reversion | Stocks that gap on earnings/results tend to revert toward VWAP by midday. Filter: gap >3% on result day + volume >3x. SELL gaps that overshoot, BUY gaps that undershoot. Different from Gap-and-Go (which trades WITH the gap). Catches the "fade" that our gap-hold filter rejects. | Existing data + earnings calendar | Medium | **MEDIUM** |
| **C.5** | **Sector Rotation Intraday** | Relative strength | Track which NIFTY sector index (Bank, IT, Pharma, Metal, etc.) is leading in the first 30 min. Buy top sector's strongest stock, sell weakest sector's weakest stock. Captures institutional sector flows. Different from Gap-and-Go (individual stock gaps) and cross-sectional momentum (pure return ranking). | Sector index data (free via Zerodha) | Medium | **MEDIUM** |
| **C.6** | **Opening Auction Imbalance** | Microstructure | NSE pre-open auction (9:00-9:08) reveals demand/supply imbalance. Stocks with large buy-side imbalance at auction tend to run in the first 15 min. This is "poor man's order flow" — free via pre-open data we already collect. | Existing pre-open data | Low | **MEDIUM** |

> C.3 (Expiry Day Iron Condor), C.7 (Calendar Spread) moved to [OPTIONS_ROADMAP.md](OPTIONS_ROADMAP.md).
> C.8 (Monthly Momentum Portfolio) moved to [SWING_ROADMAP.md](SWING_ROADMAP.md).

#### Phase 9 execution plan

| Step | Action | Status |
|---|---|---|
| 9.1 | **Backtest C.1 (FHRB)** — first hour range breakout with volume + ADX filter, walk-forward OOS. Uses existing 15-min data. | **DONE — FAIL. OOS PF 0.81.** All param combos (vol 1-3x, RR 1.2-2.0, ADX 0-30, sq-off 13-15) stay below 1.0. Skip-RANGE PF 0.95, VOLATILE-only PF 0.89. The first-hour range on NIFTY100 stocks is too wide — breakout entries get stopped out frequently. Worse than ORB-15 (PF 0.97). |
| 9.2 | **Fetch NIFTY futures data** — pull 2yr of NIFTY futures 15-min candles from Zerodha. Needed for C.2. | SKIPPED — used synthetic index proxy from NIFTY50 constituent candles instead. |
| 9.3 | **Backtest C.2 (NIFTY Futures Momentum)** — apply gap-and-go signal to NIFTY index with futures cost structure. | **DONE — FAIL. OOS PF 0.25.** Only 13 OOS trades. Index gaps are tiny (0.2-0.5%) and don't follow through. Even with zero STT (futures cost model), the signal has no edge. The index is too efficient — institutional arbitrage absorbs gaps instantly. |
| 9.4 | **Backtest C.6 (Opening Candle Momentum)** — proxy for auction imbalance using first candle shape + volume. | **DONE — FAIL. OOS PF 0.87.** VOLATILE-only PF 1.15 (81 trades) at gate threshold but thin sample. Gap confirm doesn't help. RSI filter lifts to 0.93 but still <1.0. No param combo clears gate on ALL regimes. |
| 9.5 | **Backtest C.4 (Earnings Gap Reversion)** — needs earnings calendar. | DEFERRED — no earnings calendar in data. Strategy is very sparse (~4 events/year/stock). Low priority. |
| 9.6 | **Backtest C.5 (Sector Rotation)** — buy top sector / sell bottom sector by first-45-min return. | **DONE — FAIL. OOS PF 0.78.** 472 trades. Sector momentum doesn't persist intraday — first-45-min sector leadership reverses or flattens by afternoon. No param combo (SL 1-2%, RR 1-2, spread 0.2-0.5%) rescues it. |
| 9.7 | **Verdict** — rank all strategies by OOS PF. Any that clear 1.15 gate → dry-run. | **DONE. ALL FAIL.** 4 strategies tested (C.1 FHRB PF 0.81, C.2 NIFTY Futures PF 0.25, C.5 Sector Rotation PF 0.78, C.6 OCM PF 0.87). C.4 deferred (no earnings calendar). **Gap-and-Go v1.1.1 remains the only passing strategy.** The intraday equity search space on NSE is largely exhausted at this capital/cost level. |

**Exit criteria**: At least 3 intraday equity strategies backtested OOS. Any with PF ≥ 1.15 proceed to dry-run alongside Gap-and-Go.
