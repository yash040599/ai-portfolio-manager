# ================================================================
# services/order_engine.py
# ================================================================
# Manages order execution, position tracking, SL/target monitoring,
# and P&L calculation for Phase 2 intraday trading.
#
# Two modes controlled by Config.DRY_RUN:
#   True  → orders are LOGGED to terminal + log file. No Zerodha
#            API calls. P&L is simulated using real live prices.
#   False → orders are sent to Zerodha via ZerodhaClient.place_order().
#
# Responsibilities:
#   1. Execute or simulate trade entries (buy/sell)
#   2. Track all open positions with entry price, SL, target
#   3. Monitor prices and auto-trigger SL/target exits (rule-based)
#   4. Apply Claude review adjustments (SL/target changes, exits)
#   5. Square off all open positions at end of day
#   6. Calculate full P&L with taxes and charges
#
# Position lifecycle:
#   PENDING → OPEN → CLOSED (via SL, target, review, or square-off)
#
# Every action is logged with timestamps so the end-of-day report
# can reconstruct the full trade history.
# ================================================================

import datetime
import time

from config              import Config, now_ist
from services.stock_scanner_v2 import SECTOR_MAP, MAX_PER_SECTOR
from core.logger         import Logger
from core.zerodha_client import ZerodhaClient


class OrderEngine:

    def __init__(
        self,
        config:  type[Config],
        zerodha: ZerodhaClient,
        log:     Logger,
    ):
        self.cfg     = config
        self.zerodha = zerodha
        self.log     = log

        # ── State ─────────────────────────────────────────────────
        # All positions tracked for the day.
        # Each position dict has:
        #   symbol, exchange, side, qty,
        #   entry_price, stop_loss, target_price,
        #   exit_price (set on close), exit_reason (SL/TARGET/REVIEW/SQUARE_OFF),
        #   status (OPEN/CLOSED), pnl,
        #   entry_time, exit_time, rationale,
        #   order_id (Zerodha order ID, or "DRY_RUN_xxx" in dry mode)
        self.positions:   list[dict] = []
        self.trade_log:   list[dict] = []   # chronological log of all actions
        self.claude_calls: int       = 0    # tracks Claude API call count for cost calc

        # Dynamic budget — set by PortfolioManager after fetching Zerodha funds.
        # Falls back to MAX_BUDGET_INR if not set.
        self._budget: float = float(config.MAX_BUDGET_INR)

        # Running order counter for dry-run IDs
        self._dry_run_counter: int = 0

        # Circuit breaker baseline — after cooldown, only re-trip on
        # NEW losses exceeding the threshold (not cumulative day losses).
        self._cb_pnl_baseline: float = 0.0
        self._cb_trip_count:   int   = 0    # how many times CB tripped today

        # Consecutive SL counter — triggers a pause after N straight SL hits
        self._consecutive_sl_count: int = 0
        self._sl_pause_until: float = 0.0   # time.time() when pause ends

        # Track symbols exited as stagnant — prevents re-entering same
        # stock in the same direction (churn loop prevention).
        self._stagnant_exits: set[str] = set()  # "SYMBOL_SIDE" strings

        # ── Order failure tracking ────────────────────────────────
        # Consecutive order placement failures (resets on success).
        # When this reaches ORDER_FAILURE_LIMIT, the engine signals
        # the manager to stop calling Claude and gracefully shut down.
        self._consecutive_order_failures: int = 0
        self.ORDER_FAILURE_LIMIT: int = 3
        self._order_api_broken: bool = False

        # ── Bug fix: track bot-closed positions ────────────────────
        # Stores (symbol, side, qty) tuples of positions closed BY THE BOT
        # to prevent sync_external_positions() from misidentifying them as
        # user-closed. Cleared each time sync_external_positions() runs.
        self._bot_closed_positions: set[tuple] = set()

        # ── Bug fix: track pending SL-M order IDs ──────────────────
        # Stores SL-M order IDs that are pending on the exchange.
        # Cancelled at market close to prevent stale trigger orders.
        self._pending_order_ids: set[str] = set()

        # ── Adaptive R:R tracking ─────────────────────────────────
        # Counts scans that produced 0 entries (all candidates failed
        # R:R or other checks). After N failures, R:R floor relaxes.
        self._zero_entry_scans: int = 0
        self._rr_giveup: bool = False  # True = stop trading for the day
        self._rr_retry_active: bool = False  # True = within-scan step-down active

    # ── Adaptive R:R methods ──────────────────────────────────

    def _time_based_rr_floor(self, hour: int) -> float:
        """Returns the R:R floor for the given hour of day.

        Morning (<1 PM): RR_FLOOR_MORNING  (1.3)
        Afternoon (1-2):  RR_FLOOR_AFTERNOON (1.2)
        Late (>2 PM):     RR_FLOOR_LATE     (1.0)
        """
        if hour >= self.cfg.RR_LATE_HOUR:
            return self.cfg.RR_FLOOR_LATE
        if hour >= self.cfg.RR_AFTERNOON_HOUR:
            return self.cfg.RR_FLOOR_AFTERNOON
        return self.cfg.RR_FLOOR_MORNING

    def current_rr_floor(self, hour: int = 10) -> float:
        """Returns current R:R floor based on time of day, scan failures,
        and mid-day retry state.

        Priority:
        1. Time-based floor (morning 1.3, afternoon 1.2, late 1.0)
        2. If N scans failed → min(time_floor, RR_FLOOR_RELAXED)
        3. If mid-day retry → time_floor - RR_RETRY_STEP
        """
        time_floor = self._time_based_rr_floor(hour)

        if self._zero_entry_scans >= self.cfg.RR_RELAX_AFTER_FAILS:
            # Take the more lenient (lower) of time-based and relaxed
            return min(time_floor, self.cfg.RR_FLOOR_RELAXED)

        # Mid-day retry: step down from current floor
        if self._rr_retry_active:
            return max(time_floor - self.cfg.RR_RETRY_STEP, self.cfg.RR_FLOOR_LATE)

        return time_floor

    def _rr_floor_label(self, hour: int) -> str:
        """Returns a descriptive label for the current R:R floor state."""
        if self._zero_entry_scans >= self.cfg.RR_RELAX_AFTER_FAILS:
            return "relaxed"
        if self._rr_retry_active:
            return "retry"
        if hour >= self.cfg.RR_LATE_HOUR:
            return "late"
        if hour >= self.cfg.RR_AFTERNOON_HOUR:
            return "afternoon"
        return "morning"

    def record_scan_result(self, entered: int):
        """Called after each scan+entry cycle. Tracks 0-entry streaks."""
        if entered > 0:
            # Success — reset counter
            self._zero_entry_scans = 0
            return

        self._zero_entry_scans += 1

        if self._zero_entry_scans == self.cfg.RR_RELAX_AFTER_FAILS:
            hour_now = now_ist().hour
            new_floor = self.current_rr_floor(hour=hour_now)
            label = self._rr_floor_label(hour=hour_now)
            self.log.warning(
                f"R:R adaptive: {self._zero_entry_scans} scans with 0 entries — "
                f"relaxing R:R floor to {new_floor:.1f}:1 ({label})"
            )
        elif self._zero_entry_scans >= self.cfg.RR_GIVEUP_AFTER_FAILS:
            self._rr_giveup = True
            self.log.warning(
                f"R:R adaptive: {self._zero_entry_scans} scans with 0 entries "
                f"even at relaxed floor — no viable setups today, stopping"
            )

    def is_rr_giveup(self) -> bool:
        """True if too many scans failed even at relaxed R:R floor."""
        return self._rr_giveup

    def set_budget(self, amount: float):
        """Sets the trading budget and adjusts MAX_POSITIONS dynamically."""
        self._budget = amount
        if hasattr(self.cfg, 'dynamic_max_positions'):
            new_max = self.cfg.dynamic_max_positions(amount)
            if new_max != self.cfg.MAX_POSITIONS:
                self.log.info(
                    f"MAX_POSITIONS adjusted: {self.cfg.MAX_POSITIONS} → {new_max} "
                    f"(budget Rs.{amount:,.0f})"
                )
                self.cfg.MAX_POSITIONS = new_max

    def is_order_api_broken(self) -> bool:
        """
        Returns True if Zerodha order API has failed consecutively
        and the engine should stop placing new orders.
        """
        return self._order_api_broken

    # ================================================================
    # RESUME — LOAD EXISTING POSITIONS FROM ZERODHA
    # ================================================================

    def load_existing_positions(self) -> int:
        """
        Fetches today's open MIS positions from Zerodha and loads them
        into the engine. Used when restarting after a crash so the bot
        can resume monitoring positions that are still live.

        Returns the number of positions loaded.
        """
        try:
            positions_data = self.zerodha.get_positions()
        except Exception as e:
            self.log.error(f"Failed to fetch positions from Zerodha: {e}")
            return 0

        net_positions = positions_data.get("net", [])
        loaded = 0
        now = now_ist()

        for pos in net_positions:
            # Only MIS (intraday) positions with open quantity
            if pos.get("product") != "MIS":
                continue
            qty = pos.get("quantity", 0)
            if qty == 0:
                continue

            symbol   = pos.get("tradingsymbol", "")
            exchange = pos.get("exchange", "NSE")
            avg_price = pos.get("average_price", 0)

            if avg_price <= 0 or not symbol:
                continue

            side = "BUY" if qty > 0 else "SELL"
            abs_qty = abs(qty)

            # ATR-based SL/target (same logic as enter_trade / sync_external)
            atr = self.calculate_atr(symbol, exchange)
            result = self._compute_atr_sl_target(avg_price, side, atr)
            if result:
                sl, target = result
            else:
                sl, target = self._default_sl_target(avg_price, side)

            position = {
                "symbol":       symbol,
                "exchange":     exchange,
                "side":         side,
                "qty":          abs_qty,
                "entry_price":  round(avg_price, 2),
                "stop_loss":    sl,
                "target_price": target,
                "exit_price":   None,
                "exit_reason":  None,
                "status":       "OPEN",
                "pnl":          0.0,
                "entry_time":   now.strftime("%H:%M:%S"),
                "exit_time":    None,
                "rationale":    "Resumed from existing Zerodha position",
                "order_id":     "RESUMED",
            }
            self.positions.append(position)
            loaded += 1

            sl_label = f"SL Rs.{sl:.2f}" if atr else f"SL Rs.{sl:.2f} (fallback)"
            self.log.success(
                f"Resumed: {side} {abs_qty}x {symbol} @ Rs.{avg_price:.2f} | "
                f"{sl_label} | Target Rs.{target:.2f}"
            )
            self._log_action("RESUME", symbol, side, abs_qty, avg_price,
                             "Loaded from existing Zerodha position")

        return loaded

    # ================================================================
    # SYNC — DETECT EXTERNALLY OPENED POSITIONS
    # ================================================================

    def sync_external_positions(self) -> int:
        """
        Checks Zerodha for MIS (intraday) positions that the bot doesn't
        know about (opened manually via the Zerodha app/web). Loads them
        into the engine with ATR-based SL/targets so the bot manages
        them going forward — monitoring, Claude review, and square-off.

        Only considers MIS (intraday) positions. CNC (delivery/long-term)
        positions are ignored — those are investments, not intraday trades.

        Also detects when a manually-opened position has been closed by the
        user on Zerodha — marks it EXTERNAL_CLOSE internally.

        Called before re-scans and new trade entry.
        Returns the number of new external positions detected.
        
        BUG FIX (Apr 9 2026): Only mark as EXTERNAL_CLOSE if NOT in
        _bot_closed_positions — prevents misidentifying bot-closed trades
        as user-closed trades, which was creating duplicate positions.
        
        BUG FIX (Apr 9 2026 - V2): Clear _bot_closed_positions at START
        to prevent stale entries affecting duplicate detection in rapid syncs.
        """
        # BUG FIX: Clear at START to prevent stale entries
        self._bot_closed_positions.clear()
        
        if self.cfg.DRY_RUN:
            return 0

        try:
            positions_data = self.zerodha.get_positions()
        except Exception as e:
            self.log.warning(f"sync_external_positions: failed to fetch positions — {e}")
            return 0

        net_positions = positions_data.get("net", [])
        loaded = 0

        # BUG FIX: Track (symbol, side) not just symbol
        # Prevents duplicate detection when bot has SELL and user has BUY
        known_positions = {
            (p["symbol"], p["side"]) for p in self.positions 
            if p["status"] == "OPEN"
        }

        for pos in net_positions:
            # ONLY MIS (intraday) — CNC (delivery) is long-term, not our business
            if pos.get("product") != "MIS":
                continue
            qty = pos.get("quantity", 0)
            if qty == 0:
                continue

            symbol    = pos.get("tradingsymbol", "")
            exchange  = pos.get("exchange", "NSE")
            avg_price = pos.get("average_price", 0)

            if not symbol or avg_price <= 0:
                continue

            # BUG FIX: Check (symbol, side) not just symbol
            # Prevents: bot SELL 45, user BUY 45 → no collision
            side = "BUY" if qty > 0 else "SELL"
            abs_qty = abs(qty)
            if (symbol, side) in known_positions:
                continue

            # ATR-based SL/target (same logic as enter_trade / load_existing)
            atr = self.calculate_atr(symbol, exchange)
            result = self._compute_atr_sl_target(avg_price, side, atr)
            if result:
                sl, target = result
            else:
                sl, target = self._default_sl_target(avg_price, side)

            position = {
                "symbol":       symbol,
                "exchange":     exchange,
                "side":         side,
                "qty":          abs_qty,
                "entry_price":  round(avg_price, 2),
                "stop_loss":    sl,
                "target_price": target,
                "exit_price":   None,
                "exit_reason":  None,
                "status":       "OPEN",
                "pnl":          0.0,
                "entry_time":   now_ist().strftime("%H:%M:%S"),
                "exit_time":    None,
                "rationale":    "Manual intraday position (entered via Zerodha app)",
                "order_id":     "EXTERNAL",
                "_external":    True,   # origin marker for reports
            }
            self.positions.append(position)
            known_positions.add((symbol, side))
            loaded += 1

            sl_label = f"SL Rs.{sl:.2f}" if atr else f"SL Rs.{sl:.2f} (fallback)"
            self.log.success(
                f"Adopted external MIS position: {side} {abs_qty}x {symbol} "
                f"@ Rs.{avg_price:.2f} | {sl_label} | Target Rs.{target:.2f}"
            )
            self._log_action("ADOPT_EXTERNAL", symbol, side, abs_qty, avg_price,
                             "Manual intraday position adopted for management")

        # Detect positions (external OR bot-opened) that the user closed
        # on Zerodha — mark them EXTERNAL_CLOSE internally.
        # BUG FIX: Only if NOT in _bot_closed_positions (prevent duplicates)
        zerodha_open = {
            pos.get("tradingsymbol", "")
            for pos in net_positions
            if pos.get("product") == "MIS" and pos.get("quantity", 0) != 0
        }
        for p in self.positions:
            if (
                p["status"] == "OPEN"
                and p["symbol"] not in zerodha_open
                and (p["symbol"], p["side"], p["qty"]) not in self._bot_closed_positions
            ):
                # This is a position the bot doesn't know about and it's NOT
                # in bot_closed_positions, so it looks like a user close.
                # Fetch exit price from Zerodha's day position data with multiple fallbacks
                exit_price = None
                
                # BUG FIX: Try multiple sources for exit price
                # 1. Try Zerodha position data (sell_price for BUY, buy_price for SELL)
                for zp in net_positions:
                    if zp.get("tradingsymbol") == p["symbol"] and zp.get("product") == "MIS":
                        if p["side"] == "BUY":
                            exit_price = zp.get("sell_price") or zp.get("last_price")
                        else:
                            exit_price = zp.get("buy_price") or zp.get("last_price")
                        break
                
                # 2. Fallback: Get current market price if not found above
                if not exit_price:
                    try:
                        quotes = self.zerodha.get_quotes(
                            [{"symbol": p["symbol"], "exchange": p.get("exchange", "NSE")}]
                        ) or {}
                        quote_key = f"{p.get('exchange', 'NSE')}:{p['symbol']}"
                        exit_price = quotes.get(quote_key, {}).get("last_price")
                    except Exception as e:
                        self.log.warning(f"Failed to get market quote for {p['symbol']}: {e}")
                
                # 3. Final fallback: entry price (with error logged)
                if not exit_price:
                    exit_price = p["entry_price"]
                    self.log.error(
                        f"EXTERNAL_CLOSE exit price unknown for {p['symbol']} — "
                        f"using entry price Rs.{exit_price:.2f} (P&L calculation will be INCORRECT)"
                    )
                else:
                    exit_price = round(exit_price, 2)

                if p["side"] == "BUY":
                    pnl = (exit_price - p["entry_price"]) * p["qty"]
                else:
                    pnl = (p["entry_price"] - exit_price) * p["qty"]

                # Cancel pending SL-M order for bot-opened positions
                sl_oid = p.get("_sl_order_id")
                if sl_oid and not self.cfg.DRY_RUN:
                    try:
                        self.zerodha.cancel_order(sl_oid)
                        self.log.info(f"Cancelled orphaned SL-M {sl_oid} for {p['symbol']}")
                    except Exception:
                        pass  # order may already be cancelled/completed
                    self._pending_order_ids.discard(sl_oid)
                    p["_sl_order_id"] = None

                origin = "External" if p.get("_external") else "Bot"
                p["status"] = "CLOSED"
                p["exit_price"] = round(exit_price, 2)
                p["exit_reason"] = "EXTERNAL_CLOSE"
                p["exit_time"] = now_ist().strftime("%H:%M:%S")
                p["pnl"] = round(pnl, 2)
                self.log.info(
                    f"{origin} position closed by user: {p['side']} {p['qty']}x "
                    f"{p['symbol']} @ Rs.{exit_price:.2f} | P&L: Rs.{pnl:+,.2f}"
                )
                self._log_action("EXTERNAL_CLOSE", p["symbol"], p["side"],
                                 p["qty"], exit_price, "User closed via Zerodha app")
        
        # NOTE: _bot_closed_positions already cleared at function START
        # Do NOT clear here — it needs to persist until next sync() call
        # BUG FIX: clearing at end causes stale entries in rapid successive calls

        return loaded

    def refresh_budget(self) -> float:
        """
        Re-queries Zerodha for actual available funds and updates the
        budget. Called before re-scans to account for margin used by
        external trades.

        Returns updated budget.
        """
        if self.cfg.DRY_RUN:
            return self._budget

        try:
            available = self.zerodha.get_available_funds()
            max_budget = float(self.cfg.MAX_BUDGET_INR)
            self._budget = min(available, max_budget)
        except Exception:
            pass  # keep existing budget on failure

        return self._budget

    # ================================================================
    # ATR CALCULATION
    # ================================================================

    def calculate_atr(self, symbol: str, exchange: str = "NSE", period: int = 0) -> float | None:
        """
        Computes the Average True Range over `period` candles.
        Uses intraday candles (default: 15-minute) for intraday-appropriate levels.
        Returns ATR as a price value, or None if data is unavailable.

        True Range = max(high-low, |high-prev_close|, |low-prev_close|)
        ATR = SMA of True Range over `period` candles.
        """
        if period <= 0:
            period = self.cfg.ATR_PERIOD

        interval = self.cfg.ATR_INTERVAL

        to_date   = now_ist().date()
        # For intraday intervals, fetch 5 trading days to get enough candles
        buffer_days = 5 if "minute" in interval else period * 2
        from_date = to_date - datetime.timedelta(days=buffer_days)

        try:
            candles = self.zerodha.get_historical(symbol, exchange, from_date, to_date, interval)
        except Exception as e:
            self.log.info(f"ATR: no historical data for {symbol}: {e}")
            return None

        if not candles or len(candles) < period + 1:
            self.log.info(f"ATR: insufficient data for {symbol} ({len(candles) if candles else 0} candles)")
            return None

        # Use the last `period + 1` candles so we have `period` TR values
        candles = candles[-(period + 1):]
        true_ranges = []

        for i in range(1, len(candles)):
            high       = candles[i]["high"]
            low        = candles[i]["low"]
            prev_close = candles[i - 1]["close"]

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        atr = sum(true_ranges) / len(true_ranges)
        return round(atr, 2)

    def _compute_atr_sl_target(
        self, price: float, side: str, atr: float | None
    ) -> tuple[float, float] | None:
        """
        Compute ATR-based stop-loss and target for a given entry price.
        Returns (sl, target) or None if ATR is unavailable/zero.

        SL  = price ± ATR_MULTIPLIER × ATR
        Tgt = price ± ATR_MULTIPLIER × RR_TARGET_RATIO × ATR
        SL is capped at MAX_INTRADAY_SL_PCT (target rescaled to keep R:R).

        Used by: enter_trade, load_existing_positions, sync_external_positions.
        """
        if not atr or atr <= 0:
            return None

        multiplier = self.cfg.ATR_MULTIPLIER
        rr_mult = self.cfg.RR_TARGET_RATIO

        if side == "BUY":
            sl     = round(price - multiplier * atr, 2)
            target = round(price + multiplier * rr_mult * atr, 2)
        else:
            sl     = round(price + multiplier * atr, 2)
            target = round(price - multiplier * rr_mult * atr, 2)

        # Cap SL at MAX_INTRADAY_SL_PCT to prevent swing-trade-sized stops
        max_sl_pct = self.cfg.MAX_INTRADAY_SL_PCT
        sl_pct = abs(sl - price) / price * 100
        if sl_pct > max_sl_pct:
            if side == "BUY":
                sl     = round(price * (1 - max_sl_pct / 100), 2)
                target = round(price * (1 + max_sl_pct * rr_mult / 100), 2)
            else:
                sl     = round(price * (1 + max_sl_pct / 100), 2)
                target = round(price * (1 - max_sl_pct * rr_mult / 100), 2)

        return sl, target

    def _default_sl_target(self, price: float, side: str) -> tuple[float, float]:
        """Fallback SL/target using DEFAULT_STOP_LOSS_PCT / DEFAULT_TARGET_PCT."""
        sl_pct  = self.cfg.DEFAULT_STOP_LOSS_PCT / 100
        tgt_pct = self.cfg.DEFAULT_TARGET_PCT / 100
        if side == "BUY":
            return round(price * (1 - sl_pct), 2), round(price * (1 + tgt_pct), 2)
        return round(price * (1 + sl_pct), 2), round(price * (1 - tgt_pct), 2)

    # ================================================================
    # ENTRY ORDER — LIMIT WITH MARKET FALLBACK
    # ================================================================

    def _place_entry_order(
        self, symbol: str, exchange: str, qty: int, side: str, price: float,
    ) -> str:
        """
        Places an entry order using LIMIT-first strategy when enabled.

        Industry-standard approach for Indian algo trading:
          1. Place LIMIT at LTP + 1 tick (BUY) or LTP - 1 tick (SELL)
             — catches 2 price levels in the order book for faster fill
             — max "slippage" is 1 tick/share (negligible vs MARKET)
          2. Wait FULL timeout, polling filled_qty every second
             — don't return early on first partial fill
             — most NIFTY100 orders fill fully in 2-5 seconds
          3. After timeout: if fully filled → return
          4. If partially filled → cancel unfilled remainder, accept partial
          5. If zero filled → cancel, retry with fresh LTP
          6. After all retries fail → MARKET fallback

        NSE tick size varies by instrument (Rs.0.05 or Rs.0.50).
        Fetched dynamically via zerodha.get_tick_size().
        MARKET slippage saved: Rs.0.50-3.00 per share typically.

        Returns order_id on success. Raises on failure.
        """
        if not self.cfg.USE_LIMIT_ORDERS:
            return self.zerodha.place_order(
                symbol=symbol, exchange=exchange,
                qty=qty, side=side, order_type="MARKET",
            )

        max_retries = self.cfg.LIMIT_MAX_RETRIES
        timeout = self.cfg.LIMIT_ORDER_TIMEOUT
        tick = self.zerodha.get_tick_size(symbol, exchange)

        for attempt in range(1, max_retries + 1):
            # Fetch fresh LTP
            try:
                quotes = self.zerodha.get_quotes(
                    [{"symbol": symbol, "exchange": exchange}]
                ) or {}
                ltp = quotes.get(f"{exchange}:{symbol}", {}).get("last_price", 0)
            except Exception:
                ltp = 0

            if ltp <= 0:
                ltp = price  # fallback to planned entry

            # 1-tick buffer: ensures we're at the front of the queue
            # and catch the next price level in the order book.
            # BUY: bid slightly higher → fill faster
            # SELL: ask slightly lower → fill faster
            if side == "BUY":
                limit_price = self.zerodha.round_to_tick(ltp + tick, tick)
            else:
                limit_price = self.zerodha.round_to_tick(ltp - tick, tick)

            self.log.info(
                f"LIMIT entry attempt {attempt}/{max_retries}: "
                f"{side} {qty}x {symbol} @ Rs.{limit_price:.2f} "
                f"(LTP Rs.{ltp:.2f} + {tick} tick buffer)"
            )
            try:
                order_id = self.zerodha.place_order(
                    symbol=symbol, exchange=exchange,
                    qty=qty, side=side,
                    order_type="LIMIT", price=limit_price,
                )
            except Exception as e:
                self.log.warning(f"LIMIT order placement failed: {e}")
                break  # fall through to MARKET

            # Wait FULL timeout, polling filled qty every second.
            # Don't return early on first partial fill — give the
            # exchange time to fill the remaining shares.
            filled_qty = 0
            for sec in range(timeout):
                time.sleep(1)
                filled_qty = self.zerodha.get_order_filled_qty(order_id) or 0
                if filled_qty >= qty:
                    break  # fully filled before timeout

            if filled_qty >= qty:
                # Fully filled
                fill_price = self.zerodha.get_order_fill_price(order_id, timeout=2)
                self.log.success(
                    f"LIMIT fill: {side} {filled_qty}x {symbol} "
                    f"@ Rs.{fill_price or limit_price:.2f} "
                    f"(attempt {attempt}, {sec + 1}s)"
                )
                return str(order_id)

            if filled_qty > 0:
                # Partial fill after FULL timeout — accept and cancel unfilled.
                fill_price = self.zerodha.get_order_fill_price(order_id, timeout=2)
                self.log.warning(
                    f"LIMIT partial fill after {timeout}s: "
                    f"{filled_qty}/{qty} shares @ Rs.{fill_price or limit_price:.2f} "
                    f"— accepting partial, cancelling remainder"
                )
                try:
                    self.zerodha.cancel_order(order_id)
                except Exception:
                    pass  # remainder may already be cancelled
                return str(order_id)

            # Zero fills after full timeout — cancel before retry
            self.log.info(
                f"LIMIT not filled in {timeout}s — cancelling order {order_id}"
            )
            cancel_ok = False
            try:
                self.zerodha.cancel_order(order_id)
                cancel_ok = True
            except Exception as e:
                self.log.warning(f"LIMIT cancel failed for {order_id}: {e}")

            if not cancel_ok:
                # Cancel failed — check if it filled during cancel attempt
                time.sleep(1)
                recheck_qty = self.zerodha.get_order_filled_qty(order_id) or 0
                if recheck_qty > 0:
                    self.log.warning(
                        f"LIMIT order {order_id} filled {recheck_qty} shares "
                        f"after cancel attempt — using this fill"
                    )
                    return str(order_id)
                # Not filled and can't cancel — don't place MARKET (double-entry risk)
                raise RuntimeError(
                    f"LIMIT order {order_id} for {symbol}: cancel failed and "
                    f"fill status unknown — manual check required"
                )

        # All LIMIT attempts exhausted — fall back to MARKET
        self.log.warning(
            f"LIMIT failed {max_retries}x for {symbol} — "
            f"falling back to MARKET for {qty} shares"
        )
        return self.zerodha.place_order(
            symbol=symbol, exchange=exchange,
            qty=qty, side=side, order_type="MARKET",
        )

    # ================================================================
    # ENTRY — OPEN A NEW POSITION
    # ================================================================

    def enter_trade(self, trade: dict) -> bool:
        """
        Opens a new position based on a trade plan from StockScanner.

        In dry-run mode: logs the order, assigns a fake order ID,
        and tracks the position using real live prices.

        In live mode: calls ZerodhaClient.place_order() and tracks
        the returned order ID.

        Returns True if the order was placed/logged successfully.

        Entry pipeline (each step can reject the trade):
          1. Validate entry price vs live Zerodha quote
          2. Bid-ask spread check (illiquid stocks)
          2b. Volume confirmation (RVol gate with scan-time fallback)
          3. ATR-based SL/target (pure ATR when available, config fallback otherwise)
          4. Late-entry target reduction (13:00 / 14:00 cutoffs)
          5. R:R floor check (time-based: morning 1.3, afternoon 1.2, late 1.0 + adaptive relaxation)
          6. Minimum profit check (must cover round-trip charges)
          7. Slippage simulation (dry-run only)
          8. Budget / max positions / duplicate / sector / direction guards
          9. Short entry cutoff
         10. Max re-entries per stock + declining score block
         11. RSI contradiction filter (no SELL when RSI>70, no BUY when RSI<30)
         12. Daily trade cap + expiry trade cap
         13. Stagnant churn guard (no re-enter stagnant exits)
         14. VWAP trend block (no BUY below VWAP, no SELL above VWAP)
         15. Net-of-charges R:R check (effective R:R ≥ 1.0:1 after costs)
         16. Place order → scale SL/target to actual fill price
         17. Place exchange SL-M for instant stop-loss execution
        """
        symbol    = trade["symbol"]
        exchange  = trade.get("exchange", "NSE")
        side      = trade["side"]
        qty       = trade["qty"]
        entry     = trade["entry_price"]
        sl        = trade["stop_loss"]
        target    = trade["target_price"]
        rationale = trade.get("rationale", "")

        now = now_ist()

        # ── Validate entry price against live quote ───────────────
        # Claude can hallucinate prices. Always use Zerodha as source of truth.
        live_quotes = {}
        try:
            live_quotes = self.zerodha.get_quotes(
                [{"symbol": symbol, "exchange": exchange}]
            ) or {}
            live_price = live_quotes.get(
                f"{exchange}:{symbol}", {}
            ).get("last_price", 0)
        except Exception:
            live_price = 0

        if live_price > 0:
            deviation = abs(entry - live_price) / live_price
            if deviation > 0.05:
                self.log.warning(
                    f"Entry price override: {symbol} Claude said Rs.{entry:.2f} "
                    f"but live quote is Rs.{live_price:.2f} "
                    f"({deviation*100:.1f}% off) — using live price"
                )
                entry = live_price
                trade["entry_price"] = entry
            else:
                self.log.info(
                    f"  ✓ {symbol}: price validated — plan Rs.{entry:.2f}, "
                    f"live Rs.{live_price:.2f} ({deviation*100:.1f}% off)"
                )

        # ── Bid-ask spread check ──────────────────────────────────
        max_spread = self.cfg.MAX_SPREAD_PCT
        if max_spread > 0 and not self.cfg.DRY_RUN:
            quote_data = live_quotes.get(f"{exchange}:{symbol}", {})
            depth = quote_data.get("depth", {})
            best_bid = (depth.get("buy", [{}]) or [{}])[0].get("price", 0)
            best_ask = (depth.get("sell", [{}]) or [{}])[0].get("price", 0)
            if best_bid > 0 and best_ask > 0:
                spread_pct = (best_ask - best_bid) / best_bid * 100
                if spread_pct > max_spread:
                    self.log.warning(
                        f"{symbol}: bid-ask spread {spread_pct:.2f}% exceeds "
                        f"MAX_SPREAD_PCT ({max_spread}%) — skipping "
                        f"(bid Rs.{best_bid:.2f} / ask Rs.{best_ask:.2f})"
                    )
                    return False
                self.log.info(
                    f"  ✓ {symbol}: spread {spread_pct:.2f}% OK "
                    f"(bid Rs.{best_bid:.2f} / ask Rs.{best_ask:.2f})"
                )

        # ── Volume confirmation at entry ──────────────────────────
        # Skip stocks with below-average recent volume — low conviction.
        # Note: Kite quote API returns "volume" (today's traded qty) and
        # "average_price" (VWAP), but NOT "average_volume". The field
        # doesn't exist in Kite's response so avg_volume is always 0.
        # We fall back to scan-time RVol from the indicator snapshot.
        if not self.cfg.DRY_RUN:
            quote_data = live_quotes.get(f"{exchange}:{symbol}", {})
            live_volume = quote_data.get("volume", 0)
            avg_volume = quote_data.get("average_volume", 0)
            if avg_volume > 0 and live_volume > 0:
                rvol = live_volume / avg_volume
                if rvol < 0.7:
                    self.log.warning(
                        f"{symbol}: live RVol {rvol:.1f}x (< 0.7x avg) — "
                        f"low volume, skipping entry"
                    )
                    return False
                self.log.info(f"  ✓ {symbol}: RVol {rvol:.1f}x OK (≥0.7x)")
            else:
                # Zerodha didn't provide average_volume — fall back to
                # scan-time RVol from the indicator snapshot.
                scan_rvol = 0
                snap_str = trade.get("_indicator_snapshot", "")
                if snap_str:
                    try:
                        import json
                        snap = json.loads(snap_str)
                        scan_rvol = snap.get("rvol", 0)
                    except Exception:
                        pass
                if scan_rvol > 0 and scan_rvol < 0.7:
                    self.log.warning(
                        f"{symbol}: scan RVol {scan_rvol:.1f}x (< 0.7x) — "
                        f"low volume at scan time, skipping entry"
                    )
                    return False
                elif scan_rvol > 0:
                    self.log.info(f"  ✓ {symbol}: scan RVol {scan_rvol:.1f}x OK (≥0.7x, live avg unavailable)")
                else:
                    self.log.info(f"  ⚠ {symbol}: volume data unavailable — proceeding without RVol check")

        # ── ATR-based dynamic stop-loss / target ──────────────────
        atr = self.calculate_atr(symbol, exchange)
        result = self._compute_atr_sl_target(entry, side, atr)
        if result:
            sl, target = result

            # Log cap info if SL was wider than MAX_INTRADAY_SL_PCT
            raw_sl_pct = self.cfg.ATR_MULTIPLIER * atr / entry * 100
            max_sl_pct = self.cfg.MAX_INTRADAY_SL_PCT
            if raw_sl_pct > max_sl_pct:
                self.log.info(
                    f"ATR SL was {raw_sl_pct:.1f}% — capped to {max_sl_pct}%: "
                    f"SL Rs.{sl:.2f} | Target Rs.{target:.2f}"
                )

            self.log.info(
                f"ATR({self.cfg.ATR_PERIOD}, {self.cfg.ATR_INTERVAL}) "
                f"for {symbol}: Rs.{atr:.2f} | "
                f"Dynamic SL: Rs.{sl:.2f} | Target: Rs.{target:.2f}"
            )
        else:
            self.log.info(
                f"ATR unavailable for {symbol} — using Claude SL: Rs.{sl:.2f} / Target: Rs.{target:.2f}"
            )

        # ── SL sanity check: ensure SL is on the correct side of entry ─
        # Can happen when entry was overridden to live price but SL was
        # calculated by Claude using a stale/hallucinated entry price.
        if side == "BUY" and sl >= entry:
            default_sl_pct = self.cfg.DEFAULT_STOP_LOSS_PCT
            sl = round(entry * (1 - default_sl_pct / 100), 2)
            target = round(entry * (1 + default_sl_pct * self.cfg.RR_TARGET_RATIO / 100), 2)
            self.log.warning(
                f"{symbol}: SL was above entry (invalid for BUY) — "
                f"reset to default {default_sl_pct}%: SL Rs.{sl:.2f} | Target Rs.{target:.2f}"
            )
        elif side == "SELL" and sl <= entry:
            default_sl_pct = self.cfg.DEFAULT_STOP_LOSS_PCT
            sl = round(entry * (1 + default_sl_pct / 100), 2)
            target = round(entry * (1 - default_sl_pct * self.cfg.RR_TARGET_RATIO / 100), 2)
            self.log.warning(
                f"{symbol}: SL was below entry (invalid for SELL) — "
                f"reset to default {default_sl_pct}%: SL Rs.{sl:.2f} | Target Rs.{target:.2f}"
            )

        # ── Late-entry target reduction ───────────────────────────
        # Two-tier cutoffs based on time remaining before 3:10 PM square-off:
        #   13:00+ → 20% target reduction (still ~2h to hit target)
        #   14:00+ → 25% target reduction (only ~1h, aggressive trades fail)
        # This prevents entering with unreachable targets that end up
        # hitting time-decay or square-off instead of target.
        hour_now = now.hour
        late_reduction = 0.0
        if hour_now >= self.cfg.RR_LATE_HOUR:
            late_reduction = self.cfg.LATE_TARGET_CUT_PCT_2
        elif hour_now >= self.cfg.RR_AFTERNOON_HOUR:
            late_reduction = self.cfg.LATE_TARGET_CUT_PCT_1

        if late_reduction > 0:
            if side == "BUY":
                distance = target - entry
                target = round(entry + distance * (1 - late_reduction / 100), 2)
            else:
                distance = entry - target
                target = round(entry - distance * (1 - late_reduction / 100), 2)
            self.log.info(
                f"Late entry ({hour_now}:xx): target reduced by {late_reduction:.0f}% → Rs.{target:.2f}"
            )
            # Mark position so time-decay doesn't stack on top
            trade["_late_entry_reduced"] = True

        # ── R:R safety floor (time-aware + adaptive) ─────────────
        # One unified check. Floor depends on time of day:
        #   Morning (<1 PM): RR_FLOOR_MORNING (1.3)
        #   Afternoon (1-2): RR_FLOOR_AFTERNOON (1.2)
        #   Late (>2 PM):    RR_FLOOR_LATE (1.0)
        # After N zero-entry scans: min(time_floor, RR_FLOOR_RELAXED)
        # Mid-day retry: time_floor - RR_RETRY_STEP
        rr_floor = self.current_rr_floor(hour=hour_now)
        floor_label = self._rr_floor_label(hour=hour_now)
        sl_dist = abs(entry - sl)
        tgt_dist = abs(target - entry)

        if sl_dist > 0 and tgt_dist / sl_dist < rr_floor:
            actual_rr = tgt_dist / sl_dist
            self.log.warning(
                f"{symbol}: R:R {actual_rr:.2f}:1 is below {rr_floor:.1f}:1 "
                f"{floor_label} floor — skipping"
            )
            return False
        elif sl_dist > 0:
            self.log.info(
                f"  ✓ {symbol}: R:R {tgt_dist/sl_dist:.2f}:1 OK "
                f"(floor {rr_floor:.1f}:1 {floor_label}) | "
                f"SL Rs.{sl:.2f} | Target Rs.{target:.2f}"
            )

        # ── Pre-trade minimum profit check ────────────────────────
        # Skip trades where expected profit doesn't cover charges.
        # Round-trip charges for small intraday trades ~Rs.40-50.
        min_profit = self.cfg.MIN_EXPECTED_PROFIT
        expected_profit = abs(target - entry) * qty
        if expected_profit < min_profit:
            self.log.warning(
                f"{symbol}: expected profit Rs.{expected_profit:.0f} "
                f"< min Rs.{min_profit} (charges will eat it). Skipping."
            )
            return False
        self.log.info(
            f"  ✓ {symbol}: expected profit Rs.{expected_profit:.0f} OK "
            f"(min Rs.{min_profit})"
        )

        # ── Apply slippage in dry-run mode for realism ────────────
        if self.cfg.DRY_RUN and self.cfg.SLIPPAGE_PCT > 0:
            slip_pct = self._adjusted_slippage(now.hour)
            slip = entry * slip_pct / 100
            if side == "BUY":
                entry = round(entry + slip, 2)   # buy slightly higher
            else:
                entry = round(entry - slip, 2)   # sell slightly lower

        # ── Budget check before entering ──────────────────────────
        # If qty doesn't fit, reduce to what fits (preserves trade conviction).
        # Only reject if even 1 share exceeds remaining budget.
        cost = entry * qty
        current_exposure = self._total_open_exposure()
        if current_exposure + cost > self._budget:
            remaining = self._budget - current_exposure
            max_qty = int(remaining / entry) if entry > 0 else 0
            if max_qty >= 1:
                self.log.warning(
                    f"{symbol}: {qty}x @ Rs.{entry:.2f} = Rs.{cost:,.0f} exceeds budget. "
                    f"Reducing qty to {max_qty} (Rs.{max_qty * entry:,.0f})"
                )
                qty = max_qty
                trade["qty"] = qty
                cost = entry * qty
            else:
                self.log.warning(
                    f"Cannot enter {symbol}: Rs.{cost:,.0f} would exceed "
                    f"budget (current exposure: Rs.{current_exposure:,.0f}, "
                    f"remaining: Rs.{remaining:,.0f})"
                )
                return False

        # ── Max positions check ───────────────────────────────────
        open_count = len([p for p in self.positions if p["status"] == "OPEN"])
        if open_count >= self.cfg.MAX_POSITIONS:
            ext_count = len([p for p in self.positions if p["status"] == "OPEN" and p.get("_external")])
            bot_count = open_count - ext_count
            msg = f"Cannot enter {symbol}: already at max {self.cfg.MAX_POSITIONS} positions"
            if ext_count:
                msg += f" ({bot_count} bot + {ext_count} external/manual)"
            self.log.warning(msg)
            return False

        # ── Duplicate symbol guard ────────────────────────────────
        if self._find_open_position(symbol):
            self.log.warning(
                f"Cannot enter {symbol}: already have an open position "
                f"(bot or external)"
            )
            return False

        # ── Sector concentration guard ────────────────────────────
        sector = SECTOR_MAP.get(symbol, "OTHER")
        sector_open = sum(
            1 for p in self.positions
            if p["status"] == "OPEN"
            and SECTOR_MAP.get(p["symbol"], "OTHER") == sector
        )
        if sector_open >= MAX_PER_SECTOR:
            self.log.warning(
                f"Cannot enter {symbol}: sector {sector} already has "
                f"{sector_open} open position(s) (max {MAX_PER_SECTOR})"
            )
            return False

        # ── Direction diversification guard ───────────────────────
        # Dynamic direction limit based on signal strength:
        #   - Normal: max MAX_POSITIONS-1 in same direction (diversified)
        #   - Strong signal (|score| >= 5): allow ALL slots in same direction
        #     (on a strongly trending day, forcing counter-trend trades loses money)
        # The scanner already makes the smart direction decision upstream;
        # this guard is the last safety net.
        max_same_dir_normal = max(1, self.cfg.MAX_POSITIONS - 1)
        entry_score = abs(trade.get("_entry_score") or 0)
        max_same_dir = self.cfg.MAX_POSITIONS if entry_score >= 5 else max_same_dir_normal
        same_dir_count = sum(
            1 for p in self.positions
            if p["status"] == "OPEN" and p["side"] == side
        )
        if same_dir_count >= max_same_dir:
            self.log.warning(
                f"Cannot enter {symbol} ({side}): already have {same_dir_count} "
                f"{side} position(s) — max {max_same_dir} in same direction "
                f"(score {entry_score:.1f}, need ≥5.0 to override)"
            )
            return False

        # ── Short entry time cutoff ───────────────────────────────
        # Don't open new SHORT positions after cutoff hour.
        # Short delivery if cover fails is extremely expensive
        # (Rs.500-5000+ in penalties). Early cutoff gives time to
        # handle order failures before Zerodha's 3:25 auto-square.
        short_cutoff = self.cfg.SHORT_ENTRY_CUTOFF_HOUR
        if side == "SELL" and now.hour >= short_cutoff:
            self.log.warning(
                f"Cannot short {symbol} after {short_cutoff}:00 — "
                f"short delivery risk too high if cover fails. "
                f"Current time: {now.strftime('%H:%M')}"
            )
            return False

        # ── Max re-entries per stock check ────────────────────────
        max_reentries = self.cfg.MAX_REENTRIES_PER_STOCK
        if max_reentries > 0:
            past_entries = [
                p for p in self.positions if p["symbol"] == symbol
            ]
            if len(past_entries) >= max_reentries:
                self.log.warning(
                    f"Cannot enter {symbol}: already traded {len(past_entries)} "
                    f"time(s) today (max {max_reentries}). Skipping re-entry."
                )
                return False

            # Block re-entry when score is declining (setup weakening)
            if past_entries:
                entry_score = abs(trade.get("_entry_score", 0))
                prev_score = max(
                    abs(p.get("_entry_score", 0) or 0) for p in past_entries
                )
                if entry_score < prev_score and prev_score > 0:
                    self.log.warning(
                        f"{symbol}: re-entry score {entry_score:.1f} < "
                        f"previous {prev_score:.1f} (setup weakening) — "
                        f"skipping declining re-entry"
                    )
                    return False

        # ── RSI contradiction filter ──────────────────────────────
        # Don't SELL (short) into strong buying pressure (RSI > 70)
        # or BUY into strong selling pressure (RSI < 30).
        entry_rsi = trade.get("_entry_rsi", 0) or 0
        if entry_rsi > 0:
            if side == "SELL" and entry_rsi > 70:
                self.log.warning(
                    f"{symbol}: RSI {entry_rsi:.0f} > 70 — too overbought to "
                    f"short (strong buying pressure). Skipping."
                )
                return False
            if side == "BUY" and entry_rsi < 30:
                self.log.warning(
                    f"{symbol}: RSI {entry_rsi:.0f} < 30 — too oversold to "
                    f"buy (strong selling pressure). Skipping."
                )
                return False

        # ── Daily trade cap ───────────────────────────────────────
        # Prevent overtrading churn. Each exit+entry costs ~Rs.36.
        # Intentionally counts EXTERNAL/adopted positions too — manual
        # trades on Zerodha still use slots and add to daily churn.
        max_daily = self.cfg.MAX_TRADES_PER_DAY
        if getattr(self.cfg, '_expiry_applied', False):
            max_daily = min(max_daily, self.cfg.EXPIRY_MAX_TRADES_PER_DAY)
        if max_daily > 0:
            total_trades = len(self.positions)  # open + closed + external
            if total_trades >= max_daily:
                self.log.warning(
                    f"{symbol}: daily trade cap reached ({total_trades}/{max_daily}) — "
                    f"no more entries today"
                )
                return False

        # ── Stagnant churn guard ──────────────────────────────────
        # Don't re-enter a stock+direction that was exited as stagnant.
        stagnant_key = f"{symbol}_{side}"
        if stagnant_key in self._stagnant_exits:
            self.log.warning(
                f"{symbol}: already exited as stagnant ({side}) — "
                f"skipping to avoid churn loop"
            )
            return False

        # ── VWAP trend block ──────────────────────────────────────
        # Don't BUY below VWAP or SELL above VWAP — fighting institutional flow.
        # Skip during first 45 min (before 10:00) when VWAP has < 3 candles
        # of data and isn't statistically meaningful yet.
        if now.hour >= 10:
            snap_str = trade.get("_indicator_snapshot", "")
            if snap_str:
                try:
                    import json as _json
                    snap = _json.loads(snap_str)
                    vwap_dev = snap.get("vwap_dev", 0)
                    if vwap_dev != 0:
                        if side == "BUY" and vwap_dev < -0.3:
                            self.log.warning(
                                f"{symbol}: price {vwap_dev:+.1f}% below VWAP — "
                                f"BUY fights institutional selling pressure. Skipping."
                            )
                            return False
                        if side == "SELL" and vwap_dev > 0.3:
                            self.log.warning(
                                f"{symbol}: price {vwap_dev:+.1f}% above VWAP — "
                                f"SELL fights institutional buying pressure. Skipping."
                            )
                            return False
                except Exception:
                    pass

        # ── Net-of-charges R:R check ──────────────────────────────
        # Gross R:R may look 1.5:1, but after charges on small positions
        # the effective R:R can be much worse. Ensure net profit > 1.0× net risk.
        if qty > 0 and entry > 0:
            gross_profit = abs(target - entry) * qty
            gross_risk = abs(entry - sl) * qty
            buy_val = entry * qty
            sell_val = entry * qty  # approximate
            charges = Config.calculate_charges(buy_val, sell_val, 2)
            round_trip_charges = charges["total_tax_and_charges"]
            net_profit = gross_profit - round_trip_charges
            net_risk = gross_risk + round_trip_charges
            if net_risk > 0 and net_profit / net_risk < 1.0:
                self.log.warning(
                    f"{symbol}: net-of-charges R:R {net_profit / net_risk:.2f}:1 "
                    f"< 1.0:1 (charges Rs.{round_trip_charges:.0f} eat the edge). Skipping."
                )
                return False

        # ── All pre-trade checks passed ───────────────────────────
        sl_pct_final = abs(entry - sl) / entry * 100
        tgt_pct_final = abs(target - entry) / entry * 100
        self.log.info(
            f"  ✓ {symbol}: ALL CHECKS PASSED — {side} {qty}x @ Rs.{entry:.2f} | "
            f"SL Rs.{sl:.2f} ({sl_pct_final:.1f}%) | Target Rs.{target:.2f} ({tgt_pct_final:.1f}%) | "
            f"Cost Rs.{entry * qty:,.0f}"
        )

        # ── Place or simulate the order ───────────────────────────
        estimated_entry = entry  # Save pre-fill price for proportional SL/target scaling
        if self.cfg.DRY_RUN:
            self._dry_run_counter += 1
            order_id = f"DRY_RUN_{self._dry_run_counter:04d}"
            tag = f"\033[96m[DRY RUN]\033[0m"
            self.log.info(
                f"{tag} {side} {qty}x {symbol} @ Rs.{entry:.2f} | "
                f"SL: Rs.{sl:.2f} | Target: Rs.{target:.2f} | "
                f"Cost: Rs.{cost:,.0f}"
            )
        elif self._order_api_broken:
            self.log.error(
                f"Skipping {symbol}: Zerodha order API is broken "
                f"({self._consecutive_order_failures} consecutive failures)"
            )
            return False
        else:
            try:
                order_id = self._place_entry_order(symbol, exchange, qty, side, entry)
                # Order succeeded — reset failure counter
                self._consecutive_order_failures = 0

                # Reconcile actual filled qty (LIMIT orders can partially fill)
                actual_qty = self.zerodha.get_order_filled_qty(order_id)
                if actual_qty and actual_qty != qty:
                    self.log.warning(
                        f"Qty reconciliation: {symbol} planned {qty} shares "
                        f"→ actual {actual_qty} (LIMIT partial fill)"
                    )
                    qty = actual_qty
                    trade["qty"] = qty
                    cost = entry * qty

                # Fetch actual fill price from Zerodha
                # Short timeout since _place_entry_order already waited for fill
                fill_price = self.zerodha.get_order_fill_price(order_id, timeout=3)
                if fill_price:
                    deviation = abs(fill_price - entry) / entry if entry > 0 else 0
                    if deviation > 0.05:
                        self.log.warning(
                            f"Fill price differs: {symbol} estimated Rs.{entry:.2f} "
                            f"→ actual Rs.{fill_price:.2f} ({deviation*100:.1f}% off) "
                            f"— using actual fill (Zerodha is source of truth)"
                        )
                    else:
                        self.log.success(
                            f"Fill confirmed: Order {order_id} | "
                            f"Avg price: Rs.{fill_price:.2f}"
                        )
                    # Always use the actual Zerodha fill price
                    entry = fill_price
                    cost = entry * qty
                    # Scale SL/target proportionally around new fill price.
                    # This preserves wider-of merge, MAX cap, and late-entry
                    # adjustments already applied above.
                    if estimated_entry > 0:
                        ratio = fill_price / estimated_entry
                        sl     = round(sl * ratio, 2)
                        target = round(target * ratio, 2)
                        # Re-validate SL cap after scaling (scaling can push SL beyond MAX_INTRADAY_SL_PCT)
                        max_sl_pct = self.cfg.MAX_INTRADAY_SL_PCT
                        actual_sl_pct = abs(sl - entry) / entry * 100 if entry > 0 else 0
                        if actual_sl_pct > max_sl_pct:
                            rr_mult = self.cfg.RR_TARGET_RATIO
                            if side == 'BUY':
                                sl = round(entry * (1 - max_sl_pct / 100), 2)
                                target = round(entry * (1 + max_sl_pct * rr_mult / 100), 2)
                            else:
                                sl = round(entry * (1 + max_sl_pct / 100), 2)
                                target = round(entry * (1 - max_sl_pct * rr_mult / 100), 2)
                            self.log.info(
                                f"SL re-capped after fill scaling: {actual_sl_pct:.1f}% → {max_sl_pct}%"
                            )
                        self.log.info(
                            f"SL/Target scaled to fill: SL Rs.{sl:.2f} | Target Rs.{target:.2f}"
                        )
                else:
                    self.log.warning(
                        f"ORDER PLACED but fill price unknown: {side} {qty}x {symbol} @ Rs.{entry:.2f} | "
                        f"Order ID: {order_id} — using estimated price"
                    )
            except Exception as e:
                self._consecutive_order_failures += 1
                self.log.error(
                    f"Order FAILED for {symbol}: {e} "
                    f"(failure {self._consecutive_order_failures}/{self.ORDER_FAILURE_LIMIT})"
                )
                self._log_action("ORDER_FAILED", symbol, side, qty, entry, str(e))
                if self._consecutive_order_failures >= self.ORDER_FAILURE_LIMIT:
                    self._order_api_broken = True
                    self.log.error(
                        f"ORDER API BROKEN: {self.ORDER_FAILURE_LIMIT} consecutive "
                        f"failures — halting all new orders. Will close open "
                        f"positions and shut down."
                    )
                return False

        # ── Track the position ────────────────────────────────────
        position = {
            "symbol":       symbol,
            "exchange":     exchange,
            "side":         side,
            "qty":          qty,
            "entry_price":  entry,
            "stop_loss":    sl,
            "target_price": target,
            "exit_price":   None,
            "exit_reason":  None,
            "status":       "OPEN",
            "pnl":          0.0,
            "entry_time":   now.strftime("%H:%M:%S"),
            "exit_time":    None,
            "rationale":    rationale,
            "order_id":     order_id,
            # Indicator snapshot for learning database
            "_entry_score": trade.get("_entry_score"),
            "_entry_rsi":   trade.get("_entry_rsi"),
            "_entry_time":  now.strftime("%H:%M:%S"),
            "_indicator_snapshot": trade.get("_indicator_snapshot"),
            # Late-entry flag — prevents time-decay from stacking
            "_late_entry_reduced": trade.get("_late_entry_reduced", False),
            # Store initial SL at entry for correct trailing risk calculation
            "initial_sl": sl,
        }

        # ── Place SL-M order on exchange for instant SL execution ─
        # Exchange SL-M triggers instantly at trigger_price — no polling delay.
        # Software monitoring (check_stops_and_targets) is the fallback;
        # it handles targets, trailing, and time-decay — things SL-M can't do.
        use_exchange_sl = self.cfg.USE_EXCHANGE_SL
        if use_exchange_sl and not self.cfg.DRY_RUN and hasattr(self.zerodha, 'place_sl_m_order'):
            sl_side = "SELL" if side == "BUY" else "BUY"
            try:
                sl_order_id = self.zerodha.place_sl_m_order(
                    symbol=symbol, exchange=exchange,
                    qty=qty, side=sl_side,
                    trigger_price=sl,
                )
                # BUG FIX: Explicit handling and tracking of SL-M placement
                if sl_order_id:
                    position["_sl_order_id"] = sl_order_id
                    self._pending_order_ids.add(sl_order_id)  # Track for cleanup at market close
                    self.log.info(
                        f"Exchange SL-M placed for {symbol}: {sl_side} {qty}x "
                        f"trigger Rs.{sl:.2f} | ID: {sl_order_id}"
                    )
                else:
                    position["_sl_order_id"] = None
                    self.log.warning(
                        f"SL-M placement returned None for {symbol} — "
                        f"software SL monitoring will handle stop-loss"
                    )
            except Exception as e:
                # BUG FIX: Explicit error handling for SL-M placement failures
                position["_sl_order_id"] = None
                self.log.warning(
                    f"SL-M placement exception for {symbol}: {e} — "
                    f"software SL monitoring will handle stop-loss"
                )

        self.positions.append(position)
        self._log_action("ENTRY", symbol, side, qty, entry, rationale)
        return True

    # ================================================================
    # EXIT — CLOSE A POSITION
    # ================================================================

    def exit_position(
        self,
        position: dict,
        exit_price: float,
        reason: str,
    ):
        """
        Closes an open position at the given price.

        reason is one of: "STOP_LOSS", "TARGET_HIT", "REVIEW_EXIT",
        "SQUARE_OFF", "CIRCUIT_BREAKER"

        In dry-run mode: logs the exit. P&L calculated from entry/exit prices.
        In live mode: places a counter-order (BUY→SELL or SELL→BUY).
        """
        symbol   = position["symbol"]
        exchange = position["exchange"]
        side     = position["side"]
        qty      = position["qty"]
        entry    = position["entry_price"]
        now      = now_ist()

        # Apply exit slippage in dry-run mode (adverse fill)
        if self.cfg.DRY_RUN and self.cfg.SLIPPAGE_PCT > 0:
            slip_pct = self._adjusted_slippage(now.hour)
            slip = exit_price * slip_pct / 100
            if side == "BUY":
                exit_price = round(exit_price - slip, 2)   # sell fill slightly lower
            else:
                exit_price = round(exit_price + slip, 2)   # cover fill slightly higher

        # Calculate P&L
        if side == "BUY":
            pnl = (exit_price - entry) * qty
            exit_side = "SELL"
        else:  # SELL (short)
            pnl = (entry - exit_price) * qty
            exit_side = "BUY"

        # Place exit order (or simulate)
        sl_order_id = position.get("_sl_order_id")
        sl_m_handled = False

        # ── Validate pending order tracking ────────────────────────
        # BUG FIX (Apr 9 2026): Ensure pending_order_ids consistency.
        # If position has _sl_order_id but it's not in pending set,
        # log warning (possible orphan from earlier failed discard).
        if sl_order_id and sl_order_id not in self._pending_order_ids:
            self.log.warning(
                f"Orphan pending ID detected: {symbol} has _sl_order_id {sl_order_id} "
                f"but not in pending set. Position may have been exited already or "
                f"had a failed discard. Continuing with exit."
            )

        # ── Handle exchange SL-M order ────────────────────────────
        if sl_order_id and not self.cfg.DRY_RUN:
            if reason == "STOP_LOSS":
                # SL-M on exchange should have triggered. Verify fill qty
                # matches position qty — SL-M can partially fill if
                # insufficient liquidity at trigger price.
                sl_filled_qty = qty  # default: assume full fill
                try:
                    sl_filled_qty = self.zerodha.get_order_filled_qty(sl_order_id) or qty
                except Exception:
                    pass  # API unavailable — assume full fill

                if sl_filled_qty >= qty:
                    self.log.info(
                        f"SL-M {sl_order_id} triggered for {symbol} — "
                        f"full fill confirmed ({sl_filled_qty} shares)"
                    )
                else:
                    # Partial fill — place market exit for remaining shares
                    remaining = qty - sl_filled_qty
                    self.log.warning(
                        f"SL-M {sl_order_id} PARTIAL fill: {sl_filled_qty}/{qty} shares. "
                        f"Placing MARKET exit for remaining {remaining} shares."
                    )
                    try:
                        self.zerodha.place_order(
                            symbol=symbol, exchange=exchange,
                            qty=remaining, side=exit_side, order_type="MARKET",
                        )
                    except Exception as e:
                        self.log.error(
                            f"FAILED to exit remaining {remaining} shares of {symbol}: {e} — "
                            f"MANUAL INTERVENTION NEEDED"
                        )
                self._pending_order_ids.discard(sl_order_id)
                position["_sl_order_id"] = None
                sl_m_handled = True
            else:
                # Non-SL exit (target, review, square-off) — cancel
                # the pending SL-M before placing our own exit order.
                self.log.info(
                    f"Cancelling SL-M {sl_order_id} before {reason} exit for {symbol}"
                )
                try:
                    self.zerodha.cancel_order(sl_order_id)
                except Exception as e:
                    self.log.warning(
                        f"Failed to cancel SL-M {sl_order_id} for {symbol}: {e} — "
                        f"proceeding with {reason} exit (exchange SL-M may still be active)"
                    )
                self._pending_order_ids.discard(sl_order_id)  # Remove from tracking
                position["_sl_order_id"] = None

        if self.cfg.DRY_RUN:
            tag = f"\033[96m[DRY RUN]\033[0m"
            pnl_color = "\033[92m" if pnl >= 0 else "\033[91m"
            self.log.info(
                f"{tag} EXIT {exit_side} {qty}x {symbol} @ Rs.{exit_price:.2f} | "
                f"Reason: {reason} | "
                f"P&L: {pnl_color}Rs.{pnl:+,.2f}\033[0m"
            )
        elif sl_m_handled:
            # Exchange SL-M already filled — no order to place
            pnl_color = "\033[92m" if pnl >= 0 else "\033[91m"
            self.log.info(
                f"Exchange SL exit for {symbol}: {pnl_color}Rs.{pnl:+,.2f}\033[0m"
            )
        else:
            try:
                exit_order_id = self.zerodha.place_order(
                    symbol=symbol, exchange=exchange,
                    qty=qty, side=exit_side, order_type="MARKET",
                )
                # Exit order succeeded — reset failure counter
                self._consecutive_order_failures = 0

                # Fetch actual fill price from Zerodha
                fill_price = self.zerodha.get_order_fill_price(exit_order_id)
                if fill_price:
                    deviation = abs(fill_price - exit_price) / exit_price if exit_price > 0 else 0
                    if deviation > 0.05:
                        self.log.warning(
                            f"Exit fill differs: {symbol} estimated Rs.{exit_price:.2f} "
                            f"→ actual Rs.{fill_price:.2f} ({deviation*100:.1f}% off) "
                            f"— using actual fill"
                        )
                    else:
                        self.log.success(
                            f"EXIT FILLED: {exit_side} {qty}x {symbol} | "
                            f"Estimated: Rs.{exit_price:.2f} → Actual: Rs.{fill_price:.2f} | "
                            f"Reason: {reason}"
                        )
                    # Always use the actual Zerodha fill price
                    exit_price = fill_price
                else:
                    self.log.warning(
                        f"EXIT placed but fill price unknown: {exit_side} {qty}x {symbol} @ Rs.{exit_price:.2f} | "
                        f"Reason: {reason} — using estimated price"
                    )
                # Recalculate P&L with actual fill prices
                if side == "BUY":
                    pnl = (exit_price - entry) * qty
                else:
                    pnl = (entry - exit_price) * qty
                pnl_color = "\033[92m" if pnl >= 0 else "\033[91m"
                self.log.info(
                    f"Actual P&L for {symbol}: {pnl_color}Rs.{pnl:+,.2f}\033[0m"
                )
            except Exception as e:
                self._consecutive_order_failures += 1
                self.log.error(
                    f"Exit order FAILED for {symbol}: {e} — "
                    f"MANUAL INTERVENTION NEEDED "
                    f"(failure {self._consecutive_order_failures}/{self.ORDER_FAILURE_LIMIT})"
                )
                if self._consecutive_order_failures >= self.ORDER_FAILURE_LIMIT:
                    self._order_api_broken = True
                    self.log.error(
                        f"ORDER API BROKEN: {self.ORDER_FAILURE_LIMIT} consecutive "
                        f"failures — Zerodha API may be down. "
                        f"Open positions may need manual closure."
                    )
                # Don't mark as CLOSED — the position is still open on Zerodha.
                # Resume feature will pick it up on next restart.
                self._log_action("EXIT_FAILED", symbol, exit_side, qty, exit_price, str(e))
                return

        # Update position record
        position.update(
            exit_price  = round(exit_price, 2),
            exit_reason = reason,
            status      = "CLOSED",
            pnl         = round(pnl, 2),
            exit_time   = now.strftime("%H:%M:%S"),
        )
        
        # Track that bot closed this position — prevents sync_external_positions()
        # from misidentifying it as a user close
        self._bot_closed_positions.add((symbol, side, qty))
        
        self._log_action("EXIT", symbol, exit_side, qty, exit_price, reason)

        # Track consecutive SL hits for whipsaw guard
        if reason == "STOP_LOSS":
            self.record_sl_hit()
        elif pnl > 0:
            self.record_profitable_close()

    # ================================================================
    # PARTIAL EXIT — EXIT SUBSET OF SHARES
    # ================================================================

    def _place_exit_order(
        self,
        position: dict,
        price: float,
        qty: int,
        reason: str,
    ) -> float | None:
        """
        Exits a subset of shares from an open position (for partial
        profit taking). Does NOT mark the position as CLOSED — the
        remaining shares stay open. Updates the trade log.

        Returns the actual fill price on success, None on failure.
        """
        symbol   = position["symbol"]
        exchange = position["exchange"]
        side     = position["side"]
        exit_side = "SELL" if side == "BUY" else "BUY"

        if self.cfg.DRY_RUN:
            tag = f"\033[96m[DRY RUN]\033[0m"
            self.log.info(
                f"{tag} PARTIAL EXIT {exit_side} {qty}x {symbol} @ Rs.{price:.2f} | "
                f"Reason: {reason}"
            )
        else:
            try:
                order_id = self.zerodha.place_order(
                    symbol=symbol, exchange=exchange,
                    qty=qty, side=exit_side, order_type="MARKET",
                )
                self._consecutive_order_failures = 0

                # Fetch actual fill price (same pattern as exit_position)
                fill_price = self.zerodha.get_order_fill_price(order_id)
                if fill_price:
                    price = fill_price
            except Exception as e:
                self._consecutive_order_failures += 1
                self.log.error(
                    f"Partial exit order FAILED for {symbol}: {e} — "
                    f"position qty NOT adjusted"
                )
                if self._consecutive_order_failures >= self.ORDER_FAILURE_LIMIT:
                    self._order_api_broken = True
                return None

        self._log_action(reason, symbol, exit_side, qty, price,
                         f"Partial exit {qty} shares")
        return price

    # ================================================================
    # MONITOR — CHECK SL/TARGET HITS
    # ================================================================

    def check_stops_and_targets(self, quotes: dict) -> int:
        """
        Checks all open positions against live prices.
        Auto-exits any position where stop-loss or target is hit.
        Also applies auto trailing stop-loss for winning positions.

        This is the rule-based monitoring loop — no Claude API calls.
        Called every PRICE_POLL_SECONDS.

        Returns the number of positions that were closed.
        """
        closed = 0

        for pos in self.open_positions():
            key = f"{pos['exchange']}:{pos['symbol']}"
            q   = quotes.get(key, {})
            current_price = q.get("last_price", 0)

            if current_price <= 0:
                continue

            symbol = pos["symbol"]
            side   = pos["side"]
            sl     = pos["stop_loss"]
            target = pos["target_price"]
            entry  = pos["entry_price"]
            qty    = pos["qty"]

            # Apply time-decay to targets after configured hour
            self._adjust_target_for_time(pos)
            target = pos["target_price"]  # re-read after possible adjustment

            # Calculate unrealised P&L and distances
            if side == "BUY":
                unrealised   = (current_price - entry) * qty
                sl_distance  = (current_price - sl) / current_price * 100
                tgt_distance = (target - current_price) / current_price * 100
            else:
                unrealised   = (entry - current_price) * qty
                sl_distance  = (sl - current_price) / current_price * 100
                tgt_distance = (current_price - target) / current_price * 100

            # ── Stop-loss check ───────────────────────────────────
            if side == "BUY" and current_price <= sl:
                loss = (sl - entry) * qty
                self.log.warning(
                    f"STOP-LOSS HIT: {symbol} {side} | entry Rs.{entry:.2f} → "
                    f"Rs.{current_price:.2f} (SL: Rs.{sl:.2f}) | "
                    f"Loss: Rs.{loss:,.2f} on {qty} shares"
                )
                exit_price = sl if self.cfg.DRY_RUN else current_price
                self.exit_position(pos, exit_price, "STOP_LOSS")
                closed += 1

            elif side == "SELL" and current_price >= sl:
                loss = (entry - sl) * qty
                self.log.warning(
                    f"STOP-LOSS HIT: {symbol} {side} | entry Rs.{entry:.2f} → "
                    f"Rs.{current_price:.2f} (SL: Rs.{sl:.2f}) | "
                    f"Loss: Rs.{loss:,.2f} on {qty} shares"
                )
                exit_price = sl if self.cfg.DRY_RUN else current_price
                self.exit_position(pos, exit_price, "STOP_LOSS")
                closed += 1

            # ── Target check ─────────────────────────────────────
            elif side == "BUY" and current_price >= target:
                profit = (target - entry) * qty
                self.log.success(
                    f"TARGET HIT: {symbol} {side} | entry Rs.{entry:.2f} → "
                    f"Rs.{current_price:.2f} (Target: Rs.{target:.2f}) | "
                    f"Profit: Rs.{profit:,.2f} on {qty} shares"
                )
                exit_price = target if self.cfg.DRY_RUN else current_price
                self.exit_position(pos, exit_price, "TARGET_HIT")
                closed += 1

            elif side == "SELL" and current_price <= target:
                profit = (entry - target) * qty
                self.log.success(
                    f"TARGET HIT: {symbol} {side} | entry Rs.{entry:.2f} → "
                    f"Rs.{current_price:.2f} (Target: Rs.{target:.2f}) | "
                    f"Profit: Rs.{profit:,.2f} on {qty} shares"
                )
                exit_price = target if self.cfg.DRY_RUN else current_price
                self.exit_position(pos, exit_price, "TARGET_HIT")
                closed += 1

            # ── Auto trailing stop-loss (only for open, winning positions) ──
            else:
                self._auto_trail_stop(pos, current_price)

        return closed

    def _auto_trail_stop(self, pos: dict, current_price: float):
        """
        Rule-based trailing stop-loss with partial profit taking.

        Formula example (BUY):
          Entry 100, SL 97 → initial_risk = 3
          At +1.5R (price 104.50): partial exit 1/3 qty, start trailing
          New SL = entry + (profit × TRAIL_STEP_PCT%)
                 = 100 + (4.50 × 0.50) = 102.25 (locks 50% of open profit)

        Rules:
          1. Trigger: profit >= initial_risk × TRAIL_AFTER_RISK_MULTIPLE (1.5)
          2. Partial exit: sell 1/3 qty at current price (once only, min 3 shares)
          3. Trail: SL = entry + TRAIL_STEP_PCT% of unrealised profit
          4. SL ratchets — only moves in favorable direction, never loosens
        """
        entry  = pos["entry_price"]
        sl     = pos["stop_loss"]
        side   = pos["side"]
        symbol = pos["symbol"]

        # Store initial SL on first call (so trailing calc always knows the original risk)
        if "initial_sl" not in pos:
            pos["initial_sl"] = sl

        initial_risk = abs(entry - pos["initial_sl"])
        if initial_risk <= 0:
            return  # no risk defined, can't trail

        trail_after = self.cfg.TRAIL_AFTER_RISK_MULTIPLE
        trail_pct   = self.cfg.TRAIL_STEP_PCT / 100

        if side == "BUY":
            profit = current_price - entry
            if profit < initial_risk * trail_after:
                return  # not enough profit to start trailing

            # ── Partial profit taking (once, at first trail trigger) ──
            if not pos.get("_partial_taken") and pos["qty"] >= 3:
                partial_qty = max(1, pos["qty"] // 3)  # exit 1/3, keep 2/3 running
                remaining_qty = pos["qty"] - partial_qty
                partial_pnl = round((current_price - entry) * partial_qty, 2)

                self.log.success(
                    f"PARTIAL PROFIT: {symbol} — exiting {partial_qty} of "
                    f"{pos['qty']} shares @ Rs.{current_price:.2f} "
                    f"(locking Rs.{partial_pnl:,.2f} profit)"
                )

                # Place the partial exit order
                fill = self._place_exit_order(pos, current_price, partial_qty, "PARTIAL_PROFIT")
                if fill is not None:
                    # Recalculate P&L with actual fill price
                    partial_pnl = round((fill - entry) * partial_qty, 2)
                    pos["qty"] = remaining_qty
                    pos["_partial_taken"] = True
                    pos["_partial_pnl"] = round(pos.get("_partial_pnl", 0) + partial_pnl, 2)
                    pos["_partial_qty"] = pos.get("_partial_qty", 0) + partial_qty
                    pos["_partial_exit_price"] = fill
                    # Update exchange SL-M for reduced qty
                    self._replace_exchange_sl(pos, pos["stop_loss"])

            # New SL = entry + trail_pct of current profit
            new_sl = round(entry + profit * trail_pct, 2)

            # SL must only move UP (more protective)
            if new_sl > sl:
                pos["stop_loss"] = new_sl
                self._update_exchange_sl(pos, new_sl)
                self.log.info(
                    f"AUTO-TRAIL {symbol}: SL Rs.{sl:.2f} → Rs.{new_sl:.2f} "
                    f"(locking {trail_pct*100:.0f}% of Rs.{profit:.2f} profit)"
                )
                self._log_action("AUTO_TRAIL_SL", symbol, "", 0, new_sl,
                                 f"Auto trailing: profit Rs.{profit:.2f}")

        else:  # SELL (short)
            profit = entry - current_price
            if profit < initial_risk * trail_after:
                return

            # ── Partial profit taking (once, at first trail trigger) ──
            if not pos.get("_partial_taken") and pos["qty"] >= 3:
                partial_qty = max(1, pos["qty"] // 3)  # exit 1/3, keep 2/3 running
                remaining_qty = pos["qty"] - partial_qty
                partial_pnl = round((entry - current_price) * partial_qty, 2)

                self.log.success(
                    f"PARTIAL PROFIT: {symbol} — exiting {partial_qty} of "
                    f"{pos['qty']} shares @ Rs.{current_price:.2f} "
                    f"(locking Rs.{partial_pnl:,.2f} profit)"
                )

                fill = self._place_exit_order(pos, current_price, partial_qty, "PARTIAL_PROFIT")
                if fill is not None:
                    partial_pnl = round((entry - fill) * partial_qty, 2)
                    pos["qty"] = remaining_qty
                    pos["_partial_taken"] = True
                    pos["_partial_pnl"] = round(pos.get("_partial_pnl", 0) + partial_pnl, 2)
                    pos["_partial_qty"] = pos.get("_partial_qty", 0) + partial_qty
                    pos["_partial_exit_price"] = fill
                    # Update exchange SL-M for reduced qty
                    self._replace_exchange_sl(pos, pos["stop_loss"])

            new_sl = round(entry - profit * trail_pct, 2)

            # SL must only move DOWN for shorts (more protective)
            if new_sl < sl:
                pos["stop_loss"] = new_sl
                self._update_exchange_sl(pos, new_sl)
                self.log.info(
                    f"AUTO-TRAIL {symbol}: SL Rs.{sl:.2f} → Rs.{new_sl:.2f} "
                    f"(locking {trail_pct*100:.0f}% of Rs.{profit:.2f} profit)"
                )
                self._log_action("AUTO_TRAIL_SL", symbol, "", 0, new_sl,
                                 f"Auto trailing: profit Rs.{profit:.2f}")

    def _update_exchange_sl(self, pos: dict, new_trigger: float):
        """Modify the exchange SL-M order trigger price when trailing."""
        sl_order_id = pos.get("_sl_order_id")
        if not sl_order_id or self.cfg.DRY_RUN:
            return
        if not hasattr(self.zerodha, 'modify_order'):
            return
        try:
            ok = self.zerodha.modify_order(
                sl_order_id, trigger_price=new_trigger,
                symbol=pos["symbol"], exchange=pos["exchange"],
            )
            if not ok:
                self.log.warning(
                    f"Could not update exchange SL-M for {pos['symbol']} "
                    f"(order {sl_order_id}) — software SL still active"
                )
        except Exception as e:
            self.log.warning(
                f"Exception updating SL-M for {pos['symbol']}: {e} — "
                f"software SL still active"
            )

    def _replace_exchange_sl(self, pos: dict, trigger_price: float):
        """Cancel old SL-M and place new one with current qty (after partial exit)."""
        sl_order_id = pos.get("_sl_order_id")
        if not sl_order_id or self.cfg.DRY_RUN:
            return
        if not hasattr(self.zerodha, 'place_sl_m_order'):
            return
        # Cancel old
        try:
            self.zerodha.cancel_order(sl_order_id)
        except Exception as e:
            self.log.warning(
                f"Failed to cancel old SL-M {sl_order_id} for {pos['symbol']}: {e} — "
                f"skipping SL-M replacement (software SL still active)"
            )
            return
        self._pending_order_ids.discard(sl_order_id)
        pos["_sl_order_id"] = None
        # Place new with reduced qty
        sl_side = "SELL" if pos["side"] == "BUY" else "BUY"
        try:
            new_id = self.zerodha.place_sl_m_order(
                symbol=pos["symbol"], exchange=pos["exchange"],
                qty=pos["qty"], side=sl_side,
                trigger_price=trigger_price,
            )
            if new_id:
                pos["_sl_order_id"] = new_id
                self._pending_order_ids.add(new_id)
        except Exception as e:
            self.log.warning(
                f"Failed to place replacement SL-M for {pos['symbol']}: {e} — "
                f"software SL still active"
            )

    # ================================================================
    # TIME-DECAY TARGET ADJUSTMENT
    # ================================================================

    def _adjust_target_for_time(self, pos: dict):
        """
        After TARGET_DECAY_AFTER_HOUR, reduce a position's target by
        TARGET_DECAY_PCT% of the entry-to-target distance. Only applied
        once per position (stores the original target in 'original_target').
        """
        now = now_ist()
        if now.hour < self.cfg.TARGET_DECAY_AFTER_HOUR:
            return

        # Already adjusted — don't decay again
        if "original_target" in pos:
            return

        # Skip if late-entry reduction was already applied at entry —
        # stacking both reductions makes the R:R unviable.
        if pos.get("_late_entry_reduced"):
            return

        entry  = pos["entry_price"]
        target = pos["target_price"]
        side   = pos["side"]
        decay  = self.cfg.TARGET_DECAY_PCT / 100

        pos["original_target"] = target

        if side == "BUY":
            distance = target - entry
            new_target = round(entry + distance * (1 - decay), 2)
        else:
            distance = entry - target
            new_target = round(entry - distance * (1 - decay), 2)

        pos["target_price"] = new_target
        self.log.info(
            f"TIME-DECAY: {pos['symbol']} target Rs.{target:.2f} → Rs.{new_target:.2f} "
            f"(-{self.cfg.TARGET_DECAY_PCT:.0f}% after {self.cfg.TARGET_DECAY_AFTER_HOUR}:00)"
        )
        self._log_action("TIME_DECAY_TARGET", pos["symbol"], "", 0, new_target,
                         f"Original target: Rs.{target:.2f}")

    # ================================================================
    # STAGNANT POSITION EXIT (NoAI)
    # ================================================================

    def check_stagnant_positions(self, quotes: dict) -> int:
        """
        Exits positions that have been open for STAGNANT_EXIT_MINUTES
        without moving at least STAGNANT_EXIT_MIN_MOVE_PCT toward
        their target. Frees slots for better trades.

        Only useful in NoAI mode (Claude reviews handle this in V1/V2).

        Returns the number of positions closed.
        """
        stagnant_mins = self.cfg.STAGNANT_EXIT_MINUTES
        # On expiry days (fewer positions), extend timer to reduce churn
        if getattr(self.cfg, '_expiry_applied', False):
            stagnant_mins += self.cfg.EXPIRY_STAGNANT_EXTRA_MINUTES
        # Midday lull (12:00-1:30) — positions aren't dead, just in
        # low-liquidity period. Extend timer by 15 min to avoid
        # false stagnant exits during the lull.
        now_hour = now_ist().hour
        now_min = now_ist().minute
        if now_hour == 12 or (now_hour == 13 and now_min <= 30):
            stagnant_mins += 15
        min_move_pct  = self.cfg.STAGNANT_EXIT_MIN_MOVE_PCT
        if stagnant_mins <= 0:
            return 0

        now = now_ist()
        closed = 0

        for pos in self.open_positions():
            # Parse entry time
            entry_time_str = pos.get("entry_time", "")
            if not entry_time_str:
                continue
            try:
                entry_time = datetime.datetime.strptime(
                    f"{now.strftime('%Y-%m-%d')} {entry_time_str}",
                    "%Y-%m-%d %H:%M:%S",
                )
            except ValueError:
                continue

            elapsed = (now - entry_time).total_seconds() / 60
            if elapsed < stagnant_mins:
                continue

            # Get current price
            key = f"{pos['exchange']}:{pos['symbol']}"
            q = quotes.get(key, {})
            current_price = q.get("last_price", 0)
            if current_price <= 0:
                continue

            entry  = pos["entry_price"]
            side   = pos["side"]

            # Calculate favourable move from entry (as % of entry price)
            if side == "BUY":
                move_pct = (current_price - entry) / entry * 100
            else:
                move_pct = (entry - current_price) / entry * 100

            # If barely moved (or moved against us), exit
            if move_pct < min_move_pct:
                pnl = (current_price - entry) * pos["qty"] if side == "BUY" \
                    else (entry - current_price) * pos["qty"]
                self.log.warning(
                    f"STAGNANT EXIT: {pos['symbol']} {side} — open {elapsed:.0f} min, "
                    f"moved only {move_pct:+.2f}% (need {min_move_pct}%) | "
                    f"P&L: Rs.{pnl:+,.2f}"
                )
                self.exit_position(pos, current_price, "STAGNANT_EXIT")
                self._stagnant_exits.add(f"{pos['symbol']}_{side}")
                closed += 1

        return closed

    # ================================================================
    # LATE-DAY LOSER EXIT
    # ================================================================

    def check_loser_exit(self, quotes: dict) -> int:
        """
        After LOSER_EXIT_HOUR:MINUTE, auto-exit losing positions
        and tighten SL on breakeven positions. Prevents holding losers
        into the illiquid closing minutes.

        Returns the number of positions exited.
        """
        exit_hour = self.cfg.LOSER_EXIT_HOUR
        exit_min  = self.cfg.LOSER_EXIT_MINUTE
        now = now_ist()
        exit_time = now.replace(hour=exit_hour, minute=exit_min, second=0, microsecond=0)
        if now < exit_time:
            return 0

        closed = 0
        for pos in self.open_positions():
            key = f"{pos['exchange']}:{pos['symbol']}"
            q = quotes.get(key, {})
            current_price = q.get("last_price", 0)
            if current_price <= 0:
                continue

            entry = pos["entry_price"]
            side  = pos["side"]
            pnl   = (current_price - entry) * pos["qty"] if side == "BUY" \
                else (entry - current_price) * pos["qty"]

            if pnl < 0:
                self.log.warning(
                    f"LOSER EXIT: {pos['symbol']} {side} — losing Rs.{pnl:+,.2f}, "
                    f"exiting before close"
                )
                self.exit_position(pos, current_price, "LOSER_EXIT")
                closed += 1
            elif abs(pnl) < entry * pos["qty"] * 0.001:
                # Near breakeven — tighten SL to entry ± 0.1%
                if side == "BUY":
                    tight_sl = round(entry * 0.999, 2)
                    if tight_sl > pos["stop_loss"]:
                        pos["stop_loss"] = tight_sl
                        self._update_exchange_sl(pos, tight_sl)
                        self.log.info(
                            f"LOSER TIGHTEN: {pos['symbol']} — SL → Rs.{tight_sl:.2f} (breakeven protect)"
                        )
                else:
                    tight_sl = round(entry * 1.001, 2)
                    if tight_sl < pos["stop_loss"]:
                        pos["stop_loss"] = tight_sl
                        self._update_exchange_sl(pos, tight_sl)
                        self.log.info(
                            f"LOSER TIGHTEN: {pos['symbol']} — SL → Rs.{tight_sl:.2f} (breakeven protect)"
                        )

        return closed

    # ================================================================
    # LOSS-ADJUSTED BUDGET
    # ================================================================

    def loss_adjusted_budget(self) -> float:
        """
        Returns effective budget reduced by realised losses.
        Prevents full-size re-entry after SL hits.

        Live mode: Zerodha's refresh_budget() already reflects margin.
        Dry-run mode: this is the only way to reduce budget after losses.
        """
        if not self.cfg.LOSS_SIZING_ENABLED:
            return self._budget

        day_loss = self.day_pnl()
        if day_loss >= 0:
            return self._budget

        # Reduce budget by realised losses (floor at 20% of original budget)
        adjusted = self._budget + day_loss  # day_loss is negative
        min_budget = self._budget * 0.2
        return max(adjusted, min_budget)

    # ================================================================
    # APPLY CLAUDE REVIEW ACTIONS
    # ================================================================

    def apply_review_actions(self, actions: list[dict], quotes: dict):
        """
        Applies recommendations from StockScanner.review_positions().
        Handles: EXIT, ADJUST_SL, ADJUST_TARGET, HOLD, NEW trades.
        """
        for action in actions:
            act    = action.get("action", "").upper()
            symbol = action.get("symbol", "")
            reason = action.get("reason", "no reason given")

            if act == "EXIT":
                pos = self._find_open_position(symbol)
                if pos:
                    key = f"{pos['exchange']}:{symbol}"
                    price = quotes.get(key, {}).get("last_price", pos["entry_price"])
                    pnl_est = (
                        (price - pos["entry_price"]) * pos["qty"]
                        if pos["side"] == "BUY"
                        else (pos["entry_price"] - price) * pos["qty"]
                    )
                    self.log.info(
                        f"CLAUDE REVIEW → EXIT {symbol}: {reason} | "
                        f"Current Rs.{price:.2f}, Est P&L Rs.{pnl_est:+,.2f}"
                    )
                    self.exit_position(pos, price, "REVIEW_EXIT")
                else:
                    self.log.warning(f"Claude said EXIT {symbol} but no open position found")

            elif act == "ADJUST_SL" and action.get("new_sl"):
                pos = self._find_open_position(symbol)
                if pos:
                    old_sl = pos["stop_loss"]
                    new_sl = action["new_sl"]
                    entry  = pos["entry_price"]
                    side   = pos["side"]

                    # Validate direction: SL must be on correct side of current price
                    key_sl = f"{pos['exchange']}:{symbol}"
                    current_for_sl = quotes.get(key_sl, {}).get("last_price", entry)
                    if side == "BUY" and new_sl >= current_for_sl:
                        self.log.warning(
                            f"Rejected SL adjustment for {symbol}: "
                            f"SL Rs.{new_sl:.2f} >= current Rs.{current_for_sl:.2f} (above market for BUY)"
                        )
                        continue
                    if side == "SELL" and new_sl <= current_for_sl:
                        self.log.warning(
                            f"Rejected SL adjustment for {symbol}: "
                            f"SL Rs.{new_sl:.2f} <= current Rs.{current_for_sl:.2f} (below market for SELL)"
                        )
                        continue

                    # Cap SL width at MAX_INTRADAY_SL_PCT
                    max_sl_pct = self.cfg.MAX_INTRADAY_SL_PCT
                    sl_dist_pct = abs(new_sl - entry) / entry * 100
                    if sl_dist_pct > max_sl_pct:
                        if side == "BUY":
                            new_sl = round(entry * (1 - max_sl_pct / 100), 2)
                        else:
                            new_sl = round(entry * (1 + max_sl_pct / 100), 2)
                        self.log.warning(
                            f"Claude SL for {symbol} capped: {sl_dist_pct:.1f}% "
                            f"→ {max_sl_pct}% (Rs.{new_sl:.2f})"
                        )

                    # Only allow tightening (SL moves toward entry, not away)
                    if side == "BUY" and new_sl < old_sl:
                        self.log.warning(
                            f"Rejected SL loosening for {symbol}: "
                            f"Rs.{old_sl:.2f} → Rs.{new_sl:.2f} (would widen risk)"
                        )
                        continue
                    if side == "SELL" and new_sl > old_sl:
                        self.log.warning(
                            f"Rejected SL loosening for {symbol}: "
                            f"Rs.{old_sl:.2f} → Rs.{new_sl:.2f} (would widen risk)"
                        )
                        continue

                    pos["stop_loss"] = new_sl
                    self._update_exchange_sl(pos, new_sl)
                    self.log.info(
                        f"CLAUDE REVIEW → ADJUST SL {symbol}: "
                        f"Rs.{old_sl:.2f} → Rs.{new_sl:.2f} | {reason}"
                    )
                    self._log_action("ADJUST_SL", symbol, "", 0, new_sl,
                                     reason)

            elif act == "ADJUST_TARGET" and action.get("new_target"):
                pos = self._find_open_position(symbol)
                if pos:
                    new_target = action["new_target"]
                    entry = pos["entry_price"]
                    side = pos["side"]
                    old_tgt = pos["target_price"]

                    # Validate target is on correct side of entry
                    if side == "BUY" and new_target <= entry:
                        self.log.warning(
                            f"Rejected target adjustment for {symbol}: "
                            f"target Rs.{new_target:.2f} <= entry Rs.{entry:.2f} (wrong side for BUY)"
                        )
                        continue
                    if side == "SELL" and new_target >= entry:
                        self.log.warning(
                            f"Rejected target adjustment for {symbol}: "
                            f"target Rs.{new_target:.2f} >= entry Rs.{entry:.2f} (wrong side for SELL)"
                        )
                        continue

                    pos["target_price"] = new_target
                    self.log.info(
                        f"CLAUDE REVIEW → ADJUST TARGET {symbol}: "
                        f"Rs.{old_tgt:.2f} → Rs.{new_target:.2f} | {reason}"
                    )
                    self._log_action("ADJUST_TARGET", symbol, "", 0, new_target,
                                     reason)

            elif act == "NEW":
                self.log.info(
                    f"CLAUDE REVIEW → NEW TRADE: {action.get('side', '?')} "
                    f"{action.get('symbol', '?')} | {reason}"
                )
                self.enter_trade(action)

            elif act == "HOLD":
                self.log.info(f"CLAUDE REVIEW → HOLD {symbol}: {reason}")

    # ================================================================
    # SQUARE OFF — END OF DAY
    # ================================================================

    def square_off_all(self, quotes: dict):
        """
        Closes ALL open positions at current market prices.
        Called at SQUARE_OFF time or on graceful shutdown.

        This is a safety mechanism — intraday positions MUST be
        closed before 3:20 PM or Zerodha auto-squares with penalty.
        """
        # BUG FIX: Cancel all pending SL-M orders first to prevent stale triggers
        self.cancel_all_pending_orders()
        
        open_pos = self.open_positions()
        if not open_pos:
            self.log.info("No open positions to square off")
            return

        self.log.section("SQUARE OFF — Closing all open positions")

        closed_count = 0
        for pos in open_pos:
            key = f"{pos['exchange']}:{pos['symbol']}"
            q   = quotes.get(key, {})
            current_price = q.get("last_price", pos["entry_price"])
            try:
                self.exit_position(pos, current_price, "SQUARE_OFF")
                if pos["status"] == "CLOSED":
                    closed_count += 1
            except Exception as e:
                self.log.error(
                    f"Failed to square off {pos['symbol']}: {e} — "
                    f"position may still be open on Zerodha!"
                )

        if closed_count == len(open_pos):
            self.log.success(f"Squared off all {closed_count} positions")
        else:
            self.log.error(
                f"Squared off {closed_count}/{len(open_pos)} positions — "
                f"{len(open_pos) - closed_count} may still be open on Zerodha!"
            )

    # ================================================================
    # CIRCUIT BREAKER — MAX DAILY LOSS
    # ================================================================

    def check_circuit_breaker(self) -> bool:
        """
        Returns True if total daily loss exceeds MAX_LOSS_PER_DAY_PCT.
        When triggered, all positions should be closed and no new
        trades should be entered for the rest of the day.

        Disabled if MAX_LOSS_PER_DAY_PCT is set to 0 in config.
        """
        max_loss_pct = self.cfg.MAX_LOSS_PER_DAY_PCT
        if max_loss_pct <= 0:
            return False

        budget   = self._budget
        max_loss = budget * max_loss_pct / 100
        pnl_since_baseline = self.day_pnl() - self._cb_pnl_baseline

        if pnl_since_baseline < -max_loss:
            self.log.error(
                f"CIRCUIT BREAKER: P&L since baseline Rs.{pnl_since_baseline:,.2f} "
                f"(day total Rs.{self.day_pnl():,.2f}) exceeds max loss of "
                f"Rs.{max_loss:,.0f} ({max_loss_pct}% of budget). "
                f"Stopping all trading."
            )
            return True
        return False

    def reset_circuit_breaker_baseline(self):
        """After cooldown, reset so the breaker only trips on NEW losses."""
        self._cb_pnl_baseline = self.day_pnl()
        self._cb_trip_count += 1

    def circuit_breaker_trips_exhausted(self) -> bool:
        """True if max CB trips reached — no more cooldowns allowed."""
        max_trips = self.cfg.MAX_CIRCUIT_BREAKER_TRIPS
        return max_trips > 0 and self._cb_trip_count >= max_trips

    # ================================================================
    # CONSECUTIVE SL TRACKING
    # ================================================================

    def record_sl_hit(self):
        """Called after a stop-loss exit. Increments consecutive SL counter."""
        self._consecutive_sl_count += 1
        limit = self.cfg.CONSECUTIVE_SL_PAUSE_COUNT
        if limit > 0 and self._consecutive_sl_count >= limit:
            pause_min = self.cfg.CONSECUTIVE_SL_PAUSE_MINUTES
            self._sl_pause_until = time.time() + pause_min * 60
            self.log.warning(
                f"WHIPSAW GUARD: {self._consecutive_sl_count} consecutive SL hits — "
                f"pausing new entries for {pause_min} min"
            )

    def record_profitable_close(self):
        """Called after a profitable exit. Resets consecutive SL counter."""
        self._consecutive_sl_count = 0

    def is_sl_paused(self) -> bool:
        """True if in a whipsaw pause (consecutive SL hits triggered a cooldown)."""
        if self._sl_pause_until <= 0:
            return False
        if time.time() >= self._sl_pause_until:
            self._sl_pause_until = 0.0
            self._consecutive_sl_count = 0
            return False
        return True

    # ================================================================
    # P&L AND COST CALCULATIONS
    # ================================================================

    def day_pnl(self) -> float:
        """Total P&L from all closed + partial-booked positions today (before charges)."""
        closed_pnl = sum(
            p["pnl"] + p.get("_partial_pnl", 0)
            for p in self.positions if p["status"] == "CLOSED"
        )
        # Include partial profits already booked on still-OPEN positions
        open_partial = sum(
            p.get("_partial_pnl", 0)
            for p in self.positions if p["status"] == "OPEN"
        )
        return closed_pnl + open_partial

    def unrealised_pnl(self, quotes: dict) -> float:
        """Unrealised P&L from open positions at current prices, including partial exits."""
        total = 0.0
        for pos in self.open_positions():
            key = f"{pos['exchange']}:{pos['symbol']}"
            q   = quotes.get(key, {})
            current = q.get("last_price", pos["entry_price"])
            if pos["side"] == "BUY":
                total += (current - pos["entry_price"]) * pos["qty"]
            else:
                total += (pos["entry_price"] - current) * pos["qty"]
            total += pos.get("_partial_pnl", 0)
        return round(total, 2)

    def calculate_charges(self) -> dict:
        """
        Calculates all Zerodha charges, taxes, and fees for the day's
        trades. Delegates to Config.calculate_charges().
        """
        closed = [p for p in self.positions if p["status"] == "CLOSED"]

        total_buy_turnover  = 0.0
        total_sell_turnover = 0.0
        num_orders          = 0

        for p in closed:
            partial_qty = p.get("_partial_qty", 0)
            full_qty = p["qty"] + partial_qty
            entry_value = p["entry_price"] * full_qty
            exit_value  = p["exit_price"]  * p["qty"]
            partial_exit_value = p.get("_partial_exit_price", p["entry_price"]) * partial_qty

            if p["side"] == "BUY":
                total_buy_turnover  += entry_value
                total_sell_turnover += exit_value + partial_exit_value
            else:
                total_sell_turnover += entry_value
                total_buy_turnover  += exit_value + partial_exit_value

            num_orders += 2 + (1 if partial_qty > 0 else 0)

        return self.cfg.calculate_charges(
            total_buy_turnover, total_sell_turnover,
            num_orders, self.claude_calls,
        )

    def net_profit(self) -> dict:
        """
        Returns the full P&L summary including all charges and
        estimated income tax liability on speculative business income.
        """
        gross_pnl = self.day_pnl()
        charges   = self.calculate_charges()

        # Net profit = gross P&L minus per-trade charges and Claude API cost.
        # Zerodha monthly subscription is NOT subtracted here — it's FYI.
        net = gross_pnl - charges["total_costs"]

        # Estimated tax liability (only on positive net profit)
        tax_rate = Config.TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100) / 100
        estimated_tax = round(net * tax_rate, 2) if net > 0 else 0.0
        profit_after_tax = round(net - estimated_tax, 2)

        return {
            "gross_pnl":         round(gross_pnl, 2),
            "charges":           charges,
            "net_profit":        round(net, 2),
            "is_profitable":     net > 0,
            "tax_rate_pct":      round(Config.TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100), 2),
            "estimated_tax":     estimated_tax,
            "profit_after_tax":  profit_after_tax,
        }

    # ================================================================
    # TRIGGER ORDER REFRESH — MID-POSITION SL ADJUSTMENT
    # ================================================================

    def refresh_trigger(self, pos: dict, new_trigger: float) -> bool:
        """
        Mid-position trigger refresh: cancel old SL-M, place new SL-M.
        Used when trigger hasn't met condition and we want to adjust level
        (e.g., time-decay SL, candle breakout adjustment) WITHOUT closing
        or losing the position slot.

        This is useful when an SL-M hasn't triggered by late morning and we
        want to refresh the trigger price without exiting the position.

        Args:
            pos: position dict with _sl_order_id
            new_trigger: new trigger price for SL-M

        Returns: True on success, False on persistent failure.
        """
        sl_order_id = pos.get("_sl_order_id")
        if not sl_order_id or self.cfg.DRY_RUN:
            return False

        symbol = pos["symbol"]
        exchange = pos["exchange"]

        # Retry cancel up to 3 times with 500ms backoff
        for attempt in range(3):
            try:
                self.zerodha.cancel_order(sl_order_id)
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(0.5)  # 500ms backoff
                    continue
                else:
                    self.log.error(
                        f"Cancel failed after 3 retries on {symbol} SL-M {sl_order_id}: {e}"
                    )
                    return False

        # Place new SL-M at refreshed trigger price
        try:
            new_id = self.zerodha.place_sl_m_order(
                symbol=symbol, exchange=exchange,
                qty=pos["qty"], side="SELL" if pos["side"] == "BUY" else "BUY",
                trigger_price=new_trigger,
            )
            if new_id:
                pos["_sl_order_id"] = new_id
                self._pending_order_ids.discard(sl_order_id)
                self._pending_order_ids.add(new_id)
                self.log.info(
                    f"Trigger REFRESHED for {symbol}: old {sl_order_id} → new {new_id} @ Rs.{new_trigger:.2f}"
                )
                return True
            else:
                self.log.error(f"Failed to place new SL-M for {symbol} after cancel")
                return False
        except Exception as e:
            self.log.error(f"Refresh failed for {symbol}: {e}")
            return False

    # ================================================================
    # PENDING ORDER CLEANUP — BUG FIX FOR STALE TRIGGERS
    # ================================================================

    def cancel_all_pending_orders(self) -> int:
        """
        Cancels all pending SL-M orders sitting on the exchange.
        Called at market close to prevent stale trigger orders from
        lingering and then executing unexpectedly next trading day.

        BUG FIX (Apr 9 2026): Some SL-M orders with triggers (e.g. BUY ETERNAL @ 242.49)
        were not executed and not cancelled, leaving them as stale pending orders.
        This function explicitly cancels them before square-off.

        Returns the number of orders cancelled.
        """
        if self.cfg.DRY_RUN or not self._pending_order_ids:
            return 0

        cancelled = 0
        for order_id in list(self._pending_order_ids):
            try:
                self.zerodha.cancel_order(order_id)
                self.log.info(f"Cancelled pending order {order_id}")
                self._pending_order_ids.discard(order_id)
                cancelled += 1
            except Exception as e:
                self.log.warning(
                    f"Failed to cancel pending order {order_id}: {e} — "
                    f"may need manual cancellation"
                )

        if cancelled > 0:
            self.log.success(
                f"Cancelled {cancelled} pending order(s) to clean up stale triggers"
            )

        return cancelled

    # ================================================================
    # POSITION QUERIES
    # ================================================================

    def open_positions(self) -> list[dict]:
        """Returns all currently open positions."""
        return [p for p in self.positions if p["status"] == "OPEN"]

    def closed_positions(self) -> list[dict]:
        """Returns all closed positions."""
        return [p for p in self.positions if p["status"] == "CLOSED"]

    def budget_remaining(self) -> float:
        """How much of the budget is not currently allocated (loss-adjusted)."""
        return self.loss_adjusted_budget() - self._total_open_exposure()

    def print_position_status(self, quotes: dict):
        """
        Prints a detailed per-position status table showing current price,
        P&L, distance to SL and target. Called periodically from the
        monitor loop to give visibility into what the bot is doing.
        """
        open_pos = self.open_positions()
        if not open_pos:
            return

        # Clear the in-place status line before printing the table
        print(f"\r{' ' * 100}\r")
        self.log.info(f"{'─'*80}")
        self.log.info(f"  {'SYMBOL':<12} {'SIDE':<5} {'ENTRY':>8} {'CURRENT':>8} "
                       f"{'P&L':>10} {'SL':>8} {'SL%':>6} {'TGT':>8} {'TGT%':>6}")
        self.log.info(f"  {'─'*12} {'─'*5} {'─'*8} {'─'*8} {'─'*10} {'─'*8} {'─'*6} {'─'*8} {'─'*6}")

        for pos in open_pos:
            key = f"{pos['exchange']}:{pos['symbol']}"
            q   = quotes.get(key, {})
            current = q.get("last_price", 0)
            if current <= 0:
                continue

            entry  = pos["entry_price"]
            sl     = pos["stop_loss"]
            target = pos["target_price"]
            side   = pos["side"]
            qty    = pos["qty"]

            if side == "BUY":
                pnl          = (current - entry) * qty
                sl_dist_pct  = (current - sl) / current * 100
                tgt_dist_pct = (target - current) / current * 100
            else:
                pnl          = (entry - current) * qty
                sl_dist_pct  = (sl - current) / current * 100
                tgt_dist_pct = (current - target) / current * 100

            pnl_color = "\033[92m" if pnl >= 0 else "\033[91m"
            reset     = "\033[0m"

            self.log.info(
                f"  {pos['symbol']:<12} {side:<5} "
                f"Rs.{entry:>7.2f} Rs.{current:>7.2f} "
                f"{pnl_color}Rs.{pnl:>+9,.2f}{reset} "
                f"Rs.{sl:>7.2f} {sl_dist_pct:>5.1f}% "
                f"Rs.{target:>7.2f} {tgt_dist_pct:>5.1f}%"
            )

        self.log.info(f"{'─'*80}")

    # ================================================================
    # INTERNAL HELPERS
    # ================================================================

    def _total_open_exposure(self) -> float:
        """Total capital locked in open positions."""
        return sum(
            p["entry_price"] * p["qty"]
            for p in self.positions if p["status"] == "OPEN"
        )

    def _adjusted_slippage(self, hour: int) -> float:
        """
        Returns time-of-day-adjusted slippage % for dry-run mode.
        Opening hour has wider spreads (2×), last hour before
        square-off has moderate widening (1.5×).
        """
        base = self.cfg.SLIPPAGE_PCT
        if hour == self.cfg.MARKET_OPEN_HOUR:
            return base * 2.0    # opening volatility
        if hour >= self.cfg.SQUARE_OFF_HOUR - 1:
            return base * 1.5    # last hour — reduced liquidity
        return base

    def _find_open_position(self, symbol: str) -> dict | None:
        """Finds the first open position for a given symbol."""
        for p in self.positions:
            if p["symbol"] == symbol and p["status"] == "OPEN":
                return p
        return None

    def _log_action(
        self,
        action: str,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        detail: str = "",
    ):
        """Records an action in the chronological trade log."""
        self.trade_log.append({
            "time":   now_ist().strftime("%H:%M:%S"),
            "action": action,
            "symbol": symbol,
            "side":   side,
            "qty":    qty,
            "price":  round(price, 2) if isinstance(price, (int, float)) else price,
            "detail": detail,
        })

    # ================================================================
    # END-OF-DAY RECONCILIATION WITH ZERODHA
    # ================================================================

    def reconcile_with_zerodha(self) -> int:
        """
        Fetches today's actual positions from Zerodha and compares
        with our internal tracking. Corrects entry/exit prices and
        P&L where they differ.

        Called after square-off, before report generation, so the
        report and DB have Zerodha's actual numbers.

        Returns the number of positions that were corrected.
        """
        if self.cfg.DRY_RUN:
            return 0

        self.log.section("RECONCILIATION — Verifying against Zerodha")

        try:
            zerodha_positions = self.zerodha.get_todays_positions()
        except Exception as e:
            self.log.warning(f"Zerodha position fetch failed: {e} — skipping reconciliation")
            return 0
        if not zerodha_positions:
            self.log.warning("No position data from Zerodha — skipping reconciliation")
            return 0

        # Build lookup: symbol → Zerodha position data
        # Only MIS (intraday) positions
        z_lookup: dict[str, dict] = {}
        for zp in zerodha_positions:
            if zp.get("product") != "MIS":
                continue
            sym = zp.get("tradingsymbol", "")
            if sym:
                z_lookup[sym] = zp

        if not z_lookup:
            self.log.info("No MIS positions found on Zerodha for today")
            return 0

        corrected = 0

        for pos in self.positions:
            if pos["status"] != "CLOSED":
                continue

            symbol = pos["symbol"]
            zp = z_lookup.get(symbol)
            if not zp:
                continue

            # Zerodha day position fields:
            #   buy_quantity, sell_quantity, buy_price, sell_price,
            #   quantity (net, 0 if squared off), pnl, realised
            z_buy_qty    = zp.get("buy_quantity", 0)
            z_sell_qty   = zp.get("sell_quantity", 0)
            z_buy_price  = zp.get("buy_price", 0)
            z_sell_price = zp.get("sell_price", 0)
            z_pnl        = zp.get("pnl", 0)

            # Determine Zerodha's entry/exit based on our trade side
            if pos["side"] == "BUY":
                z_entry = z_buy_price
                z_exit  = z_sell_price
                z_qty   = z_buy_qty
            else:  # SELL (short)
                z_entry = z_sell_price
                z_exit  = z_buy_price
                z_qty   = z_sell_qty

            if z_entry <= 0 or z_exit <= 0:
                continue

            # Skip price correction for partial-exit trades — Zerodha's
            # averaged sell_price can't be split between partial and final exits.
            if pos.get("_partial_qty", 0) > 0:
                expected_total = pos["qty"] + pos.get("_partial_qty", 0)
                if z_qty != expected_total:
                    self.log.warning(
                        f"QTY MISMATCH {symbol}: engine {expected_total} vs Zerodha {z_qty}"
                    )
                else:
                    self.log.success(f"{symbol}: ✓ quantities match (partial exit trade)")
                continue

            # Compare and correct
            changes = []
            old_entry = pos["entry_price"]
            old_exit  = pos["exit_price"]
            old_pnl   = pos["pnl"]

            if abs(z_entry - old_entry) > 0.01:
                changes.append(f"entry Rs.{old_entry:.2f}→Rs.{z_entry:.2f}")
                pos["entry_price"] = round(z_entry, 2)

            if old_exit is not None and abs(z_exit - old_exit) > 0.01:
                changes.append(f"exit Rs.{old_exit:.2f}→Rs.{z_exit:.2f}")
                pos["exit_price"] = round(z_exit, 2)

            # Recalculate P&L from corrected prices
            if pos["side"] == "BUY":
                new_pnl = (pos["exit_price"] - pos["entry_price"]) * pos["qty"]
            else:
                new_pnl = (pos["entry_price"] - pos["exit_price"]) * pos["qty"]
            new_pnl = round(new_pnl, 2)

            if abs(new_pnl - old_pnl) > 0.01:
                changes.append(f"P&L Rs.{old_pnl:+,.2f}→Rs.{new_pnl:+,.2f}")
                pos["pnl"] = new_pnl

            if changes:
                corrected += 1
                self.log.warning(
                    f"CORRECTED {symbol}: {' | '.join(changes)}"
                )
                self._log_action(
                    "RECONCILE", symbol, pos["side"], pos["qty"],
                    pos["entry_price"],
                    f"Zerodha correction: {' | '.join(changes)}",
                )
            else:
                self.log.success(f"{symbol}: ✓ matches Zerodha")

        if corrected:
            self.log.warning(f"Reconciliation: {corrected} position(s) corrected")
        else:
            self.log.success("Reconciliation: all positions match Zerodha ✓")

        return corrected
