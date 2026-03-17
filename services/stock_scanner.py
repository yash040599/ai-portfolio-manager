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

    def scan(self, quotes: dict, nifty_context: str = "") -> list[dict]:
        """
        Asks Claude to pick intraday trade candidates.

        Args:
            quotes: dict of live Kite quotes keyed by "NSE:SYMBOL".
                    Each value has last_price, ohlc, volume, etc.
            nifty_context: optional string with NIFTY 50 index data for trend filter.

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

        prompt = self._build_scan_prompt(snapshot, nifty_context)

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

    def _build_scan_prompt(self, snapshot: str, nifty_context: str = "") -> str:
        """
        Builds the pre-market scan prompt.
        Claude is given the full price data and budget constraints,
        and must return trade plans in a strict parseable format.
        """
        today  = datetime.date.today().strftime("%B %d, %Y")
        now    = datetime.datetime.now().strftime("%I:%M %p")
        budget = self._budget
        max_positions  = self.cfg.MAX_POSITIONS
        max_pct        = self.cfg.MAX_POSITION_PCT
        default_sl     = self.cfg.DEFAULT_STOP_LOSS_PCT
        default_target = self.cfg.DEFAULT_TARGET_PCT

        return f"""You are an expert Indian stock market intraday trader (NSE) with 15 years of fund management experience.
Today is {today}, current time is {now} IST. All positions MUST be closed by 3:10 PM IST today.

