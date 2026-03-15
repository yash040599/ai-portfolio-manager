# AI Portfolio Manager

An AI-powered intraday trading bot for the Indian stock market (NSE) that uses **Claude AI** for stock selection and **Zerodha Kite** for market data and order execution.

## What it does

### Phase 1 — Portfolio Analysis (read-only)
Analyses your existing demat holdings using Claude AI. Generates a detailed report with action recommendations (HOLD, BUY MORE, EXIT, etc.) for each stock.

```bash
python main.py --phase 1
```

### Phase 2 — Intraday Trading Bot
A fully automated intraday trading bot that:
- Waits for market open (handles weekends + NSE holidays automatically)
- Asks Claude to pick the best intraday trades from Nifty 50/100/200
- Enters positions at market open with stop-loss and target prices
- Monitors prices every 30 seconds, auto-exits on SL/target hits
- Claude reviews positions every 30 minutes for adjustments
- Squares off all positions before market close (3:10 PM)
- Generates a full P&L report with taxes, charges, and net profit

```bash
python main.py --phase 2
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
| `MIN_BALANCE_TO_TRADE` | `3,000` | Minimum Zerodha balance to start trading |
| `SCAN_UNIVERSE` | `NIFTY50` | Stock pool: NIFTY50, NIFTY100, NIFTY200, or CUSTOM |
| `MAX_POSITIONS` | `5` | Max simultaneous trades |
| `DEFAULT_STOP_LOSS_PCT` | `1.5%` | Auto-exit on loss |
| `DEFAULT_TARGET_PCT` | `2.0%` | Auto-exit on profit |
| `MAX_LOSS_PER_DAY_PCT` | `3.0%` | Circuit breaker — stops trading for the day |
| `CLAUDE_PLAN` | `pro` | Claude model tier: free, pro, or max |
| `ZERODHA_PLAN` | `connect_paid` | Zerodha plan: personal_free or connect_paid |

> **Dynamic Budget:** The bot fetches your Zerodha account balance at startup and trades with `min(available_funds, MAX_BUDGET_INR)`. So if you have ₹20K in Zerodha but `MAX_BUDGET_INR = 10,000`, only ₹10K is used. If your balance is below `MIN_BALANCE_TO_TRADE` (₹3K), the bot won't trade (skipped in dry-run mode). Increase `MAX_BUDGET_INR` as your confidence grows.

All settings are thoroughly commented in `config.py` — read the comments for details on each option.

### Step 6: Run

```bash
# Phase 1: Analyse existing portfolio
python main.py --phase 1

# Phase 2: Intraday trading bot (dry-run by default)
python main.py --phase 2
```

You can start Phase 2 anytime — even the night before. It will:
1. Detect weekends and NSE holidays, show a countdown to the next trading day
2. Wait for pre-market time (9:00 AM IST)
3. Log in to Zerodha (opens a browser — log in and close the tab)
4. Run the full trading day cycle
5. Generate reports in `reports/`

Press **Ctrl+C** to gracefully shut down (squares off all positions first).

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
│   ├── zerodha_client.py    # Zerodha Kite API wrapper (login, quotes, orders)
│   └── logger.py            # Coloured terminal output + rotating log file
├── portfolio/
│   ├── analyser.py          # Phase 1 orchestrator (read-only analysis)
│   └── manager.py           # Phase 2 orchestrator (intraday trading loop)
├── services/
│   ├── analysis_queue.py    # Per-stock Claude analysis with retry logic
│   ├── market_data.py       # Enriches portfolio with live prices + history
│   ├── stock_scanner.py     # Pre-market Claude scan + mid-day review
│   ├── order_engine.py      # Order execution, position tracking, P&L + taxes
│   └── report_writer.py     # Generates .txt reports and .json data dumps
├── reports/                 # Generated reports (one per run)
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
   python main.py --phase 2
   ```
7. The bot will wait for market open, trade the full day, and generate reports
8. Zerodha login pops up in the browser once per day — keep the RDP session alive until login completes, then you can disconnect

---

## Reports

After each run, check the `reports/` folder:

- **Phase 1:** `portfolio_report_YYYY-MM-DD.txt` + `portfolio_data_YYYY-MM-DD.json`
- **Phase 2:** `trading_report_YYYY-MM-DD.txt` + `trading_data_YYYY-MM-DD.json`

The Phase 2 report includes:
- Every trade with entry/exit prices, P&L, and reason (SL/target/review/square-off)
- Full tax breakdown: brokerage, STT, GST, exchange charges, SEBI, stamp duty
- Claude API costs (per-call, actual usage)
- Zerodha subscription cost (FYI, not deducted from daily P&L)
- **Net profit after all charges and taxes**

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

- **Dry-run mode** (default) — no real orders, simulated P&L on live prices
- **Circuit breaker** — stops trading if daily loss exceeds threshold
- **Budget cap** — never exceeds your configured budget
- **Position limits** — max stocks held simultaneously
- **Graceful shutdown** — Ctrl+C squares off all positions before exiting
- **Existing holdings are READ-ONLY** — the bot only trades with the managed budget pool
- **NSE holiday calendar** — no wasted API calls on non-trading days

---

## Disclaimer

This software is for educational and experimental purposes. Stock market trading involves substantial risk of loss. Past performance (including dry-run results) does not guarantee future results. Use at your own risk. The authors are not responsible for any financial losses incurred from using this software.
