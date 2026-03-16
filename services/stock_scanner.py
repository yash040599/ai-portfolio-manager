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

from config             import Config
from core.logger        import Logger
from core.claude_client import ClaudeClient


# ================================================================
# NIFTY INDEX CONSTITUENTS
# ================================================================
# These lists are used when SCAN_UNIVERSE is set to NIFTY50/100/200.
# Update periodically — NSE rebalances indices every 6 months.
# Last updated: March 2026.
# ================================================================

NIFTY50 = [
    "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO",
    "BAJFINANCE", "BAJAJFINSV", "BEL", "BPCL", "BHARTIARTL",
    "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "ITC", "INDUSINDBK",
    "INFY", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "NTPC", "NESTLEIND", "ONGC", "POWERGRID",
    "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN", "SUNPHARMA",
    "TCS", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TECHM",
    "TITAN", "TRENT", "ULTRACEMCO", "WIPRO", "ZOMATO",
]

# Nifty 100 = Nifty 50 + Next 50 large caps
NIFTY100_EXTRA = [
    "ABB", "ADANIENT", "AMBUJACEM", "ATGL", "BANKBARODA",
    "BOSCHLTD", "CANBK", "CHOLAFIN", "COLPAL", "DABUR",
    "DLF", "DIVISLAB", "GODREJCP", "HAL", "HAVELLS",
    "ICICIPRULI", "IOC", "INDIGO", "IRCTC", "JIOFIN",
    "LODHA", "LUPIN", "MANKIND", "MARICO", "MOTHERSON",
    "NHPC", "PFC", "PERSISTENT", "PIDILITIND", "PNB",
    "RECLTD", "SBICARD", "SIEMENS", "SRF", "TORNTPHARM",
    "TVSMOTOR", "UNITDSPR", "VBL", "VEDL", "ZYDUSLIFE",
]

