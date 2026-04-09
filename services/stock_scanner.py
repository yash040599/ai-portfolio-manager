# ================================================================
# services/stock_scanner.py
# ================================================================
# Pre-market stock scanner for Phase 2 intraday trading.
#
# Responsibilities:
#   1. Provide the stock universe (Nifty 50/100/200 or custom list)
#   2. Fetch live quotes for the universe
#   3. Ask Claude to pick the best intraday candidates with
#      entry price, stop-loss, target, and position sizing
#   4. Parse Claude's response into structured trade plans
#
# Called once before market open. The output is a list of trade
# plans that OrderEngine will monitor and execute during the day.
#
# Claude is told:
#   - The exact budget available
#   - Max number of positions allowed
#   - Max % per single stock
#   - Today's date (so it doesn't use stale training data)
#   - Live pre-market / opening prices for all candidate stocks
#
# The response format is strictly enforced so parsing never breaks.
# ================================================================

import re
import datetime

from config             import Config, now_ist
from core.logger        import Logger
from core.claude_client import ClaudeClient


# ================================================================
# NIFTY INDEX CONSTITUENTS
# ================================================================
# These lists are used when SCAN_UNIVERSE is set to NIFTY50/100/200.
# Update periodically — NSE rebalances indices every 6 months.
# Last updated: April 2026.
# ================================================================

NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM",
    "TITAN", "TMPV", "TRENT", "ULTRACEMCO", "WIPRO",
]

# Nifty 100 = Nifty 50 + Next 50 large caps
NIFTY100_EXTRA = [
    "ABB", "ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM",
    "BAJAJHLDNG", "BANKBARODA", "BOSCHLTD", "BPCL", "BRITANNIA",
    "CANBK", "CGPOWER", "CHOLAFIN", "CUMMINSIND", "DLF",
    "DIVISLAB", "DMART", "ENRIN", "GAIL", "GODREJCP",
    "HAL", "HDFCAMC", "HINDZINC", "HYUNDAI", "INDHOTEL",
    "IOC", "IRFC", "JINDALSTEL", "LODHA", "LTM",
    "MAZDOCK", "MOTHERSON", "MUTHOOTFIN", "PFC", "PIDILITIND",
    "PNB", "RECLTD", "SHREECEM", "SIEMENS", "SOLARINDS",
    "TATACAP", "TATAPOWER", "TMCV", "TORNTPHARM", "TVSMOTOR",
    "UNIONBANK", "UNITDSPR", "VBL", "VEDL", "ZYDUSLIFE",
]

# Nifty 200 adds mid-caps — only a representative subset here.
# For full Nifty 200, consider loading from an API or CSV.
NIFTY200_EXTRA = [
    "AUROPHARMA", "BALKRISIND", "BHARATFORG", "BIOCON", "CANFINHOME",
    "CONCOR", "ESCORTS", "FEDERALBNK", "GMRINFRA", "IDFCFIRSTB",
    "INDUSTOWER", "JUBLFOOD", "LICHSGFIN", "MFSL", "MRF",
    "NAUKRI", "NAVINFLUOR", "NMDC", "OBEROIRLTY", "OFSS",
    "PAGEIND", "PETRONET", "PIIND", "POLYCAB", "SAIL",
    "TATACOMM", "TORNTPOWER", "VOLTAS", "YESBANK",
]


# ================================================================
# PARSING HELPERS
# ================================================================
# Shared by _extract_trade_fields() and _parse_review_response().
# ================================================================

def _parse_price(val: str) -> float:
    """Strips \u20b9, commas, spaces and converts to float. Returns 0.0 on failure."""
    cleaned = re.sub(r'[\u20b9,\s]', '', val)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_price_optional(val: str) -> float | None:
    """Like _parse_price but returns None for empty/invalid input."""
    if not val:
        return None
    cleaned = re.sub(r'[\u20b9,\s]', '', val)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(val: str) -> int:
    """Strips commas/spaces and converts to int. Returns 0 on failure."""
    cleaned = re.sub(r'[,\s]', '', val)
    try:
        return int(float(cleaned))
    except ValueError:
        return 0


