# Swing Trading Roadmap

Roadmap for `python main.py --mode swing`.

Swing mode is a separate delivery/CNC trading engine for multi-day
equity trades. It is not a slower version of `--mode trade`, and it
must not inherit intraday assumptions like MIS, square-off, opening
range timing, or same-day-only risk controls.

Scope:

- Multi-day NSE equity swing trades, typically 2 trading days to
  8 weeks.
- Default flow is **NoAI** and **plan/report only**. `--ai` is an
  optional qualitative overlay. Live execution is a later, explicit
  `--execute` phase.
- Primary signal timeframe is the completed **daily** candle, with
  weekly trend confirmation.
- Swing-managed quantities are tracked in `data/swing.db` and are
  isolated from long-term portfolio holdings shown by `--mode analyze`.
- Default operator surface is the local dashboard `/swing`; terminal
  commands must expose the same scan, confirm, skip, and list actions.
- Dashboard prices and P&L must poll Zerodha live quotes for tracked
  symbols so displayed values are broker-current, not stale DB values.
- No intraday MIS lifecycle, no F&O, no overnight cash-equity shorts.

Sister docs:

- Long-term portfolio analyser: [ANALYZE_STRATEGY.md](ANALYZE_STRATEGY.md)
- Intraday trading strategy: [TRADE_STRATEGY.md](TRADE_STRATEGY.md)
- Tax guide: [TRADE_TAX_GUIDE.md](TRADE_TAX_GUIDE.md)

---

## Design Principles

### 1. Swing is its own mode, not intraday with a longer timer

Intraday mode is built around MIS, same-day square-off, 15-minute
candles, live quote polling, exchange SL-M, and end-of-day tax-ledger
reconciliation. Swing mode holds delivery positions overnight, so the
core lifecycle is different:

- Scan after market close using completed daily candles.
- Review positions once per day unless a live execution feature later
  needs an execution-only morning check.
- Use CNC delivery orders, not MIS.
- Use GTT/OCO or explicit manual-stop instructions for overnight risk.
- Track multi-day thesis, stop movement, and position age.

### 2. Long-term portfolio holdings are protected by a hard ledger boundary

Zerodha demat holdings will show long-term investments and swing
positions in the same holdings book. That is dangerous unless the tool
keeps its own swing ledger.

Swing mode therefore manages only the quantity recorded in
`data/swing.db::swing_positions.managed_qty`. It must never infer that
an entire Zerodha holding is swing-managed just because the symbol is
present in the demat account.

Example:

- User owns 100 INFY for long-term portfolio.
- Swing mode buys 10 INFY.
- Zerodha holdings show 110 INFY.
- Swing ledger shows managed quantity = 10.
- A swing exit can sell only 10 INFY, never 110.

If live Zerodha holdings show less quantity than the swing ledger says
is managed, swing mode must fail closed and ask for manual review.

### 3. NoAI is the floor; AI is an overlay

NoAI produces every measured number: candles, moving averages,
relative strength, ATR, stop, target, risk, reward, position size,
and rejection reasons. AI can add thesis, news/catalyst context,
peer comparison, and risk narrative, but it must not overwrite the
deterministic signal or risk math.

All automatic runs are NoAI. AI is used only when the user explicitly
requests it from the dashboard or CLI for that run.

### 4. Plan first, execute later

The first production version should not place orders. It should:

- Review existing swing positions.
- Scan new candidates.
- Produce entry/stop/target/qty plans.
- Persist every candidate and rejection.
- Render a clean report and dashboard page.

Live execution is blocked until CNC order placement, GTT/OCO handling,
ledger reconciliation, and long-term-holding isolation are tested.

### 5. Daily candles are the source of truth

Swing signals must be based on completed daily candles. Market-open
partial candles are not valid for primary signal generation. A morning
run can be useful later for order placement or gap-risk warnings, but
the main scan belongs after market close.

### 6. Every candidate must be persisted

Intraday mode already learned the cost of selection bias. Swing mode
must persist every scanned candidate, whether accepted or rejected, so
we can later audit:

- Missed winners.
- Rejected losers.
- Setup hit-rate by strategy type.
- Score bucket behavior.
- AI overlay usefulness.

### 7. Dashboard confirmation is the manual execution boundary

Swing mode does not need an always-on VM while it is report-only. The
daily workflow should run from the local dashboard:

1. User opens `/swing` and clicks Run scan after market close.
2. Dashboard shows a table report with setup, entry, stop, target,
   suggested quantity, risk, and live/latest price.
3. User manually takes or skips the broker action outside the tool.
4. User returns to the dashboard and clicks Done on the exact action.
5. Dashboard prompts for executed quantity and executed price, then
  records that as the swing-managed lot in `data/swing.db`.

The Done click is not a broker order. It is the user's explicit ledger
confirmation that this action was taken and should be tracked from the
next run onward. If the user does not click Done, the action remains
PENDING/EXPIRED and no swing-managed quantity is created.

Entry recommendations live in the top table. Once an ENTRY action is
confirmed, it moves out of the recommendation table and into the open
swing book below it. The open swing book has its own Exit/Mark Exit
Done control; confirming an exit closes tracking, computes realised
gross P&L, delivery/regulatory charges, and net P&L, then contributes
that result to the swing P&L summary at the top of the page.

Above the entry recommendation table, the dashboard must show a concise
broker-instruction card that tells the user exactly how to place the
recommended action in Zerodha: product type, side, order type, quantity,
entry/trigger/limit price, and stop/GTT reminder. Initially this is an
operator checklist only, not an automated broker order.

### 8. EOD scans are time-gated and idempotent

The daily scan must use completed daily candles only. Before market
close it should refuse to run and show a clear "wait for market close"
message. After market close:

- The Run scan button can manually start today's EOD scan.
- If the dashboard server is already running at 15:30 IST or later,
  it should auto-submit one NoAI scan for the trading day if none exists.
- If the dashboard was not open and the user first opens `/swing` at
  16:30 IST, the page should auto-trigger today's NoAI scan if it has
  not already completed or started.
- Same-day scan submission is single-flight/idempotent by run mode: if
  a NoAI scan is running or already completed for the trading day, do
  not start a duplicate NoAI scan unless the user explicitly requests a
  force re-run later.
- An AI run is a separate, explicit user action. If today's latest scan
  is NoAI and the user clicks Run AI swing analysis, run the same daily
  scan with the AI overlay and update candidates/actions with AI fields.
  Do not auto-run AI from timers or page-open logic.

