# ================================================================
# config.py
# ================================================================
# Single source of truth for every plan-related decision.
#
# TO UPGRADE PLANS: edit CLAUDE_PLAN or ZERODHA_PLAN below.
# Nothing else in the codebase needs to change — every class
# reads from Config.claude() and Config.zerodha().
#
# PHASE 2 SETTINGS are at the bottom of this file.
# They control the intraday trading bot: budget, timing, polling
# intervals, dry-run mode, cost params for P&L, etc.
# ================================================================

import os
from dotenv import load_dotenv

load_dotenv()


class Config:

    # ── Edit these two lines when you upgrade plans ───────────────

    # Options: "free" | "pro" | "max"
    CLAUDE_PLAN: str  = "pro"

    # Options: "personal_free" | "connect_paid"
    ZERODHA_PLAN: str = "connect_paid"

    # ── These rarely need editing ─────────────────────────────────

    # DYNAMIC BUDGET: The bot fetches your actual Zerodha account
    # balance at startup and displays it. The trading budget is:
    #   min(available_funds, MAX_BUDGET_INR)
    # So even if you have ₹50K in Zerodha, the bot only risks ₹10K.
    # Increase this when you're confident in the bot's performance.
    #
    # MAX_BUDGET_INR: absolute cap on how much capital the bot can
    # deploy in a single day, regardless of account balance.
    MAX_BUDGET_INR: int = 10_000

    # MIN_BALANCE_TO_TRADE: minimum Zerodha account balance required
    # to start trading. If your funds are below this, the bot logs
    # the balance and exits without trading. Prevents micro-trades
    # that get eaten by brokerage and taxes.
    # In DRY RUN mode, this check is skipped (only a warning is shown).
    MIN_BALANCE_TO_TRADE: int = 3_000

    # Scheduling: "manual" means you run it yourself.
    # "daily" / "weekly" automation comes in Phase 3.
    ANALYSIS_FREQUENCY: str = "manual"

    # API keys — loaded from your .env file
    ZERODHA_API_KEY:    str = os.getenv("ZERODHA_API_KEY",    "")
    ZERODHA_API_SECRET: str = os.getenv("ZERODHA_API_SECRET", "")
    CLAUDE_API_KEY:     str = os.getenv("CLAUDE_API_KEY",     "")

    # ══════════════════════════════════════════════════════════════
    # PHASE 2 — INTRADAY TRADING BOT SETTINGS
    # ══════════════════════════════════════════════════════════════

    # ── Dry Run Mode ──────────────────────────────────────────────
    # True  = orders are LOGGED but never sent to Zerodha.
    #         Position tracking and P&L use real live prices.
    #         Safe to run anytime — no money at risk.
    # False = LIVE TRADING. Orders are placed on Zerodha for real.
    #         Only set this after you've reviewed dry-run results
    #         and are comfortable with the bot's decisions.
    DRY_RUN: bool = True

    # ── Market Timing (IST) ──────────────────────────────────────
    # The bot waits until MARKET_OPEN_HOUR:MARKET_OPEN_MINUTE to
    # start entering trades. It squares off all positions at
    # SQUARE_OFF_HOUR:SQUARE_OFF_MINUTE and stops monitoring.
    #
    # Indian market hours: 9:15 AM – 3:30 PM IST.
    # Square-off is set to 3:10 PM to avoid last-minute illiquidity.
    # Pre-market scan happens 15 min before open (at 9:00 AM).
    #
    # Changing these:
    #   - Moving MARKET_OPEN earlier → bot enters trades sooner
    #     (risky, opening volatility can cause whipsaws)
    #   - Moving SQUARE_OFF later → closer to 3:30 hard cutoff
    #     (risky, Zerodha auto-squares at 3:20 with penalty)
    MARKET_OPEN_HOUR:   int = 9
    MARKET_OPEN_MINUTE: int = 15
    SQUARE_OFF_HOUR:    int = 15
    SQUARE_OFF_MINUTE:  int = 10
    PRE_MARKET_MINUTES_BEFORE: int = 15   # scan starts this many min before open
    CUTOFF_MINUTES_BEFORE_CLOSE: int = 30   # skip trading if less than this many min to square-off

    # ── Polling & Claude Review Intervals ─────────────────────────
    # PRICE_POLL_SECONDS: how often to check Kite quotes for SL/target hits.
    #   Lower = faster reaction to price moves, but more API calls.
    #   Kite rate limit: ~3 calls/sec. 30s is very safe.
    #   Range: 10–60 recommended.
    #
    # CLAUDE_REVIEW_MINUTES: how often Claude re-evaluates open positions.
    #   Lower = more adaptive, but costs more in Claude API credits.
    #   Each review call ≈ ₹2-4 on Pro plan.
    #   30 min = ~12 calls/day ≈ ₹25-50/day in Claude costs.
    #   15 min = ~24 calls/day ≈ ₹50-100/day. Only if budget is large.
    PRICE_POLL_SECONDS:     int = 10
    CLAUDE_REVIEW_MINUTES:  int = 25

    # ── Stock Universe ────────────────────────────────────────────
    # Which stocks Claude can pick from for intraday trades.
    # Options: "NIFTY50" | "NIFTY100" | "NIFTY200" | "CUSTOM"
    #
    # NIFTY50  → top 50 liquid stocks, tight spreads, safest
    # NIFTY100 → more variety, slightly wider spreads
    # NIFTY200 → widest pool, some less liquid mid-caps
    # CUSTOM   → uses CUSTOM_WATCHLIST below (your hand-picked list)
    #
    # For ₹10K budget, NIFTY50 is recommended — most liquid, lowest
    # impact cost, tightest bid-ask spreads for intraday.
    SCAN_UNIVERSE: str = "NIFTY50"

    # Only used when SCAN_UNIVERSE = "CUSTOM".
    # Add NSE symbols you want the bot to consider.
    CUSTOM_WATCHLIST: list[str] = [
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    ]

    # ── Position Limits ───────────────────────────────────────────
    # MAX_POSITIONS: max number of stocks to hold simultaneously.
    #   More positions = more diversified but less capital per trade.
    #   With ₹10K, 3-5 positions means ₹2K-3.3K per trade.
    #
    # MAX_POSITION_PCT: max % of budget allocated to one stock.
    #   40 = no single stock gets more than 40% of your capital.
    #   Prevents concentration risk if Claude is very bullish on one pick.
    MAX_POSITIONS:    int = 5
    MAX_POSITION_PCT: int = 40

    # MAX_REENTRIES_PER_STOCK: max number of times the bot can enter
    #   the same stock in a single day. Prevents Claude from repeatedly
    #   re-entering a stock that keeps hitting stop-loss.
    #   2 = allow one re-entry after the first trade closes.
    #   Set to 0 for unlimited (not recommended).
    MAX_REENTRIES_PER_STOCK: int = 2

    # ── Risk Management ───────────────────────────────────────────
    # DEFAULT_STOP_LOSS_PCT: fallback stop-loss if Claude doesn't set one.
    #   1.5 = exit if stock drops 1.5% from entry price.
    #   Lower = less risk per trade, but more frequent stop-outs.
    #   Higher = more room for volatility, but bigger losses when wrong.
    #
    # DEFAULT_TARGET_PCT: fallback profit target if Claude doesn't set one.
    #   2.0 = book profits when stock rises 2% from entry.
    #   Higher = bigger wins but fewer trades hit target.
    #
    # MAX_LOSS_PER_DAY_PCT: circuit breaker — stops all trading if total
    #   daily loss exceeds this % of budget.
    #   3.0 on ₹10K = stops trading after ₹300 total loss.
    #   Set to 0 to disable the circuit breaker (not recommended).
    DEFAULT_STOP_LOSS_PCT: float = 1.5
    DEFAULT_TARGET_PCT:    float = 2.0
    MAX_LOSS_PER_DAY_PCT:  float = 3.0

    # ── Trailing Stop-Loss (auto, rule-based) ──────────────────
    # TRAIL_AFTER_RISK_MULTIPLE: once the price moves this many
    #   multiples of the SL distance in your favour, trailing kicks in.
    #   1.0 = trail starts once profit equals the initial risk.
    #   e.g. entry ₹100, SL ₹98 (risk ₹2). At ₹102 (1×risk profit)
    #   the SL auto-moves to breakeven (₹100).
    #
    # TRAIL_STEP_PCT: after the initial trail-to-breakeven,
    #   the SL is moved up to lock in this % of current profit.
    #   50 = SL always sits at 50% of the way from entry to current price.
    #   e.g. entry ₹100, current ₹106 → SL moves to ₹103 (50% of ₹6 gain).
    TRAIL_AFTER_RISK_MULTIPLE: float = 1.0
    TRAIL_STEP_PCT:            float = 50.0

    # ── Dry-Run Realism ──────────────────────────────────────────
    # SLIPPAGE_PCT: simulated slippage added to entries and exits
    #   in dry-run mode. Makes simulated P&L more realistic.
    #   0.05 = 0.05% adverse fill on each trade.
    #   For a ₹1,000 stock: ₹0.50 worse per share per trade.
    SLIPPAGE_PCT: float = 0.05

    # ══════════════════════════════════════════════════════════════
    # COST & TAX PARAMETERS (for P&L calculation)
    # ══════════════════════════════════════════════════════════════
    # These are used to calculate the REAL net profit after all
    # charges. Update if Zerodha changes their fee structure.
    #
    # Source: https://zerodha.com/charges
    # All values are as of March 2026. Verify before live trading.
    #
    # ZERODHA_BROKERAGE_PER_ORDER:
    #   ₹20 flat per executed order (buy or sell), or 0.03% of
    #   turnover — whichever is LOWER. For small orders (<₹66,667),
    #   0.03% is lower. The code calculates both and uses the min.
    #
    # STT_SELL_PCT: Securities Transaction Tax — 0.025% on SELL side
    #   only for intraday equity. Charged by the exchange.
    #
    # EXCHANGE_TXN_PCT: NSE transaction charge — 0.00307% on turnover.
    #   BSE is 0.00375% but we trade NSE by default.
    #
    # GST_PCT: 18% GST on (brokerage + SEBI charges + exchange transaction charges).
    #
    # SEBI_CHARGE_PER_CR: ₹10 per crore of turnover. Negligible for
    #   small trades but included for accuracy.
    #
    # STAMP_DUTY_BUY_PCT: 0.003% on BUY side only. State-level charge.
    #
    # DP_CHARGES: Not applicable for intraday (no delivery). Set to 0.
    ZERODHA_BROKERAGE_FLAT:     float = 20.0
    ZERODHA_BROKERAGE_PCT:      float = 0.03
    STT_SELL_PCT:               float = 0.025
    EXCHANGE_TXN_PCT:           float = 0.00307
    GST_PCT:                    float = 18.0
    SEBI_CHARGE_PER_CR:         float = 10.0
    STAMP_DUTY_BUY_PCT:        float = 0.003

    # Monthly fixed costs — shown as FYI in reports.
    # ZERODHA_MONTHLY_COST: Kite Connect subscription (₹500/month).
    #   This is a MONTHLY cost, NOT deducted from daily P&L.
    #   It's shown as an informational line in the report so you can
    #   gauge whether the bot is covering subscription costs over time.
    # CLAUDE_COST_PER_CALL: estimated ₹ per Claude API call on Pro plan.
    #   This IS deducted from daily P&L because it's a per-use cost.
    ZERODHA_MONTHLY_COST:  float = 500.0
    CLAUDE_COST_PER_CALL:  float = 3.0   # avg ₹3 per Claude API call on Pro

    # ══════════════════════════════════════════════════════════════
    # NSE MARKET HOLIDAY CALENDAR — 2026
    # ══════════════════════════════════════════════════════════════
    # Source: https://zerodha.com/marketintel/holiday-calendar/
    # Zerodha has no API for holidays, so this list is maintained
    # manually. UPDATE THIS LIST every January for the new year.
    #
    # The bot uses this + weekday check to determine if today is a
    # trading day. On non-trading days, it shows a countdown timer
    # to the next market open instead of wasting Claude API calls.
    #
    # Format: list of "YYYY-MM-DD" strings.
    # Only include weekday holidays — weekends are auto-detected.
    NSE_HOLIDAYS_2026: list[str] = [
        "2026-01-15",  # Municipal Corporation Elections in Maharashtra
        "2026-01-26",  # Republic Day
        "2026-03-03",  # Holi
        "2026-03-26",  # Shri Ram Navami
        "2026-03-31",  # Shri Mahavir Jayanti
        "2026-04-03",  # Good Friday
        "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
        "2026-05-01",  # Maharashtra Day
        "2026-05-28",  # Bakri Eid
        "2026-06-26",  # Moharram
        "2026-09-14",  # Ganesh Chaturthi
        "2026-10-02",  # Mahatma Gandhi Jayanti
        "2026-10-20",  # Dussehra
        "2026-11-10",  # Diwali-Balipratipada
        "2026-11-24",  # Prakash Gurpurb Sri Guru Nanak Dev
        "2026-12-25",  # Christmas
    ]

    # ── Plan rule tables ──────────────────────────────────────────
    # Maps plan names → capabilities. Read via claude() / zerodha().

    _CLAUDE_RULES = {
        "free": {
            "analysis_depth":    "basic",
            "include_pe_ratios": False,
            "model":             "claude-haiku-4-5-20251001",
            "max_tokens":        1200,
            "note":              "Haiku model · basic analysis · ~₹1/run",
        },
        "pro": {
            "analysis_depth":    "detailed",
            "include_pe_ratios": True,
            "model":             "claude-sonnet-4-6",
            "max_tokens":        2000,
            "note":              "Sonnet model · detailed analysis · ~₹5/run",
        },
        "max": {
            "analysis_depth":    "full",
            "include_pe_ratios": True,
            "model":             "claude-sonnet-4-6",
            "max_tokens":        3000,
            "note":              "Sonnet model · full analysis · ~₹8/run",
        },
    }

    _ZERODHA_RULES = {
        "personal_free": {
            "live_prices":      False,
            "historical_data":  False,
            "can_place_orders": True,
            "price_source":     "yfinance",
            "note":             "Yahoo Finance prices · 15-min delay · free",
        },
        "connect_paid": {
            "live_prices":      True,
            "historical_data":  True,
            "can_place_orders": True,
            "price_source":     "kite_live",
            "note":             "Live Kite prices + full history · ₹500/month",
        },
    }

    # ── Derived properties ────────────────────────────────────────

    @classmethod
    def claude(cls) -> dict:
        """Returns the resolved Claude plan settings dict."""
        return cls._CLAUDE_RULES[cls.CLAUDE_PLAN]

    @classmethod
    def zerodha(cls) -> dict:
        """Returns the resolved Zerodha plan settings dict."""
        return cls._ZERODHA_RULES[cls.ZERODHA_PLAN]

    @classmethod
    def validate(cls) -> list[str]:
        """
        Checks all required API keys are present.
        Returns list of missing key names — empty means all good.
        """
        missing = []
        if not cls.ZERODHA_API_KEY:    missing.append("ZERODHA_API_KEY")
        if not cls.ZERODHA_API_SECRET: missing.append("ZERODHA_API_SECRET")
        if not cls.CLAUDE_API_KEY:     missing.append("CLAUDE_API_KEY")
        return missing

    @classmethod
    def mismatch_warnings(cls) -> list[str]:
        """
        Returns advisory messages when plans are mismatched in a way
        that wastes money or limits capability.
        """
        warnings = []
        if cls.CLAUDE_PLAN == "free" and cls.ZERODHA_PLAN == "connect_paid":
            warnings.append(
                "Paying for live Zerodha data but Claude Free limits analysis depth. "
                "Upgrade Claude to Pro for full value."
            )
        if cls.CLAUDE_PLAN in ("pro", "max") and cls.ZERODHA_PLAN == "personal_free":
            warnings.append(
                "Claude Pro is ready for daily automation. "
                "Upgrade Zerodha to Connect Paid for real-time prices."
            )
        return warnings
