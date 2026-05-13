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

### Pending (7 items)

Sorted by priority, then dependency order.

| # | Improvement | Priority | Impact | Effort |
|---|-------------|----------|--------|--------|
| S5 | Implement long-term-holding isolation. Swing exits only ledger-managed quantity; overlapping symbols require explicit swing lots. | CRITICAL | Highest | Medium |
| S11 | Backtest/replay MVP for swing candidates over daily candles. Required before any live execution. | HIGH | Highest | High |
| S12 | Zerodha CNC order wrappers. Separate from intraday `place_order()` so MIS cannot leak into swing execution. | HIGH | Highest | Medium |
| S13 | GTT/OCO support or explicit manual-stop enforcement. Overnight swing risk must have a stop plan outside the Python process. | HIGH | Highest | High |
| S14 | Execution reconciliation. Match broker holdings/orders/fills back to `swing_positions` without touching long-term holdings. | HIGH | Highest | High |
| S17 | Tax/report integration. Keep swing realised P&L and delivery/regulatory charges separate from intraday tax ledger and long-term portfolio analysis. | MEDIUM | High | Medium |

### Pending - Awaiting Data (0 items)

Awaiting-data items should be added only after S4/S7 persist enough
candidate and position history to test a measurable hypothesis.

Examples that should wait for data:

- Lowering or raising RSI thresholds for swing entries.
- Sector-specific score boosts.
- AI ranking as a hard gate.
- 60-minute timing candles.
- Market-open execution versus next-day limit orders.

### Removed (0 items)

No swing ideas have been rejected yet.

### Completed (11 items)

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