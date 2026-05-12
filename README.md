# AI Portfolio Manager

Automated intraday trading bot for the **Indian stock market (NSE)**. Uses
technical indicators + candlestick patterns for stock selection, **Zerodha
Kite** for data and execution, and optionally **Claude AI** for selection
and reviews via `--ai`.


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
  • NEVER duplicate TRADE_STRATEGY.md content — link, don't copy.
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

When in doubt: terser is better. Long-form belongs in docs/TRADE_STRATEGY.md.
══════════════════════════════════════════════════════════════ -->

---

## 1. What it does

Three surfaces, one CLI. Pick a mode at the CLI.

### Phase 1 — Portfolio analysis (read-only)

- Logs into Zerodha, reads your demat holdings.
- Default flow is **NoAI**: deterministic enrichment from Zerodha
  (positions, quotes, 52-week range), candle cache (long-term technicals
  — SMA-50, SMA-200, RSI-daily, beta vs NIFTY), and hand-curated
  reference files (sector map, dividends, fundamentals seed). Every
  field carries `source` + `as_of` so you know exactly how stale each
  number is.
- `--ai` adds a Claude overlay on top of the same NoAI base — long-term
  thesis, qualitative risks, peer comparison, news context — without
  regenerating any of the deterministic numbers.
- Per-stock recommendation (HOLD / BUY MORE / AVERAGE DOWN /
  PARTIAL EXIT / FULL EXIT) plus a portfolio-wide review with sector
  gaps, AMFI market-cap tier (LARGE / MID / SMALL / ETF) breakdown,
  concentration risks, and "what's missing" suggestions
  (industry-standard portfolio-analyser checks).
- Long-term horizon throughout. No orders placed.
- Surfaced live on the **Dashboard** (`/portfolio` page) — see Phase 3.

```
python main.py --mode analyze         # NoAI (default)
python main.py --mode analyze --ai    # with Claude
```

Full plan: [docs/ANALYZE_ROADMAP.md](docs/ANALYZE_ROADMAP.md) (P1-P7
foundation in flight; D24-D29 dashboard surface in flight).

### Phase 2 — Intraday trading (V2, default)

Fully automated NSE intraday loop. **NoAI is the default** (zero Claude
calls, pure indicators); add `--ai` to put Claude in the selection loop.

Loop, in plain English:

1. **Pre-market scan** — fetch candles for every stock in `SCAN_UNIVERSE`,
   apply price filter, run candlestick + indicator detectors, score, pick
   the best candidates.
