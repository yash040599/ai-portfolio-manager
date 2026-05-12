# HFT-Readiness Intraday Audit - 2026-05-11

Fresh full-repo audit of the NoAI intraday trading system, treating the engineering bar as industry-grade automated trading while the deployed capital remains Rs.50,000.

## Scope

Reviewed surfaces:

- Runtime path: `main.py --mode trade` -> `PortfolioManagerV2.run_noai()` -> `StockScannerV2` -> `OrderEngine`.
- Risk/execution: entry gates, sizing, order placement, SL-M lifecycle, monitor loop, exit gates, square-off, external reconciliation.
- Data truth: `data/trades.db`, reports, tax ledger, backup sync, Zerodha verification workflow.
- Config/docs/scripts: `config.py`, roadmap/statistics docs, audit docs, script inventory, generated artifacts.

Non-goals:

- No live strategy tuning in this audit. Several strategy areas are in a stability window; the correct output is evidence, roadmap ordering, and cleanup unless a concrete bug is confirmed.
- No attempt to turn a retail Zerodha REST bot into literal HFT. The realistic goal is HFT-grade discipline: deterministic data, replayability, risk controls, audit trails, and kill-switches.

## Executive Verdict

| Area | Verdict | Reason |
|---|---|---|
| Capital scaling | DO NOT SCALE | Since 2026-04-22 the ledger is net -Rs.3,550.99 over 84 tax-ledger rows. Every reviewed day in the window is net-negative after charges. |
| Strategy edge | Failing in the recent regime | Gross loss is -Rs.2,137.26 and charges are Rs.1,413.70. This is not just friction; gross alpha is negative. |
| Execution safety | Improving, not institutional yet | Exchange SL-M, duplicate-order guard, quote/depth retry, fail-closed depth, orphan SL-M reconciliation, and exit coverage are strong. Data sync/replay/typed quote plumbing are still incomplete. |
| Data integrity | Recovering, still has a gap | May 8/May 11 were repaired, but backup merge cannot propagate deletions. A canonical overwrite/tombstone sync is now roadmap item #270. |
| HFT readiness | Not HFT, but can become a disciplined low-cap intraday bot | Kite REST, 15-min candles, and 10s polling are not HFT. Use the HFT standard as a control/audit bar, not a latency claim. |

Bottom line: keep capital capped at Rs.50,000 or lower, and consider dry-run if the next live sessions keep producing net-negative days. The next work should build evidence infrastructure, not add more discretionary gates.

## Live Evidence Snapshot

Read-only audit commands reviewed:

- `scripts/trade/analyst_pulse.py`
- `scripts/score_inversion_audit.py`
- `scripts/decay_cluster_audit.py`
- `scripts/trade/strategy_stability_check.py`
- `scripts/trade/exit_coverage_check.py`
- import/config smoke checks

Key current metrics:

| Slice | Result |
|---|---|
| Window | 2026-04-22 to 2026-05-11 |
| Tax-ledger rows | 84 |
| Gross P&L | -Rs.2,137.26 |
| Charges | Rs.1,413.70 |
| Net P&L | -Rs.3,550.99 |
| BUY side | 52 rows, 7 wins, 13.5% WR, net -Rs.2,986.81 |
| SELL side | 32 rows, 14 wins, 43.8% WR, net -Rs.564.18 |
| STOP_LOSS exits | 10 rows, 0% WR, net -Rs.1,681.51 |
| STAGNANT_EXIT exits | 14 rows, 7.1% WR, net -Rs.622.37 |
| MOMENTUM_KILL exits | 9 rows, 0% WR, net -Rs.577.28 |
| SIGNAL_DECAY exits | 21 rows, 57.1% gross WR, gross +Rs.123.16, net -Rs.232.37 |
| TARGET_HIT exits | 2 rows, 100% WR, net +Rs.380.60 |
| 13:30-15:00 entries | 7 trades, 0% WR, -Rs.476.25 |
| 15-30 min holds | 6 trades, 0% WR, -Rs.783.99 |
| >120 min holds | 11 trades, 54.5% WR, +Rs.438.39 |

