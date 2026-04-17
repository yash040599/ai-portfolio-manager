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
    MAX_BUDGET_INR: int = 20_000

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
    # CLAUDE_REVIEW_MINUTES: how often Claude re-evaluates open positions.
    #   Lower = more adaptive, but costs more in Claude API credits.
    #   Each review call ≈ Rs.2-4 on Pro plan.
    #   30 min = ~12 calls/day ≈ Rs.25-50/day in Claude costs.
    #   15 min = ~24 calls/day ≈ Rs.50-100/day. Only if budget is large.
    #
    #   DECISION HISTORY:
    #     20 min (original): Claude cut winners short (32% win rate on
    #       REVIEW_EXIT). Over-reviewing causes premature exits.
    #     30 min (2026-04-09): Increased to give trades more room.
    #       Trades need time to play out before re-evaluation.
    PRICE_POLL_SECONDS:     int = 10
    CLAUDE_REVIEW_MINUTES:  int = 30

    # ── Stock Universe ────────────────────────────────────────────
    # Which stocks Claude can pick from for intraday trades.
    # Options: "NIFTY50" | "NIFTY100" | "NIFTY200" | "CUSTOM"
    #
    # NIFTY50  → top 50 liquid stocks, tight spreads, safest
    # NIFTY100 → more variety, slightly wider spreads
    # NIFTY200 → widest pool, some less liquid mid-caps
    # CUSTOM   → uses CUSTOM_WATCHLIST below (your hand-picked list)
    #
    # For Rs.10K budget, NIFTY50 is recommended — most liquid, lowest
    # impact cost, tightest bid-ask spreads for intraday.
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
    # Late targets are compressed (less time left in the day).
    # The floor ensures the compressed R:R is still worth the trade.
    #
    # TIME-BASED FLOORS — which floor applies depends on time of day:
    #   ┌─────────────────┬───────────┬─────────────────────────────────┐
    #   │ Period          │ R:R Floor │ Why                             │
    #   ├─────────────────┼───────────┼─────────────────────────────────┤
    #   │ Morning (<1 PM) │ 1.3:1     │ Full ATR target, be selective   │
    #   │ Afternoon (1-2) │ 1.2:1     │ 20% target compress → R:R ~1.2 │
    #   │ Late (>2 PM)    │ 1.0:1     │ 25% compress → R:R ~1.1, tight │
    #   └─────────────────┴───────────┴─────────────────────────────────┘
    #
    # FAILURE RELAXATION:
    #   After RR_RELAX_AFTER_FAILS (3) zero-entry scans → floor drops
    #   to min(current_time_floor, RR_FLOOR_RELAXED). This helps when
    #   ALL candidates are borderline (e.g. morning 1.25:1 fails 1.3).
    #   After RR_GIVEUP_AFTER_FAILS (5) failures → stop trading.
    #
    # MID-DAY RETRY:
    #   After the first entry fills, if a mid-day rescan finds 0
    #   entries, retries once with floor reduced by RR_RETRY_STEP
    #   (e.g. morning 1.3 → 1.2). Morning scan never retries.
    #
    # TARGET COMPRESSION (late entries, separate from floor):
    #   After 1 PM: target reduced by LATE_TARGET_CUT_PCT_1 (20%).
    #   After 2 PM: target reduced by LATE_TARGET_CUT_PCT_2 (25%).
    #   The floor values above are calibrated to these compressions.
    RR_TARGET_RATIO:       float = 1.5   # base R:R from ATR (target = SL × this)
    RR_FLOOR_MORNING:      float = 1.3   # before 1 PM — strict
    RR_FLOOR_AFTERNOON:    float = 1.2   # 1 PM to 2 PM
    RR_FLOOR_LATE:         float = 1.0   # after 2 PM — safety net only
    RR_FLOOR_RELAXED:      float = 1.1   # after N failed scans (any time)
    RR_RETRY_STEP:         float = 0.1   # mid-day retry step-down (1.3 → 1.2)
    RR_RELAX_AFTER_FAILS:  int   = 3     # zero-entry scans before relaxing
    RR_GIVEUP_AFTER_FAILS: int   = 5     # zero-entry scans before giving up
    RR_AFTERNOON_HOUR:     int   = 13    # 1 PM — afternoon rules start
    RR_LATE_HOUR:          int   = 14    # 2 PM — late rules start
    LATE_TARGET_CUT_PCT_1: float = 20.0  # target % reduction after 1 PM
    LATE_TARGET_CUT_PCT_2: float = 25.0  # target % reduction after 2 PM

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
    # Safe because late-entry target reduction + late-day loser exit
    # already protect against late-day risk.
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

    # ── Late Entry Target Compression ─────────────────────────────
    # Trades entered after 1 PM get reduced targets (less time to hit).
    # Uses RR_AFTERNOON_HOUR / RR_LATE_HOUR and LATE_TARGET_CUT_PCT_*
    # defined in the R:R Settings section above.
    # After compression, the R:R floor for that time period decides
    # whether the trade is still worth entering.

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
    # Set to 2× charges to ensure trades are economically viable.
    MIN_EXPECTED_PROFIT: float = 75.0

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

    # MIN_BUDGET_UTILISATION_PCT: minimum % of budget that Claude
    # should deploy across all trades. If Claude picks trades that
    # only use 30% of budget, the bot will auto-increase qty to
    # reach this minimum. Set to 0 to disable.
    # DISABLED: Forcing deployment into low-conviction trades causes losses.
    # Better to hold cash than force trades.
    MIN_BUDGET_UTILISATION_PCT: float = 0.0

    # ── Stagnant Position Exit (NoAI) ─────────────────────────────
    # STAGNANT_EXIT_MINUTES: after this many minutes, if a position
    #   hasn't moved toward target by at least STAGNANT_EXIT_MIN_MOVE_PCT,
    #   exit it to free the slot for a better trade. Only in NoAI mode
    #   (V2 has Claude reviews for this). Set to 0 to disable.
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
    #     2026-04-17: STAGNANT_EXIT_MIN_MOVE_PCT retired in favour of
    #       directional split. Was firing on slow-positive trades
    #       (RECLTD +0.26%, TATAPOWER +0.25%) — we were locking in
    #       sub-charge profits and paying Rs.15-20 round-trip to
    #       re-enter elsewhere. Now only exits if ADVERSE (clearly
    #       losing) or DEAD-FLAT (|move| near zero). Slow-positive
    #       trades are allowed to continue toward target.
    STAGNANT_EXIT_MINUTES:      int   = 45
    STAGNANT_EXIT_MIN_MOVE_PCT: float = 0.3   # retained for backwards compat; unused
    # Adverse threshold: fire stagnant-exit if move_pct is below this
    # negative number (i.e., losing by more than this %). Previously the
    # single 0.3% cutoff lumped slow-positive trades into this bucket.
    STAGNANT_ADVERSE_PCT:       float = 0.2
    # Dead-flat band: fire stagnant-exit if |move_pct| is below this
    # (truly going nowhere — neither up nor down in a meaningful way).
    STAGNANT_DEAD_FLAT_PCT:     float = 0.1

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

    # ── Charge-Aware Minimum Target (Roadmap #162) ────────────────
    # MIN_PROFIT_CHARGE_MULTIPLE: reject trades where expected gross
    #   profit at target is less than round-trip charges × this multiple.
    #   A Rs.2000 stock with Rs.5 SL + Rs.6.5 target on 1 share ≈ Rs.4
    #   round-trip charges, leaving Rs.2.5 net — not worth the risk.
    #   Setting 2.0 means target must yield ≥ 2× round-trip charges of
    #   net profit (so at least 1× of charges as cushion).
    # Kill-switch: MIN_PROFIT_CHARGE_MULTIPLE <= 0 disables.
    MIN_PROFIT_CHARGE_MULTIPLE: float = 2.0

    # ── Daily Loss Soft-Stop Hysteresis (Roadmap #163) ────────────
    # DAILY_LOSS_SOFT_STOP_PCT: when day P&L ≤ -this% of budget, stop
    #   taking NEW entries but keep monitoring existing positions.
    #   Hard circuit breaker at MAX_LOSS_PER_DAY_PCT still closes all.
    #   This gives a hysteresis band — prevents "open loser → hit SL
    #   → open another loser" pattern, but doesn't force exits that
    #   might recover in a green afternoon.
    # Set to 0 to disable (no soft stop, only hard CB).
    DAILY_LOSS_SOFT_STOP_PCT: float = 1.5

    # ── Lunch-Lull Entry Skip (Roadmap #164) ──────────────────────
    # Indian markets are lowest-volume and lowest-ADX during lunch.
    # Most churn trades fire in this window.
    # LUNCH_LULL_ENABLED: skip new entries during lunch lull.
    # LUNCH_LULL_START_HOUR / MINUTE: window start (11:30).
    # LUNCH_LULL_END_HOUR / MINUTE:   window end   (12:15).
    # LUNCH_LULL_SCORE_OVERRIDE: very strong signals bypass.
    LUNCH_LULL_ENABLED:         bool  = True
    LUNCH_LULL_START_HOUR:      int   = 11
    LUNCH_LULL_START_MINUTE:    int   = 30
    LUNCH_LULL_END_HOUR:        int   = 12
    LUNCH_LULL_END_MINUTE:      int   = 15
    LUNCH_LULL_SCORE_OVERRIDE:  float = 6.0

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
    #   tiny = -4 (max 8), small = -2 (max 10), normal = 0, large = +3.
    BUDGET_TRADE_CAP_DELTA = {"TINY": -4, "SMALL": -2, "NORMAL": 0, "LARGE": 3}

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

    # ── Consecutive SL Pause ──────────────────────────────────────
    # CONSECUTIVE_SL_PAUSE_COUNT: after this many consecutive SL hits
    #   (across any stocks), pause new entries for CONSECUTIVE_SL_PAUSE_MIN.
    #   Protects against whipsaw days when signals fail repeatedly.
    #   Set to 0 to disable.
    CONSECUTIVE_SL_PAUSE_COUNT: int = 3
    CONSECUTIVE_SL_PAUSE_MINUTES: int = 30

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
