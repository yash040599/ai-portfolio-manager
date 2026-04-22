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
  1. What it does       — 2 modes (Phase 1, Phase 2). One paragraph + bullets.
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
   SL/target with min-distance floor, **34-check pre-trade pipeline** (see
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
| [docs/STRATEGY_V2.md](docs/STRATEGY_V2.md) | Complete V2 strategy — NoAI + AI modes, 34-check pre-trade pipeline, all indicators/patterns, scoring, risk layers, glossary |
| [docs/STRATEGY_V1.md](docs/STRATEGY_V1.md) | V1 architecture (deprecated, frozen) |
| [docs/STRATEGY_ROADMAP.md](docs/STRATEGY_ROADMAP.md) | Pending / Awaiting-Data / Removed / Completed items with priorities |
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
| `RR_FLOOR_MORNING/AFTERNOON/LATE` | 1.3 / 1.2 / 1.0 | R:R floors by session window |

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
│   ├── order_engine.py           # 34-check entry pipeline + monitoring
│   ├── report_writer.py          # txt + json reports
│   └── performance_tracker.py    # SQLite trades + analyses
├── scripts/                      # see Sections 9 + 10 for tables
├── docs/                         # see Section 3 doc map
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
python scripts/backup_data.py            # two-way sync + push (HTTPS)
python scripts/backup_data.py --ssh      # SSH (Linux VMs)
python scripts/backup_data.py --dry-run  # preview
python scripts/backup_data.py --all-local   # full push
python scripts/backup_data.py --all-remote  # full pull
python scripts/backup_data.py --overwrite-db  # one-direction DB overwrite
```

| Scenario | Action |
|----------|--------|
| File only one side | Copied across |
| File both sides, identical | Skipped |
| `.db` in both, different | **Merged row-by-row** (no deletes) |
| `.db` with `--overwrite-db` | One-direction overwrite (asks `l/r`) |
| Other file in both, different | Asks `l/r` |

> The data repo MUST be **Private**. The main code repo has no link to
> it — only the sync script knows the URL.

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
- **Whipsaw guard** — pauses entries after 3 consecutive SL hits.
- **Per-symbol re-entry cooldown** — 30 min on same `SYMBOL_SIDE`.
- **Lunch-lull skip** — 11:30-12:15 IST unless `|score| ≥ 6.0`.
- **Charge-aware target** — gross target ≥ 2× round-trip charges.
- **Budget-regime gates** — auto-tighten on TINY/SMALL accounts.
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
# AI Portfolio Manager

An automated intraday trading bot for the Indian stock market (NSE) that uses **technical indicators + candlestick patterns** for stock selection and **Zerodha Kite** for market data and order execution. Optionally uses **Claude AI** for selection and reviews via `--ai` flag.

> **⚠️ V1 is DEPRECATED and FROZEN (April 2026).** Do not modify V1-specific
> code (`portfolio/manager.py`'s `PortfolioManager` base class methods,
> `services/stock_scanner.py`, `docs/STRATEGY_V1.md`). Shared components
> (`order_engine.py`, `market_data.py`, etc.) may evolve for V2/NoAI — V1
> inherits those changes passively. All new work targets V2 or V2 NoAI.

## What it does

### Phase 1 — Portfolio Analysis (read-only)

Logs into Zerodha, analyses your existing demat holdings using Claude AI, and generates a detailed report with action recommendations (HOLD, BUY MORE, EXIT, etc.) for each stock. Claude receives the full analysis history across all past runs, tracks pending actions, and performs a portfolio-wide assessment with new stock recommendations.

```bash
python main.py --mode analyze
```

### Phase 2 — Intraday Trading Bot (V2 — Default)

A fully automated intraday trading bot. Default mode is **NoAI** (pure technical signals, zero Claude calls). The core loop:

1. **Pre-market scan** — fetches candles for every stock in `SCAN_UNIVERSE`, applies price range filter (`SCAN_MIN_PRICE` / `SCAN_MAX_PRICE`), runs candlestick pattern detectors + technical indicators (EMA, RSI, VWAP, SuperTrend, MACD, Fibonacci, VWAP Bands, ADX, StochRSI, and more), applies sector momentum filter, computes score momentum (Δ from previous scan), auto-selects best candidates by score
2. **Execution** — enters positions using LIMIT orders at LTP + 1 tick buffer (tick size per instrument from Zerodha — Rs.0.05 or Rs.0.50) with full-timeout polling and MARKET fallback (configurable), ATR-based dynamic stop-losses (pure ATR, no merge with config defaults) with a min-distance floor (0.8% normal, 1.0% expiry) so high-priced stocks don't get wicked out on normal noise, validates entry prices against live Zerodha quotes, checks bid-ask spreads and order-book impact cost (top-5 level walk, skips paper-thin books), volume confirmation (RVol gate with scan-time fallback), time-based R:R floor (morning 1.3:1, afternoon 1.2:1, late 1.0:1 — relaxes after failed scans, gives up after repeated failures), multi-gate pre-entry pipeline (RSI symmetric block, VWAP trend + extension + fresh-reversal guards, ADX + DI directional gate with high-conviction override, gap-coherence guard on `GAP_*_STRONG` tape, circuit-limit guard near ±20% freeze, daily trade cap, direction-aware declining re-entry block, stagnant-churn guard, per-symbol re-entry cooldown, lunch-lull skip, daily-loss soft-stop + intraday equity-peak drawdown stop, net-of-charges R:R, charge-aware target multiple), ATR-based position sizing (risk-budget cap), and tries fallback candidates if primary picks fail entry checks
3. **Monitoring** — polls prices with adaptive frequency, auto-trails SL, takes partial profits, runs **two-tier stagnant exit** (45-min directional + 90-min progress-to-target, +15 min during the 12:00-1:30 midday lull), and **three layered score-driven exits** on the free 15-min candle re-scan (signal-reversal hard exit on opposite-side score flip + confirming candle; signal-decay book-and-go below 1R when same-side conviction collapses; auto-protect SL-tighten on weaker contrary signals)
4. **Risk management** — circuit breaker on daily loss (3% hard) + soft-stop hysteresis (1.5% blocks new entries), whipsaw guard, sector caps, regime-shift protection, India VIX monitoring, crash recovery, manual trade adoption with 10-min grace window (skips time-decay + loser-exit while user-opened positions settle), Thursday expiry adjustments (30-min entry delay, tighter trade cap, wider SL floor), and **dynamic budget-regime gates** (TINY/SMALL/NORMAL/LARGE account tiers automatically tighten ADX threshold, trade cap, and min-score on smaller accounts)
5. **EOD** — squares off all positions, generates P&L report with full tax breakdown, auto-verifies trades against Zerodha API, and runs the post-trade rejection audit (verdict on every skipped entry)

With `--ai` flag, Claude AI handles stock selection from pre-filtered candidates and periodic position reviews.

All thresholds, timing, and limits are configurable in `config.py`. For the complete feature list and scoring system, see **[docs/STRATEGY_V2.md](docs/STRATEGY_V2.md)**.

```bash
python main.py --mode trade                  # NoAI (default)
python main.py --mode trade --ai             # with Claude AI
```

### Other Modes

```bash
# Dry run — full strategy, no real orders (simulated slippage)
python main.py --mode trade --dryrun

# Test — see the analysis pipeline (no Claude, no trades, no cost)
python main.py --mode trade --test

# Claude AI — use Claude for stock selection + position reviews
python main.py --mode trade --ai
python main.py --mode trade --ai --dryrun
python main.py --mode trade --ai --test

# NoAI — explicitly request no-AI mode (same as default)
python main.py --mode trade --noai

# Budget cap — limit today's capital to Rs.30,000
python main.py --mode trade --max 30000

# Stock universe override — pick NIFTY 50 / 100 / 150 / 200 for the run
python main.py --mode trade --nifty 150
python main.py --mode trade --ai --nifty 100

# V1 legacy (retired — sends raw prices to Claude)
python main.py --mode trade --v1

# Test Zerodha login only
python main.py --mode login
```

NoAI mode uses the same candle pipeline for everything — stock selection, monitoring, and re-scans — with zero API costs. See **[docs/STRATEGY_V2.md](docs/STRATEGY_V2.md)** for the complete strategy reference.

### Historical Data Caching

Previous days' candle data is cached in `data/candle_cache.db` (SQLite) to avoid redundant Zerodha API calls. Features auto-cleanup, weekend/holiday-aware lookback, corporate action detection, and API rate limiting. The cache is committed to Git (pure market data). Run `--test` the evening before to pre-warm it.

### Documentation

| Doc | What it covers |
|-----|---------------|
| **[docs/STRATEGY_V2.md](docs/STRATEGY_V2.md)** | Complete V2 strategy reference — NoAI (default) + Claude AI modes, all indicators, patterns, scoring, risk layers |
| **[docs/STRATEGY_V1.md](docs/STRATEGY_V1.md)** | V1 strategy architecture (deprecated — frozen, no new changes) |
| **[docs/STRATEGY_ROADMAP.md](docs/STRATEGY_ROADMAP.md)** | Strategy improvement roadmap with research-backed enhancements |
| **[docs/TAX_GUIDE.md](docs/TAX_GUIDE.md)** | Comprehensive intraday trading tax guide for India |

---

## Prerequisites

- **Python 3.10+** (uses modern type syntax)
- **Windows/Linux/Mac** — works on headless servers too (Zerodha login supports manual/paste mode for SSH-only VMs)
- A **Zerodha trading account** with Kite Connect API access
- A **Claude API key** from Anthropic (only needed for `--ai` mode and `--mode analyze`)

---

## Setup Guide

### Step 1: Clone or unzip the code

```bash
cd ai-portfolio-manager
```

### Step 2: Install Python dependencies

```bash
pip install -r requirements.txt
```

This installs:
| Package | Purpose |
|---------|---------|
| `anthropic` | Claude AI API client |
| `kiteconnect` | Zerodha Kite trading API client (≥5.1.0 required for `market_protection` support) |
| `python-dotenv` | Loads API keys from `.env` file |
| `openpyxl` | Read Zerodha Tax P&L xlsx reports |

### Step 3: Get your API keys

You need 3 keys. Follow the guides below to get them.

---

#### 🔑 Zerodha Kite Connect API Keys

You need: `ZERODHA_API_KEY` and `ZERODHA_API_SECRET`

1. **Create a Zerodha account** (if you don't have one)
   - Sign up at [https://zerodha.com/open-account](https://zerodha.com/open-account)
   - Complete KYC and fund your account

2. **Subscribe to Kite Connect**
   - Go to [https://developers.kite.trade](https://developers.kite.trade)
   - Log in with your Zerodha credentials
   - Subscribe to the **Kite Connect** plan (Rs.500/month)
   - This gives you API access for live prices, historical data, and order placement

3. **Create an app**
   - After subscribing, click **"Create new app"**
   - App name: anything (e.g., "AI Portfolio Manager")
   - Redirect URL: `http://localhost:8080` ← **this is important, must be exactly this**
   - App type: select **"default"**
   - Click **Create**

4. **Copy your keys**
   - On the app details page, you'll see:
     - **API Key** → this is your `ZERODHA_API_KEY`
     - **API Secret** → this is your `ZERODHA_API_SECRET`
   - Keep these safe — don't share them

5. **Whitelist your IP address** (mandatory from 1 April 2026)
   - SEBI now requires all API-based trading to originate from a whitelisted IP address
   - On the [Kite Connect developer console](https://developers.kite.trade), open your app
   - Scroll to the **"Whitelisted IPs"** field and add the **public IP** of every machine that will run the bot
   - For a cloud VM (e.g. Azure Ubuntu), use the VM's **static public IP** — a dynamic IP will break after reboot
   - For a home machine, add your ISP-assigned public IP (check with `curl ifconfig.me`)
   - You can add multiple IPs (comma-separated) — e.g. one for your VM and one for your laptop
   - Click **Save** — changes take effect immediately
   - If you get `403 Forbidden` or `IP not whitelisted` errors from Kite, your current IP isn't in this list

> **Note:** Zerodha access tokens expire daily at midnight. The bot handles re-login automatically. On first run each day, you choose: open a browser on this machine, or **manual/headless mode** (copy the login URL, open it on your phone/laptop, paste the redirect URL back). Manual mode works on SSH-only VMs with no desktop.

---

#### 🔑 Claude API Key (Anthropic)

You need: `CLAUDE_API_KEY`

1. **Create an Anthropic account**
   - Go to [https://console.anthropic.com](https://console.anthropic.com)
   - Sign up with your email

2. **Add billing**
   - Go to **Settings → Billing** in the console
   - Add a payment method (credit/debit card)
   - Add credits (Rs.500–1000 is enough to start — the bot uses ~Rs.50-100/day on the Pro plan)

3. **Generate an API key**
   - Go to **Settings → API Keys**
   - Click **"Create Key"**
   - Name it anything (e.g., "portfolio-bot")
   - Copy the key immediately — it's shown only once
   - This is your `CLAUDE_API_KEY`

> **Pricing reference:** The bot uses Claude Sonnet (Pro plan). Each API call costs roughly Rs.2-4. A typical trading day makes ~15 calls = ~Rs.50-100/day.

---

### Step 4: Create your `.env` file

Create a file named `.env` in the project root (same folder as `main.py`):

```env
ZERODHA_API_KEY=your_zerodha_api_key_here
ZERODHA_API_SECRET=your_zerodha_api_secret_here
CLAUDE_API_KEY=your_claude_api_key_here
```

Replace the placeholder values with your actual keys from Step 3.

> ⚠️ **Never commit this file to Git.** The `.gitignore` is already configured to exclude it.

### Step 5: Configure your preferences

Open `config.py` and review the key settings. Everything is commented — the main ones:

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `MAX_BUDGET_INR` | `20,000` | Maximum capital the bot can deploy per day |
| `SCAN_UNIVERSE` | `NIFTY100` | Stock pool: NIFTY50, NIFTY100, NIFTY150, NIFTY200, or CUSTOM (overridable per-run via `--nifty 50\|100\|150\|200`) |
| `MAX_POSITIONS` | `3` | Max simultaneous trades |
| `DRY_RUN` | `False` | Simulate trades without placing real orders (use `--dryrun` flag) |
| `CLAUDE_PLAN` | `pro` | Claude model tier: free, pro, or max |
| `RR_TARGET_RATIO` | `1.5` | Base risk:reward ratio from ATR (1.5:1) |
| `RR_FLOOR_MORNING` | `1.3` | R:R floor before 1 PM — strict |
| `RR_FLOOR_AFTERNOON` | `1.2` | R:R floor 1-2 PM (after target compression) |
| `RR_FLOOR_LATE` | `1.0` | R:R floor after 2 PM — safety net |

All timing, risk management, indicator, and tax settings are in `config.py` with detailed comments.

### Step 6: Run

```bash
# Analyse existing portfolio
python main.py --mode analyze

# Intraday trading — V2 candle strategy (default)
python main.py --mode trade

# Dry run — full strategy, no real orders
python main.py --mode trade --dryrun

# Test — see strategy analysis pipeline (no Claude, no trades, no cost)
python main.py --mode trade --test

# NoAI — fully automated, no Claude calls
python main.py --mode trade --noai

# NoAI test — see NoAI selection pipeline
python main.py --mode trade --noai --test

# V1 legacy (retired)
python main.py --mode trade --v1

# Test Zerodha login only
python main.py --mode login
```

You can start Phase 2 anytime — even the night before. It handles weekends, NSE holidays, late starts, and token expiry automatically. Press **Ctrl+C** to gracefully shut down (squares off all positions first).

---

## Project Structure

```
ai-portfolio-manager/
├── main.py                  # Entry point — routes to Phase 1 or Phase 2
├── config.py                # All settings in one place (plans, budget, timing, costs)
├── requirements.txt         # Python dependencies
├── .env                     # Your API keys (not in Git)
├── .gitignore               # Keeps secrets and junk out of Git
├── core/
│   ├── claude_client.py     # Claude API wrapper + error classification
│   ├── zerodha_client.py    # Zerodha Kite API wrapper (login, quotes, orders, account snapshot)
│   └── logger.py            # Coloured terminal output + rotating log file
├── portfolio/
│   ├── analyser.py          # Phase 1 orchestrator (read-only analysis)
│   ├── manager.py           # Phase 2 orchestrator — V1 intraday trading loop
│   └── manager_v2.py        # Phase 2 orchestrator — V2 candle strategy (extends V1)
├── services/
│   ├── analysis_queue.py    # Per-stock Claude analysis with retry logic
│   ├── market_data.py       # Enriches portfolio with live prices + history
│   ├── stock_scanner.py     # V1 pre-market Claude scan + mid-day review
│   ├── stock_scanner_v2.py  # V2 candle pre-filter + enriched Claude scan (extends V1)
│   ├── candle_patterns.py   # 14 candlestick pattern detectors (pure math, no dependencies)
│   ├── candle_cache.py      # SQLite cache for historical candle data (avoids redundant API calls)
│   ├── technical_indicators.py # 14 technical indicators (EMA, RSI, VWAP, SuperTrend, MACD, ORB, Gap, ADX, Fibonacci, VWAP Bands + 4 more) + composite scoring
│   ├── order_engine.py      # Order execution, position tracking, SL/target monitoring, P&L + taxes
│   ├── report_writer.py     # Generates .txt reports and .json data dumps
│   └── performance_tracker.py # SQLite database for trade history + portfolio analysis tracking
├── scripts/
│   ├── generate_sheet.py           # Generate TSV spreadsheet from portfolio report (Claude-powered)
│   ├── tax_db.py                   # Shared DB helpers for all tax scripts (migration, FY utils)
│   ├── fill_intraday_ledger.py     # Fill intraday_tax_ledger from live JSONs (auto-runs after each trade day)
│   ├── verify_trades.py            # Same-day trade verification via Zerodha API (no xlsx needed)
│   ├── rejection_audit.py           # Post-trade audit — verdicts every skipped entry vs 15:30 close (auto-runs at EOD)
│   ├── import_zerodha_taxpnl.py    # Import Zerodha Tax P&L xlsx — verify intraday + import capital gains
│   ├── view_intraday_ledger.py     # View intraday trades with verified/unverified status
│   ├── view_capital_gains_ledger.py # View capital gains trades (short-term / long-term)
│   ├── tax_summary.py              # Tax summary — intraday, capital gains, or both (with grand total)
│   ├── view_trades.py              # View all intraday trades from database with P&L summary
│   ├── view_analyses.py            # View all portfolio analyses from database with action status
│   ├── view_candle_cache.py        # View candle cache — symbols, intervals, date ranges, OHLCV data
│   ├── import_reports_to_db.py     # Import existing JSON report files into the SQLite database
│   └── backup_data.py              # Two-way sync data (DB, reports, logs) with a private Git repo
├── docs/
│   ├── TAX_GUIDE.md         # Comprehensive intraday trading tax guide for India
│   ├── STRATEGY_V1.md       # V1 trading strategy — architecture, flow, risk layers
│   ├── STRATEGY_V2.md       # Complete V2 strategy reference — NoAI + Claude AI modes
│   └── STRATEGY_ROADMAP.md  # Strategy improvement roadmap — research-backed enhancements
├── data/
│   ├── trades.db            # SQLite database (auto-created on first run)
│   ├── candle_cache.db      # Candle cache (git-committed — pure market data)
│   └── access_token.json    # Zerodha session token (auto-created on login)
├── reports/                 # Generated reports, organised by type → year → month
│   ├── portfolio/           # Phase 1 portfolio analysis reports
│   │   └── <year>/
│   │       └── <month>/
│   │           ├── portfolio_report_DD.txt
│   │           ├── portfolio_data_DD.json
│   │           └── portfolio_sheet_DD.tsv
│   └── trading/             # Phase 2 intraday trading reports
│       └── <year>/
│           └── <month>/
│               ├── trading_report_DD.txt
│               └── trading_data_DD.json
└── logs/                    # Rotating log files (portfolio.log)
```

---

## Running on a VM (Azure Ubuntu)

Run the bot 24/7 on a headless Ubuntu VM. Zerodha login uses manual mode — paste the redirect URL from your phone/laptop via SSH.

> **Why a VM?** From 1 April 2026, SEBI requires API-based trading to originate from a whitelisted IP. A cloud VM with a **static public IP** gives a fixed address you can whitelist once on the [Kite developer console](https://developers.kite.trade) and never worry about again. Azure's free tier (B1s, 1 month) covers this at zero cost to start.

### One-time setup

```bash
# 1. SSH into your VM
ssh your-user@your-vm-ip

# 2. Install Python 3.10+ and pip
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git
```

### Authenticate with GitHub

GitHub doesn't support password auth. Use one of these:

**Option A — GitHub CLI (simplest):**
```bash
sudo apt install -y gh
gh auth login
# Choose: GitHub.com → HTTPS → Paste an authentication token
```

**Option B — Personal Access Token:**
1. On GitHub: Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate a token with `repo` scope
3. Clone using the token:
   ```bash
   git clone https://YOUR_USERNAME:YOUR_TOKEN@github.com/your-username/ai-portfolio-manager.git
   ```

**Option C — SSH key (most permanent):**
```bash
ssh-keygen -t ed25519 -C "azure-vm"
cat ~/.ssh/id_ed25519.pub
# Copy the output → GitHub: Settings → SSH and GPG keys → New SSH key
git clone git@github.com:your-username/ai-portfolio-manager.git
```

### Install and run

```bash
# 3. Clone the code repo (if not done above)
git clone https://github.com/your-username/ai-portfolio-manager.git
cd ai-portfolio-manager

# 4. Create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 5. Create your .env file with API keys
cat > .env << 'EOF'
ZERODHA_API_KEY=your_key_here
ZERODHA_API_SECRET=your_secret_here
CLAUDE_API_KEY=your_key_here
EOF

# 6. Sync data (reports, DB, logs) from the backup repo
#    --ssh uses SSH key auth (required on Linux VMs)
python scripts/backup_data.py --ssh

# 7. Test Zerodha login (use manual mode — option 'm')
python main.py --mode login
```

### Daily operation (SSH in → run)

Every time you SSH into the VM (including after a VM restart), you need to:

1. **Activate the virtual environment** — the venv doesn't persist across SSH sessions
2. **Navigate to the project directory**
3. **Start the bot inside tmux** so it keeps running after you disconnect

```bash
# 1. SSH into the VM
ssh azureuser@<your-vm-ip>

# 2. Go to the project and activate venv
cd ai-portfolio-manager
source venv/bin/activate      # prompt changes to (venv)

# 3. Start a tmux session and run the bot
tmux new -s bot
python main.py --mode trade   # or --mode analyze
```

When Zerodha login is needed, the bot prompts with a URL. Open it on your phone/laptop, log in, then paste the redirect URL back into SSH. After that you can detach (`Ctrl+B, D`) and disconnect.

#### Reconnecting to a running session

If the bot is already running inside tmux (you just disconnected SSH earlier):

```bash
ssh azureuser@<your-vm-ip>
cd ai-portfolio-manager
source venv/bin/activate
tmux attach -t bot            # re-attach to the existing session
```

> If the VM **restarted** (or tmux was killed), `tmux attach` will fail with "no sessions". In that case start a new session with `tmux new -s bot` and re-run the bot.

#### After the trading day

```bash
# Sync data back to GitHub
python scripts/backup_data.py --ssh
```

> **Tip:** Zerodha access tokens are IP-specific. Delete `data/access_token.json` if you switch between local and VM — the bot will prompt for a fresh login.

---

## Reports

Reports are organised by type, year, and month inside `reports/`:

- **Phase 1:** `reports/portfolio/<year>/<month>/portfolio_report_DD.txt` + `portfolio_data_DD.json`
- **Phase 2:** `reports/trading/<year>/<month>/trading_report_DD.txt` + `trading_data_DD.json`

Running Phase 2 multiple times on the same day merges all sessions into a single combined report with cumulative P&L. Reports include full tax breakdown (brokerage, STT, GST, etc.) and Claude API costs.

---

## Database

All data is stored in **SQLite** — `data/trades.db` (auto-created) for trades and analyses, `data/candle_cache.db` for cached candle data.

> **Security:** `data/` is excluded from Git via `.gitignore`, except `candle_cache.db` (public market data).

| Table | What it stores |
|---|---|
| `trades` | Intraday trade results — symbol, side, entry/exit, qty, P&L, exit reason |
| `intraday_tax_ledger` | Intraday tax ledger (see [Taxation](#taxation)) |
| `capital_gains_ledger` | Capital gains ledger (see [Taxation](#taxation)) |
| `portfolio_analyses` | Phase 1 analysis results — action, conviction, reasoning, targets |

**Utility scripts:**

| Script | Purpose |
|---|---|
| `python scripts/view_trades.py` | Print all intraday trades with P&L summary |
| `python scripts/view_performance.py` | Performance analytics — daily P&L, win rate, exit stats, indicator correlation |
| `python scripts/view_analyses.py` | Print all portfolio analyses with action status |
| `python scripts/generate_sheet.py` | Generate TSV spreadsheet from portfolio report (1 Claude call) |
| `python scripts/view_candle_cache.py` | View candle cache — symbols, date ranges, OHLCV data |
| `python scripts/verify_trades.py` | Verify trades against Zerodha API — corrects prices in reports + trades table |
| `python scripts/rejection_audit.py --append-report` | Post-trade rejection audit — verdicts every skipped entry (`AVOIDED_LOSS` / `MISSED_PROFIT` / `NEUTRAL`) using 15:30 close. Auto-runs at EOD; CLI for back-fill: `--date YYYY-MM-DD` |
| `python scripts/import_reports_to_db.py` | One-time import of existing JSON reports into DB |

All scripts support `--help` for usage details.

---

## Taxation

The bot tracks all trading charges and generates tax-ready data for Indian income tax filing (ITR-3). Intraday trades are classified as speculative business income, with separate tracking for short-term and long-term capital gains.

Key features: automatic trade verification against Zerodha API, per-trade charge breakdown (brokerage, STT, GST, stamp duty), and combined tax summary scripts.

For the comprehensive tax guide (slab rates, ITR forms, advance tax deadlines, loss carry-forward), see **[docs/TAX_GUIDE.md](docs/TAX_GUIDE.md)**.

### Tax scripts

| Script | Purpose |
|---|---|
| `python scripts/fill_intraday_ledger.py` | Fill intraday tax ledger from live trading JSONs (auto-runs after each trade day) |
| `python scripts/verify_trades.py` | Verify trades against Zerodha API — corrects prices in reports, tax ledger, and trades table |
| `python scripts/import_zerodha_taxpnl.py` | Import Zerodha Tax P&L xlsx -- verify intraday + import capital gains |
| `python scripts/import_zerodha_taxpnl.py --fy 2025` | Verify FY 2025-26 sheet only |
| `python scripts/import_zerodha_taxpnl.py --fy 2026` | Verify FY 2026-27 sheet only |
| `python scripts/tax_summary.py` | Combined tax summary -- speculative income, STCG, LTCG, estimated tax |
| `python scripts/tax_summary.py --intraday --fy 2025` | Intraday tax summary for FY 2025-26 |
| `python scripts/view_intraday_ledger.py` | View intraday trades with verified/unverified status |
| `python scripts/view_intraday_ledger.py --fy 2026` | View intraday ledger for FY 2026-27 |
| `python scripts/view_intraday_ledger.py --list` | List all FYs with intraday data |
| `python scripts/view_capital_gains_ledger.py` | View capital gains trades (short-term / long-term) |
| `python scripts/view_capital_gains_ledger.py --list` | List all FYs with capital gains data |

Tax rates are configurable in `config.py` (`TAX_RATE_PCT`, `STCG_TAX_RATE_PCT`, `LTCG_TAX_RATE_PCT`, etc.).

---

## Data Sync

The `data/`, `reports/`, and `logs/` folders are excluded from Git (they contain personal trading data). You can sync them with a **separate private Git repo** to keep data safe and portable across machines.

### Setting it up

1. **Create a private repo** on GitHub (e.g. `your-username/ai-portfolio-manager-data`). Keep it **Private**.

2. **Sync** — run whenever you want to sync your data:
   ```bash
   python scripts/backup_data.py              # two-way sync + push (HTTPS)
   python scripts/backup_data.py --ssh        # use SSH URL (for Linux VMs with SSH keys)
   python scripts/backup_data.py --dry-run    # preview changes
   python scripts/backup_data.py --overwrite-db  # overwrite remote DB with local (skip merge)
   python scripts/backup_data.py --all-local  # push ALL local data to remote (full overwrite)
   python scripts/backup_data.py --all-remote # pull ALL remote data to local (full overwrite)
   ```
   If the data repo isn't cloned yet, the script auto-clones it into the parent folder on first run.

3. **Recover on a new machine:**
   ```bash
   git clone https://github.com/your-username/ai-portfolio-manager.git
   cd ai-portfolio-manager
   python scripts/backup_data.py         # HTTPS (Windows/macOS)
   python scripts/backup_data.py --ssh   # SSH (Linux VMs)
   ```

### How sync works

| Scenario | Action |
|----------|--------|
| File only in local | Copied to backup repo |
| File only in remote | Copied to local project |
| File in both, identical | Skipped |
| SQLite database (`.db`) in both, different | **Merged row-by-row** — new rows from each side are added to the other. Nothing is deleted. Both DBs end up identical with the union of all rows |
| SQLite database with `--overwrite-db` | **One-direction overwrite** — asks which side to keep (local/remote) with a confirmation prompt, then copies that DB to the other side. Use when one side has corrected data (e.g. fixed P&L values) |
| `--all-local` | **Full push** — copies ALL local files and DBs to remote, removing remote-only files. No merge, no prompts |
| `--all-remote` | **Full pull** — copies ALL remote files and DBs to local, removing local-only files. Use to set up a new machine |
| Other file in both, different | Asks: keep **(l)**ocal or **(r)**emote? |

This means you can trade on a VM, trade on your laptop, and sync — all trades from both machines end up in both databases. The merge uses each table's unique key (`date + order_id` for tax ledger, `date + symbol + side + price` for trades, etc.) to avoid duplicates.

After syncing, all changes are committed and pushed to the backup repo.

### What gets synced

| Folder | Contents |
|--------|----------|
| `data/` | SQLite database (`trades.db`) — **merged**, not overwritten. Zerodha Tax P&L xlsx files. `candle_cache.db` is excluded from sync (committed to main repo) |
| `reports/` | All trading and portfolio reports (txt + json) |
| `logs/` | Log files |

**Excluded:** `access_token.json` (expires daily), `__pycache__`, OS junk files.

> **Security:** The backup repo must be **Private** on GitHub. The main code repo has no link to the data repo — the connection only exists inside the sync script.

---

## Cost Summary

| Cost | Amount | Frequency |
|------|--------|-----------|
| Zerodha Kite Connect | Rs.500 | Monthly |
| Claude API (Pro plan) | ~Rs.50-100 | Per trading day |
| Zerodha brokerage + charges | ~0.05-0.15% of turnover | Per trade |

NoAI mode eliminates Claude API costs entirely.

---

## Safety Features

- **Dry-run mode** — no real orders, simulated P&L with time-of-day slippage modelling
- **Circuit breaker** — stops trading on daily loss threshold, resumes after cooldown
- **Daily-loss soft-stop** — at 1.5% day loss, blocks new entries while still managing existing positions (hard CB still closes all at 3%)
- **Whipsaw guard** — pauses entries after consecutive SL hits
- **Per-symbol re-entry cooldown** — 30 min block on same SYMBOL_SIDE after any exit (churn-loop breaker)
- **Lunch-lull skip** — rejects new entries 11:30-12:15 IST unless score is exceptionally strong
- **Charge-aware target** — rejects trades where gross target profit < 2× round-trip charges
- **Budget-regime gates** — ADX threshold / trade cap / min-score auto-tighten on small accounts
- **Budget cap & loss-adjusted sizing** — never exceeds budget; reduces size after losses
- **ATR-based dynamic stop-losses** — data-driven SL/target with structural-level protection and hard caps
- **Crash recovery** — resumes monitoring orphaned positions after restart
- **Order API failure protection** — stops Claude calls and shuts down gracefully on API failures
- **Market protection on orders** — all orders include Zerodha's `market_protection` safeguard
- **Bid-ask spread check** — skips illiquid stocks before ordering
- **Impact-cost / depth check** — walks top-5 order-book levels and skips when our full qty would fill at > 0.2% slippage vs LTP (or when visible top-5 depth is smaller than our qty)
- **Loud SL-M failure alert** — if exchange SL-M placement fails, logs an ERROR banner and tags the position; user is never silently running naked
- **Graceful shutdown** — Ctrl+C squares off all positions before exiting
- **Existing holdings are READ-ONLY** — only trades with the managed budget pool
- **Config hints** — log messages tell you which config to change when an action is skipped

All thresholds are configurable in `config.py`. For the complete risk management architecture and the plain-English decision timeline (start of day → EOD), see **[docs/STRATEGY_V2.md](docs/STRATEGY_V2.md)**.

---

## Disclaimer

This software is for educational and experimental purposes. Stock market trading involves substantial risk of loss. Past performance (including dry-run results) does not guarantee future results. Use at your own risk. The authors are not responsible for any financial losses incurred from using this software.
