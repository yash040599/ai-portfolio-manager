# ================================================================
# modes/options/order_engine.py
# ================================================================
# Option order execution, position tracking, and P&L calculation.
#
# Phase O-4 scope: BUY ONLY. Naked selling is hard-blocked.
# DRY_RUN mode: orders logged, not sent to Zerodha.
#
# Position lifecycle:
#   PENDING → OPEN → CLOSED (via SL, TARGET, SQUARE_OFF, REVIEW)
#
# Safety:
#   - Naked sell hard block (always on)
#   - Circuit breaker (daily loss cap)
#   - Max lots cap
#   - Square-off at OPTIONS_SQUARE_OFF_HOUR
# ================================================================


from config              import Config, now_ist
from core.logger         import Logger
from core.zerodha_client import ZerodhaClient


class OptionsOrderEngine:
    """Manages option order execution, position tracking, and P&L."""

    def __init__(self, config: type[Config], zerodha: ZerodhaClient, log: Logger):
        self.cfg = config
        self.zerodha = zerodha
        self.log = log

        # ── State ─────────────────────────────────────────────────
        self._positions: list[dict] = []       # All positions (open + closed)
        self._budget: float = 0.0
        self._dry_run_counter: int = 0
        self._circuit_broken: bool = False

    # ================================================================
    # BUDGET
    # ================================================================

    def set_budget(self, amount: float):
        self._budget = amount

    # ================================================================
    # NAKED SELL HARD BLOCK
    # ================================================================

    @staticmethod
    def _is_naked_sell(side: str, protection_symbol: str | None) -> bool:
        """Check if this is a naked sell (no protection leg)."""
        return side.upper() == "SELL" and not protection_symbol

    # ================================================================
    # ENTER TRADE
    # ================================================================

    def enter_trade(self, candidate: dict) -> bool:
        """
        Place an option BUY order (or log in dry-run).

        Args:
            candidate: dict from OptionScanner.scan()

        Returns:
            True if order placed/logged, False if rejected.
        """
        # ── Safety: hard-block naked selling ──────────────────────
        side = candidate.get("side", "BUY")
        if self._is_naked_sell(side, candidate.get("protection_symbol")):
            self.log.error(
                "HARD BLOCK: Naked option selling is FORBIDDEN. "
                "Every sold option must have a protection leg. "
                "Order rejected."
            )
            return False

        # ── Safety: Phase O-4 = buy only ──────────────────────────
        if side.upper() != "BUY":
            self.log.warning(
                "Options Phase O-4: only BUY orders allowed. "
                f"Rejected {side} order for {candidate.get('symbol')}."
            )
            return False

        # ── Safety: circuit breaker ───────────────────────────────
        if self._circuit_broken:
            self.log.warning(
                "Circuit breaker tripped — no new entries allowed."
            )
            return False

        # ── Safety: max lots for the day ──────────────────────────
        open_lots = sum(
            p.get("lots", 1) for p in self._positions
            if p["status"] == "OPEN"
        )
        if open_lots >= self.cfg.OPTIONS_MAX_LOTS:
            self.log.info(
                f"Max lots reached ({open_lots}/{self.cfg.OPTIONS_MAX_LOTS}) — skip."
            )
            return False

        # ── Safety: budget check ──────────────────────────────────
        cost = candidate["cost"]
        if cost > self._budget:
            self.log.info(
                f"Cost Rs.{cost:,.0f} exceeds remaining budget "
                f"Rs.{self._budget:,.0f} — skip."
            )
            return False

        symbol = candidate["symbol"]
        qty    = candidate["qty"]
        premium = candidate["premium"]

        # ── Place order or dry-run log ────────────────────────────
        if self.cfg.OPTIONS_DRY_RUN:
            self._dry_run_counter += 1
            order_id = f"OPT_DRY_{self._dry_run_counter:04d}"
            self.log.info(
                f"[DRY RUN] BUY {qty}x {symbol} @ Rs.{premium:.2f} "
                f"| Cost Rs.{cost:,.0f} | OrderID: {order_id}"
            )
        else:
            try:
                order_id = self.zerodha.place_option_order(
                    symbol=symbol,
                    exchange="NFO",
                    qty=qty,
                    side="BUY",
                    order_type="MARKET",
                )
                self.log.success(
                    f"BUY {qty}x {symbol} @ Rs.{premium:.2f} "
                    f"| Cost Rs.{cost:,.0f} | OrderID: {order_id}"
                )
            except Exception as e:
                self.log.error(f"Order failed for {symbol}: {e}")
                return False

        # ── Record position ───────────────────────────────────────
        position = {
            "symbol":       symbol,
            "exchange":     "NFO",
            "side":         "BUY",
            "option_type":  candidate["option_type"],
            "strike":       candidate["strike"],
            "expiry":       candidate["expiry"],
            "lot_size":     candidate["lot_size"],
            "lots":         candidate["lots"],
            "qty":          qty,
            "entry_premium": premium,
            "current_premium": premium,
            "stop_loss":    candidate["stop_loss"],
            "target":       candidate["target"],
            "exit_premium":  None,
            "exit_reason":   None,
            "status":       "OPEN",
            "pnl":          0.0,
            "cost":         cost,
            "entry_time":   now_ist().strftime("%H:%M:%S"),
            "exit_time":    None,
            "order_id":     order_id,
            "nifty_price":  candidate["nifty_price"],
            "nifty_trend":  candidate["nifty_trend"],
            "india_vix":    candidate["india_vix"],
            "rationale":    candidate["rationale"],
            "regime":       candidate["regime"],
        }
        self._positions.append(position)
        self._budget -= cost

        return True

    # ================================================================
    # PRICE UPDATE / SL-TARGET CHECK
    # ================================================================

    def update_premiums(self, live_quotes: dict):
        """
        Update current premiums for open positions from live quotes.
        Triggers SL/target exits automatically.

        Args:
            live_quotes: dict mapping "NFO:SYMBOL" → quote dict
        """
        for pos in self._positions:
            if pos["status"] != "OPEN":
                continue

            key = f"NFO:{pos['symbol']}"
            q = live_quotes.get(key, {})
            current = q.get("last_price", 0)
            if current <= 0:
                continue

            pos["current_premium"] = current

            # ── SL check (premium dropped below SL) ──────────────
            if current <= pos["stop_loss"]:
                self._exit_position(pos, current, "SL")
                continue

            # ── Target check (premium rose above target) ──────────
            if current >= pos["target"]:
                self._exit_position(pos, current, "TARGET")
                continue

    # ================================================================
    # EXIT POSITION
    # ================================================================

    def _exit_position(self, pos: dict, exit_premium: float, reason: str):
        """Close an open position."""
        symbol = pos["symbol"]
        qty    = pos["qty"]

        if not self.cfg.OPTIONS_DRY_RUN:
            try:
                self.zerodha.place_option_order(
                    symbol=symbol,
                    exchange="NFO",
                    qty=qty,
                    side="SELL",
                    order_type="MARKET",
                )
            except Exception as e:
                self.log.error(f"Exit order failed for {symbol}: {e}")
                return

        # ── Calculate P&L ─────────────────────────────────────────
        pnl = (exit_premium - pos["entry_premium"]) * qty
        pos["exit_premium"] = exit_premium
        pos["exit_reason"]  = reason
        pos["exit_time"]    = now_ist().strftime("%H:%M:%S")
        pos["status"]       = "CLOSED"
        pos["pnl"]          = round(pnl, 2)

        prefix = "[DRY RUN] " if self.cfg.OPTIONS_DRY_RUN else ""
        result_icon = "✓" if pnl >= 0 else "✗"
        self.log.info(
            f"{prefix}{result_icon} EXIT ({reason}): SELL {qty}x {symbol} "
            f"@ Rs.{exit_premium:.2f} | P&L: Rs.{pnl:+,.2f}"
        )

        # ── Check circuit breaker ─────────────────────────────────
        day_pnl = self.day_pnl()
        max_loss = self._budget * (self.cfg.OPTIONS_MAX_LOSS_PER_DAY_PCT / 100)
        if day_pnl < 0 and abs(day_pnl) > max_loss:
            self._circuit_broken = True
            self.log.warning(
                f"⚠ CIRCUIT BREAKER: Day P&L Rs.{day_pnl:+,.2f} exceeds "
                f"max loss Rs.{max_loss:,.0f} — no new entries."
            )

    def exit_by_symbol(self, symbol: str, reason: str = "REVIEW"):
        """Exit a specific open position by symbol."""
        for pos in self._positions:
            if pos["symbol"] == symbol and pos["status"] == "OPEN":
                current = pos["current_premium"]
                self._exit_position(pos, current, reason)
                return True
        return False

    # ================================================================
    # SQUARE OFF ALL
    # ================================================================

    def square_off_all(self) -> int:
        """Close all open positions at market. Returns count closed."""
        count = 0
        for pos in self._positions:
            if pos["status"] == "OPEN":
                current = pos["current_premium"]
                if current <= 0:
                    # Last known premium as fallback
                    current = pos["entry_premium"] * 0.5
                self._exit_position(pos, current, "SQUARE_OFF")
                count += 1
        if count > 0:
            self.log.info(f"Squared off {count} option position(s).")
        return count

    # ================================================================
    # QUERIES
    # ================================================================

    def open_positions(self) -> list[dict]:
        return [p for p in self._positions if p["status"] == "OPEN"]

    def closed_positions(self) -> list[dict]:
        return [p for p in self._positions if p["status"] == "CLOSED"]

    def all_positions(self) -> list[dict]:
        return list(self._positions)

    def day_pnl(self) -> float:
        """Total realised + MTM P&L for the day."""
        pnl = 0.0
        for pos in self._positions:
            if pos["status"] == "CLOSED":
                pnl += pos["pnl"]
            elif pos["status"] == "OPEN":
                # MTM
                pnl += (pos["current_premium"] - pos["entry_premium"]) * pos["qty"]
        return round(pnl, 2)

    def is_circuit_broken(self) -> bool:
        return self._circuit_broken