Interpretation:

- The current loss is not only brokerage friction. Charges worsen the result, but the strategy itself is gross-negative in the recent window.
- BUY-side entries are the largest recent failure surface.
- Hard exits and churn exits are doing damage control but not creating edge. The bot is often entering weak tape and then paying to exit it.
- Long holds are the only recent hold-time cohort with positive aggregate P&L. The short 15-30 min cohort is particularly bad.

## Score Model Review

The full-ledger score audit is important because the recent 9-day snapshot alone overstates the score-inversion story.

Full ledger with entry score and P&L:

| Bucket | n | WR | Sum P&L | Expectancy |
|---|---:|---:|---:|---:|
| 9.0+ | 20 | 45.0% | +Rs.180 | +Rs.9.0 |
| 8.0-9.0 | 15 | 33.3% | -Rs.107 | -Rs.7.2 |
| 7.0-8.0 | 27 | 48.1% | +Rs.129 | +Rs.4.8 |
| 6.0-7.0 | 24 | 33.3% | -Rs.237 | -Rs.9.9 |
| 5.0-6.0 | 18 | 55.6% | +Rs.202 | +Rs.11.2 |
| <5.0 | 15 | 53.3% | +Rs.163 | +Rs.10.8 |

Score-cap experiments:

| Hypothesis | Blocked cohort | Admitted cohort | Verdict |
|---|---|---|---|
| Refuse abs(score) >= 8.5 | n=30, -Rs.165 | n=89, +Rs.495 | Not enough to justify a blind cap |
| Refuse abs(score) >= 8.0 | n=35, +Rs.73 | n=84, +Rs.257 | Reject cap; it blocks winners too |
| BUY-only score >= 8.5 cap | blocked +Rs.257 | admitted -Rs.123 | Bad idea; high-score BUY still has historical value |

Interpretation:

- The full ledger does not prove structural score inversion. The recent regime is bad, but the model is not globally anti-correlated.
- Do not edit `_compute_combined_score()` weights, pattern caps, or high-score caps without #24 backtesting and #259 candidate telemetry.
- The more defensible current posture is equal sizing (`SCORE_WEIGHTED_SIZING_ENABLED = False`) until score edge is stable again.

## Strategy Decision Review

### What To Keep

| Component | Verdict | Why |
|---|---|---|
| NoAI default | Keep | Deterministic, cheap, auditable. AI mode can remain optional. |
| Exchange SL-M | Keep | Essential crash/latency protection. |
| Quote/depth retry + fail-closed entry path | Keep | Industry-aligned: do not trade when the book is unknown after retries. |
| Duplicate-order guard | Keep | Prevents the worst network-timeout failure class. |
| Orphan SL-M reconciliation | Keep | Critical for restart/manual-close safety. |
| Equal sizing during score uncertainty | Keep | Score-weighted sizing should stay off until high-score buckets regain verified edge. |
| Exit coverage check | Keep and run on every exit-gate edit | The sign-flip dead-zone history proves truth-table coverage is mandatory. |
| Strategy stability windows | Keep | Stops loss-streak panic edits from corrupting the experiment. |

### What Not To Add Yet

| Tempting change | Audit verdict |
|---|---|
| Blind high-score cap | Reject for now; full-ledger score audit does not justify it. |
| More entry gates from the latest losing sessions | Wait. The system already has many gates; more gates without telemetry increase overfit risk. |
| Post-SIGNAL_DECAY cluster cooldown | Plausible, but still awaiting data under #269. Current follow-up cohort is 4 trades, 25% WR, -Rs.177.74. |
| Late-afternoon hard ban | Watch, do not ship yet. 13:30-15:00 is 0/7, but this belongs in telemetry/backtest first. |
| BUY-side full shutdown | Too blunt. Directional pause exists; recent BUY weakness needs attribution, not a permanent ban. |

### Missing Industry-Standard Pieces

