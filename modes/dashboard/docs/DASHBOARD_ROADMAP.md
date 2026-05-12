# Profitability Dashboard — Roadmap

> Separate from `TRADE_ROADMAP.md` because the dashboard is a pure
> **read-only analytics layer** that will grow over many sessions.
> Touches NO strategy/order/config code. Safe to iterate on
> independently of live trading.
>
> **Status:** Phase 3 — D1 + D1.1 shipped 2026-04-23. D13 + D16 + D17 (tax-filing module + tax page) + companion theory pages shipped 2026-04-27. Interactive HTML dashboard live (Chart.js SPA + stdlib HTTP server, in-page date / granularity / source controls, per-day budget from trading reports, vertical reference lines at SHA-change boundaries, FY tax projection + ITR-3 helpers). D2–D12, D14, D15, D18–D23 pending.
>
> **Companion Theory pages (shipped 2026-04-27):** `/theory/<slug>`
> renders four reference docs as HTML with KaTeX math and the §0 live
> snapshot card. Routes registered in
> [`modes/dashboard/theory_page.py::PAGES`](../theory_page.py):
> `statistics` (TRADE_STATISTICS.md), `trade-strategy` (TRADE_STRATEGY.md),
> `evolution` (TRADE_EVOLUTION.md), `tax-guide` (TRADE_TAX_GUIDE.md). The
> upcoming `/tax` page (D17) links into `tax-guide` for the regulatory
> reference, keeping the workflow page lean.
>
> **Location:** all dashboard code, docs, templates, and tests live
> under the top-level `modes/dashboard/` folder, isolated from the trading
> bot. Trading bot remains the source of truth for the SQLite DB and
> Zerodha sheet files; the dashboard only **reads** them.

---

## Folder Structure

```
modes/dashboard/
├── docs/
│   └── DASHBOARD_ROADMAP.md      # this file
├── __init__.py
├── cli.py                        # argparse entry; called from main.py --mode dashboard
├── server.py                     # ✅ D1.1 — stdlib http.server SPA backend (/, /api/data)
├── data_layer.py                 # all DB reads (sheet_verified filtering, FY window helper)
├── metrics.py                    # headline P&L + bucketed/cumulative series; D2 adds win-rate, profit-factor, Sharpe, max-DD, expectancy
├── budget_history.py             # ✅ D1.1 — per-day budget from reports/trading/.../trading_data_DD.json
├── verdict.py                    # capital-ladder traffic-light engine (D1 minimal; D6 expands)
├── diagnostics.py                # D3 — per-side / dow / time-bucket / exit-reason / score / symbol
├── render_text.py                # text-mode output (--text)
├── render_html.py                # ✅ D1.1 — JSON-driven SPA shell, Chart.js via CDN
├── verification.py               # D5 — pending-verification banner + launch button
└── tests/
    ├── test_data_layer.py
    ├── test_metrics.py
    └── test_verdict.py
```

**Rules:**
- `modes/dashboard/` is **read-only** w.r.t. trading state. No imports from
  `modes/trade/order_engine.py`, `modes/trade/manager.py`, or any module
  that can place orders. Safe even if dashboard code has bugs.
- `modes/dashboard/` may import from `config.py` (for `CAPITAL_LADDER`,
  paths) and `shared/tax_db.py` helpers, but only their pure-read
  functions.
- **Zero new Python deps.** D1.1 ships an interactive HTML dashboard
  using only stdlib (`http.server`) on the Python side and Chart.js
  via CDN on the page side. No Flask, no matplotlib.
- `main.py` gets one new line: `--mode dashboard` dispatches to
  `Dashboard.cli:main()`. That's the only edit outside `modes/dashboard/`.

---

## Goals

