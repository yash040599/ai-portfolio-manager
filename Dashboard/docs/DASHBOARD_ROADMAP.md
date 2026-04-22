# Profitability Dashboard — Roadmap

> Separate from `STRATEGY_ROADMAP.md` because the dashboard is a pure
> **read-only analytics layer** that will grow over many sessions.
> Touches NO strategy/order/config code. Safe to iterate on
> independently of live trading.
>
> **Pickup window:** weekend sessions when live trading is closed.
>
> **Location:** all dashboard code, docs, templates, and tests live
> under the top-level `Dashboard/` folder, isolated from the trading
> bot. Trading bot remains the source of truth for the SQLite DB and
> Zerodha sheet files; the dashboard only **reads** them.

---

## Folder Structure

```
Dashboard/
├── docs/
│   └── DASHBOARD_ROADMAP.md      # this file
├── __init__.py
├── cli.py                        # argparse entry; called from main.py --mode dashboard
├── data_layer.py                 # all DB reads (sheet_verified filtering lives here)
├── metrics.py                    # win-rate, profit-factor, Sharpe, max-DD, expectancy
├── verdict.py                    # capital-ladder traffic-light engine
├── diagnostics.py                # per-side / dow / time-bucket / exit-reason / score / symbol
├── charts.py                     # matplotlib → base64 PNG helpers
├── render_text.py                # text-mode output (D1-D3)
├── render_html.py                # HTML-mode output (D4+)
├── templates/
│   ├── dashboard.html            # f-string template (no Jinja)
│   └── style.css                 # inline-loaded CSS
├── verification.py               # D5 — pending-verification banner + launch button
└── tests/
    ├── test_data_layer.py
    ├── test_metrics.py
    └── test_verdict.py
```

**Rules:**
- `Dashboard/` is **read-only** w.r.t. trading state. No imports from
  `services/order_engine.py`, `portfolio/manager.py`, or any module
  that can place orders. Safe even if dashboard code has bugs.
- `Dashboard/` may import from `config.py` (for `CAPITAL_LADDER`,
  paths) and `scripts/tax_db.py` helpers, but only their pure-read
  functions.
- New deps stay confined to `Dashboard/`. If we add `flask` for D15,
  it goes in a separate `Dashboard/requirements.txt` so the live
  trading bot's deploy footprint stays untouched.
- `main.py` gets one new line: `--mode dashboard` dispatches to
  `Dashboard.cli:main()`. That's the only edit outside `Dashboard/`.

---

## Goals

1. **Single source of truth** for *"is this bot profitable enough to scale capital?"* — replace the patchwork of `view_*.py` scripts with one consolidated view.
2. **Mechanical scaling decisions** — dashboard outputs a traffic-light verdict (GREEN/AMBER/RED) tied to a configurable capital ladder. No more vibes-based "should I add money?".
3. **Surface silent loss patterns** that daily reports miss: per-day-of-week, per-time-bucket, per-exit-reason, per-symbol, per-score-bucket. Each pattern is a strategy improvement candidate.
4. **Data finality awareness** — clearly distinguish API-verified (day-of, may change) from sheet-verified (T+1, frozen). Numbers shown as final must come ONLY from sheet-verified rows.
5. **In-loop verification trigger** — when sheet verification is pending, the HTML can launch the import flow with one click. Closes the gap between *"I should run the importer"* and *"I actually did"*.

---

## Data Finality Contract (CRITICAL)

The DB has two verification flags on `intraday_tax_ledger`:

| Column            | Set by                              | Meaning                                                | Final? |
|-------------------|-------------------------------------|--------------------------------------------------------|--------|
| `verified`        | API confirmation (intraday)         | Order existed on Zerodha at time-of-trade              | ❌ NO — fill prices, charges, and even qty can still change after EOD reconciliation |
| `sheet_verified`  | `import_zerodha_taxpnl.py` (T+1)    | Row matched against Zerodha's official Tax P&L sheet  | ✅ YES — frozen, tax-grade, used for ITR |

**Rule:** ALL P&L / charges / win-rate / capital-ladder verdicts shown
to the user as "final" MUST filter to `sheet_verified = 'verified'`
rows. Anything else is provisional and must be visually marked as such
(amber banner, hatched bars on charts, "(provisional)" suffix on numbers).