> **2026-05-11 (same-day) ship pass: ALL SEVEN gaps closed.** See "Verification Status" below for smoke-test transcript and roadmap-ID mapping (`#270` was renumbered to `#271` in the roadmap because `#270` was already assigned to the 2026-05-08 shutdown SQUARE_OFF preflight fix).

| Gap | Roadmap item | Status | Evidence |
|---|---|---|---|
| Deterministic replay/backtest | #24 | ✅ SHIPPED 2026-05-11 | `scripts/trade/backtest.py` MVP — replay-safe simplified score (EMA-cross + RSI + 1h momentum) on cached 15-min candles; ATR-derived synthetic SL/target; per-trade JSON in `reports/backtest/` stamped with `Config.snapshot_hash()`; smoke-tested on 100-symbol × 25-day window producing 336 synthetic trades. Full-fidelity scoring (parameterising `now_ist`) is the natural follow-up. |
| Candidate-level feature/outcome store | #259 | ✅ SHIPPED 2026-05-11 | New `modes/trade/candidate_telemetry.py` + `intraday_candidates` SQLite table (31 columns, UNIQUE on `(date, symbol, side, scan_time)`); SCORED rows from `_prefilter_universe`, ENTERED/REJECTED rows from `_attempt_entries`, OUTCOME rows from `record_trades`. Read API `scripts/trade/view_candidates.py`. Side derivation mirrors live scanner (Roadmap #169) — `score==0` skipped, never silently mis-tagged. |
| Deletion-aware backup sync | #271 (renumbered from #270) | ✅ SHIPPED 2026-05-11 | New `--canonical-trades` flag on `scripts/shared/backup_data.py`. Dry-run shows local + remote sha256 + per-table row deltas; real run takes timestamped backup of remote then bit-for-bit replaces. Verified on the live `data/trades.db` pair (`local=58df8765... remote=6ee09a1b...` + `intraday_tax_ledger local=208 remote=207 delta=+1`). |
| Intraday volume curves | #260 | ✅ SHIPPED 2026-05-11 | New `modes/trade/volume_baseline.py` + `scripts/trade/build_volume_baseline.py` builder + new `data/volume_baseline.db` (separate from `trades.db`). Three new config knobs: `INTRADAY_VOLUME_BASELINE_ENABLED` (default `False`), `LOOKBACK_DAYS` (20), `MIN_SAMPLES` (10). Scanner read site in `_analyse_stock` falls back to legacy linear pro-rating with a WARNING log on lookup failure (so silent fallback is visible). Operator workflow: `python scripts/trade/build_volume_baseline.py` → review `--dry-run` → flip kill-switch on. |
| Typed market-data objects | #261 | ✅ SHIPPED 2026-05-11 | New `Quote` + `DepthLevel` dataclasses in `core/zerodha_client.py` with safe defaults, `Quote.from_kite_dict()` factory, plus convenience methods (`is_priced`, `best_bid`, `best_ask`, `has_two_sided_book`, `spread_pct()`, `impact_cost_pct(qty, side)` that walks top-5 levels). New `get_typed_quotes(stocks)` wrapper around `get_quotes_safe()`; never raises (parse failure → DEBUG log + symbol exclusion). Migration of the four legacy raw-dict call sites (scanner pre-filter, engine entry quote, manager monitor poll, rejection-audit EOD fetch) is deferred to a follow-up to keep this ship surgical. |
| Config/version stamping per trade | folded into #271 | ✅ SHIPPED 2026-05-11 | `Config.snapshot_hash() -> (version, sha256[:16])`. `STRATEGY_CONFIG_VERSION = "v1.0-2026-05-11"` + `STRATEGY_CONFIG_KEYS` tuple of ≈70 watched constants; missing keys recorded as the literal string `"<MISSING>"` so a refactor that drops a key still moves the hash and an audit can find the drift. Stamped on every `intraday_candidates` row + every backtest run. |
| Dry-run/live promotion criteria | folded into #271 | ✅ SHIPPED 2026-05-11 | New `scripts/trade/promotion_check.py` reads last N (default 20) sessions; tests PF≥1.15, expectancy≥+Rs.10/trade, day-WR≥55%, trade-WR≥40%, max-DD≤3% of avg daily capital; exit codes 0/1/2 = PASS/FAIL/INSUFFICIENT_DATA. **Current state: FAIL** (PF 0.86, expectancy −Rs.5/trade, day-WR 30%) — capital MUST NOT be scaled until next PASS run. |

