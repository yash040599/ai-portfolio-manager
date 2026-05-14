# Swing Trading Roadmap

Roadmap for `python main.py --mode swing`.

Swing mode is a separate delivery/CNC trading engine for multi-day
equity trades. It is not a slower version of `--mode trade`, and it
must not inherit intraday assumptions like MIS, square-off, opening
range timing, or same-day-only risk controls.

Scope:

- Multi-day NSE equity swing trades, typically 2 trading days to
  8 weeks.
- **Permanently report-only.** The bot never places broker orders;
  the operator trades manually on Zerodha Kite and updates the
  dashboard via Done / Mark-Exit-Done with the actual fill numbers.
  Decision recorded 2026-05-14 — see the Removed section for the
  four execution-automation items dropped.
- Default flow is **NoAI**. `--ai` is an optional qualitative
  overlay, user-initiated only.
- Primary signal timeframe is the completed **daily** candle, with
  weekly trend confirmation.
- Swing-managed quantities are tracked in `data/swing.db` purely as
  a manual log of what the operator entered on Kite.
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
reconciliation. Swing mode holds delivery positions overnight and
is report-only by design, so the lifecycle is fundamentally
different:

- Scan after market close using completed daily candles.
- Review positions once per day; no execution-side morning check is
  needed (the operator places orders themselves on Kite).
- Operator uses CNC delivery on Kite manually; the bot does not
  place orders.
- Operator places GTT manually for overnight risk.
- Track multi-day thesis, stop movement, and position age.

### 2. Long-term portfolio holdings are protected by being report-only

Zerodha demat holdings show long-term investments and swing
positions in the same holdings book. The pre-2026-05-14 design
treated this as a CRITICAL safety problem (see Removed S5) because
an automation that exited swing positions could accidentally sell
a long-term holding with the same symbol.

**The risk no longer exists.** Swing mode is report-only by
permanent design — the bot never sells anything; the operator
clicks Mark-Exit-Done with the quantity they actually sold on
Kite, which is by construction safe. `data/swing.db` is purely a
manual log of what the operator entered.

Example:

- User owns 100 INFY for long-term portfolio.
- User buys 10 INFY for swing on Kite manually.
- User clicks Done on the dashboard — swing ledger records 10.
- Zerodha holdings show 110 INFY.
- When the user exits the swing leg manually on Kite (10 shares),
  they click Mark-Exit-Done with qty=10. The S24 server-side
  validation caps qty at `swing_positions.managed_qty=10`, so
  even a fat-finger can't claim more than the swing book holds.
  The 100-share long-term lot in Kite holdings is never touched.

### 3. NoAI is the floor; AI is an overlay

NoAI produces every measured number: candles, moving averages,
relative strength, ATR, stop, target, risk, reward, position size,
and rejection reasons. AI can add thesis, news/catalyst context,
peer comparison, and risk narrative, but it must not overwrite the
deterministic signal or risk math.

All automatic runs are NoAI. AI is used only when the user explicitly
requests it from the dashboard or CLI for that run.

### 4. Plan only — the operator executes manually

Swing mode is **permanently report-only** (decision recorded
2026-05-14). It:

- Reviews existing swing positions and computes daily-action
  guidance (HOLD / TIGHTEN_STOP / WATCH / FULL_EXIT recommendations).
- Scans new candidates against the four technical setups + the
  52-week-high dip-buy strategy.
- Produces entry / stop / target / qty plans the operator can copy
  into Zerodha Kite.
- Persists every candidate and rejection for audit.
- Renders a clean report and dashboard page with Done / Skip /
  Mark-Exit-Done buttons that update `data/swing.db` only.

It does NOT place orders, never has, and per current scope never
will. If a future operator wants to automate execution they should
use `--mode trade` (intraday) or implement separately. Items that
previously tracked execution-automation work (Removed S5/S12/S13/S14)
can be re-promoted from the Removed section in `SWING_ROADMAP.md`
if that decision changes.

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

### Pending (2 items)

Sorted by priority, then dependency order.

> **Scope reminder (2026-05-14).** Swing mode is **report-only by
> permanent design** — the user places every order manually in
> Zerodha Kite and comes back to click Done / Mark Exit Done with
> the actual fill numbers. Items that previously tracked
> automation-side work (broker order wrappers, GTT/OCO,
> reconciliation, ledger isolation) have been moved to the Removed
> section as out of scope.

