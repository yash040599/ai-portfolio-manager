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

1. **Pre-market scan** — fetches candles for every stock in `SCAN_UNIVERSE`, runs candlestick pattern detectors + technical indicators (EMA, RSI, VWAP, SuperTrend, MACD, Fibonacci, VWAP Bands, ADX, and more), auto-selects best candidates by score
2. **Execution** — enters positions with ATR-based dynamic stop-losses, validates entry prices against live Zerodha quotes, checks bid-ask spreads, and tries fallback candidates if primary picks fail entry checks
3. **Monitoring** — polls prices with adaptive frequency, auto-trails SL, takes partial profits, runs stagnant position exit
4. **Risk management** — circuit breaker on daily loss, whipsaw guard, sector caps, regime-shift protection, India VIX monitoring, crash recovery, and manual trade adoption
5. **EOD** — squares off all positions, generates P&L report with full tax breakdown, auto-verifies trades against Zerodha API

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

# Budget cap — limit today's capital to ₹30,000
python main.py --mode trade --max 30000

# V1 legacy (retired — sends raw prices to Claude)
python main.py --mode trade --v1

# Test Zerodha login only
python main.py --mode login
```

NoAI mode uses the same candle pipeline for everything — stock selection, monitoring, and re-scans — with zero API costs. See **[docs/STRATEGY_V2_NOAI.md](docs/STRATEGY_V2_NOAI.md)** for details.

### Historical Data Caching

Previous days' candle data is cached in `data/candle_cache.db` (SQLite) to avoid redundant Zerodha API calls. Features auto-cleanup, weekend/holiday-aware lookback, corporate action detection, and API rate limiting. The cache is committed to Git (pure market data). Run `--test` the evening before to pre-warm it.

### Documentation

| Doc | What it covers |
|-----|---------------|
| **[docs/STRATEGY_V2.md](docs/STRATEGY_V2.md)** | V2 strategy architecture — indicators, patterns, scoring system, risk layers |
| **[docs/STRATEGY_V2_NOAI.md](docs/STRATEGY_V2_NOAI.md)** | NoAI strategy — fully automated, zero Claude calls |
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
   - Subscribe to the **Kite Connect** plan (₹500/month)
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
   - Add credits (₹500–1000 is enough to start — the bot uses ~₹50-100/day on the Pro plan)

3. **Generate an API key**
   - Go to **Settings → API Keys**
   - Click **"Create Key"**
   - Name it anything (e.g., "portfolio-bot")
   - Copy the key immediately — it's shown only once
   - This is your `CLAUDE_API_KEY`

> **Pricing reference:** The bot uses Claude Sonnet (Pro plan). Each API call costs roughly ₹2-4. A typical trading day makes ~15 calls = ~₹50-100/day.

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
| `SCAN_UNIVERSE` | `NIFTY100` | Stock pool: NIFTY50, NIFTY100, NIFTY200, or CUSTOM |
| `MAX_POSITIONS` | `3` | Max simultaneous trades |
| `DRY_RUN` | `False` | Simulate trades without placing real orders (use `--dryrun` flag) |
| `CLAUDE_PLAN` | `pro` | Claude model tier: free, pro, or max |

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
│   ├── STRATEGY_V2.md       # V2 candle strategy — indicators, patterns, scoring system
│   ├── STRATEGY_V2_NOAI.md  # NoAI strategy — fully automated, zero Claude calls
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
| `python scripts/import_zerodha_taxpnl.py` | Import Zerodha Tax P&L xlsx — verify intraday + import capital gains |
| `python scripts/tax_summary.py` | Combined tax summary — speculative income, STCG, LTCG, estimated tax |
| `python scripts/view_intraday_ledger.py` | View intraday trades with verified/unverified status |
| `python scripts/view_capital_gains_ledger.py` | View capital gains trades (short-term / long-term) |

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
| Zerodha Kite Connect | ₹500 | Monthly |
| Claude API (Pro plan) | ~₹50-100 | Per trading day |
| Zerodha brokerage + charges | ~0.05-0.15% of turnover | Per trade |

NoAI mode eliminates Claude API costs entirely.

---

## Safety Features

- **Dry-run mode** — no real orders, simulated P&L with time-of-day slippage modelling
- **Circuit breaker** — stops trading on daily loss threshold, resumes after cooldown
- **Whipsaw guard** — pauses entries after consecutive SL hits
- **Budget cap & loss-adjusted sizing** — never exceeds budget; reduces size after losses
- **ATR-based dynamic stop-losses** — data-driven SL/target, hard-capped, picks tighter of ATR vs Claude
- **Crash recovery** — resumes monitoring orphaned positions after restart
- **Order API failure protection** — stops Claude calls and shuts down gracefully on API failures
- **Market protection on orders** — all orders include Zerodha's `market_protection` safeguard
- **Bid-ask spread check** — skips illiquid stocks before ordering
- **Graceful shutdown** — Ctrl+C squares off all positions before exiting
- **Existing holdings are READ-ONLY** — only trades with the managed budget pool
- **Config hints** — log messages tell you which config to change when an action is skipped

All thresholds are configurable in `config.py`. For the complete risk management architecture, see **[docs/STRATEGY_V2.md](docs/STRATEGY_V2.md)**.

---

## Disclaimer

This software is for educational and experimental purposes. Stock market trading involves substantial risk of loss. Past performance (including dry-run results) does not guarantee future results. Use at your own risk. The authors are not responsible for any financial losses incurred from using this software.