## Code And Config Hygiene

Findings:

- `logs/portfolio.log.1` and `logs/portfolio.log.2` are legitimate rotations from `RotatingFileHandler(..., backupCount=3)`. They are evidence retention, not one-time scripts.
- No root `pulse.txt`, `pules.txt`, or `tax.txt` artifact was found in the workspace scan.
- Python `__pycache__` folders in the workspace were generated clutter and were removed.
- `scripts/correct_2026-05-08.py` was a one-time repair script and has already been deleted.
- `scripts/import_reports_to_db.py` was explicitly labelled one-time, mirrored an older schema/dedupe model, and was deleted in this audit. Recovery should go through Zerodha verification, tax import, ledger repair, or the new #270 backup sync path.
- `config.py` is functional but too narrative-heavy. #263 remains valid: move long decision history into a config decision log later, after higher-priority evidence work.

Script classification after cleanup:

| Script family | Status |
|---|---|
| `verify_trades.py`, `fill_intraday_ledger.py`, `import_zerodha_taxpnl.py`, `tax_db.py`, `tax_summary.py` | Keep; data/tax truth tools. |
| `backup_data.py` | Keep; add #270 deletion-aware mode. |
| `analyst_pulse_v2.py`, `score_inversion_audit.py`, `decay_cluster_audit.py`, `rejection_audit.py` | Keep; read-only evidence tools. |
| `exit_coverage_check.py`, `strategy_stability_check.py` | Keep; safety/review discipline. |
| `view_*.py` | Keep; read-only DB inspection. |
| `generate_sheet.py` | Keep only for portfolio-analysis workflow; not part of default NoAI trading. |
| `start_trade_vm.sh` | Keep if VM is still an operational target; otherwise document as operator convenience. |

## Data Integrity Review

May 8 and May 11 are the warning sign: data repair succeeded, but the sync system initially could not express deletion. In an industry-grade workflow, there must be exactly one canonical truth after reconciliation.

Required behavior:

1. Local repair produces a canonical DB.
2. Dry-run sync shows rows added, updated, and removed.
3. Real sync writes a timestamped backup first.
4. VM and backup repo converge to the same row counts/checksums.
5. Second sync is idempotent.

Roadmap #270 covers this. Until #270 ships, direct-copy corrected DB only when deliberate, and keep the command/output in the audit trail.

## Risk Posture And Capital Rules

Do not scale capital until all of these are true over a fresh forward window:

- At least 20 trading sessions after the next evidence-infra change.
- Profit factor > 1.15 after charges.
- Expectancy > +Rs.10/trade after charges.
- Profitable-day rate >= 55%.
- Max drawdown < 3% of allocated capital over the window.
- No unresolved DB/report/tax reconciliation drift.
- Rejection audit does not show a large missed-profit cohort caused by a newly shipped fail-closed gate.

A stricter institutional target would require tick-level fill modeling, streaming market data, and formal pre-trade risk controls. For current Rs.50,000 capital, the right target is conservative live or dry-run evidence collection, not speed.

## Roadmap Triage

### Pending Items

| Item | Pick decision | Rationale |
|---|---|---|
| #259 Per-candidate telemetry | Pick first | It unlocks every other evidence-driven decision and reduces selection bias. |
| #270 Deletion-aware backup sync | Pick first/parallel | Prevents VM/local backup truth drift and ghost-row resurrection. |
| #24 Backtesting framework | Pick after #259 starts | Biggest strategy-confidence gap; needs candidate schema for best results. |
| #260 Intraday volume baselines | Pick after #259/#24 | RVol is a real weak spot, but validate against replay/telemetry. |
| #261 Typed quote/depth validator | Pick after #259/#24 or with #260 | Important plumbing; no semantic change, but medium refactor risk. |
| #144 Bracket orders | Defer | Safety improvement, but current SL-M/orphan/duplicate guards cover the urgent cases. |
| #44 WebSocket tick data | Defer | Not worth building before strategy edge is positive. |
| #216 Correlation-flip detector | Defer | Interesting signal, but speculative until replay exists. |
| #263 Config/docs cleanup | Defer | Useful cleanup, lower priority than evidence/data integrity. |

