# Architecture and Operating Boundaries

Reviewed 2026-09-14. This is a local, single-operator application, not a
multi-tenant financial service. The public project story is in
[PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md).

## Ownership

| Layer | Owning code | Responsibility |
|---|---|---|
| Entry points | `main.py`, per-mode CLI modules | Select a mode, parse inputs and delegate. |
| Providers | `core/zerodha_client.py`, AI clients, shared market-data modules | Authentication, provider requests, errors and caching. Non-interactive broker reads must never request credentials. |
| Indian analysis | `modes/analyze/` | Typed observations with source/date, deterministic scorecard, separate risk/action decisions, optional AI enrichment and persistence. |
| US analysis | `modes/us/longterm.py`, `fundamentals.py`, dashboard US services | Long-term business/valuation/risk scorecard. Legacy swing storage does not make this a swing strategy. |
| Mutual funds | `modes/mf/` | Combine holdings by scheme, value units at NAV, track scheduled investments and calculate structural insights. |
| Swing | `modes/swing/` | Candidate scans, risk plans, position review and a manual workflow ledger. |
| Intraday | `modes/trade/manager.py`, scanner, order engine and tracker | Orchestration, candidate decisions, risk/execution, reconciliation and evidence. Live startup is paused by default. |
| Options | `modes/options/` | Contract research, pricing, scans and simulated trade lifecycle. Infrastructure exists; a profitable strategy is not validated. |
| Dashboard | `modes/dashboard/` | Render snapshots and explanations, display quotes, and expose deliberate local workflow actions. No broker order placement. |
| Public companion links | `modes/dashboard/rupevo.py` | The Rupevo address, its destinations and the page cards that point visitors to the public, login-free site. |
| Research | `scripts/trade/`, `docs/backtest/` | Replays, strategy comparisons, costs and dated evidence. |
| Repository safety | `scripts/shared/repository_audit.py`, `backup_data.py` | Publication checks, dataset integrity, consistent private snapshots and explicit publication. |

Keep calculations in domain modules, not in HTTP handlers or presentation
code. Reuse the existing dataclasses and persistence APIs rather than adding
a second representation for the same holding. Pure calculations should stay
functions; stateful lifecycles benefit from small, focused classes.

## Shared Components

- `DashboardPrivacy` owns saved screen-privacy state, the declared personal
  scopes, control synchronisation, initial paint protection, dynamic DOM
  classification and private input/tooltip handling. Masked values are swapped
  in the text flow, with originals held in memory rather than in the markup.
- `RenderCache` owns a bounded collection of short-lived response snapshots,
  monotonic expiry, a lock and prefix invalidation. Existing server helpers
  delegate to it, preserving route callers.
- `PublicationAudit` inspects publishable files without printing matched secret
  values. Public repositories and the private operational repository have
  different policies. It is a focused guard, not an exhaustive secret scanner.
- `BacktestManifestAudit` checks checksums, SQLite integrity, row counts and
  coverage. Metadata refresh is explicit and does not download or rebuild data.
- `LocalSnapshotBackup` copies local records one way, uses SQLite's backup API,
  archives replaced backup versions, and never merges unknown schemas into the
  local book. Publication requires explicit `--push`.
- `table_sort.py` gives every dashboard table click-to-sort from the shared head
  script. It reads only displayed text, keeps attached detail rows and totals
  rows in place, and re-applies the chosen order after live refreshes.
- `coffee.py` renders the "Buy me a coffee" button and dialog from the same public
  UPI details and QR code that Rupevo publishes.
- `nav.py` renders the app shell shared by every page: a fixed top app bar (brand,
  page status pills, privacy, theme and coffee controls) and a navigation rail that
  becomes a bottom bar on phones. `theme.py` holds the Material 3 tokens shared with
  Rupevo, lays out the shell and harmonises each page's own CSS; `icons.py` holds
  the line icons.

## Privacy Contract

Masking is scoped: only regions a renderer declares personal are touched.
Mark a personal region with `data-personal`, a single identity or quantity with
`data-private`, personal numbers mixed with percentage performance with
`data-private-values`, and personal form values with `data-private-input`.
Anything outside those regions — guides, thresholds, reference tables, tool
costs and research figures — stays readable, so a demo still explains itself.

