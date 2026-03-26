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

from config              import Config
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

    def set_budget(self, amount: float):
        """Sets the trading budget (called by PortfolioManager after fetching funds)."""
        self._budget = amount

    # ================================================================
    # ATR CALCULATION
    # ================================================================

    def calculate_atr(self, symbol: str, exchange: str = "NSE", period: int = 0) -> float | None:
        """
        Computes the Average True Range over `period` trading days.
        Returns ATR as a price value, or None if data is unavailable.

        True Range = max(high-low, |high-prev_close|, |low-prev_close|)
        ATR = SMA of True Range over `period` days.
        """
        if period <= 0:
            period = self.cfg.ATR_PERIOD

        to_date   = datetime.date.today()
        from_date = to_date - datetime.timedelta(days=period * 2)  # extra buffer for weekends/holidays

        try:
            candles = self.zerodha.get_historical(symbol, exchange, from_date, to_date, "day")
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
        """
        symbol    = trade["symbol"]
        exchange  = trade.get("exchange", "NSE")
        side      = trade["side"]
        qty       = trade["qty"]
        entry     = trade["entry_price"]
        sl        = trade["stop_loss"]
        target    = trade["target_price"]
        rationale = trade.get("rationale", "")

        now = datetime.datetime.now()

        # ── ATR-based dynamic stop-loss / target ──────────────────
        atr = self.calculate_atr(symbol, exchange)
        if atr and atr > 0:
            multiplier = self.cfg.ATR_MULTIPLIER
            if side == "BUY":
                atr_sl     = round(entry - multiplier * atr, 2)
                atr_target = round(entry + multiplier * 2 * atr, 2)
            else:  # SELL (short)
                atr_sl     = round(entry + multiplier * atr, 2)
                atr_target = round(entry - multiplier * 2 * atr, 2)

            self.log.info(
                f"ATR({self.cfg.ATR_PERIOD}) for {symbol}: ₹{atr:.2f} | "
                f"Dynamic SL: ₹{atr_sl:.2f} | Target: ₹{atr_target:.2f}"
            )
            sl     = atr_sl
            target = atr_target
        else:
            self.log.info(
                f"ATR unavailable for {symbol} — using Claude SL: ₹{sl:.2f} / Target: ₹{target:.2f}"
            )

        # ── Apply slippage in dry-run mode for realism ────────────
        if self.cfg.DRY_RUN and self.cfg.SLIPPAGE_PCT > 0:
            slip = entry * self.cfg.SLIPPAGE_PCT / 100
            if side == "BUY":
                entry = round(entry + slip, 2)   # buy slightly higher
            else:
                entry = round(entry - slip, 2)   # sell slightly lower

        # ── Budget check before entering ──────────────────────────
        cost = entry * qty
        current_exposure = self._total_open_exposure()
        if current_exposure + cost > self._budget:
            # Try reducing qty to fit remaining budget
            remaining = self._budget - current_exposure
            max_qty = int(remaining / entry) if entry > 0 else 0
            if max_qty >= 1:
                self.log.warning(
                    f"{symbol}: {qty}x @ ₹{entry:.2f} = ₹{cost:,.0f} exceeds budget. "
                    f"Reducing qty to {max_qty} (₹{max_qty * entry:,.0f})"
                )
                qty = max_qty
                trade["qty"] = qty
                cost = entry * qty
            else:
                self.log.warning(
                    f"Cannot enter {symbol}: ₹{cost:,.0f} would exceed "
                    f"budget (current exposure: ₹{current_exposure:,.0f}, "
                    f"remaining: ₹{remaining:,.0f})"
                )
                return False

        # ── Max positions check ───────────────────────────────────
        open_count = len([p for p in self.positions if p["status"] == "OPEN"])
        if open_count >= self.cfg.MAX_POSITIONS:
            self.log.warning(
                f"Cannot enter {symbol}: already at max {self.cfg.MAX_POSITIONS} positions"
            )
            return False

        # ── Max re-entries per stock check ────────────────────────
        max_reentries = self.cfg.MAX_REENTRIES_PER_STOCK
        if max_reentries > 0:
            past_entries = sum(
                1 for p in self.positions if p["symbol"] == symbol
            )
            if past_entries >= max_reentries:
                self.log.warning(
                    f"Cannot enter {symbol}: already traded {past_entries} "
                    f"time(s) today (max {max_reentries}). Skipping re-entry."
                )
                return False

        # ── Place or simulate the order ───────────────────────────
        if self.cfg.DRY_RUN:
            self._dry_run_counter += 1
            order_id = f"DRY_RUN_{self._dry_run_counter:04d}"
            tag = f"\033[96m[DRY RUN]\033[0m"
            self.log.info(
                f"{tag} {side} {qty}x {symbol} @ ₹{entry:.2f} | "
                f"SL: ₹{sl:.2f} | Target: ₹{target:.2f} | "
                f"Cost: ₹{cost:,.0f}"
            )
        else:
            try:
                order_id = self.zerodha.place_order(
                    symbol=symbol, exchange=exchange,
                    qty=qty, side=side, order_type="MARKET",
                )
                # Fetch actual fill price from Zerodha
                fill_price = self.zerodha.get_order_fill_price(order_id)
                if fill_price:
                    # Sanity check: reject fills that deviate >5% from estimate
                    deviation = abs(fill_price - entry) / entry if entry > 0 else 0
                    if deviation > 0.05:
                        self.log.error(
                            f"FILL PRICE REJECTED: {symbol} fill ₹{fill_price:.2f} "
                            f"deviates {deviation*100:.1f}% from estimate ₹{entry:.2f} "
                            f"(>{5}% threshold) — using estimated price"
                        )
                    else:
                        self.log.success(
                            f"ORDER FILLED: {side} {qty}x {symbol} | "
                            f"Estimated: ₹{entry:.2f} → Actual: ₹{fill_price:.2f} | "
                            f"Order ID: {order_id}"
                        )
                        entry = fill_price
                        cost = entry * qty
                        # Recalculate ATR-based SL/target around actual fill
                        if atr and atr > 0:
                            multiplier = self.cfg.ATR_MULTIPLIER
                            if side == "BUY":
                                sl     = round(entry - multiplier * atr, 2)
                                target = round(entry + multiplier * 2 * atr, 2)
                            else:
                                sl     = round(entry + multiplier * atr, 2)
                                target = round(entry - multiplier * 2 * atr, 2)
                            self.log.info(
                                f"SL/Target recalculated on fill: SL ₹{sl:.2f} | Target ₹{target:.2f}"
                            )
                else:
                    self.log.warning(
                        f"ORDER PLACED but fill price unknown: {side} {qty}x {symbol} @ ₹{entry:.2f} | "
                        f"Order ID: {order_id} — using estimated price"
                    )
            except Exception as e:
                self.log.error(f"Order FAILED for {symbol}: {e}")
                self._log_action("ORDER_FAILED", symbol, side, qty, entry, str(e))
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
        }
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
        now      = datetime.datetime.now()

        # Calculate P&L
        if side == "BUY":
            pnl = (exit_price - entry) * qty
            exit_side = "SELL"
        else:  # SELL (short)
            pnl = (entry - exit_price) * qty
            exit_side = "BUY"

        # Place exit order (or simulate)
        if self.cfg.DRY_RUN:
            tag = f"\033[96m[DRY RUN]\033[0m"
            pnl_color = "\033[92m" if pnl >= 0 else "\033[91m"
            self.log.info(
                f"{tag} EXIT {exit_side} {qty}x {symbol} @ ₹{exit_price:.2f} | "
                f"Reason: {reason} | "
                f"P&L: {pnl_color}₹{pnl:+,.2f}\033[0m"
            )
        else:
            try:
                exit_order_id = self.zerodha.place_order(
                    symbol=symbol, exchange=exchange,
                    qty=qty, side=exit_side, order_type="MARKET",
                )
                # Fetch actual fill price from Zerodha
                fill_price = self.zerodha.get_order_fill_price(exit_order_id)
                if fill_price:
                    # Sanity check: reject fills that deviate >5% from estimate
                    deviation = abs(fill_price - exit_price) / exit_price if exit_price > 0 else 0
                    if deviation > 0.05:
                        self.log.error(
                            f"EXIT FILL REJECTED: {symbol} fill ₹{fill_price:.2f} "
                            f"deviates {deviation*100:.1f}% from estimate ₹{exit_price:.2f} "
                            f"(>{5}% threshold) — using estimated price"
                        )
                    else:
                        self.log.success(
                            f"EXIT FILLED: {exit_side} {qty}x {symbol} | "
                            f"Estimated: ₹{exit_price:.2f} → Actual: ₹{fill_price:.2f} | "
                            f"Reason: {reason}"
                        )
                        exit_price = fill_price
                else:
                    self.log.warning(
                        f"EXIT placed but fill price unknown: {exit_side} {qty}x {symbol} @ ₹{exit_price:.2f} | "
                        f"Reason: {reason} — using estimated price"
                    )
                # Recalculate P&L with actual fill prices
                if side == "BUY":
                    pnl = (exit_price - entry) * qty
                else:
                    pnl = (entry - exit_price) * qty
                pnl_color = "\033[92m" if pnl >= 0 else "\033[91m"
                self.log.info(
                    f"Actual P&L for {symbol}: {pnl_color}₹{pnl:+,.2f}\033[0m"
                )
            except Exception as e:
                self.log.error(
                    f"Exit order FAILED for {symbol}: {e} — "
                    f"MANUAL INTERVENTION NEEDED"
                )

        # Update position record
        position.update(
            exit_price  = round(exit_price, 2),
            exit_reason = reason,
            status      = "CLOSED",
            pnl         = round(pnl, 2),
            exit_time   = now.strftime("%H:%M:%S"),
        )
        self._log_action("EXIT", symbol, exit_side, qty, exit_price, reason)

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
                    f"STOP-LOSS HIT: {symbol} {side} | entry ₹{entry:.2f} → "
                    f"₹{current_price:.2f} (SL: ₹{sl:.2f}) | "
                    f"Loss: ₹{loss:,.2f} on {qty} shares"
                )
                exit_price = sl if self.cfg.DRY_RUN else current_price
                self.exit_position(pos, exit_price, "STOP_LOSS")
                closed += 1

            elif side == "SELL" and current_price >= sl:
                loss = (entry - sl) * qty
                self.log.warning(
                    f"STOP-LOSS HIT: {symbol} {side} | entry ₹{entry:.2f} → "
                    f"₹{current_price:.2f} (SL: ₹{sl:.2f}) | "
                    f"Loss: ₹{loss:,.2f} on {qty} shares"
                )
                exit_price = sl if self.cfg.DRY_RUN else current_price
                self.exit_position(pos, exit_price, "STOP_LOSS")
                closed += 1

            # ── Target check ─────────────────────────────────────
            elif side == "BUY" and current_price >= target:
                profit = (target - entry) * qty
                self.log.success(
                    f"TARGET HIT: {symbol} {side} | entry ₹{entry:.2f} → "
                    f"₹{current_price:.2f} (Target: ₹{target:.2f}) | "
                    f"Profit: ₹{profit:,.2f} on {qty} shares"
                )
                exit_price = target if self.cfg.DRY_RUN else current_price
                self.exit_position(pos, exit_price, "TARGET_HIT")
                closed += 1

            elif side == "SELL" and current_price <= target:
                profit = (entry - target) * qty
                self.log.success(
                    f"TARGET HIT: {symbol} {side} | entry ₹{entry:.2f} → "
                    f"₹{current_price:.2f} (Target: ₹{target:.2f}) | "
                    f"Profit: ₹{profit:,.2f} on {qty} shares"
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
        Rule-based trailing stop-loss. Runs every price poll (free).

        Logic:
          1. Calculate original risk = |entry - initial SL|
          2. If current profit >= TRAIL_AFTER_RISK_MULTIPLE × risk:
             move SL to at least breakeven (entry price)
          3. Then, SL = entry + TRAIL_STEP_PCT% of unrealised profit
             (for BUY; inverted for SELL)
          4. SL only ever moves in the favorable direction (never down for BUY)
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

            # New SL = entry + trail_pct of current profit
            new_sl = round(entry + profit * trail_pct, 2)

            # SL must only move UP (more protective)
            if new_sl > sl:
                pos["stop_loss"] = new_sl
                self.log.info(
                    f"AUTO-TRAIL {symbol}: SL ₹{sl:.2f} → ₹{new_sl:.2f} "
                    f"(locking {trail_pct*100:.0f}% of ₹{profit:.2f} profit)"
                )
                self._log_action("AUTO_TRAIL_SL", symbol, "", 0, new_sl,
                                 f"Auto trailing: profit ₹{profit:.2f}")

        else:  # SELL (short)
            profit = entry - current_price
            if profit < initial_risk * trail_after:
                return

            new_sl = round(entry - profit * trail_pct, 2)

            # SL must only move DOWN for shorts (more protective)
            if new_sl < sl:
                pos["stop_loss"] = new_sl
                self.log.info(
                    f"AUTO-TRAIL {symbol}: SL ₹{sl:.2f} → ₹{new_sl:.2f} "
                    f"(locking {trail_pct*100:.0f}% of ₹{profit:.2f} profit)"
                )
                self._log_action("AUTO_TRAIL_SL", symbol, "", 0, new_sl,
                                 f"Auto trailing: profit ₹{profit:.2f}")

    # ================================================================
    # TIME-DECAY TARGET ADJUSTMENT
    # ================================================================

    def _adjust_target_for_time(self, pos: dict):
        """
        After TARGET_DECAY_AFTER_HOUR, reduce a position's target by
        TARGET_DECAY_PCT% of the entry-to-target distance. Only applied
        once per position (stores the original target in 'original_target').
        """
        now = datetime.datetime.now()
        if now.hour < self.cfg.TARGET_DECAY_AFTER_HOUR:
            return

        # Already adjusted — don't decay again
        if "original_target" in pos:
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
            f"TIME-DECAY: {pos['symbol']} target ₹{target:.2f} → ₹{new_target:.2f} "
            f"(-{self.cfg.TARGET_DECAY_PCT:.0f}% after {self.cfg.TARGET_DECAY_AFTER_HOUR}:00)"
        )
        self._log_action("TIME_DECAY_TARGET", pos["symbol"], "", 0, new_target,
                         f"Original target: ₹{target:.2f}")

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
                        f"Current ₹{price:.2f}, Est P&L ₹{pnl_est:+,.2f}"
                    )
                    self.exit_position(pos, price, "REVIEW_EXIT")
                else:
                    self.log.warning(f"Claude said EXIT {symbol} but no open position found")

            elif act == "ADJUST_SL" and action.get("new_sl"):
                pos = self._find_open_position(symbol)
                if pos:
                    old_sl = pos["stop_loss"]
                    pos["stop_loss"] = action["new_sl"]
                    self.log.info(
                        f"CLAUDE REVIEW → ADJUST SL {symbol}: "
                        f"₹{old_sl:.2f} → ₹{action['new_sl']:.2f} | {reason}"
                    )
                    self._log_action("ADJUST_SL", symbol, "", 0, action["new_sl"],
                                     reason)

            elif act == "ADJUST_TARGET" and action.get("new_target"):
                pos = self._find_open_position(symbol)
                if pos:
                    old_tgt = pos["target_price"]
                    pos["target_price"] = action["new_target"]
                    self.log.info(
                        f"CLAUDE REVIEW → ADJUST TARGET {symbol}: "
                        f"₹{old_tgt:.2f} → ₹{action['new_target']:.2f} | {reason}"
                    )
                    self._log_action("ADJUST_TARGET", symbol, "", 0, action["new_target"],
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
        open_pos = self.open_positions()
        if not open_pos:
            self.log.info("No open positions to square off")
            return

        self.log.section("SQUARE OFF — Closing all open positions")

        for pos in open_pos:
            key = f"{pos['exchange']}:{pos['symbol']}"
            q   = quotes.get(key, {})
            current_price = q.get("last_price", pos["entry_price"])
            self.exit_position(pos, current_price, "SQUARE_OFF")

        self.log.success(f"Squared off {len(open_pos)} positions")

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
        day_pnl  = self.day_pnl()

        if day_pnl < -max_loss:
            self.log.error(
                f"CIRCUIT BREAKER: Day P&L ₹{day_pnl:,.2f} exceeds "
                f"max loss of ₹{max_loss:,.0f} ({max_loss_pct}% of budget). "
                f"Stopping all trading."
            )
            return True
        return False

    # ================================================================
    # P&L AND COST CALCULATIONS
    # ================================================================

    def day_pnl(self) -> float:
        """Total P&L from all closed positions today (before charges)."""
        return sum(p["pnl"] for p in self.positions if p["status"] == "CLOSED")

    def unrealised_pnl(self, quotes: dict) -> float:
        """Unrealised P&L from open positions at current prices."""
        total = 0.0
        for pos in self.open_positions():
            key = f"{pos['exchange']}:{pos['symbol']}"
            q   = quotes.get(key, {})
            current = q.get("last_price", pos["entry_price"])
            if pos["side"] == "BUY":
                total += (current - pos["entry_price"]) * pos["qty"]
            else:
                total += (pos["entry_price"] - current) * pos["qty"]
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
            entry_value = p["entry_price"] * p["qty"]
            exit_value  = p["exit_price"]  * p["qty"]

            if p["side"] == "BUY":
                total_buy_turnover  += entry_value
                total_sell_turnover += exit_value
            else:
                total_sell_turnover += entry_value
                total_buy_turnover  += exit_value

            num_orders += 2

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
    # POSITION QUERIES
    # ================================================================

    def open_positions(self) -> list[dict]:
        """Returns all currently open positions."""
        return [p for p in self.positions if p["status"] == "OPEN"]

    def closed_positions(self) -> list[dict]:
        """Returns all closed positions."""
        return [p for p in self.positions if p["status"] == "CLOSED"]

    def budget_remaining(self) -> float:
        """How much of the budget is not currently allocated."""
        return self._budget - self._total_open_exposure()

    def print_position_status(self, quotes: dict):
        """
        Prints a detailed per-position status table showing current price,
        P&L, distance to SL and target. Called periodically from the
        monitor loop to give visibility into what the bot is doing.
        """
        open_pos = self.open_positions()
        if not open_pos:
            return

        print()  # newline before table
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
                f"₹{entry:>7.2f} ₹{current:>7.2f} "
                f"{pnl_color}₹{pnl:>+9,.2f}{reset} "
                f"₹{sl:>7.2f} {sl_dist_pct:>5.1f}% "
                f"₹{target:>7.2f} {tgt_dist_pct:>5.1f}%"
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
            "time":   datetime.datetime.now().strftime("%H:%M:%S"),
            "action": action,
            "symbol": symbol,
            "side":   side,
            "qty":    qty,
            "price":  round(price, 2) if isinstance(price, (int, float)) else price,
            "detail": detail,
        })
