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
                self.log.success(
                    f"ORDER PLACED: {side} {qty}x {symbol} @ ₹{entry:.2f} | "
                    f"Order ID: {order_id}"
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
                self.zerodha.place_order(
                    symbol=symbol, exchange=exchange,
                    qty=qty, side=exit_side, order_type="MARKET",
                )
                self.log.success(
                    f"EXIT ORDER: {exit_side} {qty}x {symbol} @ ₹{exit_price:.2f} | "
                    f"Reason: {reason} | P&L: ₹{pnl:+,.2f}"
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

            side   = pos["side"]
            sl     = pos["stop_loss"]
            target = pos["target_price"]

            # ── Stop-loss check ───────────────────────────────────
            if side == "BUY" and current_price <= sl:
                self.log.warning(
                    f"STOP-LOSS HIT: {pos['symbol']} dropped to "
                    f"₹{current_price:.2f} (SL: ₹{sl:.2f})"
                )
                exit_price = sl if self.cfg.DRY_RUN else current_price
                self.exit_position(pos, exit_price, "STOP_LOSS")
                closed += 1

            elif side == "SELL" and current_price >= sl:
                self.log.warning(
                    f"STOP-LOSS HIT: {pos['symbol']} rose to "
                    f"₹{current_price:.2f} (SL: ₹{sl:.2f})"
                )
                exit_price = sl if self.cfg.DRY_RUN else current_price
                self.exit_position(pos, exit_price, "STOP_LOSS")
                closed += 1

            # ── Target check ─────────────────────────────────────
            elif side == "BUY" and current_price >= target:
                self.log.success(
                    f"TARGET HIT: {pos['symbol']} reached "
                    f"₹{current_price:.2f} (Target: ₹{target:.2f})"
                )
                exit_price = target if self.cfg.DRY_RUN else current_price
                self.exit_position(pos, exit_price, "TARGET_HIT")
                closed += 1

            elif side == "SELL" and current_price <= target:
                self.log.success(
                    f"TARGET HIT: {pos['symbol']} reached "
                    f"₹{current_price:.2f} (Target: ₹{target:.2f})"
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

            if act == "EXIT":
                pos = self._find_open_position(symbol)
                if pos:
                    key = f"{pos['exchange']}:{symbol}"
                    price = quotes.get(key, {}).get("last_price", pos["entry_price"])
                    self.exit_position(pos, price, "REVIEW_EXIT")
                else:
                    self.log.warning(f"Claude said EXIT {symbol} but no open position found")

            elif act == "ADJUST_SL" and action.get("new_sl"):
                pos = self._find_open_position(symbol)
                if pos:
                    old_sl = pos["stop_loss"]
                    pos["stop_loss"] = action["new_sl"]
                    self.log.info(
                        f"SL adjusted for {symbol}: ₹{old_sl:.2f} → ₹{action['new_sl']:.2f}"
                    )
                    self._log_action("ADJUST_SL", symbol, "", 0, action["new_sl"],
                                     action.get("reason", ""))

            elif act == "ADJUST_TARGET" and action.get("new_target"):
                pos = self._find_open_position(symbol)
                if pos:
                    old_tgt = pos["target_price"]
                    pos["target_price"] = action["new_target"]
                    self.log.info(
                        f"Target adjusted for {symbol}: ₹{old_tgt:.2f} → ₹{action['new_target']:.2f}"
                    )
                    self._log_action("ADJUST_TARGET", symbol, "", 0, action["new_target"],
                                     action.get("reason", ""))

            elif act == "NEW":
                # New trade suggestion from Claude review
                self.enter_trade(action)

            elif act == "HOLD":
                self.log.info(f"HOLD {symbol} — {action.get('reason', 'no change')}")

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
        trades. Uses the cost parameters from Config.

        Returns a dict with each charge component and the total.

        Charge breakdown for intraday equity:
          - Brokerage: min(₹20, 0.03% of turnover) per executed order
          - STT: 0.025% on SELL side turnover only
          - Exchange transaction: 0.00297% on total turnover
          - GST: 18% on (brokerage + exchange charges)
          - SEBI charges: ₹10 per crore of turnover
          - Stamp duty: 0.003% on BUY side turnover only
        """
        closed = [p for p in self.positions if p["status"] == "CLOSED"]

        total_buy_turnover  = 0.0
        total_sell_turnover = 0.0
        num_orders          = 0

        for p in closed:
            entry_value = p["entry_price"] * p["qty"]
            exit_value  = p["exit_price"]  * p["qty"]

            if p["side"] == "BUY":
                total_buy_turnover  += entry_value   # buy leg
                total_sell_turnover += exit_value     # sell leg (exit)
            else:  # SELL (short) — sell first, buy later
                total_sell_turnover += entry_value    # sell leg (entry)
                total_buy_turnover  += exit_value     # buy leg (exit)

            num_orders += 2  # entry + exit = 2 orders per round trip

        total_turnover = total_buy_turnover + total_sell_turnover

        # ── Brokerage ─────────────────────────────────────────────
        # Per order: min(₹20 flat, 0.03% of that order's value)
        # Simplified: we use total turnover / num_orders for average
        brokerage_flat  = self.cfg.ZERODHA_BROKERAGE_FLAT * num_orders
        brokerage_pct   = total_turnover * self.cfg.ZERODHA_BROKERAGE_PCT / 100
        brokerage       = min(brokerage_flat, brokerage_pct) if num_orders > 0 else 0

        # ── STT — sell side only for intraday ─────────────────────
        stt = total_sell_turnover * self.cfg.STT_SELL_PCT / 100

        # ── Exchange transaction charges ──────────────────────────
        exchange_txn = total_turnover * self.cfg.EXCHANGE_TXN_PCT / 100

        # ── SEBI charges — ₹10 per crore ─────────────────────────
        sebi = total_turnover / 1e7 * self.cfg.SEBI_CHARGE_PER_CR

        # ── GST — 18% on (brokerage + SEBI charges + exchange charges)
        gst = (brokerage + sebi + exchange_txn) * self.cfg.GST_PCT / 100

        # ── Stamp duty — buy side only ────────────────────────────
        stamp_duty = total_buy_turnover * self.cfg.STAMP_DUTY_BUY_PCT / 100

        total_charges = brokerage + stt + exchange_txn + gst + sebi + stamp_duty

        # ── Claude API cost (per-use, deducted from P&L) ─────────
        claude_cost = self.claude_calls * self.cfg.CLAUDE_COST_PER_CALL

        # ── Zerodha Kite Connect — monthly subscription (FYI only)
        # This is NOT deducted from daily P&L. It's a fixed monthly
        # cost shown in the report for awareness. Over a month you
        # can check if cumulative daily profits cover this cost.
        zerodha_monthly = self.cfg.ZERODHA_MONTHLY_COST

        return {
            "total_turnover":       round(total_turnover, 2),
            "buy_turnover":         round(total_buy_turnover, 2),
            "sell_turnover":        round(total_sell_turnover, 2),
            "num_orders":           num_orders,
            "brokerage":            round(brokerage, 2),
            "stt":                  round(stt, 2),
            "exchange_txn":         round(exchange_txn, 2),
            "gst":                  round(gst, 2),
            "sebi_charges":         round(sebi, 4),
            "stamp_duty":           round(stamp_duty, 2),
            "total_tax_and_charges": round(total_charges, 2),
            "claude_api_cost":      round(claude_cost, 2),
            "total_costs":          round(total_charges + claude_cost, 2),
            "zerodha_monthly_fyi":  zerodha_monthly,
        }

    def net_profit(self) -> dict:
        """
        Returns the full P&L summary including all charges.
        This is what goes into the final report.
        """
        gross_pnl = self.day_pnl()
        charges   = self.calculate_charges()

        # Net profit = gross P&L minus per-trade charges and Claude API cost.
        # Zerodha monthly subscription is NOT subtracted here — it's FYI.
        net = gross_pnl - charges["total_costs"]

        return {
            "gross_pnl":      round(gross_pnl, 2),
            "charges":        charges,
            "net_profit":     round(net, 2),
            "is_profitable":  net > 0,
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
