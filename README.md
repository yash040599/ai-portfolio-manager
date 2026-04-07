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
- **New stock recommendations in spreadsheet** — Claude suggests 3-5 NSE-listed stocks to fill portfolio gaps (missing sectors, diversification). These appear as `NEW BUY` rows in the TSV spreadsheet alongside your existing holdings, with target prices and rationale — ready to act on.

```bash
python main.py --mode analyze
```

### Phase 2 — Intraday Trading Bot (V2 — Default)
A fully automated intraday trading bot that:
- Logs into Zerodha and shows your account snapshot (balance, portfolio, P&L)
- Waits for market open (handles weekends + NSE holidays automatically)
- If started after market hours, shows a countdown timer to the next trading day and auto-resumes
- **Pre-market candle analysis** — fetches 15-minute + daily candles for every stock in the universe from Zerodha's historical API (free). Runs 14 candlestick pattern detectors, 11 technical indicators (EMA, RSI, VWAP, SuperTrend, MACD, ORB, Gap, Daily EMA, Prev-Day S&R, Hourly EMA, BB Squeeze), and composite scoring (~-24 to +24). Only the top 15 strongest setups are sent to Claude
- **Sector diversification** — max 2 stocks per sector (BANKING, IT, PHARMA, AUTO, etc.) prevents correlated risk
- **Delayed market entry** — observes prices for 15 min after open, only enters stocks with confirmed directional movement (>0.3%). **Smart delay**: if started after 9:30 AM (opening volatility already passed), automatically reduces to a 5-min observation instead of the full 15
- **ATR-based dynamic stop-losses** — computes Average True Range from 15-minute intraday candles to set intelligent SL/target levels sized for intraday moves (falls back to Claude's values if data unavailable). SL is hard-capped at 2.5% to prevent swing-trade-sized stops. When both ATR and Claude provide SL levels, the tighter (closer to entry) SL is used
- **Actual fill prices** — in live mode, after placing a MARKET order, polls Zerodha's order trades API to get the real weighted-average fill price. Entry price, P&L, SL, and target are all recalculated on the actual fill — not the estimated quote price
- **Live entry price validation** — before placing any order, cross-checks Claude's recommended entry price against Zerodha's live quote. If they differ by >5%, overrides with the live price to prevent hallucinated-price entries
- **Always trusts Zerodha fills** — after a MARKET order fills, always uses Zerodha's actual fill price (not the pre-order estimate). SL, target, and P&L are recalculated on the real fill. Logs a warning if the fill deviates >5% but never rejects it
- Enters positions at market open with stop-loss and target prices
- Monitors prices every 10 seconds (or 5s when near SL/target), auto-exits on SL/target hits
- **Compact live status** — prints a one-line status every poll (time, open/closed count, unrealised/realised P&L). When restarting mid-day, also shows cumulative daily totals (all runs combined) from the trade database
- **Auto trailing stop-loss** — automatically moves SL in your favour as profit grows
- **Partial profit taking** — at 1× risk profit, automatically exits 50% of position and locks in guaranteed profit. Remaining 50% rides with trailing stop
- **Time-decay targets** — after 2 PM, reduces open position targets by 40% to lock in profits before square-off
- Claude reviews positions every **25 minutes** for adjustments (with full trade history context + fresh 5-min candle patterns, RSI, EMA, VWAP)
- **Candle re-scan auto-protect** — every 15 min (free, no Claude cost), re-analyses open positions. If candles form a strong contrary signal (score ±4 against your position), automatically tightens SL
- **Anti-panic exit** — Claude's review includes a rule against panic-selling: "If a position shows a loss but hasn't hit its numeric SL, do NOT recommend EXIT"
- **Auto re-scan** — when all positions close mid-day, scans for new trades instead of stopping
- **Partial re-scan** — when some (but not all) positions close via SL/target, immediately scans for replacement trades to fill empty slots instead of riding remaining losers with no hedge
- **Session-aware re-scans** — mid-day re-scans pass current day P&L and already-traded symbols to Claude so it can adjust risk appetite
- **Late entry guard** — won't open new positions if fewer than 60 minutes remain before square-off
- **Smart position sizing** — auto-reduces qty to fit budget instead of dropping the trade
- **Max re-entry limit** — prevents re-entering the same stock after repeated stop-losses (default: 2x/day)
- **Market condition detection** — classifies the day as BULLISH/BEARISH/NEUTRAL with HIGH_VOLATILITY/NORMAL regime, adjusts strategy accordingly
- **Continuous NIFTY monitoring** — re-checks NIFTY 50 index every 15 minutes during trading, detects intraday regime shifts (e.g. morning dip → afternoon recovery), and updates market bias for re-scans and Claude reviews
- Uses **NIFTY 50 index trend** to bias trade direction with sector-specific advice
- **Periodic opportunity scanning** — every 30 minutes, if position slots are free, proactively scans for new trades instead of waiting for existing positions to close. Ensures capital doesn't sit idle when the initial scan picked few stocks
- **Minimum capital deployment** — ensures at least 60% of budget is deployed by instructing Claude to size positions larger and auto-boosting qty when Claude under-sizes. Prevents scenarios where only a fraction of capital is used
- Anti-momentum-chasing rules — avoids stocks already up >2% (for BUY) or already down >2% (for SELL) at scan time. Extended moves are likely to revert
- **Performance database** — stores every trade in SQLite, feeds recent win rates and P&L history into Claude's next-day stock selection
- **Slippage model** in dry-run mode for realistic P&L simulation
- Squares off all positions before market close (3:10 PM)
- Generates a full P&L report with taxes, charges, and net profit
- **Estimated income tax** — shows per-day tax liability at your slab rate (configurable `TAX_RATE_PCT` in config.py, default 30%)
- **Tax ledger & capital gains** — full tax infrastructure with separate DB tables, verification against Zerodha's official Tax P&L report, and combined tax summary. See the **[Taxation](#taxation)** section below
- **Order API failure protection** — if Zerodha's order API fails 3 consecutive times (after retrying each order 3 times with backoff), the bot stops calling Claude immediately (no more wasted API money), closes any open positions, and shuts down gracefully. Prevents the scenario where broken Zerodha APIs cause the bot to loop endlessly asking Claude for new recommendations
- **Crash recovery** — if the bot is stopped (Ctrl+C, crash, terminal closed) while positions are still open on Zerodha, restarting it will automatically detect and resume monitoring those positions. Fetches open MIS positions from Zerodha, recalculates ATR-based SL/targets, and jumps straight to the monitor loop — no duplicate orders, no orphaned positions
- **Manual trade adoption** — if you buy or sell a stock manually on the Zerodha app (intraday/MIS only), the bot automatically detects it on its next sync, assigns ATR-based SL/targets, and manages it like any other position — including monitoring, Claude review, and end-of-day square-off. CNC (delivery/long-term) positions are ignored. If you close a manual trade yourself before the bot does, it's marked as `EXTERNAL_CLOSE` in the report. Manual trades appear in reports with a `[M]` tag
- **Whipsaw guard** — after 3 consecutive stop-loss hits (across any stocks), pauses new entries for 30 minutes. Prevents "death by a thousand cuts" on days when signals systemically fail
- **Max circuit breaker trips** — circuit breaker can only fire and resume 2 times per day. After that, the day is over. Prevents infinite cooldown loops on catastrophically bad days
- **Dynamic score threshold** — in NoAI mode, after day loss exceeds 1.5% of budget, raises the minimum technical score for new trades. Only higher-conviction setups are picked after losses
- **Regime-shift protection** — when Nifty flips from BULLISH→BEARISH (or vice versa), immediately tightens SLs on positions contradicting the new regime: locks 50% of profit or moves SL to breakeven
- **Multi-timeframe alignment** — builds hourly candles from 15-min data and checks EMA(9/21) alignment across both timeframes. Adds conviction (+1) only when both agree
- **Bollinger Band squeeze** — detects periods of unusually low volatility (bandwidth below 75% of average). Squeeze + price above middle band → bullish breakout signal

```bash
python main.py --mode trade
```

### V1 Legacy Mode (retired)

The original intraday trading strategy without candle pattern pre-filtering. Sends raw stock prices to Claude for selection. Retired — use V2 (default) instead.

```bash
python main.py --mode trade --v1
```

### Test Mode

Shows the complete strategy analysis pipeline — how the bot fetches candle data, runs 14 candlestick pattern detectors, computes 11 technical indicators, scores each stock, applies filters, and what it would do next. Zero cost, zero risk. Useful for understanding the strategy and verifying the pipeline works.

```bash
# V2 strategy test (shows what Claude would receive)
python main.py --mode trade --test

# NoAI strategy test (shows what would be auto-traded)
python main.py --mode trade --noai --test
```

### Dry-Run Mode

Runs the **full trading strategy** (Claude calls, position monitoring, SL/target checks, trailing stops — everything) but doesn't place real orders on Zerodha. Use this to validate trading decisions before going live.

```bash
python main.py --mode trade --dryrun          # V2 dry run
python main.py --mode trade --noai --dryrun   # NoAI dry run
python main.py --mode trade --v1 --dryrun     # V1 dry run
```

### NoAI Mode (fully automated, zero Claude calls)

A completely Claude-free trading mode that uses the V2 candle pipeline for everything — stock selection, monitoring, and re-scans. Zero API costs beyond Zerodha data.

**How it works:**
- Uses the same V2 pre-filter (candlestick patterns + technical indicators) to rank stocks
- **Auto-selects trades** from top-scoring candidates — `BUY` if score is positive, `SELL` if negative
- **Auto-generates SL/target** from config defaults (ATR overrides in `enter_trade` still apply)
- **Auto-sizes positions** to fit budget and per-stock limits
- All monitoring is rule-based: SL/target checks, trailing stops, auto-protect on contrary candle signals, periodic candle re-scans
- **No Claude reviews** — stagnant position exit replaces Claude's "momentum faded" judgment (exits dead positions after 90 min)
- Partial re-scans when slots free up also use pure technical selection
- All other V2 features preserved: dynamic poll rate, crash recovery, circuit breaker cooldown, loss-adjusted sizing, etc.

**Trade-offs vs V2:**
- **Free** — zero Claude API cost per trading day
- **Faster** — no waiting for Claude responses (~10-30s per call saved)
- **Less nuanced** — no qualitative reasoning about setups, sector context, or position management
- **Stagnant exit** — rule-based exit for dead positions (replaces Claude's review-based exits)

```bash
python main.py --mode trade --noai
```

### Historical Data Caching

The bot caches previous days' candle data (15-min and daily) in a separate SQLite database (`data/candle_cache.db`) to avoid redundant Zerodha API calls. Today's candles are always fetched live. This significantly speeds up scans when running multiple times or re-scanning during the day.

- **Auto-cleanup:** Entries older than 45 days are pruned on startup.
- **Weekend/holiday-aware lookback:** Cache queries automatically widen the date range (up to +3 days) to find the last trading day's data. Handles weekends, single-day holidays, and long weekends (e.g. Friday holiday + Sat + Sun) without needing a holiday calendar.
- **Corporate action detection:** If a >35% price gap is detected between cached close and live open, the cache for that symbol is automatically invalidated and refetched with Zerodha's adjusted prices.
- **API rate limiting:** All Zerodha historical API calls are throttled to ~3 req/sec (350ms minimum between calls) to stay within Zerodha's rate limits. This prevents bulk scans (100 stocks) from getting rate-limited.
- **Git-transferable:** Unlike `trades.db` (personal data), `candle_cache.db` contains only public market data and is committed to Git. Pull on a new machine → cache is ready.
- **Pre-warm with test mode:** Run `--test` the evening before a trading day to populate the cache. Next day's live scan skips ~200 Zerodha API calls.

For detailed strategy documentation, see:
- **[docs/STRATEGY_V1.md](docs/STRATEGY_V1.md)** — V1 strategy architecture (retired)
- **[docs/STRATEGY_V2.md](docs/STRATEGY_V2.md)** — V2 candle strategy with indicator explanations and scoring system
- **[docs/STRATEGY_V2_NOAI.md](docs/STRATEGY_V2_NOAI.md)** — NoAI strategy: fully automated, zero Claude calls
- **[docs/STRATEGY_ROADMAP.md](docs/STRATEGY_ROADMAP.md)** — Strategy improvement roadmap with research-backed enhancements

---

## Prerequisites

- **Python 3.10+** (uses modern type syntax)
- **Windows/Linux/Mac** — works on headless servers too (Zerodha login supports manual/paste mode for SSH-only VMs)
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

Open `config.py` and review these key settings:

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `MAX_BUDGET_INR` | `20,000` | Maximum capital the bot can deploy per day |
| `MIN_BALANCE_TO_TRADE` | `3,000` | Minimum Zerodha balance to start trading || `CUTOFF_MINUTES_BEFORE_CLOSE` | `30` | Skip trading if less than this many minutes to square-off || `SCAN_UNIVERSE` | `NIFTY100` | Stock pool: NIFTY50, NIFTY100, NIFTY200, or CUSTOM |
| `MAX_POSITIONS` | `5` | Max simultaneous trades |
| `MAX_REENTRIES_PER_STOCK` | `2` | Max times a stock can be traded in one day |
| `ENTRY_DELAY_MINUTES` | `15` | Observation period after market open before entering trades |
| `ENTRY_MIN_MOVE_PCT` | `0.3%` | Minimum directional move from open to confirm entry |
| `ATR_PERIOD` | `14` | Number of candles for Average True Range calculation |
| `ATR_MULTIPLIER` | `1.5` | ATR multiplier for dynamic SL (2× for target) |
| `ATR_INTERVAL` | `15minute` | Candle interval for ATR — `15minute` for intraday-appropriate levels |
| `MAX_INTRADAY_SL_PCT` | `2.5%` | Hard cap on ATR-based SL width — prevents swing-trade-sized stops |
| `DEFAULT_STOP_LOSS_PCT` | `1.5%` | Fallback SL when ATR data is unavailable |
| `DEFAULT_TARGET_PCT` | `2.0%` | Fallback target when ATR data is unavailable |
| `MAX_LOSS_PER_DAY_PCT` | `3.0%` | Circuit breaker — stops trading, resumes after cooldown |
| `CIRCUIT_BREAKER_COOLDOWN_MINUTES` | `30` | Wait time after circuit breaker before resuming (0 = day over) |
| `MAX_CIRCUIT_BREAKER_TRIPS` | `2` | Max CB trips per day before stopping permanently |
| `CONSECUTIVE_SL_PAUSE_COUNT` | `3` | Pause new entries after N consecutive SL hits (0 = disable) |
| `CONSECUTIVE_SL_PAUSE_MINUTES` | `30` | How long to pause after whipsaw detection |
| `LOSS_SIZING_ENABLED` | `True` | Reduce position sizes after realised losses |
| `LOSS_SCORE_BUMP_PCT` | `1.5%` | Day loss % that triggers higher MIN_SCORE (NoAI) |
| `LOSS_SCORE_BUMP_AMOUNT` | `1.5` | Extra score points added after loss threshold (NoAI) |
| `STAGNANT_EXIT_MINUTES` | `90` | NoAI: exit dead positions after N minutes (0 = disable) |
| `STAGNANT_EXIT_MIN_MOVE_PCT` | `0.3%` | NoAI: minimum favourable move to stay alive |
| `TRAIL_AFTER_RISK_MULTIPLE` | `1.0` | Start trailing SL after profit reaches 1× initial risk |
| `TRAIL_STEP_PCT` | `50.0%` | Trail SL by 50% of unrealised profit |
| `SLIPPAGE_PCT` | `0.15%` | Simulated slippage on dry-run entries |
| `TARGET_DECAY_AFTER_HOUR` | `14` | After 2 PM, start reducing targets (24h format) |
| `TARGET_DECAY_PCT` | `40.0%` | How much to reduce targets after decay hour |
| `MIN_MINUTES_FOR_ENTRY` | `60` | Don't open new trades if fewer than this many min remain |
| `V2_CANDLE_RESCAN_MINUTES` | `15` | V2 only: how often to re-run candle analysis (free, no Claude cost) |
| `V2_MIN_SCORE` | `2.0` | V2 only: minimum technical score to pass pre-filter |
| `V2_CANDLE_INTERVAL` | `15minute` | V2 only: primary candle interval for pattern detection |
| `OPPORTUNITY_RESCAN_MINUTES` | `30` | V2 only: scan for new trades every N min when slots are free (0 = disable) |
| `NIFTY_RECHECK_MINUTES` | `15` | V2 only: re-check NIFTY regime every N min during trading (0 = disable) |
| `MIN_BUDGET_UTILISATION_PCT` | `60.0%` | V2 only: auto-boost qty if total deployment is below this % of budget |
| `CLAUDE_PLAN` | `pro` | Claude model tier: free, pro, or max |
| `ZERODHA_PLAN` | `connect_paid` | Zerodha plan: personal_free or connect_paid |

> **Dynamic Budget:** The bot fetches your Zerodha margin (`available.live_balance`) at startup and trades with `min(available_funds, MAX_BUDGET_INR)`. So if you have ₹20K in Zerodha but `MAX_BUDGET_INR = 10,000`, only ₹10K is used. If your balance is below `MIN_BALANCE_TO_TRADE` (₹3K), the bot won't trade (skipped in dry-run mode). Increase `MAX_BUDGET_INR` as your confidence grows.

All settings are thoroughly commented in `config.py` — read the comments for details on each option.

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
│   ├── technical_indicators.py # EMA, RSI, VWAP, SuperTrend, MACD, ORB, Gap, composite scoring
│   ├── order_engine.py      # Order execution, position tracking, SL/target monitoring, P&L + taxes
│   ├── report_writer.py     # Generates .txt reports and .json data dumps
│   └── performance_tracker.py # SQLite database for trade history + portfolio analysis tracking
├── scripts/
│   ├── generate_sheet.py           # Generate TSV spreadsheet from portfolio report (Claude-powered)
│   ├── tax_db.py                   # Shared DB helpers for all tax scripts (migration, FY utils)
│   ├── fill_intraday_ledger.py     # Fill intraday_tax_ledger from live JSONs (auto-runs after each trade day)
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

Folders are created on-demand — only when a report is generated for that period. Files are zero-padded by day (`01`, `02`, … `31`) so they sort chronologically.

> **Re-run protection (Phase 1):** If a report for today already exists, the bot asks for confirmation before overwriting it.

> **Same-day merging (Phase 2):** Running Phase 2 multiple times on the same day merges all sessions into a single combined report with cumulative P&L, % returns on budget, and session markers in the trade log.

The Phase 2 report includes:
- Every trade with entry/exit prices, P&L, and reason (SL/target/review/square-off)
- Full tax breakdown: brokerage, STT, GST, exchange charges, SEBI, stamp duty
- Claude API costs and **net profit after all charges and taxes**

---

## Database

All historical data is stored in a single **SQLite database** at `data/trades.db` (auto-created on first run). Candle cache data is stored separately in `data/candle_cache.db` (also auto-created).

> **Security:** The `data/` directory is excluded from Git via `.gitignore`, **except** `candle_cache.db` which contains only public market data and is safe to commit.

| Table | Phase | What it stores |
|---|---|---|
| `trades` | Phase 2 | Intraday trade results — symbol, side, entry/exit price, qty, P&L, exit reason, market condition |
| `intraday_tax_ledger` | Phase 2 | Intraday tax ledger — see [Taxation](#taxation) section |
| `capital_gains_ledger` | Phase 2 | Capital gains ledger — see [Taxation](#taxation) section |
| `portfolio_analyses` | Phase 1 | Analysis results — symbol, action, conviction, reasoning, horizon, target price, current/invested values, risks |

The bot uses this data to:
- Feed recent performance (win rates, losing stocks) into Claude's stock selection prompt
- Load the previous Phase 1 analysis for comparison (faster than scanning JSON files)
- Track how Claude's recommendations for each stock evolve over time

**Utility scripts:**

| Script | Purpose |
|---|---|
| `python scripts/view_trades.py` | Print all intraday trades — entry/exit, P&L, exit reasons, market conditions, win/loss summary |
| `python scripts/view_analyses.py` | Print all portfolio analyses — action, conviction, status (DONE/PENDING/NOT ACTED), P&L, per-date summary |
| `python scripts/generate_sheet.py` | Generate a TSV spreadsheet from a portfolio report. Uses 1 Claude API call to extract structured fields |
| `python scripts/view_candle_cache.py` | View candle cache — symbols cached, date ranges, OHLCV candles. Use `--symbol RELIANCE --candles` for individual candles |
| `python scripts/import_reports_to_db.py` | One-time import of existing JSON report files into the DB. Safe to re-run — skips dates already imported |

```bash
# ── View trades ──────────────────────────────────────────
python scripts/view_trades.py

# ── View analyses ────────────────────────────────────────
python scripts/view_analyses.py

# ── Generate spreadsheet ─────────────────────────────────
python scripts/generate_sheet.py                        # today's report
python scripts/generate_sheet.py 2026-03-16             # specific date
python scripts/generate_sheet.py --list                 # list available dates

# ── View candle cache ────────────────────────────────────
python scripts/view_candle_cache.py                     # summary (all symbols)
python scripts/view_candle_cache.py --symbol RELIANCE   # one symbol
python scripts/view_candle_cache.py --symbol RELIANCE --candles          # OHLCV candles (last 20)
python scripts/view_candle_cache.py --symbol RELIANCE --candles --last 50  # more candles
python scripts/view_candle_cache.py --interval day      # filter by interval

# ── Import old reports ───────────────────────────────────
python scripts/import_reports_to_db.py                  # one-time, safe to re-run
```

Or query directly:
```bash
sqlite3 data/trades.db "SELECT symbol, COUNT(*) as trades, ROUND(AVG(pnl),2) as avg_pnl FROM trades GROUP BY symbol;"
```

---

## Taxation

Intraday equity trading in India has specific tax implications. The bot tracks all charges and generates tax-ready data.

### How intraday trading is taxed

- **Intraday (speculative) income** — classified as "speculative business income" under Section 43(5) of the Income Tax Act. Taxed at your **personal slab rate** (e.g. 30% + 4% cess = 31.2% for income above ₹24L under new regime from FY 2025-26). Reported in **ITR-3 → Schedule BP**. Speculative losses can only be set off against speculative gains and carried forward for 4 years.

- **Short-term capital gains (STCG)** — listed equity held ≤ 12 months. Taxed at a flat **20% + 4% cess = 20.8%** (w.e.f. 23-Jul-2024). Reported in **ITR-3 → Schedule CG**.

- **Long-term capital gains (LTCG)** — listed equity held > 12 months. Taxed at **12.5% + 4% cess = 13.0%** on gains above the **₹1.25 lakh annual exemption** (Section 112A).

- **Deductible expenses** — brokerage, STT, exchange transaction charges, GST, SEBI charges, stamp duty, internet/software costs, and platform subscriptions (e.g. Zerodha Kite Connect ₹500/month) can be claimed as business expenses against speculative income.

For a comprehensive guide covering ITR form selection, advance tax deadlines, loss carry-forward rules, and audit thresholds, see **[docs/TAX_GUIDE.md](docs/TAX_GUIDE.md)**.

### Tax database tables

| Table | What it stores |
|---|---|
| `intraday_tax_ledger` | Live intraday trades with per-trade charges, net P&L, and a `verified` flag (unverified → verified after Zerodha xlsx confirmation) — for ITR-3 Schedule BP |
| `capital_gains_ledger` | Short-term and long-term capital gains from Zerodha Tax P&L xlsx — entry/exit dates, holding period, FMV, taxable profit, all charges — for ITR-3 Schedule CG |

### Tax scripts

| Script | Purpose |
|---|---|
| `python scripts/fill_intraday_ledger.py` | Fill `intraday_tax_ledger` from live trading JSONs (marks as `unverified`). Auto-runs after each live trade day |
| `python scripts/import_zerodha_taxpnl.py` | Import Zerodha Tax P&L xlsx — verifies/corrects intraday data + imports short/long-term capital gains |
| `python scripts/view_intraday_ledger.py` | View intraday trades with entry/exit prices, charges, net P&L, and verified status |
| `python scripts/view_capital_gains_ledger.py` | View capital gains trades — filterable by `--type short_term` or `--type long_term` |
| `python scripts/tax_summary.py` | Combined tax summary — speculative income, STCG, LTCG, estimated tax, deductible expenses |

### Tax workflow

**Daily (automatic):** After each live trading day, the bot auto-fills `intraday_tax_ledger` with trades marked as `unverified`.

**Periodically (manual):** Download the Zerodha Tax P&L report from [Console → Tax P&L](https://console.zerodha.com/reports/taxpnl), place the xlsx in `data/ZerodhaTaxPL/`, and run the import script. This verifies/corrects intraday data and imports capital gains.

```bash
# Step 1: Fill intraday ledger from live trading JSONs
python scripts/fill_intraday_ledger.py
python scripts/fill_intraday_ledger.py --fy 2025    # specific FY
python scripts/fill_intraday_ledger.py --all         # all FYs

# Step 2: Import Zerodha Tax P&L xlsx (verify intraday + import capital gains)
python scripts/import_zerodha_taxpnl.py
python scripts/import_zerodha_taxpnl.py data/ZerodhaTaxPL/your-file.xlsx

# Step 3: View your data
python scripts/view_intraday_ledger.py               # intraday trades
python scripts/view_intraday_ledger.py --fy 2025
python scripts/view_capital_gains_ledger.py           # capital gains
python scripts/view_capital_gains_ledger.py --type short_term
python scripts/view_capital_gains_ledger.py --type long_term

# Step 4: Tax summary
python scripts/tax_summary.py                        # both intraday + CG
python scripts/tax_summary.py --intraday              # intraday only
python scripts/tax_summary.py --capital-gains          # capital gains only
python scripts/tax_summary.py --fy 2025
```

### Tax rates in config.py

| Setting | Default | What it controls |
|---------|---------|------------------|
| `TAX_RATE_PCT` | `30.0` | Your income tax slab rate (for speculative income) |
| `TAX_CESS_PCT` | `4.0` | Health & education cess on tax |
| `STCG_TAX_RATE_PCT` | `20.0` | Short-term capital gains flat rate |
| `LTCG_TAX_RATE_PCT` | `12.5` | Long-term capital gains flat rate |
| `LTCG_EXEMPTION_LIMIT` | `125000.0` | Annual LTCG exemption (₹1.25 lakh) |

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
| Zerodha brokerage | ₹20 or 0.03% per order | Per trade |
| STT, GST, stamp duty, etc. | ~0.05-0.1% of turnover | Per trade |

To be profitable, daily gross trading profits need to exceed ~₹50-100 in Claude API costs plus ~₹23/day amortised Zerodha subscription.

---

## Safety Features

- **Dry-run mode** (default) — no real orders, simulated P&L on live prices with slippage modelling
- **Circuit breaker** — stops trading if daily loss exceeds threshold; resumes after cooldown with reduced budget
- **Budget cap** — never exceeds `MAX_BUDGET_INR`
- **Loss-adjusted sizing** — reduces position sizes after realised losses (budget shrinks by day's losses)
- **Smart sizing** — auto-reduces qty to fit remaining budget instead of rejecting the trade
- **Re-entry limit** — blocks repeated entries into the same stock after stop-losses (`MAX_REENTRIES_PER_STOCK`)
- **Min balance check** — won't trade live if Zerodha balance is below `MIN_BALANCE_TO_TRADE`
- **ATR-based dynamic stop-losses** — data-driven SL/target using 15-minute intraday candles, capped at 2.5%, picks the tighter of ATR vs Claude SL
- **Auto trailing stop-loss** — rule-based SL tightening as positions move in profit
- **Delayed entry filter** — skips indecisive stocks that haven't moved after market open
- **Market condition awareness** — detects high-volatility regimes and adjusts position sizing
- **Performance memory** — learns from past trades via SQLite DB to avoid repeating mistakes
- **Order API retry + circuit breaker** — each Zerodha order retries 3× with backoff; 3 consecutive failures = stop Claude calls, square off, shutdown
- **Market protection on orders** — all MARKET and SL-M orders include `market_protection = -1`, a Zerodha-mandated safeguard (w.e.f. April 2026) that caps execution at exchange-defined price bands to prevent runaway fills. Requires `kiteconnect ≥ 5.1.0`
- **Fill price sanity check** — rejects corrupted fill prices (>5% deviation from expected quote)
- **Time-decay targets** — reduces targets after 2 PM to lock in profits before square-off
- **Late entry guard** — blocks new positions when insufficient time remains in session
- **Session-aware re-scans** — mid-day re-scans account for current P&L and traded symbols
- **Anti-panic exit** — Claude review rule prevents premature exits before SL is actually hit
- **Action tracking** — tracks whether you acted on each recommendation (PENDING → DONE / NOT ACTED)
- **Graceful shutdown** — Ctrl+C squares off all positions before exiting
- **Existing holdings are READ-ONLY** — the bot only trades with the managed budget pool
- **NSE holiday calendar** — handles weekends, holidays, late starts, and token expiry automatically

---

## Disclaimer

This software is for educational and experimental purposes. Stock market trading involves substantial risk of loss. Past performance (including dry-run results) does not guarantee future results. Use at your own risk. The authors are not responsible for any financial losses incurred from using this software.
