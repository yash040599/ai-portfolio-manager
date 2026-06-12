# ================================================================
# config.py
# ================================================================
# Single source of truth for every plan-related decision.
#
# TO UPGRADE PLANS: edit AI_PROVIDER, AI_PLAN, or ZERODHA_PLAN below.
# Nothing else in the codebase needs to change — every class
# reads from Config.ai() and Config.zerodha().
#
# PHASE 2 SETTINGS are at the bottom of this file.
# They control the intraday trading bot: budget, timing, polling
# intervals, dry-run mode, cost params for P&L, etc.
# ================================================================

import os
import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

# ── Timezone ──────────────────────────────────────────────────
# All market-timing logic must use IST regardless of system TZ.
IST = ZoneInfo("Asia/Kolkata")

def now_ist() -> datetime.datetime:
    """Returns current time in IST (naive). Use instead of datetime.now()
    to guarantee correct results regardless of system timezone."""
    return datetime.datetime.now(IST).replace(tzinfo=None)


class Config:
    # ══════════════════════════════════════════════════════════════
    # READ ME BEFORE TOUCHING THIS FILE
    # ══════════════════════════════════════════════════════════════
    #
    # 1. The rollout plan is the source of truth for which gates run
    #    at which stage: docs/TRADE_STRATEGY_ROLLOUT.md.
    #    The active rung is `TRADE_STAGE_NAME` (currently `BACKTEST_OPTIMIZED`).
    #    Gates are enabled/disabled based on backtest evidence.
    #    See docs/audit/TRADE_AUDIT_2026-05-15_CHAN_FRAMEWORK.md.
    #
    # 2. ONE disable mechanism per gate. Pick exactly one of:
    #       a) bool `<NAME>_ENABLED: bool = False`  AND code path
    #          checks `if getattr(cfg, "X_ENABLED", False):`
    #       b) numeric sentinel `<NAME>: float = 0` AND code path
    #          checks `if val > 0:` (or analogous wide threshold).
    #    NEVER both. Yesterday's NET_RR_GATE_ENABLED was a duplicate of
    #    the existing `MIN_PROFIT_CHARGE_MULTIPLE = 0` pattern and has
    #    been deleted (2026-05-20). Adding a second mechanism is the
    #    pattern that grows config sprawl.
    #
    # 3. A `getattr` fallback default must match the S0-disabled value.
    #    `getattr(cfg, "X_ENABLED", True)` when the config says False is
    #    a latent bug — if the attribute ever disappears in a refactor,
    #    the gate silently re-activates. The 2026-05-20 audit fixed
    #    three of these (REJECTION_AUDIT_ENABLED, SECTOR_CASCADE_*,
    #    VIX_SPIKE_*); follow the same default-False convention here.
    #
    # 4. Before adding a new knob: check the rollout plan. If the new
    #    behaviour isn't on the ladder, it doesn't belong here. Either
    #    add it to the plan first, or put it in the module that owns
    #    the feature (e.g. dashboard-only knobs live in
    #    `modes/dashboard/<feature>_config.py`, not on this class).
    #
    # 5. Decision-history comments are valuable but heavy. New entries
    #    should reference a roadmap item or audit number in one line;
    #    long-form rationale belongs in docs/TRADE_EVOLUTION.md or the
    #    relevant audit file under docs/audit/.

    # ── Edit these lines when you upgrade plans ──────────────────

    # Options: "gemini" | "gpt" | "claude"
    # Controls which LLM provider is used for ALL AI features.
    AI_PROVIDER: str = "gemini"

    # Options: "basic" | "detailed" | "full"  (scales prompt depth + max_tokens)
    AI_PLAN: str = "detailed"

    # Legacy alias — still read by report-writer/display code.
    CLAUDE_PLAN: str = "detailed"

    # Options: "personal_free" | "connect_paid"
    ZERODHA_PLAN: str = "connect_paid"

    # ── These rarely need editing ─────────────────────────────────

    # DYNAMIC BUDGET: The bot fetches your actual Zerodha account
    # balance at startup and displays it. The trading budget is:
    #   min(available_funds, MAX_BUDGET_INR)
    # So even if you have Rs.50K in Zerodha, the bot only risks MAX_BUDGET_INR.
    # Increase this when you're confident in the bot's performance.
    #
    # MAX_BUDGET_INR: absolute cap on how much capital the bot can
    # deploy in a single day, regardless of account balance.
    MAX_BUDGET_INR: int = 50_000

    # ── Capital-deployment ladder (Dashboard Roadmap D1, expanded D6) ──
    # Mechanical scaling rule: the dashboard recommends moving to the
    # next rung only when the current rung has been held long enough
    # AND every metric threshold is met. Read-only — the dashboard
    # consumes this; the trading bot doesn't.
    # Each entry: {budget, win_rate_min, profit_factor_min, max_dd_pct,
    #              weeks_required}.
    CAPITAL_LADDER: list = [
        {"budget":    50_000, "win_rate_min": 0.50, "profit_factor_min": 1.4, "max_dd_pct": 0.08, "weeks_required":  1},
        {"budget":  1_00_000, "win_rate_min": 0.50, "profit_factor_min": 1.4, "max_dd_pct": 0.08, "weeks_required":  4},
        {"budget":  2_50_000, "win_rate_min": 0.52, "profit_factor_min": 1.5, "max_dd_pct": 0.07, "weeks_required":  8},
        {"budget":  5_00_000, "win_rate_min": 0.55, "profit_factor_min": 1.5, "max_dd_pct": 0.07, "weeks_required": 12},
        {"budget": 10_00_000, "win_rate_min": 0.55, "profit_factor_min": 1.6, "max_dd_pct": 0.06, "weeks_required": 24},
    ]

    # MIN_BALANCE_TO_TRADE: minimum Zerodha account balance required
    # to start trading. If your funds are below this, the bot logs
    # the balance and exits without trading. Prevents micro-trades
    # that get eaten by brokerage and taxes.
    # In DRY RUN mode, this check is skipped (only a warning is shown).
    MIN_BALANCE_TO_TRADE: int = 3_000

    # API keys — loaded from your .env file
    ZERODHA_API_KEY:    str = os.getenv("ZERODHA_API_KEY",    "")
    ZERODHA_API_SECRET: str = os.getenv("ZERODHA_API_SECRET", "")
    CLAUDE_API_KEY:     str = os.getenv("CLAUDE_API_KEY",     "")
    GEMINI_API_KEY:     str = os.getenv("GEMINI_API_KEY",     "")
    OPENAI_API_KEY:     str = os.getenv("OPENAI_API_KEY",     "")

    # ── Programmatic Kite login (optional) ───────────────────────
    # If KITE_USER_ID + KITE_PASSWORD are set, the bot tries the
    # streamlined login flow before falling back to the b/m prompt.
    #   - Both set, no TOTP seed  -> ASSISTED  (prompts for 6-digit code only)
    #   - All three set            -> AUTO      (zero-touch; security trade-off,
    #                                            see README §5.4)
    # On any failure the bot falls back to the legacy browser/manual prompt.
    KITE_USER_ID:     str = os.getenv("KITE_USER_ID",     "")
    KITE_PASSWORD:    str = os.getenv("KITE_PASSWORD",    "")
    KITE_TOTP_SECRET: str = os.getenv("KITE_TOTP_SECRET", "").replace(" ", "")

    # ══════════════════════════════════════════════════════════════
    # PHASE 2 — INTRADAY TRADING BOT SETTINGS
    # ══════════════════════════════════════════════════════════════

    # ── Dry Run Mode ──────────────────────────────────────────────
    # Controlled via CLI: pass --dryrun to run without placing orders.
    # When True: orders are LOGGED but never sent to Zerodha.
    #            Position tracking and P&L use real live prices.
    # When False (default): LIVE TRADING — orders placed on Zerodha.
    # Do NOT edit this here — use: python main.py --mode trade --dryrun
    DRY_RUN: bool = False

    # Path to the dry-run analysis database (separate from live trades.db).
    TRADE_ANALYSIS_DB_PATH: str = os.path.join("data", "trade_analysis.db")

    # ── Live Trading ─────────────────────────────────────────────
    # Set to False for live trading, True to block real orders.
    # Use --dryrun CLI flag for simulation instead of editing this.
    TRADE_LIVE_TRADING_PAUSED: bool = False

    # ── Strategy Profile ─────────────────────────────────────────
    # Controls which alpha-selection logic runs in NoAI mode.
    # Version scheme: NOAI_GAP_AND_GO_X.Y
    #   X = strategy logic change (entry timing, new filters, etc.)
    #   Y = parameter tuning within the same logic
    #
    # NOAI_LEGACY_FULL:       blended score from all indicators
    # NOAI_GAP_AND_GO:        alias for NOAI_GAP_AND_GO_1.0 (backward compat)
    # NOAI_GAP_AND_GO_1.0:    original gap-and-go (OOS PF 1.28). Enters at
    #                         09:30 using LTP. No gap-hold or score checks.
    # NOAI_GAP_AND_GO_1.1:    hardened gap-and-go. Entry delayed to 09:45
    #                         (09:30 candle close), gap-hold confirmation,
    #                         score-contradiction filter, regime filter.
    TRADE_STRATEGY_PROFILE: str = "NOAI_GAP_AND_GO_1.1"

    # ── Gap-and-Go Strategy Config ───────────────────────────────
    # Shared params used by ALL gap-and-go versions (1.0, 1.1, …).
    GAP_GO_MIN_GAP_PCT: float = 1.0       # minimum gap % from prev close
    GAP_GO_MAX_GAP_PCT: float = 5.0       # reject extreme gaps (likely corporate action)
    GAP_GO_VOLUME_MULTIPLE: float = 2.0   # first-candle vol > this × 20-day avg
    GAP_GO_DAILY_CAP: int = 2             # max trades per day (sweep: PF drops with more)
    GAP_GO_SQUARE_OFF_HOUR: int = 13      # gap signal fades by midday (sweep: 13:00 PF 1.34 > 14:00 PF 1.28)
    GAP_GO_SQUARE_OFF_MINUTE: int = 0
    GAP_GO_SKIP_RANGE_REGIME: bool = True  # skip RANGE days (PF 1.35 vs 1.28)
    # RSI filter for Gap-and-Go (backtest 2026-06-08):
    #   BUY blocked RSI>70: PF 1.37 (+7%), MaxDD 7.93% (-28%), Sharpe +1.38
    #   SELL floor: ALL values harmful (same as legacy). Not enabled.
    GAP_GO_RSI_BUY_CEILING: float = 70.0  # block BUY gap-ups when RSI already overbought
    GAP_GO_RSI_SELL_FLOOR: float = 0.0    # 0 = disabled (backtest: all values worse)

    # ── Gap-and-Go 1.1 enhancements ─────────────────────────────
    # These params only apply when TRADE_STRATEGY_PROFILE contains
    # "NOAI_GAP_AND_GO_1.1" or later. 1.0 ignores them.
    #
    # GAP_GO_ENTRY_AFTER_CANDLE_CLOSE: wait for the 09:30 candle to
    #   close before entering (scan at 09:45 not 09:30). Aligns with
    #   backtest which enters at candles[1]["close"]. The 09:30 LTP
    #   used by 1.0 has look-ahead bias vs the backtest.
    GAP_GO_ENTRY_AFTER_CANDLE_CLOSE: bool = True
    #
    # GAP_GO_GAP_HOLD_MIN_PCT: reject entry if LTP has faded more
    #   than this % from today_open. e.g. 0.3 means if open was 100
    #   and LTP is 99.6 → gap faded 0.4% → skip. BHARTIARTL 2026-06-09
    #   entered at 1822.55 when open was 1837 → faded 0.79% → would
    #   have been rejected.
    #   Backtest sweep: 0.3% PF 1.57, 0.5% PF 1.44, 0.7% PF 1.47.
    #   0 = disabled.
    GAP_GO_GAP_HOLD_MIN_PCT: float = 0.3
    #
    # GAP_GO_SCORE_CONTRADICTION_BLOCK: reject entry when the technical
    #   composite score contradicts the gap direction. e.g. gap-up BUY
    #   with score -3.5 = bearish internals, gap was noise not flow.
    #   SHRIRAMFIN 2026-06-09: score -3.5 on a BUY → would be blocked.
    GAP_GO_SCORE_CONTRADICTION_BLOCK: bool = True
    #
    # GAP_GO_USE_CANDLE_CLOSE_PRICE: use the 09:30 candle's close
    #   price as entry_price instead of current LTP. Matches backtest
    #   exactly. Only meaningful when GAP_GO_ENTRY_AFTER_CANDLE_CLOSE
    #   is True (otherwise the candle hasn't closed yet).
    GAP_GO_USE_CANDLE_CLOSE_PRICE: bool = True

    # ── Alpha Strategies (backtested 2026-05-25) ─────────────────
    # Each can be enabled/disabled independently. When multiple are
    # enabled, signals are combined (a candidate must pass at least
    # one enabled strategy). All are disabled by default pending
    # gate-by-gate backtest optimization.
    #
    # See docs/TRADE_REVAMP_STRATEGIES.md for full specs.
    # See docs/BACKTEST_*.md for backtest results.
    #
    # NOTE: Only config flags exist. Scanning/entry code is NOT
    # implemented for these strategies yet. Code will be written
    # only if the strategy is enabled after the full gate audit.
    # If it stays False, no code is written (avoids dead code).

    # Strategy N1: VWAP Mean-Reversion
    # Backtest result: FAIL (WR 23%, CAGR -39%, PF 0.80)
    # Verdict: DO NOT ENABLE — loses money consistently.
    STRATEGY_VWAP_MR_ENABLED: bool = False

    # Strategy N2: ORB-15 Breakout
    # Backtest result: MARGINAL (WR 55.7%, CAGR -1.4%, PF 0.97)
    # Verdict: DISABLED — near break-even, avg loss > avg win.
    # Could become profitable with tighter SL (not yet tested).
    STRATEGY_ORB15_ENABLED: bool = False

    # Strategy N3: EMA Pullback Momentum
    # Original backtest: PROMISING (WR 42.8%, CAGR +151%, PF 1.07)
    # Re-validation (2026-05-26) with optimized config (RR 1.8, K1=2,
    # sq-off 14:00): PF 0.99 raw, PF 0.65 after costs. Edge does NOT
    # survive. Worse than main scorer (PF 0.86). DO NOT ENABLE.
    STRATEGY_EMA_PULLBACK_ENABLED: bool = False
    STRATEGY_EMA_PULLBACK_MAX_PER_STOCK_PER_DAY: int = 1

    # ── Strategy Stage ────────────────────────────────────────────
    # Stamped into reports for audit trail. No longer gates behavior.
    TRADE_STAGE_NAME: str = "BACKTEST_OPTIMIZED"

    # LOG_DISABLED_GATES is the single override that allows disabled
    # gates to still log a one-line "would have rejected" message.
    # Default False: a disabled gate is fully silent (does not arm,
    # does not run its predicate, does not log).
    LOG_DISABLED_GATES: bool = False

    # ── Market Timing (IST) ──────────────────────────────────────
    # The bot waits until MARKET_OPEN_HOUR:MARKET_OPEN_MINUTE to
    # start entering trades. It squares off all positions at
    # SQUARE_OFF_HOUR:SQUARE_OFF_MINUTE and stops monitoring.
    #
    # Indian market hours: 9:15 AM – 3:30 PM IST.
    # Square-off originally set to 3:10 PM to avoid illiquidity.
    #
    #   BACKTEST L11 (2026-05-26): Swept 13:30–15:10. Every metric
    #   degrades monotonically with later square-off. 14:00 is optimal
    #   (PF 0.85, Sharpe -1.29). 15:10 (was current) gives PF 0.79.
    #   Last 75 min of NSE introduces closing volatility, institutional
    #   rebalancing and profit-taking that hurts intraday positions.
    #   See docs/backtest/BACKTEST_GATE_L11.md.
    #
    # Changing these:
    #   - Moving MARKET_OPEN earlier → bot enters trades sooner
    #     (risky, opening volatility can cause whipsaws)
    #   - Moving SQUARE_OFF later → closer to 3:30 hard cutoff
    #     (risky, Zerodha auto-squares MIS at 3:25 with penalty)
    MARKET_OPEN_HOUR:   int = 9
    MARKET_OPEN_MINUTE: int = 15
    SQUARE_OFF_HOUR:    int = 14
    SQUARE_OFF_MINUTE:  int = 0
    PRE_MARKET_MINUTES_BEFORE: int = 15   # scan starts this many min before open
    CUTOFF_MINUTES_BEFORE_CLOSE: int = 30   # skip trading if less than this many min to square-off

    # ENTRY_DELAY_MINUTES: after market open, observe prices for this many
    #   minutes before entering positions. Only stocks with >0.3% directional
    #   movement from open price are entered. Helps avoid opening whipsaws.
    #   Set to 0 to enter immediately at market open (old behaviour).
    ENTRY_DELAY_MINUTES: int = 5
    ENTRY_MIN_MOVE_PCT:  float = 0.3   # min % move from open to confirm direction

    # ENTRY_DECISION_FLOOR_MINUTES_AFTER_OPEN: HARD floor — no entries
    #   fire before this many minutes after MARKET_OPEN, regardless of
    #   bot start time or ENTRY_DELAY_MINUTES.
    #
    #   Why 15 (i.e. 9:30 IST): the 9:15-9:30 opening range has 1.5-3x
    #   spreads (slippage doubles in the engine), pre-open auction
    #   spillover, no usable VWAP (needs ≥3 candles), and HFT-dominated
    #   flow. Choppy-morning ADX + VWAP entry guards both key off the
    #   post-9:30 window, so pre-9:30 entries skip both protections.
    #
    #   Interaction with ENTRY_DELAY_MINUTES=5: floor wins for early
    #   starts (bot up 9:15-9:25 → entry 9:30), normal observation for
    #   late starts (bot up 9:32 → entry 9:37). The 9:15-9:30 wait isn't
    #   wasted: open prices are captured at start, the directional-move
    #   filter compares 9:15→9:30 (stronger than 9:15→9:20), and the
    #   stale-score guard re-scores against fresh 9:30 candle data.
    #
    #   Set to 0 to disable (not recommended outside backtest sweeps).
    ENTRY_DECISION_FLOOR_MINUTES_AFTER_OPEN: int = 15

    # ── Polling & Claude Review Intervals ─────────────────────────
    # PRICE_POLL_SECONDS: how often to check Kite quotes for SL/target hits.
    #   Lower = faster reaction to price moves, but more API calls.
    #   Kite rate limit: ~3 calls/sec. 30s is very safe.
    #   Range: 10–60 recommended.
    #
    # POSITION_REVIEW_MINUTES: how often the bot re-evaluates open
    #   positions. Drives BOTH modes:
    #     --ai mode: Claude review interval (each call ~Rs.2-4 on Pro).
    #       30 min = ~12 calls/day = ~Rs.25-50/day.
    #     NoAI mode: stagnant-position-exit check interval (free).
    #       Tier 1 + Tier 2 evaluation runs on this cadence.
    #   Lower = more adaptive but, in --ai mode, more API spend.
    #
    #   DECISION HISTORY:
    #     20 min (original, called CLAUDE_REVIEW_MINUTES): Claude cut
    #       winners short (32% win rate on REVIEW_EXIT). Over-reviewing
    #       caused premature exits.
    #     30 min (2026-04-09): Increased to give trades more room.
    #       Trades need time to play out before re-evaluation.
    #     2026-04-20: Renamed CLAUDE_REVIEW_MINUTES →
    #       POSITION_REVIEW_MINUTES because the constant also governs
    #       NoAI's stagnant-exit interval. The old name implied a
    #       Claude-only knob, which masked the fact that lowering it
    #       made NoAI churn faster too.
    PRICE_POLL_SECONDS:     int = 10
    POSITION_REVIEW_MINUTES: int = 30

    # ── Stock Universe ────────────────────────────────────────────
    # Which stocks Claude can pick from for intraday trades.
    # Options: "NIFTY50" | "NIFTY100" | "NIFTY150" | "NIFTY200" | "CUSTOM"
    #
    # NIFTY50  → top 50 liquid stocks, tight spreads, safest
    # NIFTY100 → top 50 + next 50 large caps
    # NIFTY150 → NIFTY100 + next 50 mid caps
    # NIFTY200 → NIFTY150 + next 50 mid caps (widest pool, less liquid tail)
    # CUSTOM   → uses CUSTOM_WATCHLIST below (your hand-picked list)
    #
    # For Rs.10K budget, NIFTY50 is recommended — most liquid, lowest
    # impact cost, tightest bid-ask spreads for intraday.
    # Override per-run with: --nifty 50 | --nifty 100 | --nifty 150 | --nifty 200
    SCAN_UNIVERSE: str = "NIFTY100"

    # Only used when SCAN_UNIVERSE = "CUSTOM".
    # Add NSE symbols you want the bot to consider.
    CUSTOM_WATCHLIST: list[str] = [
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    ]

    # ── Swing — Dip-buy parameters (52-week-high reference) ──────
    # The dip-buy strategy buys a fixed-rupee ticket when a stock's
    # close drops X% below its **rolling 52-week high** (252 trading
    # bars), and sells the entire position when it rises Y% from buy.
    #
    # Current defaults are sourced from the 2026-05-16 V2 run in the
    # sibling `../market-research/results_v2` folder: finite capital
    # (Rs.1L start), Rs.20K lots, sale-proceeds recycling, NIFTY 50
    # equal-weight benchmark, and 121 X/Y combinations. In that more
    # realistic cash-constrained model, the old 18/12 default ranked
    # 64/121 (CAGR 18.47%, alpha -1.75%), while X=10/Y=20 ranked #1
    # (CAGR 21.52%, alpha +1.29%, final value Rs.7.03L).
    #
    # Caveat: V2 is still an ATH-reference research run, while live
    # swing mode uses a rolling 52-week max-close reference per the
    # user's 2026-05-14 request. For a fixed X, the 52w trigger is
    # stricter than or equal to the ATH trigger because the 52w high
    # cannot exceed the ATH, so this is a conservative provisional
    # live retune until S11 lands a full in-repo 52w finite-cap replay.
    #
    #   SWING_DIP_PCT             = 10    -> buy when close <= ref-high * 0.90
    #   SWING_DIP_TARGET_PCT      = 20    -> sell when close >= buy   * 1.20
    #   SWING_DIP_BUY_AMOUNT      = 20000 -> fixed ticket size per dip-buy
    #   SWING_DIP_LOOKBACK_DAYS   = 252  → ~52 weeks of trading bars
    #                                       (use a larger number to widen
    #                                        the reference window;
    #                                        e.g. 750 ≈ 3 years,
    #                                        3650 ≈ 10 years for ATH)
    SWING_DIP_PCT:           float = 10.0
    SWING_DIP_TARGET_PCT:    float = 20.0
    SWING_DIP_BUY_AMOUNT:    float = 20_000.0
    SWING_DIP_LOOKBACK_DAYS: int   = 252

    # Swing scan sizing uses a per-stock ticket amount, not total
    # available Zerodha funds. The user decides how much to deploy per
    # selected idea and can rerun the review with a larger ticket to make
    # higher-priced technical setups feasible.
    SWING_TICKET_AMOUNT:      float = 20_000.0

    # ── Swing — AI overlay cost cap ───────────────────────────────
    # SWING_AI_MAX_CANDIDATES caps how many *accepted* candidates the
    # Claude overlay will process in a single swing run. Without this
    # cap a NIFTY 100 scan that flags ~40 ATH-dip + ~10 technical
    # candidates would cost ~50 × CLAUDE_COST_PER_CALL = Rs.150 per
    # run on the Pro plan, which surprised the user once (a long-
    # running scan was Ctrl+C'd partway and produced no report).
    #
    # The cap takes the highest-priority candidates (priority_rank
    # ascending) so the budget always lands on the strongest signals.
    # Set to a large number (e.g. 100) to disable the cap.
    SWING_AI_MAX_CANDIDATES: int = 15

    # ══════════════════════════════════════════════════════════════
    # OPTIONS MODE — Directional NIFTY option buying (Phase O-4)
    # ══════════════════════════════════════════════════════════════
    # See docs/OPTIONS_ROADMAP.md for the phased rollout plan.
    # Currently: BUY ONLY, dry-run only, 1 lot NIFTY weeklies.
    # Naked selling is hard-blocked in code regardless of config.

    # ── Budget & sizing ───────────────────────────────────────────
    OPTIONS_BUDGET_INR:           int   = 15_000    # Start small (1 lot ~5K-15K premium)
    OPTIONS_MAX_LOTS:             int   = 1         # Scale up with evidence only
    OPTIONS_NIFTY_LOT_SIZE:       int   = 25        # NIFTY lot size (fixed by NSE)
    OPTIONS_INDEX:                str   = "NIFTY"   # Only NIFTY for now

    # ── Risk management ───────────────────────────────────────────
    OPTIONS_SL_PCT_OF_PREMIUM:    float = 30.0      # SL = 30% loss on premium paid
    OPTIONS_TARGET_PCT_OF_PREMIUM: float = 75.0     # Target = 75% gain on premium
    OPTIONS_MAX_LOSS_PER_DAY_PCT: float = 3.0       # Circuit breaker (% of budget)

    # ── Strike selection ──────────────────────────────────────────
    OPTIONS_NIFTY_STRIKE_STEP:    int   = 50        # NIFTY strikes at 50-pt intervals
    OPTIONS_STRIKE_OFFSET_STEPS:  int   = 0         # 0 = ATM, 1 = 1 strike OTM
    OPTIONS_EXPIRY_PREFERENCE:    str   = "WEEKLY"  # Thursday weekly expiry
    OPTIONS_MIN_DTE:              int   = 1         # Min days to expiry (skip 0-DTE)

    # ── VIX filter ────────────────────────────────────────────────
    OPTIONS_VIX_MAX:              float = 25.0      # Skip when VIX > 25 (premiums too rich)

    # ── Timing ────────────────────────────────────────────────────
    OPTIONS_SQUARE_OFF_HOUR:      int   = 14        # 14:00 IST (same as equity)
    OPTIONS_SQUARE_OFF_MINUTE:    int   = 0
    OPTIONS_ENTRY_DELAY_MINUTES:  int   = 15        # Wait 15 min after open (09:30)
    OPTIONS_POLL_SECONDS:         int   = 15        # Premium poll frequency

    # ── Mode ──────────────────────────────────────────────────────
    OPTIONS_DRY_RUN:              bool  = True      # ALWAYS start in dry-run
    OPTIONS_NAKED_SELL_ALLOWED:   bool  = False     # HARD BLOCK — never change

    # ── Position Limits ───────────────────────────────────────────
    # MAX_POSITIONS: auto-set at runtime by dynamic_max_positions().
    #   DO NOT manually edit this — it is overwritten when set_budget() runs.
    #   To force a specific value, set MAX_POSITIONS_OVERRIDE instead.
    MAX_POSITIONS:    int = 3   # runtime default; overwritten by dynamic_max_positions()
    MAX_POSITIONS_OVERRIDE: int = 0  # 0 = auto-scale with budget; >0 = locked manual value
    MAX_POSITION_PCT: int = 40

    # SCORE_WEIGHTED_SIZING_ENABLED: kill-switch for the score-weighted
    #   position-sizing pass in services/stock_scanner_v2._score_weight_sizing.
    #   When True (legacy behaviour, Roadmap #107): per-trade qty is
    #   reweighted by |entry_score| so higher-conviction names get more
    #   capital.
    #   When False (default after Roadmap #258, 2026-05-07): the pass is
    #   a no-op and equal-sizing (one slot per primary candidate, sized
    #   off budget-per-slot) stands.
    #   Why default-OFF: the 2026-05-06 NoAI audit + 9-day rolling ledger
    #   showed score-magnitude is anti-correlated with realised P&L for
    #   the score>=6 cohort (|score|>=9: -Rs.51/trade; |score|<6:
    #   -Rs.0.28/trade). Score-weighting concentrates capital on the
    #   buckets that lose the most. Equal-sizing is the documented
    #   out-of-sample fallback when factor confidence is low
    #   (DeMiguel/Garlappi/Uppal 2009). Re-enable trigger logged as
    #   Roadmap #258R (Awaiting-Data).
    # Backtest audit (2026-05-26): equal sizing only.
    SCORE_WEIGHTED_SIZING_ENABLED: bool = False

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
    #   1.2 = book profits when stock rises 1.2% from entry.
    #   Most NIFTY100 stocks move 1-1.5% net per day; 1.2% is achievable
    #   while 1.5% often results in SQUARE_OFF exits.
    #   Higher = bigger wins but fewer trades hit target.
    #
    # MAX_LOSS_PER_DAY_PCT: circuit breaker — stops all trading if total
    #   daily loss exceeds this % of budget.
    #   3.0 on Rs.10K = stops trading after Rs.300 total loss.
    #   Set to 0 to disable the circuit breaker (not recommended).
    DEFAULT_STOP_LOSS_PCT: float = 1.5
    DEFAULT_TARGET_PCT:    float = 1.2
    MAX_LOSS_PER_DAY_PCT:  float = 3.0

    # ATR_MULTIPLIER: multiplier for ATR to compute dynamic stop-loss.
    #   SL = entry - (ATR_MULTIPLIER * ATR) for longs.
    #   Target = entry + (ATR_MULTIPLIER * RR_TARGET_RATIO * ATR).
    #   Falls back to DEFAULT_STOP_LOSS_PCT if historical data is unavailable.
    #
    #   BACKTEST (2026-05-25): Swept 0.5-3.0. Wider SL = better PF.
    #   ATR 2.0 has best per-trade expectancy (-0.092%). Tight SL (0.5)
    #   causes whipsaw churn (75K trades, PF 0.46). See BACKTEST_GATE_E1.md.
    ATR_MULTIPLIER: float = 2.0
    ATR_PERIOD:     int   = 14    # number of candles for ATR calculation
    ATR_INTERVAL:   str   = "15minute"  # candle interval: "15minute" for intraday
    MAX_INTRADAY_SL_PCT: float = 2.5  # hard cap: SL never wider than 2.5% for intraday

    # MIN_SL_DISTANCE_PCT: floor for SL distance. ATR on high-priced
    #   stocks can produce absurdly tight SLs (0.4-0.6%) that wick out
    #   on normal intraday noise. When ATR produces something tighter,
    #   the SL is widened to this floor and the target is widened
    #   proportionally to preserve the R:R ratio.
    #   On expiry days, EXPIRY_MIN_SL_DISTANCE_PCT overrides this
    #   (wider floor due to bigger F&O-driven swings).
    MIN_SL_DISTANCE_PCT: float = 0.8

    # ── R:R (Risk:Reward) Settings ────────────────────────────────
    # R:R = (target distance) / (stop distance). ATR produces a base
    # R:R of RR_TARGET_RATIO (1.5:1).
    #
    # Single uniform all-day floor: RR_HARD_FLOOR = 1.3. The previous
    # time-tiered floors and adaptive-relaxation branches were dead
    # code — RR_HARD_FLOOR always won last so the routing only added
    # log noise. Adaptive relaxation ("haven't traded in an hour, drop
    # the bar") is the same instinct that bankrupts retail traders.
    #
    # KEPT: RR_GIVEUP_AFTER_FAILS — after N zero-entry scans we stop
    # trading entirely ("today is not a trading day" signal).
    #   BACKTEST (2026-05-25): Swept 1.0-3.0. Higher RR = better PF.
    #   RR 2.5 has best PF but requires 2.5% intraday move — unrealistic
    #   for most NIFTY 50 stocks (typical daily range 1.5-2.5%).
    #   RR 1.8 is the practical optimum: target needs ~1.8% move which
    #   is achievable on trending days. PF 0.73 vs baseline 0.71.
    #   See BACKTEST_GATE_E1.md.
    RR_TARGET_RATIO:       float = 1.8   # base R:R from ATR (target = SL * this)
    RR_HARD_FLOOR:         float = 1.3   # always-on R:R floor (uniform all day)
    RR_GIVEUP_AFTER_FAILS: int   = 5     # zero-entry scans before stopping for the day

    # ── Trailing Stop-Loss (auto, rule-based) ──────────────────
    # TRAIL_AFTER_RISK_MULTIPLE: how many multiples of initial risk
    #   the position must profit before trailing starts.
    #   1.5 = trail starts at 1.5× risk (e.g. risk Rs.2 → trail at Rs.3 profit).
    #   At trigger: exits 1/3 qty (partial profit) + moves SL.
    #
    # TRAIL_STEP_PCT: % of unrealised profit locked in by the trailing SL.
    #   50 = SL sits halfway between entry and current price.
    #   e.g. entry Rs.100, current Rs.106 → SL at Rs.103 (50% of Rs.6 gain).
    #
    #   DECISION HISTORY (do NOT change without backtesting evidence):
    #     65% (commit 4444248, 2026-03-16): Set to 65% after observing
    #       that loose trailing let winning trades reverse into losses.
    #       Rationale: lock more profit, accept fewer home runs.
    #     50% (commit 418d668, 2026-04-08): Reduced to 50% based on
    #       Indian financial analyst review. Finding: 65% is too tight
    #       for NSE intraday where stocks commonly pull back 0.5-0.7%
    #       before continuing. 65% triggered trail exits on normal
    #       retracements, converting potential 1.5% winners into 0.3%
    #       winners. 50% gives enough room for typical pullbacks while
    #       still protecting the majority of the gain.
    #     OPTIMAL RANGE: 40-60%. Below 40% risks giving back too much;
    #       above 60% chops out of winners on normal volatility.
    #       Backtest on ≥50 trades before changing this value.
    # Backtest audit (2026-05-26): trailing-stop / partial-profit disabled.
    TRAIL_AFTER_RISK_MULTIPLE: float = 0.0
    TRAIL_STEP_PCT:            float = 50.0

    # ── Bid-Ask Spread Check ─────────────────────────────────────
    # MAX_SPREAD_PCT: skip stocks with bid-ask spread wider than this %.
    #   Wide spreads eat into tight ATR targets. 0.3% is typical for
    #   NIFTY100 stocks; illiquid small-caps can be 0.5-1%.
    #   Set to 0 to disable the check.
    #
    #   #236 (analyst pass, 2026-04-27): this is the BASE value. Use
    #   `OrderEngine.effective_max_spread()` instead of reading this
    #   directly so BUDGET_SPREAD_DELTA tightens it for TINY/SMALL
    #   accounts where the per-trade charge hurdle (~0.27% on Rs.50K)
    #   is dangerously close to the spread itself — trades that pay the
    #   spread alone eat the edge before the position even moves.
    MAX_SPREAD_PCT: float = 0.3

    # ── Impact-Cost / Depth Liquidity Check (Roadmap #146) ───────
    # MAX_IMPACT_COST_PCT: skip entries where our full order qty would
    #   fill at a weighted-average price more than this % worse than
    #   LTP, based on top-5 order-book levels.
    #   Catches "paper-thin ask, deep gap to next level" traps that
    #   MAX_SPREAD_PCT alone misses. Fail-open when depth data is
    #   missing/malformed (logs a warning, lets trade through).
    #   Set to 0 to disable the check.
    #   0.2% is conservative for NIFTY100 at typical slot size (< Rs.50K);
    #   raise to 0.3-0.4% if you see frequent skips on stocks you want.
    MAX_IMPACT_COST_PCT: float = 0.2

    # ── Broker VWAP Drift Sanity Check (Roadmap #268) ────────────
    # Pure observability gate. Compares our locally-computed session
    # VWAP (from cached 1-min candles in `data/candle_cache.db` via
    # `shared/technical_indicators.py::vwap()`) against Kite's
    # exchange-truth `average_price` field returned in every quote
    # payload, and emits a structured WARN log when the two disagree
    # by more than VWAP_DRIFT_WARN_PCT. Three production gates
    # consume our VWAP — #34 SD bands, #125 trend block,
    # #228 statistical-band consolidation — so a silent cache gap
    # would corrupt all three; this check surfaces the divergence
    # before it bleeds money.
    #
    # NO entry behaviour change: nothing is blocked or admitted on
    # the basis of drift, no score is adjusted. The only signal is a
    # WARN line per drifting candidate during the Pre-filter pass.
    # If WARN lines start appearing on healthy market days, that's
    # the cue to fix the candle-cache pipeline (separate Pending item
    # filed at that point).
    #
    # Threshold rationale: 0.30% is wider than the natural noise
    # floor of two methods averaging the same prints (rounding,
    # candle-bucket boundary, late ingestion of a 1-min bar). On a
    # Rs.1,000 stock that is Rs.3 of VWAP-anchored gates' reference
    # — wide enough that anything above it is a genuine cache /
    # ingestion problem, not a numerical artifact.
    #
    # Set VWAP_DRIFT_CHECK_ENABLED = False to silence the check
    # entirely if the WARN volume becomes noise post-shipping.
    # Backtest audit (2026-05-26): VWAP drift check disabled (observability only).
    VWAP_DRIFT_CHECK_ENABLED: bool = False
    VWAP_DRIFT_WARN_PCT: float = 0.30

    # ── Dry-Run Realism ──────────────────────────────────────────
    # SLIPPAGE_PCT: simulated slippage added to entries and exits
    #   in dry-run mode. Makes simulated P&L more realistic.
    #   0.15 = 0.15% adverse fill on each trade.
    #   For a Rs.1,000 stock: Rs.1.50 worse per share per trade.
    #   Real-world slippage on NSE is typically 0.1-0.3%.
    SLIPPAGE_PCT: float = 0.15

    # ── Time-Decay Targets ────────────────────────────────────────
    # After TARGET_DECAY_AFTER_HOUR (24h format), reduce open position
    # targets by TARGET_DECAY_PCT% to account for less time remaining.
    # e.g. After 2:00 PM, a Rs.100 → Rs.106 target becomes Rs.100 → Rs.103.60
    # Backtest audit (2026-05-26): target decay disabled (TARGET_DECAY_AFTER_HOUR=24 → never trips).
    TARGET_DECAY_AFTER_HOUR: int   = 24     # disabled at S0; was 14 (2 PM IST)
    TARGET_DECAY_PCT:        float = 25.0

    # ── Late Entry Guard ──────────────────────────────────────────
    # MIN_MINUTES_FOR_ENTRY: don't open new positions if fewer than
    # this many minutes remain until square-off. Prevents entering
    # too late when full-day targets are impossible.
    # 45 min = won't enter after ~2:25 PM (with 3:10 square-off).
    # Safe because the late-day loser exit (LOSER_EXIT_HOUR/MINUTE)
    # already protects against late-day drift on open positions.
    MIN_MINUTES_FOR_ENTRY: int = 45

    # ── Late-Day Loser Exit ─────────────────────────────────────
    # LOSER_EXIT_HOUR / MINUTE: after this time, auto-exit any
    #   losing position. Prevents holding losers into the close where
    #   liquidity drops and square-off slippage is higher.
    # Breakeven positions get SL tightened to entry ± 0.1%.
    # NOTE: This is NOT the full square-off (SQUARE_OFF_HOUR:MINUTE).
    #   Winners with active trails keep running until square-off.
    #
    #   BACKTEST L10 Phase 7 (2026-05-26): With K1=2, L10=13:00 is
    #   marginally beneficial. PF flat, Exp +6%, Sharpe +5%, MaxDD -6%.
    #   K1 blocks re-entry so no churn. Cuts losers 1h before close.
    LOSER_EXIT_HOUR:   int = 13
    LOSER_EXIT_MINUTE: int = 0

    # ── Late Entry Target Compression — REMOVED (Roadmap #242) ────
    # Removed 2026-04-27: with always-on RR_HARD_FLOOR=1.3 (#225),
    # cutting default-ATR R:R from 1.5 → 1.20 (afternoon) or 1.125
    # (late) caused systematic rejection of every afternoon entry.
    # Drift control is owned by stagnant-exit (#172), momentum kill
    # (#198), and the open-position TARGET_DECAY_PCT — pre-shrinking
    # entry targets is a retail superstition with no statistical edge.

    # ── Short Position Safety ─────────────────────────────────────
    # SHORT_ENTRY_CUTOFF_HOUR: don't open new SHORT positions after
    #   this hour. Short delivery if cover fails is very expensive
    #   (Rs.500-5000+ penalties). Earlier cutoff gives more time to
    #   handle order failures before Zerodha's 3:25 PM auto-square.
    # Backtest audit (2026-05-26): short cutoff disabled (=16 → never trips before square-off).
    SHORT_ENTRY_CUTOFF_HOUR: int = 16   # disabled at S0; was 13 (1 PM)

    # ── Thursday Expiry Adjustments ───────────────────────────────
    # On weekly F&O expiry Thursdays, NIFTY stocks see wider swings
    # driven by options settlement. All of the values below apply
    # ONLY on Thursdays (auto-detected via weekday check).
    #
    # EXPIRY_ATR_BUMP: added to ATR_MULTIPLIER (wider SLs).
    # EXPIRY_POSITION_REDUCTION: MAX_POSITIONS reduced by this many —
    #   skipped when budget < EXPIRY_POSITION_REDUCTION_MIN_BUDGET so
    #   small accounts keep full slot count for rotation capacity.
    # EXPIRY_SCORE_BUMP: added to MIN_SCORE (demand stronger signals).
    # EXPIRY_STAGNANT_EXTRA_MINUTES: extends stagnant timer on expiry.
    # EXPIRY_ENTRY_DELAY_MINUTES: observation window is "market_open + N"
    #   — at 9:15 start with 30 min → entry at 9:45. At 9:30 start →
    #   also 9:45 (we already observed 15 min). At 10:00 start → late
    #   path uses EXPIRY_ENTRY_DELAY_LATE_FLOOR from current time.
    # EXPIRY_MAX_TRADES_PER_DAY: caps total trades on expiry (each
    #   exit+entry cycle costs ~Rs.36 in charges).
    # EXPIRY_MIN_SL_DISTANCE_PCT: overrides MIN_SL_DISTANCE_PCT —
    #   wider floor on expiry accommodates bigger option-driven swings.
    # Backtest audit (2026-05-26): expiry score/ATR/trade-cap adjustments disabled (=0).
    # Expiry DATE detection (HOLIDAY_SHIFTED_EXPIRY_ENABLED) stays on.
    EXPIRY_ATR_BUMP:                      float = 0.0
    EXPIRY_POSITION_REDUCTION:            int   = 0
    EXPIRY_POSITION_REDUCTION_MIN_BUDGET: float = 100000.0  # Rs.1L
    EXPIRY_SCORE_BUMP:                    float = 0.0
    EXPIRY_STAGNANT_EXTRA_MINUTES:        int   = 15
    EXPIRY_ENTRY_DELAY_MINUTES:           int   = 30
    EXPIRY_ENTRY_DELAY_LATE_FLOOR:        int   = 15
    EXPIRY_MAX_TRADES_PER_DAY:            int   = 0
    EXPIRY_MIN_SL_DISTANCE_PCT:           float = 1.0

    # ── Entry Filter — RSI Contradiction (symmetric) ─────────────
    # Blocks trades that fight or chase extreme RSI readings.
    # Applies EVERY day (not expiry-specific).
    #   SELL blocked when RSI > RSI_SELL_BLOCK_THRESHOLD
    #     → shorting into strong buying pressure
    #   BUY blocked when RSI > RSI_BUY_BLOCK_THRESHOLD
    #     → buying an already-extended overbought move
    #   BUY  blocked when RSI < RSI_BUY_FLOOR_THRESHOLD
    #     → oversold, wait for reversal (DISABLED by backtest G3)
    #   SELL blocked when RSI < RSI_SELL_FLOOR_THRESHOLD
    #     → selling an extended low (DISABLED by backtest G4)
    RSI_SELL_BLOCK_THRESHOLD: float = 100.0
    #   BACKTEST G2 (2026-05-26): Swept 0-85. Gate is inert — removes
    #   <1% of trades, PF identical at 0.71 at every level. Scorer
    #   already avoids shorting into high RSI. DISABLED (100).
    #   See docs/backtest/BACKTEST_GATE_G2.md.
    #   BACKTEST G1 (2026-05-26): Swept 0-85. All ceiling values make
    #   PF WORSE (0.69-0.70 vs baseline 0.71). Removes profitable
    #   momentum trades. RSI>70 intraday = strong uptrend not a sell
    #   signal. Scorer already penalizes high RSI. DISABLED (100).
    #   See docs/backtest/BACKTEST_GATE_G1.md.
    RSI_BUY_BLOCK_THRESHOLD:  float = 100.0

    #   BACKTEST G3 (2026-05-26): Swept 0-40. Inert at 0-25 (no BUY
    #   signals at RSI<25). At 30 (was hardcoded) marginal -1 PF tick.
    #   At 40, slightly worse. DISABLED (0). Oversold BUYs are profitable
    #   intraday (bounce entries). Scorer already penalizes low RSI.
    RSI_BUY_FLOOR_THRESHOLD:  float = 0.0
    #   BACKTEST G4 (2026-05-26): Swept 0-35. SEVERELY HARMFUL.
    #   Current hardcoded 25 drops PF 0.86→0.77 (-10%). Blocking
    #   shorts at low RSI removes profitable downtrend entries.
    #   Intraday low RSI = strong selling = short works. DISABLED (0).
    RSI_SELL_FLOOR_THRESHOLD: float = 0.0

    # ── Entry Filter — VWAP Trend + Extension ────────────────────
    # Two-sided VWAP guard (every day, activates after 10:15 AM when
    # VWAP has enough candles to be stable):
    #   1. Trend-fight block: BUY if price > VWAP_TREND_FIGHT_PCT% BELOW
    #      VWAP, or SELL if above. Fighting institutional flow.
    #   2. Extension-chase block: BUY if price > VWAP_EXTENSION_BLOCK_PCT
    #      ABOVE VWAP, or SELL if below. Mean-reversion risk.
    # Extension block is overridden when |score| >= VWAP_EXT_SCORE_OVERRIDE.
    #
    #   BACKTEST G6 (2026-05-26): Swept 0-1.0%. Gate is inert — PF
    #   stays 0.71, WR 40.5% at every level. Removes <3% of trades.
    #   DISABLED (99.0). See docs/backtest/BACKTEST_GATE_G6G7.md.
    VWAP_TREND_FIGHT_PCT:     float = 99.0
    #   BACKTEST G7 (2026-05-26): Swept 0-2.0%. ALL values make PF
    #   WORSE (0.66-0.69 vs 0.71). Removes profitable momentum trades.
    #   At 0.8% (was current) drops PF to 0.67. DISABLED (99.0).
    VWAP_EXTENSION_BLOCK_PCT: float = 99.0
    VWAP_EXT_SCORE_OVERRIDE:  float = 6.0

    # ── Entry Filter — Fresh Reversal Guard ──────────────────────
    # If the composite score just swung hard since the previous scan
    # (large |score_delta|), the setup has only just formed. Trading
    # the first bar of a violent reversal is risky — wait one more
    # scan cycle for confirmation.
    # Applies every day.
    FRESH_REVERSAL_DELTA_THRESHOLD: float = 8.0

    # ── Entry Filter — Gap-Coherence Gate (#173) ─────────────────
    # Pro intraday desks treat gap direction as the strongest single
    # opening signal — it reflects overnight institutional positioning
    # AND the first wave of regular-session order flow. Taking a BUY on
    # a strong gap-DOWN (or SELL on strong gap-UP) means trading against
    # that flow; the score may justify it on indicators alone, but the
    # opening flow rarely V-recovers within the same intraday session.
    #
    # When the trade direction contradicts a STRONG gap signal, require
    # an extra-high score to override. WEAK gaps (low-volume) and NO_GAP
    # are not gated — only the high-conviction GAP_*_STRONG signals.
    #
    # MOTIVATING CASE: HDFCBANK 2026-04-20 — entered BUY at 11:31 with
    #   `Gap GAP_DOWN_STRONG` in rationale and score exactly at the
    #   lunch-lull floor (+6.0). Stopped out at 13:26 for Rs.-155, then
    #   the very next scanner tick scored it -10.0 STRONG_SELL with
    #   EVENING_STAR + BEARISH_HARAMI. Gap direction was the early tell.
    # Backtest audit (2026-05-26): gap-coherence gate disabled.
    GAP_COHERENCE_GATE_ENABLED:  bool  = False
    GAP_COHERENCE_OVERRIDE_SCORE: float = 7.5

    # ── Holiday-Shifted Expiry Detection (Roadmap #41) ──────────
    # NSE weekly F&O expiry is Thursday. When Thursday is a market
    # holiday (Holi, Eid, Republic Day falling on Thu, etc.), the
    # exchange shifts expiry to the prior trading day — normally
    # Wednesday. ~3 days/year. Without this detection the bot uses
    # normal Wednesday gates and misses expiry-day wider SLs / score
    # bumps / trade-cap reductions.
    # Kill-switch: HOLIDAY_SHIFTED_EXPIRY_ENABLED = False reverts to
    # pure-Thursday detection.
    HOLIDAY_SHIFTED_EXPIRY_ENABLED: bool = True

    # ── Circuit-Limit (UC/LC) Entry Guard (Roadmap #180) ─────────
    # Indian equities have a daily ±20% price band ("upper / lower
    # circuit"). Within ~1% of that band the order book becomes
    # one-sided: at UC there are zero asks below the freeze price, at
    # LC zero bids above. SL-M orders cannot fill, MIS positions get
    # auto-squared at 15:20 at whatever desperate price exists, and
    # post-freeze unwinds routinely slip 5-15 Rs/share.
    #
    # Gate: reject BUY when current move >= +(20 - buffer)% from prev
    # close, and SELL when current move <= -(20 - buffer)%. Fail-open
    # if prev_close (ohlc.close) is unavailable in the live quote.
    #
    # Buffer of 1.0% leaves ~50 paise of room on a Rs.500 stock for
    # normal noise without admitting a near-freeze entry.
    # Backtest audit (2026-05-26): circuit-limit guard disabled.
    CIRCUIT_LIMIT_GUARD_ENABLED: bool  = False
    CIRCUIT_LIMIT_BUFFER_PCT:    float = 1.0

    # ── Post-Trade Rejection Audit (read-only, EOD) ───────────────
    # After Step 11 (Zerodha trade verification), the manager parses
    # today's portfolio.log for every entry that was SKIPPED by the
    # order engine (R:R, RVol, ADX, lunch-lull, gap-coherence, etc),
    # fetches each rejected stock's 15:30 close from Zerodha, and
    # prints a verdict table:
    #   AVOIDED_LOSS / AVOIDED_MILD  — gate saved us money
    #   MISSED_PROFIT / MISSED_MILD  — gate may be too strict
    #   NEUTRAL                      — within ±0.5% drift
    # Output is logged live AND appended to the trading report
    # under <!-- REJECTION_AUDIT_BEGIN/END --> markers (idempotent
    # — re-running the audit replaces the old block, never duplicates).
    # This is purely a review aid; never changes positions.
    # Backtest audit (2026-05-26): rejection audit disabled (review aid only).
    REJECTION_AUDIT_ENABLED:     bool  = False

    # Dashboard cumulative-P&L chart overlays a thin vertical line
    # at every trading day where the bot's git SHA changed vs the
    # previous day, with a hover tooltip showing the commit subject.
    # Visual proof of when a strategy change inflected the equity
    # curve. Roadmap D13 + V2 #246. Read by `modes/dashboard/render_html.py`
    # via `modes/dashboard/strategy_versions.py`. Set False to hide overlay
    # without breaking anything else (data still recorded either way).
    DASHBOARD_STRATEGY_VERSION_OVERLAY: bool = True

    # ── Adopted / External Position Handling ─────────────────────
    # When the bot picks up a position it did not originate
    # (load_existing_positions → RESUMED, sync_external_positions
    # → EXTERNAL), it respects a grace window before applying
    # bot-specific forced adjustments.
    # During grace, TIME_DECAY_TARGET and LOSER_EXIT are skipped —
    # software SL, target, trailing, and square-off still apply.
    # Applies every day.
    ADOPTED_POSITION_GRACE_MINUTES: int = 10

    # ── Daily Trade Cap ─────────────────────────────────────────
    # Prevents overtrading churn. Each exit+entry cycle costs ~Rs.36
    # in fixed charges. Set to 0 for unlimited.
    # Portfolio-level: counts ALL positions (open+closed) across all stocks.
    #
    #   BACKTEST (2026-05-25): Swept 2-20. Cap=2 is optimal: PF 0.81
    #   vs baseline 0.71. Reduces trades from ~75/day to ~2/day.
    #   First N signals by entry time (no hindsight). See BACKTEST_GATE_K1.md.
    MAX_TRADES_PER_DAY: int = 2

    # ══════════════════════════════════════════════════════════════
    # V2 — CANDLE STRATEGY SETTINGS (default strategy)
    # ══════════════════════════════════════════════════════════════
    # Pre-filters stocks using candlestick patterns and technical
    # indicators (EMA, RSI, VWAP, SuperTrend) before sending the
    # top candidates to Claude. This gives Claude richer technical
    # context and higher signal-to-noise ratio.
    #
    # These settings apply when running: python main.py --mode trade (default)

    # CANDLE_RESCAN_MINUTES: how often to re-run candle analysis
    # on the universe during monitoring (separate from Claude review).
    # This is FREE (no Claude cost) — just Zerodha historical API calls.
    # Lower = detect new setups faster, but more API calls.
    CANDLE_RESCAN_MINUTES: int = 15

    # ── SuperTrend Parameters ─────────────────────────────────────
    # SuperTrend is the primary trend-following indicator.
    # SUPERTREND_PERIOD: ATR lookback period (on 15-min candles).
    #   7 = 1.75 hour lookback — faster reversals for intraday.
    #   10 = 2.5 hours — original, slower, better for daily charts.
    # SUPERTREND_MULTIPLIER: band width multiplier.
    #   2.0 = tighter bands, flips faster — better for intraday.
    #   3.0 = wider bands, fewer flips — original daily setting.
    SUPERTREND_PERIOD:     int   = 7
    SUPERTREND_MULTIPLIER: float = 2.0

    # ── Exchange SL Orders ────────────────────────────────────────
    # USE_EXCHANGE_SL: place SL-M (stop-loss market) orders on NSE
    #   at trade entry. The exchange triggers the exit instantly when
    #   price breaches the SL — no more 10-second polling delay.
    #   This is the standard approach for algo trading in India.
    #   When trailing, the SL-M order is modified on the exchange.
    #   Set to False to use the legacy software-monitored SL polling.
    USE_EXCHANGE_SL: bool = True

    # ── LIMIT Orders ──────────────────────────────────────────────
    # USE_LIMIT_ORDERS: place LIMIT orders at LTP instead of MARKET.
    #   MARKET orders get adverse fills (Rs.20-40/day slippage on
    #   Rs.18K budget). On liquid NIFTY100 stocks, LIMIT at LTP fills
    #   within seconds. If LIMIT doesn't fill within LIMIT_ORDER_TIMEOUT
    #   seconds, cancel and retry at updated LTP. After LIMIT_MAX_RETRIES
    #   failures, fall back to MARKET. Only for entry orders — exits
    #   always use MARKET for guaranteed fill.
    USE_LIMIT_ORDERS: bool = True
    LIMIT_ORDER_TIMEOUT: int = 8   # seconds to wait for LIMIT fill
    LIMIT_MAX_RETRIES:  int = 2    # LIMIT attempts before MARKET fallback

    # MIN_EXPECTED_PROFIT: skip trades where expected profit (target
    # distance × qty) is less than this amount in Rs.. Prevents
    # entering trades where brokerage + STT eats all the profit.
    # Round-trip charges for small intraday trades ~Rs.40-50.
    #
    # #237 (analyst pass, 2026-04-27): 75 was 2× round-trip charges —
    # too thin once typical NSE 0.05-0.20% spread + first-tick fade is
    # factored in. Industry rule of thumb: hard floor at 3× round-trip
    # charges. Raised to Rs.135. Use OrderEngine.effective_min_profit()
    # instead of reading this constant directly — the helper combines
    # this absolute floor with BUDGET_MIN_PROFIT_DELTA so larger
    # accounts (where charges grow with slot value) raise the bar.
    # Backtest audit (2026-05-26): charge-aware min profit floor disabled (=0).
    MIN_EXPECTED_PROFIT: float = 0.0

    # MIN_SCORE: minimum absolute technical score for a stock to
    # pass the pre-filter. Lower = more candidates for Claude to
    # choose from (more Claude context). Higher = fewer but stronger signals.
    # Range: 1-5 recommended. Default 2 = mild signal required.
    MIN_SCORE: float = 2.0

    # CANDLE_INTERVAL: primary candle interval for pattern detection.
    # Options: "5minute", "10minute", "15minute", "30minute"
    # 15minute = good balance of signal clarity vs responsiveness.
    # 5minute = more signals but noisier patterns.
    CANDLE_INTERVAL: str = "15minute"

    # ── Scan Price Filter ─────────────────────────────────────────
    # Skip stocks outside this price range during scanning.
    # MIN: Very low-price stocks (Rs.10-50) have terrible bid-ask
    #   spreads (0.5-2%) that eat into tight ATR targets.
    # MAX: Very high-price stocks can't be properly sized with a
    #   small budget (1 share = too concentrated). Set to 0 to disable.
    #   Auto-calculated from budget if 0: budget × MAX_POSITION_PCT / 100
    #   ensures at least 1 share fits within per-stock cap.
    SCAN_MIN_PRICE: float = 100.0
    SCAN_MAX_PRICE: float = 0  # 0 = auto from budget (budget × MAX_POSITION_PCT%)

    # OPPORTUNITY_RESCAN_MINUTES: how often to scan for new trades
    # when there are free position slots. Independent of position
    # close events. Only triggers when open_positions < MAX_POSITIONS.
    # Costs 1 Claude call per rescan (V2) or 0 (NoAI).
    # Set to 0 to disable periodic opportunity scanning.
    OPPORTUNITY_RESCAN_MINUTES: int = 30

    # NIFTY_RECHECK_MINUTES: how often to re-fetch NIFTY index and
    # update market condition (BULLISH/BEARISH/NEUTRAL) during the
    # monitoring loop. Detects intraday regime shifts (e.g. morning
    # dip → afternoon recovery). Set to 0 to disable.
    NIFTY_RECHECK_MINUTES: int = 15

    # ── Stagnant Position Exit (NoAI) ─────────────────────────────
    # STAGNANT_EXIT_MINUTES: after this many minutes, evaluate the
    #   position with the directional adverse / dead-flat thresholds
    #   below. Only in NoAI mode (V2 has Claude reviews for this).
    #   Set to 0 to disable.
    #
    #   DECISION HISTORY:
    #     0.5% (original): Too aggressive with 1.2% target — exited
    #       positions at +0.3% profit as "stagnant" even though they were
    #       25% of the way to target. On April 15, 9 of 13 trades hit
    #       stagnant exit at exactly 45 min, including profitable ones
    #       (ONGC +0.42%, HCLTECH +0.32%, VBL +0.37%). These were moving
    #       in the right direction, just slowly.
    #     0.3% (2026-04-15): Only exit truly dead positions. A stock that
    #       moved 0.3% toward target in 45 min is progressing. Below 0.3%
    #       means near-zero movement — likely going nowhere.
    #     2026-04-17: Single STAGNANT_EXIT_MIN_MOVE_PCT retired in favour
    #       of directional split (adverse / dead-flat). Was firing on
    #       slow-positive trades (RECLTD +0.26%, TATAPOWER +0.25%) — we
    #       were locking in sub-charge profits and paying Rs.15-20
    #       round-trip to re-enter elsewhere. Now only exits if ADVERSE
    #       (clearly losing) or DEAD-FLAT (|move| near zero).
    #     2026-04-20: Removed the leftover STAGNANT_EXIT_MIN_MOVE_PCT
    #       field entirely (was kept as a "backwards compat" no-op for
    #       3 days but read by nothing). Added second-tier hard-max
    #       check (#172) — see block below.
    # Backtest audit (2026-05-26): stagnant exit disabled (=0).
    STAGNANT_EXIT_MINUTES:      int   = 0
    STAGNANT_ADVERSE_PCT:       float = 0.2
    STAGNANT_DEAD_FLAT_PCT:     float = 0.1

    # ── Stagnant-Drift Hard-Max (#172, Tier 2) ─────────────────
    # Second-tier checkpoint that uses progress-to-target rather than
    # absolute move-band. Catches drifters that survived the 45-min
    # directional check by sitting just outside the dead-flat band on
    # the snapshot tick. See STAGNANT_EXIT_MINUTES decision history
    # (entry dated 2026-04-20) for the UNITDSPR motivating case.
    # Backtest audit (2026-05-26): stagnant hard-max disabled.
    STAGNANT_HARD_MAX_ENABLED:    bool  = False
    STAGNANT_HARD_MAX_MINUTES:    int   = 90
    # If progress_pct toward target is below this after HARD_MAX_MINUTES,
    # exit. progress_pct = move_toward_target / (target - entry) * 100.
    # 20 means "covered less than a fifth of the entry→target distance".
    #
    #   DECISION HISTORY:
    #     25.0 (initial #172, 2026-04-20 morning): Initial conservative
    #       value to limit false-positive exits on slow-but-progressing
    #       trades.
    #     20.0 (2026-04-20 EOD): Lowered after re-review. At 90 min
    #       (~50% of typical session-remaining), 25% progress required
    #       linear projection of target hit by ~5:45 PM (past close).
    #       20% projects to ~4:30 PM — the bare-minimum pace needed
    #       to plausibly hit target before square-off.
    STAGNANT_MIN_PROGRESS_PCT:    float = 20.0

    # ── Candle-Protect / Regime-Shift SL Cushion ──────────────────
    # CANDLE_PROTECT_MIN_CUSHION_PCT: minimum gap (as % of current
    #   price) between the tightened SL and current market price.
    #   Previously when a contrary signal fired on a break-even/loss
    #   position, SL collapsed to exact entry — which, if current
    #   price was already against entry, triggered an instant stop
    #   (the INDIGO 2026-04-17 SL-M double-book bug). This cushion
    #   prevents that collapse by keeping SL at least this % away
    #   from the live price, and at least this % from entry.
    CANDLE_PROTECT_MIN_CUSHION_PCT: float = 0.3

    # ── Signal-Reversal Exit (#174) ───────────────────────────────
    # When the periodic candle re-scan (CANDLE_RESCAN_MINUTES) sees
    # a held position's combined_score flip strongly OPPOSITE to the
    # trade direction AND a confirming reversal candle pattern is
    # present, exit immediately rather than waiting for price to hit SL.
    # The static SL-M only catches price-side moves; a momentum reversal
    # is signal-side information that arrives BEFORE the price stop —
    # acting on it cuts losses earlier than the fixed SL would.
    #
    # Triggers (BUY position, mirrored for SELL):
    #   combined_score <= -SIGNAL_REVERSAL_SCORE
    #   AND (if SIGNAL_REVERSAL_REQUIRE_PATTERN) at least one of:
    #     EVENING_STAR, BEARISH_ENGULFING, BEARISH_HARAMI,
    #     SHOOTING_STAR, HANGING_MAN, THREE_BLACK_CROWS
    # Skipped when the position is already in profit ≥ 1× initial risk
    # (let the trailing stop manage winners — don't dump a profitable
    #  trade just because a single 15-min candle reverses).
    #
    # MOTIVATING CASE: HDFCBANK 2026-04-20 — bot held BUY from 11:31,
    #   stopped out at 13:26 for Rs.-155. The very next scanner tick
    #   (13:27) scored it -10.0 STRONG_SELL with EVENING_STAR +
    #   BEARISH_HARAMI. Held positions weren't being rescored at all;
    #   the bearish patterns were forming for ~30 min before the SL hit.
    # Backtest audit (2026-05-26): signal-reversal exit disabled.
    #   GATE AUDIT (2026-05-26): Cannot backtest (needs live score/pattern
    #   tracking). ENABLED by pro decision — cutting losses on thesis
    #   invalidation is institutional best practice. Conservative trigger
    #   (score>=7 AND pattern AND skip winners). HDFCBANK case validates.
    SIGNAL_REVERSAL_EXIT_ENABLED:    bool  = True
    SIGNAL_REVERSAL_SCORE:           float = 7.0
    SIGNAL_REVERSAL_REQUIRE_PATTERN: bool  = True

    # ── Signal-Decay Exit ─────────────────────────────────────────
    # Companion to signal-reversal: catches same-direction thesis
    # decay AND any sign flip that #174 (which requires |fresh|≥7
    # and a confirming pattern) silently misses.
    #
    # Triggers (BUY, mirrored for SELL):
    #   |entry_score| ≥ MIN_ENTRY_SCORE   (only conviction entries)
    #   AND one of:
    #     a) same-sign decay: |fresh| < |entry| × DECAY_FRACTION
    #     b) sign flip (any magnitude)
    #   AND elapsed ≥ MIN_HOLD_MINUTES
    #   AND pnl < initial_risk × WINNER_SKIP_R_MULTIPLE
    #     (sub-1R profit has no trailing cushion; ≥1R keeps running
    #      on the trailing stop. Falls back to `pnl > 0` skip when
    #      `initial_sl` is missing — restart-rehydrated trade.)
    #
    # Origin: BHARTIARTL 2026-04-21 (entered +10.1, decayed to +3.6,
    # sat 5h in slow-positive then exited LOSER_EXIT). Sign-flip
    # path added 2026-04-28 after a soft +10→-3 flip slipped past
    # both #174 (no pattern) and the prior same-sign-only #188.
    # Backtest audit (2026-05-26): signal-decay exit disabled.
    SIGNAL_DECAY_EXIT_ENABLED:          bool  = False
    SIGNAL_DECAY_FRACTION:              float = 0.4
    SIGNAL_DECAY_MIN_ENTRY_SCORE:       float = 7.0
    SIGNAL_DECAY_MIN_HOLD_MINUTES:      int   = 30
    SIGNAL_DECAY_WINNER_SKIP_R_MULTIPLE: float = 1.0

    # ── Post-Observation Score Recheck (Roadmap #196) ─────────────
    # The observation window (ENTRY_DELAY_MINUTES, EXPIRY_ENTRY_DELAY_MINUTES,
    # or the late-start floor) keeps the bot honest on price direction —
    # but the composite score that justified the trade is computed BEFORE
    # the wait and never refreshed. On 2026-04-23 the morning burst entered
    # CUMMINSIND/HINDUNILVR/SIEMENS at scan-time scores +9.9/+9.8/+9.5
    # 15 min after the scan; all three exited losers within 41 min, two
    # via SIGNAL_DECAY (#188 confirmed scores had decayed below 40% of
    # entry). This gate refreshes the score AFTER the wait and aborts
    # entries whose conviction has evaporated.
    #
    # Triggers (per surviving trade after price-direction filter):
    #   abort if sign(fresh) != sign(entry_score)        (signal flipped)
    #   abort if abs(fresh) < abs(entry_score) * FRESH_ENTRY_DECAY_FRACTION
    #   otherwise update trade["_entry_score"] = fresh   (so downstream
    #     score-gated checks compare against the freshest available score)
    #
    # Only fires when wait >= FRESH_ENTRY_RECHECK_MIN_WAIT_MINUTES
    # (default 5 — skip on near-zero waits where no new candle has closed).
    # Requires the active scanner to expose `_analyse_stock(symbol, exchange)`
    # — Scanner does, V1 (frozen) does not, so V1 path is unaffected.
    # Backtest audit (2026-05-26): post-observation score recheck disabled.
    FRESH_ENTRY_RECHECK_ENABLED:        bool  = False
    FRESH_ENTRY_DECAY_FRACTION:         float = 0.6
    FRESH_ENTRY_RECHECK_MIN_WAIT_MINUTES: int = 5
    # Monotonic-direction gate (Roadmap #199, follow-up to #196).
    # The retention floor (FRACTION) is a magnitude check — a score
    # that *fell* from +6.5 to +4.5 still passes 60% retention but the
    # market is actively telling us the edge is decaying in real time.
    # When this is True, additionally abort entries whose magnitude
    # dropped at all between entry and recheck (|fresh| < |entry|),
    # subject to a small tolerance to absorb scoring jitter.
    # When False, only sign-flip and retention-floor are enforced
    # (legacy #196 behaviour).
    FRESH_ENTRY_REQUIRE_MONOTONIC:        bool  = True
    FRESH_ENTRY_MONOTONIC_TOLERANCE:      float = 0.3

    # ── ADX + DI Entry Gate (Roadmap #157) ────────────────────────
    # ADX_ENTRY_GATE_ENABLED: require minimum ADX and directional
    #   alignment (DI) before entering a new trade. Kills chop-day
    #   churn — on days when NIFTY is range-bound and ADX sits
    #   below ~18, entries keep firing and immediately get stopped
    #   out or force-exited, burning ~Rs.40 round-trip each.
    # ADX_MIN_THRESHOLD: minimum ADX value for an entry (below this
    #   is considered "no trend" — the setup may still work but the
    #   odds are meaningfully worse).
    # ADX_OVERRIDE_SCORE: |combined_score| threshold that overrides
    #   the ADX gate. A very strong signal can override a weak ADX
    #   reading — trend may be *about* to emerge.
    # Backtest audit (2026-05-26): ADX entry gate disabled.
    ADX_ENTRY_GATE_ENABLED:  bool  = False
    ADX_MIN_THRESHOLD:       float = 18.0
    ADX_OVERRIDE_SCORE:      float = 7.0

    # ── ATR-Based Position Sizing (Roadmap #145) ──────────────────
    # ATR_SIZING_ENABLED: compute qty based on per-trade risk budget
    #   and stock volatility instead of pure price-based allocation.
    #   Without this, a low-ATR stock and a high-ATR stock get the
    #   same rupee exposure but 5× different rupee risk. With it,
    #   every trade risks roughly the same amount.
    # RISK_PER_TRADE_PCT: fraction of total budget risked per trade.
    #   risk_rupees = budget × RISK_PER_TRADE_PCT / 100.
    #   risk_qty    = risk_rupees / sl_distance   (sl_distance = |entry − sl|)
    #   Final qty   = min(price-based cap, risk_qty) — never exceeds the
    #   existing per-position budget cap, only reduces it.
    # Backtest audit (2026-05-26): ATR-based sizing disabled (use equal sizing).
    ATR_SIZING_ENABLED:      bool  = False
    RISK_PER_TRADE_PCT:      float = 0.5

    # ── Per-Symbol Re-Entry Cooldown (Roadmap #161) ───────────────
    # RE_ENTRY_COOLDOWN_ENABLED: after ANY exit of a symbol (SL, target,
    #   stagnant, external), block re-entry in the SAME direction for
    #   RE_ENTRY_COOLDOWN_MINUTES. Stops the "re-enter immediately on
    #   same signal" loop that burns Rs.40 round-trip each time.
    #   Opposite direction is still allowed (reversal setups).
    # RE_ENTRY_COOLDOWN_MINUTES: block window after any exit.
    # RE_ENTRY_SCORE_OVERRIDE: very strong score overrides cooldown.
    # Backtest audit (2026-05-26): re-entry cooldown disabled.
    RE_ENTRY_COOLDOWN_ENABLED:  bool  = False
    RE_ENTRY_COOLDOWN_MINUTES:  int   = 30
    RE_ENTRY_SCORE_OVERRIDE:    float = 7.0

    # ── Charge-Aware Minimum Target (Roadmap #162, retuned by #238) ─
    # MIN_PROFIT_CHARGE_MULTIPLE: reject trades where expected gross
    #   profit at target is less than round-trip charges × this multiple.
    #   A Rs.2000 stock with Rs.5 SL + Rs.6.5 target on 1 share ≈ Rs.4
    #   round-trip charges, leaving Rs.2.5 net — not worth the risk.
    #
    # #238 (analyst pass, 2026-04-27): raised 2.0 → 3.0. With multiple=2
    # the rule guaranteed only 1× charges as cushion for slippage —
    # which is exactly the expected NSE first-minute slippage on a
    # liquid NIFTY100 entry, leaving zero true-profit cushion. 3.0
    # gives 2× charges of cushion (industry rule of thumb for retail
    # intraday). Combined with #237 (Rs.135 absolute floor) this kills
    # the marginal trades that are the bulk of loss days at small
    # budgets.
    # Kill-switch: MIN_PROFIT_CHARGE_MULTIPLE <= 0 disables.
    # Backtest audit (2026-05-26): charge-aware R:R veto disabled (<=0 → off).
    MIN_PROFIT_CHARGE_MULTIPLE: float = 0.0

    # ── Daily Loss Soft-Stop Hysteresis (Roadmap #163) ────────────
    # DAILY_LOSS_SOFT_STOP_PCT: when day P&L ≤ -this% of budget, stop
    #   taking NEW entries but keep monitoring existing positions.
    #   Hard circuit breaker at MAX_LOSS_PER_DAY_PCT still closes all.
    #   This gives a hysteresis band — prevents "open loser → hit SL
    #   → open another loser" pattern, but doesn't force exits that
    #   might recover in a green afternoon.
    # Set to 0 to disable (no soft stop, only hard CB).
    # Backtest audit (2026-05-26): disabled — only MAX_LOSS_PER_DAY_PCT hard CB runs.
    DAILY_LOSS_SOFT_STOP_PCT: float = 0.0

    # ── Intraday Equity-Peak Drawdown Stop (Roadmap #168) ───────
    # Soft-stop (#163) and hard CB both measure loss vs the day's
    # starting budget. If the bot is +2% by 11 AM and gives it all
    # back to +0.2% by 1 PM, neither fires — total day P&L never
    # went negative. Pro intraday desks track the equity high-water
    # mark and pause new entries on a defined drawdown from peak.
    #
    # Tracks `_intraday_peak_pnl = max(peak, day_pnl())` each scan.
    # When `(peak - day_pnl) / budget > PEAK_DRAWDOWN_STOP_PCT` block
    # NEW entries for the rest of the session. Existing positions
    # continue to be managed normally.
    #
    # PEAK_DRAWDOWN_STOP_PCT: % of budget that the day P&L must
    #   give back from its intraday peak to trigger the stop.
    #   Default 1.5% mirrors the soft-stop sensitivity.
    # PEAK_DRAWDOWN_MIN_PEAK_PCT: only arm the gate after the day
    #   P&L peak has been above this threshold (default 0.5% of
    #   budget). Prevents triggering on tiny early-morning swings
    #   when peak is essentially noise.
    # Set PEAK_DRAWDOWN_STOP_PCT <= 0 to disable.
    # Backtest audit (2026-05-26): disabled.
    PEAK_DRAWDOWN_STOP_PCT:     float = 0.0
    PEAK_DRAWDOWN_MIN_PEAK_PCT: float = 0.5

    # ── Lunch-Lull Entry Skip (Roadmap #164) ──────────────────────
    # Indian markets are lowest-volume and lowest-ADX during lunch.
    # Most churn trades fire in this window.
    # LUNCH_LULL_ENABLED: skip new entries during lunch lull.
    # LUNCH_LULL_START_HOUR / MINUTE: window start (11:30).
    # LUNCH_LULL_END_HOUR / MINUTE:   window end   (12:15).
    # LUNCH_LULL_SCORE_OVERRIDE: very strong signals bypass.
    # NOTE 2026-04-26 (#221 then immediate same-day step-up):
    # rejection-audit on 2026-04-{22,23,24} showed lunch-lull was
    # net-NEGATIVE every day (Avoided Rs.1,649 vs Missed Rs.2,656
    # = net -Rs.1,007 over 3 days at 1-slot hypothetical sizing).
    # Initial fix lowered the override 6.0 → 5.5, but that
    # equalled MIN_SCORE (5.5) and silently turned the gate
    # into a no-op (anything passing the entry score gate also
    # passed lunch). Final landing: 5.7 — a meaningful step above
    # MIN_SCORE so the gate still bites on truly weak lunch-
    # window signals (5.5–5.7 range) while admitting the
    # borderline-but-profitable 5.7+ band the audit said we were
    # missing. Conservative tweak — not disabling the gate.
    # Backtest audit (2026-05-26): lunch-lull skip disabled.
    LUNCH_LULL_ENABLED:         bool  = False
    LUNCH_LULL_START_HOUR:      int   = 11
    LUNCH_LULL_START_MINUTE:    int   = 30
    LUNCH_LULL_END_HOUR:        int   = 12
    LUNCH_LULL_END_MINUTE:      int   = 15
    LUNCH_LULL_SCORE_OVERRIDE:  float = 5.7

    # ── Choppy-Morning Entry Pause (Roadmap #192) ─────────────────
    # On weak-trend mornings (NIFTY ADX < CHOPPY_PAUSE_ADX_THRESHOLD
    # for ≥ CHOPPY_PAUSE_MIN_CONSECUTIVE_SCANS in the 09:30 –
    # CHOPPY_PAUSE_WINDOW_END_HOUR:MINUTE window) AND the bot has
    # already churned out ≥ CHOPPY_PAUSE_MIN_RECENT_STAGNANT_EXITS
    # entries via STAGNANT_EXIT or SIGNAL_DECAY in the last
    # CHOPPY_PAUSE_RECENT_EXIT_LOOKBACK_MINUTES, set a sliding pause
    # of CHOPPY_PAUSE_MINUTES on NEW entries. Existing positions
    # are managed normally. Pause re-arms after expiry, so a chop
    # morning can pause multiple times.
    # Backtest audit (2026-05-26): choppy-morning pause disabled.
    CHOPPY_MORNING_PAUSE_ENABLED:                 bool  = False
    CHOPPY_PAUSE_ADX_THRESHOLD:                   float = 16.0
    CHOPPY_PAUSE_MIN_CONSECUTIVE_SCANS:           int   = 3
    CHOPPY_PAUSE_MINUTES:                         int   = 15
    CHOPPY_PAUSE_WINDOW_START_HOUR:               int   = 9
    CHOPPY_PAUSE_WINDOW_START_MINUTE:             int   = 30
    CHOPPY_PAUSE_WINDOW_END_HOUR:                 int   = 10
    CHOPPY_PAUSE_WINDOW_END_MINUTE:               int   = 30
    CHOPPY_PAUSE_MIN_RECENT_STAGNANT_EXITS:       int   = 2
    CHOPPY_PAUSE_RECENT_EXIT_LOOKBACK_MINUTES:    int   = 10

    # ── VIX Intraday-Spike Pause (Roadmap #211) ───────────────────
    # India VIX measures 30-day expected NIFTY volatility. An intraday
    # VIX spike (≥ VIX_SPIKE_PCT vs day open) signals a regime shift:
    # correlations blow up, mean-reversion strategies misfire, SLs get
    # hunted. The bot already used `_check_vix_spike()` to skip the
    # opportunity scan and the all-closed re-scan, but the per-trade
    # `enter_trade()` path was not gated — meaning an entry queued by
    # the initial pre-market scan or a partial re-scan could still fire
    # during a spike. #211 routes the same VIX-spike state through the
    # OrderEngine via `set_vix_spike()` and adds a check inside
    # `enter_trade()` so all entry paths honour the pause. Existing
    # positions continue to be managed normally (only NEW entries
    # block); the pause auto-clears when VIX retreats below the spike
    # threshold. Kill-switch: VIX_SPIKE_ENTRY_PAUSE_ENABLED.
    # Threshold tuning: VIX_SPIKE_PCT (10.0) is the existing
    # `_check_vix_spike()` constant; reused so the engine and manager
    # cannot disagree.
    # Backtest audit (2026-05-26): VIX-spike entry pause disabled.
    VIX_SPIKE_ENTRY_PAUSE_ENABLED:                bool  = False

    # ── Tape-Breadth Filter (Roadmap #212) ────────────────────────
    # On heavy-FII-sell days the broader tape is bearish: 70 %+ of the
    # pre-filter set scores SELL, sectors weak across the board. BUYs
    # entered on such days underperform their score-implied edge by
    # ~30 % (anecdotal Apr 2026 backtests). #212 counts BUY vs SELL
    # candidates AFTER the MIN_SCORE filter and applies a small
    # score penalty to the minority side. The penalty operates on
    # magnitude (sign preserved) so weak counter-tape candidates fall
    # below `MIN_SCORE` naturally instead of being hard-blocked.
    # BREADTH_BEARISH_BUY_RATIO: when BUY count ≤ this fraction of
    #     {BUY+SELL}, tape is bearish — penalize remaining BUYs.
    # BREADTH_BULLISH_SELL_RATIO: mirror for bullish tape (penalize
    #     SELLs).
    # BREADTH_PENALTY: subtract from |score| of the minority side.
    # BREADTH_MIN_CANDIDATES: skip the filter when the post-V2_MIN
    #     set is too small to be statistically meaningful.
    # Kill-switch: BREADTH_FILTER_ENABLED.
    # Backtest audit (2026-05-26): tape-breadth filter disabled.
    BREADTH_FILTER_ENABLED:        bool  = False
    BREADTH_BEARISH_BUY_RATIO:     float = 0.30
    BREADTH_BULLISH_SELL_RATIO:    float = 0.30
    BREADTH_PENALTY:               float = 0.5
    BREADTH_MIN_CANDIDATES:        int   = 5

    # ── Sector-Rank Directional Bias (Roadmap #215) ───────────────
    # Beyond the existing per-sector momentum boost (+/-0.5 when 3+
    # stocks in the sector agree on direction), rank ALL sectors by
    # their average score at scan time, then nudge each candidate's
    # score by `bias = (mid_rank - rank_of_my_sector) * STEP`,
    # clamped to ±MAX. Sign-aware: BUYs in top-ranked (most-bullish)
    # sectors get a positive nudge; SELLs in bottom-ranked
    # (most-bearish) sectors get a negative nudge (which deepens
    # |score|). Counter-rank candidates are penalised. Operates on
    # magnitude — never flips sign.
    #
    # SECTOR_RANK_BIAS_STEP: per-rank step size in score points.
    # SECTOR_RANK_BIAS_MAX: cap on absolute bias regardless of rank
    #     gap (prevents tiny universes with 2 sectors blowing up).
    # SECTOR_RANK_MIN_SECTORS: skip the bias when we have fewer than
    #     this many distinct sectors with ≥1 candidate (sample too
    #     small for a meaningful ranking).
    # Kill-switch: SECTOR_RANK_BIAS_ENABLED.
    # Backtest audit (2026-05-26): sector-rank directional bias disabled.
    SECTOR_RANK_BIAS_ENABLED:      bool  = False
    SECTOR_RANK_BIAS_STEP:         float = 0.1
    SECTOR_RANK_BIAS_MAX:          float = 0.6
    SECTOR_RANK_MIN_SECTORS:       int   = 4

    # ── Sector-Cascade Exit (Roadmap #149) ────────────────────────
    # When a sector's average score collapses fast (a "cascade"),
    # all our open positions in that sector are statistically more
    # likely to lose simultaneously. We tighten their SLs to break-
    # even (or 50 % of profit if already in the green) BEFORE each
    # individual position trips its own decay / SL gate. Defensive,
    # never opens a new trade.
    #
    # Trigger (CONSERVATIVE — must satisfy ALL):
    #   - sector_avg_score dropped by ≥ SECTOR_CASCADE_DROP_THRESHOLD
    #     between the previous and current scan
    #   - sector_avg_score is now on the side opposite to our open
    #     positions (BEARISH for our BUYs, BULLISH for our SELLs)
    #   - we have ≥ SECTOR_CASCADE_MIN_OPEN positions open in that
    #     sector
    #
    # Action: software SL → max(current SL, breakeven-with-buffer);
    # exchange SL-M is replaced via `_update_exchange_sl()`.
    #
    # SECTOR_CASCADE_DROP_THRESHOLD: minimum |avg-score-drop| in one
    #     scan window to qualify (default 2.0 = "from neutral to
    #     clearly bearish in one window"). Set high enough that
    #     normal sector noise never trips it.
    # SECTOR_CASCADE_OPPOSITE_FLOOR: the new sector avg must cross
    #     this magnitude on the contrary side to qualify. Prevents
    #     "drop from +5 to +1" (still positive) from firing on BUYs.
    # SECTOR_CASCADE_MIN_OPEN: only fire when we have at least this
    #     many open positions in the cascading sector.
    # Kill-switch: SECTOR_CASCADE_EXIT_ENABLED.
    # Backtest audit (2026-05-26): sector-cascade exit disabled.
    SECTOR_CASCADE_EXIT_ENABLED:   bool  = False
    SECTOR_CASCADE_DROP_THRESHOLD: float = 2.0
    SECTOR_CASCADE_OPPOSITE_FLOOR: float = 1.5
    SECTOR_CASCADE_MIN_OPEN:       int   = 2

    # ── Unrealised-MTM-Aware Circuit Breaker (Roadmap #166) ───────
    # When True, check_circuit_breaker / is_soft_stopped /
    # is_peak_drawdown_stopped use day_pnl() + unrealised_pnl(quotes)
    # instead of just day_pnl(). Catches the "five open positions all
    # bleeding -1.5% MTM = -7.5% real exposure but no SL has fired
    # yet" pattern. Engine keeps its own quote cache populated by the
    # monitor loop — enabling/disabling is purely a flag flip.
    # Backtest audit (2026-05-26): MTM-aware circuit-breaker disabled (closed-only CB runs).
    MTM_AWARE_CB_ENABLED:        bool  = False

    # ── Strong-Gap ADX Threshold Boost (Roadmap #194) ─────────────
    # When today's NIFTY opening gap is GAP_*_STRONG and continues
    # the prior-day direction (continuation, not reversal), raise
    # the ADX entry threshold and override score for the rest of the
    # day. Levels the field for fade-the-gap setups whose intraday
    # hit-rate sits below 40%. Reverts at session end.
    # Backtest audit (2026-05-26): strong-gap ADX boost disabled.
    STRONG_GAP_ADX_BOOST_ENABLED: bool  = False
    STRONG_GAP_ADX_DELTA:         float = 1.0
    STRONG_GAP_OVERRIDE_DELTA:    float = 0.5

    # ── Average-Down Prevention (Roadmap #195) ────────────────────
    # Per-symbol cooldown (#161) blocks 30 min after any exit. After
    # that, a fresh same-direction signal at the SAME magnitude as
    # the prior STAGNANT/DECAY exit means we're chasing the same
    # false signal twice. Block when |new_score - last_exit_score|
    # ≤ AVG_DOWN_SCORE_DELTA AND the prior exit was STAGNANT_EXIT
    # or SIGNAL_DECAY AND the prior exit was within
    # AVG_DOWN_LOOKBACK_MINUTES. Override at |score| ≥
    # AVG_DOWN_OVERRIDE_SCORE (real reversal-strength signal).
    # Backtest audit (2026-05-26): average-down prevention disabled.
    AVG_DOWN_PREVENTION_ENABLED:    bool  = False
    AVG_DOWN_SCORE_DELTA:           float = 1.0
    AVG_DOWN_LOOKBACK_MINUTES:      int   = 120
    AVG_DOWN_OVERRIDE_SCORE:        float = 8.0

    # ── Pattern-direction entry veto ─────────────────────────────
    # Mirror of the SIGNAL_REVERSAL exit applied at ENTRY. A BUY can
    # currently clear |score|≥6 even when the entry-tick pattern set
    # contains a bearish reversal (BEARISH_ENGULFING, EVENING_STAR,
    # BEARISH_HARAMI, SHOOTING_STAR, HANGING_MAN, THREE_BLACK_CROWS) —
    # mirrored for SELL with bullish set. Live evidence: PNB / TRENT
    # 2026-04-21 entered at +6 with BEARISH_ENGULFING, both stagnated.
    # Gate: opposite-side reversal pattern AND |score| < OVERRIDE_SCORE
    # → skip entry. Override allows high-conviction break-outs through.
    # Backtest audit (2026-05-26): pattern-direction entry veto disabled.
    PATTERN_VETO_ENABLED:        bool  = False
    PATTERN_VETO_OVERRIDE_SCORE: float = 8.0

    # ── Pattern↔Tech Contradiction Penalty ──────────────────────
    # Patterns flow into combined_score as raw additive contributions;
    # two failure modes:
    #   (a) DOJI is NEUTRAL (indecision) but its weight currently
    #       survives into a directional verdict.
    #   (b) A bearish reversal pattern on a BUY verdict (or vice-versa)
    #       means the chart is already printing the flip; the score is
    #       reading momentum about to die. #190 hard-vetoes at very low
    #       conviction — this is a softer continuous penalty applied at
    #       scanner scoring time so downstream gates / Claude / sorting
    #       see the de-risked score.
    # Apply after pattern+tech sum, clamp so |score| can't flip sign.
    # Penalties stack: DOJI + BEARISH_ENGULFING on a BUY = -2.5.
    # Backtest audit (2026-05-26): pattern/tech contradiction penalty disabled.
    PATTERN_CONTRADICTION_PENALTY_ENABLED: bool  = False
    PATTERN_CONTRADICTION_PENALTY:         float = 2.0
    PATTERN_INDECISION_PENALTY:            float = 0.5

    # ── VWAP-Extension Entry Gate (Roadmap #201) ──────────────────
    # Buying ≥+1σ above VWAP (or selling ≤-1σ below) means entering at
    # the top/bottom of the intraday range — there's no room to run
    # before mean-reversion kicks in. The existing VWAP guard checks
    # `vwap_dev` % distance only; this new gate uses the proper
    # statistical band classification (`vwap_bands.signal` from
    # technical_indicators) which adapts to each stock's intraday
    # volatility instead of using a fixed 0.8% cap.
    # Gate (read from snapshot field `vwap_band`):
    #   BUY  blocked at AT_UPPER_1SD or AT_UPPER_2SD
    #   SELL blocked at AT_LOWER_1SD or AT_LOWER_2SD
    # Override at |score| ≥ VWAP_BAND_OVERRIDE_SCORE — high-conviction
    # break-out entries can still chase the band.
    # Kill-switch: VWAP_BAND_GATE_ENABLED.
    # Backtest audit (2026-05-26): VWAP-band entry gate disabled.
    VWAP_BAND_GATE_ENABLED:    bool  = False
    VWAP_BAND_OVERRIDE_SCORE:  float = 7.0

    # ── Late-Entry Tightening (Roadmap #202) ──────────────────────
    # When the bot joins the market mid-session (or scans late after
    # all morning candidates closed), the remaining session is
    # shorter and the high-edge moves of the day have already played
    # out. Late entries are *higher* risk yet the entry pipeline
    # currently relaxes: observation floor drops to 5 min ("opening
    # volatility passed"), R:R floor stays the same. We invert this:
    # past LATE_ENTRY_HOUR (default 10:00 IST), demand a strictly
    # better-than-base score for fresh entries.
    # Effect when active:
    #   - effective_min_score() bumped by LATE_ENTRY_MIN_SCORE_BUMP
    #     (checked inside enter_trade against |_entry_score|)
    #
    # NOTE (#225 simplification): the previous late-entry-only R:R
    # floor and concurrent-position cap were dropped. The R:R guard now
    # lives in RR_HARD_FLOOR (always-on, see RR section above) which
    # already prevents adaptive relaxation from undercutting morning
    # standards. Concurrency is fully owned by dynamic_max_positions()
    # all day — the late-only cap was budget-disproportionate and
    # rarely bound in practice (most days fill 2-4 of 5-7 slots total).
    # Kill-switch: LATE_ENTRY_TIGHTENING_ENABLED.
    # Backtest audit (2026-05-26): late-entry tightening disabled.
    LATE_ENTRY_TIGHTENING_ENABLED: bool  = False
    LATE_ENTRY_HOUR:               int   = 10    # 10:00 IST and later
    # #239 (analyst pass, 2026-04-27): 0.5 → 1.0. The 2026-04-27
    # session-2 entries (HINDZINC/ADANIENSOL/HINDALCO at 10:27) all
    # passed the +0.5 bump and all faded — the bump was too gentle
    # to materially change which trades clear the bar. Post-10:00
    # trades have less than half the session left; they need a
    # visibly higher bar, not marginally higher.
    LATE_ENTRY_MIN_SCORE_BUMP:     float = 1.0

    # ── Post-Entry Momentum Kill ──────────────────────────────────
    # Targets the "slow bleed to SL" pattern: a fill turns red
    # immediately and walks 8-12 min to its full -1×ATR SL. If the
    # setup has real edge, the first few minutes should at least try
    # to move toward target; if not (and MTM is already negative),
    # exit at small loss rather than wait for the full SL hit.
    #
    # Logic in check_stops_and_targets per-position loop:
    #   skip if elapsed < GRACE_SECONDS or > WINDOW_MINUTES
    #   skip if pos["_external"] or pos.get("_partial_taken")
    #   adverse_pct = |entry-current|/entry × 100 (red side only)
    #   skip if adverse_pct < MIN_ADVERSE_PCT (sub-noise / spread)
    #   progress = (current-entry)/(target-entry)  [mirrored SELL]
    #   if progress < MIN_PROGRESS_PCT/100 AND unrealised < 0 → exit
    #
    # Tuning: grace=180s after 2026-04-27 prod evidence (1-min
    # progress test was killing on sub-spread micro-moves);
    # MIN_ADVERSE_PCT=0.40 = ~4× typical NSE intraday spread.
    # Backtest audit (2026-05-26): post-entry momentum kill disabled.
    MOMENTUM_KILL_ENABLED:           bool  = False
    MOMENTUM_KILL_GRACE_SECONDS:     int   = 180  # 3-min settlement window (industry std)
    MOMENTUM_KILL_WINDOW_MINUTES:    int   = 5
    MOMENTUM_KILL_MIN_PROGRESS_PCT:  float = 25.0  # at least 25% of way to target
    MOMENTUM_KILL_MIN_ADVERSE_PCT:   float = 0.40  # noise floor: adverse move must exceed this %

    # ── Realised-P&L Recovery from Prior-Session Fills (#203) ─────
    # On restart after a crash, load_existing_positions only adopts
    # OPEN positions (qty != 0). Any position that opened in a prior
    # session and was closed by an exchange-side SL-M during the crash
    # window is silently lost — bot starts with realised = 0 even
    # though Zerodha holds the full picture. This breaks the MTM-aware
    # circuit breaker (#197) and adaptive sizing because both reason
    # from a wrong P&L floor.
    # Logic in OrderEngine.recover_prior_session_fills() (called from
    # manager right after load_existing_positions):
    #   For each net-position with product==MIS, quantity==0,
    #   buy_quantity > 0 AND sell_quantity > 0, AND not already
    #   represented in self.positions:
    #     synthesise a CLOSED record using Zerodha's authoritative
    #     pnl/buy_price/sell_price fields. exit_reason =
    #     "RECOVERED_FROM_ZERODHA". entry_time / exit_time are unknown.
    # Kill-switch: REALISED_PNL_RECOVERY_ENABLED.
    REALISED_PNL_RECOVERY_ENABLED: bool  = True

    # ── Session-time-aware RVol normalization (Roadmap #147) ────
    # NSE intraday volume is U-shaped: heavy 09:15-10:30, light 11:00-
    # 13:00, heavy 13:30-15:30. The scanner's prorated RVol divides
    # the day's volume so far by the linear time fraction — which
    # over-estimates expected midday volume and under-estimates
    # morning/close volume. The 0.7× entry floor was calibrated on full-day
    # average and rejects valid trades during the lunch trough.
    #
    # RVOL_FLOOR: base relative volume floor. Stocks with intraday
    # volume below this fraction of their average are rejected.
    # 0.7 = reject if today's volume is below 70% of typical.
    #
    #   BACKTEST (2026-05-26): Swept 0-2.0. RVOL 0.7 has best
    #   per-trade expectancy (-0.091%). Higher values (1.3+) improve
    #   PF slightly but worse per-trade. See BACKTEST_GATE_D5.md.
    RVOL_FLOOR: float = 0.7

    # Approach: scale the RVOL_FLOOR by an hour-bucket multiplier.
    # E.g. at 12:00 the multiplier is 0.7 → effective floor 0.49,
    # so a 0.5× RVol observed at noon now passes (it would have
    # been mid-pack relative to typical noon volume).
    # Buckets cover 09-15. Hours outside fall back to 1.0 (no scaling).
    # Backtest audit (2026-05-26): RVol time normalisation disabled.
    RVOL_TIME_NORMALIZATION_ENABLED: bool = False
    RVOL_FLOOR_BY_HOUR: dict[int, float] = {
        9:  1.00,  # opening surge — keep strict
        10: 1.00,
        11: 0.85,  # mid-morning fade
        12: 0.70,  # lunch trough
        13: 0.85,  # post-lunch warm-up
        14: 1.00,
        15: 1.00,  # closing surge
    }

    # ── Intraday volume baselines (Roadmap #260) ─────────────────
    # When True AND `data/volume_baseline.db` exists with a row for the
    # current `(symbol, hour-bucket)`, the scanner replaces the linear
    # daily-volume pro-rating with a per-symbol per-hour baseline:
    #   live_rvol = today_cumulative_volume_so_far
    #               / (avg_daily_volume * baseline_hour_share)
    # where `baseline_hour_share` is the historical fraction of the
    # full-day volume that completes by the current hour boundary on
    # that symbol (mean over the last N trading days from
    # `data/candle_cache.db`). NSE intraday volume is U-shaped, so the
    # current linear pro-rating over-rejects midday and under-rejects
    # opens/closes — RVOL_FLOOR_BY_HOUR softens that but still uses
    # the wrong denominator. The baseline replaces the denominator.
    #
    # Default OFF (fail-safe). Build the baseline first via
    #   `python scripts/trade/build_volume_baseline.py`
    # then flip this to True. The scanner falls back to the existing
    # linear pro-rating when the baseline DB is missing or the symbol
    # has no row yet (e.g. a newly added universe member).
    INTRADAY_VOLUME_BASELINE_ENABLED: bool = False
    INTRADAY_VOLUME_BASELINE_LOOKBACK_DAYS: int = 20
    INTRADAY_VOLUME_BASELINE_MIN_SAMPLES:   int = 10

    # ══════════════════════════════════════════════════════════════
    # BUDGET REGIME — DYNAMIC CONFIG BY ACCOUNT SIZE (Roadmap #165)
    # ══════════════════════════════════════════════════════════════
    # Different account sizes need different gate tightness.
    # At Rs.20k budget, a Rs.40 charge is 0.2% — one losing trade
    # wipes out two winners. At Rs.500k budget, the same charge is
    # 0.008% — noise. Rather than re-tuning every constant when we
    # bump the budget, these tiers scale the gates automatically.
    #
    # Regime is determined at startup from self._budget:
    #   TINY   : < BUDGET_TIER_SMALL      (default < Rs.30k)
    #   SMALL  : < BUDGET_TIER_NORMAL     (default < Rs.1L)
    #   NORMAL : < BUDGET_TIER_LARGE      (default < Rs.5L)
    #   LARGE  : >= BUDGET_TIER_LARGE     (≥ Rs.5L)
    #
    # BUDGET_REGIME_ENABLED: master kill-switch. When False, all
    #   regime-adjusted gates fall back to the base cfg value.
    # Backtest audit (2026-05-26): budget-regime deltas disabled (no silent
    # MIN_SCORE / ADX / trade-cap / spread / profit bumps).
    BUDGET_REGIME_ENABLED: bool = False
    BUDGET_TIER_SMALL:     float = 30_000.0
    BUDGET_TIER_NORMAL:    float = 100_000.0
    BUDGET_TIER_LARGE:     float = 500_000.0

    # Regime-specific deltas applied on top of base cfg values.
    # Positive delta = stricter gate. Looked up via regime name.
    # ADX threshold bump (base 18): tiny = +2 (demand ADX ≥ 20),
    #   small = +1, normal = 0, large = -1 (can trade weaker trends).
    BUDGET_ADX_THRESHOLD_DELTA = {"TINY": 2.0, "SMALL": 1.0, "NORMAL": 0.0, "LARGE": -1.0}

    # Trade-cap bump (base MAX_TRADES_PER_DAY = 12):
    #   tiny = -4 (max 8), small = -4 (max 8), normal = 0, large = +3.
    #   #240 (analyst pass, 2026-04-27): SMALL tightened -2 → -4. With
    #   Rs.50K budget the per-trade charge hurdle is ~0.27%; sustaining
    #   10+ trades/day at that hurdle requires a >55% win rate that we
    #   have not yet demonstrated. 8 trades is the safe ceiling until
    #   the live ledger shows otherwise.
    BUDGET_TRADE_CAP_DELTA = {"TINY": -4, "SMALL": -4, "NORMAL": 0, "LARGE": 3}

    # Bid-ask spread tightening (base MAX_SPREAD_PCT = 0.3):
    #   tiny/small subtract 0.10 → effective 0.20% cap (charge hurdle
    #     at small budgets is ~0.27%; trades whose spread alone is
    #     >70% of the hurdle have no edge before they start).
    #   normal/large = 0 (default 0.30% suffices once slot value is
    #     large enough that 0.30% is well below the charge hurdle).
    #   #236 (analyst pass, 2026-04-27).
    BUDGET_SPREAD_DELTA = {"TINY": -0.10, "SMALL": -0.10, "NORMAL": 0.0, "LARGE": 0.0}

    # Min-expected-profit floor bump (base MIN_EXPECTED_PROFIT = 135):
    #   tiny/small = 0 (Rs.135 floor = 3× typical round-trip charges
    #     at Rs.16-25K slot value).
    #   normal = +65 (Rs.200 floor) — with Rs.50K+ slots, charges grow
    #     to Rs.65-90; preserve the 3× ratio.
    #   large = +265 (Rs.400 floor) — Rs.1L+ slots see Rs.130+ charges.
    #   #237 (analyst pass, 2026-04-27).
    BUDGET_MIN_PROFIT_DELTA = {"TINY": 0.0, "SMALL": 0.0, "NORMAL": 65.0, "LARGE": 265.0}

    # MIN_SCORE bump (base MIN_SCORE = 2.0):
    #   tiny = +1.0, small = +0.5, normal = 0, large = 0.
    BUDGET_MIN_SCORE_DELTA = {"TINY": 1.0, "SMALL": 0.5, "NORMAL": 0.0, "LARGE": 0.0}

    # ── Loss-Adjusted Position Sizing ─────────────────────────────
    # LOSS_SIZING_ENABLED: if True, reduce position sizes after
    #   realising losses. Budget for new trades shrinks by day's
    #   losses, preventing full-size re-entry after SL hits.
    #   Live mode already gets this from Zerodha's margin API.
    #   This mainly helps dry-run mode stay realistic.
    # Backtest audit (2026-05-26): loss-adjusted sizing disabled.
    LOSS_SIZING_ENABLED: bool = False

    # ── Circuit Breaker Cooldown ──────────────────────────────────
    # CIRCUIT_BREAKER_COOLDOWN_MINUTES: after circuit breaker trips,
    #   wait this many minutes then resume with loss-adjusted budget.
    #   Only NEW losses after resume can re-trip the breaker.
    #   Set to 0 to disable (old behaviour: circuit breaker = day over).
    CIRCUIT_BREAKER_COOLDOWN_MINUTES: int = 30

    # MAX_CIRCUIT_BREAKER_TRIPS: max number of times CB can trip
    #   and resume in one day. After this, the day is over.
    #   2 = trip → cooldown → resume → trip again → done.
    MAX_CIRCUIT_BREAKER_TRIPS: int = 2

    # ── Consecutive Loss Pause (Roadmap #20 + #244 broadening) ────
    # CONSECUTIVE_SL_PAUSE_COUNT: after this many consecutive losing
    #   exits (across any stocks), pause new entries for
    #   CONSECUTIVE_SL_PAUSE_MINUTES. Protects against whipsaw days when
    #   signals fail repeatedly.
    #   Set to 0 to disable.
    #
    # 2026-04-27 broadening (#244): pre-#244 the counter only fired on
    # `STOP_LOSS` exits. Today's session 1 lost 4 of 4 morning trades to
    # `MOMENTUM_KILL` (now noise-floored by #233) — the whipsaw guard
    # never fired because the counter ignored MOMENTUM_KILL / STAGNANT_EXIT
    # / SIGNAL_DECAY / LOSER_EXIT classes of loss. Industry standard
    # (prop-firm risk frameworks) is to count *any* losing exit, not just
    # hard-SL hits. Kill-switch `LOSS_STREAK_INCLUDE_NON_SL_LOSSES`
    # below; flip to False for one-line revert to STOP_LOSS-only behaviour.
    # Backtest audit (2026-05-26): consecutive-loss pause disabled (=0).
    #   GATE AUDIT (2026-05-26): Cannot backtest (portfolio-level
    #   cross-day streaks). ENABLED by pro decision — standard
    #   prop-firm practice. With K1=2, 3 consecutive SLs = 2+ bad
    #   days = regime detection signal. 30-min pause is conservative.
    CONSECUTIVE_SL_PAUSE_COUNT: int = 3
    CONSECUTIVE_SL_PAUSE_MINUTES: int = 30

    # When True, MOMENTUM_KILL / STAGNANT_EXIT / SIGNAL_DECAY / LOSER_EXIT
    # exits with `pnl < 0` also feed the consecutive-loss counter. EOD
    # reasons (SQUARE_OFF / CIRCUIT_BREAKER) and operator/external closes
    # are excluded — they are not strategy failures.
    LOSS_STREAK_INCLUDE_NON_SL_LOSSES: bool = True

    # ── Entry-burst cap (Roadmap #179) ───────────────────────────
    # MAX_ENTRIES_PER_60S: hard cap on how many positions the bot
    #   may open inside any rolling 60-second window. Same-direction
    #   sub-60s bursts had ~92% lose-together correlation across 3
    #   qualifying days (Apr-22, Apr-23, May-05; 11 of 12 burst entries
    #   were losers). Cap-2 means the third-and-later burst entry is
    #   skipped with a logged BURST_CAP rejection.
    #   Set to 0 to disable (no cap).
    # Backtest audit (2026-05-26): entry-burst cap disabled.
    ENTRY_BURST_CAP_ENABLED: bool = False
    ENTRY_BURST_CAP_MAX_ENTRIES_PER_60S: int = 2
    # Roadmap #179a (2026-05-06): per-budget delta on the base cap.
    # The 92% lose-together evidence was on a Rs.50K SMALL account, so
    # SMALL/TINY stay at the audit-validated cap. NORMAL/LARGE accounts
    # have 5-8 morning slots and the cap-2 single-threads them with no
    # contradicting evidence — industry prop-firm risk frameworks
    # (TopstepTrader, FTMO, MyForexFunds) tier max-concurrent caps by
    # account size. Effective cap = base + delta, floored at 0.
    BUDGET_BURST_CAP_DELTA = {"TINY": 0, "SMALL": 0, "NORMAL": 1, "LARGE": 2}

    # ── BUY-side directional auto-pause (Roadmap #251) ───────────
    # When the rolling 7-day BUY-side win-rate collapses AND NIFTY's
    # rolling-7-day return is negative, disable BUY entries for the
    # session. Symmetric SELL side is wired but defaults disabled
    # (no SELL-bleed regime observed yet).
    #
    # Origin: 2026-04-22 → 2026-05-05 9-day audit. BUY 5/40 wins
    # (12.5%), SELL 6/14 wins (42.9%). BUY-side net negative on 8/8
    # days across BEARISH/NEUTRAL/BULLISH regimes — not a
    # regime-classifier bug, a structural scoring/scanner-side bias.
    # The pause clears at next session start when conditions recover.
    # Backtest audit (2026-05-26): directional auto-pause disabled.
    DIRECTIONAL_PAUSE_ENABLED: bool = False
    DIRECTIONAL_PAUSE_LOOKBACK_DAYS: int = 7
    DIRECTIONAL_PAUSE_MIN_TRADES: int = 10        # need ≥ N trades on the side in lookback to act
    DIRECTIONAL_PAUSE_WR_THRESHOLD: float = 0.30  # arm if rolling WR ≤ 30%
    DIRECTIONAL_PAUSE_NIFTY_FLOOR_PCT: float = 0.0  # arm if NIFTY 7d return ≤ this %
    DIRECTIONAL_PAUSE_RECOVER_WR: float = 0.40    # not used in same-session logic; documented for skill files
    # Fractional-Kelly opposing-side cap. When a side's pause arms,
    # the surviving (un-paused) side may have thin evidence
    # (n < OPPOSING_MIN_TRADES). Per Kelly: ≤20 trades is statistically
    # noisy. Cap entries on the un-validated side until it accumulates
    # history. MAX_ENTRIES bumped 3 → 5 after live SELL-side WR=67%
    # (n=3) under the original cap left profit on the table.
    DIRECTIONAL_PAUSE_OPPOSING_MIN_TRADES: int = 20
    DIRECTIONAL_PAUSE_OPPOSING_THIN_MAX_ENTRIES: int = 5

    # Intraday NIFTY-bounce bypass. Base pause uses 7-day NIFTY return;
    # in a flat-but-bearish regime the pause can stay armed for weeks.
    # If today's NIFTY rallies for ≥ MIN_SCANS consecutive readings in
    # the paused-side direction, query-time bypass kicks in (state is
    # retained, drains naturally if NIFTY reverses). All other gates
    # (opposing-thin, RR/score/RSI/ADX) still apply.
    DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_PCT: float = 1.0   # |intraday return| above which bypass may trigger
    DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_MIN_SCANS: int = 2  # consecutive scans required

    # Tape-breadth divergence bypass — A/D-line analogue. NIFTY is
    # cap-weighted (~50% in top-7 names) so a "flat NIFTY but mid-caps
    # rallying" day never trips the NIFTY-bounce bypass. When the
    # scanner's post-V2_MIN candidate snapshot shows the paused side
    # holding ≥ RATIO of {BUY+SELL} (and absolute floors are met), we
    # probe the regime. The 30-40% band between BREADTH_BEARISH_BUY_RATIO
    # and BREADTH_BYPASS_RATIO is an explicit "uncertain — neither
    # rule fires" zone so the two gates never overlap.
    DIRECTIONAL_PAUSE_BREADTH_BYPASS_ENABLED:        bool  = True
    DIRECTIONAL_PAUSE_BREADTH_BYPASS_RATIO:          float = 0.40  # paused-side share of {BUY+SELL} that triggers bypass
    DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_PAUSED_SIDE: int = 3      # absolute paused-side count floor
    DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_TOTAL:      int   = 5     # total below which bypass is skipped (small sample)

    # ── Rolling profit-factor circuit breaker ────────────────────
    # Multi-day analogue of CONSECUTIVE_SL_PAUSE_COUNT computed at
    # session start from intraday_tax_ledger. When rolling N-day net
    # is below NET_FLOOR AND PF is below THRESHOLD, blocks new entries
    # for the session (existing positions managed normally).
    #
    # DISABLED 2026-05-05 — counterfactual replay over 17 sessions
    # showed this gate REMOVES Rs.116 net once #251 (directional pause)
    # is in place. The directional gate already surgically blocks the
    # failing side; a full-session blackout (a) costs the opposing
    # side's profit and (b) creates false-pause days on single-loss
    # arming. Per Kelly: when edge is uncertain, reduce stake, don't
    # bet zero. Code/ledger reading kept for telemetry; re-enable only
    # after ≥ 60 days of post-#251 data validates new thresholds.
    ROLLING_PF_PAUSE_ENABLED: bool = False
    ROLLING_PF_PAUSE_LOOKBACK_DAYS: int = 3
    ROLLING_PF_PAUSE_THRESHOLD: float = 0.6       # PF = Σwins / |Σlosses|
    ROLLING_PF_PAUSE_NET_FLOOR: float = -300.0    # rupees; both must hold
    ROLLING_PF_PAUSE_MIN_TRADES: int = 5          # need ≥ N trades in lookback to act

    # ── Dynamic Score Threshold ───────────────────────────────────
    # After losses, raise the minimum score for new NoAI trades.
    # LOSS_SCORE_BUMP_PCT: day loss threshold (as % of budget)
    #   that triggers a higher MIN_SCORE.
    # LOSS_SCORE_BUMP_AMOUNT: extra score points added to MIN_SCORE.
    # Backtest audit (2026-05-26): dynamic post-loss score bump disabled (=0).
    LOSS_SCORE_BUMP_PCT: float = 0.0
    LOSS_SCORE_BUMP_AMOUNT: float = 1.5

    # ══════════════════════════════════════════════════════════════
    # MARKET INTELLIGENCE — VIX, PRE-OPEN, FII/DII
    # ══════════════════════════════════════════════════════════════

    # ── India VIX Thresholds ──────────────────────────────────
    # India VIX measures 30-day expected volatility of NIFTY.
    # High VIX = fear/uncertainty, low VIX = complacency.
    # Fetched once at startup and rechecked during NIFTY rechecks.
    #
    # VIX_HIGH_THRESHOLD: above this, reduce exposure. Typical
    #   high-VIX regime occurs during corrections, earnings season,
    #   or global events. Indian intraday gets whippier — wider SLs
    #   needed but fewer positions to limit drawdown.
    # VIX_LOW_THRESHOLD: below this, breakout strategies work better.
    #   Market is calm/complacent — SL-triggered exits are fewer.
    # VIX_SPIKE_PCT: if VIX jumps this much intraday (vs day open),
    #   pause new entries and protect existing positions.
    # VIX_HIGH_POSITION_REDUCTION: reduce MAX_POSITIONS by this in high VIX.
    # VIX_HIGH_SCORE_BUMP: raise MIN_SCORE by this in high VIX.
    VIX_HIGH_THRESHOLD: float = 20.0
    VIX_LOW_THRESHOLD:  float = 12.0
    VIX_SPIKE_PCT:      float = 10.0
    # Backtest audit (2026-05-26): VIX-driven position reduction / score bump disabled.
    VIX_HIGH_POSITION_REDUCTION: int   = 0
    VIX_HIGH_SCORE_BUMP:         float = 0.0

    # ── Pre-Open Auction ──────────────────────────────────────
    # NSE pre-open session runs 9:00-9:08 AM. After 9:08, the
    # equilibrium (opening) price is set for each stock.
    # Fetching quotes at ~9:08 gives gap direction, magnitude,
    # and pre-open volume before the first candle forms.
    #
    # PREOPEN_ENABLED: enable pre-open data collection.
    # PREOPEN_GAP_SIGNIFICANT_PCT: minimum gap % to flag as significant.
    #   Stocks gapping > this with high volume → institutional interest.
    PREOPEN_ENABLED: bool = True
    PREOPEN_GAP_SIGNIFICANT_PCT: float = 1.0

    # ── FII/DII Flow Bias ─────────────────────────────────────
    # Previous day's FII/DII net buy/sell data from NSE.
    # Used as a morning bias signal (one-time fetch at startup).
    # Both buying = bullish, both selling = bearish, mixed = neutral.
    #
    # FII_DII_ENABLED: enable FII/DII data fetch (from NSE website).
    #   If the fetch fails (NSE blocking, timeout), it's silently
    #   skipped — no impact on trading.
    FII_DII_ENABLED: bool = True

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
    #   Rs.20 flat per executed order (buy or sell), or 0.03% of
    #   turnover — whichever is LOWER. For small orders (<Rs.66,667),
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
    # SEBI_CHARGE_PER_CR: Rs.10 per crore of turnover. Negligible for
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
    # ZERODHA_MONTHLY_COST: Kite Connect subscription (Rs.500/month).
    #   This is a MONTHLY cost, NOT deducted from daily P&L.
    #   It's shown as an informational line in the report so you can
    #   gauge whether the bot is covering subscription costs over time.
    # ZERODHA_BILLING_START_DATE: anchor date for the monthly billing
    #   cycle. Cycles are exactly one calendar month each (anchored on
    #   this day-of-month) and a cycle is "paid" the moment it starts.
    #   Used by `Config.zerodha_subscription_for_fy()` so deductible
    #   reports count actual paid cycles instead of a naive
    #   "distinct months that had trades" proxy. Format: "YYYY-MM-DD".
    # CLAUDE_COST_PER_CALL: estimated Rs. per Claude API call on Pro plan.
    #   This IS deducted from daily P&L because it's a per-use cost.
    ZERODHA_MONTHLY_COST:        float = 500.0
    ZERODHA_BILLING_START_DATE:  str   = "2026-03-14"
    CLAUDE_COST_PER_CALL:        float = 3.0   # avg Rs.3 per Claude API call on Pro

    # ══════════════════════════════════════════════════════════════
    # INCOME TAX SETTINGS
    # ══════════════════════════════════════════════════════════════
    # Intraday equity trading is "speculative business income" and
    # taxed at your personal income tax slab rate.
    #
    # TAX_RATE_PCT: Your marginal tax slab rate (%).
    #   Set this to the slab you fall in based on your total income.
    #   30 = 30% (income above Rs.24L in new regime from FY 2025-26)
    #   25 = 25% (Rs.20-24L new regime)
    #   The bot uses this to show estimated tax liability on each
    #   day's net profit in the trading report. This helps you track
    #   how much you need to set aside for advance tax payments.
    #
    # TAX_CESS_PCT: Health & Education Cess — currently 4% on tax.
    #   Effective rate = TAX_RATE_PCT × (1 + TAX_CESS_PCT/100)
    #   e.g. 30% slab → 30 × 1.04 = 31.2% effective.
    TAX_RATE_PCT:  float = 30.0   # your income tax slab rate
    TAX_CESS_PCT:  float = 4.0    # health & education cess on tax

    # ── Capital-gains tax (statutory flat rates, not slab-based) ──
    # STCG on listed equity (held ≤ 12 months):  20%  (w.e.f. 23-Jul-2024)
    # LTCG on listed equity (held > 12 months):  12.5% above Rs.1.25 lakh exemption
    STCG_TAX_RATE_PCT:     float = 20.0
    LTCG_TAX_RATE_PCT:     float = 12.5
    LTCG_EXEMPTION_LIMIT:  float = 125000.0   # annual LTCG exemption (Rs.1.25L)

    # ══════════════════════════════════════════════════════════════
    # PORTFOLIO ANALYSER (--mode analyze) — risk metrics
    # ══════════════════════════════════════════════════════════════
    # RISK_FREE_RATE_PCT: annualised risk-free rate used in Sharpe.
    #   India 10-year G-Sec yield ≈ 7% historically; refresh quarterly
    #   as the actual yield drifts.
    # CASH_DRAG_FLAG_PCT: when (cash / total_account_value) exceeds
    #   this %, the analyser flags the portfolio as under-invested.
    # ANALYZE_VOL_LOOKBACK_DAYS: trailing window for daily-return
    #   volatility (annualised by sqrt(252)).
    RISK_FREE_RATE_PCT:        float = 7.0
    CASH_DRAG_FLAG_PCT:        float = 25.0
    ANALYZE_VOL_LOOKBACK_DAYS: int   = 60

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

    # ── NSE early-close calendar (Roadmap #193) ────────────────────
    # NSE shuts the equity segment at 13:30 IST on a handful of days
    # each year (Diwali Muhurat eve, year-end). On those days Zerodha
    # auto-square-off intraday positions starts ~13:25 at distress
    # prices. To exit ahead of that we shift our SQUARE_OFF time to
    # ~13:25 by mutating SQUARE_OFF_HOUR / MINUTE at startup via
    # `Config.apply_early_close_if_today()` (called once from
    # `manager.run()` right after `validate_ranges()`).
    #
    # Format: dict[str, tuple[int, int]] — {"YYYY-MM-DD": (hour, minute)}
    # Tuple is the EARLY square-off the bot should use, NOT the NSE
    # close time. Default convention is to exit 5 min before the
    # actual NSE early close.
    #
    # NSE publishes the early-close calendar on its circular page
    # each year; verify dates and the actual close time annually.
    # UPDATE THIS DICT every January.
    NSE_EARLY_CLOSE_DATES_2026: dict[str, tuple[int, int]] = {
        # Pending NSE 2026 circular confirmation. Examples:
        # "2026-11-10": (13, 25),  # Diwali / Muhurat — NSE closes 13:30
        # "2026-12-31": (13, 25),  # Year-end early close (if so notified)
    }

    # ── Earnings/results-day blackout (Roadmap #167) ──────────────
    # Skip names announcing quarterly results today — Q1-Q4 result
    # days produce 3-5 % gap moves intraday that no technical setup
    # can predict. The scanner consults this dict in
    # `_prefilter_universe()` right after the price filter and drops
    # any matching symbol BEFORE per-stock analysis runs (cheap).
    #
    # Format: dict[str, list[str]] — {"YYYY-MM-DD": ["NSE_SYMBOL", ...]}
    # Source: BSE / NSE corporate-action calendar — review every
    # Friday evening for the upcoming week, OR every morning at
    # 8:30 IST for that day. Empty list (or missing date key) means
    # no blackout that day.
    #
    # Kill-switch: EARNINGS_BLACKOUT_ENABLED.
    # Backtest audit (2026-05-26): earnings-day blackout disabled (calendar data preserved).
    EARNINGS_BLACKOUT_ENABLED: bool = False
    EARNINGS_BLACKOUT_SYMBOLS_2026: dict[str, list[str]] = {
        # Example entries (commented):
        # "2026-04-25": ["RELIANCE", "TCS"],
        # "2026-04-28": ["INFY", "WIPRO"],
    }

    # ── Plan rule tables ──────────────────────────────────────────
    # Maps plan names → capabilities. Read via claude() / zerodha().

    # ── Provider-specific model tables ─────────────────────────
    # Each provider maps plan → {model, max_tokens, ...}.
    # The active combo is AI_PROVIDER × AI_PLAN.
    #
    # Fields per entry:
    #   model            — API model identifier
    #   max_tokens       — max output tokens per call
    #   analysis_depth   — controls prompt complexity (basic/detailed/full)
    #   include_pe_ratios— whether PE/valuation data is injected
    #   input_cost_per_m — cost in USD per 1M input tokens
    #   output_cost_per_m— cost in USD per 1M output tokens
    #   cost_inr_approx  — approximate Rs. per typical run (scan+review)
    #   note             — human-readable one-liner for console/dashboard
    #   free_tier        — (Gemini only) free-tier limits, None if N/A

    # ── Gemini free-tier limits (Google AI Studio) ───────────
    # As of May 2026 the free tier for gemini-2.5-flash gives:
    #   • 500 requests/day  (resets midnight PT)
    #   • 1 million tokens/minute
    #   • No credit card required
    # Well within typical trading-bot usage (~50-100 calls/day).
    GEMINI_FREE_TIER_INFO: str = (
        "Free tier: 500 req/day, 1M tok/min (Google AI Studio). "
        "No credit card required. Sufficient for typical bot usage."
    )

    _GEMINI_RULES = {
        "basic": {
            "analysis_depth":    "basic",
            "include_pe_ratios": False,
            "model":             "gemini-2.5-flash",
            "max_tokens":        1200,
            "input_cost_per_m":  0.15,
            "output_cost_per_m": 0.60,
            "cost_inr_approx":   "FREE (within daily limit)",
            "note":              "Gemini 2.5 Flash · basic · FREE tier",
            "free_tier":         "500 req/day · 1M tok/min",
        },
        "detailed": {
            "analysis_depth":    "detailed",
            "include_pe_ratios": True,
            "model":             "gemini-2.5-flash",
            "max_tokens":        2000,
            "input_cost_per_m":  0.15,
            "output_cost_per_m": 0.60,
            "cost_inr_approx":   "~Rs.0.5/run (or FREE within limit)",
            "note":              "Gemini 2.5 Flash · detailed · ~Rs.0.5/run",
            "free_tier":         "500 req/day · 1M tok/min",
        },
        "full": {
            "analysis_depth":    "full",
            "include_pe_ratios": True,
            "model":             "gemini-2.5-flash",
            "max_tokens":        3000,
            "input_cost_per_m":  0.15,
            "output_cost_per_m": 0.60,
            "cost_inr_approx":   "~Rs.1/run (or FREE within limit)",
            "note":              "Gemini 2.5 Flash · full · ~Rs.1/run",
            "free_tier":         "500 req/day · 1M tok/min",
        },
    }

    _GPT_RULES = {
        "basic": {
            "analysis_depth":    "basic",
            "include_pe_ratios": False,
            "model":             "gpt-4.1-nano",
            "max_tokens":        1200,
            "input_cost_per_m":  0.10,
            "output_cost_per_m": 0.40,
            "cost_inr_approx":   "~Rs.0.3/run",
            "note":              "GPT-4.1 Nano · basic · ~Rs.0.3/run",
            "free_tier":         None,
        },
        "detailed": {
            "analysis_depth":    "detailed",
            "include_pe_ratios": True,
            "model":             "gpt-4.1-mini",
            "max_tokens":        2000,
            "input_cost_per_m":  0.40,
            "output_cost_per_m": 1.60,
            "cost_inr_approx":   "~Rs.1.5/run",
            "note":              "GPT-4.1 Mini · detailed · ~Rs.1.5/run",
            "free_tier":         None,
        },
        "full": {
            "analysis_depth":    "full",
            "include_pe_ratios": True,
            "model":             "gpt-4.1-mini",
            "max_tokens":        3000,
            "input_cost_per_m":  0.40,
            "output_cost_per_m": 1.60,
            "cost_inr_approx":   "~Rs.2/run",
            "note":              "GPT-4.1 Mini · full · ~Rs.2/run",
            "free_tier":         None,
        },
    }

    _CLAUDE_RULES = {
        "basic": {
            "analysis_depth":    "basic",
            "include_pe_ratios": False,
            "model":             "claude-haiku-4-5-20251001",
            "max_tokens":        1200,
            "input_cost_per_m":  0.80,
            "output_cost_per_m": 4.00,
            "cost_inr_approx":   "~Rs.1/run",
            "note":              "Claude Haiku · basic · ~Rs.1/run",
            "free_tier":         None,
        },
        "detailed": {
            "analysis_depth":    "detailed",
            "include_pe_ratios": True,
            "model":             "claude-sonnet-4-6",
            "max_tokens":        2000,
            "input_cost_per_m":  3.00,
            "output_cost_per_m": 15.00,
            "cost_inr_approx":   "~Rs.5/run",
            "note":              "Claude Sonnet · detailed · ~Rs.5/run",
            "free_tier":         None,
        },
        "full": {
            "analysis_depth":    "full",
            "include_pe_ratios": True,
            "model":             "claude-sonnet-4-6",
            "max_tokens":        3000,
            "input_cost_per_m":  3.00,
            "output_cost_per_m": 15.00,
            "cost_inr_approx":   "~Rs.8/run",
            "note":              "Claude Sonnet · full · ~Rs.8/run",
            "free_tier":         None,
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
            "note":             "Live Kite prices + full history · Rs.500/month",
        },
    }

    # ── Derived properties ────────────────────────────────────────

    _AI_PROVIDER_TABLE = {
        "gemini": "_GEMINI_RULES",
        "gpt":    "_GPT_RULES",
        "claude": "_CLAUDE_RULES",
    }

    @classmethod
    def ai(cls) -> dict:
        """Returns the resolved AI plan settings dict for the active provider."""
        table_attr = cls._AI_PROVIDER_TABLE.get(cls.AI_PROVIDER)
        if not table_attr:
            raise ValueError(f"Unknown AI_PROVIDER: {cls.AI_PROVIDER!r}. "
                             f"Valid: {list(cls._AI_PROVIDER_TABLE)}")
        rules = getattr(cls, table_attr)
        plan = cls.AI_PLAN
        if plan not in rules:
            raise ValueError(f"Unknown AI_PLAN: {plan!r}. Valid: {list(rules)}")
        return rules[plan]

    @classmethod
    def ai_display_label(cls) -> str:
        """One-line label: 'GEMINI / gemini-2.5-flash (pro) · ~Rs.0.5/run'"""
        plan = cls.ai()
        provider = cls.AI_PROVIDER.upper()
        return f"{provider} / {plan['model']} ({cls.AI_PLAN}) · {plan['cost_inr_approx']}"

    @classmethod
    def ai_display_block(cls) -> str:
        """Multi-line block for console startup banners."""
        plan = cls.ai()
        provider = cls.AI_PROVIDER.upper()
        lines = [
            f"AI provider    : {provider}",
            f"AI model       : {plan['model']}",
            f"AI plan        : {cls.AI_PLAN.upper()}  ({plan['note']})",
            f"Estimated cost : {plan['cost_inr_approx']}",
        ]
        if plan.get("free_tier"):
            lines.append(f"Free-tier limit: {plan['free_tier']}")
        return "\n".join(lines)

    @classmethod
    def ai_providers_summary(cls) -> list[dict]:
        """Returns a list of all providers with their plans for dashboard display."""
        result = []
        for provider, attr in cls._AI_PROVIDER_TABLE.items():
            rules = getattr(cls, attr)
            for plan_name, plan in rules.items():
                result.append({
                    "provider":     provider,
                    "plan":         plan_name,
                    "model":        plan["model"],
                    "cost":         plan["cost_inr_approx"],
                    "note":         plan["note"],
                    "free_tier":    plan.get("free_tier"),
                    "active":       provider == cls.AI_PROVIDER and plan_name == cls.AI_PLAN,
                })
        return result

    @classmethod
    def claude(cls) -> dict:
        """Returns the resolved AI plan settings dict.
        Legacy alias — delegates to ai() for the active provider."""
        return cls.ai()

    @classmethod
    def zerodha(cls) -> dict:
        """Returns the resolved Zerodha plan settings dict."""
        return cls._ZERODHA_RULES[cls.ZERODHA_PLAN]

    @classmethod
    def zerodha_subscription_for_fy(
        cls,
        fy_start_year: int,
        today: "datetime.date | None" = None,
    ) -> tuple[float, int]:
        """Total Zerodha Kite Connect subscription cost attributable to
        an Indian financial year (Apr 1 → Mar 31), and the number of
        billing cycles counted.

        Each cycle is one calendar month, anchored on the
        day-of-month of ``ZERODHA_BILLING_START_DATE``. A cycle is
        considered *paid* the moment it starts (Kite bills
        in-advance). To avoid double-counting cycles that straddle a
        FY boundary, each cycle is assigned to the FY containing its
        **end date** (`start + 1 month`).

        Example: anchor 2026-03-14, today 2026-04-28, FY 2026-27 →
          cycle [2026-03-14, 2026-04-14): end date in FY 2026-27 → count.
          cycle [2026-04-14, 2026-05-14): end date in FY 2026-27 → count.
          cycle [2026-05-14, ...):       not yet started      → skip.
        Result: 2 cycles × Rs.500 = Rs.1,000.

        Returns ``(total_rs, num_cycles)``.
        """
        if today is None:
            today = now_ist().date()

        try:
            anchor = datetime.datetime.strptime(
                cls.ZERODHA_BILLING_START_DATE, "%Y-%m-%d"
            ).date()
        except (TypeError, ValueError):
            return 0.0, 0

        fy_start = datetime.date(fy_start_year, 4, 1)
        fy_end   = datetime.date(fy_start_year + 1, 3, 31)
        horizon  = min(today, fy_end)

        if anchor > horizon:
            return 0.0, 0

        def _add_one_month(d: datetime.date) -> datetime.date:
            # Kite bills monthly on the same day-of-month. If next
            # month doesn't have that day (e.g. anchor 31 → Feb),
            # clamp to month-end.
            year, month = d.year, d.month + 1
            if month > 12:
                year, month = year + 1, 1
            day = d.day
            # Find last valid day in target month.
            for d_try in (day, 30, 29, 28):
                try:
                    return datetime.date(year, month, d_try)
                except ValueError:
                    continue
            return datetime.date(year, month, 28)  # safety net

        n = 0
        cycle_start = anchor
        while cycle_start <= horizon:
            cycle_end = _add_one_month(cycle_start)
            # Assign to FY containing cycle_end.
            if fy_start <= cycle_end <= fy_end:
                n += 1
            cycle_start = cycle_end

        return n * cls.ZERODHA_MONTHLY_COST, n

    @classmethod
    def apply_early_close_if_today(cls) -> tuple[int, int] | None:
        """Mutate ``SQUARE_OFF_HOUR`` / ``SQUARE_OFF_MINUTE`` if today
        is in the NSE early-close calendar (Roadmap #193).

        NSE shuts the equity segment at 13:30 IST on a handful of
        days each year (Diwali Muhurat eve, year-end). On those days
        Zerodha auto-square fires at distress prices around 13:25.
        We move our SQUARE_OFF time forward to ~13:25 so positions
        are exited at our chosen prices, not Zerodha's.

        Idempotent: calling twice the same day is a no-op (we only
        shift if the configured time is LATER than the early-close
        time, so a manually-set earlier time is preserved).

        Returns ``(hour, minute)`` if today was an early-close day
        and SQUARE_OFF was actually advanced, else ``None``.
        """
        today_str = now_ist().strftime("%Y-%m-%d")
        year = today_str[:4]
        attr_name = f"NSE_EARLY_CLOSE_DATES_{year}"
        cal: dict[str, tuple[int, int]] = getattr(cls, attr_name, {}) or {}
        target = cal.get(today_str)
        if not target:
            return None
        new_hour, new_minute = target
        cur_minutes = cls.SQUARE_OFF_HOUR * 60 + cls.SQUARE_OFF_MINUTE
        new_minutes = new_hour * 60 + new_minute
        if new_minutes >= cur_minutes:
            # Already earlier than (or equal to) the early-close target —
            # honour user's tighter setting, do nothing.
            return None
        cls.SQUARE_OFF_HOUR = new_hour
        cls.SQUARE_OFF_MINUTE = new_minute
        return (new_hour, new_minute)

    @classmethod
    def dynamic_max_positions(cls, budget: float) -> int:
        """Scale MAX_POSITIONS with budget to keep per-position size viable.

        Returns the effective max positions count. If MAX_POSITIONS_OVERRIDE
        is set (non-zero), that value is used unconditionally.

        Goal: each position should be ≥Rs.8K so that round-trip charges
        (Rs.40-50) stay below 0.5% of position value.

        Thresholds (Rs.):
          < 25K  → 2 positions  (Rs.10-12K each)
           25-60K → 3 positions  (Rs.8-20K each)
           60-1L  → 4 positions  (Rs.15-25K each)
           1-3L   → 5 positions  (Rs.20-60K each)
           3-5L   → 6 positions  (Rs.50-83K each)
          > 5L   → 7 positions  (Rs.70K+ each)
        """
        if cls.MAX_POSITIONS_OVERRIDE > 0:
            return cls.MAX_POSITIONS_OVERRIDE

        if budget < 25_000:
            return 2
        elif budget < 60_000:
            return 3
        elif budget < 100_000:
            return 4
        elif budget < 300_000:
            return 5
        elif budget < 500_000:
            return 6
        else:
            return 7

    @classmethod
    def budget_regime(cls, budget: float) -> str:
        """Return budget regime name — "TINY", "SMALL", "NORMAL", or "LARGE".

        Used by gate helpers to apply regime-specific deltas (ADX threshold,
        trade cap, min score) based on account size. See Roadmap #165.
        """
        if not cls.BUDGET_REGIME_ENABLED:
            return "NORMAL"
        if budget < cls.BUDGET_TIER_SMALL:
            return "TINY"
        if budget < cls.BUDGET_TIER_NORMAL:
            return "SMALL"
        if budget < cls.BUDGET_TIER_LARGE:
            return "NORMAL"
        return "LARGE"

    @classmethod
    def validate(cls, require_claude: bool = True) -> list[str]:
        """
        Checks all required API keys are present.
        Returns list of missing key names — empty means all good.
        Set require_claude=False for --noai mode.
        """
        missing = []
        if not cls.ZERODHA_API_KEY:    missing.append("ZERODHA_API_KEY")
        if not cls.ZERODHA_API_SECRET: missing.append("ZERODHA_API_SECRET")
        if require_claude:
            # Check the key for whichever AI provider is active
            if cls.AI_PROVIDER == "gemini" and not cls.GEMINI_API_KEY:
                missing.append("GEMINI_API_KEY")
            elif cls.AI_PROVIDER == "gpt" and not cls.OPENAI_API_KEY:
                missing.append("OPENAI_API_KEY")
            elif cls.AI_PROVIDER == "claude" and not cls.CLAUDE_API_KEY:
                missing.append("CLAUDE_API_KEY")
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

    @classmethod
    def validate_ranges(cls) -> list[str]:
        """
        Sanity-checks every numeric config value for a plausible range.
        Returns list of human-readable error strings — empty means OK.
        Call at startup; abort if non-empty.

        This catches typos like accidentally setting ATR_MULTIPLIER=0
        (divides by zero), MAX_BUDGET_INR=-1 (nonsense), etc.
        """
        errors = []

        def _pos(name: str, val) -> None:
            if val is None or val <= 0:
                errors.append(f"{name} must be > 0 (got {val!r})")

        def _pct(name: str, val, hi: float = 100.0) -> None:
            if val is None or val < 0 or val > hi:
                errors.append(f"{name} must be between 0 and {hi} (got {val!r})")

        # Budget + positions
        _pos("MAX_BUDGET_INR",       cls.MAX_BUDGET_INR)
        _pos("MIN_BALANCE_TO_TRADE", cls.MIN_BALANCE_TO_TRADE)
        _pct("MAX_POSITION_PCT",     cls.MAX_POSITION_PCT)
        if not isinstance(cls.SCORE_WEIGHTED_SIZING_ENABLED, bool):
            errors.append(
                f"SCORE_WEIGHTED_SIZING_ENABLED must be bool "
                f"(got {cls.SCORE_WEIGHTED_SIZING_ENABLED!r})"
            )
        if not isinstance(cls.TRADE_LIVE_TRADING_PAUSED, bool):
            errors.append(
                f"TRADE_LIVE_TRADING_PAUSED must be bool "
                f"(got {cls.TRADE_LIVE_TRADING_PAUSED!r})"
            )

        # SL / target / R:R
        _pos("ATR_MULTIPLIER",         cls.ATR_MULTIPLIER)
        _pos("ATR_PERIOD",             cls.ATR_PERIOD)
        _pos("RR_TARGET_RATIO",        cls.RR_TARGET_RATIO)
        _pct("DEFAULT_STOP_LOSS_PCT",  cls.DEFAULT_STOP_LOSS_PCT, 10)
        _pct("DEFAULT_TARGET_PCT",    cls.DEFAULT_TARGET_PCT, 10)
        _pct("MAX_INTRADAY_SL_PCT",   cls.MAX_INTRADAY_SL_PCT, 10)
        _pct("MIN_SL_DISTANCE_PCT",   cls.MIN_SL_DISTANCE_PCT, 10)
        _pct("EXPIRY_MIN_SL_DISTANCE_PCT", cls.EXPIRY_MIN_SL_DISTANCE_PCT, 10)
        if cls.MIN_SL_DISTANCE_PCT >= cls.MAX_INTRADAY_SL_PCT:
            errors.append("MIN_SL_DISTANCE_PCT must be < MAX_INTRADAY_SL_PCT")

        # Liquidity filters
        # MAX_SPREAD_PCT and MAX_IMPACT_COST_PCT accept 0 (disabled).
        if cls.MAX_SPREAD_PCT < 0 or cls.MAX_SPREAD_PCT > 10:
            errors.append(f"MAX_SPREAD_PCT out of range (0-10): {cls.MAX_SPREAD_PCT!r}")
        if cls.MAX_IMPACT_COST_PCT < 0 or cls.MAX_IMPACT_COST_PCT > 10:
            errors.append(f"MAX_IMPACT_COST_PCT out of range (0-10): {cls.MAX_IMPACT_COST_PCT!r}")
        if not isinstance(cls.VWAP_DRIFT_CHECK_ENABLED, bool):
            errors.append(
                f"VWAP_DRIFT_CHECK_ENABLED must be bool "
                f"(got {cls.VWAP_DRIFT_CHECK_ENABLED!r})"
            )
        if cls.VWAP_DRIFT_WARN_PCT < 0 or cls.VWAP_DRIFT_WARN_PCT > 10:
            errors.append(
                f"VWAP_DRIFT_WARN_PCT out of range (0-10): "
                f"{cls.VWAP_DRIFT_WARN_PCT!r}"
            )

        # Risk caps
        _pct("MAX_LOSS_PER_DAY_PCT", cls.MAX_LOSS_PER_DAY_PCT, 20)
        _pct("TRAIL_STEP_PCT",       cls.TRAIL_STEP_PCT)

        # Timing
        if not (0 <= cls.MARKET_OPEN_HOUR <= 23):
            errors.append(f"MARKET_OPEN_HOUR out of range: {cls.MARKET_OPEN_HOUR!r}")
        if not (0 <= cls.SQUARE_OFF_HOUR <= 23):
            errors.append(f"SQUARE_OFF_HOUR out of range: {cls.SQUARE_OFF_HOUR!r}")
        _pos("PRICE_POLL_SECONDS",      cls.PRICE_POLL_SECONDS)
        _pos("MIN_MINUTES_FOR_ENTRY",   cls.MIN_MINUTES_FOR_ENTRY)
        if cls.ENTRY_DECISION_FLOOR_MINUTES_AFTER_OPEN < 0:
            errors.append(
                f"ENTRY_DECISION_FLOOR_MINUTES_AFTER_OPEN must be ≥ 0: "
                f"{cls.ENTRY_DECISION_FLOOR_MINUTES_AFTER_OPEN!r}"
            )

        # Entry filters
        _pct("RSI_BUY_BLOCK_THRESHOLD",  cls.RSI_BUY_BLOCK_THRESHOLD)
        _pct("RSI_SELL_BLOCK_THRESHOLD", cls.RSI_SELL_BLOCK_THRESHOLD)
        _pct("VWAP_EXTENSION_BLOCK_PCT", cls.VWAP_EXTENSION_BLOCK_PCT)
        _pos("VWAP_EXT_SCORE_OVERRIDE",  cls.VWAP_EXT_SCORE_OVERRIDE)
        _pos("FRESH_REVERSAL_DELTA_THRESHOLD", cls.FRESH_REVERSAL_DELTA_THRESHOLD)
        _pos("GAP_COHERENCE_OVERRIDE_SCORE", cls.GAP_COHERENCE_OVERRIDE_SCORE)
        _pos("SIGNAL_REVERSAL_SCORE",   cls.SIGNAL_REVERSAL_SCORE)
        _pos("SIGNAL_DECAY_MIN_ENTRY_SCORE", cls.SIGNAL_DECAY_MIN_ENTRY_SCORE)
        _pos("SIGNAL_DECAY_MIN_HOLD_MINUTES", cls.SIGNAL_DECAY_MIN_HOLD_MINUTES)

        # Subscription billing anchor (Kite Connect monthly cycles).
        try:
            datetime.datetime.strptime(cls.ZERODHA_BILLING_START_DATE, "%Y-%m-%d")
        except (TypeError, ValueError):
            errors.append(
                f"ZERODHA_BILLING_START_DATE must be 'YYYY-MM-DD' "
                f"(got {cls.ZERODHA_BILLING_START_DATE!r})"
            )

        if cls.SIGNAL_DECAY_WINNER_SKIP_R_MULTIPLE < 0:
            errors.append(
                f"SIGNAL_DECAY_WINNER_SKIP_R_MULTIPLE must be ≥ 0: "
                f"{cls.SIGNAL_DECAY_WINNER_SKIP_R_MULTIPLE!r}"
            )
        if not (0 < cls.SIGNAL_DECAY_FRACTION < 1):
            errors.append(
                f"SIGNAL_DECAY_FRACTION must be in (0, 1): {cls.SIGNAL_DECAY_FRACTION!r}"
            )

        # Post-observation score recheck (#196)
        if not (0 < cls.FRESH_ENTRY_DECAY_FRACTION < 1):
            errors.append(
                f"FRESH_ENTRY_DECAY_FRACTION must be in (0, 1): "
                f"{cls.FRESH_ENTRY_DECAY_FRACTION!r}"
            )
        if cls.FRESH_ENTRY_RECHECK_MIN_WAIT_MINUTES < 0:
            errors.append(
                f"FRESH_ENTRY_RECHECK_MIN_WAIT_MINUTES must be ≥ 0: "
                f"{cls.FRESH_ENTRY_RECHECK_MIN_WAIT_MINUTES!r}"
            )
        if cls.FRESH_ENTRY_MONOTONIC_TOLERANCE < 0:
            errors.append(
                f"FRESH_ENTRY_MONOTONIC_TOLERANCE must be ≥ 0: "
                f"{cls.FRESH_ENTRY_MONOTONIC_TOLERANCE!r}"
            )

        # Pattern↔tech contradiction penalty (#200)
        if cls.PATTERN_CONTRADICTION_PENALTY < 0:
            errors.append(
                f"PATTERN_CONTRADICTION_PENALTY must be ≥ 0: "
                f"{cls.PATTERN_CONTRADICTION_PENALTY!r}"
            )
        if cls.PATTERN_INDECISION_PENALTY < 0:
            errors.append(
                f"PATTERN_INDECISION_PENALTY must be ≥ 0: "
                f"{cls.PATTERN_INDECISION_PENALTY!r}"
            )

        # VWAP-band gate (#201)
        _pos("VWAP_BAND_OVERRIDE_SCORE", cls.VWAP_BAND_OVERRIDE_SCORE)

        # Late-entry tightening (#202)
        if not (0 <= cls.LATE_ENTRY_HOUR <= 23):
            errors.append(
                f"LATE_ENTRY_HOUR out of range: {cls.LATE_ENTRY_HOUR!r}"
            )
        if cls.LATE_ENTRY_MIN_SCORE_BUMP < 0:
            errors.append(
                f"LATE_ENTRY_MIN_SCORE_BUMP must be ≥ 0: "
                f"{cls.LATE_ENTRY_MIN_SCORE_BUMP!r}"
            )
        _pos("RR_HARD_FLOOR", cls.RR_HARD_FLOOR)

        # Post-entry momentum kill (#198)
        if cls.MOMENTUM_KILL_GRACE_SECONDS < 0:
            errors.append(
                f"MOMENTUM_KILL_GRACE_SECONDS must be ≥ 0: "
                f"{cls.MOMENTUM_KILL_GRACE_SECONDS!r}"
            )
        _pos("MOMENTUM_KILL_WINDOW_MINUTES", cls.MOMENTUM_KILL_WINDOW_MINUTES)
        if cls.MOMENTUM_KILL_GRACE_SECONDS >= cls.MOMENTUM_KILL_WINDOW_MINUTES * 60:
            errors.append(
                f"MOMENTUM_KILL_GRACE_SECONDS ({cls.MOMENTUM_KILL_GRACE_SECONDS}) "
                f"must be < MOMENTUM_KILL_WINDOW_MINUTES*60 "
                f"({cls.MOMENTUM_KILL_WINDOW_MINUTES * 60})"
            )
        _pos("MOMENTUM_KILL_MIN_ADVERSE_PCT", cls.MOMENTUM_KILL_MIN_ADVERSE_PCT)
        if not (0 <= cls.MOMENTUM_KILL_MIN_PROGRESS_PCT <= 100):
            errors.append(
                f"MOMENTUM_KILL_MIN_PROGRESS_PCT must be in [0, 100]: "
                f"{cls.MOMENTUM_KILL_MIN_PROGRESS_PCT!r}"
            )

        # Choppy-morning pause (#192)
        if cls.CHOPPY_PAUSE_ADX_THRESHOLD <= 0:
            errors.append(
                f"CHOPPY_PAUSE_ADX_THRESHOLD must be > 0: {cls.CHOPPY_PAUSE_ADX_THRESHOLD!r}"
            )
        _pos("CHOPPY_PAUSE_MIN_CONSECUTIVE_SCANS", cls.CHOPPY_PAUSE_MIN_CONSECUTIVE_SCANS)
        _pos("CHOPPY_PAUSE_MINUTES",               cls.CHOPPY_PAUSE_MINUTES)
        _pos("CHOPPY_PAUSE_MIN_RECENT_STAGNANT_EXITS",
             cls.CHOPPY_PAUSE_MIN_RECENT_STAGNANT_EXITS)
        _pos("CHOPPY_PAUSE_RECENT_EXIT_LOOKBACK_MINUTES",
             cls.CHOPPY_PAUSE_RECENT_EXIT_LOOKBACK_MINUTES)
        if not (0 <= cls.CHOPPY_PAUSE_WINDOW_START_HOUR <= 23):
            errors.append(
                f"CHOPPY_PAUSE_WINDOW_START_HOUR out of range: "
                f"{cls.CHOPPY_PAUSE_WINDOW_START_HOUR!r}"
            )
        if not (0 <= cls.CHOPPY_PAUSE_WINDOW_END_HOUR <= 23):
            errors.append(
                f"CHOPPY_PAUSE_WINDOW_END_HOUR out of range: "
                f"{cls.CHOPPY_PAUSE_WINDOW_END_HOUR!r}"
            )

        # Strong-gap ADX boost (#194)
        if cls.STRONG_GAP_ADX_DELTA < 0:
            errors.append(
                f"STRONG_GAP_ADX_DELTA must be ≥ 0: {cls.STRONG_GAP_ADX_DELTA!r}"
            )
        if cls.STRONG_GAP_OVERRIDE_DELTA < 0:
            errors.append(
                f"STRONG_GAP_OVERRIDE_DELTA must be ≥ 0: {cls.STRONG_GAP_OVERRIDE_DELTA!r}"
            )

        # Average-down prevention (#195)
        if cls.AVG_DOWN_SCORE_DELTA < 0:
            errors.append(
                f"AVG_DOWN_SCORE_DELTA must be ≥ 0: {cls.AVG_DOWN_SCORE_DELTA!r}"
            )
        _pos("AVG_DOWN_LOOKBACK_MINUTES", cls.AVG_DOWN_LOOKBACK_MINUTES)
        _pos("AVG_DOWN_OVERRIDE_SCORE",   cls.AVG_DOWN_OVERRIDE_SCORE)

        # Tape-breadth filter (#212)
        if not (0.0 < cls.BREADTH_BEARISH_BUY_RATIO < 1.0):
            errors.append(
                f"BREADTH_BEARISH_BUY_RATIO must be in (0, 1): "
                f"{cls.BREADTH_BEARISH_BUY_RATIO!r}"
            )
        if not (0.0 < cls.BREADTH_BULLISH_SELL_RATIO < 1.0):
            errors.append(
                f"BREADTH_BULLISH_SELL_RATIO must be in (0, 1): "
                f"{cls.BREADTH_BULLISH_SELL_RATIO!r}"
            )
        if cls.BREADTH_PENALTY < 0:
            errors.append(
                f"BREADTH_PENALTY must be ≥ 0: {cls.BREADTH_PENALTY!r}"
            )
        _pos("BREADTH_MIN_CANDIDATES", cls.BREADTH_MIN_CANDIDATES)

        # Sector-rank bias (#215)
        if cls.SECTOR_RANK_BIAS_STEP < 0:
            errors.append(
                f"SECTOR_RANK_BIAS_STEP must be ≥ 0: "
                f"{cls.SECTOR_RANK_BIAS_STEP!r}"
            )
        if cls.SECTOR_RANK_BIAS_MAX < 0:
            errors.append(
                f"SECTOR_RANK_BIAS_MAX must be ≥ 0: "
                f"{cls.SECTOR_RANK_BIAS_MAX!r}"
            )
        _pos("SECTOR_RANK_MIN_SECTORS", cls.SECTOR_RANK_MIN_SECTORS)

        # Sector-cascade exit (#149)
        if cls.SECTOR_CASCADE_DROP_THRESHOLD <= 0:
            errors.append(
                f"SECTOR_CASCADE_DROP_THRESHOLD must be > 0: "
                f"{cls.SECTOR_CASCADE_DROP_THRESHOLD!r}"
            )
        if cls.SECTOR_CASCADE_OPPOSITE_FLOOR < 0:
            errors.append(
                f"SECTOR_CASCADE_OPPOSITE_FLOOR must be ≥ 0: "
                f"{cls.SECTOR_CASCADE_OPPOSITE_FLOOR!r}"
            )
        _pos("SECTOR_CASCADE_MIN_OPEN", cls.SECTOR_CASCADE_MIN_OPEN)

        # NSE early-close calendar (#217) — format check only
        for date_str, val in cls.NSE_EARLY_CLOSE_DATES_2026.items():
            if (
                not isinstance(val, tuple)
                or len(val) != 2
                or not all(isinstance(x, int) for x in val)
                or not (0 <= val[0] <= 23)
                or not (0 <= val[1] <= 59)
            ):
                errors.append(
                    f"NSE_EARLY_CLOSE_DATES_2026[{date_str!r}] must be "
                    f"(hour:int 0-23, minute:int 0-59), got {val!r}"
                )

        # Earnings blackout calendar (#219) — format check only
        if cls.EARNINGS_BLACKOUT_ENABLED:
            for date_str, syms in cls.EARNINGS_BLACKOUT_SYMBOLS_2026.items():
                if not isinstance(syms, list) or not all(
                    isinstance(s, str) for s in syms
                ):
                    errors.append(
                        f"EARNINGS_BLACKOUT_SYMBOLS_2026[{date_str!r}] "
                        f"must be list[str], got {syms!r}"
                    )

        # India VIX thresholds
        if not (0.0 < cls.VIX_SPIKE_PCT <= 100.0):
            errors.append(
                f"VIX_SPIKE_PCT must be in (0, 100]: {cls.VIX_SPIKE_PCT!r}"
            )
        if cls.VIX_HIGH_THRESHOLD <= 0:
            errors.append(
                f"VIX_HIGH_THRESHOLD must be > 0: {cls.VIX_HIGH_THRESHOLD!r}"
            )
        if cls.VIX_LOW_THRESHOLD <= 0:
            errors.append(
                f"VIX_LOW_THRESHOLD must be > 0: {cls.VIX_LOW_THRESHOLD!r}"
            )
        if cls.VIX_LOW_THRESHOLD >= cls.VIX_HIGH_THRESHOLD:
            errors.append(
                f"VIX_LOW_THRESHOLD ({cls.VIX_LOW_THRESHOLD}) must be < "
                f"VIX_HIGH_THRESHOLD ({cls.VIX_HIGH_THRESHOLD})"
            )

        # Tax / charges
        _pct("TAX_RATE_PCT", cls.TAX_RATE_PCT)
        _pct("TAX_CESS_PCT", cls.TAX_CESS_PCT)

        # Entry-burst cap (#179)
        if cls.ENTRY_BURST_CAP_MAX_ENTRIES_PER_60S < 0:
            errors.append(
                f"ENTRY_BURST_CAP_MAX_ENTRIES_PER_60S must be ≥ 0: "
                f"{cls.ENTRY_BURST_CAP_MAX_ENTRIES_PER_60S!r}"
            )

        # Directional auto-pause (#251)
        if not (0.0 <= cls.DIRECTIONAL_PAUSE_WR_THRESHOLD <= 1.0):
            errors.append(
                f"DIRECTIONAL_PAUSE_WR_THRESHOLD must be in [0, 1]: "
                f"{cls.DIRECTIONAL_PAUSE_WR_THRESHOLD!r}"
            )
        if cls.DIRECTIONAL_PAUSE_LOOKBACK_DAYS <= 0:
            errors.append(
                f"DIRECTIONAL_PAUSE_LOOKBACK_DAYS must be > 0: "
                f"{cls.DIRECTIONAL_PAUSE_LOOKBACK_DAYS!r}"
            )
        if cls.DIRECTIONAL_PAUSE_MIN_TRADES <= 0:
            errors.append(
                f"DIRECTIONAL_PAUSE_MIN_TRADES must be > 0: "
                f"{cls.DIRECTIONAL_PAUSE_MIN_TRADES!r}"
            )
        if not (-100.0 <= cls.DIRECTIONAL_PAUSE_NIFTY_FLOOR_PCT <= 100.0):
            errors.append(
                f"DIRECTIONAL_PAUSE_NIFTY_FLOOR_PCT must be in [-100, 100]: "
                f"{cls.DIRECTIONAL_PAUSE_NIFTY_FLOOR_PCT!r}"
            )
        # Directional pause — opposing-thin cap.
        if cls.DIRECTIONAL_PAUSE_OPPOSING_MIN_TRADES < 0:
            errors.append(
                f"DIRECTIONAL_PAUSE_OPPOSING_MIN_TRADES must be ≥ 0: "
                f"{cls.DIRECTIONAL_PAUSE_OPPOSING_MIN_TRADES!r}"
            )
        if cls.DIRECTIONAL_PAUSE_OPPOSING_THIN_MAX_ENTRIES < 0:
            errors.append(
                f"DIRECTIONAL_PAUSE_OPPOSING_THIN_MAX_ENTRIES must be ≥ 0: "
                f"{cls.DIRECTIONAL_PAUSE_OPPOSING_THIN_MAX_ENTRIES!r}"
            )
        # Directional pause — NIFTY-bounce bypass.
        if not (0.0 < cls.DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_PCT <= 100.0):
            errors.append(
                f"DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_PCT must be in (0, 100]: "
                f"{cls.DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_PCT!r}"
            )
        if cls.DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_MIN_SCANS < 1:
            errors.append(
                f"DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_MIN_SCANS must be ≥ 1: "
                f"{cls.DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_MIN_SCANS!r}"
            )
        # Directional pause — tape-breadth bypass.
        if not (0.0 < cls.DIRECTIONAL_PAUSE_BREADTH_BYPASS_RATIO <= 1.0):
            errors.append(
                f"DIRECTIONAL_PAUSE_BREADTH_BYPASS_RATIO must be in (0, 1]: "
                f"{cls.DIRECTIONAL_PAUSE_BREADTH_BYPASS_RATIO!r}"
            )
        if cls.DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_PAUSED_SIDE < 1:
            errors.append(
                f"DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_PAUSED_SIDE must be ≥ 1: "
                f"{cls.DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_PAUSED_SIDE!r}"
            )
        if cls.DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_TOTAL < 1:
            errors.append(
                f"DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_TOTAL must be ≥ 1: "
                f"{cls.DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_TOTAL!r}"
            )
        # MIN_TOTAL must be ≥ BREADTH_MIN_CANDIDATES — otherwise the
        # bypass checks a snapshot the scanner never publishes.
        if cls.DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_TOTAL < cls.BREADTH_MIN_CANDIDATES:
            errors.append(
                f"DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_TOTAL "
                f"({cls.DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_TOTAL}) must be ≥ "
                f"BREADTH_MIN_CANDIDATES ({cls.BREADTH_MIN_CANDIDATES})"
            )
        # Roadmap #179a per-budget burst-cap delta.
        if not isinstance(cls.BUDGET_BURST_CAP_DELTA, dict):
            errors.append(
                f"BUDGET_BURST_CAP_DELTA must be a dict: "
                f"{type(cls.BUDGET_BURST_CAP_DELTA).__name__}"
            )
        else:
            for regime in ("TINY", "SMALL", "NORMAL", "LARGE"):
                if regime not in cls.BUDGET_BURST_CAP_DELTA:
                    errors.append(
                        f"BUDGET_BURST_CAP_DELTA missing regime {regime!r}"
                    )

        # Rolling-PF pause (#253)
        if cls.ROLLING_PF_PAUSE_THRESHOLD < 0:
            errors.append(
                f"ROLLING_PF_PAUSE_THRESHOLD must be ≥ 0: "
                f"{cls.ROLLING_PF_PAUSE_THRESHOLD!r}"
            )
        if cls.ROLLING_PF_PAUSE_NET_FLOOR > 0:
            errors.append(
                f"ROLLING_PF_PAUSE_NET_FLOOR must be ≤ 0 (it is a loss "
                f"floor): {cls.ROLLING_PF_PAUSE_NET_FLOOR!r}"
            )
        if cls.ROLLING_PF_PAUSE_LOOKBACK_DAYS <= 0:
            errors.append(
                f"ROLLING_PF_PAUSE_LOOKBACK_DAYS must be > 0: "
                f"{cls.ROLLING_PF_PAUSE_LOOKBACK_DAYS!r}"
            )
        if cls.ROLLING_PF_PAUSE_MIN_TRADES <= 0:
            errors.append(
                f"ROLLING_PF_PAUSE_MIN_TRADES must be > 0: "
                f"{cls.ROLLING_PF_PAUSE_MIN_TRADES!r}"
            )

        # Intraday volume baselines (#260)
        if not isinstance(cls.INTRADAY_VOLUME_BASELINE_ENABLED, bool):
            errors.append(
                f"INTRADAY_VOLUME_BASELINE_ENABLED must be bool "
                f"(got {cls.INTRADAY_VOLUME_BASELINE_ENABLED!r})"
            )
        _pos("INTRADAY_VOLUME_BASELINE_LOOKBACK_DAYS",
             cls.INTRADAY_VOLUME_BASELINE_LOOKBACK_DAYS)
        _pos("INTRADAY_VOLUME_BASELINE_MIN_SAMPLES",
             cls.INTRADAY_VOLUME_BASELINE_MIN_SAMPLES)
        if cls.INTRADAY_VOLUME_BASELINE_MIN_SAMPLES > cls.INTRADAY_VOLUME_BASELINE_LOOKBACK_DAYS:
            errors.append(
                f"INTRADAY_VOLUME_BASELINE_MIN_SAMPLES "
                f"({cls.INTRADAY_VOLUME_BASELINE_MIN_SAMPLES}) must be ≤ "
                f"INTRADAY_VOLUME_BASELINE_LOOKBACK_DAYS "
                f"({cls.INTRADAY_VOLUME_BASELINE_LOOKBACK_DAYS})"
            )

        # Strategy config snapshot (Roadmap #259) — version is plain str
        if not isinstance(cls.STRATEGY_CONFIG_VERSION, str) or not cls.STRATEGY_CONFIG_VERSION:
            errors.append(
                f"STRATEGY_CONFIG_VERSION must be a non-empty str "
                f"(got {cls.STRATEGY_CONFIG_VERSION!r})"
            )
        if not isinstance(cls.STRATEGY_CONFIG_KEYS, tuple):
            errors.append(
                f"STRATEGY_CONFIG_KEYS must be a tuple "
                f"(got {type(cls.STRATEGY_CONFIG_KEYS).__name__})"
            )

        return errors

    # ── Strategy-config version & hash (Roadmap #259 scope) ──────
    # Records-grade fingerprint of every strategy-relevant constant
    # the runtime decides on. Two consumers:
    #   1. `modes/trade/candidate_telemetry.py` stamps `config_version`
    #      and `config_hash` on every candidate row, so later replay
    #      knows which rule set produced the score / decision.
    #   2. `scripts/trade/backtest.py` (#24) records the same pair on every
    #      synthetic trade for direct apples-to-apples comparison
    #      with live runs.
    #
    # `version` is a short human-readable string that bumps whenever
    # the underlying constants set is widened. `hash` is the SHA-256
    # of the JSON-serialised constants and is the durable identifier
    # — even a one-char change to any tracked constant flips it.
    #
    # Adding a new gate? Add its constant to STRATEGY_CONFIG_KEYS so
    # the hash starts tracking it. Removing one? Same, in reverse.
    # Pure observability changes (logging only) need not be added.
    STRATEGY_CONFIG_VERSION: str = "v2.1-2026-06-09-GAP_AND_GO_1.1"

    STRATEGY_CONFIG_KEYS: tuple = (
        # Stage ladder (rollout doc)
        "TRADE_STAGE_NAME",
        # Sizing / budget
        "MAX_BUDGET_INR", "MAX_POSITIONS_OVERRIDE", "MAX_POSITION_PCT",
        "SCORE_WEIGHTED_SIZING_ENABLED", "MAX_REENTRIES_PER_STOCK",
        # Risk / SL / target
        "DEFAULT_STOP_LOSS_PCT", "DEFAULT_TARGET_PCT",
        "MAX_LOSS_PER_DAY_PCT", "ATR_MULTIPLIER", "ATR_PERIOD",
        "MAX_INTRADAY_SL_PCT", "MIN_SL_DISTANCE_PCT",
        "RR_TARGET_RATIO", "RR_HARD_FLOOR", "RR_GIVEUP_AFTER_FAILS",
        "TRAIL_AFTER_RISK_MULTIPLE", "TRAIL_STEP_PCT",
        "MAX_SPREAD_PCT", "MAX_IMPACT_COST_PCT", "MIN_EXPECTED_PROFIT",
        "VWAP_DRIFT_CHECK_ENABLED", "VWAP_DRIFT_WARN_PCT",
        "SLIPPAGE_PCT",
        # Timing
        "MARKET_OPEN_HOUR", "MARKET_OPEN_MINUTE",
        "SQUARE_OFF_HOUR", "SQUARE_OFF_MINUTE",
        "ENTRY_DELAY_MINUTES", "ENTRY_MIN_MOVE_PCT",
        "ENTRY_DECISION_FLOOR_MINUTES_AFTER_OPEN",
        "PRICE_POLL_SECONDS", "POSITION_REVIEW_MINUTES",
        "TARGET_DECAY_AFTER_HOUR", "TARGET_DECAY_PCT",
        "MIN_MINUTES_FOR_ENTRY",
        # Scanner / score floors
        "TRADE_STRATEGY_PROFILE",
        "GAP_GO_MIN_GAP_PCT", "GAP_GO_MAX_GAP_PCT",
        "GAP_GO_VOLUME_MULTIPLE", "GAP_GO_DAILY_CAP",
        "GAP_GO_SQUARE_OFF_HOUR", "GAP_GO_SQUARE_OFF_MINUTE",
        "GAP_GO_SKIP_RANGE_REGIME",
        "GAP_GO_RSI_BUY_CEILING", "GAP_GO_RSI_SELL_FLOOR",
        "GAP_GO_ENTRY_AFTER_CANDLE_CLOSE", "GAP_GO_GAP_HOLD_MIN_PCT",
        "GAP_GO_SCORE_CONTRADICTION_BLOCK", "GAP_GO_USE_CANDLE_CLOSE_PRICE",
        "MIN_SCORE", "CANDLE_INTERVAL",
        "SCAN_UNIVERSE", "SCAN_MIN_PRICE", "SCAN_MAX_PRICE",
        "OPPORTUNITY_RESCAN_MINUTES", "NIFTY_RECHECK_MINUTES",
        # RVol / time normalisation
        "RVOL_TIME_NORMALIZATION_ENABLED", "RVOL_FLOOR_BY_HOUR",
        "INTRADAY_VOLUME_BASELINE_ENABLED",
        # Lunch lull
        "LUNCH_LULL_ENABLED", "LUNCH_LULL_SCORE_OVERRIDE",
        # Choppy-morning pause
        "CHOPPY_MORNING_PAUSE_ENABLED",
        "CHOPPY_PAUSE_ADX_THRESHOLD", "CHOPPY_PAUSE_MINUTES",
        # Pattern penalties
        "PATTERN_CONTRADICTION_PENALTY_ENABLED",
        "PATTERN_CONTRADICTION_PENALTY", "PATTERN_INDECISION_PENALTY",
        # Tape breadth
        "BREADTH_FILTER_ENABLED", "BREADTH_BEARISH_BUY_RATIO",
        "BREADTH_BULLISH_SELL_RATIO", "BREADTH_PENALTY",
        "BREADTH_MIN_CANDIDATES",
        # Budget regime
        "BUDGET_REGIME_ENABLED",
        "BUDGET_TIER_SMALL", "BUDGET_TIER_NORMAL", "BUDGET_TIER_LARGE",
        # Directional pause
        "DIRECTIONAL_PAUSE_WR_THRESHOLD",
        "DIRECTIONAL_PAUSE_LOOKBACK_DAYS",
        "DIRECTIONAL_PAUSE_MIN_TRADES",
        # Rolling PF
        "ROLLING_PF_PAUSE_THRESHOLD",
        "ROLLING_PF_PAUSE_NET_FLOOR",
        "ROLLING_PF_PAUSE_LOOKBACK_DAYS",
        "ROLLING_PF_PAUSE_MIN_TRADES",
        # VIX
        "VIX_SPIKE_PCT", "VIX_HIGH_THRESHOLD", "VIX_LOW_THRESHOLD",
    )

    @classmethod
    def snapshot_hash(cls) -> tuple[str, str]:
        """
        Returns (version, hash_hex) for the current strategy-config
        snapshot. `hash_hex` is SHA-256 of the JSON-serialised values
        of every key in STRATEGY_CONFIG_KEYS.

        Missing keys (config typo or refactor lag) are recorded as the
        literal string "<MISSING>" so the hash still changes when a
        new key is added or removed; a downstream consumer that sees
        "<MISSING>" in the audit can investigate the drift.
        """
        import hashlib
        import json
        snapshot = {}
        for k in cls.STRATEGY_CONFIG_KEYS:
            try:
                v = getattr(cls, k)
            except AttributeError:
                v = "<MISSING>"
            # Make non-JSON-serialisable values stable.
            try:
                json.dumps(v, sort_keys=True, default=str)
            except (TypeError, ValueError):
                v = repr(v)
            snapshot[k] = v
        blob = json.dumps(snapshot, sort_keys=True, default=str).encode("utf-8")
        digest = hashlib.sha256(blob).hexdigest()
        return cls.STRATEGY_CONFIG_VERSION, digest[:16]

    @classmethod
    def calculate_charges(
        cls,
        total_buy_turnover:  float,
        total_sell_turnover: float,
        num_orders:          int,
        claude_calls:        int = 0,
    ) -> dict:
        """
        Calculates Zerodha charges, taxes, and fees using the
        cost parameters defined in this config.

        Shared by OrderEngine.calculate_charges() and
        ReportWriter._calculate_combined_pnl() to avoid duplication.

        Returns a dict with each charge component and the total.
        """
        total_turnover = total_buy_turnover + total_sell_turnover

        brokerage_flat = cls.ZERODHA_BROKERAGE_FLAT * num_orders
        brokerage_pct  = total_turnover * cls.ZERODHA_BROKERAGE_PCT / 100
        brokerage      = min(brokerage_flat, brokerage_pct) if num_orders > 0 else 0

        stt          = total_sell_turnover * cls.STT_SELL_PCT / 100
        exchange_txn = total_turnover * cls.EXCHANGE_TXN_PCT / 100
        sebi         = total_turnover / 1e7 * cls.SEBI_CHARGE_PER_CR
        gst          = (brokerage + sebi + exchange_txn) * cls.GST_PCT / 100
        stamp_duty   = total_buy_turnover * cls.STAMP_DUTY_BUY_PCT / 100

        total_charges = brokerage + stt + exchange_txn + gst + sebi + stamp_duty
        claude_cost   = claude_calls * cls.CLAUDE_COST_PER_CALL

        return {
            "total_turnover":        round(total_turnover, 2),
            "buy_turnover":          round(total_buy_turnover, 2),
            "sell_turnover":         round(total_sell_turnover, 2),
            "num_orders":            num_orders,
            "brokerage":             round(brokerage, 2),
            "stt":                   round(stt, 2),
            "exchange_txn":          round(exchange_txn, 2),
            "gst":                   round(gst, 2),
            "sebi_charges":          round(sebi, 4),
            "stamp_duty":            round(stamp_duty, 2),
            "total_tax_and_charges": round(total_charges, 2),
            # AI API cost is informational only (FYI) — NOT deducted from
            # net profit. Trading P&L reflects market + Zerodha costs only.
            "claude_api_cost":       round(claude_cost, 2),
            "total_costs":           round(total_charges, 2),
            "zerodha_monthly_fyi":   cls.ZERODHA_MONTHLY_COST,
        }