# Nifty 200 adds mid-caps — only a representative subset here.
# For full Nifty 200, consider loading from an API or CSV.
NIFTY200_EXTRA = [
    "AUROPHARMA", "BALKRISIND", "BHARATFORG", "BIOCON", "CANFINHOME",
    "CONCOR", "CUMMINSIND", "ESCORTS", "FEDERALBNK", "GAIL",
    "GMRINFRA", "IDFCFIRSTB", "INDUSTOWER", "IRFC", "JUBLFOOD",
    "LICHSGFIN", "LTIM", "MFSL", "MRF", "MUTHOOTFIN",
    "NAUKRI", "NAVINFLUOR", "NMDC", "OBEROIRLTY", "OFSS",
    "PAGEIND", "PETRONET", "PIIND", "POLYCAB", "SAIL",
    "TATACOMM", "TATAPOWER", "TORNTPOWER", "VOLTAS", "YESBANK",
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

    def scan(self, quotes: dict) -> list[dict]:
        """
        Asks Claude to pick intraday trade candidates.

        Args:
            quotes: dict of live Kite quotes keyed by "NSE:SYMBOL".
                    Each value has last_price, ohlc, volume, etc.

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

        prompt = self._build_scan_prompt(snapshot)

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
            open_positions, quotes, day_pnl, budget_remaining
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
                f"₹{price:>10.2f}  "
                f"Chg: {change_pct:>+6.2f}%  "
                f"O: ₹{ohlc.get('open', 0):.2f}  "
                f"H: ₹{ohlc.get('high', 0):.2f}  "
                f"L: ₹{ohlc.get('low', 0):.2f}  "
                f"PrevClose: ₹{ohlc.get('close', 0):.2f}  "
                f"Vol: {volume:>12,}"
            )

        return "\n".join(lines)

    def _build_scan_prompt(self, snapshot: str) -> str:
        """
        Builds the pre-market scan prompt.
        Claude is given the full price data and budget constraints,
        and must return trade plans in a strict parseable format.
        """
        today  = datetime.date.today().strftime("%B %d, %Y")
        budget = self._budget
        max_positions  = self.cfg.MAX_POSITIONS
        max_pct        = self.cfg.MAX_POSITION_PCT
        default_sl     = self.cfg.DEFAULT_STOP_LOSS_PCT
        default_target = self.cfg.DEFAULT_TARGET_PCT

        return f"""You are an expert Indian stock market intraday trader (NSE).
Today is {today}. You must pick stocks for INTRADAY trading (buy and sell same day).

BUDGET: ₹{budget:,} total capital. All positions MUST be closed by 3:10 PM IST today.
MAX POSITIONS: {max_positions} stocks simultaneously.
MAX PER STOCK: {max_pct}% of budget (= ₹{budget * max_pct // 100:,} max per stock).

STRATEGY GUIDELINES:
- Focus on liquid large-cap stocks with tight bid-ask spreads
- Look for momentum, gaps, support/resistance levels, volume breakouts
- Entry price should be realistic (near current market price)
- Stop-loss: typically {default_sl}% below entry for longs
- Target: typically {default_target}% above entry for longs
- Risk-reward ratio should be at least 1:1.3
- Consider pre-market sentiment, gap openings, sector trends
- You can recommend SHORT (sell first, buy later) if bearish setup exists
- Total position value across all trades MUST NOT exceed ₹{budget:,}

CURRENT MARKET DATA (live prices):
{snapshot}

RESPOND WITH EXACTLY THIS FORMAT. One block per trade. No text before or after.
If no good trades exist today, respond with exactly: NO_TRADES_TODAY

TRADE 1:
SYMBOL: [NSE stock symbol e.g. RELIANCE]
SIDE: [BUY or SELL]
ENTRY_PRICE: [realistic entry price in ₹, near current price]
STOP_LOSS: [stop-loss price in ₹]
TARGET: [target price in ₹]
QTY: [number of shares — must fit within budget constraints]
RATIONALE: [1-2 sentences why this trade]
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
    ) -> str:
        """
        Builds the periodic review prompt for open positions.
        """
        today = datetime.date.today().strftime("%B %d, %Y")
        now   = datetime.datetime.now().strftime("%I:%M %p")

        pos_text = ""
        for p in positions:
            key = f"NSE:{p['symbol']}"
            q   = quotes.get(key, {})
            current_price = q.get("last_price", p.get("entry_price", 0))
            entry = p.get("entry_price", 0)
            pnl = (current_price - entry) * p.get("qty", 0)
            if p.get("side") == "SELL":
                pnl = (entry - current_price) * p.get("qty", 0)

            pos_text += (
                f"  {p['symbol']}: {p['side']} {p['qty']} shares @ ₹{entry:.2f}  "
                f"Current: ₹{current_price:.2f}  P&L: ₹{pnl:.2f}  "
                f"SL: ₹{p.get('stop_loss', 'N/A')}  Target: ₹{p.get('target_price', 'N/A')}\n"
            )

        return f"""You are an expert Indian stock market intraday trader (NSE).
Today is {today}, current time is {now} IST. Market closes at 3:30 PM, we square off at 3:10 PM.

CURRENT OPEN POSITIONS:
{pos_text if pos_text else "  (none)"}

DAY P&L SO FAR: ₹{day_pnl:,.2f}
REMAINING BUDGET: ₹{budget_remaining:,.2f}

Review each position and recommend ONE action per position.
You may also suggest NEW trades if budget allows and there's a good setup.

For each position, respond with EXACTLY this format:

REVIEW 1:
SYMBOL: [symbol]
ACTION: [HOLD | EXIT | ADJUST_SL | ADJUST_TARGET]
NEW_SL: [new stop-loss price if ADJUST_SL, otherwise leave blank]
NEW_TARGET: [new target price if ADJUST_TARGET, otherwise leave blank]
REASON: [1 sentence]
---

For new trades (optional), add:
NEW_TRADE:
SYMBOL: [symbol]
SIDE: [BUY or SELL]
ENTRY_PRICE: [price]
STOP_LOSS: [price]
TARGET: [price]
QTY: [quantity]
RATIONALE: [1 sentence]
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
        Drops trades that would push over the limit.
        Warns when a trade is dropped.
        """
        budget    = self._budget
        max_pct   = self.cfg.MAX_POSITION_PCT / 100
        max_per   = budget * max_pct
        allocated = 0
        valid     = []

        for t in trades:
            cost = t["entry_price"] * t["qty"]

            if cost > max_per:
                self.log.warning(
                    f"Dropping {t['symbol']}: ₹{cost:,.0f} exceeds "
                    f"per-stock limit of ₹{max_per:,.0f}"
                )
                continue

            if allocated + cost > budget:
                self.log.warning(
                    f"Dropping {t['symbol']}: ₹{cost:,.0f} would exceed "
                    f"total budget of ₹{budget:,}"
                )
                continue

            allocated += cost
            valid.append(t)

        if valid:
            self.log.info(
                f"Total allocated: ₹{allocated:,.0f} / ₹{budget:,} "
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