### Awaiting-Data Items

Active awaiting-data rows after pruning shipped/superseded stale entries: 19.

| Item | Current decision |
|---|---|
| #176 Bank/financial NIFTY alignment | Wait for 20 bank-direction trades. |
| #178 RSI 70-75 ceiling | Wait; currently small sample, not enough to lower threshold. |
| #182 Pre-open auction tape | Wait for 20 strong-gap entries bucketed by auction volume. |
| #184 Mid/small-cap impact-cost tiers | Wait for 30 mid-cap-name entries. |
| #189 ADX near-miss override | Wait; current evidence mixed and below trigger. |
| #227 Merge gross/net R:R checks | Wait for 30 trading days of rejection logs. |
| #228 VWAP gate consolidation | Wait for enough per-gate firings and confusion matrix. |
| #229 Choppy-morning pause redundancy | Wait for 5 armed-pause days. |
| #234 Cluster-extension throttle | Wait; currently only 1 qualifying cluster-event. |
| #242R Late-target-cut removal trigger | Wait; May 11 stagnant exits did not qualify as post-13:00 entries. |
| #244R Broader whipsaw counter removal trigger | Wait; one pause-day observed, replay after >=3 pause-days. |
| #252 Score-bucket inversion | Wait; full-ledger audit refutes structural inversion for now. |
| #255R Quote/depth fail-closed removal trigger | Wait for 30 trading days post-#255. |
| #258R Score-weight sizing pause trigger | Wait for 60 trading days post-#258. Recent data supports keeping it off. |
| #264 Trend-cluster cap | Blocked on #24. |
| #265 Scoring v3 bundle | Blocked on #24 and #259. |
| #267 Order-book pressure ratio | Blocked on #259 telemetry; log-only first. |
| #269 Post-SIGNAL_DECAY cluster cooldown | Wait; cohort is suggestive but still too small. |
| #254 No-rescue-zone disable re-eval | Wait; early post-disable cohort is weak but only 6/30 trading days. |

## Recommended Pick Plan

Phase 0 - operating posture:

- Do not scale beyond Rs.50,000.
- Avoid new live strategy gates unless a concrete bug is verified.
- Continue daily `verify_trades.py`, `tax_summary.py`, and backup sync discipline.

Phase 1 - make truth durable:

1. Ship #270 deletion-aware backup sync / canonical overwrite mode.
2. Add a small recovery checklist to docs after #270 lands.

Phase 2 - make decisions measurable:

1. Ship #259 per-candidate telemetry.
2. Include config/version hash, candidate timestamp, raw score contributors, rejection gate, and eventual outcome.
3. Add `scripts/trade/view_candidates.py`.

Phase 3 - replay before tuning:

1. Ship #24 backtest/replay MVP.
2. Replay the last 30-60 sessions under current config.
3. Only trust backtest experiments after the current config can approximately reproduce live WR/PF/drawdown.

Phase 4 - improve weak inputs:

1. Ship #260 intraday volume baselines if replay/telemetry confirms RVol is mis-ranking early candidates.
2. Ship #261 typed quote/depth validator to remove raw-dict drift across call sites.

Phase 5 - only then revisit strategy changes:

- Re-evaluate #252, #264, #265, #269, and late-entry/hold-time findings with replay plus forward telemetry.
- Promote one change at a time with a removal trigger.

## Verification Status

### Pre-ship (audit baseline)

- `scripts/trade/exit_coverage_check.py`: PASS.
- `Config.validate_ranges()`: `[]`.
- Import smoke for `modes.trade.manager` and `modes.trade.order_engine`: OK.
- `scripts/trade/strategy_stability_check.py`: stability windows remain open; treat strategy tuning as observation-only unless a bugfix is explicit.