class StockScanner:

    def __init__(self, config: type[Config], claude: ClaudeClient, log: Logger):
        self.cfg    = config
        self.claude = claude
        self.log    = log

        # Dynamic budget — set by PortfolioManager after fetching Zerodha funds.
        # Falls back to MAX_BUDGET_INR if not set.
        self._budget: float = float(config.MAX_BUDGET_INR)

    def set_budget(self, amount: float):
        """Sets the trading budget (called by PortfolioManager after fetching funds)."""
        self._budget = amount

    # ================================================================
    # GET STOCK UNIVERSE
    # ================================================================

    def get_universe(self) -> list[str]:
        """
        Returns the list of stock symbols the bot is allowed to trade.
        Controlled by Config.SCAN_UNIVERSE.
        """
        universe = self.cfg.SCAN_UNIVERSE.upper()

        if universe == "NIFTY50":
            return list(NIFTY50)
        elif universe == "NIFTY100":
            return list(NIFTY50) + list(NIFTY100_EXTRA)
        elif universe == "NIFTY200":
            return list(NIFTY50) + list(NIFTY100_EXTRA) + list(NIFTY200_EXTRA)
        elif universe == "CUSTOM":
            return list(self.cfg.CUSTOM_WATCHLIST)
        else:
            self.log.warning(
                f"Unknown SCAN_UNIVERSE '{universe}', falling back to NIFTY50"
            )
            return list(NIFTY50)

    # ================================================================
    # PRE-MARKET SCAN
    # ================================================================

    def scan(self, quotes: dict, nifty_context: str = "", perf_context: str = "", session_context: str = "") -> list[dict]:
        """
        Asks Claude to pick intraday trade candidates.

        Args:
            quotes: dict of live Kite quotes keyed by "NSE:SYMBOL".
                    Each value has last_price, ohlc, volume, etc.
            nifty_context: optional string with NIFTY 50 index data for trend filter.
            perf_context: optional string with recent trading performance for Claude.
            session_context: optional string with mid-day session data (P&L, traded symbols).

        Returns:
            List of trade plan dicts, each with:
              symbol, exchange, side ("BUY"),
              entry_price, stop_loss, target_price,
              qty, rationale
        """
        # Build a compact market snapshot for Claude
        snapshot = self._build_snapshot(quotes)

        if not snapshot:
            self.log.warning("No valid quotes to scan — snapshot is empty")
            return []

        prompt = self._build_scan_prompt(snapshot, nifty_context, perf_context, session_context)

        self.log.info("Asking Claude to pick intraday trades...")
        try:
            raw = self.claude.call(prompt)
            trades = self._parse_scan_response(raw)
            self.log.success(f"Claude recommended {len(trades)} trades")
            return trades
        except Exception as e:
            error = ClaudeClient.classify_error(e)
            self.log.error(f"Pre-market scan failed: {error}")
            return []

    # ================================================================
    # MID-DAY REVIEW
    # ================================================================

    def review_positions(
        self,
        open_positions: list[dict],
        quotes: dict,
        day_pnl: float,
        budget_remaining: float,
        nifty_context: str = "",
        closed_positions: list[dict] | None = None,
    ) -> list[dict]:
        """
        Periodic Claude review of open positions + market conditions.
        Called every CLAUDE_REVIEW_MINUTES during trading hours.

        Claude can recommend:
          - HOLD:  keep position, adjust SL/target
          - EXIT:  close position immediately
          - NEW:   open a new trade (if budget allows)

        Returns list of action dicts:
          {"action": "HOLD|EXIT|NEW", "symbol": ..., ...}
        """
        prompt = self._build_review_prompt(
            open_positions, quotes, day_pnl, budget_remaining, nifty_context,
            closed_positions or [],
        )

        self.log.info("Claude reviewing open positions...")
        try:
            raw = self.claude.call(prompt)
            actions = self._parse_review_response(raw)
            self.log.success(f"Claude review: {len(actions)} recommendations")
            return actions
        except Exception as e:
            error = ClaudeClient.classify_error(e)
            self.log.warning(f"Claude review failed: {error} — keeping current positions")
            return []

    # ================================================================
    # PROMPT BUILDERS
    # ================================================================
    # Two main prompts sent to Claude:
    #
    # _build_scan_prompt  — "Find new trades": gives Claude market
    #   snapshot + strict rejection filters + budget constraints.
    #   Claude returns structured trade plans (BUY/SELL with levels).
    #
    # _build_review_prompt — "Manage open positions": gives Claude
    #   portfolio state + R-multiple framework. Claude recommends
    #   HOLD/ADJUST_SL/EXIT for each position.
    #
    # Both prompts contain time-of-day context because intraday
    # strategy changes significantly throughout the trading day.
    # ================================================================

    def _build_snapshot(self, quotes: dict) -> str:
        """
        Converts raw Kite quotes into a compact text table for Claude.
        Only includes stocks that have valid price data.
        """
        lines = []
        for key, q in sorted(quotes.items()):
            price = q.get("last_price", 0)
            if not price or price <= 0:
                continue

            ohlc   = q.get("ohlc", {})
            change = price - ohlc.get("close", price)
            change_pct = (change / ohlc["close"] * 100) if ohlc.get("close") else 0
            volume = q.get("volume", 0)

            # Extract symbol from "NSE:RELIANCE" format
            symbol = key.split(":")[1] if ":" in key else key

            lines.append(
                f"{symbol:<16} "
                f"Rs.{price:>10.2f}  "
                f"Chg: {change_pct:>+6.2f}%  "
                f"O: Rs.{ohlc.get('open', 0):.2f}  "
                f"H: Rs.{ohlc.get('high', 0):.2f}  "
                f"L: Rs.{ohlc.get('low', 0):.2f}  "
                f"PrevClose: Rs.{ohlc.get('close', 0):.2f}  "
                f"Vol: {volume:>12,}"
            )

        return "\n".join(lines)

    def _build_scan_prompt(self, snapshot: str, nifty_context: str = "", perf_context: str = "", session_context: str = "") -> str:
        """
        Builds the pre-market scan prompt.
        Claude is given the full price data and budget constraints,
        and must return trade plans in a strict parseable format.
        """
        today  = now_ist().date().strftime("%B %d, %Y")
        now    = now_ist().strftime("%I:%M %p")
        budget = self._budget
        max_positions  = self.cfg.MAX_POSITIONS
        max_pct        = self.cfg.MAX_POSITION_PCT
        default_sl     = self.cfg.DEFAULT_STOP_LOSS_PCT
        default_target = self.cfg.DEFAULT_TARGET_PCT

        # Time-of-day context
        hour = now_ist().hour
        if hour < 10:
            time_phase = "OPENING (before 10 AM): ORB trades strongest. Wait for 15-min candle close. Avoid chasing opening spikes."
        elif hour < 11:
            time_phase = "MORNING TREND (10-11 AM): Best trending window. Momentum trades have highest success."
        elif hour < 13:
            time_phase = "MIDDAY (11 AM-1 PM): Volume drops. Favour mean-reversion near day's VWAP."
        elif hour < 14:
            time_phase = "AFTERNOON (1-2 PM): Reduce targets by 30%. European market opens — fresh volatility but less time."
        else:
            time_phase = "LATE SESSION (after 2 PM): Only high-conviction setups. Reduce targets by 50%."

        return f"""You are an expert Indian stock market intraday trader (NSE) with 15 years of experience in price action, sector rotation, and NSE microstructure.
Today is {today}, current time is {now} IST. All positions MUST be closed by 3:10 PM IST today.
CURRENT TIME PHASE: {time_phase}

BUDGET: Rs.{budget:,} total capital (Rs.{budget // max_positions:,} per slot).
MAX POSITIONS: {max_positions} stocks simultaneously.
MAX PER STOCK: {max_pct}% of budget (= Rs.{budget * max_pct // 100:,} max per stock).
{nifty_context}{perf_context}{session_context}
══════════════════════════════════════════════════════════
HARD REJECTION FILTERS — REJECT any trade that fails even ONE:
══════════════════════════════════════════════════════════
✗ REJECT BUY if stock already UP >2% from PrevClose — move is extended, mean-reversion risk.
✗ REJECT SELL if stock already DOWN >2% from PrevClose — move is extended, bounce risk.
✗ REJECT if Risk:Reward < 1:1.5 — insufficient edge after costs.
✗ REJECT if no clear structural level for stop-loss — no random % stops.
✗ REJECT if stock gapped up/down >1.5% AND is still near the extreme — do NOT chase gaps. If it pulled back toward the gap edge, a pullback entry is acceptable.
✗ REJECT if total position cost across ALL trades would exceed Rs.{budget:,}.

NOTE: The system automatically takes partial profit (50% of qty) at 1× risk profit and trails SL on the remainder. Prefer qty >= 2 so partial exits can work.

══════════════════════════════════════════════════════════
TRADING APPROACH (use price action from the data below):
══════════════════════════════════════════════════════════
WHAT YOU HAVE: OHLC, % change from previous close, and volume for each stock.
Use these to infer setups:

1. OPENING RANGE BREAKOUT (ORB):
   Stock where current price > today's high AND Chg >0.3% = potential breakout BUY.
   Stock where current price < today's low AND Chg <-0.3% = potential breakout SELL.
   Best before 10:30 AM, weakens after 11 AM.

2. MEAN-REVERSION (best setup for small capital):
   • BUY: stock DOWN 0.5-1.5% but Open was near PrevClose (no gap) → price likely reverting to open.
   • SELL: stock UP 0.5-1.5% but Open was near PrevClose → price likely reverting to open.
   • SL on wrong side of the open price. Target = halfway back to PrevClose.

3. RELATIVE STRENGTH/WEAKNESS:
   • Compare each stock's Chg% to NIFTY's Chg%. Stocks significantly outperforming = strong (BUY on pullback). Stocks significantly underperforming = weak (SELL on rally).
   • If NIFTY is DOWN >1.5%: SHORT cyclicals (Banking, Auto, Metals). AVOID BUY except defensives (Pharma, IT, FMCG).
   • If NIFTY is UP >1.5%: BUY cyclicals. AVOID shorting defensives.

4. VOLUME CONFIRMATION:
   High volume (relative to position in sorted list) confirms the move is real.
   Low volume on a breakout = likely false breakout → avoid.

5. STOP-LOSS PLACEMENT:
   BUY: SL just below today's low or open (whichever is tighter and structural). Range: {default_sl}%-2%.
   SELL: SL just above today's high or open. Range: {default_sl}%-2%.
   NEVER use arbitrary fixed % — always reference a structural level (O, H, L, PrevClose).

══════════════════════════════════════════════════════════
COMMON MISTAKES (from actual loss patterns):
══════════════════════════════════════════════════════════
✗ Shorting a stock already down 3-5% hoping it falls more — it BOUNCES.
✗ Buying a stock already up 3-5% hoping it goes higher — it REVERSES.
✗ Shorting a stock that is UP while the market is DOWN — it has relative strength and will snap back.
✗ All trades in same direction on same sector — if sector reverses, ALL lose together.
✗ Over-trading: With Rs.{budget:,} capital, fewer high-conviction trades always beat many mediocre ones. 2-3 good trades > 5 weak ones.
✗ Chasing gaps: wait for the pullback to the gap edge, don't buy/sell at the extremes.

CURRENT MARKET DATA (live prices):
{snapshot}

══════════════════════════════════════════════════════════
RESPONSE FORMAT — STRICTLY FOLLOW:
══════════════════════════════════════════════════════════
One block per trade. No text before or after.
If no trades pass ALL rejection filters, respond with exactly: NO_TRADES_TODAY
Prefer FEWER high-conviction trades (2-3) over many mediocre ones.

TRADE 1:
SYMBOL: [NSE stock symbol e.g. RELIANCE]
SIDE: [BUY or SELL]
ENTRY_PRICE: [realistic entry price in Rs., near current price]
STOP_LOSS: [stop-loss price in Rs. — state which structural level: today's L/H, Open, or PrevClose]
TARGET: [target price in Rs. — must be at least 1.5× the SL distance from entry]
QTY: [number of shares — must fit within budget constraints]
RATIONALE: [2-3 sentences: (1) what setup (ORB/mean-reversion/relative strength), (2) structural SL level, (3) R:R ratio. If Chg >2%, explain why it's NOT an extended-move violation.]
---
TRADE 2:
...
---
===END===
"""

    def _build_review_prompt(
        self,
        positions: list[dict],
        quotes: dict,
        day_pnl: float,
        budget_remaining: float,
        nifty_context: str = "",
        closed_positions: list[dict] | None = None,
    ) -> str:
        """
        Builds the periodic review prompt for open positions.
        """
        today = now_ist().date().strftime("%B %d, %Y")
        now   = now_ist().strftime("%I:%M %p")

        budget         = self._budget
        max_positions  = self.cfg.MAX_POSITIONS
        max_pct        = self.cfg.MAX_POSITION_PCT
        max_per        = budget * max_pct // 100
        max_reentries  = self.cfg.MAX_REENTRIES_PER_STOCK

        # Calculate minutes until square-off for time-pressure context
        now_dt = now_ist()
        square_off = now_dt.replace(
            hour=self.cfg.SQUARE_OFF_HOUR,
            minute=self.cfg.SQUARE_OFF_MINUTE,
            second=0, microsecond=0,
        )
        mins_left = max(0, (square_off - now_dt).total_seconds() / 60)

        pos_text = ""
        for p in positions:
            key = f"NSE:{p['symbol']}"
            q   = quotes.get(key, {})
            current_price = q.get("last_price", p.get("entry_price", 0))
            entry = p.get("entry_price", 0)
            pnl = (current_price - entry) * p.get("qty", 0)
            if p.get("side") == "SELL":
                pnl = (entry - current_price) * p.get("qty", 0)

            # Calculate risk and R-multiple for Claude's context
            sl = p.get("stop_loss", entry)
            risk_per_share = abs(entry - sl) if sl else 0
            r_multiple = (pnl / (risk_per_share * p.get("qty", 1))) if risk_per_share > 0 else 0

            pos_text += (
                f"  {p['symbol']}: {p['side']} {p['qty']} shares @ Rs.{entry:.2f}  "
                f"Current: Rs.{current_price:.2f}  P&L: Rs.{pnl:.2f} ({r_multiple:+.1f}R)  "
                f"SL: Rs.{p.get('stop_loss', 'N/A')}  Target: Rs.{p.get('target_price', 'N/A')}\n"
            )

        # Build closed/failed trade history so Claude doesn't re-enter losers
        closed_text = ""
        reentry_counts: dict[str, int] = {}
        for cp in (closed_positions or []):
            sym = cp.get("symbol", "")
            reentry_counts[sym] = reentry_counts.get(sym, 0) + 1
            closed_text += (
                f"  {sym}: {cp.get('side', '?')} {cp.get('qty', 0)} shares @ Rs.{cp.get('entry_price', 0):.2f}  "
                f"Exit: Rs.{cp.get('exit_price', 0):.2f}  P&L: Rs.{cp.get('pnl', 0):.2f}  "
                f"Reason: {cp.get('exit_reason', '?')}\n"
            )

        # Build list of stocks at re-entry limit
        blocked_stocks = [
            sym for sym, count in reentry_counts.items()
            if max_reentries > 0 and count >= max_reentries
        ]
        blocked_text = (
            f"\nBLOCKED FROM RE-ENTRY (already traded {max_reentries}x today): "
            + ", ".join(blocked_stocks)
            if blocked_stocks else ""
        )

        return f"""You are an expert Indian stock market intraday trader (NSE) specialising in position management with 15 years of experience.
Today is {today}, current time is {now} IST. Market closes at 3:30 PM, we square off at 3:10 PM.
TIME REMAINING: {mins_left:.0f} minutes until square-off.
{nifty_context}
CURRENT OPEN POSITIONS:
{pos_text if pos_text else "  (none)"}

CLOSED TRADES TODAY:
{closed_text if closed_text else "  (none)"}

DAY P&L SO FAR: Rs.{day_pnl:,.2f}
REMAINING BUDGET: Rs.{budget_remaining:,.2f}
MAX POSITIONS: {max_positions} stocks simultaneously.
MAX PER STOCK: {max_pct}% of Rs.{budget:,} = Rs.{max_per:,} max per stock.
{blocked_text}

══════════════════════════════════════════════════════════
POSITION MANAGEMENT FRAMEWORK (R-multiple based):
══════════════════════════════════════════════════════════
AUTOMATIC ACTIONS (handled by the system — do NOT suggest these):
  • At {self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R profit: system auto-exits 33% of qty and begins trailing SL (you'll see reduced qty in positions above).
  • Trailing SL: system continuously moves SL to lock in {int(self.cfg.TRAIL_STEP_PCT)}% of current profit. This is automatic.
  • You should only suggest ADJUST_SL to tighten BEYOND what the system already set, never to loosen.

YOUR ROLE — Use the R-multiple to guide ADDITIONAL decisions:
  Deep loser (<-0.5R): Trade thesis is FAILING. Unless price is clearly reversing back in your favour, EXIT.
  Losing (-0.5R to 0R): Still within initial risk. Check if NIFTY trend still supports trade direction. If yes → HOLD. If NIFTY reversed → EXIT.
  Breakeven (0R to +0.5R): HOLD and let it develop.
  Small winner (+0.5R to +{self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R): HOLD — system will auto-take partial profit at {self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R.
  Good winner (+{self.cfg.TRAIL_AFTER_RISK_MULTIPLE}R to +2R): Partial profit already taken by system. Remaining position has trailing SL. HOLD unless trend has clearly reversed.
  Large winner (>+2R): System trailing is active. Consider ADJUST_TARGET closer if time is short (<60 min remain).

══════════════════════════════════════════════════════════
REVIEW RULES — MUST FOLLOW:
══════════════════════════════════════════════════════════
1. TRAILING STOP (handled automatically at {int(self.cfg.TRAIL_STEP_PCT)}% of profit):
   Only suggest ADJUST_SL to tighten MORE than the auto-trail (e.g. due to NIFTY reversal).
   *** NEVER suggest loosening SL (moving it further from current price). ***

2. TIME MANAGEMENT ({mins_left:.0f} min remaining):
   • >120 min: Full discretion. Manage positions normally.
   • 60-120 min: Reduce targets by 30%. No new trades unless strong setup.
   • 30-60 min: Reduce targets by 50%. EXIT underwater positions. HOLD only profitable positions with strong momentum.
   • <30 min: EXIT ALL positions unless within 0.3% of target.

3. CUT LOSERS: Positions underwater that have been drifting sideways for 2+ review cycles → EXIT. Dead money is worse than a small loss.

4. DO NOT AVERAGE DOWN on losing positions. Only suggest NEW trades for fresh setups.

5. NIFTY ALIGNMENT: If NIFTY has turned against your trade direction since entry, tighten SL to within 0.5% of current price.

6. RE-ENTRY: DO NOT re-enter a stock that hit STOP_LOSS today unless in the OPPOSITE direction. Check CLOSED TRADES above.

7. PROTECT WINNERS: DO NOT exit profitable positions just because of "time pressure" when 30+ min remain AND the trend is intact. Tighten SL instead of exiting. Only exit if trend has clearly reversed or target is unreachable.

8. NEW TRADES (strict criteria):
   • Only if 60+ minutes remain
   • Stock must NOT already be extended (within ±2% of previous close)
   • Budget must be available
   • QTY × ENTRY_PRICE ≤ min(REMAINING BUDGET Rs.{budget_remaining:,.0f}, MAX PER STOCK Rs.{max_per:,})
   • Prefer FEWER new trades — 1 good trade > 2 mediocre trades

Review each position. For each, respond with EXACTLY this format:

REVIEW 1:
SYMBOL: [symbol]
ACTION: [HOLD | EXIT | ADJUST_SL | ADJUST_TARGET]
NEW_SL: [new stop-loss price if ADJUST_SL, otherwise leave blank]
NEW_TARGET: [new target price if ADJUST_TARGET, otherwise leave blank]
REASON: [1-2 sentences — reference the R-multiple, time remaining, and NIFTY alignment in your decision]
---

For new trades (optional, strict criteria above):
NEW_TRADE:
SYMBOL: [symbol]
SIDE: [BUY or SELL]
ENTRY_PRICE: [price]
STOP_LOSS: [price — based on structural level]
TARGET: [price — reduced for time remaining]
QTY: [quantity — MUST satisfy budget constraint above]
RATIONALE: [1-2 sentences — setup type, R:R ratio, why worth the late-day risk]
---
===END===
"""

    # ================================================================
    # RESPONSE PARSERS
    # ================================================================

    def _parse_scan_response(self, raw: str) -> list[dict]:
        """
        Parses Claude's trade recommendations from the pre-market scan.
        Returns a list of trade plan dicts.

        Tolerant of minor format variations (extra spaces, missing fields).
        Validates that total cost doesn't exceed budget.
        """
        text = raw.strip()

        if "NO_TRADES_TODAY" in text:
            self.log.info("Claude says: no good trades today")
            return []

        trades = []
        # Split by the --- separator to get individual trade blocks
        blocks = re.split(r'-{3,}', text)

        for block in blocks:
            block = block.strip()
            if not block or "===END===" in block:
                continue

            trade = self._extract_trade_fields(block)
            if trade:
                trades.append(trade)

        # Validate total cost doesn't exceed budget
        trades = self._validate_budget(trades)
        return trades

    def _extract_trade_fields(self, block: str) -> dict | None:
        """
        Extracts structured fields from one trade block.
        Returns None if critical fields are missing.
        """
        def extract(field: str) -> str:
            pattern = rf"(?i){field}\s*:\s*(.+)"
            match = re.search(pattern, block)
            return match.group(1).strip() if match else ""

        symbol = extract("SYMBOL")
        side   = extract("SIDE").upper()
        entry  = extract("ENTRY_PRICE")
        sl     = extract("STOP_LOSS")
        target = extract("TARGET")
        qty    = extract("QTY")
        reason = extract("RATIONALE") or extract("REASON")

        # All critical fields must be present
        if not all([symbol, side, entry, qty]):
            return None

        # Side must be exactly BUY or SELL
        if side not in ("BUY", "SELL"):
            return None

        entry_price  = _parse_price(entry)
        stop_loss    = _parse_price(sl) if sl else 0.0
        target_price = _parse_price(target) if target else 0.0
        quantity     = _parse_int(qty)

        if entry_price <= 0 or quantity <= 0:
            return None

        # Apply default SL/target if Claude didn't provide them
        if stop_loss <= 0:
            sl_pct = self.cfg.DEFAULT_STOP_LOSS_PCT / 100
            stop_loss = round(
                entry_price * (1 - sl_pct) if side == "BUY"
                else entry_price * (1 + sl_pct),
                2
            )

        if target_price <= 0:
            tgt_pct = self.cfg.DEFAULT_TARGET_PCT / 100
            target_price = round(
                entry_price * (1 + tgt_pct) if side == "BUY"
                else entry_price * (1 - tgt_pct),
                2
            )

        # Validate SL/target are on the correct side of entry
        if side == "BUY":
            if stop_loss >= entry_price:
                sl_pct = self.cfg.DEFAULT_STOP_LOSS_PCT / 100
                stop_loss = round(entry_price * (1 - sl_pct), 2)
            if target_price <= entry_price:
                tgt_pct = self.cfg.DEFAULT_TARGET_PCT / 100
                target_price = round(entry_price * (1 + tgt_pct), 2)
        else:  # SELL
            if stop_loss <= entry_price:
                sl_pct = self.cfg.DEFAULT_STOP_LOSS_PCT / 100
                stop_loss = round(entry_price * (1 + sl_pct), 2)
            if target_price >= entry_price:
                tgt_pct = self.cfg.DEFAULT_TARGET_PCT / 100
                target_price = round(entry_price * (1 - tgt_pct), 2)

        return {
            "symbol":       symbol,
            "exchange":     "NSE",
            "side":         side,
            "entry_price":  round(entry_price, 2),
            "stop_loss":    round(stop_loss, 2),
            "target_price": round(target_price, 2),
            "qty":          quantity,
            "rationale":    reason,
            "status":       "PENDING",   # PENDING → OPEN → CLOSED
        }

    def _validate_budget(self, trades: list[dict]) -> list[dict]:
        """
        Ensures total trade value doesn't exceed budget.
        Reduces qty to fit when possible, drops only as a last resort.
        """
        budget    = self._budget
        max_pct   = self.cfg.MAX_POSITION_PCT / 100
        max_per   = budget * max_pct
        allocated = 0
        valid     = []

        for t in trades:
            cost = t["entry_price"] * t["qty"]
            entry = t["entry_price"]

            # Check per-stock limit — reduce qty to fit if needed
            if cost > max_per and entry > 0:
                new_qty = int(max_per / entry)
                if new_qty >= 1:
                    self.log.warning(
                        f"{t['symbol']}: {t['qty']}x @ Rs.{entry:.2f} = Rs.{cost:,.0f} exceeds "
                        f"per-stock limit Rs.{max_per:,.0f}. Reducing qty to {new_qty}"
                    )
                    t["qty"] = new_qty
                    cost = entry * new_qty
                else:
                    self.log.warning(
                        f"Dropping {t['symbol']}: Rs.{cost:,.0f} exceeds "
                        f"per-stock limit of Rs.{max_per:,.0f} and min qty is 1"
                    )
                    continue

            # Check total budget — reduce qty to fit if needed
            if allocated + cost > budget and entry > 0:
                remaining = budget - allocated
                new_qty = int(remaining / entry)
                if new_qty >= 1:
                    self.log.warning(
                        f"{t['symbol']}: {t['qty']}x @ Rs.{entry:.2f} = Rs.{cost:,.0f} exceeds "
                        f"remaining budget Rs.{remaining:,.0f}. Reducing qty to {new_qty}"
                    )
                    t["qty"] = new_qty
                    cost = entry * new_qty
                else:
                    self.log.warning(
                        f"Dropping {t['symbol']}: Rs.{cost:,.0f} would exceed "
                        f"total budget of Rs.{budget:,} (only Rs.{remaining:,.0f} left)"
                    )
                    continue

            allocated += cost
            valid.append(t)

        if valid:
            self.log.info(
                f"Total allocated: Rs.{allocated:,.0f} / Rs.{budget:,} "
                f"({allocated / budget * 100:.1f}%)"
            )

        return valid

    def _parse_review_response(self, raw: str) -> list[dict]:
        """
        Parses Claude's review response into action dicts.
        Each action has: symbol, action, new_sl, new_target, reason.
        New trade suggestions are also parsed.
        """
        text = raw.strip()
        actions = []
        blocks = re.split(r'-{3,}', text)

        for block in blocks:
            block = block.strip()
            if not block or "===END===" in block:
                continue

            # Check if it's a new trade suggestion
            if "NEW_TRADE" in block.upper() or "NEW TRADE" in block.upper():
                trade = self._extract_trade_fields(block)
                if trade:
                    trade["action"] = "NEW"
                    actions.append(trade)
                continue

            # Otherwise it's a position review
            def extract(field: str) -> str:
                pattern = rf"(?i){field}\s*:\s*(.+)"
                match = re.search(pattern, block)
                return match.group(1).strip() if match else ""

            symbol = extract("SYMBOL")
            action = extract("ACTION").upper()
            new_sl = extract("NEW_SL")
            new_target = extract("NEW_TARGET")
            reason = extract("REASON")

            if symbol and action:
                actions.append({
                    "symbol":     symbol,
                    "action":     action,
                    "new_sl":     _parse_price_optional(new_sl),
                    "new_target": _parse_price_optional(new_target),
                    "reason":     reason,
                })

        return actions