**Why:** Day-of API-reported charges have been observed to drift by 5–15%
once the sheet posts the next day (rounding, STT recomputation, brokerage
slab adjustments). Using API-only numbers to make capital-scaling
decisions risks compounding small reporting errors into the wrong call.

---

## Pending (sorted by priority)

| # | Item | Priority | Impact | Effort | Status |
|---|------|----------|--------|--------|--------|
| D1 | **Skeleton + sheet-verified data layer** — scaffold `Dashboard/` folder per Folder Structure section above (`__init__.py`, `cli.py`, `data_layer.py`). Wire `main.py --mode dashboard` to dispatch into `Dashboard.cli:main()`. `--text` mode only. Read `intraday_tax_ledger` filtered to `sheet_verified='verified'`. Show Section A (headline P&L) + Section E (capital-ladder verdict). Print provisional-days banner | HIGH | High | Low | Pending |
| D2 | **Section B + C metrics** — trade-quality (win rate, profit factor, avg win/loss, R-multiple histogram) + risk (max DD, max consecutive losses, Sharpe, % profitable days). Still text mode | HIGH | High | Low | Pending |
| D3 | **Section D diagnostics — per-side / per-day-of-week / per-time-bucket / per-exit-reason / per-symbol / per-score-bucket / per-regime breakdowns**. Each one is one helper function reusing aggregation logic from `view_performance.py` | HIGH | High | Medium | Pending |
| D4 | **HTML mode (`--html`)** — render `reports/dashboard/<YYYY-MM-DD>.html` using a stdlib Jinja-style template (or just f-strings + a CSS file). Embed matplotlib charts as base64 PNGs. No JS required | MEDIUM | High | Medium | Pending |
| D5 | **Pending-verification banner + verification-launch button** — HTML shows count of trading days where rows exist in `intraday_tax_ledger` but no row has `sheet_verified='verified'`. A button (or printable command) triggers the import flow | MEDIUM | High | Medium | Pending |
| D6 | **Capital-ladder config + verdict engine** — promote the ladder from a hardcoded list to `Config.CAPITAL_LADDER`. Verdict engine reads it + the metrics + emits GREEN / AMBER / RED with the recommended next budget | MEDIUM | High | Low | Pending |
| D7 | **Rejection-audit panel** — surface the *opportunity cost* of new gates (#192/#194/#195). Reuse `scripts/rejection_audit.py`. Show "X trades blocked this week, hypothetical P&L if any 50% had won = Rs.Y" so we can tune gates with data | MEDIUM | Medium | Medium | Pending |
| D8 | **Indicator-attribution panel** — join `analyses` table with `trades`. Per-indicator (RSI / MACD / pattern / ADX) win-rate contribution. Surfaces which indicators have edge and which are noise. Pattern after `view_analyses.py` + `print_indicator_correlation` in `view_performance.py` | MEDIUM | High | Medium | Pending |
| D9 | **Capital-gains panel (delivery side)** — separate section for `capital_gains_ledger` (FIFO-matched delivery trades). STCG/LTCG breakdown, per-stock gains. Reuse `view_capital_gains_ledger.py` aggregation. Read-only on top of existing ledger | LOW | Medium | Low | Pending |
| D10 | **Drill-down per-trade table** — collapsible expander that shows every trade in the window with full audit trail (entry score, ADX, pattern, exit reason, charges, net). Reuse `view_trades.py` SQL | LOW | Medium | Medium | Pending |
| D11 | **Email digest cron** — Sunday 6 PM, render the HTML, attach to email, send via SES/SMTP (or just save as PDF on Windows + Outlook automation). Catches "haven't checked in 2 weeks" failure mode | LOW | Medium | Medium | Pending |
| D12 | **Data quality badge** — top-of-page indicator: "✓ N days verified / ⚠ M days pending / ✗ K days no data". Click for diff vs Zerodha API to see what's missing. Reuse `verify_trades.py` | LOW | Medium | Low | Pending |
| D13 | **Strategy-version timeline** — vertical timeline of git commits affecting strategy, overlaid on cumulative P&L curve. Visually answer: *"did P&L improve after we shipped #192?"*. Read `git log` + match dates to trades | LOW | High | Medium | Pending (after D4) |
| D14 | **Cost-of-friction breakdown** — pie chart of where money goes on a losing day: brokerage / STT / GST / exchange / SEBI / stamp / actual losses. Validates the "reduce trade frequency" hypothesis. Reuse `tax_summary.py` | LOW | Medium | Low | Pending |
| D15 | **Live-mode mini-dashboard** — read-only HTTP page (Flask single-route) hosted on the trading machine that auto-refreshes every 30s during market hours. Shows current open positions, today's P&L (provisional), open MTM, last 5 entries/exits. NOT for analytics — just for "is the bot alive and what's it doing" | LOW | High | High | Pending |

---

## Layout Sketch (HTML mode)

```
┌──────────────────────────────────────────────────────────────────┐
│  AI Portfolio Manager — Profitability Dashboard                  │
│  Window: 2026-04-15 → 2026-04-21  (7 trading days)               │
│  Data quality: ✓ 5 verified  ⚠ 2 pending sheet  [Import Sheet]   │
└──────────────────────────────────────────────────────────────────┘

┌─ VERDICT ────────────────────────────────────────────────────────┐
│  🟢 GREEN — READY TO SCALE                                       │
│  Current budget: Rs.50,000   →   Recommended: Rs.1,00,000        │
│  All thresholds met for 1+ weeks at current tier.                │
└──────────────────────────────────────────────────────────────────┘

┌─ HEADLINE P&L (sheet-verified) ────┬─ TRADE QUALITY ─────────────┐
│  Gross:        Rs.+4,250           │  Trades:        24          │
│  Charges:      Rs.  -780           │  Win rate:      58%         │
│  Net:          Rs.+3,470  (+6.9%)  │  Profit factor: 1.62        │
│  Best day:     Rs.+1,210  (Wed)    │  Avg win:       Rs.+265     │
│  Worst day:    Rs.  -340  (Fri)    │  Avg loss:      Rs.-145     │
│                                    │  Expectancy:    Rs.+92/trade│
└────────────────────────────────────┴─────────────────────────────┘

┌─ RISK ──────────────────────────────────────────────────────────┐
│  Max drawdown:           Rs.-820  (-1.6% of budget)             │
│  Max consecutive losses: 3                                       │
│  Sharpe (annualised):    1.34                                    │
│  Profitable days:        4 / 5  (80%)                            │
└──────────────────────────────────────────────────────────────────┘

┌─ DAILY CUMULATIVE P&L (chart) ──────────────────────────────────┐
│   [matplotlib line chart, base64 PNG]                            │
│   Y axis: cumulative net P&L Rs.                                 │
│   X axis: date                                                   │
│   Hatched region for provisional days                            │
└──────────────────────────────────────────────────────────────────┘

┌─ DIAGNOSTICS ───────────────────────────────────────────────────┐
│  ┌── BY SIDE ──────┐   ┌── BY DAY-OF-WEEK ────┐                  │
│  │ BUY  : 12 / 67% │   │ Mon: +120  Tue: -340 │                  │
│  │ SELL : 12 / 50% │   │ Wed: +1210 Thu: +850 │                  │
│  └─────────────────┘   │ Fri: +1630           │                  │
│                        └──────────────────────┘                  │
│  ┌── BY EXIT REASON ─────────────────────────┐                   │
│  │ TARGET_HIT     : 8 trades  +Rs.2,840      │                   │
│  │ STOP_LOSS      : 5 trades  -Rs.  900      │                   │
│  │ STAGNANT_EXIT  : 6 trades  -Rs.  120      │ ← #192 #195 ammo  │
│  │ SIGNAL_DECAY   : 3 trades  +Rs.  450      │                   │
│  │ SQUARE_OFF     : 2 trades  +Rs. 1,200     │                   │
│  └────────────────────────────────────────────┘                  │
│  ┌── BY SCORE BUCKET ────────────────────────┐                   │
│  │ Score 3-4: 5 trades, 40% win, -Rs.180     │ ← maybe drop?     │
│  │ Score 4-5: 8 trades, 50% win, +Rs.450     │                   │
│  │ Score 5-6: 6 trades, 67% win, +Rs.1,800   │                   │
│  │ Score 6-7: 3 trades, 67% win, +Rs.900     │                   │
│  │ Score 7+ : 2 trades, 100% win, +Rs.500    │                   │
│  └────────────────────────────────────────────┘                  │
│  ┌── BY TIME-OF-ENTRY ────────────────────────┐                  │
│  │ 09:15-10:00: 8 trades, 75% win, +Rs.2,100  │                  │
│  │ 10:00-11:00: 6 trades, 50% win, +Rs.  340  │                  │
│  │ 11:00-12:00: 4 trades, 50% win, +Rs.  120  │                  │
│  │ 12:00-13:00: 2 trades, 50% win, +Rs.    0  │ ← lunch lull OK  │
│  │ 13:00-14:00: 3 trades, 67% win, +Rs.  600  │                  │
│  │ 14:00-15:10: 1 trades,  0% win, -Rs.  690  │ ← late entries?  │
│  └────────────────────────────────────────────┘                  │
│  ┌── TOP / BOTTOM SYMBOLS ───────────────────┐                   │
│  │ ✅ Top:    RELIANCE +Rs.890, TCS +Rs.620   │                  │
│  │ ❌ Bottom: ZOMATO -Rs.340, ADANIENT -Rs.290│ ← exclude?       │
│  └────────────────────────────────────────────┘                  │
└──────────────────────────────────────────────────────────────────┘

┌─ DATA QUALITY ───────────────────────────────────────────────────┐
│  Verified days (frozen):     5                                   │
│  Pending sheet verification: 2  (2026-04-20, 2026-04-21)         │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ ⚠ 2 trading days are still provisional. Numbers above    │    │
│  │   exclude them. To finalise:                              │    │
│  │                                                           │    │
│  │   1. Download Zerodha Tax P&L for FY 2026-27:            │    │
│  │      Console → Reports → Tax P&L → Download              │    │
│  │   2. Save to: data/ZerodhaTaxPL/Equity-Tradewise-...xlsx │    │
│  │   3. Click here:  [ Run Sheet Verification ]             │    │
│  │      (or terminal: python scripts/import_zerodha_taxpnl  │    │
│  │       .py --fy 2026)                                     │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘

┌─ COST OF FRICTION (charges breakdown) ──────────────────────────┐
│  Brokerage:     Rs.320  (41%)                                    │
│  STT:           Rs.220  (28%)                                    │
│  Exchange txn:  Rs. 90  (12%)                                    │
│  GST:           Rs. 80  (10%)                                    │
│  SEBI + Stamp:  Rs. 70  ( 9%)                                    │
│  Total:         Rs.780                                           │
│  → 18.4% of gross P&L went to charges. Target < 15%.             │
└──────────────────────────────────────────────────────────────────┘
```

---

## Implementation Notes

### Data sources (all read-only)

| Table                      | DB                           | Used for                                |
|----------------------------|------------------------------|-----------------------------------------|
| `trades`                   | `data/trades.db`             | Entry/exit details, scores, indicators  |
| `intraday_tax_ledger`      | `data/trades.db`             | **Final** P&L, charges (sheet-verified) |
| `capital_gains_ledger`     | `data/trades.db`             | Delivery STCG/LTCG (D9 panel)           |
| `analyses`                 | `data/trades.db`             | Claude / scoring rationale (D8 panel)   |
| `data/ZerodhaTaxPL/*.xlsx` | filesystem                   | Source of truth for verification        |

### Reusable scripts (don't reinvent)

| Existing script                     | Reuse for                                                    |
|-------------------------------------|--------------------------------------------------------------|
| `scripts/view_performance.py`       | `get_trades()`, `print_daily_summary`, `print_exit_reason_stats`, `print_side_stats`, `print_indicator_correlation` — most aggregation logic already exists, just needs a wrapper |
| `scripts/view_intraday_ledger.py`   | FY filtering helpers, `current_fy()`                         |
| `scripts/view_capital_gains_ledger.py` | D9 panel — delivery side                                  |
| `scripts/tax_summary.py`            | D14 cost-of-friction breakdown                               |
| `scripts/rejection_audit.py`        | D7 opportunity-cost panel                                    |
| `scripts/verify_trades.py`          | D12 data-quality badge                                       |
| `scripts/view_analyses.py`          | D8 indicator-attribution panel                               |
| `scripts/import_zerodha_taxpnl.py`  | D5 verification-launch button — invoke `--fy <year>`         |

### Tech stack

- **Backend**: Python 3.10+, stdlib + `pandas` (already a dep) + `matplotlib` (already a dep). Zero new deps for D1–D10.
- **HTML**: f-string template + tiny inline CSS. No Jinja, no JS, no build step. PNG charts embedded as base64 (`<img src="data:image/png;base64,...">`).
- **Email** (D11): stdlib `smtplib` or Outlook COM via `pywin32` on Windows.
- **Live HTTP** (D15): `flask` (would be a new dep — defer until D15 actually picked up).

### CLI design

```bash
# Default: text dashboard, last 7 trading days
python main.py --mode dashboard

# Last N trading days, text
python main.py --mode dashboard --days 30

# Specific window
python main.py --mode dashboard --from 2026-04-01 --to 2026-04-21

# Whole month
python main.py --mode dashboard --month 2026-04

# Whole financial year (FY 2026-27)
python main.py --mode dashboard --fy 2026

# HTML output instead of text
python main.py --mode dashboard --html

# Include provisional (non-sheet-verified) data — explicit opt-in
python main.py --mode dashboard --include-provisional

# Open the latest HTML report in default browser (Windows: start <path>)
python main.py --mode dashboard --html --open
```

Wire `--mode dashboard` in `main.py` so users don't need to remember the
import path. Keep `Dashboard/cli.py` as the actual entry point and
treat `main.py` as a thin dispatcher (matches existing
`--mode analyze` / `--mode trade` pattern).

### Capital ladder config

Lives in `config.py`:

```python
CAPITAL_LADDER: list[dict] = [
    {"budget":     50_000, "win_rate_min": 0.50, "profit_factor_min": 1.4, "max_dd_pct": 0.08, "weeks_required": 1},
    {"budget":   1_00_000, "win_rate_min": 0.50, "profit_factor_min": 1.4, "max_dd_pct": 0.08, "weeks_required": 4},
    {"budget":   2_50_000, "win_rate_min": 0.52, "profit_factor_min": 1.5, "max_dd_pct": 0.07, "weeks_required": 8},
    {"budget":   5_00_000, "win_rate_min": 0.55, "profit_factor_min": 1.5, "max_dd_pct": 0.07, "weeks_required": 12},
    {"budget":  10_00_000, "win_rate_min": 0.55, "profit_factor_min": 1.6, "max_dd_pct": 0.06, "weeks_required": 24},
]
```

Verdict engine in pseudocode:

```
current_rung = find rung where rung.budget == Config.BUDGET
next_rung    = next rung after current

if metrics.weeks_at_current >= next_rung.weeks_required AND
   metrics.win_rate >= next_rung.win_rate_min AND
   metrics.profit_factor >= next_rung.profit_factor_min AND
   metrics.max_dd_pct <= next_rung.max_dd_pct AND
   metrics.net_pnl > 0:
       verdict = GREEN; recommended = next_rung.budget
elif metrics.net_pnl > 0:
       verdict = AMBER; reason = which threshold failed
else:
       verdict = RED; highlight = worst diagnostic
```

### Edge cases to handle

- **Zero trades in window** → "Insufficient data — no trades in selected window."
- **Zero losses in window** → profit-factor = ∞; show "N/A (no losses)".
- **< 20 trades in window** → banner "Sample size N=X — metrics not statistically meaningful below 20."
- **Future date in `--to`** → clamp to today; warn user.
- **`sheet_verified` column missing on old rows** → treat as `'pending'` (column has `DEFAULT 'pending'` per `tax_db.py`).
- **Mixed-FY window** (e.g. last 7 days crosses Apr 1) → still works, just don't double-count.

### Testing

- Run on existing `data/trades.db` (~30 days of historical trades) and validate against hand-computed values for any one week.
- Verify Sharpe formula against a `pandas.Series` reference: `(returns.mean() / returns.std()) * sqrt(252)`.
- Ensure `--include-provisional` shows BIGGER P&L numbers than default (since provisional includes more days).
- Snapshot HTML output for visual regression.

---

## Out of scope (do not build into v1)

- ML-based forecasting of next week's P&L. Premature.
- Real-money paper-trading simulator. Use existing `--dryrun`.
- Multi-strategy comparison view. Only have V2 NoAI right now.
- Mobile app. HTML opened in browser is fine.
- Anything that writes back to `trades.db` or `intraday_tax_ledger`. Read-only forever.

---

## Maintenance

- Update this file at the end of every dashboard session — mark items completed, add new ideas, re-prioritise.
- Counts at the top of the Pending table must match table row count (same convention as `STRATEGY_ROADMAP.md`).
- When an item ships, move it to a `## Completed` section at the bottom (TBD when first item ships).
- Keep this doc separate from `STRATEGY_ROADMAP.md` — strategy lives there, analytics/observability lives here.
