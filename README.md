# AI Portfolio Manager

An AI-powered intraday trading bot for the Indian stock market (NSE) that uses **Claude AI** for stock selection and **Zerodha Kite** for market data and order execution.

## What it does

### Phase 1 — Portfolio Analysis (read-only)
Logs into Zerodha, shows an account snapshot (available balance, portfolio value, P&L), then analyses your existing demat holdings using Claude AI. Generates a detailed report with action recommendations (HOLD, BUY MORE, EXIT, etc.) for each stock.

If a previous report exists, Claude automatically receives the last analysis for each stock — including its old price, action, target, and next steps — so it can compare changes and make better-informed recommendations.

All analysis results are stored in a **SQLite database** (`data/trades.db`) for historical tracking and faster lookups across runs.

**Key intelligence features:**
- **Multi-report history** — Claude sees the full analysis history for each stock across all past runs, not just the latest. This lets it track evolving trends, conviction changes, and price movements over time.
- **Action tracking** — Every non-HOLD recommendation is tracked as PENDING → DONE / NOT ACTED. When you act on a recommendation and run the analyser again, it detects the change (e.g. reduced quantity = partial exit done) and marks it DONE. Pending actions are re-surfaced to Claude so it can follow up.
- **Portfolio-level review** — After analysing individual stocks, Claude performs a separate portfolio-wide assessment: sector-wise breakdown, portfolio health grade, missing exposure, rebalancing suggestions, and new stock recommendations.

```bash
python main.py --mode analyze
```

