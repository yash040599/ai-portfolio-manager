# AI Portfolio Manager

Automated intraday trading bot for the **Indian stock market (NSE)**. Uses
technical indicators + candlestick patterns for stock selection, **Zerodha
Kite** for data and execution, and optionally **Claude AI** for selection
and reviews via `--ai`.

> **V1 is DEPRECATED and FROZEN (April 2026).** Do not modify
> `portfolio/manager.py` (V1 base class), `services/stock_scanner.py`, or
> `docs/STRATEGY_V1.md`. Shared components (`order_engine.py`,
> `market_data.py`, …) may evolve for V2/NoAI — V1 inherits passively.
> All new work targets V2 or V2 NoAI.

<!-- ══════════════════════════════════════════════════════════════
README MAINTENANCE CONTRACT (read before editing this file).

Purpose: keep the README short, scannable, and honest about scope.
Copilot/automation should follow this contract so updates are
consistent across edits.

Structure (do NOT reorder, do NOT merge):
  1. What it does       — 3 modes (Phase 1, Phase 2, Phase 3 dashboard). One paragraph + bullets.
  2. Quick start        — install + first run, max 7 commands.
  3. Documentation map  — table of links to docs/* (single source of truth).
  4. Prerequisites      — bullets only.
  5. Setup              — numbered steps with copy-paste commands.
  6. Run modes          — table of CLI flags.
  7. Project structure  — tree (truncated; full layout in docs/).
  8. Run on a VM        — Azure Ubuntu instructions.
  9. Reports & data     — where files land + sync.
  10. Taxation          — link to TAX_GUIDE; brief script table.
  11. Cost / safety     — bullets only.
  12. Disclaimer        — fixed text.

Style rules:
  • Prefer BULLETS over paragraphs. Max 3 sentences per bullet.
  • Use TABLES for: scripts, CLI flags, env vars, doc links, costs.
  • NEVER duplicate STRATEGY_V2.md content — link, don't copy.
  • Each section starts with a 1-line "what is this" sentence.
  • Use third-level headings (###) for sub-sections, not bold paragraphs.
  • All file/directory references use markdown links to actual paths.
  • Code blocks: shell (no language tag if mixed prompts) or `python`.
  • If a feature is shipped, this doc reflects it; if not, it does NOT.

When updating:
  • Find the right section by structure number above; edit IN PLACE.
  • Bump version stamps where present (e.g. "V1 is FROZEN (April 2026)").
  • Cross-check the docs map (section 3) before adding new doc references.
  • Run `python main.py --help` after any CLI flag change to verify the
    flag table matches reality.

When in doubt: terser is better. Long-form belongs in docs/STRATEGY_V2.md.
══════════════════════════════════════════════════════════════ -->

---

## 1. What it does

Two modes, one binary. Pick a mode at the CLI.

### Phase 1 — Portfolio analysis (read-only)

- Logs into Zerodha, reads your demat holdings.
- Sends each holding to Claude with full prior-analysis context.
- Generates a per-stock recommendation (HOLD / BUY MORE / EXIT / …) plus
  a portfolio-wide assessment with new ideas.
- No orders placed.

```
python main.py --mode analyze
```

### Phase 2 — Intraday trading (V2, default)

Fully automated NSE intraday loop. **NoAI is the default** (zero Claude
calls, pure indicators); add `--ai` to put Claude in the selection loop.

Loop, in plain English:

1. **Pre-market scan** — fetch candles for every stock in `SCAN_UNIVERSE`,
   apply price filter, run candlestick + indicator detectors, score, pick
   the best candidates.
2. **Execute** — LIMIT entry at LTP + 1 tick (MARKET fallback), ATR-based
   SL/target with min-distance floor, **40-check pre-trade pipeline** (see
   [STRATEGY_V2.md](docs/STRATEGY_V2.md#risk-management--entry-pre-checks)
   for the full table), risk-budget position sizing.
3. **Monitor** — adaptive polling, auto-trail SL, partial profits,
   two-tier stagnant exit, three score-driven exits (signal-reversal,
   signal-decay, auto-protect SL-tighten) on the free 15-min re-scan.
4. **Risk** — circuit breaker (3% hard) + soft-stop (1.5%), peak-drawdown
   stop, whipsaw guard, sector caps, regime-shift protection, India VIX,
   crash recovery, manual-trade adoption with grace window, Thursday +
   holiday-shifted expiry adjustments, dynamic budget regimes.
5. **EOD** — square off, generate P&L + tax report, auto-verify trades
   against Zerodha, run rejection audit (verdict on every skipped entry).

```
python main.py --mode trade           # NoAI (default)
python main.py --mode trade --ai      # with Claude
```

### Phase 3 — Profitability dashboard (D1 + D1.1 + D13 shipped)

A dedicated **read-only analytics layer** that answers one question:
*"is the bot profitable enough to scale capital?"*. Default launch
starts a local web server and opens an interactive page in the
browser — the **webpage itself is the config surface** (date range,
granularity, source toggle), so the CLI is just an entry point.

- Defaults to current Indian FY (Apr 1 → Mar 31); a single Quick-range dropdown covers This FY / Previous FY / FY before previous / This month / Last month / Last 7d / Last 30d / Last 90d / All time, plus from/to date pickers.
- Two charts (Chart.js via CDN, zero new Python deps): cumulative net P&L (line, daily) + per-bucket P&L (bar, daily/weekly/monthly switchable). Cumulative chart overlays a thin dashed vertical line at every trading day where the bot's git SHA changed (D13, shipped 2026-04-27); hover shows the commit subject so you can visually correlate strategy ships with equity-curve inflections. Toggle off via `Config.DASHBOARD_STRATEGY_VERSION_OVERLAY = False`.
- Capital-ladder traffic-light verdict (GREEN / AMBER / RED / GREY) — D1 minimum-viable rules; D6 will plug in win-rate / profit-factor / max-DD / weeks-required gates.
- Source toggle: **all trades** (verified + provisional, the default) or **verified only** (T+1 frozen, tax-grade). Provisional rows are clearly badged so they can never be mistaken for final numbers.
- `% of budget` is computed against the **per-day budget actually deployed** (read from each day's `reports/trading/.../trading_data_DD.json` → `config.budget`), not a static config value — matters because `--max` varies day to day.
- Pending-verification banner lists trading days awaiting Zerodha sheet import.
- Lives in its own [Dashboard/](Dashboard/) folder, isolated from the trading bot. Touches no strategy/order code; reads only.
- **Theory & statistics pages** ([`/theory/<slug>`](Dashboard/theory_page.py)) — four reference docs rendered live from `docs/` with KaTeX math + dropdown nav: Statistical Analysis (with a theoretical-vs-live snapshot card on top), V2 Strategy reference, Strategy Evolution log, and the India Tax Guide.
- **Tax page** ([`/tax`](Dashboard/tax_page.py)) — FY-summary + projection. Enter your other FY income; the page computes which slab you fall into under Budget-2025 new-regime rules, applies Section 87A rebate + 4% cess, and shows the headline "tax attributable to intraday this FY" (= total-tax-with-intraday minus total-tax-without). Includes click-to-copy ITR-3 Schedule BP fields, a documents checklist, and a cross-link to the Tax Guide for the regulatory reference. Backed by versioned slabs in [`Dashboard/tax/slabs.py`](Dashboard/tax/slabs.py) — adding a future FY is a one-line config.

```
python main.py --mode dashboard                    # interactive (server + browser)
python main.py --mode dashboard --no-open          # static HTML snapshot
python main.py --mode dashboard --text             # legacy plain-text
python main.py --mode dashboard --port 8765        # fixed port
```

Full plan: [Dashboard/docs/DASHBOARD_ROADMAP.md](Dashboard/docs/DASHBOARD_ROADMAP.md) (D1 + D1.1 + D13 + theory-pages done; D2–D12, D14–D23 pending).

### Historical candle cache

- `data/candle_cache.db` (SQLite) keeps prior days' candles to avoid
  re-fetching from Zerodha.
- Auto-cleanup, weekend/holiday-aware lookback, corporate-action
  detection, rate limiting.
- Committed to Git (pure market data).
- Pre-warm by running `--test` the evening before.

---

## 2. Quick start

```bash
git clone https://github.com/<you>/ai-portfolio-manager.git
cd ai-portfolio-manager
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                  # then fill in keys
python main.py --mode login                           # one-time Zerodha login
python main.py --mode trade                           # start the bot
```

---

## 3. Documentation map

Single-source-of-truth lives in `docs/`. The README never duplicates
their content.

| Doc | What it covers |
|-----|----------------|
| [docs/STRATEGY_V2.md](docs/STRATEGY_V2.md) | Complete V2 strategy — NoAI + AI modes, 40-check pre-trade pipeline, all indicators/patterns, scoring, risk layers, glossary |
| [docs/STRATEGY_V1.md](docs/STRATEGY_V1.md) | V1 architecture (deprecated, frozen) |
| [docs/STRATEGY_ROADMAP.md](docs/STRATEGY_ROADMAP.md) | Pending / Awaiting-Data / Removed / Completed items with priorities |
| [docs/STRATEGY_EVOLUTION.md](docs/STRATEGY_EVOLUTION.md) | Chronological one-line history of every shipped strategy item (auto-regenerated from the Roadmap) |
| [docs/STRATEGY_STATISTICS.md](docs/STRATEGY_STATISTICS.md) | Theoretical edge math + live snapshot. §2.5 holds the per-item ΔEV / ΔMDD verdict every shipped strategy item must carry. Rendered live at the dashboard's `/theory/statistics` page. |
| [Dashboard/docs/DASHBOARD_ROADMAP.md](Dashboard/docs/DASHBOARD_ROADMAP.md) | **Phase 3 (D1 + D1.1 + theory pages shipped)** — Profitability dashboard roadmap; lives in its own `Dashboard/` folder |
| [docs/IDEATIONS.md](docs/IDEATIONS.md) | V3 research ideas (ML scoring, options chain, Claude narrative) |
| [docs/TAX_GUIDE.md](docs/TAX_GUIDE.md) | India intraday tax guide (FY 2026-27 ready) |

---

## 4. Prerequisites

- **Python 3.10+** (uses modern type syntax).
- **Windows / Linux / macOS** — works on headless Linux VMs (manual login
  mode for SSH-only setups).
- **Zerodha account** with [Kite Connect](https://developers.kite.trade)
  subscription (Rs.500/month).
- **Claude API key** from [Anthropic](https://console.anthropic.com) —
  only needed for `--ai` and `--mode analyze`.

---

## 5. Setup

### 5.1 Install

```bash
cd ai-portfolio-manager
pip install -r requirements.txt
```

| Package | Purpose |
|---------|---------|
| `anthropic` | Claude API client |
| `kiteconnect` | Zerodha Kite trading API (≥ 5.1.0 required for `market_protection`) |
| `python-dotenv` | Loads keys from `.env` |
| `openpyxl` | Reads Zerodha Tax P&L xlsx files |

### 5.2 API keys

Create `.env` in the project root:

```env
ZERODHA_API_KEY=...
ZERODHA_API_SECRET=...
CLAUDE_API_KEY=...
```

#### Zerodha Kite Connect

1. [zerodha.com/open-account](https://zerodha.com/open-account) → fund your account.
2. [developers.kite.trade](https://developers.kite.trade) → subscribe Kite Connect.
3. Create app → name = anything, redirect URL = `http://localhost:8080`,
   type = `default`. Copy **API Key** and **API Secret**.
4. **Whitelist your public IP** (mandatory from 1 April 2026 per SEBI).
   On the app page, add the IP of every machine that runs the bot. For a
   VM use its **static** public IP. Multiple IPs are comma-separated.
5. Tokens expire daily at midnight; the bot re-prompts. On SSH-only VMs
   pick **manual mode** (paste the redirect URL from your phone).

#### Claude API (Anthropic)

1. Sign up at [console.anthropic.com](https://console.anthropic.com).
2. Settings → Billing → add credits (Rs.500–1000 to start; ~Rs.50–100/day on Pro).
3. Settings → API Keys → Create Key (shown only once).

### 5.3 Configure

Open [config.py](config.py). Common settings:

| Setting | Default | Controls |
|---------|---------|----------|
| `MAX_BUDGET_INR` | 20,000 | Max capital deployed per day |
| `SCAN_UNIVERSE` | NIFTY100 | Stock pool (overridable per-run with `--nifty 50/100/150/200`) |
| `MAX_POSITIONS` | 3 | Simultaneous trades |
| `DRY_RUN` | False | Simulate without real orders (or use `--dryrun`) |
| `CLAUDE_PLAN` | pro | Claude tier: `free`, `pro`, `max` |
| `RR_TARGET_RATIO` | 1.5 | Base R:R from ATR |
| `RR_HARD_FLOOR` | 1.3 | Always-on R:R floor — uniform across the trading day (collapsed from the deprecated time-tiered floors by #243) |

---

## 6. Run modes

| Command | What it does |
|---------|--------------|
| `python main.py --mode analyze` | Phase 1 — read holdings, Claude analysis |
| `python main.py --mode trade` | Phase 2 NoAI (default) |
| `python main.py --mode trade --ai` | Phase 2 with Claude |
| `python main.py --mode trade --noai` | Same as default; explicit |
| `python main.py --mode trade --dryrun` | Full strategy, no real orders |
| `python main.py --mode trade --test` | See pipeline only (no Claude, no trades, no cost) |
| `python main.py --mode trade --max 30000` | Cap today's capital at Rs.30,000 |
| `python main.py --mode trade --nifty 150` | Override scan universe |
| `python main.py --mode trade --v1` | V1 legacy (deprecated) |
| `python main.py --mode login` | Test Zerodha login only |
| `python main.py --mode dashboard` | Launch interactive profitability dashboard (local server + browser). `--no-open` writes a static HTML snapshot; `--text` prints plain text; `--port N` pins a port. See [Dashboard/docs/DASHBOARD_ROADMAP.md](Dashboard/docs/DASHBOARD_ROADMAP.md) |

**Ctrl+C** triggers graceful shutdown — squares off all positions first.
Phase 2 can be started any time (handles weekends / NSE holidays / late
starts / token expiry automatically).

---

## 7. Project structure

```
ai-portfolio-manager/
├── main.py                       # entry point
├── config.py                     # all settings
├── requirements.txt
├── .env                          # API keys (gitignored)
├── core/
│   ├── claude_client.py          # Claude wrapper + error classification
│   ├── zerodha_client.py         # Kite wrapper
│   └── logger.py                 # coloured terminal + rotating file log
├── portfolio/
│   ├── analyser.py               # Phase 1
│   ├── manager.py                # Phase 2 V1 (FROZEN)
│   └── manager_v2.py             # Phase 2 V2 (active)
├── services/
│   ├── analysis_queue.py         # Per-stock Claude analysis with retry
│   ├── market_data.py            # Live prices + history enrichment
│   ├── stock_scanner.py          # V1 scanner
│   ├── stock_scanner_v2.py       # V2 candle pre-filter + scorer
│   ├── candle_patterns.py        # 14 pure-math pattern detectors
│   ├── candle_cache.py           # SQLite cache for candles
│   ├── technical_indicators.py   # Indicators + composite scoring
│   ├── order_engine.py           # 40-check entry pipeline + monitoring
│   ├── report_writer.py          # txt + json reports
│   └── performance_tracker.py    # SQLite trades + analyses
├── scripts/                      # see Sections 9 + 10 for tables
├── docs/                         # see Section 3 doc map
├── Dashboard/                    # ✅ Phase 3 (D1 + D1.1 shipped) — read-only analytics layer
│   ├── cli.py                    # argparse entry; `python main.py --mode dashboard`
│   ├── server.py                 # stdlib http.server SPA backend (/, /api/data)
│   ├── data_layer.py             # DB reads, sheet-verified filtering, FY window
│   ├── metrics.py                # headline P&L, bucketed/cumulative series
│   ├── budget_history.py         # per-day budget from trading_data_*.json
│   ├── verdict.py                # capital-ladder traffic-light engine
│   ├── render_html.py            # Chart.js SPA shell + JSON payload builder
│   ├── render_text.py            # plain-text mode (--text)
│   └── docs/DASHBOARD_ROADMAP.md # full plan (D1 + D1.1 done; D2–D23 pending)
├── data/                         # gitignored (trades.db, tokens, etc.)
├── reports/                      # generated; gitignored
└── logs/                         # rotating logs; gitignored
```

---

## 8. Running on a VM (Azure Ubuntu)

A cloud VM with a **static public IP** sidesteps SEBI's IP whitelist
hassle (whitelist once on Kite, never again). Azure B1s free tier covers
the first month.

### One-time setup

```bash
ssh azureuser@<vm-ip>
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git
```

Authenticate with GitHub via one of:

- **GitHub CLI:** `sudo apt install -y gh && gh auth login`
- **Personal Access Token:** clone with `https://USER:TOKEN@github.com/...`
- **SSH key:** `ssh-keygen -t ed25519` → paste `~/.ssh/id_ed25519.pub`
  into GitHub → Settings → SSH keys.

```bash
git clone <repo-url> ai-portfolio-manager
cd ai-portfolio-manager
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cat > .env <<'EOF'
ZERODHA_API_KEY=...
ZERODHA_API_SECRET=...
CLAUDE_API_KEY=...
EOF
python scripts/backup_data.py --ssh   # pull data from your private backup repo
python main.py --mode login           # manual mode (option 'm')
```

### Daily operation

```bash
ssh azureuser@<vm-ip>
cd ai-portfolio-manager
source venv/bin/activate
tmux new -s bot                       # detach with Ctrl+B, D
python main.py --mode trade
```

To re-attach after disconnect: `tmux attach -t bot`. After a VM reboot
the tmux session is gone; start fresh with `tmux new -s bot`.

After the trading day:

```bash
python scripts/backup_data.py --ssh
```

> **Tokens are IP-specific.** Delete `data/access_token.json` when
> switching machines so the bot prompts for fresh login.

---

## 9. Reports & data

### Report layout

- Phase 1 → `reports/portfolio/<year>/<month>/portfolio_report_DD.txt` + `.json`.
- Phase 2 → `reports/trading/<year>/<month>/trading_report_DD.txt` + `.json`.

Multiple Phase-2 sessions on the same day merge into one combined report
with cumulative P&L. Reports include full tax breakdown and Claude API
costs.

### Database (SQLite)

`data/trades.db` (auto-created) and `data/candle_cache.db`. `data/` is
gitignored except `candle_cache.db` (public market data is committed).

| Table | Stores |
|-------|--------|
| `trades` | Intraday trades — symbol, side, entry/exit, qty, P&L, exit reason |
| `intraday_tax_ledger` | Per-trade charges for ITR-3 |
| `capital_gains_ledger` | Short-term + long-term capital gains |
| `portfolio_analyses` | Phase 1 results — action, conviction, reasoning, targets |

### Utility scripts

| Script | Purpose |
|--------|---------|
| `scripts/view_trades.py` | All intraday trades with P&L summary |
| `scripts/view_performance.py` | Daily P&L, win rate, exit stats, indicator correlation |
| `scripts/view_analyses.py` | Phase 1 analyses with action status |
| `scripts/generate_sheet.py` | TSV spreadsheet from a portfolio report (1 Claude call) |
| `scripts/view_candle_cache.py` | Inspect candle cache contents |
| `scripts/verify_trades.py` | EOD trade verification vs Zerodha API |
| `scripts/rejection_audit.py --append-report` | Verdict on every skipped entry |
| `scripts/import_reports_to_db.py` | One-time backfill of old JSON reports |

All scripts support `--help`.

### Data sync (private repo)

`data/`, `reports/`, `logs/` are personal — keep them in a **separate
private repo** so they're portable across machines.

```bash
python scripts/backup_data.py            # two-way append-merge + push (HTTPS)
python scripts/backup_data.py --ssh      # SSH (Linux VMs)
python scripts/backup_data.py --dry-run  # preview, no writes

# Manual-fix flow (you edited a row/report on this machine — make it the truth)
python scripts/backup_data.py --prefer local    # local wins, edits propagate via UPSERT
python scripts/backup_data.py --prefer remote   # remote wins (rare — adopt VM's version)

# Nuclear reset (also DELETES files not on the chosen side; prompts y/n)
python scripts/backup_data.py --all-local       # full overwrite of remote
python scripts/backup_data.py --all-remote      # full overwrite of local
```

| Scenario | Action |
|----------|--------|
| File only one side | Copied across |
| File both sides, identical | Skipped |
| `.db` in both, different (default) | **Append-merge** — new rows from each side added; nothing overwritten or deleted |
| `.db` in both with `--prefer X` | **Row UPSERT** — X's values win on key collisions; rows only on the OTHER side preserved |
| Other file in both, different (default) | Asks `l/r` |
| Other file in both, different with `--prefer X` | X's copy kept (no prompt) |
| Log files (`logs/portfolio.log`) | Always line-merged (chronological union) |

**Two normal flows**

1. **EOD VM → coding machine** (no flag needed):
   - VM: `python scripts/backup_data.py --ssh` after market close.
   - Dev machine: `python scripts/backup_data.py` next morning.
   - DBs append-merge cleanly because both sides only added new rows.

2. **Manual data fix on coding machine → VM** (use `--prefer local`):
   - Edit a DB row or report .txt to correct bad data.
   - `python scripts/backup_data.py --prefer local` — your edits become the truth.
   - VM picks up corrections on its next pull.

> The data repo MUST be **Private**. The main code repo has no link to
> it — only the sync script knows the URL.

**GitHub 100 MB file limit**

GitHub rejects any single file > 100 MB on push. The sync script will
fail with a clear error from `git push` if this happens. Two protections
are in place:

- **Dedup key uses null-safe `IS` comparison** so the `trades` table
  doesn't double on every sync (a single bug here previously inflated
  `data/trades.db` from <1 MB to 135 MB over ~20 syncs).
- **Periodic check.** If `data/trades.db` ever grows unexpectedly
  (>10 MB for normal usage), inspect with:
  ```bash
  python -c "import sqlite3; c=sqlite3.connect('data/trades.db'); print('trades rows:', c.execute('SELECT COUNT(*) FROM trades').fetchone()[0])"
  ```
  Real row count for a few months of trading should be in the low
  hundreds. If you see >10 000, the dedup is broken — bisect by date
  and rebuild the table (see `_dedup` pattern in commit history).

`--all-local` and `--all-remote` are NOT immune to the 100 MB limit —
they `git push` the chosen side as-is. If the file is already too big,
you must shrink it first (DELETE + VACUUM) before any sync flag will
succeed.

---

## 10. Taxation

Intraday is **speculative business income** in India (ITR-3). Bot tracks
brokerage, STT, GST, stamp duty per trade, separates short-term and
long-term capital gains.

Full guide: **[docs/TAX_GUIDE.md](docs/TAX_GUIDE.md)** (slabs, advance
tax dates, loss carry-forward).

| Script | Purpose |
|--------|---------|
| `scripts/fill_intraday_ledger.py` | Build intraday ledger from trade JSONs (auto-runs EOD) |
| `scripts/verify_trades.py` | Verify trades vs Zerodha; correct prices in reports + ledger + DB |
| `scripts/import_zerodha_taxpnl.py [--fy YYYY]` | Import Zerodha Tax P&L xlsx (intraday + capital gains) |
| `scripts/tax_summary.py [--intraday] [--fy YYYY]` | Combined tax summary — speculative + STCG + LTCG + estimated tax |
| `scripts/view_intraday_ledger.py [--fy YYYY] [--list]` | Intraday ledger view |
| `scripts/view_capital_gains_ledger.py [--list]` | Capital gains ledger view |

Tax rates configurable in [config.py](config.py): `TAX_RATE_PCT`,
`STCG_TAX_RATE_PCT`, `LTCG_TAX_RATE_PCT`, etc.

---

## 11. Cost & safety

### Cost

| Cost | Amount | Frequency |
|------|--------|-----------|
| Zerodha Kite Connect | Rs.500 | Monthly |
| Claude API (Pro) | ~Rs.50–100 | Per trading day (`--ai` only; NoAI = Rs.0) |
| Brokerage + charges | ~0.05–0.15% of turnover | Per trade |

### Safety features

- **Dry-run** — simulated P&L with time-of-day slippage modelling.
- **Circuit breaker** — daily loss hard-stop with cooldown + max trips.
- **Daily-loss soft-stop** — at -1.5% blocks new entries, manages existing.
- **Peak-drawdown stop** — blocks new entries when day P&L gives back ≥1.5% from intraday peak.
- **MTM-aware circuit breaker** — circuit breaker, soft-stop, and peak-drawdown all include open-position unrealised MTM (not just closed P&L), so blowups are caught while positions are still open.
- **Choppy-morning entry pause** — auto-pauses new entries (15 min, sliding) when NIFTY 15-min ADX prints weak (<16) for 3 consecutive scans in 09:30–10:30 IST AND ≥2 recent exits were STAGNANT/SIGNAL_DECAY. Re-arms each session.
- **Whipsaw guard** — pauses entries after 3 consecutive losing exits (post-#244 broadening: any of STOP_LOSS, MOMENTUM_KILL, STAGNANT_EXIT, SIGNAL_DECAY, or LOSER_EXIT with `pnl < 0`; EOD/operator closes excluded).
- **Per-symbol re-entry cooldown** — 30 min on same `SYMBOL_SIDE`.
- **Stale-score guard** — after the post-open observation wait, re-runs the scoring and aborts entries whose conviction sign-flipped, decayed below 60% of the scan-time score, OR (#199) lost magnitude (`|fresh| + 0.3 < |entry|`) — catches the slow-bleed setups the magnitude-only floor missed.
- **Post-entry momentum kill (#198, retuned by #233)** — exits at market between 3 and 5 min after fill if the trade is unrealised-loss, has moved adversely by ≥0.40% (≈4× typical NSE intraday spread), AND has covered <25% of the entry→target distance. Caps slow-bleed losers at ~-0.4% instead of waiting for the -1.1% SL hit. The 3-min grace + adverse-move floor were added on 2026-04-27 after the original 60s/no-floor settings killed 4/4 morning entries on sub-spread micro-moves.
- **Pattern↔tech contradiction penalty (#200)** — at the scanner combine, subtracts 2.0 from `|combined_score|` when patterns include an opposite-side reversal (e.g. BUY candidate showing `BEARISH_ENGULFING`) and 0.5 when patterns include `DOJI` indecision; weak-conviction conflict setups fall below `V2_MIN_SCORE` naturally.
- **VWAP statistical-band gate (#201)** — blocks BUY at the upper 1σ/2σ VWAP band and SELL at the lower 1σ/2σ; complements the existing % VWAP-extension check with a volatility-adaptive band classifier. Override at `|score| ≥ 7.0`.
- **Late-entry tightening (#202, retuned by #239)** — after 10:00 IST: `MIN_SCORE` bumped by +1.0 (raised 0.5 → 1.0 by #239 after first live day showed +0.5 was too gentle). R:R floor and concurrency are owned by always-on `RR_HARD_FLOOR` + `dynamic_max_positions(budget)` (simplified by #225).
- **Realised-P&L recovery on restart (#203)** — on init, scans Zerodha net-positions for already-closed MIS round-trips not in our session and imports them as synthetic CLOSED records so the MTM-aware safety gates and adaptive budget reason from the correct realised baseline after a mid-session restart.
- **Lunch-lull skip** — 11:30-12:15 IST unless `|score| ≥ 5.7`.
- **Charge-aware target (retuned by #238)** — gross target ≥ 3× round-trip charges (was 2×), so every trade carries 2× charges of slippage cushion.
- **Budget-adaptive minimum profit (#237)** — `effective_min_profit()` floor: Rs.135 on TINY/SMALL (3× typical round-trip charges), Rs.200 NORMAL, Rs.400 LARGE. Auto-scales when you raise `--max`.
- **Budget-adaptive spread cap (#236)** — `effective_max_spread()`: 0.20% on TINY/SMALL, 0.30% NORMAL/LARGE. Tighter cap on small budgets where spread eats a large share of the per-trade charge hurdle.
- **Budget-regime trade cap** — `MAX_TRADES_PER_DAY` is regime-tightened: 8 on TINY/SMALL (#240), 12 NORMAL, 15 LARGE. Forces fewer-and-better trades at small budgets where charge hurdle is high.
- **Budget-regime gates** — ADX, score floor, and trade-cap auto-tighten on TINY/SMALL accounts (#165).
- **Loss-adjusted sizing** — shrinks position size after losses.
- **ATR-based SL/target** — pure ATR with structural-level cap.
- **Bid-ask spread + impact-cost check** — skips paper-thin books.
- **Crash recovery** — re-adopts orphaned positions and orphan SL-M orders on restart.
- **Loud SL-M failure alert** — never silently runs naked.
- **`market_protection` on every order** — Zerodha-side circuit safeguard.
- **Existing demat holdings are READ-ONLY** — only the managed budget pool is traded.
- **Graceful shutdown** — Ctrl+C squares off everything before exit.

Full risk architecture: **[docs/STRATEGY_V2.md](docs/STRATEGY_V2.md#risk-management--entry-pre-checks)**.

---

## 12. Disclaimer

This software is for educational and experimental purposes. Stock market
trading involves substantial risk of loss. Past performance (including
dry-run results) does not guarantee future results. Use at your own
risk. The authors are not responsible for any financial losses incurred
from using this software.