---

## Status Overview

### Pending (6 items)

Sorted by priority, then dependency order.

| # | Improvement | Priority | Impact | Effort |
|---|-------------|----------|--------|--------|
| S5 | Implement long-term-holding isolation. Swing exits only ledger-managed quantity; overlapping symbols require explicit swing lots. | CRITICAL | Highest | Medium |
| S11 | Backtest/replay MVP for swing candidates over daily candles. Required before any live execution. | HIGH | Highest | High |
| S12 | Zerodha CNC order wrappers. Separate from intraday `place_order()` so MIS cannot leak into swing execution. | HIGH | Highest | Medium |
| S13 | GTT/OCO support or explicit manual-stop enforcement. Overnight swing risk must have a stop plan outside the Python process. | HIGH | Highest | High |
| S14 | Execution reconciliation. Match broker holdings/orders/fills back to `swing_positions` without touching long-term holdings. | HIGH | Highest | High |
| S17 | Tax/report integration. Keep swing realised P&L and delivery/regulatory charges separate from intraday tax ledger and long-term portfolio analysis. | MEDIUM | High | Medium |

### Pending - Awaiting Data (5 items)

Awaiting-data items should be added only after S4/S7 persist enough
candidate and position history to test a measurable hypothesis.

| # | Improvement | Trigger to promote |
|---|-------------|---------------------|
| S30 | **Fibonacci 38.2/50/61.8 retracement levels for PULLBACK_UPTREND scoring.** Current pullback scoring only checks proximity to EMA-20 and SMA-50; most swing playbooks weigh "pullback to 50% Fib of the prior leg" higher. Needs a swing-high/swing-low detection routine to compute "the prior leg", which is non-trivial — defer until live data justifies it. | After **≥30 PULLBACK_UPTREND closed trades** in `swing_positions`, query realised P&L by `(close_price / sma_50 - 1)` bucket. If the 0%-2% above-SMA50 bucket has materially worse net than the 2%-5% bucket, fib levels are likely the missing signal — promote and add a swing-high detector. |
| S31 | **Pairwise position correlation cap.** `risk.py` checks per-sector exposure but not pairwise stock correlation. A book of 8 mid-cap IT names is one bet, not eight. Needs a rolling-60d return matrix, real engineering effort. | After the live swing book has held **≥10 simultaneously-open positions for ≥30 calendar days** AND `realised_pnl_summary()` shows a single-day drawdown ≥3% of swing capital traceable to ≥3 positions moving in lockstep. |
| S32 | **Gap fade / gap-and-go as a new setup type (`GAP_PLAY`).** Daily gaps (open vs prior close) are common entry signals; today's scanners ignore the open-price field entirely. Adds another setup with its own scoring rules. | After **the user explicitly requests it OR ≥5 saved swing reports show ≥3% gap-day moves on universe symbols where the existing 4 setups produced no candidate**. |
| S33 | **Per-tier dip% (`SWING_DIP_PCT_BY_INDEX_TIER`).** Same `SWING_DIP_PCT = 18` is applied to NIFTY 50 large-caps (tighter spreads, tame ATR) and NIFTY 100/150/200 mid-caps (wider ATR, deeper drawdowns). A regime-aware cap would lift mid-cap dip threshold to ~22% and possibly tighten large-cap to ~16%. | After **≥30 closed dip-buy trades on NIFTY100-extra symbols** (not in NIFTY 50). Compare avg holding-period and net P&L vs the NIFTY-50 cohort; if mid-cap stops fire materially more often than large-cap stops on the same dip threshold, promote and add the per-tier dict. |
| S34 | **Fold TREND_CONTINUATION into BREAKOUT as a sub-stage.** Today TREND_CONTINUATION fires when SMAs are stacked and the name isn't extended — which is structurally a "breakout we missed entry on". Two named setups for the same situation costs readability and clutters the dashboard. | After **≥30 closed TREND_CONTINUATION trades**, compare net P&L vs BREAKOUT trades on the same symbols within ±10 trading days. If TREND_CONTINUATION net is within ±15% of BREAKOUT net, the setup adds nothing — fold it into BREAKOUT with a `stage='early'` / `stage='continuation'` discriminator. |

Examples that should also wait for data (not yet promoted to numbered items):

- Lowering or raising RSI thresholds for swing entries.
- Sector-specific score boosts (i.e. structural — the S28 dynamic sector bonus is data-free).
- AI ranking as a hard gate.
- 60-minute timing candles.
- Market-open execution versus next-day limit orders.

### Removed (0 items)

No swing ideas have been rejected yet.

### Completed (26 items)

Shipped 2026-05-13 in the first swing-mode build.