| # | Improvement | Priority | Impact | Effort |
|---|-------------|----------|--------|--------|
| S11 | Backtest/replay MVP for swing candidates over daily candles. Validates scoring + setup tweaks (S26 NR7, S27 weekly-trend gate, S28 sector bonus, S30+ Awaiting-Data items) on historical data before going live. Report-only by design — never places orders. | HIGH | Highest | High |
| S17 | Tax/report integration. Keep swing realised P&L and delivery/regulatory charges separate from intraday tax ledger and long-term portfolio analysis. Reads `swing_positions.gross_pnl` / `charges` / `net_pnl` written by the existing Done / Mark-Exit-Done flows; no broker calls needed. | MEDIUM | High | Medium |

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

### Removed (4 items)

All four items were removed on 2026-05-14 after the user confirmed
swing mode is **permanently report-only**: the operator places every
order manually in Zerodha Kite and updates the dashboard via Done /
Mark-Exit-Done with the actual fill numbers. Anything that would
have placed orders, reconciled broker state, or insisted on
mechanical exit safety is therefore out of scope.

| # | Original idea | Why removed |
|---|---------------|-------------|
| S5 | Long-term-holding isolation: swing exits only ledger-managed quantity; overlapping symbols require explicit swing lots. | Originally a CRITICAL safety item to stop the bot from accidentally selling a long-term holding when it tried to exit a swing position with the same symbol. **The bot never exits anything** — the operator clicks Mark-Exit-Done with their actual fill quantity, which is by construction ≤ what they actually sold in Kite. The risk this item guards against can no longer occur. |
| S12 | Zerodha CNC order wrappers separate from intraday `place_order()` so MIS cannot leak into swing execution. | Pure execution-automation. Manual entry in Zerodha Kite cannot leak between MIS and CNC because the operator picks the product type on the broker UI. |
| S13 | GTT/OCO support or explicit manual-stop enforcement for overnight swing risk. | Operator places GTT manually in Kite alongside the buy order (the existing "How to Enter in Zerodha" instruction card on `/swing` already tells them to do this). Bot doesn't need to know about it. |
| S14 | Execution reconciliation matching broker holdings/orders/fills back to `swing_positions` without touching long-term holdings. | The Done / Mark-Exit-Done flow IS the reconciliation — the operator types the actual fill numbers into the prompts (S24 hardened the input validation). No need for a Kite holdings/orders/fills sync because the swing ledger never has to match Kite automatically; it's a manual log. |

If the report-only stance ever changes, these items can be
re-promoted from this Removed section into Pending — the original
designs are intact in git history (commits `47210d1` and earlier).