2. **Execute** — LIMIT entry at LTP + 1 tick (MARKET fallback), ATR-based
   SL/target with min-distance floor, **44-check pre-trade pipeline** (see
   [TRADE_STRATEGY.md](docs/TRADE_STRATEGY.md#risk-management--entry-pre-checks)
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

### Phase 3 — Dashboard (tool-wide read-only surface)

The dashboard is the project's **single tool-wide read-only surface**.
It hosts pages for every mode the project exposes — Portfolio analysis,
Intraday trading P&L, Tax filing, Theory & strategy reference — and is
independent of every mode's code path (touches no strategy / order /
config code). Default launch starts a local web server and opens the
default page in your browser; the webpage itself is the config surface
for date range / source toggles / per-stock drill-down / "Analyse now"
buttons, so the CLI is just an entry point.

Pages:

- **`/portfolio`** (default landing) — Phase 1 analyser surface.
  Reads the latest `--mode analyze` run from `data/portfolio_analyses.db`,
  shows holdings summary + portfolio metrics + "what's missing" panel
  + a per-stock drill-down with on-demand "Analyse now (NoAI / AI)"
  buttons. Header carries the most-stale `as_of` across the run so you
  can see how fresh the analysis is. Login flow integrated for the
  on-demand runs.
- **`/trading`** — intraday-trading profitability view (the original
  Phase 3 SPA from D1.1). Two charts (Chart.js via CDN, zero new
  Python deps): cumulative net P&L (line, daily) + per-bucket P&L
  (bar, daily/weekly/monthly switchable). Cumulative chart overlays a
  thin dashed vertical line at every trading day where the bot's git
  SHA changed (D13); hover shows the commit subject. Capital-ladder
  traffic-light verdict (GREEN / AMBER / RED / GREY). Source toggle:
  all trades (default) or verified only (T+1 frozen, tax-grade).
  `% of budget` is computed against the per-day budget actually
  deployed (read from each day's `reports/trading/.../trading_data_DD.json`).
  Quick-range dropdown (This FY / Previous FY / Last 7d / Last 30d /
  All time / from-to date pickers). Pending-verification banner lists
  trading days awaiting Zerodha sheet import.
- **`/tax`** — FY-summary + projection. Enter your other FY income;
  computes which slab you fall into under Budget-2025 new-regime
  rules, applies Section 87A rebate + 4% cess, shows the headline
  "tax attributable to intraday this FY". Click-to-copy ITR-3
  Schedule BP fields, documents checklist, cross-link to Tax Guide.
  Backed by versioned slabs in [`modes/dashboard/tax/slabs.py`](modes/dashboard/tax/slabs.py)
  — adding a future FY is a one-line config.
- **`/theory/<slug>`** — four reference docs rendered live from
  `docs/` with KaTeX math + dropdown nav: Statistical Analysis (with a
  theoretical-vs-live snapshot card on top), Trade Strategy reference,
  Strategy Evolution log, India Tax Guide.

Lives in its own [modes/dashboard/](modes/dashboard/) folder, isolated
from every mode's runtime. Touches no strategy/order code; reads only.

```
python main.py --mode dashboard                    # interactive (server + browser)
python main.py --mode dashboard --no-open          # static HTML snapshot
python main.py --mode dashboard --text             # legacy plain-text
python main.py --mode dashboard --port 8765        # fixed port
```

Full plan: [modes/dashboard/docs/DASHBOARD_ROADMAP.md](modes/dashboard/docs/DASHBOARD_ROADMAP.md)
(D1 + D1.1 + D13 + theory/tax pages done; D2–D12, D14, D15, D18–D29 pending,
including D24-D29 Portfolio-Analyser sub-module).

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
| [docs/TRADE_STRATEGY.md](docs/TRADE_STRATEGY.md) | Complete Trade strategy — NoAI + AI modes, 44-check pre-trade pipeline, all indicators/patterns, scoring, risk layers, glossary |
| [docs/TRADE_ROADMAP.md](docs/TRADE_ROADMAP.md) | Pending / Awaiting-Data / Removed / Completed items with priorities |
| [docs/TRADE_EVOLUTION.md](docs/TRADE_EVOLUTION.md) | Chronological one-line history of every shipped strategy item (auto-regenerated from the Roadmap) |
| [docs/TRADE_STATISTICS.md](docs/TRADE_STATISTICS.md) | Theoretical edge math + live snapshot. §2.5 holds the per-item ΔEV / ΔMDD verdict every shipped strategy item must carry. Rendered live at the dashboard's `/theory/statistics` page. |
| [docs/ANALYZE_STRATEGY.md](docs/ANALYZE_STRATEGY.md) | Complete Portfolio-Analyser reference — what every field on a stock card means, how rule-based actions are chosen, what the AI overlay adds, the report layout, the persistence schema |
| [docs/ANALYZE_ROADMAP.md](docs/ANALYZE_ROADMAP.md) | **P1-P9 shipped** — Portfolio-Analyser foundation: typed `StockAnalysis` with per-field `source`/`as_of`, NoAI + AI enrichment split, persistence DB, industry-standard metrics (HHI, Sharpe, vol, max-DD, CAGR, cash drag, AMFI mcap-tier breakdown), "what's missing" engine |
| [modes/dashboard/docs/DASHBOARD_ROADMAP.md](modes/dashboard/docs/DASHBOARD_ROADMAP.md) | **Tool-wide read-only surface** — D1/D1.1/D13/D16/D17 + **D24-D29 (Portfolio-Analyser pages: `/portfolio` + per-stock drill-down + on-demand "Analyse now" + `/login`) all shipped 2026-05-12** |
| [docs/IDEATIONS.md](docs/IDEATIONS.md) | Future money-engine ideation: A1 V3 AI intraday research, A2 delivery swing, A3 ETF rotation; cash-market only, no F&O, Phase 1 remains FYI-only |
| [docs/TRADE_TAX_GUIDE.md](docs/TRADE_TAX_GUIDE.md) | India intraday tax guide (FY 2026-27 ready) |

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
| `requests` | Programmatic Kite login (AUTO / ASSISTED modes — §5.4) |
| `pyotp` | Optional, only if you opt-in to AUTO login (§5.4) |

### 5.2 API keys

Create `.env` in the project root:

```env
# Required
ZERODHA_API_KEY=...
ZERODHA_API_SECRET=...
CLAUDE_API_KEY=...

# Optional — enable streamlined login (§5.4)
KITE_USER_ID=AB1234              # your Zerodha client id
KITE_PASSWORD=your_kite_password # web login password (NOT the API secret)

# Optional — only if you want fully unattended login (security trade-off!)
KITE_TOTP_SECRET=JBSWY3DPEHPK...  # base32 TOTP seed (§5.4)
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
| `MAX_BUDGET_INR` | 50,000 | Max capital deployed per day |
| `SCAN_UNIVERSE` | NIFTY100 | Stock pool (overridable per-run with `--nifty 50/100/150/200`) |
| `MAX_POSITIONS` | 3 | Simultaneous trades |
| `DRY_RUN` | False | Simulate without real orders (or use `--dryrun`) |
| `CLAUDE_PLAN` | pro | Claude tier: `free`, `pro`, `max` |
| `RR_TARGET_RATIO` | 1.5 | Base R:R from ATR |
| `RR_HARD_FLOOR` | 1.3 | Always-on R:R floor — uniform across the trading day (collapsed from the deprecated time-tiered floors by #243) |

---

### 5.4 Zerodha login modes

Kite access tokens expire daily at midnight, so the bot has to re-login
once a day. Four flows are supported — **the bot picks the most
automated one your `.env` allows, then falls back automatically**:

| Mode | Trigger | Human action per day | Security |
|---|---|---|---|
| **AUTO** | `KITE_USER_ID` + `KITE_PASSWORD` + `KITE_TOTP_SECRET` set | none | ⚠️ password **and** TOTP seed both on disk — effectively single-factor (§5.4.3) |
| **ASSISTED** | `KITE_USER_ID` + `KITE_PASSWORD` set, no seed | type the 6-digit code from your authenticator app or Kite mobile app | password on disk; TOTP stays on phone (§5.4.4) |
| **Browser (`b`)** | none of the above, you press `b` | log in via the browser tab the bot opens; redirect is auto-caught on `localhost:8080` | nothing on disk; native browser flow |
| **Manual (`m`)** | press `m` (default for SSH-only / VM setups) | open the printed URL on a phone/laptop, log in, paste the **full** redirect URL back into the terminal | nothing on disk; works headless |

The order of attempts is: cached token → AUTO/ASSISTED (if env permits) →
on failure or missing env, the legacy `b/m/q` prompt.

#### 5.4.1 ASSISTED setup (recommended for most users)

Add two lines to `.env`:

```env
KITE_USER_ID=AB1234
KITE_PASSWORD=your_kite_web_password
```

Next run will detect them, drive the login form itself, and prompt:

```
  Open your authenticator app (Apple Passwords / Authy / Google Auth)
  or read the 6-digit code from your Kite mobile app.

  Enter 6-digit code: ______
```

Works whether you have External TOTP enabled (code from Authy / Apple
Passwords / Google Auth) or not (PIN from the Kite mobile app login
screen). Total user input: 6 digits.

#### 5.4.2 AUTO setup (zero-touch — read the security note first)

AUTO needs the base32 **TOTP seed** that Zerodha shows once at
enrollment. If you used the QR-scan path (Apple Passwords, Authy etc.
scanning the QR directly), the seed is buried inside the QR image and
you have to re-enroll to see it as text:

1. Kite web → **Profile → Password & Security → Disable External TOTP**
   (asks for password + current TOTP).
2. **Enable External TOTP** again.
3. On the QR screen click **“Can’t scan? Copy the key”** — a long
   base32 string (letters A–Z + digits 2–7, no spaces) appears.
4. **Copy it into `.env`** as `KITE_TOTP_SECRET=...` immediately. You
   only see it once.
5. Then re-add the same secret to your phone authenticator (Apple
   Passwords / Authy support a “manual entry” option that takes the
   same string). This keeps your phone working as a backup.
6. Verify everything wired up:

   ```
   python main.py --mode login
   ```

   With all three env vars present the bot logs `Attempting Kite AUTO
   login (env-driven)…` and finishes without prompting.

#### 5.4.3 Security trade-offs of AUTO mode

> 🚨 **AUTO mode reduces 2FA to single-factor.** Anyone who can read
> `.env` (malware, stolen laptop, accidental git commit, OneDrive
> sync, screen share) can place trades on your account. The TOTP seed
> is **non-rotating** — a leak is silent until trades start happening.

Only opt-in if you also do at minimum:

- Confirm `.env` is gitignored: `git check-ignore -v .env` should print `.gitignore`.
- **BitLocker** on the laptop drive (Win Pro built-in, free).
- **Exclude the project folder from OneDrive / iCloud** (Settings →
  Choose folders).
- Restrict `.env` ACL to your Windows user only (Properties → Security
  → disable inheritance, remove all but your account).
- Keep your Zerodha bank-withdrawal whitelist set to **only your
  primary account** so a hijacker can’t move funds out cleanly.

If any of those feel like too much hassle, **stay on ASSISTED** —
you’ve given up almost nothing and kept real 2FA.

#### 5.4.4 Security trade-offs of ASSISTED mode

Mild: your Kite **password** sits in `.env` next to your existing
`ZERODHA_API_SECRET`. The TOTP factor still requires your phone, so a
`.env` leak alone cannot log in. Same minimum hygiene applies
(gitignore + BitLocker + no cloud sync).

For maximum hygiene store the password in Windows Credential Manager
via the `keyring` package instead of `.env` — a future enhancement.

---

## 6. Run modes

| Command | What it does |
|---------|--------------|
| `python main.py --mode analyze` | Phase 1 — long-term portfolio analyser, NoAI default (no Claude cost) |
| `python main.py --mode analyze --ai` | Phase 1 + Claude qualitative overlay (thesis/risks/news) |
| `python main.py --mode trade` | Phase 2 NoAI (default) |
| `python main.py --mode trade --ai` | Phase 2 with Claude |
| `python main.py --mode trade --noai` | Same as default; explicit |
| `python main.py --mode trade --dryrun` | Full strategy, no real orders |
| `python main.py --mode trade --test` | See pipeline only (no Claude, no trades, no cost) |
| `python main.py --mode trade --max 30000` | Cap today's capital at Rs.30,000 |
| `python main.py --mode trade --nifty 150` | Override scan universe |
| `python main.py --mode login` | Test Zerodha login only |
| `python main.py --mode dashboard` | Launch interactive profitability dashboard (local server + browser). `--no-open` writes a static HTML snapshot; `--text` prints plain text; `--port N` pins a port. See [modes/dashboard/docs/DASHBOARD_ROADMAP.md](modes/dashboard/docs/DASHBOARD_ROADMAP.md) |

**Ctrl+C** triggers graceful shutdown — squares off all positions first.
Phase 2 can be started any time (handles weekends / NSE holidays / late
starts / token expiry automatically).

---

## 7. Project structure

```
ai-portfolio-manager/
├── main.py                          # entry point
├── config.py                        # all settings
├── requirements.txt
├── .env                             # API keys (gitignored)
├── core/                            # shared infrastructure
│   ├── claude_client.py             # Claude wrapper + error classification
│   ├── zerodha_client.py            # Kite wrapper
│   └── logger.py                    # coloured terminal + rotating file log
├── shared/                          # cross-mode services
│   ├── candle_cache.py              # SQLite cache for candles
│   ├── candle_patterns.py           # 14 pure-math pattern detectors
│   ├── market_data.py               # Live prices + history enrichment
│   ├── technical_indicators.py      # Indicators + composite scoring
│   └── tax_db.py                    # tax-ledger DB helpers
├── modes/                           # one folder per CLI mode
│   ├── analyze/                     # `--mode analyze` (read-only long-term review)
│   │   ├── analyser.py              # 8-step orchestrator (NoAI default; --ai overlay)
│   │   ├── types.py                 # Field[T] + StockAnalysis + PortfolioMetrics + GapAnalysis + PortfolioSnapshot
│   │   ├── enrich_noai.py           # deterministic Zerodha + cache + reference-seed enrichment
│   │   ├── enrich_ai.py             # Claude qualitative overlay (only ai_* slots)
│   │   ├── recommendation_rules.py  # 7-branch deterministic action engine
│   │   ├── metrics.py               # HHI / top-5 / Sharpe / vol / max-DD / CAGR / cash drag / mcap tier
│   │   ├── gaps.py                  # what's-missing engine + suggested additions
│   │   ├── persistence.py           # data/portfolio_analyses.db (two tables, six read helpers)
│   │   └── report.py                # .txt + .json output (drops the legacy .tsv)
│   ├── trade/                       # `--mode trade` (default; --noai or --ai)
│   │   ├── manager.py               # day orchestrator (run / run_noai / run_test)
│   │   ├── stock_scanner.py         # candle + indicator scanner
│   │   ├── order_engine.py          # 44-check entry pipeline + monitoring
│   │   ├── performance_tracker.py   # SQLite trades + analyses
│   │   ├── report_writer.py         # txt + json reports
│   │   ├── analysis_queue.py        # per-stock Claude analysis (--ai)
│   │   ├── candidate_telemetry.py   # `intraday_candidates` writer
│   │   └── volume_baseline.py       # per-symbol intraday RVol baselines
│   └── dashboard/                   # `--mode dashboard` (read-only, tool-wide)
│       ├── cli.py                   # argparse entry
│       ├── server.py                # stdlib HTTP server SPA backend
│       ├── data_layer.py            # DB reads, sheet-verified filtering, FY window
│       ├── metrics.py               # headline P&L, cumulative series (intraday)
│       ├── budget_history.py        # per-day budget from trading_data_*.json
│       ├── verdict.py               # capital-ladder traffic-light engine
│       ├── render_html.py           # /trading Chart.js SPA shell
│       ├── render_text.py           # plain-text mode (--text)
│       ├── portfolio_page.py        # /portfolio + /portfolio/<symbol> + /login (D24-D29)
│       ├── portfolio_actions.py     # background "Analyse now" worker (D26/D27)
│       ├── theory_page.py           # /theory/<slug> renderer
│       ├── tax_page.py              # /tax FY-summary + projection
│       ├── tax/                     # FY tax sub-package (slabs, fy_summary)
│       └── docs/DASHBOARD_ROADMAP.md # D1+D1.1+D13+D16+D17+D24-D29 done; D2-D23 pending
├── scripts/
│   ├── trade/                       # trade-mode CLIs (see Section 9)
│   └── shared/                      # cross-mode CLIs (see Section 10)
├── docs/                            # see Section 3 doc map
├── data/                            # gitignored (trades.db, tokens, etc.)
├── reports/                         # generated; gitignored
└── logs/                            # rotating logs; gitignored
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
# Optional but recommended on a VM — enables ASSISTED login so you only
# type a 6-digit code once a day (vs pasting the full redirect URL).
KITE_USER_ID=AB1234
KITE_PASSWORD=your_kite_web_password
# Optional — fully unattended login (read §5.4.3 first; on a VM the
# .env risk is similar but the blast radius is the VM, not your laptop).
# KITE_TOTP_SECRET=JBSWY3DPEHPK...
EOF
chmod 600 .env                      # restrict to your VM user
python scripts/shared/backup_data.py --ssh   # pull data from your private backup repo
python main.py --mode login           # picks ASSISTED if KITE_USER_ID+PASSWORD set,
                                       # else falls back to manual mode (option 'm')
```

### Daily operation

One-command bring-up (recommended) — the script `cd`s into the repo,
activates the venv, runs `git pull`, pulls the latest data from the
backup repo (`--all-remote`, auto-confirmed), and starts
`--mode trade --noai --max 50000`. From your VM home directory:

```bash
ssh azureuser@<vm-ip>
tmux new -s bot                              # detach with Ctrl+B, D
./ai-portfolio-manager/scripts/trade/start_trade_vm.sh
# overrides: ./ai-portfolio-manager/scripts/trade/start_trade_vm.sh --ai --max 30000
```

Step-by-step (if you want to see each phase):

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
python scripts/shared/backup_data.py --ssh
```

> **Tokens are IP-specific.** Delete `data/access_token.json` when
> switching machines so the bot prompts for fresh login.

---

## 9. Reports & data

### Report layout

- Phase 1 → `reports/modes/trade/<year>/<month>/portfolio_report_DD.txt` + `.json`.
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
| `scripts/trade/view_trades.py` | All intraday trades with P&L summary |
| `scripts/trade/view_performance.py` | Daily P&L, win rate, exit stats, indicator correlation |
| `scripts/shared/view_analyses.py` | Phase 1 analyses with action status |
| `scripts/shared/generate_sheet.py` | TSV spreadsheet from a portfolio report (1 Claude call) |
| `scripts/shared/view_candle_cache.py` | Inspect candle cache contents |
| `scripts/trade/verify_trades.py` | EOD trade verification vs Zerodha API |
| `scripts/trade/rejection_audit.py --append-report` | Verdict on every skipped entry |
| `scripts/trade/exit_coverage_check.py` | Truth-table guard — fails if any thesis-broken in-loss `(entry, fresh, pattern)` cell is uncovered by both `_signal_reversal_exit` and `_signal_decay_exit`. Run as part of the smoke triple after any exit-pipeline change to catch cross-gate dead zones (the 2026-04-28 sign-flip class) before they ship. |
| `scripts/trade/strategy_stability_check.py [--lookback N] [--window-days N]` | Reads `git log` and reports (a) currently-open 10-trading-day no-tune windows opened by recent strategy commits, (b) any tuning commit that landed inside another commit's window without an exempt token. Informational only — never blocks a commit or push. Roadmap #245. **Bug-fix commits that touch tracked strategy files MUST include `bugfix-during-stability-window` in the subject** so the script doesn't spuriously open a fresh window; `#NNNR` removal commits use `removal-trigger-fired`. See `copilot/review-cycle.md` Wrap-up table for the full classification rules. |
| `scripts/trade/view_candidates.py [--date YYYY-MM-DD] [--since YYYY-MM-DD] [--symbol STK] [--side BUY/SELL] [--status STATUS] [--summary] [--hash]` | Read-only viewer for the `intraday_candidates` telemetry table (Roadmap #259). Filters by date / symbol / side / status (`SCORED`, `ENTERED`, `REJECTED`); `--summary` totals; `--hash` lists distinct config hashes seen in the window. |
| `scripts/trade/build_volume_baseline.py [--lookback N] [--universe UNIV] [--symbol STK] [--dry-run]` | Rebuilds `data/volume_baseline.db` from the trailing N trading days of 15-min candles in `data/candle_cache.db` (Roadmap #260). Computes per-symbol, per-hour mean cumulative-volume share. After build, set `Config.INTRADAY_VOLUME_BASELINE_ENABLED = True` to switch the scanner's RVol denominator from linear pro-rating to baseline-aware. |
| `scripts/trade/backtest.py --from YYYY-MM-DD --to YYYY-MM-DD [--symbol STK] [--min-score N] [--max-trades-per-day N]` | Offline replay harness (Roadmap #24). Walks 15-min cached candles, applies a simplified directional score (EMA-cross + RSI + 1h momentum), and simulates synthetic trades using ATR-derived SL / target geometry and `Config.SQUARE_OFF_*`. Output: per-trade JSON in `reports/backtest/` plus a stdout summary (WR / PF / expectancy / max-DD), each row stamped with `Config.snapshot_hash()` for replay-vs-live comparison. **Do not read absolute P&L as a forecast** — see the script docstring "Scoring fidelity" note. |
| `scripts/trade/promotion_check.py [--window N] [--json]` | Codified PASS / FAIL gate for capital scale-ups. Reads the last N (default 20) trading sessions from `data/trades.db` and tests profit factor, expectancy, day-WR, trade-WR and max-drawdown against fixed thresholds. Exit codes: `0` = PASS, `1` = FAIL, `2` = INSUFFICIENT_DATA. Run BEFORE any major risk-knob relax or capital scale; the script is the single source of truth on whether the live edge is positive enough to justify the change. |

All scripts support `--help`.

### Data sync (private repo)

`data/`, `reports/`, `logs/` are personal — keep them in a **separate
private repo** so they're portable across machines. The default sync
is a glob walk of those three folders so any new file (e.g. a new
DB, a new report subfolder) is picked up automatically with no code
change.

**Synced as of 2026-05-11 (everything in these locations):**
- `data/trades.db` — trades, intraday_tax_ledger, capital_gains_ledger, **`intraday_candidates`** (Roadmap #259, full SCORED → ENTERED/REJECTED → OUTCOME chain stamped with `Config.snapshot_hash()`)
- `data/intraday_tax.db`, `data/tax.db` — tax DBs
- **`data/volume_baseline.db`** (Roadmap #260) — per-(symbol, hour) cumulative volume share, built by `scripts/trade/build_volume_baseline.py`
- `data/zerodha_authoritative_*.json` — quarterly Zerodha truth snapshots
- `data/candle_cache.db` — git-tracked alongside the code repo (already identical across machines, NOT in the data backup)
- `reports/dashboard/`, `reports/modes/trade/`, `reports/trading/`, **`reports/backtest/`** (Roadmap #24, per-trade JSON stamped with `Config.snapshot_hash()` so two machines with the same config produce comparable runs)
- `logs/portfolio.log*`

**Never synced (operator secrets / local-only):** `data/access_token.json`, `data/access_token.json.bak`, `data/ZerodhaTaxPL/`, `__pycache__/`.

```bash
python scripts/shared/backup_data.py            # two-way append-merge + push (HTTPS)
python scripts/shared/backup_data.py --ssh      # SSH (Linux VMs)
python scripts/shared/backup_data.py --dry-run  # preview, no writes

# Manual-fix flow (you edited a row/report on this machine — make it the truth)
python scripts/shared/backup_data.py --prefer local    # local wins, edits propagate via UPSERT
python scripts/shared/backup_data.py --prefer remote   # remote wins (rare — adopt VM's version)

# Nuclear reset (also DELETES files not on the chosen side; prompts y/n)
python scripts/shared/backup_data.py --all-local       # full overwrite of remote
python scripts/shared/backup_data.py --all-remote      # full overwrite of local
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
   - VM: `python scripts/shared/backup_data.py --ssh` after market close.
   - Dev machine: `python scripts/shared/backup_data.py` next morning.
   - DBs append-merge cleanly because both sides only added new rows.

2. **Manual data fix on coding machine → VM** (use `--prefer local`):
   - Edit a DB row or report .txt to correct bad data.
   - `python scripts/shared/backup_data.py --prefer local` — your edits become the truth.
   - VM picks up corrections on its next pull.

  Important: row-level sync does not delete remote-only ghost DB rows yet. If a repair deliberately removes rows from `trades.db`, use the new deletion-aware path:

  ```powershell
  python scripts/shared/backup_data.py --canonical-trades --dry-run   # shows local sha256 + remote sha256 + per-table row deltas, no writes
  python scripts/shared/backup_data.py --canonical-trades             # backs up the remote DB to a timestamped file then bit-for-bit replaces it with the local DB
  ```

  This propagates row deletions correctly (Roadmap #270). Use the dry-run first whenever you’re about to overwrite the remote DB so you see exactly which tables differ. The legacy nuclear `--all-local` still works but copies *all* files; `--canonical-trades` is the surgical option for canonical DBs only. As of 2026-05-11 the canonical set is `data/trades.db` + `data/volume_baseline.db` — both will be diffed and replaced together in a single pass when you use the flag, with one timestamped backup per file.

**Bringing up a new machine** (clean checkout):

1. Clone this repo, set up the venv, fill in `.env` (Zerodha + optional `KITE_TOTP_SECRET` for unattended login).
2. `python scripts/shared/backup_data.py --ssh` (or HTTPS) — pulls the data repo into `../ai-portfolio-manager-data` and merges into local `data/`, `reports/`, `logs/`. The new machine now has the full trade ledger, tax ledger, telemetry rows, and any backtest runs another machine produced.
3. `python -c "from config import Config; print(Config.snapshot_hash())"` — confirm the same `(version, hash)` pair on both machines. Different hashes mean a config knob differs in `config.py` and any backtest comparison is invalid until reconciled.
4. Optional: `python scripts/trade/build_volume_baseline.py --dry-run` to confirm the baseline DB on the new machine; the file syncs in step 2 but the builder is fully reproducible from `data/candle_cache.db` (which is in the code repo, identical across machines), so a rebuild produces an identical DB.
5. Optional: `python scripts/trade/promotion_check.py` — read-only, confirms the new machine sees the same PASS/FAIL state as the old one (proves the trade ledger merged correctly).

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

Full guide: **[docs/TRADE_TAX_GUIDE.md](docs/TRADE_TAX_GUIDE.md)** (slabs, advance
tax dates, loss carry-forward).

| Script | Purpose |
|--------|---------|
| `scripts/trade/fill_intraday_ledger.py` | Build intraday ledger from trade JSONs (auto-runs EOD) |
| `scripts/trade/verify_trades.py` | Verify trades vs Zerodha; correct prices in reports + ledger + DB |
| `scripts/shared/import_zerodha_taxpnl.py [--fy YYYY]` | Import Zerodha Tax P&L xlsx (intraday + capital gains) |
| `scripts/shared/tax_summary.py [--intraday] [--fy YYYY]` | Combined tax summary — speculative + STCG + LTCG + estimated tax |
| `scripts/trade/view_intraday_ledger.py [--fy YYYY] [--list]` | Intraday ledger view |
| `scripts/shared/view_capital_gains_ledger.py [--list]` | Capital gains ledger view |

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
- **Pattern↔tech contradiction penalty (#200)** — at the scanner combine, subtracts 2.0 from `|combined_score|` when patterns include an opposite-side reversal (e.g. BUY candidate showing `BEARISH_ENGULFING`) and 0.5 when patterns include `DOJI` indecision; weak-conviction conflict setups fall below `MIN_SCORE` naturally.
- **VWAP statistical-band gate (#201)** — blocks BUY at the upper 1σ/2σ VWAP band and SELL at the lower 1σ/2σ; complements the existing % VWAP-extension check with a volatility-adaptive band classifier. Override at `|score| ≥ 7.0`.
- **Late-entry tightening (#202, retuned by #239, coupled by #246)** — after 10:00 IST: `MIN_SCORE` bumped by +1.0 (raised 0.5 → 1.0 by #239 after first live day showed +0.5 was too gentle), then clamped to `>= SIGNAL_DECAY_MIN_ENTRY_SCORE = 7.0` by #246 so the entry floor is never below the rescue-gate floor (no-rescue-zone alignment, motivated by JIOFIN 2026-04-28). R:R floor and concurrency are owned by always-on `RR_HARD_FLOOR` + `dynamic_max_positions(budget)` (simplified by #225).
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

Full risk architecture: **[docs/TRADE_STRATEGY.md](docs/TRADE_STRATEGY.md#risk-management--entry-pre-checks)**.

---

## 12. Disclaimer

This software is for educational and experimental purposes. Stock market
trading involves substantial risk of loss. Past performance (including
dry-run results) does not guarantee future results. Use at your own
risk. The authors are not responsible for any financial losses incurred
from using this software.
