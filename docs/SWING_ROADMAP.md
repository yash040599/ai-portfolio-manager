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

---

## Status Overview

### Pending (17 items)

Sorted by priority, then dependency order.

| # | Improvement | Priority | Impact | Effort |
|---|-------------|----------|--------|--------|
| S1 | Create `modes/swing/` package skeleton: `manager.py`, `scanner.py`, `signals.py`, `risk.py`, `types.py`, `persistence.py`, `report.py`, `ai_overlay.py`. | HIGH | Highest | Medium |
| S2 | Add `--mode swing` CLI dispatch in `main.py`. Default is NoAI report-only; `--ai` adds qualitative overlay; `--execute` remains blocked until S12-S14 ship. | HIGH | Highest | Low |
| S3 | Add `SWING_STRATEGY.md` and keep it synced with code as implementation lands. | HIGH | High | Low |
| S4 | Build `data/swing.db` schema: `swing_runs`, `swing_candidates`, `swing_positions`, `swing_events`. | HIGH | Highest | Medium |
| S5 | Implement long-term-holding isolation. Swing exits only ledger-managed quantity; overlapping symbols require explicit swing lots. | CRITICAL | Highest | Medium |
| S6 | Daily/weekly candle fetcher. Reuse `shared.candle_cache` and Zerodha historical API, but support 400-900 daily-candle lookbacks without intraday cache cleanup assumptions. | HIGH | High | Medium |
| S7 | NoAI swing scanner. Score breakout, pullback, trend-continuation, and support-reversal setups using daily + weekly context. | HIGH | Highest | High |
| S8 | Swing risk engine. Compute ATR/swing-low stop, 2R+ target, risk-per-trade position size, max sector exposure, max total open risk. | HIGH | Highest | Medium |
| S9 | Open-position review engine. Daily action: HOLD, TIGHTEN_STOP, PARTIAL_EXIT, FULL_EXIT, WATCH. | HIGH | High | Medium |
| S10 | Swing report writer under `reports/swing/<YYYY>/<MM>/swing_report_DD.txt` and JSON data dump. | HIGH | High | Low |
| S11 | Backtest/replay MVP for swing candidates over daily candles. Required before any live execution. | HIGH | Highest | High |
| S12 | Zerodha CNC order wrappers. Separate from intraday `place_order()` so MIS cannot leak into swing execution. | HIGH | Highest | Medium |
| S13 | GTT/OCO support or explicit manual-stop enforcement. Overnight swing risk must have a stop plan outside the Python process. | HIGH | Highest | High |
| S14 | Execution reconciliation. Match broker holdings/orders/fills back to `swing_positions` without touching long-term holdings. | HIGH | Highest | High |
| S15 | Optional AI overlay. Adds thesis, risks, recent news/catalysts, peer comparison, and qualitative warning flags. | MEDIUM | Medium | Medium |
| S16 | Dashboard `/swing` page. Show open swing book, candidates, stops, targets, R multiples, sector exposure, and action list. | MEDIUM | High | Medium |
| S17 | Tax/report integration. Keep swing realised P&L separate from intraday tax ledger and long-term portfolio analysis. | MEDIUM | High | Medium |

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

### Completed (0 items)

No implementation has shipped yet. This roadmap is the starting
planning artifact for the first swing-mode build.

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

**Fix.** Add `data/swing.db` with four tables:

```sql
CREATE TABLE swing_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    mode            TEXT NOT NULL,   -- NOAI | AI
    universe        TEXT,
    market_regime   TEXT,
    candidates_seen INTEGER,
    candidates_kept INTEGER,
    notes           TEXT
);

CREATE TABLE swing_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES swing_runs(run_id),
    symbol          TEXT NOT NULL,
    exchange        TEXT NOT NULL DEFAULT 'NSE',
    setup_type      TEXT NOT NULL,
    score           REAL,
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
    snapshot_json   TEXT NOT NULL
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
    source          TEXT NOT NULL,   -- TOOL | ADOPTED_MANUAL
    linked_run_id   INTEGER,
    notes           TEXT
);

CREATE TABLE swing_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER REFERENCES swing_positions(position_id),
    event_time      TEXT NOT NULL,
    event_type      TEXT NOT NULL,   -- ENTRY | STOP_MOVE | PARTIAL_EXIT | EXIT | REVIEW
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
3. New candidates: setup type, entry, stop, target, qty, score.
4. Rejections: top rejected candidates and reasons.
5. Risk summary: open risk, sector exposure, capital used, cash left.
6. AI overlay block when `--ai` is used.

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

### S16 - Dashboard `/swing`

**Today.** Dashboard has portfolio/analyse and intraday P&L pages.

**Fix.** Add a read-only swing page with:

- Open swing positions.
- Today's action list.
- Candidate table.
- Risk at stop.
- Sector exposure.
- Position chart with entry/stop/target markers.
- Run swing scan button.

### S17 - Tax/report integration

**Today.** Intraday tax ledger and long-term capital gains ledger are
separate.

**Fix.** Swing realised P&L needs its own reporting path. It should
not pollute intraday performance stats, and it should be distinguishable
from long-term portfolio analysis.

---

## Implementation Order

Recommended first build sequence:

1. S1-S4: package, CLI stub, DB, docs.
2. S5: holding isolation before any scanner excitement.
3. S6-S8: daily/weekly scanner + risk math.
4. S9-S10: position review + report.
5. S11: replay/backtest.
6. S15-S16: AI overlay + dashboard.
7. S12-S14: live execution only after evidence and safety are in place.

The first usable milestone is not live trading. It is a daily report
that says: "Here are open swing positions, here is what changed, here
are candidate setups, here is the exact risk if you take them."