`data-privacy-public` is an explicit, reviewed exception inside a personal
region: public market prices, percentage-only charts or simulated research
data. Never put a personal holding, folio, budget or absolute return inside
that exception. Monetary charts are hidden unless deliberately converted to
public/percentage-only values and checked for tooltip leaks.

A masked value is replaced in the text flow, so the placeholder inherits its
container's alignment and baseline and lines up as a column. Plain counts such
as "23 runs" are left alone; only amounts, quantities and identities are hidden.

The saved `dashPrivacy` preference migrates the old HQ `homePrivacy` setting.
Privacy survives navigation and reloads, synchronises tabs and reclassifies
asynchronously rendered sections. Disabled storage defaults to hidden values.
The HQ and navigation controls operate the same state.

Links to Rupevo, the public companion site, are fixed addresses defined in
`modes/dashboard/rupevo.py`. Never build a Rupevo URL from personal data such as
held symbols, amounts, folios or scheme codes. Links open in a new tab with
`rel="noopener noreferrer"`, and the server also sends `Referrer-Policy: no-referrer`.

Table sorting reads the text a cell currently shows. While privacy is on, masked
amounts sort as equal values and keep the original row order, so sorting never
reveals a ranking of hidden values.

Screen privacy does not remove the underlying API/HTML/browser-memory data.
Do not expose the personal server publicly, share raw snapshots, open developer
tools during a recording, or show broker login/credential screens. The public
Theory page does not query the personal ledger. Test screenshots with synthetic
data or the public page; keep private captures outside the code repository.

## Repository Roles

| Repository | Allowed contents | Publication boundary |
|---|---|---|
| Application | Source, tests, documentation and labelled research summaries | No personal identifiers, balances, positions or credentials. Deliberate exception: the maintainer's donation UPI ID, payee name and QR code, the same details Rupevo publishes. |
| Backtest data | Historical market data, schemas, metadata and checksums | No personal records. Provider redistribution rights need a separate review before publication. |
| Operational data | Personal books, tax/trade records, reports, logs and private runbooks | Must remain private; never store credentials, even invalidated tokens. |
| Rupevo (public website) | Static site, market snapshots and copies of the pure scoring engines | Public. No personal records, credentials or broker access; screener-style output without buy/sell calls or entry, stop and target levels. The dashboard only links to it. |

Git ignore rules do not remove a file already tracked, and removing it from
the current index does not erase history. The invalidated token previously
tracked in the operational repository was removed from tracking; old commits
still need a separate exposure/rotation review. Public source history may also
retain older personal references. No history rewrite was performed.

Backtest inventory dates are not market-data freshness dates. Read each
interval's coverage in the manifest; do not imply that reviewing the manifest
reran a strategy or acquired recent candles.

## Verification

Run the workflow's Ruff, compile and unittest gates before pushing, plus the
publication audit. Financial tests cover tax slabs, indicators, scoring,
pricing, trade fallback and US long-term decisions. New focused checks cover
screen-privacy contracts, public-only Theory rendering, cache lifecycle,
non-interactive authentication and backup preservation.

The latest inspected remote failure, run 33066222750 on 2026-08-27, was the
lint step: five unused imports (`F401`). Tests were skipped because lint failed.
The unused imports were removed without weakening the CI rules.

## Remaining Work

This maintenance pass used repository-wide static checks and tests plus
targeted reviews of dashboard, authentication, prompt and backup paths. It is
not a line-by-line proof of the entire trading engine. Large HTTP handlers
and page renderers remain; extract routes by domain behind their existing
interfaces as adjacent changes gain tests. Avoid a wholesale rewrite of
execution or tax calculations for a presentation.

For multi-user hosting, authentication, authorisation, request-origin checks,
API redaction and an isolated synthetic demo dataset are required. A local
privacy toggle is not that deployment architecture. Wider browser automation,
input-level coverage scores and fresh, untouched strategy evaluations remain
separate work. Intraday and options cannot be promoted by documentation alone.