### Phase 2 — Intraday Trading Bot
A fully automated intraday trading bot that:
- Logs into Zerodha and shows your account snapshot (balance, portfolio, P&L)
- Waits for market open (handles weekends + NSE holidays automatically)
- If started after market hours, shows a countdown timer to the next trading day and auto-resumes
- Asks Claude to pick the best intraday trades from Nifty 50/100/200
- **Delayed market entry** — observes prices for 15 min after open, only enters stocks with confirmed directional movement (>0.3%)
- **ATR-based dynamic stop-losses** — computes Average True Range from historical data to set intelligent SL/target levels (falls back to Claude's values if data unavailable)
- Enters positions at market open with stop-loss and target prices
- Monitors prices every 30 seconds, auto-exits on SL/target hits
- **Detailed live position tracking** — prints a per-position status table every ~60s showing current price, P&L, and distance to SL/target
- **Auto trailing stop-loss** — automatically moves SL in your favour as profit grows
- Claude reviews positions every **15 minutes** for adjustments (with full trade history context)
- **Auto re-scan** — when all positions close mid-day, scans for new trades instead of stopping
- **Smart position sizing** — auto-reduces qty to fit budget instead of dropping the trade
- **Max re-entry limit** — prevents re-entering the same stock after repeated stop-losses (default: 2x/day)
- **Market condition detection** — classifies the day as BULLISH/BEARISH/NEUTRAL with HIGH_VOLATILITY/NORMAL regime, adjusts strategy accordingly
- Uses **NIFTY 50 index trend** to bias trade direction with sector-specific advice
- Anti-momentum-chasing rules — avoids stocks already up >2% at scan time
- **Performance database** — stores every trade in SQLite, feeds recent win rates and P&L history into Claude's next-day stock selection
- **Slippage model** in dry-run mode for realistic P&L simulation
- Squares off all positions before market close (3:10 PM)
- Generates a full P&L report with taxes, charges, and net profit

```bash
python main.py --mode trade
```

**Dry-run mode** is ON by default — no real orders are placed. Set `DRY_RUN = False` in `config.py` only after reviewing dry-run results.

---

## Prerequisites

- **Python 3.10+** (uses modern type syntax)
- **Windows/Linux/Mac** with a desktop environment (browser needed for Zerodha login)
- A **Zerodha trading account** with Kite Connect API access
- A **Claude API key** from Anthropic

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
| `kiteconnect` | Zerodha Kite trading API client |
| `python-dotenv` | Loads API keys from `.env` file |

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

> **Note:** Zerodha access tokens expire daily at midnight. The bot handles re-login automatically. On first run each day, a browser window opens for you to log in to Zerodha.

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

Open `config.py` and review these key settings:

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `DRY_RUN` | `True` | `True` = simulate orders (safe). `False` = real trading |
| `MAX_BUDGET_INR` | `10,000` | Maximum capital the bot can deploy per day |
| `MIN_BALANCE_TO_TRADE` | `3,000` | Minimum Zerodha balance to start trading || `CUTOFF_MINUTES_BEFORE_CLOSE` | `30` | Skip trading if less than this many minutes to square-off || `SCAN_UNIVERSE` | `NIFTY50` | Stock pool: NIFTY50, NIFTY100, NIFTY200, or CUSTOM |
| `MAX_POSITIONS` | `5` | Max simultaneous trades |
| `MAX_REENTRIES_PER_STOCK` | `2` | Max times a stock can be traded in one day |
| `ENTRY_DELAY_MINUTES` | `15` | Observation period after market open before entering trades |
| `ENTRY_MIN_MOVE_PCT` | `0.3%` | Minimum directional move from open to confirm entry |
| `ATR_PERIOD` | `14` | Number of days for Average True Range calculation |
| `ATR_MULTIPLIER` | `1.5` | ATR multiplier for dynamic SL (2× for target) |
| `DEFAULT_STOP_LOSS_PCT` | `1.5%` | Fallback SL when ATR data is unavailable |
| `DEFAULT_TARGET_PCT` | `2.0%` | Fallback target when ATR data is unavailable |
| `MAX_LOSS_PER_DAY_PCT` | `3.0%` | Circuit breaker — stops trading for the day |
| `TRAIL_AFTER_RISK_MULTIPLE` | `1.0` | Start trailing SL after profit reaches 1× initial risk |
| `TRAIL_STEP_PCT` | `50.0%` | Trail SL by 50% of unrealised profit |
| `SLIPPAGE_PCT` | `0.05%` | Simulated slippage on dry-run entries |
| `CLAUDE_PLAN` | `pro` | Claude model tier: free, pro, or max |
| `ZERODHA_PLAN` | `connect_paid` | Zerodha plan: personal_free or connect_paid |

> **Dynamic Budget:** The bot fetches your Zerodha margin (`available.live_balance`) at startup and trades with `min(available_funds, MAX_BUDGET_INR)`. So if you have ₹20K in Zerodha but `MAX_BUDGET_INR = 10,000`, only ₹10K is used. If your balance is below `MIN_BALANCE_TO_TRADE` (₹3K), the bot won't trade (skipped in dry-run mode). Increase `MAX_BUDGET_INR` as your confidence grows.

All settings are thoroughly commented in `config.py` — read the comments for details on each option.

### Step 6: Run

```bash
# Analyse existing portfolio
python main.py --mode analyze

# Intraday trading bot (dry-run by default)
python main.py --mode trade
```

You can start Phase 2 anytime — even the night before. It handles weekends, NSE holidays, late starts, and token expiry automatically. Press **Ctrl+C** to gracefully shut down (squares off all positions first).

---

## Project Structure

```
ai-portfolio-manager/
├── main.py                  # Entry point — routes to Phase 1 or Phase 2
├── config.py                # All settings in one place (plans, budget, timing, costs)
├── generate_sheet.py        # One-off script to generate TSV spreadsheet from report data
├── import_reports_to_db.py  # Import existing JSON report files into the SQLite database
├── view_trades.py           # View all intraday trades from database with P&L summary
├── view_analyses.py         # View all portfolio analyses from database with action status
├── requirements.txt         # Python dependencies
├── .env                     # Your API keys (not in Git)
├── .gitignore               # Keeps secrets and junk out of Git
├── core/
│   ├── claude_client.py     # Claude API wrapper + error classification
│   ├── zerodha_client.py    # Zerodha Kite API wrapper (login, quotes, orders, account snapshot)
│   └── logger.py            # Coloured terminal output + rotating log file
├── portfolio/
│   ├── analyser.py          # Phase 1 orchestrator (read-only analysis)
│   └── manager.py           # Phase 2 orchestrator (intraday trading loop)
├── services/
│   ├── analysis_queue.py    # Per-stock Claude analysis with retry logic
│   ├── market_data.py       # Enriches portfolio with live prices + history
│   ├── stock_scanner.py     # Pre-market Claude scan + mid-day review + price parsing helpers
│   ├── order_engine.py      # Order execution, position tracking, SL/target monitoring, P&L + taxes
│   ├── report_writer.py     # Generates .txt reports and .json data dumps
│   └── performance_tracker.py # SQLite database for trade history + portfolio analysis tracking
├── data/
│   └── trades.db            # SQLite database (auto-created on first run)
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

## Running on a VM

If you want to keep the bot running 24/7:

1. Use a **Windows VM with RDP access** (needed for Zerodha browser login)
2. Zip the project folder (exclude `__pycache__/`, `logs/`, `reports/`)
3. On the VM:
   ```bash
   pip install -r requirements.txt
   ```
4. Create your `.env` file with API keys
5. Delete any old `access_token.json` (tokens are IP-specific)
6. RDP into the VM and run:
   ```bash
   python main.py --mode trade
   ```
7. The bot will wait for market open, trade the full day, and generate reports
8. Zerodha login pops up in the browser once per day — keep the RDP session alive until login completes, then you can disconnect

---

## Reports

Reports are organised by type, year, and month inside `reports/`:

- **Phase 1:** `reports/portfolio/<year>/<month>/portfolio_report_DD.txt` + `portfolio_data_DD.json`
- **Phase 2:** `reports/trading/<year>/<month>/trading_report_DD.txt` + `trading_data_DD.json`

Folders are created on-demand — only when a report is generated for that period. Files are zero-padded by day (`01`, `02`, … `31`) so they sort chronologically.

> **Re-run protection (Phase 1):** If a report for today already exists, the bot asks for confirmation before overwriting it.

> **Same-day merging (Phase 2):** Running Phase 2 multiple times on the same day merges all sessions into a single combined report with cumulative P&L, % returns on budget, and session markers in the trade log.

The Phase 2 report includes:
- Every trade with entry/exit prices, P&L, and reason (SL/target/review/square-off)
- Full tax breakdown: brokerage, STT, GST, exchange charges, SEBI, stamp duty
- Claude API costs and **net profit after all charges and taxes**

---

## Database

All historical data is stored in a single **SQLite database** at `data/trades.db` (auto-created on first run).

| Table | Phase | What it stores |
|---|---|---|
| `trades` | Phase 2 | Intraday trade results — symbol, side, entry/exit price, qty, P&L, exit reason, market condition |
| `portfolio_analyses` | Phase 1 | Analysis results — symbol, action, conviction, reasoning, horizon, target price, current/invested values, risks |

The bot uses this data to:
- Feed recent performance (win rates, losing stocks) into Claude's stock selection prompt
- Load the previous Phase 1 analysis for comparison (faster than scanning JSON files)
- Track how Claude's recommendations for each stock evolve over time

**Utility scripts:**

| Script | Purpose |
|---|---|
| `python view_trades.py` | Print all intraday trades — entry/exit, P&L, exit reasons, market conditions, win/loss summary |
| `python view_analyses.py` | Print all portfolio analyses — action, conviction, status (DONE/PENDING/NOT ACTED), P&L, per-date summary |
| `python import_reports_to_db.py` | One-time import of existing JSON report files into the DB. Safe to re-run — skips dates already imported. Also auto-resolves `action_taken` by comparing portfolio quantities across consecutive reports. |

Or query directly:
```bash
sqlite3 data/trades.db "SELECT symbol, COUNT(*) as trades, ROUND(AVG(pnl),2) as avg_pnl FROM trades GROUP BY symbol;"
```

---

## Cost Summary

| Cost | Amount | Frequency |
|------|--------|-----------|
| Zerodha Kite Connect | ₹500 | Monthly |
| Claude API (Pro plan) | ~₹50-100 | Per trading day |
| Zerodha brokerage | ₹20 or 0.03% per order | Per trade |
| STT, GST, stamp duty, etc. | ~0.05-0.1% of turnover | Per trade |

To be profitable, daily gross trading profits need to exceed ~₹50-100 in Claude API costs plus ~₹23/day amortised Zerodha subscription.

---

## Safety Features

- **Dry-run mode** (default) — no real orders, simulated P&L on live prices with slippage modelling
- **Circuit breaker** — stops trading if daily loss exceeds threshold
- **Budget cap** — never exceeds `MAX_BUDGET_INR`
- **Smart sizing** — auto-reduces qty to fit remaining budget instead of rejecting the trade
- **Re-entry limit** — blocks repeated entries into the same stock after stop-losses (`MAX_REENTRIES_PER_STOCK`)
- **Min balance check** — won't trade live if Zerodha balance is below `MIN_BALANCE_TO_TRADE`
- **ATR-based dynamic stop-losses** — data-driven SL/target using historical volatility
- **Auto trailing stop-loss** — rule-based SL tightening as positions move in profit
- **Delayed entry filter** — skips indecisive stocks that haven't moved after market open
- **Market condition awareness** — detects high-volatility regimes and adjusts position sizing
- **Performance memory** — learns from past trades via SQLite DB to avoid repeating mistakes
- **Action tracking** — tracks whether you acted on each recommendation (PENDING → DONE / NOT ACTED)
- **Graceful shutdown** — Ctrl+C squares off all positions before exiting
- **Existing holdings are READ-ONLY** — the bot only trades with the managed budget pool
- **NSE holiday calendar** — handles weekends, holidays, late starts, and token expiry automatically

---

## Disclaimer

This software is for educational and experimental purposes. Stock market trading involves substantial risk of loss. Past performance (including dry-run results) does not guarantee future results. Use at your own risk. The authors are not responsible for any financial losses incurred from using this software.