1. **Single source of truth** for *"is this bot profitable enough to scale capital?"* — replace the patchwork of `view_*.py` scripts with one consolidated view.
2. **Mechanical scaling decisions** — dashboard outputs a traffic-light verdict (GREEN/AMBER/RED) tied to a configurable capital ladder. No more vibes-based "should I add money?".
3. **Surface silent loss patterns** that daily reports miss: per-day-of-week, per-time-bucket, per-exit-reason, per-symbol, per-score-bucket. Each pattern is a strategy improvement candidate.
4. **Data finality awareness** — clearly distinguish API-verified (day-of, may change) from sheet-verified (T+1, frozen). Numbers shown as final must come ONLY from sheet-verified rows.
5. **In-loop verification trigger** — when sheet verification is pending, the HTML can launch the import flow with one click. Closes the gap between *"I should run the importer"* and *"I actually did"*.
6. **Tax-filing-ready** — turn a year of trades into ITR-3-shaped numbers, an advance-tax schedule, a proof-document folder, and a CA-friendly export. Make ITR season a 30-minute task instead of a weekend of spreadsheet wrangling. See [TRADE_TAX_GUIDE.md](../../docs/TRADE_TAX_GUIDE.md) for the regulatory rules this builds on.

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
| D1 | **Skeleton + sheet-verified data layer** — scaffold `modes/dashboard/` folder per Folder Structure section above (`__init__.py`, `cli.py`, `data_layer.py`). Wire `main.py --mode dashboard` to dispatch into `Dashboard.cli:main()`. `--text` mode only. Read `intraday_tax_ledger` filtered to `sheet_verified='verified'`. Show Section A (headline P&L) + Section E (capital-ladder verdict). Print provisional-days banner | HIGH | High | Low | ✅ Done (2026-04-23) |
| D2 | **Section B + C metrics** — trade-quality (win rate, profit factor, avg win/loss, R-multiple histogram) + risk (max DD, max consecutive losses, Sharpe, % profitable days). Still text mode | HIGH | High | Low | Pending |
| D3 | **Section D diagnostics — per-side / per-day-of-week / per-time-bucket / per-exit-reason / per-symbol / per-score-bucket / per-regime breakdowns**. Each one is one helper function reusing aggregation logic from `view_performance.py` | HIGH | High | Medium | Pending |
| D4 | ~~**HTML mode (`--html`)** — render `reports/dashboard/<YYYY-MM-DD>.html` using a stdlib Jinja-style template (or just f-strings + a CSS file). Embed matplotlib charts as base64 PNGs. No JS required~~ — **superseded by D1.1**: shipped as default-mode interactive SPA (Chart.js via CDN, no matplotlib, no PNG embedding). Static `--no-open` snapshot still writes to `reports/dashboard/dashboard_<date>.html` | MEDIUM | High | Medium | ✅ Done via D1.1 |
| D5 | **Pending-verification banner + verification-launch button** — HTML shows count of trading days where rows exist in `intraday_tax_ledger` but no row has `sheet_verified='verified'`. A button (or printable command) triggers the import flow. **Banner shipped in D1.1** (lists pending dates + shows the import command); launch button still pending | MEDIUM | High | Medium | Partially done (banner in D1.1; button pending) |
| D6 | **Capital-ladder config + verdict engine** — promote the ladder from a hardcoded list to `Config.CAPITAL_LADDER`. Verdict engine reads it + the metrics + emits GREEN / AMBER / RED with the recommended next budget | MEDIUM | High | Low | Pending |
| D7 | **Rejection-audit panel** — surface the *opportunity cost* of new gates (#192/#194/#195). Reuse `scripts/trade/rejection_audit.py`. Show "X trades blocked this week, hypothetical P&L if any 50% had won = Rs.Y" so we can tune gates with data | MEDIUM | Medium | Medium | Pending |
| D8 | **Indicator-attribution panel** — join `analyses` table with `trades`. Per-indicator (RSI / MACD / pattern / ADX) win-rate contribution. Surfaces which indicators have edge and which are noise. Pattern after `view_analyses.py` + `print_indicator_correlation` in `view_performance.py` | MEDIUM | High | Medium | Pending |
| D9 | **Capital-gains panel (delivery side)** — separate section for `capital_gains_ledger` (FIFO-matched delivery trades). STCG/LTCG breakdown, per-stock gains. Reuse `view_capital_gains_ledger.py` aggregation. Read-only on top of existing ledger | LOW | Medium | Low | Pending |
| D10 | **Drill-down per-trade table** — collapsible expander that shows every trade in the window with full audit trail (entry score, ADX, pattern, exit reason, charges, net). Reuse `view_trades.py` SQL | LOW | Medium | Medium | Pending |
| D11 | **Email digest cron** — Sunday 6 PM, render the HTML, attach to email, send via SES/SMTP (or just save as PDF on Windows + Outlook automation). Catches "haven't checked in 2 weeks" failure mode | LOW | Medium | Medium | Pending |
| D12 | **Data quality badge** — top-of-page indicator: "✓ N days verified / ⚠ M days pending / ✗ K days no data". Click for diff vs Zerodha API to see what's missing. Reuse `verify_trades.py` | LOW | Medium | Low | Pending |
| D13 | **Strategy-version timeline overlay on cumulative-P&L chart** — ✅ **shipped 2026-04-27**, see Completed table below. | MEDIUM | High | Medium | ✅ Done (2026-04-27) |
| D14 | **Cost-of-friction breakdown** — pie chart of where money goes on a losing day: brokerage / STT / GST / exchange / SEBI / stamp / actual losses. Validates the "reduce trade frequency" hypothesis. Reuse `tax_summary.py` | LOW | Medium | Low | Pending |
| D15 | **Live-mode mini-dashboard** — read-only HTTP page (Flask single-route) hosted on the trading machine that auto-refreshes every 30s during market hours. Shows current open positions, today's P&L (provisional), open MTM, last 5 entries/exits. NOT for analytics — just for "is the bot alive and what's it doing" | LOW | High | High | Pending |
| D16 | **Tax filing module — FY summary view** — new `modes/dashboard/tax/` sub-package. Reads `intraday_tax_ledger` (sheet-verified only) + `capital_gains_ledger` for the chosen FY. Outputs an ITR-3-friendly summary: speculative business gross profit, total deductible expenses (brokerage / STT / exchange / GST / SEBI / stamp), net speculative income, turnover (absolute-sum method per [TAX_GUIDE §7](../../docs/TRADE_TAX_GUIDE.md)), STCG/LTCG split. Reuse `scripts/shared/tax_summary.py` as the data layer; this module just adds presentation + Schedule-BP framing | HIGH | High | Low | ✅ **Shipped 2026-04-27** — `modes/dashboard/tax/fy_summary.py` exposes `compute_fy_summary(fy_start)` returning `FYSummary` (intraday: trades / charges by bucket / net / turnover; CG: STCG+LTCG profit/loss/net). Surfaced on `/tax` page with a Schedule-BP-shaped card and click-to-copy ITR-3 values. Today still aggregates *all* rows (verified + provisional); flipping to verified-only is a one-line filter once we tighten the data-finality contract. |
| D17 | **Tax projection / what-if engine** — user inputs (or CLI flag) projected total income for the FY (salary + other sources). Module pulls current FY intraday net + capital-gains net, applies Budget-2025 new-regime slabs from a versioned `modes/dashboard/tax/slabs.py`, computes projected total tax, advance-tax due dates with cumulative %, and Section-87A rebate eligibility. Output: "If FY ends today, total tax = Rs.X; next advance tax due Sep 15 = Rs.Y". Versioned slabs file so FY 2027-28 changes are a one-line config | HIGH | High | Medium | ✅ **Shipped 2026-04-27** (advance-tax part deferred to D21) — `modes/dashboard/tax/slabs.py` carries Budget-2025 new-regime slabs (versioned by FY-start year), Section 87A rebate ceilings, 4% cess and surcharge bands. `compute_tax(...)` returns a `TaxComputation` (slab tax, rebate, surcharge, cess, total, marginal+effective rates). `/tax` page text input recomputes live via `/api/tax`; computes the headline number "tax attributable to intraday this FY" as `total_tax_with_intraday − total_tax_without_intraday`. |
| D18 | **Documentary-proof collection workflow** — scaffolds a per-FY folder `data/tax_proofs/FY_<YYYY>/` with sub-folders `broker/`, `api_subscriptions/`, `software/`, `hardware/`, `internet/`, `electricity/`, `claude_api/`, `misc/`. Dashboard shows a checklist UI ("Have you saved Zerodha Console statement? Kite Connect Rs.500/mo bills × 12? Anthropic monthly invoices?") with last-modified-date per slot. A "Mark collected" button writes a manifest JSON `proofs_manifest.json` listing every file with SHA-256 + tag (e.g. `kite_connect_apr_2026`). At ITR time the dashboard zips the whole folder + manifest as `FY_<YYYY>_proofs.zip` | HIGH | High | Low | Pending |
| D19 | **Charge anomaly detector** — for each trading day where both `verified='verified'` and `sheet_verified='verified'` rows exist, compute per-charge-bucket drift (brokerage, STT, exchange, GST, SEBI, stamp) between API-day-of vs sheet-T+1. Flag any bucket drift > 5% or any total-charge drift > 2%. List on dashboard "Days where Zerodha sheet differed materially from API". Useful for tax (final number is sheet) and for noticing broker billing issues | MEDIUM | Medium | Low | Pending |
| D20 | **Loss carry-forward ledger** — new SQLite table `loss_carryforward` tracking speculative losses pending offset (4-year window per [TAX_GUIDE §6](../../docs/TRADE_TAX_GUIDE.md)) and STCL/LTCL (8-year window). Dashboard shows: "Rs.X speculative loss from FY 2026-27 expires after FY 2030-31; current FY profit Rs.Y absorbs Rs.Z; remaining Rs.W". Critical for not letting carry-forwards lapse silently | MEDIUM | High | Medium | Pending |
| D21 | **Advance-tax tracker + reminder** — reads projection from D17. Shows the four advance-tax due dates (Jun 15 / Sep 15 / Dec 15 / Mar 15) with cumulative-% targets, amount due each, amount paid so far (manual entry → stored in `advance_tax_payments` table). Highlights upcoming deadline within 14 days. Optional: writes an iCal `.ics` reminder users can import to their calendar | MEDIUM | Medium | Low | Pending |
| D22 | **AIS / Form 26AS reconciliation helper** — user uploads/pastes their AIS JSON or CSV (downloaded from income-tax e-filing portal). Dashboard cross-checks the broker transaction list against AIS-reported aggregates and surfaces any mismatch (e.g. AIS shows 124 trades, our ledger has 122). Reuse `scripts/trade/verify_trades.py` matching logic. Prevents the "got an IT notice for un-reported trades" failure mode | LOW | High | Medium | Pending |
| D23 | **ITR-3 schedule pre-fill JSON exporter** — emits a structured JSON containing every value an ITR-3 filer needs to type into the income-tax portal: Schedule BP (speculative gross / expenses / net), Schedule CG (STCG/LTCG), Schedule BS (no-account-case minimal), expense breakdown table, audit-applicability flag, advance-tax-paid total. CA gets one file instead of 5 reports. Out of scope: actual ITR XML upload (income-tax portal API is closed) | LOW | High | Medium | Pending |

### Tax Filing Sub-Module (D16–D23) — design notes

All tax-filing items live under `modes/dashboard/tax/` (separate sub-package
inside the dashboard so the analytics core and tax module can evolve
independently):

```
modes/dashboard/tax/
├── __init__.py
├── slabs.py              # versioned Budget-2025+ new-regime slabs, 87A rebate, cess
├── fy_summary.py         # D16 — Schedule-BP-shaped aggregations
├── projection.py         # D17 — what-if engine + advance-tax computation
├── proofs.py             # D18 — folder scaffolding + manifest writer
├── anomaly.py            # D19 — charge drift detector
├── carryforward.py       # D20 — loss carry-forward ledger
├── advance_tax.py        # D21 — tracker + iCal exporter
├── reconciliation.py     # D22 — AIS cross-check
├── itr3_export.py        # D23 — JSON exporter
└── tests/
```

**Source of truth split:**
- [docs/TRADE_TAX_GUIDE.md](../../docs/TRADE_TAX_GUIDE.md) — regulatory reference (slabs, ITR forms, deductible expense list, audit thresholds, advance-tax due dates). Updated once per Union Budget.
- `modes/dashboard/tax/` — the *workflow* layer that turns those rules into per-trade computations, prefilled forms, and a proofs folder. References TAX_GUIDE wherever a regulatory citation is needed.

**Critical rule (re-stated for tax module):** every number that ends up
in an ITR-3 field MUST come from `sheet_verified='verified'` rows.
Provisional rows are never used in tax outputs, even with
`--include-provisional` (that flag is for analytics only — D16+ ignores
it and warns if set).

**Document-collection mental model (D18):**

| Slot | What goes there | Source |
|------|-----------------|--------|
| `broker/zerodha_tax_pl_<FY>.xlsx` | Official Zerodha Tax P&L | Zerodha Console → Reports → Tax P&L |
| `broker/zerodha_ledger_<FY>.xlsx` | Funds ledger (proves capital deployed) | Zerodha Console → Funds → Statement |
| `broker/contract_notes/` | Daily contract notes (zip OK) | Zerodha email or Console |
| `api_subscriptions/kite_connect_<MMM_YYYY>.pdf` | Kite Connect Rs.500/month invoice (×12) | developers.kite.trade billing |
| `claude_api/anthropic_<MMM_YYYY>.pdf` | Anthropic monthly invoice | console.anthropic.com → Settings → Billing |
| `software/` | Other paid trading tools (data feeds, screeners) | vendor invoices |
| `hardware/laptop_invoice.pdf` | One-time computer purchase (depreciable) | purchase invoice |
| `internet/<MMM_YYYY>.pdf` | ISP bills (×12, claim a proportion) | ISP portal |
| `electricity/<MMM_YYYY>.pdf` | Electricity bills (×12, optional, claim small proportion) | utility portal |
| `misc/` | CA fees, books, courses, stamp paper for rent agreement | as incurred |

The dashboard does NOT scrape any vendor portal automatically (creds
risk + ToS risk). It just provides the folder skeleton, the checklist,
and the manifest. User uploads files manually. This is intentional —
tax docs are too important to entrust to a scraper.

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

┌─ TAX FILING — FY 2026-27 (D16–D23) ─────────────────────────────┐
│  Schedule BP (speculative business income)                       │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ Gross speculative profit:     Rs.   12,450               │    │
│  │ Deductible expenses:          Rs.    9,820               │    │
│  │   ├─ Brokerage / exchange:    Rs.    4,200               │    │
│  │   ├─ STT:                     Rs.    2,100               │    │
│  │   ├─ GST + SEBI + stamp:      Rs.    1,520               │    │
│  │   ├─ Kite Connect API (×12):  Rs.    6,000               │    │
│  │   ├─ Anthropic Claude (×7):   Rs.    1,800               │    │
│  │   └─ Internet (40% of ×12):   Rs.      …                 │    │
│  │ Net speculative income:       Rs.    2,630               │    │
│  │ Turnover (absolute-sum):      Rs.   28,400  (no audit)   │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  Tax projection (your salary input: Rs. 22,00,000)               │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ Total income (salary + speculative + STCG):              │    │
│  │   Rs. 22,02,630 → slab 25% on the marginal Rs.2,630      │    │
│  │ Projected total tax (incl 4% cess):  Rs. 3,12,580        │    │
│  │ Section 87A rebate:                  N/A (income > 12L)  │    │
│  │ Advance tax paid YTD:                Rs. 2,00,000        │    │
│  │ Next due (Sep 15, 45% target):       Rs.    40,661       │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  Loss carry-forward                                              │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ Speculative loss FY 2025-26:  Rs.  8,200  (expires FY30) │    │
│  │ Absorbed by current FY:       Rs.  8,200                 │    │
│  │ Remaining:                    Rs.      0  ✓              │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  Documentary proofs (data/tax_proofs/FY_2026/)                   │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ ✓ Zerodha Tax P&L                                        │    │
│  │ ✓ Zerodha funds ledger                                   │    │
│  │ ⚠ Kite Connect invoices (10/12 — Feb, Mar missing)       │    │
│  │ ⚠ Anthropic invoices    (5/7 — Jan, Feb missing)         │    │
│  │ ✗ Internet bills        (0/12)        [ Mark collected ] │    │
│  │ ✗ Electricity bills     (0/12 — optional)                │    │
│  │ ─────────────────────────────────────────────────────── │    │
│  │ [ Export FY_2026_proofs.zip ]   [ Open ITR-3 JSON ]      │    │
│  └──────────────────────────────────────────────────────────┘    │
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
| `scripts/trade/view_performance.py`       | `get_trades()`, `print_daily_summary`, `print_exit_reason_stats`, `print_side_stats`, `print_indicator_correlation` — most aggregation logic already exists, just needs a wrapper |
| `scripts/trade/view_intraday_ledger.py`   | FY filtering helpers, `current_fy()`                         |
| `scripts/shared/view_capital_gains_ledger.py` | D9 panel — delivery side                                  |
| `scripts/shared/tax_summary.py`            | D14 cost-of-friction breakdown                               |
| `scripts/trade/rejection_audit.py`        | D7 opportunity-cost panel                                    |
| `scripts/trade/verify_trades.py`          | D12 data-quality badge                                       |
| `scripts/shared/view_analyses.py`          | D8 indicator-attribution panel                               |
| `scripts/shared/import_zerodha_taxpnl.py`  | D5 verification-launch button — invoke `--fy <year>`         |

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
import path. Keep `modes/dashboard/cli.py` as the actual entry point and
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
- Multi-strategy comparison view. Only have NoAI right now.
- Mobile app. HTML opened in browser is fine.
- Anything that writes back to `trades.db` or `intraday_tax_ledger`. Read-only forever.

---

## Maintenance

- Update this file at the end of every dashboard session — mark items completed, add new ideas, re-prioritise.
- Counts at the top of the Pending table must match table row count (same convention as `TRADE_ROADMAP.md`).
- When an item ships, move it to a `## Completed` section at the bottom (TBD when first item ships).
- Keep this doc separate from `TRADE_ROADMAP.md` — strategy lives there, analytics/observability lives here.

---

## Completed

| # | Item | Shipped | Notes |
|---|------|---------|-------|
| D1 | Skeleton + sheet-verified data layer + headline P&L + minimum-viable verdict | 2026-04-23 | New `modes/dashboard/` package with `__init__.py`, `data_layer.py`, `metrics.py`, `verdict.py`, `render_text.py`, `cli.py`. Wired `--mode dashboard` in `main.py` (thin dispatcher; argparse owned by `modes/dashboard/cli.py`). Added `Config.CAPITAL_LADDER` (5 rungs, Rs.50K → Rs.10L) — D6 will plug in win-rate / profit-factor / DD / weeks-required gates once those metrics land in `metrics.py`. Verdict logic: GREEN (net>0 AND ≥20 trades), AMBER (net>0 but <20 trades), RED (net≤0), GREY (no trades). Headline shows trades / gross / charges / net (with % of budget) / best & worst day, sourced ONLY from `sheet_verified='verified'` rows by default. `--include-provisional` opts in to pending rows for analytics; the data-finality contract still holds — tax outputs (D16+) ignore the flag. CLI flags shipped: `--days N`, `--from`, `--to`, `--include-provisional`. Future-date `--to` is clamped with a stderr warning. Smoke-tested against live `data/trades.db` (114 sheet-verified trades over 14 days → RED -3.92%; 137 with provisional → RED -2.49%). |
| D1.1 | Interactive HTML dashboard + local server + per-day budget | 2026-04-23 | Pivoted from "static text/HTML report" to "the webpage IS the config surface". (a) `render_html.py` rewritten as a JSON-driven SPA shell using Chart.js 4.4.1 via CDN (zero new Python deps, no matplotlib). Two charts: cumulative net P&L (line, daily) + per-bucket P&L (bar, granularity-switchable). In-page controls: `from`/`to` date pickers, granularity (daily/weekly/monthly), source toggle (all vs verified-only), Apply button + presets (FY / month / 7d / 30d). (b) New `modes/dashboard/server.py` runs a stdlib `ThreadingHTTPServer` on `127.0.0.1` (auto-allocated port) exposing `GET /` (HTML shell with embedded initial payload) and `GET /api/data` (JSON refresh on every filter change). (c) New `modes/dashboard/budget_history.py` reads `reports/trading/<YYYY>/<MM>/trading_data_<DD>.json` → `config.budget` so % return is computed against the **actually deployed** capital per day (matters because the user runs with `--max 50000` some days, default on others). LRU-cached, falls back to `Config.MAX_BUDGET_INR` for missing days. (d) `metrics.py` extended with `Granularity` literal, `bucketed_pnl(trades, granularity)` (ISO-week and YYYY-MM keys), `cumulative_series(trades)` (always daily). (e) `data_layer.py` added `current_fy_window(today)` and changed `resolve_window()` default from "last 7 days" to current Indian FY (Apr 1 → Mar 31). (f) `cli.py` redesigned: default = launch server + open browser; `--text` = legacy plain text; `--no-open` = static HTML snapshot to `reports/dashboard/dashboard_<date>.html`. Static snapshots include client-side re-bucketing so granularity still works offline; date-range changes prompt a relaunch. Also `--port` and `--host` for fixed-port serving. (g) Smoke-tested live: `--no-open` writes 17 KB HTML; server returns 200 on `/` (17 KB, Chart.js CDN linked) and `/api/data?granularity=monthly` (1.4 KB JSON). FY view: 15 trading days, RED, -Rs.382 net, avg budget Rs.38,231 (mix of 50K and lower). User-visible: `MAX_BUDGET_INR` raised 20K → 50K to match current trading reality. |
| D13 | Strategy-version timeline overlay on cumulative-P&L chart | 2026-04-27 | Pairs with V2 #245 stability-check script: D13 is the visual proof of whether a stability-window change inflected the equity curve. (a) `modes/trade/report_writer.py::save_trading_day` now stamps `config.git_sha` into `trading_data_DD.json` via a new `_git_short_sha()` helper (subprocess `git rev-parse --short HEAD`, cached per-process, fails silently when git is missing). (b) New `modes/dashboard/strategy_versions.py` (~110 lines, stdlib only): `strategy_shas(date_from, date_to)` walks `reports/trading/<YYYY>/<MM>/trading_data_DD.json` and returns a `{date: short_sha}` map; `commit_subject(sha)` resolves a commit subject via `git log -1 --pretty=%s <sha>` with per-process caching and stale-SHA fallback to None; `boundaries(shas)` returns the chronological subset of dates whose SHA differs from the previous day's. (c) `modes/dashboard/render_html.py::build_payload` accepts `strategy_boundaries` + `strategy_overlay_enabled` and emits a new `strategy_overlay` block in the payload. (d) `drawCum()` registers a one-off Chart.js plugin (`strategyVersionLines`) that draws a thin dashed grey vertical line at every boundary date in `afterDatasetsDraw`, plus a tooltip `afterLabel` callback that appends `Strategy version: <sha>\n<subject>` when hovering a boundary date. The plugin is read from the live payload (not the embedded initial JSON) so filter changes refresh the overlay correctly. (e) `modes/dashboard/server.py` and `modes/dashboard/cli.py` (snapshot path) compute boundaries via `strategy_versions` and pass them through, gated on `Config.DASHBOARD_STRATEGY_VERSION_OVERLAY = True` (new). Pre-D13 `trading_data_DD.json` files have no `git_sha` field and are silently skipped — no migration needed. Kill-switch flips off the overlay without breaking either the chart or the per-day SHA recording. |
