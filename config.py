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

    # ── Edit these two lines when you upgrade plans ───────────────

    # Options: "free" | "pro" | "max"
    CLAUDE_PLAN: str  = "pro"

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
    #     (risky, Zerodha auto-squares MIS at 3:25 with penalty)
    MARKET_OPEN_HOUR:   int = 9
    MARKET_OPEN_MINUTE: int = 15
    SQUARE_OFF_HOUR:    int = 15
    SQUARE_OFF_MINUTE:  int = 10
    PRE_MARKET_MINUTES_BEFORE: int = 15   # scan starts this many min before open
    CUTOFF_MINUTES_BEFORE_CLOSE: int = 30   # skip trading if less than this many min to square-off

    # ENTRY_DELAY_MINUTES: after market open, observe prices for this many
    #   minutes before entering positions. Only stocks with >0.3% directional
    #   movement from open price are entered. Helps avoid opening whipsaws.
    #   Set to 0 to enter immediately at market open (old behaviour).
    ENTRY_DELAY_MINUTES: int = 5
    ENTRY_MIN_MOVE_PCT:  float = 0.3   # min % move from open to confirm direction

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

    # ── Position Limits ───────────────────────────────────────────
    # MAX_POSITIONS: auto-set at runtime by dynamic_max_positions().
    #   DO NOT manually edit this — it is overwritten when set_budget() runs.
    #   To force a specific value, set MAX_POSITIONS_OVERRIDE instead.
    MAX_POSITIONS:    int = 3   # runtime default; overwritten by dynamic_max_positions()
    MAX_POSITIONS_OVERRIDE: int = 0  # 0 = auto-scale with budget; >0 = locked manual value
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
    #   SL = entry - (ATR_MULTIPLIER × ATR) for longs.
    #   Target = entry + (ATR_MULTIPLIER × RR_TARGET_RATIO × ATR).
    #   Falls back to DEFAULT_STOP_LOSS_PCT if historical data is unavailable.
    ATR_MULTIPLIER: float = 1.5
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
    # R:R = (target distance) / (stop distance).
    # ATR produces a base R:R of RR_TARGET_RATIO (1.5:1).
    #
    # SINGLE FLOOR — UNIFORM ALL DAY (#242 / #243).
    # Roadmap #242 removed entry-time target compression (the previous
    # 20%/25% target cuts after 1 PM / 2 PM). Roadmap #243 followed
    # through by collapsing the time-tiered floors and the dead
    # relaxation/retry knobs into a single always-on floor:
    #
    #   RR_HARD_FLOOR = 1.3   ← the only knob that matters
    #
    # Rationale (analyst lens — copilot/analyst-review.md):
    #   1. Pre-shrinking entry targets while the always-on hard floor
    #      then rejected the resulting R:R was a self-defeating loop —
    #      both x and y were ours. Removed by #242.
    #   2. After #242 every time-of-day floor (afternoon/late) was
    #      already pinned to 1.3 = RR_HARD_FLOOR; the time-routing
    #      added zero behavioural value, only log-label noise.
    #   3. Adaptive relaxation ("we haven't traded in an hour, drop the
    #      bar") is the same instinct that bankrupts retail traders
    #      (see #235). RR_HARD_FLOOR always won last anyway, so the
    #      relaxation branch never produced a different outcome.
    #
    # KEPT: RR_GIVEUP_AFTER_FAILS — after N zero-entry scans we stop
    # trading entirely. That's the keeper, the "today is not a trading
    # day" signal. RR_TARGET_RATIO sets the default ATR target so
    # default-ATR trades produce R:R = 1.5 (clears the 1.3 floor with
    # ~0.2 of headroom for tick-rounding noise).
    RR_TARGET_RATIO:       float = 1.5   # base R:R from ATR (target = SL × this)
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
    TRAIL_AFTER_RISK_MULTIPLE: float = 1.5
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
    TARGET_DECAY_AFTER_HOUR: int   = 14     # 2:00 PM IST
    TARGET_DECAY_PCT:        float = 25.0   # reduce target by this %

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
    LOSER_EXIT_HOUR:   int = 14   # 2:45 PM IST
    LOSER_EXIT_MINUTE: int = 45

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
    SHORT_ENTRY_CUTOFF_HOUR: int = 13   # 1:00 PM — no new shorts after 1 PM

    # ── Thursday Expiry Adjustments ───────────────────────────────
    # On weekly F&O expiry Thursdays, NIFTY stocks see wider swings
    # driven by options settlement. All of the values below apply
    # ONLY on Thursdays (auto-detected via weekday check).
    #
    # EXPIRY_ATR_BUMP: added to ATR_MULTIPLIER (wider SLs).
    # EXPIRY_POSITION_REDUCTION: MAX_POSITIONS reduced by this many —
    #   skipped when budget < EXPIRY_POSITION_REDUCTION_MIN_BUDGET so
    #   small accounts keep full slot count for rotation capacity.
    # EXPIRY_SCORE_BUMP: added to V2_MIN_SCORE (demand stronger signals).
    # EXPIRY_STAGNANT_EXTRA_MINUTES: extends stagnant timer on expiry.
    # EXPIRY_ENTRY_DELAY_MINUTES: observation window is "market_open + N"
    #   — at 9:15 start with 30 min → entry at 9:45. At 9:30 start →
    #   also 9:45 (we already observed 15 min). At 10:00 start → late
    #   path uses EXPIRY_ENTRY_DELAY_LATE_FLOOR from current time.
    # EXPIRY_MAX_TRADES_PER_DAY: caps total trades on expiry (each
    #   exit+entry cycle costs ~Rs.36 in charges).
    # EXPIRY_MIN_SL_DISTANCE_PCT: overrides MIN_SL_DISTANCE_PCT —
    #   wider floor on expiry accommodates bigger option-driven swings.
    EXPIRY_ATR_BUMP:                      float = 0.3
    EXPIRY_POSITION_REDUCTION:            int   = 1
    EXPIRY_POSITION_REDUCTION_MIN_BUDGET: float = 100000.0  # Rs.1L
    EXPIRY_SCORE_BUMP:                    float = 1.0
    EXPIRY_STAGNANT_EXTRA_MINUTES:        int   = 15
    EXPIRY_ENTRY_DELAY_MINUTES:           int   = 30
    EXPIRY_ENTRY_DELAY_LATE_FLOOR:        int   = 15
    EXPIRY_MAX_TRADES_PER_DAY:            int   = 5
    EXPIRY_MIN_SL_DISTANCE_PCT:           float = 1.0

    # ── Entry Filter — RSI Contradiction (symmetric) ─────────────
    # Blocks trades that fight or chase extreme RSI readings.
    # Applies EVERY day (not expiry-specific).
    #   SELL blocked when RSI > RSI_SELL_BLOCK_THRESHOLD (default 70)
    #     → shorting into strong buying pressure
    #   BUY blocked when RSI > RSI_BUY_BLOCK_THRESHOLD (default 75)
    #     → buying an already-extended overbought move
    #   BUY  blocked when RSI < 30 (hardcoded)  → oversold, wait for reversal
    #   SELL blocked when RSI < 25 (hardcoded)  → selling an extended low
    RSI_SELL_BLOCK_THRESHOLD: float = 70.0
    RSI_BUY_BLOCK_THRESHOLD:  float = 75.0

    # ── Entry Filter — VWAP Trend + Extension ────────────────────
    # Two-sided VWAP guard (every day, activates after 10:15 AM when
    # VWAP has enough candles to be stable):
    #   1. Trend-fight block: BUY if price > 0.3% BELOW VWAP, or
    #      SELL if price > 0.3% ABOVE VWAP. Fighting institutional flow.
    #   2. Extension-chase block: BUY if price > VWAP_EXTENSION_BLOCK_PCT
    #      ABOVE VWAP, or SELL if price > that BELOW VWAP. Move has
    #      already happened, mean-reversion risk high.
    # Extension block is overridden when |score| >= VWAP_EXT_SCORE_OVERRIDE
    # (a very strong signal justifies chasing).
    VWAP_EXTENSION_BLOCK_PCT: float = 0.8
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
    GAP_COHERENCE_GATE_ENABLED:  bool  = True
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
    CIRCUIT_LIMIT_GUARD_ENABLED: bool  = True
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
    REJECTION_AUDIT_ENABLED:     bool  = True

    # Dashboard cumulative-P&L chart overlays a thin vertical line
    # at every trading day where the bot's git SHA changed vs the
    # previous day, with a hover tooltip showing the commit subject.
    # Visual proof of when a strategy change inflected the equity
    # curve. Roadmap D13 + V2 #246. Read by `Dashboard/render_html.py`
    # via `Dashboard/strategy_versions.py`. Set False to hide overlay
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
    MAX_TRADES_PER_DAY: int = 12

    # ══════════════════════════════════════════════════════════════
    # V2 — CANDLE STRATEGY SETTINGS (default strategy)
    # ══════════════════════════════════════════════════════════════
    # V2 pre-filters stocks using candlestick patterns and technical
    # indicators (EMA, RSI, VWAP, SuperTrend) before sending the
    # top candidates to Claude. This gives Claude richer technical
    # context and higher signal-to-noise ratio.
    #
    # These settings apply when running: python main.py --mode trade (default)

    # V2_CANDLE_RESCAN_MINUTES: how often to re-run candle analysis
    # on the universe during monitoring (separate from Claude review).
    # This is FREE (no Claude cost) — just Zerodha historical API calls.
    # Lower = detect new setups faster, but more API calls.
    V2_CANDLE_RESCAN_MINUTES: int = 15

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
    MIN_EXPECTED_PROFIT: float = 135.0

    # V2_MIN_SCORE: minimum absolute technical score for a stock to
    # pass the pre-filter. Lower = more candidates for Claude to
    # choose from (more Claude context). Higher = fewer but stronger signals.
    # Range: 1-5 recommended. Default 2 = mild signal required.
    V2_MIN_SCORE: float = 2.0

    # V2_CANDLE_INTERVAL: primary candle interval for pattern detection.
    # Options: "5minute", "10minute", "15minute", "30minute"
    # 15minute = good balance of signal clarity vs responsiveness.
    # 5minute = more signals but noisier patterns.
    V2_CANDLE_INTERVAL: str = "15minute"

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
    STAGNANT_EXIT_MINUTES:      int   = 45
    # Adverse threshold: fire stagnant-exit if move_pct is below this
    # negative number (i.e., losing by more than this %).
    STAGNANT_ADVERSE_PCT:       float = 0.2
    # Dead-flat band: fire stagnant-exit if |move_pct| is below this
    # (truly going nowhere — neither up nor down in a meaningful way).
    STAGNANT_DEAD_FLAT_PCT:     float = 0.1

    # ── Stagnant-Drift Hard-Max (#172, Tier 2) ─────────────────
    # Second-tier checkpoint that uses progress-to-target rather than
    # absolute move-band. Catches drifters that survived the 45-min
    # directional check by sitting just outside the dead-flat band on
    # the snapshot tick. See STAGNANT_EXIT_MINUTES decision history
    # (entry dated 2026-04-20) for the UNITDSPR motivating case.
    STAGNANT_HARD_MAX_ENABLED:    bool  = True
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
    # When the periodic candle re-scan (V2_CANDLE_RESCAN_MINUTES) sees
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
    SIGNAL_REVERSAL_EXIT_ENABLED:    bool  = True
    SIGNAL_REVERSAL_SCORE:           float = 7.0
    SIGNAL_REVERSAL_REQUIRE_PATTERN: bool  = True

    # ── Signal-Decay Exit (#188) ──────────────────────────────────
    # Companion to signal-reversal: catches *same-direction thesis
    # decay* — the entry signal hasn't flipped to the opposite side
    # (which #174 would already catch), but its strength has collapsed
    # to a small fraction of what it was at entry. Without this gate
    # such trades sit in the slow-positive corridor for hours and
    # only exit on LOSER_EXIT, burning a slot the whole time.
    #
    # Triggers (BUY position, mirrored for SELL):
    #   abs(entry_score) >= SIGNAL_DECAY_MIN_ENTRY_SCORE   (only act
    #     on trades that started with real conviction — a +3 → +1
    #     drift is statistical noise, not decay)
    #   AND fresh_score is still SAME-SIGN as entry      (opposite
    #     flips are #174's job)
    #   AND abs(fresh_score) < abs(entry_score) * SIGNAL_DECAY_FRACTION
    #   AND elapsed_minutes >= SIGNAL_DECAY_MIN_HOLD_MINUTES
    #   AND pnl < initial_risk * SIGNAL_DECAY_WINNER_SKIP_R_MULTIPLE
    #     (book-and-go below 1R: sub-1R profit has no trailing-stop
    #     cushion anyway — the stop sits at or below entry, so any
    #     pullback gives it all back. Winners ≥1R keep running on
    #     the trailing stop. If `initial_sl` is missing — e.g. a
    #     restart-rehydrated position — fall back to the conservative
    #     `pnl > 0` skip so we never dump a profitable legacy trade.)
    #
    # MOTIVATING CASE: BHARTIARTL 2026-04-21 — entered BUY @ +10.1 at
    #   09:42, re-scored +3.6 (Δ-6.5) at 10:31, sat in the slow-
    #   positive corridor for 5h 3min and exited LOSER_EXIT @ -Rs.16
    #   at 14:45. Max favourable was +Rs.109 at 11:05 (0.72R). Initial
    #   #188 with a strict `pnl > 0` skip would NOT have fired between
    #   09:57 and 13:30 because the trade was always in small profit.
    #   With the 1R winner-skip the gate fires at the 10:31 re-scan
    #   (pnl Rs.+41 = 0.27R < 1R) and books +Rs.41 instead of drifting
    #   to -Rs.16 at LOSER_EXIT.
    SIGNAL_DECAY_EXIT_ENABLED:          bool  = True
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
    # — V2 scanner does, V1 (frozen) does not, so V1 path is unaffected.
    FRESH_ENTRY_RECHECK_ENABLED:        bool  = True
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
    ADX_ENTRY_GATE_ENABLED:  bool  = True
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
    ATR_SIZING_ENABLED:      bool  = True
    RISK_PER_TRADE_PCT:      float = 0.5

    # ── Per-Symbol Re-Entry Cooldown (Roadmap #161) ───────────────
    # RE_ENTRY_COOLDOWN_ENABLED: after ANY exit of a symbol (SL, target,
    #   stagnant, external), block re-entry in the SAME direction for
    #   RE_ENTRY_COOLDOWN_MINUTES. Stops the "re-enter immediately on
    #   same signal" loop that burns Rs.40 round-trip each time.
    #   Opposite direction is still allowed (reversal setups).
    # RE_ENTRY_COOLDOWN_MINUTES: block window after any exit.
    # RE_ENTRY_SCORE_OVERRIDE: very strong score overrides cooldown.
    RE_ENTRY_COOLDOWN_ENABLED:  bool  = True
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
    MIN_PROFIT_CHARGE_MULTIPLE: float = 3.0

    # ── Daily Loss Soft-Stop Hysteresis (Roadmap #163) ────────────
    # DAILY_LOSS_SOFT_STOP_PCT: when day P&L ≤ -this% of budget, stop
    #   taking NEW entries but keep monitoring existing positions.
    #   Hard circuit breaker at MAX_LOSS_PER_DAY_PCT still closes all.
    #   This gives a hysteresis band — prevents "open loser → hit SL
    #   → open another loser" pattern, but doesn't force exits that
    #   might recover in a green afternoon.
    # Set to 0 to disable (no soft stop, only hard CB).
    DAILY_LOSS_SOFT_STOP_PCT: float = 1.5

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
    PEAK_DRAWDOWN_STOP_PCT:     float = 1.5
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
    # equalled V2_MIN_SCORE (5.5) and silently turned the gate
    # into a no-op (anything passing the entry score gate also
    # passed lunch). Final landing: 5.7 — a meaningful step above
    # V2_MIN_SCORE so the gate still bites on truly weak lunch-
    # window signals (5.5–5.7 range) while admitting the
    # borderline-but-profitable 5.7+ band the audit said we were
    # missing. Conservative tweak — not disabling the gate.
    LUNCH_LULL_ENABLED:         bool  = True
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
    CHOPPY_MORNING_PAUSE_ENABLED:                 bool  = True
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
    VIX_SPIKE_ENTRY_PAUSE_ENABLED:                bool  = True

    # ── Tape-Breadth Filter (Roadmap #212) ────────────────────────
    # On heavy-FII-sell days the broader tape is bearish: 70 %+ of the
    # pre-filter set scores SELL, sectors weak across the board. BUYs
    # entered on such days underperform their score-implied edge by
    # ~30 % (anecdotal Apr 2026 backtests). #212 counts BUY vs SELL
    # candidates AFTER the V2_MIN_SCORE filter and applies a small
    # score penalty to the minority side. The penalty operates on
    # magnitude (sign preserved) so weak counter-tape candidates fall
    # below `V2_MIN_SCORE` naturally instead of being hard-blocked.
    # BREADTH_BEARISH_BUY_RATIO: when BUY count ≤ this fraction of
    #     {BUY+SELL}, tape is bearish — penalize remaining BUYs.
    # BREADTH_BULLISH_SELL_RATIO: mirror for bullish tape (penalize
    #     SELLs).
    # BREADTH_PENALTY: subtract from |score| of the minority side.
    # BREADTH_MIN_CANDIDATES: skip the filter when the post-V2_MIN
    #     set is too small to be statistically meaningful.
    # Kill-switch: BREADTH_FILTER_ENABLED.
    BREADTH_FILTER_ENABLED:        bool  = True
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
    SECTOR_RANK_BIAS_ENABLED:      bool  = True
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
    SECTOR_CASCADE_EXIT_ENABLED:   bool  = True
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
    MTM_AWARE_CB_ENABLED:        bool  = True

    # ── Strong-Gap ADX Threshold Boost (Roadmap #194) ─────────────
    # When today's NIFTY opening gap is GAP_*_STRONG and continues
    # the prior-day direction (continuation, not reversal), raise
    # the ADX entry threshold and override score for the rest of the
    # day. Levels the field for fade-the-gap setups whose intraday
    # hit-rate sits below 40%. Reverts at session end.
    STRONG_GAP_ADX_BOOST_ENABLED: bool  = True
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
    AVG_DOWN_PREVENTION_ENABLED:    bool  = True
    AVG_DOWN_SCORE_DELTA:           float = 1.0
    AVG_DOWN_LOOKBACK_MINUTES:      int   = 120
    AVG_DOWN_OVERRIDE_SCORE:        float = 8.0

    # ── Pattern-direction entry veto (Roadmap #190) ──────────
    # Mirror of the SIGNAL_REVERSAL exit (#174) applied at ENTRY.
    # Candle patterns flow into combined_score as weighted contributions
    # but never act as a hard veto. A BUY can clear |score| ≥ 6.0 even
    # when the entry-tick pattern set contains a bearish reversal
    # (BEARISH_ENGULFING, EVENING_STAR, BEARISH_HARAMI, SHOOTING_STAR,
    # HANGING_MAN, THREE_BLACK_CROWS) — and vice-versa for SELL with
    # the bullish set. Live observations (2026-04-21): PNB BUY @ +6.1
    # with BEARISH_ENGULFING and TRENT BUY @ +6.4 with
    # BEARISH_ENGULFING both stagnated.
    #
    # Gate: if entry-tick patterns include an opposite-side reversal
    # AND |score| < PATTERN_VETO_OVERRIDE_SCORE, skip the entry.
    # Set PATTERN_VETO_ENABLED = False to disable.
    PATTERN_VETO_ENABLED:        bool  = True
    PATTERN_VETO_OVERRIDE_SCORE: float = 8.0

    # ── Pattern↔Tech Contradiction Penalty (Roadmap #200) ─────────
    # Patterns currently flow into combined_score as raw additive
    # contributions. Two failure modes were observed live:
    #   (a) Indecision noise: DOJI is a NEUTRAL candle (indecision).
    #       It tells us "no commitment" yet its weight currently
    #       survives into a directional verdict. Live observation
    #       (2026-04-21): NESTLEIND scored +5.6
    #       STRONG_BUY with both BEARISH_ENGULFING and DOJI present.
    #   (b) Direct contradiction: a bearish reversal pattern on a BUY
    #       verdict (or vice-versa) means the chart is *already*
    #       printing the flip — the score is reading momentum that's
    #       about to die. #190 PATTERN_VETO is a hard skip at very low
    #       conviction; this is a softer continuous penalty applied at
    #       SCANNER scoring time so downstream gates / Claude / sorting
    #       all see the de-risked score.
    # Apply at scanner combine point in stock_scanner_v2._analyse_stock,
    # *after* pattern_score + tech_score have been summed:
    #   - Contradiction (BUY-leaning + bearish pattern OR SELL-leaning
    #     + bullish pattern): subtract PATTERN_CONTRADICTION_PENALTY
    #     from |combined_score|, clamped so the score does NOT flip
    #     sign (we already have #190 for hard veto; this is just a
    #     conviction haircut).
    #   - Indecision (DOJI present, regardless of side): subtract
    #     PATTERN_INDECISION_PENALTY from |combined_score|, same
    #     no-flip clamp.
    # Both penalties stack — DOJI + BEARISH_ENGULFING on a BUY would
    # take 0.5 + 2.0 = 2.5 off the score magnitude.
    # Kill-switch: PATTERN_CONTRADICTION_PENALTY_ENABLED.
    PATTERN_CONTRADICTION_PENALTY_ENABLED: bool  = True
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
    VWAP_BAND_GATE_ENABLED:    bool  = True
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
    LATE_ENTRY_TIGHTENING_ENABLED: bool  = True
    LATE_ENTRY_HOUR:               int   = 10    # 10:00 IST and later
    # #239 (analyst pass, 2026-04-27): 0.5 → 1.0. The 2026-04-27
    # session-2 entries (HINDZINC/ADANIENSOL/HINDALCO at 10:27) all
    # passed the +0.5 bump and all faded — the bump was too gentle
    # to materially change which trades clear the bar. Post-10:00
    # trades have less than half the session left; they need a
    # visibly higher bar, not marginally higher.
    LATE_ENTRY_MIN_SCORE_BUMP:     float = 1.0

    # ── Post-Entry Momentum Kill (Roadmap #198) ───────────────────
    # The dominant loss pattern today is "slow bleed to SL" — a trade
    # is filled, immediately turns red, and walks 8-12 minutes to its
    # SL while we wait. If the stock had real edge in our direction
    # the first three minutes of post-fill price action should at
    # least *try* to move toward target. When that doesn't happen and
    # MTM is already negative, the setup is wrong — exit at small loss
    # rather than wait for the full -1×ATR SL hit.
    # Logic in check_stops_and_targets per-position loop:
    #   skip if elapsed_seconds < MOMENTUM_KILL_GRACE_SECONDS  (let order settle)
    #   skip if elapsed_seconds > MOMENTUM_KILL_WINDOW_MINUTES*60
    #   skip if pos["_external"]                              (manual / adopted — give grace)
    #   skip if pos.get("_partial_taken")                     (already booking profit; trailing stop owns it)
    #   compute adverse_pct = |entry - current| / entry * 100  (only on red side)
    #   skip if adverse_pct < MOMENTUM_KILL_MIN_ADVERSE_PCT     (sub-noise / inside spread)
    #   compute progress = (current - entry) / (target - entry) for BUY (mirrored for SELL)
    #   if progress < MOMENTUM_KILL_MIN_PROGRESS_PCT/100 AND unrealised < 0:
    #     exit at market with reason "MOMENTUM_KILL"
    #
    # 2026-04-27 production-data tuning: shipped 2026-04-24 with
    # grace=60s and no adverse floor. First live day (2026-04-27) the
    # rule killed 4/4 morning entries on sub-spread micro-moves
    # (HDFCLIFE killed at -0.018% / Rs.0.11 on Rs.600). Cause: the
    # 25%-progress-to-target test in the first minute fires on any
    # negative tick because typical NSE bid-ask + fade is 0.05-0.20%
    # and the +0.3% favorable bar is mathematically unreachable in
    # under a minute. Fix: (a) grace 60s -> 180s — first 3 min is
    # settlement, only hard SL fires (industry standard ORB practice);
    # (b) new MOMENTUM_KILL_MIN_ADVERSE_PCT = 0.40 — adverse move must
    # exceed 4x typical NSE intraday spread before kill is even
    # considered. Both gates must trip with the existing 25% progress
    # test as a third filter. On 2026-04-27 data this would have
    # killed zero trades.
    # Kill-switch: MOMENTUM_KILL_ENABLED.
    MOMENTUM_KILL_ENABLED:           bool  = True
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
    # Approach: scale the 0.7 floor by an hour-bucket multiplier.
    # E.g. at 12:00 the multiplier is 0.7 → effective floor 0.49,
    # so a 0.5× RVol observed at noon now passes (it would have
    # been mid-pack relative to typical noon volume).
    # Buckets cover 09-15. Hours outside fall back to 1.0 (no scaling).
    RVOL_TIME_NORMALIZATION_ENABLED: bool = True
    RVOL_FLOOR_BY_HOUR: dict[int, float] = {
        9:  1.00,  # opening surge — keep strict
        10: 1.00,
        11: 0.85,  # mid-morning fade
        12: 0.70,  # lunch trough
        13: 0.85,  # post-lunch warm-up
        14: 1.00,
        15: 1.00,  # closing surge
    }

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
    BUDGET_REGIME_ENABLED: bool = True
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

    # MIN_SCORE bump (base V2_MIN_SCORE = 2.0):
    #   tiny = +1.0, small = +0.5, normal = 0, large = 0.
    BUDGET_MIN_SCORE_DELTA = {"TINY": 1.0, "SMALL": 0.5, "NORMAL": 0.0, "LARGE": 0.0}

    # ── Loss-Adjusted Position Sizing ─────────────────────────────
    # LOSS_SIZING_ENABLED: if True, reduce position sizes after
    #   realising losses. Budget for new trades shrinks by day's
    #   losses, preventing full-size re-entry after SL hits.
    #   Live mode already gets this from Zerodha's margin API.
    #   This mainly helps dry-run mode stay realistic.
    LOSS_SIZING_ENABLED: bool = True

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
    CONSECUTIVE_SL_PAUSE_COUNT: int = 3
    CONSECUTIVE_SL_PAUSE_MINUTES: int = 30

    # When True, MOMENTUM_KILL / STAGNANT_EXIT / SIGNAL_DECAY / LOSER_EXIT
    # exits with `pnl < 0` also feed the consecutive-loss counter. EOD
    # reasons (SQUARE_OFF / CIRCUIT_BREAKER) and operator/external closes
    # are excluded — they are not strategy failures.
    LOSS_STREAK_INCLUDE_NON_SL_LOSSES: bool = True

    # ── Dynamic Score Threshold ───────────────────────────────────
    # After losses, raise the minimum score for new NoAI trades.
    # LOSS_SCORE_BUMP_PCT: day loss threshold (as % of budget)
    #   that triggers a higher MIN_SCORE.
    # LOSS_SCORE_BUMP_AMOUNT: extra score points added to V2_MIN_SCORE.
    LOSS_SCORE_BUMP_PCT: float = 1.5
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
    # VIX_HIGH_SCORE_BUMP: raise V2_MIN_SCORE by this in high VIX.
    VIX_HIGH_THRESHOLD: float = 20.0
    VIX_LOW_THRESHOLD:  float = 12.0
    VIX_SPIKE_PCT:      float = 10.0
    VIX_HIGH_POSITION_REDUCTION: int   = 1
    VIX_HIGH_SCORE_BUMP:         float = 1.0

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
    # CLAUDE_COST_PER_CALL: estimated Rs. per Claude API call on Pro plan.
    #   This IS deducted from daily P&L because it's a per-use cost.
    ZERODHA_MONTHLY_COST:  float = 500.0
    CLAUDE_COST_PER_CALL:  float = 3.0   # avg Rs.3 per Claude API call on Pro

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
    EARNINGS_BLACKOUT_ENABLED: bool = True
    EARNINGS_BLACKOUT_SYMBOLS_2026: dict[str, list[str]] = {
        # Example entries (commented):
        # "2026-04-25": ["RELIANCE", "TCS"],
        # "2026-04-28": ["INFY", "WIPRO"],
    }

    # ── Plan rule tables ──────────────────────────────────────────
    # Maps plan names → capabilities. Read via claude() / zerodha().

    _CLAUDE_RULES = {
        "free": {
            "analysis_depth":    "basic",
            "include_pe_ratios": False,
            "model":             "claude-haiku-4-5-20251001",
            "max_tokens":        1200,
            "note":              "Haiku model · basic analysis · ~Rs.1/run",
        },
        "pro": {
            "analysis_depth":    "detailed",
            "include_pe_ratios": True,
            "model":             "claude-sonnet-4-6",
            "max_tokens":        2000,
            "note":              "Sonnet model · detailed analysis · ~Rs.5/run",
        },
        "max": {
            "analysis_depth":    "full",
            "include_pe_ratios": True,
            "model":             "claude-sonnet-4-6",
            "max_tokens":        3000,
            "note":              "Sonnet model · full analysis · ~Rs.8/run",
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

    @classmethod
    def claude(cls) -> dict:
        """Returns the resolved Claude plan settings dict."""
        return cls._CLAUDE_RULES[cls.CLAUDE_PLAN]

    @classmethod
    def zerodha(cls) -> dict:
        """Returns the resolved Zerodha plan settings dict."""
        return cls._ZERODHA_RULES[cls.ZERODHA_PLAN]

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
        if require_claude and not cls.CLAUDE_API_KEY:
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

        # Entry filters
        _pct("RSI_BUY_BLOCK_THRESHOLD",  cls.RSI_BUY_BLOCK_THRESHOLD)
        _pct("RSI_SELL_BLOCK_THRESHOLD", cls.RSI_SELL_BLOCK_THRESHOLD)
        _pct("VWAP_EXTENSION_BLOCK_PCT", cls.VWAP_EXTENSION_BLOCK_PCT, 10)
        _pos("VWAP_EXT_SCORE_OVERRIDE",  cls.VWAP_EXT_SCORE_OVERRIDE)
        _pos("FRESH_REVERSAL_DELTA_THRESHOLD", cls.FRESH_REVERSAL_DELTA_THRESHOLD)
        _pos("GAP_COHERENCE_OVERRIDE_SCORE", cls.GAP_COHERENCE_OVERRIDE_SCORE)
        _pos("SIGNAL_REVERSAL_SCORE",   cls.SIGNAL_REVERSAL_SCORE)
        _pos("SIGNAL_DECAY_MIN_ENTRY_SCORE", cls.SIGNAL_DECAY_MIN_ENTRY_SCORE)
        _pos("SIGNAL_DECAY_MIN_HOLD_MINUTES", cls.SIGNAL_DECAY_MIN_HOLD_MINUTES)
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

        return errors

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
            "claude_api_cost":       round(claude_cost, 2),
            "total_costs":           round(total_charges + claude_cost, 2),
            "zerodha_monthly_fyi":   cls.ZERODHA_MONTHLY_COST,
        }