BUDGET: ₹{budget:,} total capital.
MAX POSITIONS: {max_positions} stocks simultaneously.
MAX PER STOCK: {max_pct}% of budget (= ₹{budget * max_pct // 100:,} max per stock).
{nifty_context}
CRITICAL RULES — MUST FOLLOW:
1. DO NOT chase stocks already up more than 2% from previous close. These moves are extended and likely to revert.
2. DO NOT pick a stock just because it gapped up with volume — that move already happened. Look for PULLBACK ENTRIES near intraday support or VWAP.
3. RISK:REWARD must be at least 1:1.5 for every trade. If you can't find 1.5× upside vs your stop-loss, skip the stock.
4. Use REALISTIC stop-loss levels — base SL on chart structure (recent swing low for BUY, swing high for SELL), NOT a fixed %. Typical range: {default_sl}% to 2%.
5. Actively consider SHORT (SELL) trades when the market index is weak or a stock shows bearish structure. Don't default to all-BUY.
6. Prefer stocks near support (for BUY) or near resistance (for SELL) — mean-reversion setups with tight risk.
7. Avoid stocks with less than ₹10 average intraday range — too tight for meaningful P&L on small capital.
8. Total position value across all trades MUST NOT exceed ₹{budget:,}.

STRATEGY FRAMEWORK:
- Opening Range Breakout (ORB): if within the first 30 minutes, identify stocks that break above/below their opening 15-min high/low with volume.
- VWAP mean-reversion: stocks that gap up and pull back to VWAP are good BUY candidates. Stocks that gap down and rally to VWAP are good SELL candidates.
- Sector relative strength: compare each stock's % change to the NIFTY 50 index. Pick the strongest stocks in a up-trending market, weakest in a down-trending market.
- Volume confirmation: entry signals are stronger when current volume is above the stock's average. Low volume breakouts fail more often.
- Time decay: if it's past 1:00 PM, reduce your target by ~30% — less time for the move to play out. After 2:00 PM, only take high-conviction setups.

CURRENT MARKET DATA (live prices):
{snapshot}

RESPOND WITH EXACTLY THIS FORMAT. One block per trade. No text before or after.
If no good trades exist today, respond with exactly: NO_TRADES_TODAY

TRADE 1:
SYMBOL: [NSE stock symbol e.g. RELIANCE]
SIDE: [BUY or SELL]
ENTRY_PRICE: [realistic entry price in ₹, near current price]
STOP_LOSS: [stop-loss price in ₹ — based on chart structure, not a fixed %]
TARGET: [target price in ₹ — must be at least 1.5× the SL distance from entry]
QTY: [number of shares — must fit within budget constraints]
RATIONALE: [1-2 sentences: what setup you see, key level, why R:R is favorable]
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
        today = datetime.date.today().strftime("%B %d, %Y")
        now   = datetime.datetime.now().strftime("%I:%M %p")

        budget         = self._budget
        max_positions  = self.cfg.MAX_POSITIONS
        max_pct        = self.cfg.MAX_POSITION_PCT
        max_per        = budget * max_pct // 100
        max_reentries  = self.cfg.MAX_REENTRIES_PER_STOCK

        # Calculate minutes until square-off for time-pressure context
        now_dt = datetime.datetime.now()
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
                f"  {p['symbol']}: {p['side']} {p['qty']} shares @ ₹{entry:.2f}  "
                f"Current: ₹{current_price:.2f}  P&L: ₹{pnl:.2f} ({r_multiple:+.1f}R)  "
                f"SL: ₹{p.get('stop_loss', 'N/A')}  Target: ₹{p.get('target_price', 'N/A')}\n"
            )

        # Build closed/failed trade history so Claude doesn't re-enter losers
        closed_text = ""
        reentry_counts: dict[str, int] = {}
        for cp in (closed_positions or []):
            sym = cp.get("symbol", "")
            reentry_counts[sym] = reentry_counts.get(sym, 0) + 1
            closed_text += (
                f"  {sym}: {cp.get('side', '?')} {cp.get('qty', 0)} shares @ ₹{cp.get('entry_price', 0):.2f}  "
                f"Exit: ₹{cp.get('exit_price', 0):.2f}  P&L: ₹{cp.get('pnl', 0):.2f}  "
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

        return f"""You are an expert Indian stock market intraday trader (NSE) with 15 years of fund management experience.
Today is {today}, current time is {now} IST. Market closes at 3:30 PM, we square off at 3:10 PM.
TIME REMAINING: {mins_left:.0f} minutes until square-off.
{nifty_context}
CURRENT OPEN POSITIONS:
{pos_text if pos_text else "  (none)"}

CLOSED TRADES TODAY:
{closed_text if closed_text else "  (none)"}

DAY P&L SO FAR: ₹{day_pnl:,.2f}
REMAINING BUDGET: ₹{budget_remaining:,.2f}
MAX POSITIONS: {max_positions} stocks simultaneously.
MAX PER STOCK: {max_pct}% of ₹{budget:,} = ₹{max_per:,} max per stock.
{blocked_text}

REVIEW RULES — MUST FOLLOW:
1. TRAILING STOP: If a position is profitable by more than 1× the original risk (entry-to-SL distance), move the SL to at least breakeven. If profitable by 2× risk, move SL to lock in 50% of profit.
2. TIME DECAY: With {mins_left:.0f} minutes left, reduce expected targets. Under 60 min → lower target by 30%. Under 30 min → EXIT all positions unless they are very close to target.
3. CUT LOSERS EARLY: If a position is underwater and has been drifting sideways for 2+ review cycles, EXIT. Dead money is worse than a small loss.
4. DON'T AVERAGE DOWN: Never add to a losing position. Only suggest NEW trades for fresh setups with good R:R.
5. SECTOR ALIGNMENT: If NIFTY 50 has turned against your trade direction since entry, tighten the SL aggressively.
6. DO NOT RE-ENTER A STOCK THAT ALREADY HIT STOP-LOSS TODAY unless you have a fundamentally different setup (opposite direction or completely new catalyst). Check CLOSED TRADES above before suggesting any NEW trade.
7. NEW TRADE SIZING: QTY × ENTRY_PRICE must not exceed REMAINING BUDGET (₹{budget_remaining:,.0f}) or MAX PER STOCK (₹{max_per:,}), whichever is lower. Calculate QTY accordingly.

Review each position and recommend ONE action per position.
You may also suggest NEW trades if budget allows and there's a good setup (only if 60+ minutes remain).

For each position, respond with EXACTLY this format:

REVIEW 1:
SYMBOL: [symbol]
ACTION: [HOLD | EXIT | ADJUST_SL | ADJUST_TARGET]
NEW_SL: [new stop-loss price if ADJUST_SL, otherwise leave blank]
NEW_TARGET: [new target price if ADJUST_TARGET, otherwise leave blank]
REASON: [1 sentence explaining why, referencing the R-multiple or time remaining]
---

For new trades (optional, only if 60+ minutes remain), add:
NEW_TRADE:
SYMBOL: [symbol]
SIDE: [BUY or SELL]
ENTRY_PRICE: [price]
STOP_LOSS: [price]
TARGET: [price]
QTY: [quantity — MUST satisfy: QTY × ENTRY_PRICE ≤ ₹{min(budget_remaining, max_per):,.0f}]
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
                        f"{t['symbol']}: {t['qty']}x @ ₹{entry:.2f} = ₹{cost:,.0f} exceeds "
                        f"per-stock limit ₹{max_per:,.0f}. Reducing qty to {new_qty}"
                    )
                    t["qty"] = new_qty
                    cost = entry * new_qty
                else:
                    self.log.warning(
                        f"Dropping {t['symbol']}: ₹{cost:,.0f} exceeds "
                        f"per-stock limit of ₹{max_per:,.0f} and min qty is 1"
                    )
                    continue

            # Check total budget — reduce qty to fit if needed
            if allocated + cost > budget and entry > 0:
                remaining = budget - allocated
                new_qty = int(remaining / entry)
                if new_qty >= 1:
                    self.log.warning(
                        f"{t['symbol']}: {t['qty']}x @ ₹{entry:.2f} = ₹{cost:,.0f} exceeds "
                        f"remaining budget ₹{remaining:,.0f}. Reducing qty to {new_qty}"
                    )
                    t["qty"] = new_qty
                    cost = entry * new_qty
                else:
                    self.log.warning(
                        f"Dropping {t['symbol']}: ₹{cost:,.0f} would exceed "
                        f"total budget of ₹{budget:,} (only ₹{remaining:,.0f} left)"
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