### Completed (31 items)

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
| S38 | **Single-stock search box on `/swing` (BYO ticker → analyse → Done / Skip).** Origin: 2026-05-14 user request — "We should have a text field which takes the ticker name of the indian stock and then analyse just that and give details about it below. Lets say I want to know about SBIN to know if I should buy that then I should be able to come to the tool and add SBIN in text window and the tool should analyse all this for that stock and give me details below that text button for me to decide. This flow should also have a done (add button) to add it to swing open book. This individual search should also have the AI check box to populate AI info also". Four pieces: (a) `SwingScanner.scan_one(symbol, swing_capital, existing_positions)` — runs the full per-stock pipeline against ONE ticker and returns `(candidate, action)`; even rejected results come back so the user sees WHY their pick didn't qualify (no daily history, earnings blackout, already in book, sector cap hit, etc.). Reuses every helper the universe scan uses, so the verdict is identical to what a universe scan would produce on the same day. (b) `POST /api/swing/analyse_one?symbol=X&ai=1&capital=N` endpoint — wraps `scan_one`, optionally chains the per-stock AI overlay (~Rs.{CLAUDE_COST_PER_CALL}), persists the result as a one-row swing_runs entry + a PENDING ENTRY action so Done / Skip work just like a recommendation from a full scan, and returns the candidate JSON + `action_id` + AI overlay payload. (c) Dashboard `/swing` page gets a new "Analyse a Single Stock" card directly under the scan controls — text input, AI checkbox, Analyse button, result card renders below with the same Done / Skip controls + a link to the full detail page. (d) AI prompt rewritten to senior-analyst depth: now asks for THESIS / RECENT NEWS (60d) / FUNDAMENTAL CONTEXT (P/E vs sector median, ROE/ROCE band, debt sense, promoter holding) / PEER COMPARISON / RISKS / CORPORATE-ACTION SANITY CHECK / WHY IT MIGHT FAIL / VERDICT (BUY/WATCH/SKIP) — with hard rules forcing "Unknown" / "None known" instead of fabricated multiples, and capping the response at 400 words for 60-second readability. The richer prompt benefits both the per-scan overlay (S15) AND the per-stock buttons (S37, S38). | Indicators | 2026-05-14 |
| S39 | **AI prompt strengthened to senior-PM depth.** Folded into S38 because it ships in the same commit. Old prompt asked for thesis + risks + news + peers + why-fail (5 sections, generic). New prompt asks for: THESIS (with concrete catalysts) → RECENT NEWS / CATALYSTS (last 60 days, no fabricating) → FUNDAMENTAL CONTEXT (P/E vs sector median, ROE/ROCE band, debt sense, promoter holding stability + pledge status) → PEER COMPARISON (1-2 sector peers with one-line comparison each) → RISKS (specific to this name, not generic) → CORPORATE-ACTION SANITY CHECK (split / bonus / demerger in last 24m) → WHY IT MIGHT FAIL → VERDICT for a swing buyer (BUY / WATCH / SKIP). Hard rules force "Unknown" / "None known" / "Unsure" answers instead of fabricated numbers, and cap the total response at 400 words for 60-second readability. Same prompt path used by per-scan overlay (S15), per-stock detail-page button (S37), and the new search-box (S38). | Indicators | 2026-05-14 |
| S40 | **scan_one falls through to 52w-dip + better NONE rejection + detail-page 52w% + AI carry-forward.** Origin: 2026-05-14 user reported (a) "I don't see 52 week % point in the details page of a stock only on the table at the home page shows the dip"; (b) `SBIN search → REJECTED, No qualifying setup`; (c) "as SBIN is also in NIFTY 100 the AI response must also reflect in our tool recommended list where SBIN must also be there. For stocks outside NIFTY 100 we can store it separately so the AI response is sticky when searched next time with a time stamp of when this was analysed". Four fixes shipped together: (a) `SwingScanner.scan_one()` previously ran ONLY `classify_setup()` and bailed with "No qualifying setup" if no technical setup scored ≥ 2.0. SBIN at -21% from its 52w high is a textbook 52W_DIP candidate but slipped through because the technical scanner can't see the dip rule. New behaviour: when `classify_setup` returns NONE, scan_one falls through to the dip-buy rule using the same `Config.SWING_DIP_PCT` / `SWING_DIP_TARGET_PCT` / `SWING_DIP_BUY_AMOUNT` / `SWING_DIP_LOOKBACK_DAYS` knobs the universe `DipBuyScanner` uses, so the result is identical to what the universe scan would produce. Verified live: SBIN now returns `setup=52W_DIP, ACCEPTED, dip=20.62%, entry=974.60, stop=877.14, target=1091.55, qty=10`. (b) When BOTH technical AND dip-buy reject, the rejection message now lists per-setup scores ("BREAKOUT=1.5, PULLBACK=0.0, TREND_CONT=0.0, SUPPORT_REV=0.0; below 52w-dip threshold (5.2% < 18%)") so the user knows what was close to qualifying. (c) Detail page (`/swing/<symbol>`) gets a new "% Below 52w High" row in the kvtable next to entry/stop/target — qualifies as a 52w-dip buy when ≥18%, muted when <10%, hidden when ATH unknown. (d) New `latest_ai_overlay_for_symbol(symbol, max_age_days=7)` helper in `persistence.py` JOINs `swing_candidates` to `swing_runs` to find the most recent non-empty `ai_overlay_json` within freshness window. `SwingManager.run()` now has a "carry-forward" pass after the AI overlay step: any accepted candidate without an AI overlay inherits a recent cached one (≤ 7 days old). The detail page also shows a freshness badge ("Analysed 3 days ago (2026-05-11T... UTC)") above the AI text, with a "Click Analyse with AI to refresh" hint. Net effect: a stock the user paid Rs.3 to analyse on Monday still shows the AI thesis on Wednesday's recommendation table without a re-charge — and out-of-NIFTY-100 stocks (which the universe scan can't pick up) still hold their AI history when re-searched. | Indicators | 2026-05-14 |
| S41 | **Stock Health Check showed all zeros for SBIN — fix candidate-row resolution to prefer ACCEPTED over REJECTED.** Origin: 2026-05-14 user feedback after S40 shipped — "Stock Health Check shows 0 for all" on `/swing/SBIN`. Root cause: `persistence.py::candidate_by_symbol()` had a single rule "prefer non-dip-buy (technical) candidates because they carry richer indicator detail" — which made sense when both rows were ACCEPTED. But for SBIN the most recent technical row was REJECTED (NONE setup, all indicator fields blank by design) while the most recent dip-buy row was ACCEPTED with full indicator detail. The detail page picked the REJECTED technical row → all health-check cells read zero. New three-pass resolution order: (1) any ACCEPTED row, newest first; (2) most recent technical row regardless of status; (3) anything else. Pass 1 catches the live dip-buy candidate so the detail page shows real numbers. Verified live: SBIN now resolves to ACCEPTED 52W_DIP with `close=974.60, ema20=1056.80, sma50=1081.75, sma200=972.68, rsi=31.9, rs=-5.0%, vol_ratio=1.35, rr_ratio=1.20, weekly_up=False, 52w high=1234.70, dip%=20.62`. Health-check rows now render real values: long-term trend ✓ (974.60 > 972.68 SMA-200), medium-term ✗ (974.60 < 1081.75 SMA-50), RSI 31.9 just out of oversold, RS -5% vs NIFTY ✗, etc. | Indicators | 2026-05-14 |
| S42 | **Full-sweep data-staleness hardening pass: 6 verified bugs across persistence, dip scanner, dashboard, and zerodha login.** Origin: 2026-05-14 user request — "find these kind of bugs all over the swing and dashboard code; do a full sweep and review of code". Subagent surfaced 19 candidates; verification (per `/memories/review-subagents.md`) confirmed 6 real bugs, rejected 13 as either already-mitigated by S35-S41 or theoretical. Six fixes shipped: **(B1)** `latest_ai_overlay_for_symbol()` was comparing `datetime.utcnow()` cutoff against IST-shaped `swing_runs.finished_at` strings — semantically wrong (let in runs ~5h30m older than the freshness window). Switched to `now_ist()` so cutoff matches the stored timestamp shape. **(B2)** `latest_candidate_row_id_by_symbol()` had no status filter — same bug class as S41. The per-stock AI analyse endpoints (S37, S38) used it to find the row to persist Claude's response to; if a stale REJECTED row was newer it won, so the freshly-paid AI overlay was written to the wrong row and the detail page never showed it. New three-pass resolution: ACCEPTED first → SCORED/PLANNED → any. **(B3)** Dashboard dedup logic in `swing_page.py` had `if not getattr(c, "ath_price", 0): copy from dip-buy row` — but the technical scanner DOES populate `ath_price` (from a different lookback than the dip scanner), so the technical row's ATH always won and the dip-buy's reference high was silently dropped. Always copy now: the dip-buy scanner's reference is canonical because it was the value used to qualify the dip rule. **(A3)** `DipBuyScanner.scan()` called `compute_swing_indicators(candles)` with NO `nifty_candles` argument, so EVERY 52W_DIP candidate had `relative_strength=0.0`. Detail-page health check always showed "Beating the market? +0.0% vs NIFTY ✗" for dip-buys. Fixed by fetching NIFTY 50 candles once at scan start and passing them. Verified live: SBIN/INFY/TCS now show -4.96% / -14.01% / -13.07% RS vs NIFTY (all correctly negative for stocks in dip territory). **(F1)** `skip_action()` returned False on a double-click ("Skip an already-skipped action"), surfacing as a confusing JS error toast. Now idempotent — returns True iff the action ends up in SKIPPED state, regardless of which call flipped it. **(F2)** `confirm_action()` for ENTRY had no re-entrancy guard. Two concurrent Done clicks would both pass the `status='PENDING'` check (default-isolation SQLite is racy on read-then-write), both INSERT a fresh `swing_positions` row → duplicate position for the same action. New guard: `SELECT position_id FROM swing_positions WHERE linked_action_id = ?` before INSERT; if a row already exists, return it instead of duplicating. Done is now safe to spam-click. **(G1)** `core/zerodha_client.py::login(interactive=False)` fell through to `self._login_browser(login_url)` when the saved token was invalid — silently launching a browser on a server-side render path (live_quotes / swing_capital / scan_one). Pre-S42 a stale token surfaced as a hung dashboard render with no user-visible reason. Now raises `RuntimeError` so callers can record via `core.error_sink` and surface a Re-login toast. (Same bug class as S35 but the fix there only handled the swing-capital call site; this one fixes it at the source.) **Rejected (not bugs):** A1/A2/A4 (silent zeros surfaced via S35/S36 toast already), C1 (sparse-cache `{}` is intentional cache-not-yet-warm behaviour), C2 (corp-action handling is awaiting-data territory; S30+), D1 (S41 already mitigates the only high-leverage case — all-REJECTED rows showing zeros is correct since there's no real candidate), D2 (theoretical action-linking concern, the action carries position_id not candidate_id for exits), E1/E2 (no real unescaped path; division already guarded), G2 (cached quotes after auth failure — the toast already screams "Re-login"; clearing the cache would just blank the page on transient blips). | Risk | 2026-05-14 |

Also shipped (not in original roadmap):
- Dashboard quick-login: OTP-only assisted login on `/login` when `KITE_USER_ID` + `KITE_PASSWORD` are set in `.env`. No more URL paste-back needed for daily login.
- Zerodha `login_assisted_with_otp()` method in `core/zerodha_client.py`.

---

## Pending - Details

The two remaining Pending items (S11 + S17) are documented below.
Items shipped in the first build (S1-S10, S15, S16, S18) and the
follow-up hardening pass (S19-S29, S35-S39) have their full Details
in the per-item Completed-section descriptions above; their old
free-form Details blocks were removed in the 2026-05-14 report-only
scope cleanup so the doc reflects the current shipped contract.
The four execution-automation items removed in the same cleanup
(S5, S12, S13, S14) are documented in the Removed section above.

### S11 - Backtest/replay MVP

**Today.** Swing strategy parameters (the four technical setups, the
52w-high dip-buy mechanics, the S26 NR7 bonus, the S27 weekly-trend
hard gate, the S28 sector-rotation bonus, and the S30+ Awaiting-Data
items) have no in-repo backtest evidence base. The 121-combo X/Y
heatmap in the standalone [market-research](https://github.com/yash040599/market-research)
repo covered ATH-dip only; it does not validate the technical setups
or the cross-setup modifiers.

**Fix.** Add a daily-candle replay that can answer:

- Which candidates would have been selected each day for the last
  N years on the current scoring rules?
- Did the entry trigger on the next bar?
- Did stop or target hit first?
- Max adverse / favourable excursion per setup type?
- After-charges expectancy per setup type and per cross-setup
  modifier (NR7 bonus, sector-leader bonus, 52w-proximity).

Pure-offline. Never touches the broker. Output is a HTML/CSV report
that the swing-review skill can cite when promoting Awaiting-Data
items (S30-S34).

### S17 - Tax / report integration

**Today.** Intraday tax ledger and long-term capital-gains ledger
are separate. Swing realised P&L is written into
swing_positions.gross_pnl / charges / 
et_pnl by the existing
Done / Mark-Exit-Done flow, but no consumer surfaces it as a tax-
ready report.

**Fix.** Surface swing realised P&L on its own report path:

- New 
eports/swing/<YYYY>/swing_realised_<FY>.csv with one row
  per closed swing trade: symbol, entry date, entry price, exit
  date, exit price, qty, gross P&L, charges (broken down: STT,
  exchange transaction charges, GST, SEBI charges, stamp duty),
  net P&L.
- Dashboard /tax page learns to read it and split the existing
  intraday/long-term split into three buckets: intraday, swing,
  long-term.
- swing_positions.charge_breakdown_json is already populated by
  _estimate_delivery_charges() in persistence.py — the
  consumer just needs to deserialise it.

No broker calls needed; this is purely a reporting surface on
already-persisted data.

---

## Implementation Order

The first build shipped 2026-05-13 (S1-S4, S6-S10, S15-S16, S18) and
the 2026-05-14 hardening pass shipped S19-S29 + S35-S39 (see the
Completed section above for full per-item descriptions).

Remaining sequence (both report-only by design — the 2026-05-14
scope decision means there is no execution-automation phase to
sequence against):

1. **S11**: backtest / replay MVP — strategy-validation aid
   that lets the swing-review skill promote Awaiting-Data items
   (S30-S34) on real evidence rather than a small live sample.
2. **S17**: tax / report integration — surface
   swing_positions.gross_pnl / charges / 
et_pnl as a
   tax-ready CSV + dashboard /tax swing bucket.

Plus the five Awaiting-Data items (S30 Fib levels, S31 pairwise
correlation cap, S32 GAP_PLAY setup, S33 per-tier dip%, S34 fold
TREND_CONTINUATION) which only promote when their measurable
trigger fires.

The current usable milestone is: daily EOD scan on the dashboard
with priority-sorted recommendations, broker-entry instructions,
manual Done / Skip confirmation, open swing book with live prices
(5 s polling), per-stock AI analyse button, single-stock search
box, top-right error-toast surface, and realised P&L tracking with
delivery charges.