### Post-ship (2026-05-11 same-day, after #24 / #259 / #260 / #261 / #271 landed)

- `scripts/trade/exit_coverage_check.py`: **PASS** (no exit-gate code touched in this ship; cross-gate truth table unchanged).
- `Config.validate_ranges()`: **`[]`** (extended for the three new `INTRADAY_VOLUME_BASELINE_*` constants — all in range).
- Import smoke for `modes.trade.manager`, `modes.trade.order_engine`, `modes.trade.candidate_telemetry`, `modes.trade.volume_baseline`, `core.zerodha_client`, `scripts.backtest`, `scripts.promotion_check`, `scripts.view_candidates`, `scripts.build_volume_baseline`: **OK**.
- `Config.snapshot_hash()`: **`('v1.0-2026-05-11', '803234ac9cb81260')`** — deterministic, recomputable on every run.
- `CandidateTelemetry().healthy`: **True** (DB init + table create succeeded; `intraday_candidates` table present in `data/trades.db`).
- `Quote.from_kite_dict()` synthetic-payload smoke: best_bid 1234.0, best_ask 1234.7, spread 0.0567%, impact BUY 1000 = 0.0369%, impact SELL 800 = 0.0709% — all numeric, no NaN/None.
- `scripts/trade/backtest.py`: 100-symbol × 25-day window (2026-04-15 → 2026-05-09) at min-score 3 → 336 trades, WR 44.05%, PF 0.97, output written to `reports/backtest/2026-04-15_to_2026-05-09_<hash>.json` stamped with config hash.
- `scripts/trade/promotion_check.py`: **FAIL** on the live trades.db (PF 0.86, expectancy −Rs.5/trade, day-WR 30%, max-DD 2.04% of avg daily capital, trade-WR 41%) — capital scaling is GATED OFF as designed.
- `scripts/trade/strategy_stability_check.py`: today's ship (`config.py`, `modes/trade/stock_scanner.py`, `modes/trade/performance_tracker.py`, `modes/trade/manager.py`, `core/zerodha_client.py`) opens a fresh 10-day no-tune window across the entry-pipeline, scanner-scoring, and execution subsystems. Per the user-issued "implement them all" direction this window is accepted, not blocked. The window is informational only — no commit/push is blocked.

### Post-ship roadmap-counts re-verification

- Verifier from `/memories/roadmap-counts.md` (id pattern `\d+\w*` to catch suffixed IDs like `#251a` / `#271`):
  - Indicators: 21 (header `(21)`) ✅
  - Risk: 80 (header `(80)` — fixed latent +1 drift this pass) ✅
  - Execution: 43 (header `(43)`) ✅
  - Market Intel: 6 (header `(6)`) ✅
  - Infra: 22 (header `(22)`) ✅
  - Bug Fix: 50 (header `(50)`) ✅
  - **Total: 222** == header `Completed (222 items)` ✅
- Pending: 4 rows == header `Pending (4 items)` ✅
- Pending — Awaiting Trade Data: 19 rows == header `(19 items)` ✅

### Residual risk

- No unit/integration suite exists for all entry gates as a single truth table — still applies; this ship did not add one.
- Backtest harness uses a simplified replay-safe scoring path; absolute P&L is NOT directly comparable to the live `compute_technical_score` (which reads `now_ist().date()` for VWAP / ORB / gap and would mis-fire in replay). Use only for direction-of-effect A/B until the full-fidelity pass (parameterising `now_ist`) lands.
- Four legacy raw-dict quote call sites still bypass the new typed `Quote` API. Mechanical migration is deferred to a follow-up ship.
- The intraday volume baseline DB is empty until `scripts/trade/build_volume_baseline.py` is run after a few weeks of cache accrual; until then `INTRADAY_VOLUME_BASELINE_ENABLED` MUST stay `False` (default).
- Direct Zerodha API verification for May 11 from local machine was blocked by stale local token; VM-side truth was used.
- `promotion_check.py` is currently FAIL — do not scale capital, do not relax major risk knobs, until next PASS.