| # | Improvement | Category | Date |
|---|-------------|----------|------|
| S1 | Package skeleton: `modes/swing/` with `manager.py`, `scanner.py`, `signals.py`, `risk.py`, `types.py`, `persistence.py`, `report.py`, `ai_overlay.py`. | Infra | 2026-05-13 |
| S2 | CLI dispatch `--mode swing` in `main.py` with `--ai`, `--actions`, `--positions`, `--confirm`, `--skip` sub-commands. | Infra | 2026-05-13 |
| S3 | `SWING_STRATEGY.md` written and kept in sync with code. | Infra | 2026-05-13 |
| S4 | `data/swing.db` schema: `swing_runs`, `swing_candidates`, `swing_actions`, `swing_positions`, `swing_events`. Full CRUD + action confirm/skip. | Infra | 2026-05-13 |
| S6 | Daily candle fetcher via `shared.candle_cache` + Zerodha historical API. 750-day lookback. Pre-close scans use yesterday's completed candle. | Indicators | 2026-05-13 |
| S7 | NoAI swing scanner: breakout, pullback-uptrend, trend-continuation, support-reversal setup detectors with composite scoring. | Indicators | 2026-05-13 |
| S8 | Swing risk engine: ATR-based stop, 2R+ target, risk-budget position sizing, portfolio-level risk/sector caps, broker instruction generator. | Risk | 2026-05-13 |
| S9 | Open-position review engine: industry-standard exit stack (stop breach, target, SMA break, trailing, time stop, RS deterioration). | Execution | 2026-05-13 |
| S10 | Swing report writer: `reports/swing/<YYYY>/<MM>/swing_report_DD.txt` + JSON data dump. | Infra | 2026-05-13 |
| S15 | AI overlay: Claude qualitative overlay (thesis, risks, news, peers). User-initiated only; auto-scans always NoAI. | Indicators | 2026-05-13 |
| S16 | Dashboard `/swing` page: P&L summary, broker-entry instructions, priority-sorted recommendations with live quotes, open swing book, Done/Skip/Exit controls, capital input from Zerodha balance, loading banner with polling, auto-run note. Dashboard D30 (live_quotes.py) + D31 (swing_page.py + swing_actions.py). | Infra | 2026-05-13 |
| S18 | Terminal parity: `--actions`, `--positions`, `--confirm <ID> --qty N --price P`, `--skip <ID>`. Same service layer as dashboard. | Infra | 2026-05-13 |
| S19 | **Dip-buy defaults retuned to backtest sweet-spot.** Old hard-coded `DEFAULT_DIP_PCT = 15`, `DEFAULT_TARGET_PCT = 15` in `modes/swing/ath_scanner.py` ignored the calibration evidence. New `Config.SWING_DIP_PCT = 18`, `SWING_DIP_TARGET_PCT = 12`, `SWING_DIP_BUY_AMOUNT = 10000` are sourced from a 10y, 121-combo X/Y backtest in the standalone [market-research](https://github.com/yash040599/market-research) repo. Every cell of the X∈[10,20] × Y∈[10,20] grid was profitable; the (18,12) cell scores XIRR≈25.6% with 264 trades and ~95% win rate. (20,10) was the highest cell at 29.5% but (18,12) was chosen for ~2× higher dip frequency and a comfortable charges buffer on a Rs.10k ticket. (Knob names changed from `SWING_ATH_*` to `SWING_DIP_*` in the same-day S22 ATH→52w switch — read `config.py`'s "Swing — Dip-buy parameters" block for current spelling.) | Indicators | 2026-05-14 |
| S20 | **Unified entry-recommendations table on dashboard `/swing`.** The earlier UI showed two separate cards ("Dip-Buy Opportunities" and "Technical Entry Recommendations") which forced the user to compare ranks across cards. Replaced with a single unified table that includes a "% Below 52w High" column populated for **every** candidate. The table is sorted by a unified `priority_rank` assigned in `SwingManager` after both scanners complete (technical first, then dip-buy). New `SwingCandidate.ath_price` and `SwingCandidate.dip_from_ath_pct` fields persist the dip context for the audit trail (legacy field names — values held since S22 are 52-week-high references). | Infra | 2026-05-14 |
| S21 | **AI cost cap + pre-AI snapshot — fixes the "AI ran no stop and produced no report" failure mode.** Origin: 2026-05-14 user feedback on a NIFTY 100 AI swing scan that consumed credits for several minutes before being Ctrl+C'd. Two changes shipped together: (a) `Config.SWING_AI_MAX_CANDIDATES = 15` caps `overlay_ai_on_candidates()` at the top-N by unified `priority_rank` so a wide scan can never exceed `15 × CLAUDE_COST_PER_CALL = ~Rs.45` on the Pro plan (without the cap a NIFTY 100 scan after a correction could consume ~50 × Rs.3 = Rs.150). (b) `SwingManager.run()` now writes a pre-AI snapshot of the run to `data/swing.db` BEFORE the AI overlay loop starts and wraps the loop in try/except KeyboardInterrupt so a Ctrl+C still produces a saved scan + a written report (the AI fields are blank for the candidates that didn't get their turn). Dashboard `/swing` page now surfaces the cost preview above the Run Scan button (Rs.X cap, Rs.Y per single-stock click), and the AI confirm dialog echoes the same numbers from `window._swingAiPerCall` / `window._swingAiCap`. Same cost preview added to `/portfolio` analyse cards via `_ai_cost_card(holdings_count)`. New `copilot/swing-review.md` skill includes a mandatory Step 5 (AI cost reconciliation). | Risk | 2026-05-14 |
| S22 | **Dip-buy strategy switched from all-time-high to rolling 52-week-high reference.** Origin: 2026-05-14 user request "instead of all time high focus on 52 week high only". The dip-buy mechanic is unchanged (X% below a recent high → mean-revert Y%); only the lookback shrinks from `max(closes)` over 10y to `max(closes[-N:])` where N defaults to `Config.SWING_DIP_LOOKBACK_DAYS = 252` trading bars (~52 weeks). Switching the reference is intentional: (a) the 52w high resets every year, so the trigger is responsive to the current regime — a stock declining for 3y no longer sits silently 60% below an old ATH ignored by the gate; (b) the 52w high is the canonical large-cap-investor anchor (it's the standard breakout-watch level), so using it as the single reference removes a buy-side / trend-side blind spot in one window. Files touched: `config.py` (knob rename SWING_ATH_* → SWING_DIP_* + new SWING_DIP_LOOKBACK_DAYS), `modes/swing/ath_scanner.py` (rename ATHScanner → DipBuyScanner with legacy alias, slice `closes[-lookback_days:]` for the reference), `modes/swing/types.py` (new `SETUP_52W_DIP` constant + `DIP_SETUP_TYPES` set; legacy `SETUP_ATH_DIP` retained so old DB rows load), `modes/swing/persistence.py` (rename `ath_candidate_by_symbol` → `dip_candidate_by_symbol`, queries match both 'ATH_DIP' and '52W_DIP'), `modes/swing/manager.py` (new `DIP_SETUP_TYPES` lookup in unified-rank pass), `modes/swing/ai_overlay.py` (corp-action sanity-check section now keyed off `c.setup_type in ('52W_DIP','ATH_DIP')`), `modes/dashboard/swing_page.py` (column label "% Below ATH" → "% Below 52w High"; setup explainer adds 52W_DIP and labels ATH_DIP as "Legacy"), `docs/SWING_STRATEGY.md` §5.5 rewritten + new §5.6. Backtest delta: the original ATH heatmap and the post-COVID 5y slice of the 52w-high variant track within ~150 bps XIRR in the (X∈[16,20], Y∈[10,13]) sweet spot, so the (18,12) default carries over; a full 52w-high re-run is queued under the next swing-review pass per `copilot/swing-review.md` Step 7. | Indicators | 2026-05-14 |
| S23 | **52-week-high proximity scoring (cross-setup modifier).** Origin: 2026-05-14 user request "having 52week high data point also is good can we rate that in scoring also?". New `signals.py::score_52w_high_proximity()` returns a 0.0–2.0 bonus depending on how close the current close is to the rolling 52w high (closing AT or ABOVE the prior 52w high → +2.0; within 1.5% → +1.5; within 3% → +1.0; within 5% → +0.5). `classify_setup()` adds the bonus to continuation setups (BREAKOUT, TREND_CONTINUATION) and *subtracts the same magnitude* from mean-reversion setups (PULLBACK_UPTREND, SUPPORT_REVERSAL) — a "pullback" or "reversal" trigger that fires within 3% of the 52w high is by definition not a real pullback, so penalising it prevents fully-extended names slipping through under the wrong label. Magnitude (+0.5 to +2.0) was picked to match the existing volume / RS bumps so the modifier never single-handedly flips a setup verdict. Documented in `docs/SWING_STRATEGY.md` §5.6. | Indicators | 2026-05-14 |
| S24 | **Hardened the Done / Mark-Exit-Done input validation path.** Origin: 2026-05-14 senior-SDE review of the data flow `done-click → swing_actions(CONFIRMED) → swing_positions(OPEN) → mark-exit-done → swing_positions(CLOSED)`. Three latent bugs found and fixed: (a) `_serve_swing_action_confirm()` and `_serve_swing_position_exit()` both used `int(data.get("qty", 0))` / `float(data.get("price", 0))` straight to the SQL writer — a fat-fingered "0" or empty prompt would have silently INSERT'd `entry_price=0` (or `exit_price=0` with `gross_pnl=-entry*qty`, a catastrophic synthetic loss). Both endpoints now reject any non-positive qty/price with HTTP 400 + a descriptive JSON error; the exit endpoint additionally caps `qty <= pos.managed_qty` so a fat-fingered "10000" on a 50-share position can't mark CLOSED. (b) Dashboard JS `confirmAction()` / `exitPosition()` previously sent the raw `parseInt`/`parseFloat` result without validating `NaN`/<=0; new shared `_parsePosNum()` helper rejects empty / negative / non-numeric input client-side and surfaces an `alert()` so the round-trip never happens. (c) `confirmAction()` now also prompts for the stop-loss price (previously `stop` was always 0 → `confirm_action()` fell through to `action.suggested_stop`, which is fine for entries that take the bot's plan verbatim, but lost the case where the user's actual broker fill landed at a different stop). Status surface: failed confirms now reload only on success; failed exits surface the server's error message in an `alert()`. | Risk | 2026-05-14 |
| S25 | **Earnings-blackout filter on entry candidates.** Origin: 2026-05-14 financial-analyst review identified `Config.EARNINGS_BLACKOUT_SYMBOLS_2026` as plumbed-but-unused for swing mode (trade mode wires it via `_prefilter_universe()` for same-day only). Swing positions are held overnight, so a result-day gap on a freshly-bought name is the textbook overnight-risk failure mode the 10% hard stop doesn't size for. New `modes/swing/risk.py::earnings_blackout_symbols()` returns `{SYMBOL: 'YYYY-MM-DD'}` for any name announcing in the next 3 calendar days (today + 2). Both `SwingScanner` and `DipBuyScanner` call it once per scan and reject matching symbols pre-indicator-computation with a "Earnings on YYYY-MM-DD (T+0..2)" rejection reason. Respects the existing `EARNINGS_BLACKOUT_ENABLED` kill-switch. Empty calendar → zero behaviour change (the dict ships empty by default; user populates each Friday from the NSE corp-action calendar — same convention as trade-mode #219). | Risk | 2026-05-14 |
| S26 | **NR7 contraction → expansion bonus on BREAKOUT.** Origin: 2026-05-14 financial-analyst review noted `score_breakout()` only rewarded volume EXPANSION; the higher-EV breakouts come from names whose daily range has been *contracting* into a low base (Mark Minervini's VCP variant). New `nr7` boolean in `compute_swing_indicators()` — true when today's H–L is the smallest of the last 7 daily ranges. `score_breakout()` adds a `+1.0` bonus when both `nr7` AND `vol_ratio >= 1.2` are true (NR7 alone is not enough; we still need today's volume to confirm the breakout). Surfaced as "NR7 contraction → expansion (volume confirms)" in `reasons`. Pure additive — no new setup type; existing tests and ranks are unaffected when NR7 is False. | Indicators | 2026-05-14 |
| S27 | **SUPPORT_REVERSAL: weekly-trend-up hard gate.** Origin: 2026-05-14 financial-analyst review rated SUPPORT_REVERSAL 6/10 on "near 52w low + active downtrend = catching a falling knife". The earlier soft `score -= 0.5; reason="lower conviction"` admitted reversals on a still-falling weekly tape and produced a high stop-out rate. New behaviour: `score_support_reversal()` returns `(0.0, [])` immediately when `weekly_trend_up` is False — a real trend turn (10-week SMA rising again) is now the only valid trigger. Successful candidates surface "Weekly trend turned up (10-week SMA rising)" as an explicit confirmation reason. RSI 25–40 + near 52w low + near SMA-200 still apply on top of the gate. | Risk | 2026-05-14 |
| S28 | **Sector-rotation bonus.** Origin: 2026-05-14 financial-analyst review noted `SwingCandidate.sector` was captured but never scored. New helpers in `signals.py`: `compute_sector_rs(candidates)` returns `{SECTOR: mean_relative_strength}` from today's full scan pool (sectors with <2 candidates excluded so a single outlier can't flip the ranking); `top_n_sectors_by_rs(...)` returns the leaders. `SwingManager.run()` calls these once per scan AFTER both scanners complete and adds `+0.5` (`SECTOR_LEADER_BONUS`) to each accepted candidate sitting in the top-3 sectors with a "Sector leader: <SECTOR> (mean RS +X.X%)" reason. Bonus is applied BEFORE the unified-rank pass so sector leaders float to the top of their setup family. Manager-level — no per-symbol fundamental data needed; piggybacks on the relative-strength field every candidate already carries. | Indicators | 2026-05-14 |
| S29 | **`swing_runs.is_snapshot` column to mark pre-AI snapshot rows.** Origin: 2026-05-14 senior-SDE review flagged the S21 pre-AI snapshot as distinguishable only via `notes LIKE '%pre-AI snapshot%'` substring — fragile and undocumented. New `is_snapshot INTEGER NOT NULL DEFAULT 0` column on `swing_runs`, set to `1` in `save_run(..., is_snapshot=True)` from the pre-AI write path in `SwingManager.run()`. `latest_run()` / `latest_run_for_date()` / `latest_run_for_date_and_mode()` now filter `WHERE COALESCE(is_snapshot, 0) = 0` so the dashboard never picks a snapshot as the "current" run when an AI run completed afterwards. Snapshots remain queryable directly for audit. Migration: `_ensure_schema()` probes via `PRAGMA table_info(swing_runs)` and `ALTER TABLE ADD COLUMN` only when missing — legacy `data/swing.db` rows default `is_snapshot=0` (treated as real runs, matching pre-S29 behaviour). Verified with a synthetic old-schema DB. | Infra | 2026-05-14 |
| S35 | **Live-price polling actually wired up + swing capital fallback surfaces real reason.** Origin: 2026-05-14 user feedback — "I don't see the live prices being refreshed on the dashboard for analyze and swing pages" + "swing capital is shown as 1L why it is not fetching live data from zerodha?". Both bugs were the same shape: code that *claimed* to do the right thing but silently fell back to defaults. **(a) Live prices** — both `/portfolio` and `/swing` page copy claimed "Live prices refresh every 5 seconds" but the page only ran `get_live_quotes()` server-side at render time; no JS poller existed. New `/api/live_prices?symbols=A,B,C` endpoint in `modes/dashboard/server.py` (backed by the existing rate-limited `get_live_quotes()` so the broker is never hit faster than once per 5s, regardless of how many polls fire); price-bearing cells in both holdings tables tagged with `data-live-symbol="X" data-live-field="price|value|pnl|price_with_change"`; small JS poller at the tail of each page's script block walks tagged rows every 5s and rewrites only the marked cells. Static cells (avg / qty / sector / cap / weight / entry / stop / target) carry no markers and never get touched. A failed poll leaves the previous DOM untouched so a network blip doesn't blank out the table. **(b) Swing capital** — `swing_page.py::render_swing_page()` had `try / except Exception: pass` around the Zerodha funds fetch, swallowing the real reason silently. Three failure modes (no token / expired token / margins API error) now each surface a precise inline `capital_source_note` next to the input box (yellow text), and the success path shows "Live from Zerodha (Rs.X available margin)" in green. Also bypasses the `login(interactive=False)` browser-fallback trap (the Zerodha client's `interactive=False` only suppresses `input()`, it still falls through to `_login_browser()` when the saved token is invalid — a real bug for a server-side render path) by setting the access token directly on a fresh `KiteConnect` instance when a same-day token is on disk. | Infra | 2026-05-14 |
| S36 | **External-API error sink + top-right toast notifications + auth-pill auto-flip on token reject.** Origin: 2026-05-14 user feedback after S35 shipped — "Funds fetch failed (Incorrect `api_key` or `access_token`); using default Rs.1,00,000. I think this is why it also not fetching real values. If this is the case then why does it not ask for a reauth? These errors in any external api should be printed on top right like a notification so we can know the exact error". The `_auth_pill()` was previously **date-stamp-based** (just checked `saved.date == today`), so a token rejected by Zerodha (mismatched API key, IP-bound session killed by login from another machine, manual revocation in Kite console) still showed `Auth: OK` while every API call 401'd. Three pieces shipped together: (a) **`core/error_sink.py`** — thread-safe ring buffer (last 20 errors), `record_external_error(source, exc)` classifies the failure (`auth` / `rate_limit` / `network` / `other`), and on Zerodha auth-shaped failures (`Incorrect api_key`, `TokenException`, `401`) **automatically renames `data/access_token.json` → `.invalid`** so the next pill render correctly flips. `has_auth_invalid()` lets the server-side pill flip even when the file rename failed (e.g. permission denied). (b) **`/api/errors?since=<id>`** endpoint returns new errors only, monotonic ids let the JS poller dedupe across page reloads. (c) **`modes/dashboard/error_toast.py`** — a self-contained `<div>` + `<script>` block mounted in both `swing_page._wrap()` and `portfolio_page._wrap()` shells. Polls every 5 s, renders top-right toast cards with `SOURCE · KIND · HH:MM:SS` header, exact server error text in the body, and a "Open login page →" CTA on auth toasts. Auth toasts stay until manually dismissed; non-auth toasts auto-dismiss in 10 s. Successful re-login (`/api/login_submit` and `/api/login_assisted`) calls `core.error_sink.clear()` so stale toasts don't reappear. Hooks in `live_quotes.py` (which previously swallowed every Zerodha failure) and the `swing_page` capital fetch path. | Infra | 2026-05-14 |
| S37 | **Per-stock AI analyse button on the swing detail page.** Origin: 2026-05-14 user feedback — "I dont see the AI analysis button for each stock - so in detail page we should have the ai analyse button above the AI section so it can be populated for that stock only". The full-scan AI overlay (S15+S21) is bulk and capped at top-15; there was no way to add Claude colour for a single name without paying for the whole batch. Three pieces: (a) `modes/swing/ai_overlay.py::analyse_single_candidate(candidate, claude, log)` — does exactly one Claude call (~Rs.{CLAUDE_COST_PER_CALL}) and writes the response into `candidate.ai_overlay_json`; never raises (errors land in the payload as `{"error": "..."}`). (b) `modes/swing/persistence.py::latest_candidate_row_id_by_symbol(symbol)` + `update_candidate_ai_overlay(candidate_id, overlay_json)` — surgical row-level update so the overlay is persisted back to the row the detail page is showing. (c) `POST /api/swing/ai_analyse/<SYMBOL>` endpoint in `modes/dashboard/server.py` — wires the helper to a new "Analyse with AI (~Rs.{CLAUDE_COST_PER_CALL})" button placed ABOVE the AI Analysis card on the detail page; cost-confirm dialog before the call, spinner during, replaces the host `<div>` content with the response. Claude failures (auth, rate-limit, etc.) automatically surface via `core.error_sink` as a top-right toast, so users can see exactly why an AI analyse failed (consistent with S36). | Indicators | 2026-05-14 |

Also shipped (not in original roadmap):
- Dashboard quick-login: OTP-only assisted login on `/login` when `KITE_USER_ID` + `KITE_PASSWORD` are set in `.env`. No more URL paste-back needed for daily login.
- Zerodha `login_assisted_with_otp()` method in `core/zerodha_client.py`.

---

## Pending - Details

### S1 - `modes/swing/` package skeleton

**Today.** No swing mode exists. The closest code paths are intraday
trade mode and long-term analyse mode, but neither lifecycle is safe
to reuse wholesale.

**Fix.** Create a separate package:

```text
modes/swing/
  __init__.py
  manager.py
  scanner.py
  signals.py
  risk.py
  types.py
  persistence.py
  report.py
  ai_overlay.py
```

`manager.py` orchestrates. `scanner.py` fetches candles and builds
candidate records. `signals.py` owns setup classification. `risk.py`
owns stops, targets, sizing, and exposure checks. `persistence.py`
is the DB contract. `report.py` renders operator output.

### S2 - CLI dispatch

**Today.** `main.py` accepts `analyze`, `trade`, `login`, and
`dashboard` only.

**Fix.** Add `swing` as a first-class mode with clear flags:

```text
python main.py --mode swing             # NoAI scan + review + report
python main.py --mode swing --ai        # NoAI base + Claude overlay
python main.py --mode swing --execute   # later: live CNC/GTT execution
python main.py --mode swing --dryrun    # later: simulate order actions
python main.py --mode swing --nifty 100 # optional universe override
```

`--execute` must initially print a blocked message until S12-S14 are
complete.

### S3 - Strategy reference doc

**Today.** The user needs a readable strategy reference before the
implementation starts.

**Fix.** Keep [SWING_STRATEGY.md](SWING_STRATEGY.md) in sync with
the code. Any new setup, gate, risk formula, CLI flag, or execution
behavior must update the strategy doc in the same change.

### S4 - Swing DB

**Today.** Intraday uses `data/trades.db`; analyse uses
`data/portfolio_analyses.db`. Swing needs its own source of truth.

**Fix.** Add `data/swing.db` with five tables:

```sql
CREATE TABLE swing_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    mode            TEXT NOT NULL,   -- NOAI | AI
    universe        TEXT,
    market_regime   TEXT,
    run_for_date    TEXT,
    trigger_source  TEXT,     -- CLI | DASHBOARD_BUTTON | DASHBOARD_AUTO | PAGE_OPEN_AUTO
    user_requested_ai INTEGER NOT NULL DEFAULT 0,
    rerun_of_run_id INTEGER,
    rerun_reason    TEXT,
    candidates_seen INTEGER,
    candidates_kept INTEGER,
    blocked_reason  TEXT,
    notes           TEXT
);

CREATE TABLE swing_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES swing_runs(run_id),
    symbol          TEXT NOT NULL,
    exchange        TEXT NOT NULL DEFAULT 'NSE',
    setup_type      TEXT NOT NULL,
    score           REAL,
    priority_rank   INTEGER,
    priority_score  REAL,
    close_price     REAL,
    entry_price     REAL,
    stop_price      REAL,
    target_price    REAL,
    risk_rupees     REAL,
    reward_rupees   REAL,
    rr_ratio        REAL,
    suggested_qty   INTEGER,
    status          TEXT NOT NULL,   -- SCORED | ACCEPTED | REJECTED | PLANNED
    rejected_reason TEXT,
    broker_instruction_json TEXT,
    ai_overlay_json TEXT,
    snapshot_json   TEXT NOT NULL
);

CREATE TABLE swing_actions (
    action_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES swing_runs(run_id),
    candidate_id    INTEGER REFERENCES swing_candidates(id),
    position_id     INTEGER REFERENCES swing_positions(position_id),
    symbol          TEXT NOT NULL,
    exchange        TEXT NOT NULL DEFAULT 'NSE',
    action_type     TEXT NOT NULL,   -- ENTRY | TIGHTEN_STOP | PARTIAL_EXIT | FULL_EXIT | WATCH
    status          TEXT NOT NULL,   -- PENDING | CONFIRMED | SKIPPED | EXPIRED | MANUAL_REVIEW
    suggested_qty   INTEGER,
    suggested_price REAL,
    suggested_stop  REAL,
    suggested_target REAL,
    priority_rank   INTEGER,
    live_price      REAL,
    broker_instruction_json TEXT,
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    confirmed_at    TEXT,
    executed_qty    INTEGER,
    executed_price  REAL,
    confirmed_stop  REAL,
    confirmation_source TEXT,        -- DASHBOARD | CLI | RECONCILED
    notes           TEXT
);

CREATE TABLE swing_positions (
    position_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    exchange        TEXT NOT NULL DEFAULT 'NSE',
    side            TEXT NOT NULL,   -- BUY only initially
    managed_qty     INTEGER NOT NULL,
    entry_price     REAL NOT NULL,
    entry_date      TEXT NOT NULL,
    stop_price      REAL NOT NULL,
    target_price    REAL,
    trailing_stop   REAL,
    status          TEXT NOT NULL,   -- OPEN | CLOSED | MANUAL_REVIEW
    source          TEXT NOT NULL,   -- DASHBOARD_DONE | CLI_CONFIRM | ADOPTED_MANUAL | RECONCILED
    linked_run_id   INTEGER,
    linked_action_id INTEGER,
    exit_date       TEXT,
    exit_price      REAL,
    exit_qty        INTEGER,
    gross_pnl       REAL,
    charges         REAL,
    net_pnl         REAL,
    charge_breakdown_json TEXT,
    closed_action_id INTEGER,
    notes           TEXT
);

CREATE TABLE swing_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER REFERENCES swing_positions(position_id),
    event_time      TEXT NOT NULL,
    event_type      TEXT NOT NULL,   -- ENTRY | STOP_MOVE | PARTIAL_EXIT | EXIT | EXIT_CONFIRMED | REVIEW
    old_value       TEXT,
    new_value       TEXT,
    reason          TEXT,
    event_json      TEXT
);
```

### S5 - Long-term-holding isolation

**Today.** Intraday positions are isolated naturally by MIS. Swing
positions will appear in the same Zerodha holdings book as long-term
investments, so the tool needs its own lot boundary.

**Fix.** Swing mode manages only `swing_positions.managed_qty`.
Before any planned exit it must verify:

1. The position exists in `swing_positions` and status is OPEN.
2. Zerodha holdings quantity for the symbol is at least `managed_qty`.
3. The planned sell quantity is no greater than `managed_qty`.
4. If a symbol overlaps with long-term holdings, the report must show
   both quantities separately:
   - Zerodha total qty.
   - Swing-managed qty.
   - Long-term/unmanaged qty = total - swing-managed.

No automatic sale is allowed when these checks disagree.

### S6 - Daily/weekly candle fetcher

**Today.** Intraday scanner fetches 15-minute and daily candles, with
cache cleanup tuned around intraday history.

**Fix.** Swing needs long daily history:

- 252 trading days minimum for 52-week levels.
- 400 trading days preferred for SMA-200 warmup and robust trend.
- 750-900 calendar days useful for weekly candles and regime context.

Weekly candles can be built from daily candles locally. The fetcher
should use `shared.candle_cache` but avoid deleting long daily history
needed by swing and analyse.

### S7 - NoAI swing scanner

**Today.** Intraday scanner's indicators are useful, but its scoring
weights are tuned for 15-minute momentum.

**Fix.** Build a swing-specific score from industry-standard setups:

- Breakout: close above 20/50-day high, volume confirmation, positive
  relative strength versus NIFTY.
- Pullback in uptrend: above SMA-50/SMA-200, pullback toward EMA-20 or
  SMA-50, RSI 40-60, bullish candle confirmation.
- Trend continuation: SMA-20 > SMA-50 > SMA-200, weekly trend aligned,
  price not too extended from EMA-20.
- Support reversal: near SMA-200/prior support/52-week support with
  bullish reversal pattern and improving RSI. Smaller default size.

### S8 - Swing risk engine

**Today.** Intraday risk uses same-day ATR stops, MIS, and charge
constraints. Swing risk needs overnight-aware sizing.

**Fix.** Compute:

- Stop = below swing low or `2 * ATR(14)`, whichever better reflects
  structure.
- Target = at least 2R; prefer 2.5R+.
- Risk per trade = configurable, default 0.5% of swing capital.
- Qty = `risk_rupees / abs(entry - stop)`.
- Max position value = configurable, default 10-15% of swing capital.
- Max total open risk = configurable, default 5% of swing capital.
- Max sector exposure = configurable, default 25-30%.

### S9 - Open-position review engine

**Today.** There is no multi-day review loop.

**Fix.** Each daily run reviews existing swing positions before
looking for new entries. Possible actions:

- HOLD: thesis intact.
- TIGHTEN_STOP: price advanced enough to reduce risk.
- PARTIAL_EXIT: target zone or extended move reached.
- FULL_EXIT: stop/trend/time rule says exit.
- WATCH: no action, but thesis warning logged.

The exit engine must be smarter than "hold until target or stop". It
should use an industry-standard stack of exit signals:

- Hard stop breach on close or broker-side stop confirmation.
- Target/extension zone reached.
- ATR or swing-low trailing stop.
- Break of SMA-50/EMA-20 for trend setups, depending on setup type.
- Weekly trend break or lower-high/lower-low structure.
- Relative strength deterioration versus NIFTY.
- High-volume distribution or bearish reversal candle at resistance.
- Time stop when there is no progress after the configured holding
  window.
- Gap-down or event-risk warning that invalidates the setup.

Exit recommendations appear on the open swing book row. The dashboard
Exit/Mark Exit Done control is a ledger confirmation in report-only
mode: it asks for executed exit quantity and price, computes realised
P&L and charges, and then closes or reduces the tracked swing position.

### S10 - Report writer

**Today.** Analyse and trade modes already write human-readable
reports and JSON data dumps.

**Fix.** Write swing reports to:

```text
reports/swing/<YYYY>/<MM>/swing_report_DD.txt
reports/swing/<YYYY>/<MM>/swing_data_DD.json
```

Report sections:

1. Header: run mode, universe, market regime, generated time.
2. Open swing book: symbol, qty, entry, stop, target, R multiple,
   action.
3. New entry candidates: setup type, entry, stop, target, qty, score.
4. Rejections: top rejected candidates and reasons.
5. Realised swing P&L summary: gross P&L, charges, net P&L.
6. Risk summary: open risk, sector exposure, capital used, cash left.
7. AI overlay block when `--ai` is used.

### S11 - Backtest/replay MVP

**Today.** Swing strategy has no evidence base in this repo.

**Fix.** Add a daily-candle replay that can answer:

- Which candidates would have been selected each day?
- Did entry trigger next day?
- Did stop or target hit first?
- What was max adverse excursion and max favourable excursion?
- Which setup type has positive expectancy after charges/slippage?

No live execution should ship before this exists.

### S12 - CNC order wrappers

**Today.** `core.zerodha_client.place_order()` hardcodes MIS for
intraday.

**Fix.** Add separate methods for delivery orders, for example:

```python
place_cnc_order(symbol, exchange, qty, side, order_type="LIMIT", price=0)
```

Do not add a generic `product` argument to the intraday method unless
the call sites are audited. A separate method is safer.

### S13 - GTT/OCO or manual-stop enforcement

**Today.** Intraday uses exchange SL-M, but MIS SL-M orders are not an
overnight swing solution.

**Fix.** Prefer Zerodha GTT/OCO for delivery positions when available.
If API support is unavailable or unreliable, swing mode must print a
manual action list and refuse unattended live execution.

### S14 - Execution reconciliation

**Today.** Intraday reconciles same-day MIS positions. Swing needs
multi-day reconciliation.

**Fix.** On every run:

- Read Zerodha holdings.
- Read open swing positions from `data/swing.db`.
- Verify managed quantities still exist.
- Detect manual exits or partial exits.
- Detect corporate actions that change quantity/price.
- Mark mismatches as MANUAL_REVIEW, not auto-fixed silently.

### S15 - AI overlay

**Today.** Analyse mode has a clean AI overlay pattern.

**Fix.** Reuse that philosophy. Claude receives fixed NoAI data and
fills only qualitative fields:

- Thesis.
- Risks.
- News/catalyst context.
- Peer comparison.
- Reasons to skip despite technical setup.
- Change since prior scan.

AI run semantics:

- Default scan is always NoAI.
- Dashboard auto-runs and page-open auto-runs are always NoAI.
- The dashboard may show an AI mode checkbox or "Run AI swing analysis"
  button, but using AI requires an explicit user click.
- If today's latest completed scan is NoAI and the user requests AI,
  run a new AI scan for the same trading day and update the report with
  AI fields.
- If today's latest completed scan is already AI, do not rerun just
  because the user opens the page; only a manual force re-run should do
  that.
- AI must not change entry price, stop, target, quantity, risk, or R:R;
  it can change qualitative ranking notes, risk warnings, and thesis.

### S16 - Dashboard `/swing`

**Today.** Dashboard has portfolio/analyse and intraday P&L pages.
Swing does not need a VM in the report-only phase because no automated
broker order loop is running.

**Fix.** Add an interactive local swing page. It may write only to
`data/swing.db`; it must not place broker orders until S12-S14 ship.

Required sections:

- Top summary: realised swing gross P&L, charges, net P&L, open
  unrealised P&L, and latest quote timestamp.
- Run scan button for the EOD scan. Before market close, it must show
  "wait for market close" and refuse to scan incomplete daily candles.
- NoAI/AI control: default NoAI. AI mode is user-initiated only and must
  never be used by the automatic 15:30/page-open scans.
- Broker instruction card above the entry table. It explains how to add
  the stock in Zerodha: buy CNC/delivery equity on NSE, not MIS or F&O;
  use the suggested quantity; use the recommended limit/trigger price;
  set or note the stop/GTT plan; then return and click Done after the
  broker order actually executes.
- If Zerodha supports an entry trigger such as AMO/GTT/trigger-limit for
  the symbol and product, show a separate "set for next market open"
  checklist so the user can place the trigger after EOD. The dashboard
  still waits for the user to confirm Done the next day with actual
  executed quantity and price before tracking the position.
- Entry recommendation table with action id, symbol, setup, score, entry,
  stop, target, suggested quantity, risk, R:R, live/latest price,
  and reason.
- Pending action controls: Done, Skip, and Manual review.
- Done modal/form asking at minimum executed quantity and executed
  price for entry/exit actions, or confirmed stop price for stop-only
  actions. Optional fields can include execution date/time, order id,
  GTT confirmation, and notes.
- Open swing book below the report table, fed only from confirmed
  swing positions. It shows symbol, managed quantity, entry, live
  Zerodha price, stop, target, unrealised P&L, R multiple, age, and
  daily action such as HOLD, TIGHTEN_STOP, PARTIAL_EXIT, FULL_EXIT, or
  WATCH.
- Exit/Mark Exit Done control on each open swing row. In report-only
  mode this does not place a broker order; it confirms that the user
  already exited manually, asks for exit quantity and price, computes
  gross P&L, delivery/regulatory charges, and net P&L, and closes or
  reduces the tracked position.
- Sector exposure and open risk at stop.
- Position chart with entry/stop/target markers.

Important behavior:

- Recommendations must be sorted by priority, with rank 1 as the best
  candidate. Priority combines deterministic setup score, R:R, liquidity,
  risk fit, sector/open-risk constraints, and optional AI warning/boost
  notes when the user explicitly ran AI.
- Clicking Done creates or updates the swing-managed lot from the
  user-entered executed quantity and price.
- A candidate must stop appearing as a pending entry after it is
  confirmed, skipped, or expired.
- Open positions appear below the report table and remain tracked
  until the exit/partial-exit action is confirmed.
- The page polls Zerodha live quotes for symbols in both the entry
  recommendation table and open swing book, then recomputes displayed
  current price and P&L from the latest quote.
- Realised swing P&L must include regulatory/delivery charges, not just
  gross entry-vs-exit difference.
- A dashboard server that is running after 15:30 IST should auto-submit
  one NoAI EOD scan per trading day. Opening `/swing` after 15:30 IST
  should also auto-submit today's NoAI scan if no run exists yet.
- If a NoAI scan exists and the user explicitly requests AI, run an AI
  overlay scan even though the NoAI scan already exists. Same-mode runs
  remain idempotent unless force rerun is added later.
- If a confirmed position overlaps with long-term holdings, the page
  must show Zerodha total quantity, swing-managed quantity, and
  unmanaged quantity separately.

Dashboard API sketch:

```text
GET  /swing
GET  /api/swing/data
GET  /api/swing/live
POST /api/swing/run
POST /api/swing/actions/<action_id>/confirm
POST /api/swing/actions/<action_id>/skip
POST /api/swing/actions/<action_id>/manual_review
POST /api/swing/positions/<position_id>/exit
```

### S17 - Tax/report integration

**Today.** Intraday tax ledger and long-term capital gains ledger are
separate.

**Fix.** Swing realised P&L needs its own reporting path. It should
not pollute intraday performance stats, and it should be distinguishable
from long-term portfolio analysis.

Realised P&L must store:

- Entry value.
- Exit value.
- Gross P&L.
- Delivery/regulatory charges, with a charge-breakdown JSON.
- Net P&L after charges.

Delivery equity brokerage is usually zero at Zerodha, but statutory
charges still matter: STT, exchange transaction charges, GST on
brokerage/transaction charges where applicable, SEBI charges, stamp
duty on buy-side, and any other broker-reported charges. The calculator
must be delivery/CNC-aware and not reuse intraday MIS assumptions
blindly.

### S18 - Terminal parity for dashboard actions

**Today.** Swing is planned as a dashboard-first workflow, but every
state-changing dashboard action must also be possible from terminal for
backup, scripting, and auditability.

**Fix.** The dashboard API and terminal commands should call the same
service layer, so there is only one implementation of scan/confirm/skip.

Planned commands:

```text
python main.py --mode swing                         # EOD scan + print table
python main.py --mode swing --ai                    # user-initiated AI scan
python main.py --mode swing --actions               # pending actions
python main.py --mode swing --positions             # open swing book
python main.py --mode swing --confirm <ACTION_ID> --qty 10 --price 1450.50
python main.py --mode swing --close-position <POSITION_ID> --qty 10 --price 1510.25
python main.py --mode swing --live                  # live prices + open swing book P&L
python main.py --mode swing --skip <ACTION_ID> --reason "not taken"
python main.py --mode swing --manual-review <ACTION_ID> --reason "broker mismatch"
```

CLI confirmations must write the same fields as the dashboard Done
form: action id, executed quantity, executed price or confirmed stop,
confirmation time, source, and optional notes.

The default EOD scan command must refuse to scan before market close
and print the same "wait for market close" reason as the dashboard.

---

## Implementation Order

First build shipped 2026-05-13: S1-S4, S6-S10, S15-S16, S18.

Remaining sequence:

1. S5: holding isolation (safety gate before live execution).
2. S11: backtest/replay MVP (evidence before live execution).
3. S12-S14: CNC wrappers, GTT/OCO, execution reconciliation.
4. S17: tax/report integration for swing realised P&L.

The current usable milestone is: daily EOD scan on the dashboard with
priority-sorted recommendations, broker-entry instructions, manual
Done/Skip confirmation, open swing book with live prices, and
realised P&L tracking with delivery charges.