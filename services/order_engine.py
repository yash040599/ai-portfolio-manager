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
import collections

from config              import Config, now_ist
from services.stock_scanner import SECTOR_MAP, MAX_PER_SECTOR
from services             import candle_patterns
from core.logger         import Logger
from core.zerodha_client import ZerodhaClient


class OrderEngine:

    # Reversal pattern sets used by the pattern-direction entry veto
    # (#190). Single source of truth lives in services/candle_patterns.
    _BEARISH_REVERSAL_PATTERNS = candle_patterns.BEARISH_REVERSAL_PATTERNS
    _BULLISH_REVERSAL_PATTERNS = candle_patterns.BULLISH_REVERSAL_PATTERNS

    @staticmethod
    def _depth_levels(quote_data: dict, side: str) -> list[dict]:
        """Return Zerodha depth levels for one side, or [] when malformed."""
        if not isinstance(quote_data, dict):
            return []
        depth = quote_data.get("depth")
        if not isinstance(depth, dict):
            return []
        levels = depth.get(side)
        return levels if isinstance(levels, list) else []

    @staticmethod
    def _level_number(level: dict, field: str) -> float:
        if not isinstance(level, dict):
            return 0.0
        try:
            return float(level.get(field, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _fetch_entry_quote(
        self,
        symbol: str,
        exchange: str,
        required_depth_sides: set[str] | None = None,
        require_spread_book: bool = False,
        impact_side: str | None = None,
        max_attempts: int = 3,
    ) -> tuple[dict, dict]:
        """Fetch a valid live entry quote, retrying transient Zerodha gaps."""
        required_depth_sides = required_depth_sides or set()
        stock = {"symbol": symbol, "exchange": exchange}
        quote_key = f"{exchange}:{symbol}"

        for attempt in range(1, max_attempts + 1):
            live_quotes = self.zerodha.get_quotes_safe([stock], max_retries=1) or {}
            quote_data = live_quotes.get(quote_key, {})
            live_price = (
                quote_data.get("last_price", 0)
                if isinstance(quote_data, dict) else 0
            )
            has_price = live_price > 0
            has_depth = all(
                self._depth_levels(quote_data, depth_side)
                for depth_side in required_depth_sides
            )
            has_spread_book = True
            if require_spread_book:
                buy_depth = self._depth_levels(quote_data, "buy")
                sell_depth = self._depth_levels(quote_data, "sell")
                best_bid = self._level_number(buy_depth[0] if buy_depth else {}, "price")
                best_ask = self._level_number(sell_depth[0] if sell_depth else {}, "price")
                has_spread_book = best_bid > 0 and best_ask > 0 and best_ask >= best_bid

            has_impact_book = True
            if impact_side:
                has_impact_book = any(
                    self._level_number(level, "price") > 0
                    and self._level_number(level, "quantity") > 0
                    for level in self._depth_levels(quote_data, impact_side)
                )

            if has_price and has_depth and has_spread_book and has_impact_book:
                return live_quotes, quote_data

            if attempt < max_attempts:
                missing = []
                if not has_price:
                    missing.append("live price")
                if not has_depth:
                    missing.append("order-book depth")
                if not has_spread_book:
                    missing.append("bid/ask prices")
                if not has_impact_book:
                    missing.append("impact-depth levels")
                self.log.warning(
                    f"{symbol}: Zerodha quote missing {' + '.join(missing)} "
                    f"(attempt {attempt}/{max_attempts}) — retrying."
                )
                time.sleep(attempt)

        return {}, {}

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
        # Semantics: TOTAL deployable cap (does NOT shrink with margin used).
        # Used by loss_adjusted_budget() and as the ceiling for exposure.
        self._budget: float = float(config.MAX_BUDGET_INR)

        # Live-mode available funds from Zerodha (already reflects margin
        # blocked by open positions). Refreshed by refresh_budget(). None
        # in dry-run or before first refresh — callers must handle that.
        # Roadmap #171: keep separate from _budget to avoid double-counting
        # exposure (Zerodha already subtracted it once).
        self._available_funds: float | None = None

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

        # Per-symbol re-entry cooldown (Roadmap #161). Keyed by
        # "SYMBOL_SIDE", value is exit timestamp. Checked in enter_trade
        # to block re-entry in the same direction within the cooldown window.
        self._last_exit_time: dict[str, datetime.datetime] = {}

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

        # ── Peak-drawdown tracking (#168) ──────────────────────────
        # Ratchets up via max(peak, day_pnl()) on every is_peak_drawdown_stopped()
        # call. Fresh per OrderEngine instance — reset implicitly by daily restart.
        self._intraday_peak_pnl: float = 0.0

        # ── MTM-aware CB quote cache (#166) ────────────────────────
        # Populated by manager monitor loop via set_latest_quotes().
        # Used by effective_day_pnl() so circuit-breaker / soft-stop /
        # peak-drawdown can include open-position MTM without every
        # call site having to thread quotes through.
        self._latest_quotes: dict = {}

        # ── Choppy-morning pause state (#192) ──────────────────────
        # _recent_nifty_adx is a rolling buffer of recent NIFTY ADX
        # readings stamped via record_nifty_adx() each scan tick.
        # _recent_chop_exits keeps timestamps of STAGNANT_EXIT and
        # SIGNAL_DECAY exits so we know when churn is in progress.
        # _choppy_pause_until is the wall-clock time the current pause
        # ends (None when no pause is active).
        self._recent_nifty_adx: collections.deque = collections.deque(
            maxlen=max(1, int(self.cfg.CHOPPY_PAUSE_MIN_CONSECUTIVE_SCANS))
        )
        self._recent_chop_exits: collections.deque = collections.deque(maxlen=20)
        self._choppy_pause_until: datetime.datetime | None = None

        # ── Strong-gap ADX boost flag (#194) ───────────────────────
        # Set once per session by record_strong_gap_day("UP"|"DOWN") when the
        # NIFTY opens with a strong gap that continues the prior-day trend.
        # Resets on engine restart (i.e. at session boundary).
        # `_strong_gap_day` is a cached bool kept in sync for legacy readers;
        # the authoritative value is `_strong_gap_direction` ("UP"/"DOWN"/None).
        self._strong_gap_day: bool = False
        self._strong_gap_direction: str | None = None
        # ── Last-exit score cache (#195) ───────────────────────────
        # Stamped by exit_position(). Keyed "SYMBOL_SIDE", value is
        # {"score": float, "reason": str, "time": datetime}. Used by
        # the average-down prevention gate in enter_trade.
        self._last_exit_score: dict[str, dict] = {}

        # ── VIX intraday-spike pause state (#211) ──────────────────
        # Manager updates this each NIFTY-recheck via set_vix_spike()
        # so enter_trade() honours the same pause as the manager-level
        # opportunity / re-scan paths. Default False = no pause.
        self._vix_spike_active: bool = False

        # ── Entry-burst cap state (#179) ───────────────────────────
        # Rolling timestamps of recent entries (any side, any symbol).
        # `enter_trade()` consults this to enforce
        # `ENTRY_BURST_CAP_MAX_ENTRIES_PER_60S` — the third entry inside
        # any 60-second window is rejected with `BURST_CAP`. Stamped at
        # the END of `enter_trade()` so only successful entries count.
        self._recent_entry_times: collections.deque = collections.deque(maxlen=32)

        # ── Multi-day pause state (#251 directional + #253 rolling-PF) ──
        # Both are armed at session start by the manager via the
        # `arm_multiday_pauses(...)` helper below, which queries
        # `intraday_tax_ledger` once and stamps these attributes.
        # `_directional_pause_side` is None / "BUY" / "SELL".
        # `_rolling_pf_pause_armed` is False / True.
        # Both clear naturally at next session (fresh OrderEngine).
        self._directional_pause_side: str | None = None
        self._directional_pause_reason: str = ""
        self._rolling_pf_pause_armed: bool = False
        self._rolling_pf_pause_reason: str = ""

        # ── Opposing-side fractional-Kelly cap ─────────────────────
        # When a directional pause arms, cap entries on the un-paused
        # side at OPPOSING_THIN_MAX_ENTRIES if its history is thin
        # (n < OPPOSING_MIN_TRADES). Stamped in `arm_multiday_pauses`,
        # bumped in `record_entry`.
        self._opposing_thin_side: str | None = None  # the side TO BE CAPPED
        self._opposing_thin_reason: str = ""
        self._opposing_thin_count: int = 0
        self._opposing_thin_max: int = 0

        # ── Intraday NIFTY-bounce bypass on directional pause ──────
        # Manager pushes NIFTY intraday-return % each scan via
        # `record_nifty_intraday_return()`. When the deque shows
        # MIN_SCANS consecutive readings whose sign favours the paused
        # side, `is_directional_paused()` returns False (bypass).
        # `_directional_bypass_logged` keeps the WARN one-shot.
        self._nifty_intraday_returns: collections.deque = collections.deque(maxlen=10)
        self._directional_bypass_logged: dict[str, bool] = {"BUY": False, "SELL": False}

        # ── Tape-breadth state ─────────────────────────────────────
        # Scanner stamps this dict each scan with {"buys", "sells",
        # "ratio", "tape": "BULLISH"|"BEARISH"|"NEUTRAL"}. Consumed by
        # (a) score-rejection log context, (b) the breadth-bypass on
        # directional pause (`_has_breadth_divergence`).
        self._tape_breadth: dict | None = None
        # Per-side one-shot WARN flag for the breadth-bypass episode.
        # Re-arms when divergence stops firing so a new episode logs.
        self._breadth_bypass_logged: dict[str, bool] = {"BUY": False, "SELL": False}

        # ── Adaptive R:R tracking ─────────────────────────────────
        # Counts scans that produced 0 entries (every candidate rejected).
        # After RR_GIVEUP_AFTER_FAILS consecutive empty scans we stop
        # trading entirely — "today is not a trading day". The previous
        # adaptive-relaxation and mid-day-retry branches were collapsed
        # by #243 (always-on RR_HARD_FLOOR=1.3 made them no-ops).
        self._zero_entry_scans: int = 0
        self._rr_giveup: bool = False  # True = stop trading for the day

        # Per-batch counter of R:R-related rejections (gross R:R below
        # `current_rr_floor()` OR net-of-charges R:R below 1.0). The
        # manager's mid-day R:R-retry step-down checks this before
        # firing a second pass: if zero R:R rejections happened, no
        # candidate would benefit from a lower floor and re-running
        # the same 15 stocks just wastes Claude/Kite quota and pollutes
        # logs (observed 2026-04-24 11:00:56 → 11:01:13 batch). The
        # manager calls reset_rr_rejection_count() at the start of
        # each `_attempt_entries` pass.
        self._rr_rejection_count: int = 0

    # ── Adaptive R:R methods ──────────────────────────────────

    def current_rr_floor(self, hour: int = 10) -> float:
        """Returns the R:R floor that every trade must clear.

        Single uniform floor since #243 — collapsed from the previous
        time-tiered + relaxation + retry resolution, which #235/#242
        had already neutralised. The `hour` parameter is kept for call-
        site signature compatibility but is no longer consulted.
        """
        return float(getattr(self.cfg, "RR_HARD_FLOOR", 1.3))

    def _rr_floor_label(self, hour: int) -> str:
        """Returns a descriptive label for the active R:R floor.

        Always `hard-floor` since #243 (single-floor regime). The
        `hour` parameter is kept for call-site signature compatibility.
        """
        return "hard-floor"

    def record_scan_result(self, entered: int):
        """Called after each scan+entry cycle. Tracks 0-entry streaks
        so the manager can stop trading entirely after RR_GIVEUP_AFTER_FAILS
        consecutive empty scans (#243 keeper — relaxation/retry are gone)."""
        if entered > 0:
            self._zero_entry_scans = 0
            return

        self._zero_entry_scans += 1

        if self._zero_entry_scans >= self.cfg.RR_GIVEUP_AFTER_FAILS:
            self._rr_giveup = True
            self.log.warning(
                f"R:R adaptive: {self._zero_entry_scans} scans with 0 entries "
                f"at the {self.cfg.RR_HARD_FLOOR:.1f}:1 floor — no viable "
                f"setups today, stopping"
            )

    def is_rr_giveup(self) -> bool:
        """True if too many scans failed even at relaxed R:R floor."""
        return self._rr_giveup

    def set_budget(self, amount: float):
        """Sets the trading budget and adjusts MAX_POSITIONS dynamically."""
        self._budget = amount
        # Roadmap #171: clear any stale live-funds reading from a prior
        # session so the next refresh_budget() call repopulates it.
        self._available_funds = None
        if hasattr(self.cfg, 'dynamic_max_positions'):
            new_max = self.cfg.dynamic_max_positions(amount)
            if new_max != self.cfg.MAX_POSITIONS:
                self.log.info(
                    f"MAX_POSITIONS adjusted: {self.cfg.MAX_POSITIONS} → {new_max} "
                    f"(budget Rs.{amount:,.0f})"
                )
                self.cfg.MAX_POSITIONS = new_max

        # Announce the budget regime and its effective gates so every
        # run's log header records which knob values are actually in play.
        if self.cfg.BUDGET_REGIME_ENABLED:
            self.log.info(
                f"Budget regime: {self.budget_regime()} (Rs.{amount:,.0f}) → "
                f"ADX≥{self.effective_adx_threshold():.1f}, "
                f"trade-cap {self.effective_trade_cap()}, "
                f"min-score {self.effective_min_score():.1f}"
            )

    def is_order_api_broken(self) -> bool:
        """
        Returns True if Zerodha order API has failed consecutively
        and the engine should stop placing new orders.
        """
        return self._order_api_broken

    # ================================================================
    # RESUME — LOAD EXISTING POSITIONS FROM ZERODHA
    # ================================================================

    def _trades_index_by_symbol(self) -> dict[str, list[dict]]:
        """Fetch today's executed trades from Zerodha and group by
        symbol, sorted by fill_timestamp.

        Returns {} on API failure (callers fall back to net-positions
        only). Used by both load_existing_positions() and
        recover_prior_session_fills() to recover real order_ids,
        timestamps, and side ordering after a crash/restart.
        """
        try:
            raw = self.zerodha.get_todays_trades()
        except Exception as e:
            self.log.warning(f"get_todays_trades() failed: {e}")
            return {}

        idx: dict[str, list[dict]] = {}
        for t in raw or []:
            sym = t.get("tradingsymbol")
            if not sym:
                continue
            idx.setdefault(sym, []).append(t)
        for sym, fills in idx.items():
            fills.sort(key=lambda f: str(f.get("fill_timestamp")
                                         or f.get("order_timestamp") or ""))
        return idx

    @staticmethod
    def _fmt_fill_time(ts) -> str | None:
        """Return HH:MM:SS for a Kite fill_timestamp (datetime or str)."""
        if ts is None:
            return None
        s = str(ts)
        # Kite returns "YYYY-MM-DD HH:MM:SS" (str or datetime). Take last 8 chars.
        return s[-8:] if len(s) >= 8 else None

    @staticmethod
    def _time_seconds(time_str) -> int | None:
        """Return seconds since midnight for HH:MM:SS-ish strings."""
        if not time_str:
            return None
        s = str(time_str).replace("T", " ")
        if " " in s:
            s = s.split(" ")[-1]
        s = s[:8]
        try:
            h, m, sec = (int(part) for part in s.split(":")[:3])
        except (TypeError, ValueError):
            return None
        return h * 3600 + m * 60 + sec

    def _external_close_fill_from_trades(
        self,
        fills: list[dict],
        exit_side: str,
        qty: int,
        entry_time: str | None,
    ) -> tuple[float, str] | None:
        """Infer a user/broker close fill from kite.trades() rows.

        Zerodha net-positions exposes only day-level buy/sell averages, which
        can blend separate round-trips on the same symbol. For external closes
        we want the first opposite-side fills after our entry time, capped at
        the position qty. This is the broker-truth path that prevents one
        position's exit price from being averaged with a later ghost/opening
        fill on the same symbol.
        """
        if qty <= 0:
            return None

        entry_sec = self._time_seconds(entry_time)
        total_qty = 0
        total_value = 0.0
        last_time: str | None = None

        for fill in fills:
            if fill.get("transaction_type") != exit_side:
                continue
            fill_time = self._fmt_fill_time(
                fill.get("fill_timestamp") or fill.get("order_timestamp")
            )
            fill_sec = self._time_seconds(fill_time)
            if entry_sec is not None and fill_sec is not None and fill_sec < entry_sec:
                continue

            try:
                fill_qty = int(fill.get("quantity", 0) or 0)
                fill_price = float(fill.get("average_price", 0) or 0)
            except (TypeError, ValueError):
                continue
            if fill_qty <= 0 or fill_price <= 0:
                continue

            take_qty = min(fill_qty, qty - total_qty)
            total_qty += take_qty
            total_value += take_qty * fill_price
            last_time = fill_time or last_time
            if total_qty >= qty:
                return round(total_value / total_qty, 2), (last_time or "")

        if total_qty > 0:
            return round(total_value / total_qty, 2), (last_time or "")
        return None

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

        # Pull today's fills so resumed positions get the real opening
        # order_id + entry_time instead of the placeholder "RESUMED".
        # Falls back gracefully to {} (and the legacy placeholder
        # behaviour) if the API call fails.
        trades_idx = self._trades_index_by_symbol()

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

            # Recover the real opening order_id + entry_time from
            # today's fills, if available. The opening side equals the
            # current net side (a still-open position has only opening
            # fills on the dominant side; the opposite side's qty is 0).
            real_order_id   = "RESUMED"
            real_entry_time = now.strftime("%H:%M:%S")
            for fill in trades_idx.get(symbol, []):
                if fill.get("transaction_type") == side:
                    real_order_id = str(fill.get("order_id") or "RESUMED")
                    ft = self._fmt_fill_time(fill.get("fill_timestamp")
                                             or fill.get("order_timestamp"))
                    if ft:
                        real_entry_time = ft
                    break  # earliest fill (list is timestamp-sorted)

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
                "entry_time":   real_entry_time,
                "exit_time":    None,
                "rationale":    "Resumed from existing Zerodha position",
                "order_id":     real_order_id,
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

        # Roadmap #148 — reconcile orphan SL-M orders
        # ALWAYS run reconcile, even when loaded == 0. Scenario: on
        # restart Zerodha has no open positions (they closed before
        # crash) but a stray SL-M is still live on the exchange — it
        # would fire later and open an unintended REVERSE position.
        self._reconcile_orphan_sl_m()

        return loaded

    # ================================================================
    # REALISED-P&L RECOVERY (#203) — RECONSTRUCT PRIOR-SESSION CLOSES
    # ================================================================

    def recover_prior_session_fills(self) -> int:
        """Rebuild today's CLOSED positions from Zerodha's net-positions
        for ones that the bot didn't record (because it crashed / was
        restarted between fills).

        Why: load_existing_positions() only adopts OPEN positions
        (qty != 0). Any prior-session position closed by an exchange-
        side SL-M during the crash window is invisible to the bot. The
        in-memory `self.positions` list starts empty each session and
        `day_pnl()` sums only what's in that list — so realised P&L
        resets to 0 on every restart. This breaks the MTM-aware
        circuit breaker (#197) and adaptive-budget sizing because
        both reason from a wrong P&L floor.

        Logic:
          For each Zerodha net-positions row with:
            - product == "MIS"  (intraday only)
            - quantity == 0     (closed)
            - buy_quantity > 0 AND sell_quantity > 0  (round-trip done)
            - not already represented in self.positions  (no double-count)
          Synthesise a CLOSED record using Zerodha's authoritative
          buy_price / sell_price / pnl. Side is inferred from which
          side opened first (we don't actually know — Zerodha doesn't
          give that detail in net positions — so we mark with the
          side that had the larger quantity at the close time, which
          for fully-closed positions equals buy_quantity == sell_quantity
          and we default to BUY/long convention).

          Net qty for the synthetic record = buy_quantity (== sell_quantity
          since net == 0). entry_time / exit_time are unknown.
          exit_reason = "RECOVERED_FROM_ZERODHA". status = "CLOSED".

        Returns the number of closed positions recovered.

        Fail-safe: any API failure logs a warning and returns 0 — the
        bot continues with realised = 0 on this session, same as the
        legacy behaviour (no regression).

        Kill-switch: REALISED_PNL_RECOVERY_ENABLED.
        """
        if not getattr(self.cfg, "REALISED_PNL_RECOVERY_ENABLED", False):
            return 0
        try:
            positions_data = self.zerodha.get_positions()
        except Exception as e:
            self.log.warning(
                f"Realised-P&L recovery: failed to fetch positions from Zerodha: {e}"
            )
            return 0

        # Pull fills so we can reconstruct true side / entry_time /
        # exit_time / opening order_id for each round-trip. Falls back
        # to the legacy net-positions-only path if the API fails.
        trades_idx = self._trades_index_by_symbol()

        net_positions = positions_data.get("net", []) or []
        # Symbols already in self.positions (open or closed) — skip to
        # avoid double-booking. load_existing_positions() runs first so
        # any OPEN MAZDOCK won't be re-recovered if it later closes mid-
        # session (that path goes through exit_position).
        existing_symbols = {p.get("symbol") for p in self.positions}

        recovered = 0
        recovered_pnl = 0.0
        recovered_lines: list[str] = []

        for pos in net_positions:
            try:
                if pos.get("product") != "MIS":
                    continue
                if int(pos.get("quantity", 0) or 0) != 0:
                    continue   # still open — handled by load_existing_positions
                buy_qty  = int(pos.get("buy_quantity", 0) or 0)
                sell_qty = int(pos.get("sell_quantity", 0) or 0)
                if buy_qty <= 0 or sell_qty <= 0:
                    continue   # one-sided (carry-forward / placeholder); skip
                symbol = pos.get("tradingsymbol", "")
                if not symbol or symbol in existing_symbols:
                    continue
                buy_price  = float(pos.get("buy_price", 0) or 0)
                sell_price = float(pos.get("sell_price", 0) or 0)
                if buy_price <= 0 or sell_price <= 0:
                    continue
                # Zerodha net-positions returns matched qty; for an
                # MIS round-trip buy_qty should equal sell_qty.
                # Use the smaller in case of asymmetry (defensive).
                qty = min(buy_qty, sell_qty)
                if qty <= 0:
                    continue

                pnl = float(pos.get("pnl", 0) or 0)
                exchange = pos.get("exchange", "NSE")

                # ── Reconstruct true side/times/order_id from fills ──
                # First fill chronologically opened the position; the
                # opposite side closed it. This disambiguates LONG vs
                # SHORT (net-positions alone cannot — see comment block
                # below for the legacy fallback rationale).
                side          = "BUY"   # legacy fallback (see below)
                entry_time    = None
                exit_time     = None
                opening_order = "RECOVERED"
                closing_order = None
                fills = trades_idx.get(symbol, [])
                if fills:
                    first = fills[0]
                    last  = fills[-1]
                    open_side  = first.get("transaction_type")
                    close_side = last.get("transaction_type")
                    if open_side in ("BUY", "SELL") and open_side != close_side:
                        side          = open_side
                        entry_time    = self._fmt_fill_time(
                            first.get("fill_timestamp")
                            or first.get("order_timestamp"))
                        exit_time     = self._fmt_fill_time(
                            last.get("fill_timestamp")
                            or last.get("order_timestamp"))
                        opening_order = str(first.get("order_id") or "RECOVERED")
                        closing_order = str(last.get("order_id") or "")

                # Without kite.trades() we cannot reliably reconstruct
                # whether the original trade was LONG or SHORT from
                # net-positions alone. Conservative default: BUY.
                # (See git history for full rationale; the cooldown
                # impact is asymmetric but a fresh +score override
                # bypasses it.) Since #fills-recovery now handles the
                # common case, this fallback only fires when get_trades
                # itself fails — which also blocks the side disambiguation.

                # Use real fill prices when we know the side (avoids
                # the LONG-loser misnomer where buy_price > sell_price).
                if side == "BUY":
                    entry_price_disp, exit_price_disp = buy_price, sell_price
                else:
                    entry_price_disp, exit_price_disp = sell_price, buy_price

                # Heuristic exit reason: stop-loss-style exit if loss
                # exceeded ~0.5% of entry value — purely cosmetic, real
                # reason is unrecoverable post-restart.
                if abs(pnl) > 0 and entry_price_disp > 0:
                    loss_pct = pnl / (entry_price_disp * qty) * 100
                    if loss_pct <= -1.0:
                        exit_reason = "STOP_LOSS_RECOVERED"
                    elif loss_pct >= 1.0:
                        exit_reason = "TARGET_RECOVERED"
                    else:
                        exit_reason = "SQUARE_OFF_RECOVERED"
                else:
                    exit_reason = "RECOVERED_FROM_ZERODHA"

                synthetic = {
                    "symbol":        symbol,
                    "exchange":      exchange,
                    "side":          side,
                    "qty":           qty,
                    "entry_price":   round(entry_price_disp, 2),
                    "stop_loss":     0.0,
                    "target_price":  0.0,
                    "exit_price":    round(exit_price_disp, 2),
                    "exit_reason":   exit_reason,
                    "status":        "CLOSED",
                    "pnl":           round(pnl, 2),
                    "entry_time":    entry_time,
                    "exit_time":     exit_time,
                    "rationale":     "Recovered from Zerodha after restart",
                    "order_id":      opening_order,
                    "_sl_order_id":  closing_order,
                    "_external":     True,   # don't attribute to bot strategy
                }
                self.positions.append(synthetic)
                existing_symbols.add(symbol)
                recovered += 1
                recovered_pnl += pnl
                recovered_lines.append(
                    f"{symbol} {side} {qty}× Rs.{synthetic['entry_price']:.2f}"
                    f"→Rs.{synthetic['exit_price']:.2f} = Rs.{pnl:+,.2f}"
                )
            except (ValueError, TypeError, KeyError) as e:
                self.log.warning(
                    f"Realised-P&L recovery: skipped malformed Zerodha row "
                    f"({type(e).__name__}: {e})"
                )
                continue

        if recovered > 0:
            self.log.success(
                f"✓ Recovered {recovered} closed position(s) from Zerodha "
                f"(realised Rs.{recovered_pnl:+,.2f}): "
                f"{'; '.join(recovered_lines)}"
            )
        return recovered

    # ================================================================
    # STALE SL-M RECONCILIATION — #148
    # ================================================================

    def _reconcile_orphan_sl_m(self) -> None:
        """
        Reconcile pending SL-M orders against in-memory positions.

        Called after load_existing_positions (crash recovery) and after
        sync_external_positions (adoption). For each live SL-M order on
        Zerodha:
          (a) If a tracked OPEN position matches on (symbol, exit_side)
              with the same qty → attach the order id (reuse broker stop).
          (b) If (symbol, exit_side) matches but qty differs → attach AND
              resize the broker-side SL-M to the tracked qty. Never cancel
              on qty-only mismatch — that would leave the position
              unprotected between cancel and any subsequent re-place.
          (c) If no position matches the symbol+side and the order was
              placed TODAY → cancel it (orphan from a prior crash).
          (d) Stray order from an earlier day → log loud warning, don't
              touch (shouldn't happen because MIS auto-expires overnight).

        Fail-safe: any API failure is logged and skipped — never raises.
        """
        if self.cfg.DRY_RUN:
            return

        try:
            orders = self.zerodha.get_orders()
        except Exception as e:
            self.log.warning(f"SL-M reconcile: get_orders() failed: {e}")
            return

        if not orders:
            return

        # Only care about live SL-M orders (still capable of firing)
        live_states = {"OPEN", "TRIGGER PENDING"}
        sl_types = {"SL-M", "SL"}
        # Exclude orders the bot already tracks. Without this filter, a
        # periodic reconcile would see our own freshly-placed SL-M on the
        # exchange, fail to find its position in pos_lookup (because
        # positions with _sl_order_id are skipped), and cancel it as an
        # "orphan today" — wiping out every valid broker-side stop on
        # every sync cycle.
        live_sl_orders = [
            o for o in orders
            if (o.get("status") in live_states
                and o.get("order_type") in sl_types
                and o.get("product") == "MIS"
                and o.get("order_id") not in self._pending_order_ids)
        ]

        if not live_sl_orders:
            return

        today_str = now_ist().strftime("%Y-%m-%d")
        attached = 0
        resized = 0
        cancelled = 0
        stray = 0

        # Build lookup: (symbol, exit_side) -> position (qty-agnostic so
        # a crashed/partial resize doesn't leave the position unprotected)
        pos_lookup: dict[tuple, dict] = {}
        for p in self.open_positions():
            if p.get("_sl_order_id"):
                continue  # already has an SL-M attached
            exit_side = "SELL" if p["side"] == "BUY" else "BUY"
            pos_lookup[(p["symbol"], exit_side)] = p

        for o in live_sl_orders:
            sym     = o.get("tradingsymbol", "")
            txn     = o.get("transaction_type", "")  # BUY / SELL of the SL-M (= exit side)
            qty     = int(o.get("quantity", 0) or 0)
            oid     = o.get("order_id", "")
            trig    = float(o.get("trigger_price") or 0)
            ts_str  = str(o.get("order_timestamp", ""))[:10]  # YYYY-MM-DD

            key = (sym, txn)
            p = pos_lookup.get(key)
            if p is not None:
                # Attach first — this always-succeeds in-memory op ensures
                # the position is NEVER left unprotected even if the
                # subsequent resize fails.
                p["_sl_order_id"] = oid
                self._pending_order_ids.add(oid)
                del pos_lookup[key]

                if qty == p["qty"]:
                    attached += 1
                    self.log.success(
                        f"SL-M reconcile: attached order {oid} "
                        f"({txn} {qty}x {sym} @ trigger Rs.{trig:.2f}) "
                        f"to open {p['side']} position"
                    )
                    self._log_action("SL_M_ATTACH", sym, txn, qty, trig,
                                     f"Reconciled orphan SL-M {oid}")
                else:
                    # Qty drift (e.g. partial taken before crash, or mid-
                    # resize crash). Resize the broker-side order to match
                    # tracked qty. _replace_exchange_sl cancels the current
                    # order and places a new one at p["qty"]. NOTE: it
                    # swallows failures internally (no exception), so we
                    # must verify by checking _sl_order_id afterwards.
                    self.log.warning(
                        f"SL-M reconcile: order {oid} qty {qty} != "
                        f"tracked qty {p['qty']} for {sym} — resizing"
                    )
                    try:
                        self._replace_exchange_sl(p, p["stop_loss"])
                    except Exception as e:
                        self.log.error(
                            f"SL-M reconcile: resize raised for {sym} "
                            f"(order {oid}): {e}"
                        )

                    new_oid = p.get("_sl_order_id")
                    if new_oid and new_oid != oid:
                        resized += 1
                        self._log_action("SL_M_RESIZE", sym, txn, p["qty"], trig,
                                         f"Reconciled + resized SL-M (was qty {qty})")
                    else:
                        # Replacement cancelled the old order but failed to
                        # place a new one. Position is now UNPROTECTED.
                        # _replace_exchange_sl already cleared _sl_order_id.
                        self.log.error(
                            f"SL-M RECONCILE CRITICAL: {sym} resize failed — "
                            f"old order {oid} was cancelled, new order NOT "
                            f"placed. POSITION IS UNPROTECTED. Software SL "
                            f"is the only defence. REVIEW IMMEDIATELY."
                        )
                        self._log_action("SL_M_UNPROTECTED", sym, txn, p["qty"], trig,
                                         f"Resize failed — software SL only")
                continue

            # Not matched to any position on (symbol, side)
            if ts_str and ts_str == today_str:
                # Orphan from this trading day — safe to cancel
                try:
                    self.zerodha.cancel_order(oid)
                    cancelled += 1
                    self.log.warning(
                        f"SL-M reconcile: cancelled orphan order {oid} "
                        f"({txn} {qty}x {sym} @ trigger Rs.{trig:.2f}) — "
                        f"no matching open position"
                    )
                    self._log_action("SL_M_CANCEL", sym, txn, qty, trig,
                                     f"Orphan SL-M cancelled (no matching position)")
                except Exception as e:
                    self.log.error(
                        f"SL-M reconcile: failed to cancel orphan {oid}: {e}"
                    )
            else:
                # Stray from a different day (MIS should auto-cancel overnight,
                # but if Zerodha left it we refuse to touch it — too risky)
                stray += 1
                self.log.error(
                    f"SL-M reconcile: STRAY order {oid} "
                    f"({txn} {qty}x {sym}) from {ts_str or 'unknown date'} — "
                    f"NOT cancelled automatically. Review manually."
                )

        if attached or resized or cancelled or stray:
            self.log.info(
                f"SL-M reconcile summary: {attached} attached, "
                f"{resized} resized, {cancelled} cancelled, {stray} stray"
            )

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

        # ── Defensive guard: empty response with tracked open positions ──
        # A temporary Zerodha API glitch can return net=[] while positions
        # are actually still open. Without this guard, every open position
        # would be marked EXTERNAL_CLOSE (software SL cancelled, monitoring
        # stopped) and left unprotected until 3:20 auto-square-off. Only
        # trust an empty response if we don't have anything open.
        open_tracked_count = sum(1 for p in self.positions if p["status"] == "OPEN")
        if not net_positions and open_tracked_count > 0:
            self.log.warning(
                f"sync_external_positions: Zerodha returned empty positions "
                f"but bot tracks {open_tracked_count} OPEN position(s). "
                f"Likely API glitch — skipping close-detection this cycle."
            )
            return 0

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
        # Roadmap #151 — map (symbol, bot-side) to current Zerodha abs qty
        # so we can detect PARTIAL external closes (user closes half on Kite).
        # Key is (symbol, side_in_bot_terms) because a long (qty>0) matches
        # bot's BUY side and a short (qty<0) matches bot's SELL side.
        zerodha_qty_by_side: dict[tuple, int] = {}
        for pos in net_positions:
            if pos.get("product") != "MIS":
                continue
            zq = pos.get("quantity", 0)
            if zq == 0:
                continue
            z_side = "BUY" if zq > 0 else "SELL"
            zerodha_qty_by_side[(pos.get("tradingsymbol", ""), z_side)] = abs(zq)

        trades_idx_for_external: dict[str, list[dict]] | None = None

        for p in self.positions:
            if p["status"] != "OPEN":
                continue

            # ── Full external close detection ───────────────────────
            if (p["symbol"] not in zerodha_open
                    and (p["symbol"], p["side"], p["qty"]) not in self._bot_closed_positions):
                # This is a position the bot doesn't know about and it's NOT
                # in bot_closed_positions, so it looks like a user close.
                #
                # CRITICAL BUG FIX (2026-04-22): Before labelling this as
                # EXTERNAL_CLOSE, check if the position's tracked exchange
                # SL-M order has actually FIRED. The exchange SL-M can
                # trigger between two of our 10s polling cycles (or via a
                # gap that skips the software-SL check entirely). When that
                # happens, _bot_closed_positions never gets populated
                # because exit_position() was never called — and we
                # mis-attribute a real STOP_LOSS as a manual user exit.
                # GRASIM 2026-04-22 was the first observed case: software
                # never called exit_position("STOP_LOSS") but the exchange
                # SL-M fired at Rs.2782.90 and the position vanished.
                sl_oid_check = p.get("_sl_order_id")
                sl_fired = False
                sl_fill_price = None
                if sl_oid_check and not self.cfg.DRY_RUN:
                    try:
                        sl_status = self.zerodha.get_order_status(sl_oid_check)
                    except Exception as e:
                        sl_status = None
                        self.log.warning(
                            f"Could not read SL-M status for {p['symbol']} "
                            f"({sl_oid_check}) during external-close check: {e} — "
                            f"falling back to EXTERNAL_CLOSE attribution"
                        )
                    if sl_status == "COMPLETE":
                        sl_fired = True
                        try:
                            sl_fill_price = self.zerodha.get_order_fill_price(
                                sl_oid_check, timeout=3,
                            )
                        except Exception:
                            sl_fill_price = None

                # Fetch exit price from Zerodha's day position data with multiple fallbacks
                exit_price = sl_fill_price  # may be None; will fall through
                exit_time = None

                # 1. Prefer actual kite.trades() fills after this position's
                # entry time. Net-position buy/sell averages can blend multiple
                # same-symbol round-trips and produced the 2026-05-08 phantom
                # weighted-average exit price (#270 / #266 overlap).
                if exit_price is None:
                    if trades_idx_for_external is None:
                        trades_idx_for_external = self._trades_index_by_symbol()
                    exit_side = "SELL" if p["side"] == "BUY" else "BUY"
                    inferred = self._external_close_fill_from_trades(
                        trades_idx_for_external.get(p["symbol"], []),
                        exit_side=exit_side,
                        qty=int(p.get("qty", 0) or 0),
                        entry_time=p.get("entry_time"),
                    )
                    if inferred:
                        exit_price, exit_time = inferred
                
                # 2. Try Zerodha position data (sell_price for BUY, buy_price for SELL)
                for zp in net_positions:
                    if zp.get("tradingsymbol") == p["symbol"] and zp.get("product") == "MIS":
                        if p["side"] == "BUY":
                            exit_price = exit_price or zp.get("sell_price") or zp.get("last_price")
                        else:
                            exit_price = exit_price or zp.get("buy_price") or zp.get("last_price")
                        break
                
                # 3. Fallback: Get current market price if not found above
                if not exit_price:
                    try:
                        quotes = self.zerodha.get_quotes(
                            [{"symbol": p["symbol"], "exchange": p.get("exchange", "NSE")}]
                        ) or {}
                        quote_key = f"{p.get('exchange', 'NSE')}:{p['symbol']}"
                        exit_price = quotes.get(quote_key, {}).get("last_price")
                    except Exception as e:
                        self.log.warning(f"Failed to get market quote for {p['symbol']}: {e}")
                
                # 4. Final fallback: entry price (with error logged)
                if not exit_price:
                    exit_price = p["entry_price"]
                    self.log.error(
                        f"EXTERNAL_CLOSE exit price unknown for {p['symbol']} — "
                        f"using entry price Rs.{exit_price:.2f} as placeholder "
                        f"(live P&L will read 0; EOD verify_trades.py will "
                        f"reconcile the true fill price from broker tax P&L)."
                    )
                else:
                    exit_price = round(exit_price, 2)

                if p["side"] == "BUY":
                    pnl = (exit_price - p["entry_price"]) * p["qty"]
                else:
                    pnl = (p["entry_price"] - exit_price) * p["qty"]

                # Cancel pending SL-M order for bot-opened positions
                # (skip when SL-M already fired — there's nothing to cancel).
                sl_oid = p.get("_sl_order_id")
                if sl_oid and not self.cfg.DRY_RUN and not sl_fired:
                    try:
                        self.zerodha.cancel_order(sl_oid)
                        self.log.info(f"Cancelled orphaned SL-M {sl_oid} for {p['symbol']}")
                    except Exception as e:
                        # The cancel can legitimately fail when the order has
                        # already filled / been cancelled by the user / expired.
                        # `cancel_order()` itself already demotes the common
                        # "does not exist" / "already" message to DEBUG (#208);
                        # mirror that here so unexpected failures still surface.
                        msg = str(e).lower()
                        if "does not exist" in msg or "already" in msg:
                            self.log.debug(
                                f"Orphan SL-M cancel for {p['symbol']} "
                                f"({sl_oid}) — terminal state: {e}"
                            )
                        else:
                            self.log.warning(
                                f"Orphan SL-M cancel failed for {p['symbol']} "
                                f"({sl_oid}): {type(e).__name__}: {e}"
                            )
                if sl_oid:
                    self._pending_order_ids.discard(sl_oid)
                    p["_sl_order_id"] = None

                origin = "External" if p.get("_external") else "Bot"
                p["status"] = "CLOSED"
                p["exit_price"] = round(exit_price, 2)
                if sl_fired:
                    # Exchange SL-M fired between our polling cycles — attribute
                    # to STOP_LOSS, not EXTERNAL_CLOSE. Also feed the whipsaw
                    # guard via record_sl_hit() so consecutive SL behaviour stays
                    # consistent with software-detected stops.
                    p["exit_reason"] = "STOP_LOSS"
                else:
                    p["exit_reason"] = "EXTERNAL_CLOSE"
                p["exit_time"] = exit_time or now_ist().strftime("%H:%M:%S")
                p["pnl"] = round(pnl, 2)
                # Record exit time for per-symbol re-entry cooldown (Roadmap #161).
                self._last_exit_time[f"{p['symbol']}_{p['side']}"] = now_ist()
                if sl_fired:
                    pnl_color = "\033[92m" if pnl >= 0 else "\033[91m"
                    self.log.info(
                        f"Exchange SL-M fired for {p['symbol']} (detected via sync, "
                        f"order {sl_oid_check}): {p['side']} {p['qty']}x "
                        f"@ Rs.{exit_price:.2f} | P&L: {pnl_color}Rs.{pnl:+,.2f}\033[0m"
                    )
                    self._log_action("EXIT", p["symbol"], p["side"], p["qty"],
                                     exit_price, "STOP_LOSS")
                    self.record_sl_hit()
                    # Track in _bot_closed_positions so a second sync pass in the
                    # same cycle doesn't re-process this as something else.
                    self._bot_closed_positions.add((p["symbol"], p["side"], p["qty"]))
                else:
                    self.log.info(
                        f"{origin} position closed by user: {p['side']} {p['qty']}x "
                        f"{p['symbol']} @ Rs.{exit_price:.2f} | P&L: Rs.{pnl:+,.2f}"
                    )
                    self._log_action("EXTERNAL_CLOSE", p["symbol"], p["side"],
                                     p["qty"], exit_price, "User closed via Zerodha app")
                continue

            # ── Partial external close detection (Roadmap #151) ─────
            # Position still present on Zerodha but with REDUCED qty.
            # User closed part of the position via Kite; reduce our
            # tracked qty and resize the exchange SL-M.
            z_qty = zerodha_qty_by_side.get((p["symbol"], p["side"]))
            if z_qty is None or z_qty >= p["qty"]:
                continue  # either fully gone (handled above) or still same/larger

            closed_qty = p["qty"] - z_qty
            # Estimate exit price from day position data (same fallbacks
            # as full-close path) for P&L attribution of the closed slice.
            partial_exit_price = None
            for zp in net_positions:
                if zp.get("tradingsymbol") == p["symbol"] and zp.get("product") == "MIS":
                    if p["side"] == "BUY":
                        partial_exit_price = zp.get("sell_price") or zp.get("last_price")
                    else:
                        partial_exit_price = zp.get("buy_price") or zp.get("last_price")
                    break
            if not partial_exit_price:
                partial_exit_price = p["entry_price"]  # safe fallback
                self.log.warning(
                    f"EXTERNAL_PARTIAL {p['symbol']}: could not determine "
                    f"exit price — using entry price for closed slice P&L"
                )
            partial_exit_price = round(partial_exit_price, 2)

            if p["side"] == "BUY":
                slice_pnl = round((partial_exit_price - p["entry_price"]) * closed_qty, 2)
            else:
                slice_pnl = round((p["entry_price"] - partial_exit_price) * closed_qty, 2)

            # Update tracked qty to the REAL remaining qty on Zerodha —
            # we cannot roll this back even if the SL-M resize fails,
            # because the user really did close closed_qty shares.
            # Rolling back qty would cause the bot to try to SL-exit
            # shares that no longer exist.
            old_qty = p["qty"]
            p["qty"] = z_qty
            old_sl_oid = p.get("_sl_order_id")

            # Resize exchange SL-M to match the new qty. _replace_exchange_sl
            # swallows exceptions internally, so we verify by inspecting
            # _sl_order_id afterwards rather than relying on try/except.
            try:
                self._replace_exchange_sl(p, p["stop_loss"])
            except Exception as e:
                self.log.error(
                    f"EXTERNAL_PARTIAL {p['symbol']}: resize raised: {e}"
                )

            new_sl_oid = p.get("_sl_order_id")
            resize_ok = bool(new_sl_oid) and new_sl_oid != old_sl_oid

            if resize_ok or old_sl_oid is None:
                # Either resize landed a fresh oid, or there was no
                # broker SL to begin with (software SL only).
                protection_note = (
                    f"resized SL-M active @ {new_sl_oid}"
                    if resize_ok else "software SL only (no broker SL was attached)"
                )
                self.log.info(
                    f"EXTERNAL_PARTIAL {p['symbol']} {p['side']}: user closed "
                    f"{closed_qty}/{old_qty} shares on Kite @ "
                    f"Rs.{partial_exit_price:.2f} | slice P&L Rs.{slice_pnl:+,.2f} | "
                    f"remaining {z_qty} shares — {protection_note}"
                )
                self._log_action("EXTERNAL_PARTIAL", p["symbol"], p["side"],
                                 closed_qty, partial_exit_price,
                                 f"User closed {closed_qty}/{old_qty} on Kite")
            else:
                # Cancel landed but replacement failed — position has
                # NO broker-side SL. Software SL will still fire on
                # stop breach so we're not totally naked, but broker
                # protection is gone. Log LOUD.
                self.log.error(
                    f"EXTERNAL_PARTIAL CRITICAL {p['symbol']}: user closed "
                    f"{closed_qty}/{old_qty} shares, but SL-M resize failed — "
                    f"broker SL is GONE (software SL is only defence). "
                    f"Remaining {z_qty} shares tracked, review manually."
                )
                self._log_action("EXTERNAL_PARTIAL_UNPROTECTED", p["symbol"], p["side"],
                                 closed_qty, partial_exit_price,
                                 f"User closed {closed_qty}/{old_qty}; broker SL lost")

            # Accumulate partial P&L so final close reports full picture
            p["_partial_pnl"] = round(p.get("_partial_pnl", 0) + slice_pnl, 2)
            p["_partial_qty"] = p.get("_partial_qty", 0) + closed_qty

        # NOTE: _bot_closed_positions already cleared at function START
        # Do NOT clear here — it needs to persist until next sync() call
        # BUG FIX: clearing at end causes stale entries in rapid successive calls

        # Roadmap #148 — reconcile orphan SL-M orders after any external
        # position adoption (user may have placed manual SL-M from Kite).
        # Run unconditionally: a user may place a stray SL-M from Kite
        # even when the bot adopted zero positions.
        self._reconcile_orphan_sl_m()

        return loaded

    def refresh_budget(self) -> float:
        """
        Re-queries Zerodha for actual available funds and stores it in
        ``_available_funds``. Called before re-scans so the next entry
        check sees the latest deployable cash.

        Roadmap #171: this used to overwrite ``_budget`` (the total cap),
        which then double-counted exposure in the budget check
        (`exposure + cost > _budget` where `_budget` had already been
        reduced by margin used). Now `_budget` stays as the configured
        cap and `_available_funds` carries the live-margin truth.

        Returns the latest available funds (or current ``_budget`` if
        unavailable / dry-run) for backwards compatibility.
        """
        if self.cfg.DRY_RUN:
            return self._budget

        try:
            self._available_funds = self.zerodha.get_available_funds()
        except Exception as e:
            # Keep last-known _available_funds so the budget check still
            # has a live-margin reference. Surface the failure so the
            # operator knows the bot is operating on stale broker data.
            if self._available_funds is not None:
                self.log.warning(
                    f"refresh_budget: Zerodha funds fetch failed — {e}. "
                    f"Using last-known available funds "
                    f"Rs.{self._available_funds:,.2f}"
                )
            else:
                self.log.warning(
                    f"refresh_budget: Zerodha funds fetch failed — {e}. "
                    f"No prior available-funds reading; budget check will "
                    f"fall back to configured cap minus exposure."
                )

        return self._available_funds if self._available_funds is not None else self._budget

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

        # Floor SL at MIN_SL_DISTANCE_PCT — ATR on high-priced stocks can
        # produce absurdly tight SLs (0.5%) that wick out on normal noise.
        # Target widens proportionally to preserve R:R.
        min_sl_pct = (
            self.cfg.EXPIRY_MIN_SL_DISTANCE_PCT
            if getattr(self.cfg, '_expiry_applied', False)
            else self.cfg.MIN_SL_DISTANCE_PCT
        )
        sl_pct = abs(sl - price) / price * 100
        if sl_pct < min_sl_pct:
            if side == "BUY":
                sl     = round(price * (1 - min_sl_pct / 100), 2)
                target = round(price * (1 + min_sl_pct * rr_mult / 100), 2)
            else:
                sl     = round(price * (1 + min_sl_pct / 100), 2)
                target = round(price * (1 - min_sl_pct * rr_mult / 100), 2)

        return sl, target

    def _default_sl_target(self, price: float, side: str) -> tuple[float, float]:
        """Fallback SL/target using DEFAULT_STOP_LOSS_PCT / DEFAULT_TARGET_PCT."""
        sl_pct  = self.cfg.DEFAULT_STOP_LOSS_PCT / 100
        tgt_pct = self.cfg.DEFAULT_TARGET_PCT / 100
        if side == "BUY":
            return round(price * (1 - sl_pct), 2), round(price * (1 + tgt_pct), 2)
        return round(price * (1 + sl_pct), 2), round(price * (1 - tgt_pct), 2)

    def _in_adoption_grace(self, pos: dict) -> bool:
        """
        Returns True if this position was adopted (ADOPT_EXTERNAL) or
        resumed (RESUMED) within the grace window. During grace, bot
        skips TIME_DECAY_TARGET and LOSER_EXIT so the user's manual
        trade gets a chance to play out.
        """
        grace_min = self.cfg.ADOPTED_POSITION_GRACE_MINUTES
        if grace_min <= 0:
            return False
        oid = pos.get("order_id") or ""
        if oid not in ("RESUMED", "EXTERNAL") and not pos.get("_external"):
            return False
        entry_time_str = pos.get("entry_time", "")
        if not entry_time_str:
            return False
        try:
            now = now_ist()
            entry_time = datetime.datetime.strptime(
                f"{now.strftime('%Y-%m-%d')} {entry_time_str}",
                "%Y-%m-%d %H:%M:%S",
            )
            elapsed_min = (now - entry_time).total_seconds() / 60
            return elapsed_min < grace_min
        except ValueError:
            return False

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
            except Exception as e:
                # Fail-open: fall back to the planned entry price below.
                # Logging visibility so a sustained Zerodha quote outage is
                # diagnosable from logs (otherwise the LIMIT-vs-LTP buffer
                # silently degrades to a stale-price MARKET-equivalent fill).
                self.log.warning(
                    f"LIMIT entry attempt {attempt}: get_quotes failed for "
                    f"{symbol} ({type(e).__name__}: {e}) — falling back to "
                    f"planned entry price."
                )
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
        """Open a new position from a scanner trade plan.

        Dry-run: log the order, assign a fake order ID, track from
        live prices. Live: call ZerodhaClient.place_order() and track
        the returned order ID. Returns True on success.

        Entry pipeline runs ~44 checks; STRATEGY_V2.md is the source of
        truth and lists each one. The numbered walkthrough below covers
        the in-this-method gates in code order; scanner-side filters
        (earnings blackout, pattern↔tech contradiction, tape-breadth)
        run before this method is called.
          0a. Rolling-PF circuit breaker — session pause on rolling 3d losses
          0b. Directional auto-pause + bypasses (NIFTY-bounce, tape-breadth)
          0c. Entry-burst cap — block 3rd+ entry inside any 60s window
          0.  Choppy-morning entry pause (NIFTY ADX < 16 + recent stagnant exits)
          1.  Lunch-lull skip — 11:30-12:15 IST unless |score|≥5.7
          2.  Daily-loss soft-stop (MTM-aware) at -1.5% of effective_day_pnl
          3.  Peak-drawdown stop (MTM-aware) at ≥1.5% give-back from intraday peak
          4.  Validate entry price vs live Zerodha quote
          5.  Circuit-limit (UC/LC) entry guard
          6.  Bid-ask spread check (illiquid stocks)
          7.  Volume confirmation (RVol with hour-bucket scaling)
          8.  Impact-cost check (depth-weighted slippage)
          9.  ATR-based SL/target
         10.  Late-entry target reduction (13:00 / 14:00 cutoffs)
         11.  R:R floor check (uniform 1.3 all day)
         12.  Minimum profit check (must cover round-trip charges)
         13.  Charge-aware target floor (gross target ≥ 3× charges)
         14.  Slippage simulation (dry-run only)
         15.  Budget cap
         16.  Max positions cap
         17.  Duplicate position guard (no two open on same symbol+side)
         18.  Sector cap (max 2 per sector)
         19.  Direction diversification
         20.  Short entry cutoff
         21.  Max re-entries per stock + declining score block
         22.  Per-symbol re-entry cooldown (30 min same-side)
         22b. Average-down prevention
         23.  RSI > BUY ceiling
         24.  RSI > SELL ceiling
         25.  RSI < BUY/SELL floor
         26.  Pattern-direction entry veto (opposite-side reversal pattern)
         27.  ADX + DI directional gate (chop-day reject)
         28.  Gap-coherence gate (BUY blocked on GAP_DOWN_STRONG)
         29.  Daily trade cap + expiry trade cap
         30.  Stagnant churn guard
         31.  VWAP guard (trend-fight + extension + fresh-reversal)
         32.  Net-of-charges R:R check (effective R:R ≥ 1.0)
         --- order placement ---
         33.  Place order → scale SL/target to actual fill price
         34.  Place exchange SL-M for instant stop-loss execution
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

        # ── Rolling-PF circuit breaker (Roadmap #253) ─────────────
        # Multi-day analogue of the intra-day soft-stop. Armed once at
        # session start by the manager via `arm_multiday_pauses()` when
        # the trailing N-day intraday_tax_ledger shows both a sub-0.5
        # profit factor AND a net loss exceeding ROLLING_PF_PAUSE_NET_FLOOR.
        # Existing positions managed normally. Kill-switch:
        # ROLLING_PF_PAUSE_ENABLED.
        if self.is_rolling_pf_paused():
            self.log.warning(
                f"{symbol}: rolling-PF pause active "
                f"({self._rolling_pf_pause_reason}) — new entries "
                f"blocked for the session. Skipping (existing "
                f"positions still managed)."
            )
            return False

        # ── Directional auto-pause (Roadmap #251) ─────────────────
        # Side-specific session-wide pause when the trailing-7-day
        # WR for `side` collapsed below threshold AND NIFTY's rolling
        # 7-day return is on the contra side. Armed by manager at
        # session start. Kill-switch: DIRECTIONAL_PAUSE_ENABLED.
        if self.is_directional_paused(side):
            self.log.warning(
                f"{symbol}: directional pause active for {side} "
                f"({self._directional_pause_reason}) — {side} entries "
                f"blocked for the session. Skipping (other-side and "
                f"existing positions still managed)."
            )
            return False

        # ── Opposing-thin fractional-Kelly cap (Roadmap #251a) ───
        # When the directional pause armed against the OTHER side based
        # on a thin opposing-side sample (typically the SELL n=14 case),
        # cap entries on this surviving side at OPPOSING_THIN_MAX_ENTRIES
        # per session. Reduces concentration risk on the un-validated
        # side per Kelly best-practice (Investopedia: 50-60 trades is the
        # typical lookback for win-prob estimation; binomial CI at n=14
        # is ±26pp). Kill-switch: DIRECTIONAL_PAUSE_ENABLED (shared with
        # #251 — disabling that gate also disables this one).
        if self.is_opposing_thin_capped(side):
            self.log.warning(
                f"{symbol}: opposing-thin cap reached for {side} "
                f"({self._opposing_thin_count}/{self._opposing_thin_max} "
                f"entries this session; {self._opposing_thin_reason}). "
                f"Skipping (existing positions still managed)."
            )
            return False

        # ── Entry-burst cap (Roadmap #179, budget-tiered #179a) ──
        # Hard cap on entries per rolling 60s. Same-direction sub-60s
        # bursts had ~92% lose-together correlation across 3 qualifying
        # days. Per-budget delta from BUDGET_BURST_CAP_DELTA tunes the
        # cap to account size. Kill-switch: ENTRY_BURST_CAP_ENABLED.
        if self.is_burst_capped(now):
            cap = self.effective_burst_cap()
            self.log.warning(
                f"{symbol}: entry-burst cap reached ({cap} entries in "
                f"trailing 60s). Skipping (existing positions still managed)."
            )
            return False

        # ── Choppy-morning entry pause (Roadmap #192) ─────────────
        # Pause new entries when NIFTY ADX has been weak for several
        # consecutive scans AND ≥2 entries already exited STAGNANT/
        # SIGNAL_DECAY in the last 10 min. Existing positions are
        # managed normally. Kill-switch: CHOPPY_MORNING_PAUSE_ENABLED.
        if self.is_choppy_morning_paused(now):
            until = self._choppy_pause_until
            self.log.warning(
                f"{symbol}: choppy-morning pause active — new entries "
                f"paused until {until.strftime('%H:%M:%S') if until else '?'}. "
                f"Skipping (existing positions still managed)."
            )
            return False

        # ── VIX intraday-spike pause (Roadmap #211) ───────────────
        # Skip new entries when India VIX has spiked ≥ VIX_SPIKE_PCT
        # vs day open. Manager already gates the opportunity / re-scan
        # paths via _check_vix_spike(); this closes the entry-path
        # hole so initial-scan / partial-rescan entries also honour
        # the pause. Existing positions managed normally. Kill-switch:
        # VIX_SPIKE_ENTRY_PAUSE_ENABLED.
        if (
            getattr(self.cfg, "VIX_SPIKE_ENTRY_PAUSE_ENABLED", True)
            and self.is_vix_spike_active()
        ):
            self.log.warning(
                f"{symbol}: VIX intraday-spike pause active — new entries "
                f"blocked until VIX retreats below "
                f"{self.cfg.VIX_SPIKE_PCT:.0f}% above day open. "
                f"Skipping (existing positions still managed)."
            )
            return False

        # ── Late-entry tightening — score floor (Roadmap #202, #246) ────
        # Past LATE_ENTRY_HOUR, demand a stricter |score| than the
        # base effective_min_score (regime-aware). The R:R floor and
        # max-positions cap are enforced by their own checks below
        # (current_rr_floor + max-positions block); this is the score
        # half. Skip when score data is missing (don't penalise the
        # tiny path where _entry_score is None — let other gates run).
        #
        # #246 (2026-04-28) couples this floor to the rescue-gate
        # floor: the effective late-entry minimum is
        #     max(base_min + late_bump, SIGNAL_DECAY_MIN_ENTRY_SCORE)
        # so the entry side cannot admit trades the rescue gates
        # cannot save (the "no-rescue zone"). The constant is REUSED
        # from `_signal_decay_exit` / `_signal_reversal_exit`
        # intentionally — no new threshold knob — so the two stay
        # coupled by code review. Kill-switch:
        # LATE_ENTRY_NO_RESCUE_FLOOR_ENABLED.
        if (
            getattr(self.cfg, "LATE_ENTRY_TIGHTENING_ENABLED", False)
            and now.hour >= int(self.cfg.LATE_ENTRY_HOUR)
            and trade.get("_entry_score") is not None
        ):
            base_min  = self.effective_min_score()
            late_bump = float(self.cfg.LATE_ENTRY_MIN_SCORE_BUMP)
            bump_min  = base_min + late_bump
            rescue_floor = float(
                getattr(self.cfg, "SIGNAL_DECAY_MIN_ENTRY_SCORE", 0.0)
            )
            no_rescue_active = bool(
                getattr(self.cfg, "LATE_ENTRY_NO_RESCUE_FLOOR_ENABLED", False)
            )
            late_min = max(bump_min, rescue_floor) if no_rescue_active else bump_min
            score_abs_late = abs(float(trade.get("_entry_score") or 0))
            if score_abs_late < late_min:
                # Tag whichever floor was binding so the rejection-audit
                # and EOD review can grade #246 separately from #202.
                if no_rescue_active and rescue_floor > bump_min and late_min == rescue_floor:
                    reason_tag = (
                        f"NO_RESCUE_ZONE (rescue floor {rescue_floor:.1f} "
                        f"> bump floor {bump_min:.1f})"
                    )
                else:
                    reason_tag = (
                        f"late-entry tightening "
                        f"(base {base_min:.1f} + late bump {late_bump:.1f})"
                    )
                self.log.warning(
                    f"{symbol}: {reason_tag} — |score| "
                    f"{score_abs_late:.1f} < {late_min:.1f}. "
                    f"Skipping (after {self.cfg.LATE_ENTRY_HOUR:02d}:00 IST, "
                    f"only entries the rescue gates can save should run)."
                )
                return False

        # ── Lunch-lull entry skip (Roadmap #164) ──────────────────
        # Indian markets are lowest-volume + lowest-ADX during lunch.
        # Skip new entries in the window unless score is strong enough
        # to justify the reduced edge. Kill-switch: LUNCH_LULL_ENABLED=False.
        if self.is_lunch_lull(now):
            score_abs = abs(trade.get("_entry_score", 0) or 0)
            override  = self.cfg.LUNCH_LULL_SCORE_OVERRIDE
            if score_abs < override:
                self.log.warning(
                    f"{symbol}: lunch-lull window "
                    f"{self.cfg.LUNCH_LULL_START_HOUR:02d}:"
                    f"{self.cfg.LUNCH_LULL_START_MINUTE:02d}-"
                    f"{self.cfg.LUNCH_LULL_END_HOUR:02d}:"
                    f"{self.cfg.LUNCH_LULL_END_MINUTE:02d} — |score| "
                    f"{score_abs:.1f} < {override:.1f} override. Skipping."
                )
                return False
            self.log.info(
                f"  ✓ {symbol}: lunch-lull bypass — |score| {score_abs:.1f} "
                f"≥ {override:.1f} override"
            )

        # ── Daily-loss soft stop (Roadmap #163) ───────────────────
        # Stop taking NEW entries once day P&L crosses the soft threshold.
        # Existing positions continue to be managed; hard circuit breaker
        # still closes all at MAX_LOSS_PER_DAY_PCT.
        if self.is_soft_stopped():
            self.log.warning(
                f"{symbol}: soft-stop active — day P&L Rs.{self.effective_day_pnl():,.2f} "
                f"≤ -{self.cfg.DAILY_LOSS_SOFT_STOP_PCT}% of budget. "
                f"No new entries (existing positions still managed)."
            )
            return False

        # ── Intraday equity-peak drawdown stop (Roadmap #168) ──────
        # Pause new entries once day P&L has given back PEAK_DRAWDOWN_STOP_PCT
        # of budget from its intraday high. Catches the "+2% by 11 AM,
        # bleed back to flat by 13:00" pattern that soft-stop misses.
        # Existing positions managed normally.
        if self.is_peak_drawdown_stopped():
            peak = getattr(self, "_intraday_peak_pnl", 0.0)
            give_back = peak - self.effective_day_pnl()
            self.log.warning(
                f"{symbol}: peak-drawdown stop active — day P&L gave back "
                f"Rs.{give_back:,.2f} from peak Rs.{peak:,.2f} "
                f"(≥ {self.cfg.PEAK_DRAWDOWN_STOP_PCT}% of budget). "
                f"No new entries (existing positions still managed)."
            )
            return False

        # ── Validate entry price against live quote ───────────────
        # Claude can hallucinate prices. Always use Zerodha as source of truth.
        max_spread = self.effective_max_spread()
        max_impact = self.cfg.MAX_IMPACT_COST_PCT
        required_depth_sides: set[str] = set()
        impact_side = None
        if not self.cfg.DRY_RUN:
            if max_spread > 0:
                required_depth_sides.update(("buy", "sell"))
            if max_impact > 0:
                impact_side = "sell" if side == "BUY" else "buy"
                required_depth_sides.add(impact_side)

        live_quotes, quote_data = self._fetch_entry_quote(
            symbol,
            exchange,
            required_depth_sides,
            require_spread_book=max_spread > 0 and not self.cfg.DRY_RUN,
            impact_side=impact_side,
        )

        live_price = quote_data.get("last_price", 0) if isinstance(quote_data, dict) else 0
        if live_price <= 0:
            self.log.warning(
                f"{symbol}: live quote still missing/invalid after 3 attempts — skipping."
            )
            return False
        if required_depth_sides and any(
            not self._depth_levels(quote_data, depth_side)
            for depth_side in required_depth_sides
        ):
            self.log.warning(
                f"{symbol}: order-book depth still missing after 3 attempts — skipping."
            )
            return False

        deviation = abs(entry - live_price) / live_price
        if deviation > 0.05:
            self.log.warning(
                f"Entry price override: {symbol} plan Rs.{entry:.2f} "
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

        # ── Circuit-limit (UC/LC) entry guard (Roadmap #180) ──────
        # Refuse entries within CIRCUIT_LIMIT_BUFFER_PCT of the ±20%
        # daily price band. Near the freeze, exits cannot fill (one
        # side of the order book is empty); SL-M sits dead and MIS
        # auto-square at 15:20 takes whatever distressed price exists.
        # Fail-open if prev_close missing.
        if (
            self.cfg.CIRCUIT_LIMIT_GUARD_ENABLED
            and live_price > 0
        ):
            quote_data = live_quotes.get(f"{exchange}:{symbol}", {})
            prev_close = (quote_data.get("ohlc", {}) or {}).get("close", 0)
            if prev_close > 0:
                move_pct = (live_price - prev_close) / prev_close * 100
                limit_pct = 20.0 - self.cfg.CIRCUIT_LIMIT_BUFFER_PCT
                if side == "BUY" and move_pct >= limit_pct:
                    self.log.warning(
                        f"{symbol}: BUY blocked — move {move_pct:+.2f}% from "
                        f"prev close Rs.{prev_close:.2f} is within "
                        f"{self.cfg.CIRCUIT_LIMIT_BUFFER_PCT:.1f}% of upper "
                        f"circuit (+20%). Liquidity dries near freeze; "
                        f"exits unreliable. Skipping."
                    )
                    return False
                if side == "SELL" and move_pct <= -limit_pct:
                    self.log.warning(
                        f"{symbol}: SELL blocked — move {move_pct:+.2f}% from "
                        f"prev close Rs.{prev_close:.2f} is within "
                        f"{self.cfg.CIRCUIT_LIMIT_BUFFER_PCT:.1f}% of lower "
                        f"circuit (-20%). Liquidity dries near freeze; "
                        f"exits unreliable. Skipping."
                    )
                    return False

        # ── Bid-ask spread check ──────────────────────────────────
        # #236: budget-adaptive cap via effective_max_spread() so
        # small-budget accounts (where spread alone can rival the
        # charge hurdle) get a stricter cap automatically.
        if max_spread > 0 and not self.cfg.DRY_RUN:
            buy_depth = self._depth_levels(quote_data, "buy")
            sell_depth = self._depth_levels(quote_data, "sell")
            best_bid = self._level_number(buy_depth[0] if buy_depth else {}, "price")
            best_ask = self._level_number(sell_depth[0] if sell_depth else {}, "price")
            if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
                self.log.warning(
                    f"{symbol}: bid-ask depth unavailable or malformed after 3 attempts — skipping."
                )
                return False
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

        # ── Impact-cost / depth liquidity check (Roadmap #146) ────
        # Walk top-5 depth and reject when the full qty cannot fill inside
        # MAX_IMPACT_COST_PCT. Missing depth is not a tradable state.
        if max_impact > 0 and not self.cfg.DRY_RUN:
            book_side = self._depth_levels(quote_data, "sell" if side == "BUY" else "buy")
            ltp = quote_data.get("last_price", entry) or entry
            if not book_side:
                self.log.warning(
                    f"{symbol}: impact-cost depth unavailable after 3 attempts — skipping."
                )
                return False
            try:
                remaining = int(qty)
                filled_notional = 0.0
                filled_qty = 0
                for level in (book_side or []):
                    if remaining <= 0:
                        break
                    lvl_price = self._level_number(level, "price")
                    lvl_qty   = self._level_number(level, "quantity")
                    if lvl_price <= 0 or lvl_qty <= 0:
                        continue
                    take = min(remaining, int(lvl_qty))
                    filled_notional += take * lvl_price
                    filled_qty      += take
                    remaining       -= take

                if filled_qty == 0 or ltp <= 0:
                    self.log.warning(
                        f"{symbol}: impact-cost depth malformed after 3 attempts — skipping."
                    )
                    return False
                elif remaining > 0:
                    # Not enough visible depth in top-5 for our full qty.
                    # This is a strong liquidity signal — treat as a skip.
                    visible = filled_qty
                    self.log.warning(
                        f"{symbol}: insufficient visible depth for {qty} shares "
                        f"(only {visible} available in top-5 levels) — skipping"
                    )
                    return False
                else:
                    avg_fill = filled_notional / filled_qty
                    # BUY: worse = higher than LTP; SELL: worse = lower than LTP
                    if side == "BUY":
                        impact_pct = (avg_fill - ltp) / ltp * 100
                    else:
                        impact_pct = (ltp - avg_fill) / ltp * 100
                    # Negative means our side of the book is BETTER than LTP
                    # (can happen right after a print) — floor at 0 for display.
                    impact_display = max(impact_pct, 0.0)
                    if impact_pct > max_impact:
                        self.log.warning(
                            f"{symbol}: impact cost {impact_display:.2f}% exceeds "
                            f"MAX_IMPACT_COST_PCT ({max_impact}%) — skipping "
                            f"(qty {qty} would fill @ Rs.{avg_fill:.2f} vs LTP Rs.{ltp:.2f})"
                        )
                        return False
                    self.log.info(
                        f"  ✓ {symbol}: impact cost {impact_display:.2f}% OK "
                        f"(avg fill Rs.{avg_fill:.2f} vs LTP Rs.{ltp:.2f})"
                    )
            except Exception as e:
                self.log.warning(
                    f"{symbol}: impact-cost depth parse failed "
                    f"({type(e).__name__}: {e}) after 3 attempts — skipping."
                )
                return False

        # ── Volume confirmation at entry ──────────────────────────
        # Skip stocks with below-average recent volume — low conviction.
        # Note: Kite quote API returns "volume" (today's traded qty) and
        # "average_price" (VWAP), but NOT "average_volume". The field
        # doesn't exist in Kite's response so avg_volume is always 0.
        # We fall back to scan-time RVol from the indicator snapshot.
        if not self.cfg.DRY_RUN:
            # Session-time-aware RVol floor (Roadmap #147). NSE intraday
            # volume is U-shaped \u2014 the linear-prorated RVol over-rejects
            # midday and under-rejects opens/closes. Scale 0.7\u00d7 floor by
            # the hour bucket. Falls back to 1.0\u00d7 outside table or when
            # disabled.
            rvol_floor = 0.7
            if self.cfg.RVOL_TIME_NORMALIZATION_ENABLED:
                hour_now = now_ist().hour
                bucket = self.cfg.RVOL_FLOOR_BY_HOUR.get(hour_now, 1.0)
                rvol_floor = round(0.7 * bucket, 2)
            quote_data = live_quotes.get(f"{exchange}:{symbol}", {})
            live_volume = quote_data.get("volume", 0)
            avg_volume = quote_data.get("average_volume", 0)
            if avg_volume > 0 and live_volume > 0:
                rvol = live_volume / avg_volume
                if rvol < rvol_floor:
                    self.log.warning(
                        f"{symbol}: live RVol {rvol:.1f}x (< {rvol_floor:.2f}x avg) — "
                        f"low volume, skipping entry"
                    )
                    return False
                self.log.info(f"  ✓ {symbol}: RVol {rvol:.1f}x OK (\u2265{rvol_floor:.2f}x)")
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
                if scan_rvol > 0 and scan_rvol < rvol_floor:
                    self.log.warning(
                        f"{symbol}: scan RVol {scan_rvol:.1f}x (< {rvol_floor:.2f}x) — "
                        f"low volume at scan time, skipping entry"
                    )
                    return False
                elif scan_rvol > 0:
                    self.log.info(f"  ✓ {symbol}: scan RVol {scan_rvol:.1f}x OK (\u2265{rvol_floor:.2f}x, live avg unavailable)")
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

        # NOTE (Roadmap #242, 2026-04-27): the late-entry target reduction
        # block (20% cut after 1 PM, 25% after 2 PM) was removed. With the
        # always-on `RR_HARD_FLOOR = 1.3` (#225), default-ATR trades (raw
        # R:R = 1.5) cut to 1.20 / 1.125 fell below the floor and were
        # systematically rejected after 1 PM — a self-defeating loop where
        # we engineered the target down then rejected for low R:R. The
        # underlying concern (unreachable targets) is already addressed by
        # stagnant-exit Tier-1/2 (#172), momentum kill (#198), time-decay
        # target compression (TARGET_DECAY_PCT, separate mechanism for
        # *open* positions), and the hard square-off at 3:10 PM. Pro
        # intraday desks don't compress entry targets; they let
        # stop/timing rules close drifters.

        # ── ATR-based position sizing (Roadmap #145) ──────────────
        # Reduce qty for high-volatility stocks so every trade risks
        # approximately the same rupee amount. If a low-ATR stock gets
        # 50 shares with a Rs.1 stop (= Rs.50 risk) while a high-ATR
        # stock gets 50 shares with a Rs.5 stop (= Rs.250 risk), a
        # single SL hit on the latter wipes out 5 winners on the former.
        # We never INCREASE qty — only cap it so risk ≤ RISK_PER_TRADE_PCT
        # of budget. Price-based qty remains the upper bound.
        #
        # NOTE: sized by ACTUAL sl_distance (|entry-sl|), not by
        # ATR×multiplier. If the SL was overridden to a candle low the
        # actual rupee risk is what matters — the ATR path is just the
        # common case.
        sl_distance = abs(entry - sl)
        if self.cfg.ATR_SIZING_ENABLED and sl_distance > 0 and qty > 0:
            risk_rupees = self._budget * self.cfg.RISK_PER_TRADE_PCT / 100
            risk_qty = int(risk_rupees / sl_distance)
            if risk_qty < 1:
                # Even 1 share would exceed risk budget — this is a
                # very volatile or very high-priced stock. Skip.
                self.log.warning(
                    f"{symbol}: 1 share has risk Rs.{sl_distance:.2f} > "
                    f"risk budget Rs.{risk_rupees:.0f} per trade. Skipping "
                    f"(ATR sizing). Disable ATR_SIZING_ENABLED to override."
                )
                return False
            if risk_qty < qty:
                self.log.info(
                    f"  ✓ {symbol}: ATR sizing — qty reduced {qty} → "
                    f"{risk_qty} (risk Rs.{risk_qty * sl_distance:.0f} of "
                    f"Rs.{risk_rupees:.0f} budget per trade)"
                )
                qty = risk_qty
                trade["qty"] = qty

        # ── R:R safety floor (time-aware + adaptive) ─────────────
        # One unified check. Time-of-day labels remain (morning /
        # afternoon / late) for log readability, but with the always-on
        # RR_HARD_FLOOR (#225, 1.3) the effective floor is 1.3 across
        # the entire trading day. Adaptive relaxation is also pinned
        # to 1.3 (#235), so the relaxation/retry steps are no-ops in
        # practice. The hard floor is the structural correctness gate.
        hour_now = now.hour
        rr_floor = self.current_rr_floor(hour=hour_now)
        floor_label = self._rr_floor_label(hour=hour_now)
        sl_dist = abs(entry - sl)
        tgt_dist = abs(target - entry)

        if sl_dist > 0 and tgt_dist / sl_dist < rr_floor:
            actual_rr = tgt_dist / sl_dist
            self._rr_rejection_count += 1
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
        # #237: budget-adaptive via effective_min_profit() so larger
        # accounts (where per-slot charges grow) raise the bar.
        min_profit = self.effective_min_profit()
        expected_profit = abs(target - entry) * qty
        if expected_profit < min_profit:
            self.log.warning(
                f"{symbol}: expected profit Rs.{expected_profit:.0f} "
                f"< min Rs.{min_profit:.0f} (charges will eat it). Skipping."
            )
            return False
        self.log.info(
            f"  ✓ {symbol}: expected profit Rs.{expected_profit:.0f} OK "
            f"(min Rs.{min_profit:.0f})"
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
        #
        # Roadmap #171: live mode must use the LOWER of:
        #   (a) configured cap minus current exposure  — respects MAX_BUDGET
        #       and loss-sizing
        #   (b) Zerodha available funds                — already reflects
        #       margin blocked by open positions; do NOT subtract exposure
        #       again or we double-count it (the bug that blocked TRENT
        #       on 2026-04-20).
        cost = entry * qty
        current_exposure = self._total_open_exposure()
        cap_remaining = self.loss_adjusted_budget() - current_exposure
        if not self.cfg.DRY_RUN and self._available_funds is not None:
            remaining = min(cap_remaining, self._available_funds)
        else:
            remaining = cap_remaining
        if cost > remaining:
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
        max_pos_cap = int(self.cfg.MAX_POSITIONS)
        # NOTE (#225): the late-entry-only concurrency cap was removed.
        # dynamic_max_positions(budget) (set on engine init / set_budget)
        # already scales the cap with account size all day; the late-only
        # cap was budget-disproportionate and rarely bound in practice.
        if open_count >= max_pos_cap:
            ext_count = len([p for p in self.positions if p["status"] == "OPEN" and p.get("_external")])
            bot_count = open_count - ext_count
            msg = f"Cannot enter {symbol}: already at max {max_pos_cap} positions"
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

            # Block re-entry when score is declining (setup weakening).
            # Only compare against PRIOR ENTRIES ON THE SAME SIDE — a flip from
            # SELL @ -8.7 to BUY @ +7.0 is a different setup (the market
            # reversed), not a weakening of the original thesis. Opposite-side
            # re-entries bypass this gate AND the per-symbol cooldown #161
            # (which is keyed by SYMBOL_SIDE); they're protected by the
            # standard entry gates (ADX, RSI, VWAP, gap-coherence). (#185)
            same_side_past = [
                p for p in past_entries if p.get("side") == side
            ]
            if same_side_past:
                entry_score = abs(trade.get("_entry_score") or 0)
                prev_score = max(
                    abs(p.get("_entry_score") or 0) for p in same_side_past
                )
                if entry_score < prev_score and prev_score > 0:
                    self.log.warning(
                        f"{symbol}: re-entry score {entry_score:.1f} < "
                        f"previous {prev_score:.1f} same-side (setup weakening) — "
                        f"skipping declining re-entry"
                    )
                    return False

        # ── RSI contradiction filter (symmetric) ──────────────────
        # Block chasing RSI extremes in either direction:
        #   SELL blocked when RSI > RSI_SELL_BLOCK_THRESHOLD (default 70)
        #     — shorting into strong buying pressure.
        #   BUY blocked when RSI > RSI_BUY_BLOCK_THRESHOLD (default 75)
        #     — buying an already-extended overbought move.
        #   BUY blocked when RSI < 30 — buying into strong selling.
        #   SELL blocked when RSI < 25 — shorting an already-extended oversold move.
        entry_rsi = trade.get("_entry_rsi", 0) or 0
        if entry_rsi > 0:
            rsi_sell_max = self.cfg.RSI_SELL_BLOCK_THRESHOLD
            rsi_buy_max  = self.cfg.RSI_BUY_BLOCK_THRESHOLD
            if side == "SELL" and entry_rsi > rsi_sell_max:
                self.log.warning(
                    f"{symbol}: RSI {entry_rsi:.0f} > {rsi_sell_max:.0f} — too overbought to "
                    f"short (strong buying pressure). Skipping."
                )
                return False
            if side == "BUY" and entry_rsi > rsi_buy_max:
                self.log.warning(
                    f"{symbol}: RSI {entry_rsi:.0f} > {rsi_buy_max:.0f} — BUY chasing "
                    f"extended overbought move. Skipping."
                )
                return False
            if side == "BUY" and entry_rsi < 30:
                self.log.warning(
                    f"{symbol}: RSI {entry_rsi:.0f} < 30 — too oversold to "
                    f"buy (strong selling pressure). Skipping."
                )
                return False
            if side == "SELL" and entry_rsi < 25:
                self.log.warning(
                    f"{symbol}: RSI {entry_rsi:.0f} < 25 — SELL chasing "
                    f"extended oversold move. Skipping."
                )
                return False

        # ── Pattern-direction entry veto (Roadmap #190) ───────────
        # Mirror of #174 SIGNAL_REVERSAL exit, applied at ENTRY.
        # If the entry-tick patterns include an opposite-side reversal
        # (e.g. BUY with BEARISH_ENGULFING) AND |score| is below the
        # high-conviction override, skip. Patterns flow into score as
        # weighted contributions but borderline scores can clear the
        # main gate even when the chart is printing a flip pattern.
        # Empirical: PNB BUY @ +6.1 / TRENT BUY @ +6.4 both with
        # BEARISH_ENGULFING on 2026-04-22 \u2014 both stagnant losers.
        if self.cfg.PATTERN_VETO_ENABLED:
            entry_patterns = trade.get("_entry_patterns") or []
            if entry_patterns:
                pset = {str(p).upper() for p in entry_patterns}
                opposite = (
                    self._BEARISH_REVERSAL_PATTERNS if side == "BUY"
                    else self._BULLISH_REVERSAL_PATTERNS
                )
                conflicts = pset & opposite
                if conflicts:
                    score_abs_pv = abs(trade.get("_entry_score", 0) or 0)
                    if score_abs_pv < self.cfg.PATTERN_VETO_OVERRIDE_SCORE:
                        self.log.warning(
                            f"{symbol}: {side} pattern {sorted(conflicts)[0]} "
                            f"contradicts direction (score {score_abs_pv:.1f} "
                            f"< {self.cfg.PATTERN_VETO_OVERRIDE_SCORE:.1f} override). "
                            f"Skipping."
                        )
                        return False

        # ── ADX + DI directional gate (Roadmap #157) ──────────────
        # Reject entries on chop days (ADX below threshold) UNLESS the
        # combined score is strong enough to override (big conviction).
        # Also reject when DI direction disagrees with the trade side —
        # e.g. trying to BUY while -DI > +DI means sellers are dominant.
        # Fails open when ADX is missing (treat as pass).
        if self.cfg.ADX_ENTRY_GATE_ENABLED:
            entry_adx = trade.get("_entry_adx", 0) or 0
            plus_di   = trade.get("_entry_plus_di", 0) or 0
            minus_di  = trade.get("_entry_minus_di", 0) or 0
            score_abs = abs(trade.get("_entry_score", 0) or 0)
            adx_override_score = self.effective_adx_override_score(side)
            override  = score_abs >= adx_override_score

            if entry_adx > 0:  # only enforce if ADX was measured
                adx_threshold = self.effective_adx_threshold(side)
                if entry_adx < adx_threshold and not override:
                    self.log.warning(
                        f"{symbol}: ADX {entry_adx:.1f} < {adx_threshold:.1f} "
                        f"(chop, regime={self.budget_regime()}) and |score| "
                        f"{score_abs:.1f} < {adx_override_score} override "
                        f"— skipping. Entry likely to churn out."
                    )
                    return False
                # Directional disagreement (only meaningful when both DI present)
                if plus_di > 0 and minus_di > 0 and not override:
                    if side == "BUY" and minus_di > plus_di:
                        self.log.warning(
                            f"{symbol}: BUY but -DI {minus_di:.1f} > +DI {plus_di:.1f} "
                            f"(sellers dominant). Skipping."
                        )
                        return False
                    if side == "SELL" and plus_di > minus_di:
                        self.log.warning(
                            f"{symbol}: SELL but +DI {plus_di:.1f} > -DI {minus_di:.1f} "
                            f"(buyers dominant). Skipping."
                        )
                        return False
                self.log.info(
                    f"  ✓ {symbol}: ADX gate OK — ADX {entry_adx:.1f}, "
                    f"+DI {plus_di:.1f} / -DI {minus_di:.1f}"
                )

        # ── Gap-coherence gate (#173) ─────────────────────────────
        # Pro-desk practice: opening gap direction reflects overnight
        # institutional positioning + the first wave of regular-session
        # flow. Taking a BUY on a STRONG gap-DOWN (or SELL on STRONG
        # gap-UP) means trading against that flow; intraday V-recoveries
        # of strong gaps are the exception, not the rule.
        # Block such contradictory entries unless |score| is very high
        # (signals an exceptional setup that justifies fighting flow).
        # Only acts on the high-conviction GAP_*_STRONG signals — WEAK
        # gaps (low-volume) and NO_GAP are not gated here. Fails open
        # when the snapshot is missing/malformed (other gates remain
        # active).
        if getattr(self.cfg, "GAP_COHERENCE_GATE_ENABLED", False):
            snap_str = trade.get("_indicator_snapshot", "")
            if snap_str:
                try:
                    import json as _json
                    snap = _json.loads(snap_str)
                    gap_signal = snap.get("gap", "NO_GAP")
                    score_abs  = abs(trade.get("_entry_score") or 0)
                    override   = self.cfg.GAP_COHERENCE_OVERRIDE_SCORE
                    contradicts = (
                        (side == "BUY"  and gap_signal == "GAP_DOWN_STRONG") or
                        (side == "SELL" and gap_signal == "GAP_UP_STRONG")
                    )
                    if contradicts and score_abs < override:
                        self.log.warning(
                            f"{symbol}: {side} contradicts {gap_signal} — "
                            f"|score| {score_abs:.1f} < {override:.1f} override. Skipping."
                        )
                        return False
                    if contradicts:
                        self.log.info(
                            f"  ✓ {symbol}: gap-coherence override — {side} on "
                            f"{gap_signal} allowed at |score| {score_abs:.1f} ≥ {override:.1f}"
                        )
                except Exception as e:
                    self.log.warning(
                        f"{symbol}: gap-coherence gate skipped — indicator snapshot "
                        f"parse failed ({type(e).__name__}: {e})"
                    )

        # ── Daily trade cap ───────────────────────────────────────
        # Prevent overtrading churn. Each exit+entry costs ~Rs.36.
        # Intentionally counts EXTERNAL/adopted positions too — manual
        # trades on Zerodha still use slots and add to daily churn.
        # Regime-adjusted (Roadmap #165): tighter for small accounts,
        # looser for large ones.
        max_daily = self.effective_trade_cap()
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

        # ── Per-symbol re-entry cooldown (Roadmap #161) ───────────
        # Block re-entry in the same direction for RE_ENTRY_COOLDOWN_MINUTES
        # after ANY exit (SL, target, external, stagnant). Stops the
        # "re-enter immediately on same signal" loop. Opposite direction
        # (reversal setups) is still allowed. Very strong score bypasses.
        if self.is_in_re_entry_cooldown(symbol, side, now):
            score_abs = abs(trade.get("_entry_score", 0) or 0)
            override  = self.cfg.RE_ENTRY_SCORE_OVERRIDE
            last = self._last_exit_time.get(f"{symbol}_{side}")
            mins_ago = (now - last).total_seconds() / 60 if last else 0
            if score_abs < override:
                self.log.warning(
                    f"{symbol}: re-entry cooldown — exited {mins_ago:.1f} min ago "
                    f"(window {self.cfg.RE_ENTRY_COOLDOWN_MINUTES} min), "
                    f"|score| {score_abs:.1f} < {override:.1f} override. Skipping."
                )
                return False
            self.log.info(
                f"  ✓ {symbol}: re-entry cooldown bypass — exited {mins_ago:.1f} min ago, "
                f"|score| {score_abs:.1f} ≥ {override:.1f} override"
            )

        # ── Average-down prevention (Roadmap #195) ────────────────
        # After the 30-min cooldown expires, a fresh same-direction
        # signal at the same magnitude as the prior STAGNANT/DECAY
        # exit is essentially chasing the same false signal twice —
        # no new information has arrived. Block such re-entries
        # within AVG_DOWN_LOOKBACK_MINUTES of the prior chop exit.
        # Override at |score| >= AVG_DOWN_OVERRIDE_SCORE so genuine
        # high-conviction reversals still get through.
        # Kill-switch: AVG_DOWN_PREVENTION_ENABLED.
        if getattr(self.cfg, "AVG_DOWN_PREVENTION_ENABLED", False):
            last_exit = self._last_exit_score.get(f"{symbol}_{side}")
            if last_exit:
                last_reason = last_exit.get("reason", "")
                last_score  = float(last_exit.get("score", 0) or 0)
                last_time   = last_exit.get("time")
                lookback    = int(self.cfg.AVG_DOWN_LOOKBACK_MINUTES)
                within_lookback = (
                    last_time is not None
                    and (now - last_time).total_seconds() <= lookback * 60
                )
                # Skip the gate when last_score is effectively zero — we have
                # no real magnitude to compare against, and abs(new_score - 0)
                # would spuriously block any low-score fresh signal.
                has_meaningful_last_score = abs(last_score) >= 0.5
                if (
                    last_reason in ("STAGNANT_EXIT", "SIGNAL_DECAY")
                    and within_lookback
                    and has_meaningful_last_score
                ):
                    new_score = float(trade.get("_entry_score") or 0)
                    delta = abs(new_score - last_score)
                    abs_override = float(self.cfg.AVG_DOWN_OVERRIDE_SCORE)
                    if (
                        delta <= float(self.cfg.AVG_DOWN_SCORE_DELTA)
                        and abs(new_score) < abs_override
                    ):
                        mins_ago = (now - last_time).total_seconds() / 60
                        self.log.warning(
                            f"{symbol}: same-magnitude re-signal after "
                            f"{last_reason} {mins_ago:.0f} min ago — "
                            f"prev score {last_score:+.1f}, new {new_score:+.1f} "
                            f"(Δ {delta:.1f} ≤ {self.cfg.AVG_DOWN_SCORE_DELTA:.1f}). "
                            f"Skipping average-down."
                        )
                        return False

        # ── VWAP trend + extension block ──────────────────────────
        # Two-sided guard:
        #   1. Trend-fight: don't BUY below VWAP / SELL above VWAP
        #      (fighting institutional flow).
        #   2. Extension-chase: don't BUY when already far ABOVE VWAP /
        #      SELL when already far BELOW VWAP — the move has happened,
        #      mean-reversion risk is high. Override allowed when score
        #      magnitude is very strong (VWAP_EXT_SCORE_OVERRIDE).
        # Skip before 10:15 — VWAP needs at least a full hour of candles
        # to be stable; early readings swing wildly on low volume.
        entry_score_abs = abs(trade.get("_entry_score") or 0)
        if now.hour > 10 or (now.hour == 10 and now.minute >= 15):
            snap_str = trade.get("_indicator_snapshot", "")
            if snap_str:
                try:
                    import json as _json
                    snap = _json.loads(snap_str)
                    vwap_dev = snap.get("vwap_dev", 0)
                    ext_cap = self.cfg.VWAP_EXTENSION_BLOCK_PCT
                    ext_override = self.cfg.VWAP_EXT_SCORE_OVERRIDE
                    if vwap_dev != 0:
                        # 1. Trend-fight
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
                        # 2. Extension-chase (skip when score very strong)
                        if entry_score_abs < ext_override:
                            if side == "BUY" and vwap_dev > ext_cap:
                                self.log.warning(
                                    f"{symbol}: price {vwap_dev:+.1f}% above VWAP — "
                                    f"BUY chasing extended move (score {entry_score_abs:.1f} < "
                                    f"{ext_override:.1f} override). Skipping."
                                )
                                return False
                            if side == "SELL" and vwap_dev < -ext_cap:
                                self.log.warning(
                                    f"{symbol}: price {vwap_dev:+.1f}% below VWAP — "
                                    f"SELL chasing extended move (score {entry_score_abs:.1f} < "
                                    f"{ext_override:.1f} override). Skipping."
                                )
                                return False
                        # 3. Fresh-reversal guard: if score just swung hard
                        # (large |delta|), wait one scan cycle for confirmation.
                        score_delta = snap.get("score_delta")
                        if score_delta is not None:
                            fresh_cap = self.cfg.FRESH_REVERSAL_DELTA_THRESHOLD
                            if abs(score_delta) >= fresh_cap:
                                self.log.warning(
                                    f"{symbol}: score Δ {score_delta:+.1f} >= {fresh_cap:.0f} — "
                                    f"fresh reversal, waiting one cycle for confirmation. Skipping."
                                )
                                return False
                except Exception as e:
                    # VWAP guard is a safety check — if the snapshot is malformed
                    # we log it (don't want to silently skip protection) but still
                    # allow the trade. R:R and other checks remain active.
                    self.log.warning(
                        f"{symbol}: VWAP guard skipped — indicator snapshot "
                        f"parse failed ({type(e).__name__}: {e})"
                    )

        # ── VWAP statistical-band gate (Roadmap #201) ─────────────
        # Buying ≥+1σ above VWAP (or selling ≤-1σ below) means entering
        # at the top/bottom of the intraday range — mean-reversion risk
        # is high, no room to run before the natural pullback. The
        # extension-chase guard above uses a fixed 0.8% cap; this gate
        # is volatility-adaptive (uses each stock's own intraday std).
        # Reads `vwap_band` from the indicator snapshot:
        #   AT_UPPER_1SD / AT_UPPER_2SD → BUY blocked
        #   AT_LOWER_1SD / AT_LOWER_2SD → SELL blocked
        # Override at |score| ≥ VWAP_BAND_OVERRIDE_SCORE.
        # Fail-open if snapshot missing/malformed.
        # Kill-switch: VWAP_BAND_GATE_ENABLED.
        if getattr(self.cfg, "VWAP_BAND_GATE_ENABLED", False):
            snap_str = trade.get("_indicator_snapshot", "")
            if snap_str:
                try:
                    import json as _json_band
                    snap = _json_band.loads(snap_str)
                    vwap_band = str(snap.get("vwap_band", "INSIDE")).upper()
                    score_abs_band = abs(trade.get("_entry_score") or 0)
                    band_override = float(self.cfg.VWAP_BAND_OVERRIDE_SCORE)
                    upper_bands = {"AT_UPPER_1SD", "AT_UPPER_2SD"}
                    lower_bands = {"AT_LOWER_1SD", "AT_LOWER_2SD"}
                    contradicts_band = (
                        (side == "BUY"  and vwap_band in upper_bands)
                        or (side == "SELL" and vwap_band in lower_bands)
                    )
                    if contradicts_band and score_abs_band < band_override:
                        self.log.warning(
                            f"{symbol}: {side} blocked at VWAP band "
                            f"{vwap_band} — entering top/bottom of range "
                            f"(|score| {score_abs_band:.1f} < "
                            f"{band_override:.1f} override). Skipping."
                        )
                        return False
                    if contradicts_band:
                        self.log.info(
                            f"  ✓ {symbol}: VWAP-band override — {side} at "
                            f"{vwap_band} allowed at |score| "
                            f"{score_abs_band:.1f} ≥ {band_override:.1f}"
                        )
                except Exception as e:
                    self.log.warning(
                        f"{symbol}: VWAP-band gate skipped — snapshot "
                        f"parse failed ({type(e).__name__}: {e})"
                    )

        # ── Net-of-charges R:R check ──────────────────────────────
        # Gross R:R may look 1.5:1, but after charges on small positions
        # the effective R:R can be much worse. Ensure net profit > 1.0× net risk.
        if qty > 0 and entry > 0:
            gross_profit = abs(target - entry) * qty
            gross_risk = abs(entry - sl) * qty
            if side == "BUY":
                buy_val = entry * qty
                sell_val = target * qty
            else:
                sell_val = entry * qty
                buy_val = target * qty
            charges = Config.calculate_charges(buy_val, sell_val, 2)
            round_trip_charges = charges["total_tax_and_charges"]
            net_profit = gross_profit - round_trip_charges
            net_risk = gross_risk + round_trip_charges
            if net_risk > 0 and net_profit / net_risk < 1.0:
                self._rr_rejection_count += 1
                self.log.warning(
                    f"{symbol}: net-of-charges R:R {net_profit / net_risk:.2f}:1 "
                    f"< 1.0:1 (charges Rs.{round_trip_charges:.0f} eat the edge). Skipping."
                )
                return False

            # ── Charge-aware minimum target (Roadmap #162) ────────
            # Even if net R:R passes, reject when gross target profit
            # doesn't clear round-trip charges by a comfortable margin.
            # Prevents tiny-target trades where a Rs.4 charge on Rs.10
            # expected profit leaves Rs.6 for all the slippage + risk.
            multiple = float(self.cfg.MIN_PROFIT_CHARGE_MULTIPLE)
            if multiple > 0 and gross_profit < round_trip_charges * multiple:
                self.log.warning(
                    f"{symbol}: gross target profit Rs.{gross_profit:.2f} < "
                    f"{multiple:.1f}× round-trip charges Rs.{round_trip_charges:.2f} "
                    f"— target too thin after costs. Skipping."
                )
                return False

        # ── All pre-trade checks passed ───────────────────────────
        sl_pct_final = abs(entry - sl) / entry * 100
        tgt_pct_final = abs(target - entry) / entry * 100
        regime_tag = f" [regime={self.budget_regime()}]" if self.cfg.BUDGET_REGIME_ENABLED else ""
        self.log.info(
            f"  ✓ {symbol}: ALL CHECKS PASSED{regime_tag} — {side} {qty}x @ Rs.{entry:.2f} | "
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
        else:
            # NOTE: we no longer early-return when _order_api_broken is True.
            # A single transient network glitch should not kill the entire
            # trading day. Instead, we attempt the order; success clears the
            # flag, failure re-increments the counter and re-trips if it's
            # genuinely broken. Exit paths behave the same way.
            if self._order_api_broken:
                self.log.warning(
                    f"Retrying after {self._consecutive_order_failures} prior "
                    f"failure(s) \u2014 attempting entry for {symbol}"
                )
            try:
                order_id = self._place_entry_order(symbol, exchange, qty, side, entry)
                # Order succeeded — reset failure counter and clear broken flag
                # (allows recovery from transient API glitches without killing the day).
                if self._consecutive_order_failures > 0 or self._order_api_broken:
                    self.log.info(
                        f"Order API recovered after {self._consecutive_order_failures} "
                        f"failure(s) — resuming normal operation"
                    )
                self._consecutive_order_failures = 0
                self._order_api_broken = False

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
            # Store initial SL at entry for correct trailing risk calculation
            "initial_sl": sl,
            # Roadmap #152: flag set True if exchange SL-M placement fails.
            # Defaults False — overwritten below if SL-M path runs.
            "_sl_m_failed": False,
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
                    position["_sl_m_failed"] = False
                    self._pending_order_ids.add(sl_order_id)  # Track for cleanup at market close
                    self.log.info(
                        f"Exchange SL-M placed for {symbol}: {sl_side} {qty}x "
                        f"trigger Rs.{sl:.2f} | ID: {sl_order_id}"
                    )
                else:
                    # Roadmap #152: loud alert on silent SL-M failure.
                    # User must see this — exchange-side protection is NOT in place.
                    position["_sl_order_id"] = None
                    position["_sl_m_failed"] = True
                    self.log.error(
                        f"*** EXCHANGE SL-M FAILED for {symbol} *** "
                        f"Zerodha returned no order ID. "
                        f"Position is protected only by software SL monitoring — "
                        f"a bot crash before exit would leave this position NAKED. "
                        f"A restart on a later trading day is NOT safe for this position."
                    )
            except Exception as e:
                # Roadmap #152: loud alert on silent SL-M failure.
                position["_sl_order_id"] = None
                position["_sl_m_failed"] = True
                self.log.error(
                    f"*** EXCHANGE SL-M FAILED for {symbol} *** "
                    f"Exception: {type(e).__name__}: {e}. "
                    f"Position is protected only by software SL monitoring — "
                    f"a bot crash before exit would leave this position NAKED. "
                    f"A restart on a later trading day is NOT safe for this position."
                )

        self.positions.append(position)
        self._log_action("ENTRY", symbol, side, qty, entry, rationale)
        # Stamp the entry timestamp for the burst-cap window (#179).
        # Done at the very end so only successful entries count toward
        # the rolling-60s cap; rejected attempts are not penalised.
        # Side passed in to drive the #251a opposing-thin counter.
        self.record_entry(now, side=side)
        return True

    # ================================================================
    # EXIT — CLOSE A POSITION
    # ================================================================

    def _fetch_fill_price_with_retry(
        self,
        order_id: str,
        symbol: str,
        label: str = "order",
        timeout: int = 3,
    ) -> float | None:
        """
        Fetch the actual broker fill price with one outer retry.

        `get_order_fill_price()` already retries internally for `timeout`
        seconds. We add a single outer retry to ride out transient Kite
        API blips (occasional 502s, network reset). Returns None on
        sustained failure — caller is expected to fall back to the LTP
        estimate, and EOD `verify_trades.py` corrects the stored P&L
        from the broker order book regardless. (#187)
        """
        for attempt in (1, 2):
            try:
                price = self.zerodha.get_order_fill_price(order_id, timeout=timeout)
                if price:
                    return price
                # None on first attempt → maybe trades not yet visible; retry once.
                if attempt == 1:
                    self.log.warning(
                        f"{label} fill price for {symbol} not available "
                        f"(order {order_id}, attempt 1) — retrying once"
                    )
                    continue
                # Second attempt also returned None — give up.
                self.log.warning(
                    f"{label} fill price for {symbol} unavailable after retry — "
                    f"live log will use LTP estimate, EOD verify will correct"
                )
                return None
            except Exception as e:
                if attempt == 1:
                    self.log.warning(
                        f"{label} fill price fetch raised for {symbol} "
                        f"(order {order_id}, attempt 1): {e} — retrying once"
                    )
                    continue
                self.log.warning(
                    f"{label} fill price fetch raised for {symbol} "
                    f"(order {order_id}, attempt 2): {e} — "
                    f"live log will use LTP estimate, EOD verify will correct"
                )
                return None
        return None

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
        if position.get("status") != "OPEN":
            self.log.debug(
                f"Skipping {reason} exit for {position.get('symbol', 'UNKNOWN')}: "
                f"position already {position.get('status')}"
            )
            return

        symbol   = position["symbol"]
        exchange = position["exchange"]
        side     = position["side"]
        qty      = position["qty"]
        entry    = position["entry_price"]
        now      = now_ist()

        # NOTE: the broker-truth preflight that prevents shutdown ghost
        # trips lives in `square_off_all()` (the only entry point that
        # ever fires SQUARE_OFF / CIRCUIT_BREAKER exits). Doing it again
        # here on every SL / target / decay / momentum exit added ~200-
        # 500ms of Zerodha latency per polled exit, duplicated work the
        # main loop already does each scan tick, and short-circuited the
        # nuanced SL-M status path below. The early-OPEN guard at the
        # top of this method is the cheap defence that still covers the
        # rare "another path just marked it CLOSED" race.

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
        # SQUARE_OFF / CIRCUIT_BREAKER paths bulk-cancel SL-Ms upstream
        # in `square_off_all()` and clear `_sl_order_id` immediately
        # afterwards, so a stale ID here would never be reached on a
        # clean square-off. If we DO see one on those paths it means
        # the bulk-cancel was bypassed (e.g. direct exit_position()
        # call) — still worth a debug breadcrumb but not a user-facing
        # warning.
        if sl_order_id and sl_order_id not in self._pending_order_ids:
            if reason in ("SQUARE_OFF", "CIRCUIT_BREAKER"):
                self.log.debug(
                    f"{symbol}: stale _sl_order_id {sl_order_id} on {reason} "
                    f"path (already cancelled by square_off_all bulk path)"
                )
            else:
                self.log.warning(
                    f"Orphan pending ID detected: {symbol} has _sl_order_id {sl_order_id} "
                    f"but not in pending set. Position may have been exited already or "
                    f"had a failed discard. Continuing with exit."
                )

        # ── Handle exchange SL-M order ────────────────────────────
        if sl_order_id and not self.cfg.DRY_RUN:
            if reason == "STOP_LOSS":
                # CRITICAL BUG FIX (Apr 17 2026): Do NOT assume the exchange
                # SL-M has already triggered just because our software SL
                # fired. When candle-protect / regime-shift / loser-tighten
                # move the software SL TIGHTER than the exchange SL-M trigger,
                # the software stop fires first and the exchange SL-M is
                # still pending on the book. Previously we asked
                # `get_order_filled_qty() or qty`, and `0 or qty == qty` made
                # the bot believe the order had fully filled even when it was
                # still TRIGGER PENDING. Result: no real exit was placed, the
                # position stayed open on Zerodha, and P&L was double-booked
                # when reconciliation later re-adopted the still-live short.
                # Now we check the ACTUAL order status first.
                order_status = None
                try:
                    order_status = self.zerodha.get_order_status(sl_order_id)
                except Exception as e:
                    self.log.warning(
                        f"Could not read SL-M status for {symbol}: {e} — "
                        f"treating as untriggered, will place market exit"
                    )

                if order_status == "COMPLETE":
                    # Exchange SL-M actually fired. Confirm qty filled.
                    sl_filled_qty = 0
                    try:
                        sl_filled_qty = self.zerodha.get_order_filled_qty(sl_order_id) or 0
                    except Exception:
                        sl_filled_qty = qty  # API blip after COMPLETE — safe to trust

                    # Pull the broker's actual SL-M fill price so the live log
                    # and stored pos["exit_price"] match Zerodha exactly. Without
                    # this, exit_price stays as the LTP we polled at the moment
                    # we noticed the breach, which can drift from the real fill
                    # by 1-2 ticks (HDFCLIFE 2026-04-21: LTP Rs.609.65 vs real
                    # fill Rs.609.30 → log showed -124.80 vs Zerodha -116.40).
                    # One outer retry on transient API failures (Kite occasionally
                    # 502s); falls back silently to the LTP estimate after that
                    # — verify_trades.py still corrects EOD (#186, #187).
                    sl_fill_price = self._fetch_fill_price_with_retry(
                        sl_order_id, symbol, label="SL-M", timeout=3,
                    )

                    if sl_filled_qty >= qty:
                        if sl_fill_price:
                            exit_price = sl_fill_price
                        self.log.info(
                            f"SL-M {sl_order_id} triggered for {symbol} — "
                            f"full fill confirmed ({sl_filled_qty} shares "
                            f"@ Rs.{exit_price:.2f})"
                        )
                    else:
                        # Partial fill — place market exit for remaining shares
                        remaining = qty - sl_filled_qty
                        self.log.warning(
                            f"SL-M {sl_order_id} PARTIAL fill: {sl_filled_qty}/{qty} shares "
                            f"@ Rs.{sl_fill_price or exit_price:.2f}. "
                            f"Placing MARKET exit for remaining {remaining} shares."
                        )
                        market_fill_price = None
                        try:
                            market_exit_id = self.zerodha.place_order(
                                symbol=symbol, exchange=exchange,
                                qty=remaining, side=exit_side, order_type="MARKET",
                            )
                            market_fill_price = self._fetch_fill_price_with_retry(
                                market_exit_id, symbol,
                                label="MARKET top-up", timeout=5,
                            )
                        except Exception as e:
                            self.log.error(
                                f"FAILED to exit remaining {remaining} shares of {symbol}: {e} — "
                                f"MANUAL INTERVENTION NEEDED"
                            )

                        # Weighted average of SL-M slice + market top-up
                        if sl_fill_price and market_fill_price:
                            exit_price = round(
                                (sl_fill_price * sl_filled_qty +
                                 market_fill_price * remaining) / qty,
                                2,
                            )
                        elif sl_fill_price:
                            exit_price = sl_fill_price  # market fill unknown
                        elif market_fill_price:
                            exit_price = market_fill_price  # SL fill unknown
                        # else: keep LTP estimate, EOD verify will correct
                    self._pending_order_ids.discard(sl_order_id)
                    position["_sl_order_id"] = None
                    sl_m_handled = True

                    # Recompute P&L with the actual broker fill price
                    if side == "BUY":
                        pnl = (exit_price - entry) * qty
                    else:
                        pnl = (entry - exit_price) * qty
                else:
                    # SL-M did NOT fire on exchange (status likely TRIGGER PENDING
                    # or OPEN — software SL was tighter). Cancel the stale SL-M
                    # and place our own market exit so the position actually closes.
                    self.log.warning(
                        f"SOFTWARE SL fired for {symbol} but exchange SL-M "
                        f"{sl_order_id} status={order_status!r} (not COMPLETE). "
                        f"Cancelling stale SL-M and placing MARKET exit."
                    )
                    try:
                        self.zerodha.cancel_order(sl_order_id)
                    except Exception as e:
                        self.log.warning(
                            f"Failed to cancel stale SL-M {sl_order_id} for {symbol}: {e} — "
                            f"proceeding with market exit anyway"
                        )
                    self._pending_order_ids.discard(sl_order_id)
                    position["_sl_order_id"] = None
                    # Intentionally DO NOT set sl_m_handled=True — let the
                    # normal exit path below place the market order.
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
                # Exit order succeeded — reset failure counter and clear broken flag
                if self._consecutive_order_failures > 0 or self._order_api_broken:
                    self.log.info(
                        f"Order API recovered after {self._consecutive_order_failures} "
                        f"failure(s) — resuming normal operation"
                    )
                self._consecutive_order_failures = 0
                self._order_api_broken = False

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

        # Record exit time for per-symbol re-entry cooldown (Roadmap #161).
        self._last_exit_time[f"{symbol}_{side}"] = now

        # Record exit score + reason for average-down prevention (#195).
        # Prefer position["_exit_score"] when caller stamped a fresh re-score
        # (e.g. SIGNAL_DECAY); fall back to the entry score so we always
        # have a magnitude to compare against. Never crash on missing.
        try:
            exit_score = position.get("_exit_score")
            if exit_score is None:
                exit_score = position.get("_entry_score", 0)
            self._last_exit_score[f"{symbol}_{side}"] = {
                "score":  float(exit_score or 0),
                "reason": reason,
                "time":   now,
            }
        except (TypeError, ValueError):
            pass

        # Track chop-exit timestamps for the choppy-morning pause (#192).
        if reason in ("STAGNANT_EXIT", "SIGNAL_DECAY"):
            self.record_chop_exit(now)
        
        self._log_action("EXIT", symbol, exit_side, qty, exit_price, reason)

        # Track consecutive losing exits for whipsaw guard (#20 + #244).
        # Pre-#244 only STOP_LOSS counted; today's MOMENTUM_KILL streak
        # bypassed the guard. Now any pnl<0 exit (excluding EOD/operator
        # reasons) also feeds the counter when the kill-switch is on.
        if reason == "STOP_LOSS":
            self.record_sl_hit()
        elif pnl > 0:
            self.record_profitable_close()
        elif (
            pnl < 0
            and getattr(self.cfg, "LOSS_STREAK_INCLUDE_NON_SL_LOSSES", False)
            and reason in ("MOMENTUM_KILL", "STAGNANT_EXIT", "SIGNAL_DECAY", "LOSER_EXIT")
        ):
            self.record_sl_hit()

    # ================================================================
    # PARTIAL EXIT — EXIT SUBSET OF SHARES
    # ================================================================

    def _place_exit_order(
        self,
        position: dict,
        price: float,
        qty: int,
        reason: str,
    ) -> tuple[float, int] | None:
        """
        Exits a subset of shares from an open position (for partial
        profit taking). Does NOT mark the position as CLOSED — the
        remaining shares stay open. Updates the trade log.

        Returns (actual_fill_price, actual_filled_qty) on success,
        None on failure. The caller MUST use actual_filled_qty when
        adjusting pos["qty"] — a MARKET order on an illiquid stock can
        partial-fill, and the caller would otherwise under-track the
        live share count (Roadmap #150).
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
            self._log_action(reason, symbol, exit_side, qty, price,
                             f"Partial exit {qty} shares")
            return price, qty

        try:
            order_id = self.zerodha.place_order(
                symbol=symbol, exchange=exchange,
                qty=qty, side=exit_side, order_type="MARKET",
            )
            # Partial exit succeeded — reset failure counter and clear broken flag
            self._consecutive_order_failures = 0
            self._order_api_broken = False

            # Fetch actual fill price AND actual filled qty
            fill_price = self.zerodha.get_order_fill_price(order_id)
            if fill_price:
                price = fill_price
            filled_qty = self.zerodha.get_order_filled_qty(order_id)
            if filled_qty is None or filled_qty <= 0:
                # Could not determine fills — assume zero to be safe
                self.log.error(
                    f"Partial exit {symbol}: order {order_id} placed but "
                    f"could not determine filled qty. Treating as 0 filled — "
                    f"position qty NOT reduced. Review manually."
                )
                return None
            if filled_qty < qty:
                self.log.warning(
                    f"Partial exit {symbol}: requested {qty} shares but only "
                    f"{filled_qty} filled (illiquid MARKET fill). Position qty "
                    f"will be reduced by actual filled amount."
                )
        except Exception as e:
            self._consecutive_order_failures += 1
            self.log.error(
                f"Partial exit order FAILED for {symbol}: {e} — "
                f"position qty NOT adjusted"
            )
            if self._consecutive_order_failures >= self.ORDER_FAILURE_LIMIT:
                self._order_api_broken = True
            return None

        self._log_action(reason, symbol, exit_side, filled_qty, price,
                         f"Partial exit {filled_qty}/{qty} shares")
        return price, filled_qty

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

            # ── Post-entry momentum kill (Roadmap #198) ───────────
            # Slow-bleed-to-SL is the dominant loss pattern: trade
            # fills, immediately turns red, walks 8-12 minutes to its
            # full -1×ATR SL while we wait. If the first three minutes
            # of post-fill action don't move at least 25% toward the
            # target while we're underwater, the setup is wrong — exit
            # at small loss instead of waiting for the full SL.
            # Skipped if:
            #   - kill-switch off
            #   - elapsed < grace (let the order settle, spread tighten)
            #   - elapsed > window (too late, normal SL/trail handles it)
            #   - pos["_external"]   (manual / adopted, give grace)
            #   - pos["_partial_taken"] (already locking profit)
            #   - elapsed unknown (entry_time missing)
            # Closes via exit_position(reason="MOMENTUM_KILL"). The exit
            # itself stamps _stagnant_exits + _last_exit_score so the
            # average-down (#195) gate prevents an instant re-entry on
            # the same false signal.
            if (
                getattr(self.cfg, "MOMENTUM_KILL_ENABLED", False)
                and not pos.get("_external")
                and not pos.get("_partial_taken")
            ):
                killed = self._momentum_kill_check(
                    pos, current_price, unrealised, side, entry, target
                )
                if killed:
                    closed += 1
                    continue  # exited; skip SL/target/trail for this pos

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

    def _momentum_kill_check(
        self,
        pos: dict,
        current_price: float,
        unrealised: float,
        side: str,
        entry: float,
        target: float,
    ) -> bool:
        """Roadmap #198 — exit at small loss when post-fill momentum
        fails to develop in the trade direction.

        Rationale: a real edge shows up in the first 1-3 minutes of
        post-fill price action. If we're still red AND haven't moved a
        meaningful fraction of the way to target by then, the entry
        thesis was wrong. Take a small loss now (≪ 1×ATR SL).

        Returns True if the position was exited.

        Skipped (returns False) when:
          - elapsed < MOMENTUM_KILL_GRACE_SECONDS  (let order settle)
          - elapsed > MOMENTUM_KILL_WINDOW_MINUTES * 60  (window passed)
          - unrealised >= 0  (trade is already winning)
          - target == entry (degenerate; division-by-zero guard)
          - entry_time missing or unparseable  (can't measure age)

        Caller is responsible for filtering out _external and
        _partial_taken positions before invoking.
        """
        entry_time_str = pos.get("entry_time", "") or ""
        if not entry_time_str:
            return False
        now = now_ist()
        try:
            entry_dt = datetime.datetime.strptime(
                f"{now.strftime('%Y-%m-%d')} {entry_time_str}",
                "%Y-%m-%d %H:%M:%S",
            )
            # Stamp tz so tz-aware "now" subtracts cleanly
            if now.tzinfo is not None and entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=now.tzinfo)
            elapsed = (now - entry_dt).total_seconds()
        except (ValueError, TypeError):
            return False

        grace = int(self.cfg.MOMENTUM_KILL_GRACE_SECONDS)
        window_sec = int(self.cfg.MOMENTUM_KILL_WINDOW_MINUTES) * 60
        if elapsed < grace or elapsed > window_sec:
            return False
        if unrealised >= 0:
            return False
        if target == entry:
            return False

        # Noise floor (#198 production-data tuning, 2026-04-27).
        # Without this, the rule fires on bid-ask spread / first-minute
        # fade because the 25%-target-progress test treats any
        # negative tick as failure. Industry practice: only consider
        # an early exit if adverse move exceeds typical NSE intraday
        # spread (~0.10%) by a comfortable margin (4x).
        min_adverse = float(
            getattr(self.cfg, "MOMENTUM_KILL_MIN_ADVERSE_PCT", 0.0)
        )
        if entry > 0 and min_adverse > 0:
            adverse_pct = abs(entry - current_price) / entry * 100.0
            if adverse_pct < min_adverse:
                return False

        # Progress = fraction of distance from entry to target traveled
        # in the trade's favor. Negative when underwater.
        if side == "BUY":
            progress = (current_price - entry) / (target - entry)
        else:
            progress = (entry - current_price) / (entry - target)
        min_progress = float(self.cfg.MOMENTUM_KILL_MIN_PROGRESS_PCT) / 100.0
        if progress >= min_progress:
            return False  # adequate progress, let the trade breathe

        symbol = pos["symbol"]
        elapsed_min = elapsed / 60.0
        self.log.warning(
            f"MOMENTUM KILL: {symbol} {side} | entry Rs.{entry:.2f} → "
            f"Rs.{current_price:.2f} | progress {progress*100:+.1f}% "
            f"(< {self.cfg.MOMENTUM_KILL_MIN_PROGRESS_PCT:.0f}%) after "
            f"{elapsed_min:.1f} min | Unrealised Rs.{unrealised:+,.2f} — "
            f"exiting at small loss"
        )
        # Use live current_price as the exit (no SL gaming — we want
        # the actual market exit). exit_position handles charges + book.
        self.exit_position(pos, current_price, "MOMENTUM_KILL")
        return True

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
                result = self._place_exit_order(pos, current_price, partial_qty, "PARTIAL_PROFIT")
                if result is not None:
                    fill, actual_qty = result
                    # Recalculate P&L with actual fill price AND actual qty
                    # (Roadmap #150: MARKET can partial-fill on illiquid stocks)
                    partial_pnl = round((fill - entry) * actual_qty, 2)
                    pos["qty"] = pos["qty"] - actual_qty
                    pos["_partial_taken"] = True
                    pos["_partial_pnl"] = round(pos.get("_partial_pnl", 0) + partial_pnl, 2)
                    pos["_partial_qty"] = pos.get("_partial_qty", 0) + actual_qty
                    pos["_partial_exit_price"] = fill
                    # Update exchange SL-M for reduced qty — only if we
                    # actually reduced the position (actual_qty > 0)
                    if actual_qty > 0 and pos["qty"] > 0:
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

                result = self._place_exit_order(pos, current_price, partial_qty, "PARTIAL_PROFIT")
                if result is not None:
                    fill, actual_qty = result
                    partial_pnl = round((entry - fill) * actual_qty, 2)
                    pos["qty"] = pos["qty"] - actual_qty
                    pos["_partial_taken"] = True
                    pos["_partial_pnl"] = round(pos.get("_partial_pnl", 0) + partial_pnl, 2)
                    pos["_partial_qty"] = pos.get("_partial_qty", 0) + actual_qty
                    pos["_partial_exit_price"] = fill
                    # Update exchange SL-M for reduced qty — only if we
                    # actually reduced the position (Roadmap #150)
                    if actual_qty > 0 and pos["qty"] > 0:
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
                pos["_sl_m_failed"] = False
                self._pending_order_ids.add(new_id)
            else:
                # Cancel succeeded but Kite returned no order_id for the
                # replacement — position is now unprotected at the
                # exchange level. Flag it so EOD reports / restart logic
                # know software SL is the only line of defence.
                pos["_sl_m_failed"] = True
                self.log.error(
                    f"*** EXCHANGE SL-M REPLACE FAILED for {pos['symbol']} *** "
                    f"Old SL cancelled, new SL not placed (Kite returned no ID). "
                    f"Position is protected only by software SL monitoring — "
                    f"a bot crash before exit would leave this position NAKED."
                )
        except Exception as e:
            # Same fault-class as the no-id branch above. Set the flag so
            # downstream code (EOD reports, restart safety) sees the
            # position is no longer exchange-protected.
            pos["_sl_m_failed"] = True
            self.log.error(
                f"*** EXCHANGE SL-M REPLACE FAILED for {pos['symbol']} *** "
                f"Old SL cancelled, new SL placement raised "
                f"{type(e).__name__}: {e}. Software SL still active; "
                f"a bot crash before exit would leave this position NAKED."
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

        # Skip if position was just adopted / resumed — give the user's
        # manual trade a grace window to breathe before bot touches it.
        if self._in_adoption_grace(pos):
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
        Two-tier stagnant-position exit:

        Tier 1 (45 min directional, default):
          Fires when the trade is clearly adverse (move_pct < -ADVERSE_PCT)
          OR truly dead-flat (|move_pct| < DEAD_FLAT_PCT). Slow-positive
          trades are allowed to continue toward target.

        Tier 2 (90 min progress-to-target, #172):
          Fires when the trade has covered less than
          STAGNANT_MIN_PROGRESS_PCT of the entry→target distance.
          Catches drifters that survived Tier 1 by sitting just outside
          the dead-flat band on the snapshot tick (UNITDSPR 2026-04-20:
          183 min for +0.03%). Skipped if Tier 1 already exited the
          symbol on this run.

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
        adverse_pct   = self.cfg.STAGNANT_ADVERSE_PCT
        dead_flat_pct = self.cfg.STAGNANT_DEAD_FLAT_PCT
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

            # Directional stagnant-exit (2026-04-17): only exit if the
            # trade is clearly going wrong (adverse) or truly dead (flat
            # in a tight band near entry). A slow-positive trade is
            # progressing toward target and should be allowed to run —
            # forcing it out locks in a sub-charge profit AND costs
            # another round-trip in charges on the replacement trade.
            is_adverse   = move_pct < -adverse_pct
            is_dead_flat = abs(move_pct) < dead_flat_pct

            reason_tag = None
            if is_adverse:
                reason_tag = "adverse"
            elif is_dead_flat:
                reason_tag = "dead-flat"
            else:
                # Tier 2 (#172): hard-max progress check. A slow-positive
                # trade that's barely budged toward target after 90 min
                # is a drifter — band-based check missed it because price
                # straddled the dead-flat band on the snapshot tick.
                # progress_pct uses target distance, not absolute move,
                # so it scales with the trade's own R:R (1.0% target vs
                # 4% expiry-day target both judged on equal footing).
                if (self.cfg.STAGNANT_HARD_MAX_ENABLED
                        and elapsed >= self.cfg.STAGNANT_HARD_MAX_MINUTES):
                    target = pos.get("target_price") or 0
                    target_dist = abs(target - entry)
                    if target <= 0 or target_dist <= 0:
                        # Malformed position (no target / target == entry).
                        # Skip Tier-2 — Tier-1 already had its chance and
                        # LOSER_EXIT will catch this at 14:45 if it's
                        # still losing.
                        continue
                    # Signed move toward target: +ve = progressing,
                    # -ve = went backwards past entry. Clamp upper at
                    # 100 (target reached) and lower at -100 to keep
                    # the log readable on extreme cases.
                    progress_pct = max(-100.0, min(100.0,
                        (move_pct / (target_dist / entry * 100)) * 100
                    ))
                    if progress_pct < self.cfg.STAGNANT_MIN_PROGRESS_PCT:
                        reason_tag = f"drift {progress_pct:+.0f}% to target"

            if reason_tag is None:
                continue  # trade is progressing (or just outside both gates) — let it breathe

            pnl = (current_price - entry) * pos["qty"] if side == "BUY" \
                else (entry - current_price) * pos["qty"]
            self.log.warning(
                f"STAGNANT EXIT: {pos['symbol']} {side} — open {elapsed:.0f} min, "
                f"moved {move_pct:+.2f}% ({reason_tag}) | P&L: Rs.{pnl:+,.2f}"
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

            # Adopted/resumed positions: give grace window before forced exit.
            # User just opened this intentionally — bot inheriting doesn't
            # give bot permission to close immediately.
            if self._in_adoption_grace(pos):
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

        # After the bulk cancel, every open position's tracked SL-M is
        # known to be gone from the exchange. Clear `_sl_order_id` on
        # each so `exit_position()` doesn't fire its orphan-pending
        # warning + a futile cancel-attempt for an order ID that no
        # longer exists. Without this, every clean SQUARE_OFF logs two
        # noisy WARNINGs per position ("Orphan pending ID detected" and
        # "Could not cancel order ... does not exist") even though the
        # exit path is fully intentional.
        for _p in self.open_positions():
            if _p.get("_sl_order_id"):
                _p["_sl_order_id"] = None

        # Broker truth preflight (#270): rebuild the open-position list
        # after checking whether the user already closed any tracked MIS
        # positions in Kite. This prevents shutdown SQUARE_OFF from opening
        # reverse-side ghost positions against broker net=0.
        if not self.cfg.DRY_RUN:
            try:
                self.sync_external_positions()
            except Exception as e:
                self.log.warning(
                    f"square_off_all broker preflight failed: {e} — "
                    f"continuing with tracked open positions"
                )

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
        # MTM-aware (#166): include open-position MTM. Backwards
        # compatible — falls back to closed-only day_pnl() when
        # MTM_AWARE_CB_ENABLED is False or no quotes have been cached.
        eff_pnl = self.effective_day_pnl()
        pnl_since_baseline = eff_pnl - self._cb_pnl_baseline

        if pnl_since_baseline < -max_loss:
            self.log.error(
                f"CIRCUIT BREAKER: P&L since baseline Rs.{pnl_since_baseline:,.2f} "
                f"(day total Rs.{eff_pnl:,.2f}) exceeds max loss of "
                f"Rs.{max_loss:,.0f} ({max_loss_pct}% of budget). "
                f"Stopping all trading."
            )
            return True
        return False

    def reset_circuit_breaker_baseline(self):
        """After cooldown, reset so the breaker only trips on NEW losses."""
        self._cb_pnl_baseline = self.effective_day_pnl()
        self._cb_trip_count += 1

    def circuit_breaker_trips_exhausted(self) -> bool:
        """True if max CB trips reached — no more cooldowns allowed."""
        max_trips = self.cfg.MAX_CIRCUIT_BREAKER_TRIPS
        return max_trips > 0 and self._cb_trip_count >= max_trips

    # ================================================================
    # CONSECUTIVE SL TRACKING
    # ================================================================

    def record_sl_hit(self):
        """Called after a losing exit. Increments consecutive-loss counter.

        Despite the legacy name, this counts more than just hard SL hits
        when `Config.LOSS_STREAK_INCLUDE_NON_SL_LOSSES` is True (#244 —
        also counts MOMENTUM_KILL / STAGNANT_EXIT / SIGNAL_DECAY /
        LOSER_EXIT classes of loss). Function name retained because
        external callers (sync path, manager loops) reference it.
        """
        self._consecutive_sl_count += 1
        limit = self.cfg.CONSECUTIVE_SL_PAUSE_COUNT
        if limit > 0 and self._consecutive_sl_count >= limit:
            pause_min = self.cfg.CONSECUTIVE_SL_PAUSE_MINUTES
            self._sl_pause_until = time.time() + pause_min * 60
            self.log.warning(
                f"LOSS-STREAK GUARD: {self._consecutive_sl_count} consecutive losing exits — "
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
    # ENTRY-BURST CAP (Roadmap #179)
    # ================================================================

    def is_burst_capped(self, now: datetime.datetime | None = None) -> bool:
        """True if `MAX_ENTRIES_PER_60S` successful entries are already
        recorded in the trailing 60 seconds.

        Cap-2 means the third entry inside any rolling 60-second window
        is blocked. Same-direction sub-60s bursts had ~92% lose-together
        correlation across 3 qualifying days (Apr-22 / Apr-23 / May-05).
        Kill-switch: ENTRY_BURST_CAP_ENABLED.

        Cap is budget-tiered (#179a) — `effective_burst_cap()` adds
        `BUDGET_BURST_CAP_DELTA[regime]` so NORMAL/LARGE accounts that
        run 5-8 morning slots aren't single-threaded by the SMALL-cohort
        audit value.
        """
        if not getattr(self.cfg, "ENTRY_BURST_CAP_ENABLED", True):
            return False
        cap = self.effective_burst_cap()
        if cap <= 0:
            return False
        t = now or now_ist()
        cutoff = t - datetime.timedelta(seconds=60)
        recent = sum(1 for ts in self._recent_entry_times if ts >= cutoff)
        return recent >= cap

    def effective_burst_cap(self) -> int:
        """Base ENTRY_BURST_CAP_MAX_ENTRIES_PER_60S with budget-regime
        delta applied (#179a).

        The 92% lose-together evidence was on a Rs.50K SMALL account,
        so SMALL/TINY get delta=0 (audit-validated cap-2). NORMAL/LARGE
        get +1 / +2 to allow morning slot fills without single-threading.
        Floored at 0 (a negative effective cap is meaningless).
        """
        base = int(getattr(self.cfg, "ENTRY_BURST_CAP_MAX_ENTRIES_PER_60S", 0))
        delta = 0
        if getattr(self.cfg, "BUDGET_REGIME_ENABLED", True):
            delta_map = getattr(self.cfg, "BUDGET_BURST_CAP_DELTA", {}) or {}
            try:
                delta = int(delta_map.get(self.budget_regime(), 0))
            except (TypeError, ValueError):
                delta = 0
        return max(0, base + delta)

    def record_entry(
        self,
        now: datetime.datetime | None = None,
        side: str | None = None,
    ) -> None:
        """Stamp a successful entry timestamp for the burst-cap window.

        Called from the END of `enter_trade()` after the order placement
        succeeds. Only recording successful entries (not rejections)
        keeps the cap honest — a rejected attempt should not count.

        When `side` is supplied AND the #251a opposing-thin cap is
        armed AND this entry is on the opposing (un-paused) side,
        bump the per-session counter so the cap can fire on subsequent
        attempts.
        """
        self._recent_entry_times.append(now or now_ist())
        if side is not None and self._opposing_thin_side == side:
            self._opposing_thin_count += 1
            if self._opposing_thin_count >= self._opposing_thin_max:
                self.log.warning(
                    f"OPPOSING-THIN CAP REACHED ({side}): "
                    f"{self._opposing_thin_count}/{self._opposing_thin_max} "
                    f"entries this session. Further {side} entries blocked "
                    f"until next session (fractional-Kelly cap on the "
                    f"surviving side of the directional pause)."
                )

    # ================================================================
    # MULTI-DAY PAUSES (#251 directional, #253 rolling-PF)
    # ================================================================

    def arm_multiday_pauses(
        self,
        rolling_pf: float | None,
        rolling_net: float | None,
        rolling_n_trades: int,
        side_stats: dict[str, dict] | None,
        nifty_return_pct: float | None,
    ) -> None:
        """Called once at session start by the manager.

        `rolling_pf`, `rolling_net`, `rolling_n_trades` describe the
        last `ROLLING_PF_PAUSE_LOOKBACK_DAYS` of intraday_tax_ledger
        rows. `side_stats` is `{"BUY": {"n": int, "wins": int}, "SELL":
        {...}}` over the directional lookback. `nifty_return_pct` is
        the rolling-N-day NIFTY total return in %.

        Either argument can be None when the manager couldn't compute
        it (e.g. fresh DB, no prior trades) — in that case the
        corresponding pause stays unarmed and the bot trades normally.
        """
        # Reset all per-session pause state before re-arming, so a
        # multi-day same-process run cannot leak yesterday's state.
        self._rolling_pf_pause_armed = False
        self._rolling_pf_pause_reason = ""
        self._directional_pause_side = None
        self._directional_pause_reason = ""
        self._opposing_thin_side = None
        self._opposing_thin_reason = ""
        self._opposing_thin_count = 0
        self._opposing_thin_max = 0
        self._nifty_intraday_returns.clear()
        self._directional_bypass_logged = {"BUY": False, "SELL": False}
        self._breadth_bypass_logged = {"BUY": False, "SELL": False}

        # ── Rolling-PF circuit breaker (#253) ─────────────────────
        if (
            getattr(self.cfg, "ROLLING_PF_PAUSE_ENABLED", False)
            and rolling_pf is not None
            and rolling_net is not None
            and rolling_n_trades >= int(getattr(self.cfg, "ROLLING_PF_PAUSE_MIN_TRADES", 5))
            and rolling_pf < float(getattr(self.cfg, "ROLLING_PF_PAUSE_THRESHOLD", 0.6))
            and rolling_net < float(getattr(self.cfg, "ROLLING_PF_PAUSE_NET_FLOOR", -300.0))
        ):
            lookback = int(getattr(self.cfg, "ROLLING_PF_PAUSE_LOOKBACK_DAYS", 3))
            self._rolling_pf_pause_armed = True
            self._rolling_pf_pause_reason = (
                f"rolling-{lookback}d PF={rolling_pf:.2f} (threshold "
                f"{self.cfg.ROLLING_PF_PAUSE_THRESHOLD:.2f}), "
                f"net=Rs.{rolling_net:.0f} (floor "
                f"Rs.{self.cfg.ROLLING_PF_PAUSE_NET_FLOOR:.0f}), "
                f"n={rolling_n_trades}"
            )
            self.log.warning(
                f"ROLLING-PF PAUSE ARMED: {self._rolling_pf_pause_reason}. "
                f"All NEW entries blocked for the session; existing "
                f"positions managed normally."
            )

        # ── Directional auto-pause (#251) ─────────────────────────
        if (
            getattr(self.cfg, "DIRECTIONAL_PAUSE_ENABLED", True)
            and side_stats
            and nifty_return_pct is not None
        ):
            min_trades = int(getattr(self.cfg, "DIRECTIONAL_PAUSE_MIN_TRADES", 10))
            wr_threshold = float(getattr(self.cfg, "DIRECTIONAL_PAUSE_WR_THRESHOLD", 0.30))
            nifty_floor = float(getattr(self.cfg, "DIRECTIONAL_PAUSE_NIFTY_FLOOR_PCT", 0.0))
            lookback = int(getattr(self.cfg, "DIRECTIONAL_PAUSE_LOOKBACK_DAYS", 7))

            # BUY-side check: contra-NIFTY (NIFTY ≤ floor)
            buy = side_stats.get("BUY") or {}
            buy_n = int(buy.get("n", 0))
            buy_wr = (int(buy.get("wins", 0)) / buy_n) if buy_n > 0 else None
            if (
                buy_n >= min_trades
                and buy_wr is not None
                and buy_wr <= wr_threshold
                and nifty_return_pct <= nifty_floor
            ):
                self._directional_pause_side = "BUY"
                self._directional_pause_reason = (
                    f"rolling-{lookback}d BUY-WR={buy_wr*100:.1f}% "
                    f"({buy['wins']}/{buy_n}, threshold "
                    f"{wr_threshold*100:.0f}%) AND NIFTY-{lookback}d-return="
                    f"{nifty_return_pct:+.2f}% (floor {nifty_floor:+.2f}%)"
                )
                self.log.warning(
                    f"DIRECTIONAL PAUSE ARMED (BUY): {self._directional_pause_reason}. "
                    f"All NEW BUY entries blocked for the session; SELL "
                    f"side and existing positions managed normally."
                )
                self._maybe_arm_opposing_thin("BUY", side_stats)
                return

            # SELL-side check: contra-NIFTY (NIFTY ≥ -floor for upside trend)
            sell = side_stats.get("SELL") or {}
            sell_n = int(sell.get("n", 0))
            sell_wr = (int(sell.get("wins", 0)) / sell_n) if sell_n > 0 else None
            if (
                sell_n >= min_trades
                and sell_wr is not None
                and sell_wr <= wr_threshold
                and nifty_return_pct >= -nifty_floor
            ):
                self._directional_pause_side = "SELL"
                self._directional_pause_reason = (
                    f"rolling-{lookback}d SELL-WR={sell_wr*100:.1f}% "
                    f"({sell['wins']}/{sell_n}, threshold "
                    f"{wr_threshold*100:.0f}%) AND NIFTY-{lookback}d-return="
                    f"{nifty_return_pct:+.2f}% (floor {-nifty_floor:+.2f}%)"
                )
                self.log.warning(
                    f"DIRECTIONAL PAUSE ARMED (SELL): {self._directional_pause_reason}. "
                    f"All NEW SELL entries blocked for the session; BUY "
                    f"side and existing positions managed normally."
                )
                self._maybe_arm_opposing_thin("SELL", side_stats)

    def _maybe_arm_opposing_thin(
        self,
        paused_side: str,
        side_stats: dict[str, dict],
    ) -> None:
        """After a directional pause arms against `paused_side`, check
        whether the OPPOSING (un-paused) side has thin evidence (n <
        OPPOSING_MIN_TRADES). If so, arm a per-session entry cap on
        the opposing side per Roadmap #251a (fractional-Kelly).

        Industry priors (Investopedia / Kelly criterion): typical
        win-prob lookback is 50-60 trades. With n=14 the binomial CI
        at p=0.5 is ±26pp — statistical noise. Without this gate the
        bot would lean its full per-session quota on an unverified
        side just because the OTHER side broke down.
        """
        opposing = "SELL" if paused_side == "BUY" else "BUY"
        n_threshold = int(
            getattr(self.cfg, "DIRECTIONAL_PAUSE_OPPOSING_MIN_TRADES", 20)
        )
        max_entries = int(
            getattr(self.cfg, "DIRECTIONAL_PAUSE_OPPOSING_THIN_MAX_ENTRIES", 5)
        )
        if max_entries <= 0 or n_threshold <= 0:
            return
        opposing_stats = side_stats.get(opposing) or {}
        opposing_n = int(opposing_stats.get("n", 0))
        if opposing_n >= n_threshold:
            return
        self._opposing_thin_side = opposing
        self._opposing_thin_max = max_entries
        self._opposing_thin_count = 0
        self._opposing_thin_reason = (
            f"opposing-side {opposing} n={opposing_n} below "
            f"OPPOSING_MIN_TRADES={n_threshold}; capping {opposing} "
            f"entries at {max_entries}/session (fractional-Kelly)"
        )
        self.log.warning(
            f"OPPOSING-THIN CAP ARMED ({opposing}): "
            f"{self._opposing_thin_reason}"
        )

    def is_directional_paused(self, side: str) -> bool:
        """True iff a session-wide directional pause is armed for `side`
        AND no fresh-evidence bypass overrides it. Two bypass paths:

        * NIFTY intraday-bounce: index itself rallies in paused-side
          direction.
        * Tape-breadth divergence: scanner finds enough paused-side
          candidates (A/D divergence vs cap-weighted index).

        Bypass returns False; pause state is retained so the gate
        auto-re-engages on the next scan if conditions revert.
        """
        if not getattr(self.cfg, "DIRECTIONAL_PAUSE_ENABLED", True):
            return False
        if self._directional_pause_side != side:
            return False
        if self._is_intraday_nifty_bouncing(side):
            if not self._directional_bypass_logged.get(side, False):
                pct = float(getattr(self.cfg, "DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_PCT", 1.0))
                n = int(getattr(self.cfg, "DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_MIN_SCANS", 2))
                self.log.warning(
                    f"DIRECTIONAL-PAUSE BYPASS ({side}): NIFTY intraday "
                    f"return crossed {'+' if side == 'BUY' else '-'}{pct:.2f}% "
                    f"for {n} consecutive scans — probing regime flip. "
                    f"Pause state retained; gate auto-re-engages if "
                    f"NIFTY reverses."
                )
                self._directional_bypass_logged[side] = True
            # Re-arm the breadth log so a subsequent breadth-only
            # bypass episode (after NIFTY pulls back) prints fresh.
            self._breadth_bypass_logged[side] = False
            return False
        # If we previously bypassed but NIFTY has since pulled back
        # below the threshold, allow the next genuine bypass to log
        # again (one-shot per "bypass episode", not per session).
        self._directional_bypass_logged[side] = False
        # ── #251c tape-breadth divergence bypass ───────────────────
        if self._has_breadth_divergence(side):
            if not self._breadth_bypass_logged.get(side, False):
                info = self._tape_breadth or {}
                paused_count = int(info.get(
                    "buys" if side == "BUY" else "sells", 0
                ))
                total = int(info.get("buys", 0)) + int(info.get("sells", 0))
                ratio_pct = (paused_count / total * 100.0) if total > 0 else 0.0
                ratio_floor = float(getattr(
                    self.cfg, "DIRECTIONAL_PAUSE_BREADTH_BYPASS_RATIO", 0.40
                )) * 100.0
                self.log.warning(
                    f"DIRECTIONAL-PAUSE BYPASS ({side}): tape-breadth "
                    f"shows {paused_count}/{total} ({ratio_pct:.0f}%) "
                    f"{side} candidates — A/D divergence vs NIFTY "
                    f"(≥{ratio_floor:.0f}% threshold). Probing regime "
                    f"flip; gate auto-re-engages on next scan if breadth "
                    f"shifts. All other entry gates still apply."
                )
                self._breadth_bypass_logged[side] = True
            return False
        # Breadth shifted — next genuine breadth-bypass should re-log.
        self._breadth_bypass_logged[side] = False
        return True

    def _has_breadth_divergence(self, side: str) -> bool:
        """True iff the latest scanner tape-breadth snapshot shows
        meaningful paused-side representation. Conservative: returns
        False on missing snapshot, small samples, or thin paused-side
        counts. The 30-40% band between BREADTH_BEARISH_BUY_RATIO and
        BREADTH_BYPASS_RATIO is an explicit "uncertain — neither rule
        fires" zone so the two gates never overlap.
        """
        if not getattr(self.cfg, "DIRECTIONAL_PAUSE_BREADTH_BYPASS_ENABLED", True):
            return False
        info = self._tape_breadth
        if not isinstance(info, dict):
            return False
        try:
            buys = int(info.get("buys", 0))
            sells = int(info.get("sells", 0))
        except (TypeError, ValueError):
            return False
        total = buys + sells
        min_total = int(getattr(self.cfg, "DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_TOTAL", 5))
        if total < min_total:
            return False
        if side == "BUY":
            paused_count = buys
        elif side == "SELL":
            paused_count = sells
        else:
            return False
        min_paused = int(getattr(self.cfg, "DIRECTIONAL_PAUSE_BREADTH_BYPASS_MIN_PAUSED_SIDE", 3))
        if paused_count < min_paused:
            return False
        ratio_floor = float(getattr(self.cfg, "DIRECTIONAL_PAUSE_BREADTH_BYPASS_RATIO", 0.40))
        if total <= 0:
            return False
        return (paused_count / total) >= ratio_floor

    def record_nifty_intraday_return(self, pct: float) -> None:
        """Push the latest NIFTY intraday return % onto the rolling
        deque consulted by `_is_intraday_nifty_bouncing`. Drops NaN /
        inf to keep the bypass comparison honest.
        """
        try:
            v = float(pct)
        except (TypeError, ValueError):
            return
        import math
        if math.isnan(v) or math.isinf(v):
            return
        self._nifty_intraday_returns.append(v)

    def _is_intraday_nifty_bouncing(self, side: str) -> bool:
        """True iff the last MIN_SCANS readings all cross the threshold
        in the direction that favours the paused `side`. Returns False
        on insufficient samples or any mixed-sign run (conservative).
        """
        pct = float(getattr(self.cfg, "DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_PCT", 1.0))
        n = int(getattr(self.cfg, "DIRECTIONAL_PAUSE_INTRADAY_BOUNCE_MIN_SCANS", 2))
        if n < 1 or len(self._nifty_intraday_returns) < n:
            return False
        recent = list(self._nifty_intraday_returns)[-n:]
        if side == "BUY":
            # BUY paused → bearish-regime suspicion; bounce = NIFTY up
            return all(r > pct for r in recent)
        if side == "SELL":
            # SELL paused → bullish-regime suspicion; bounce = NIFTY down
            return all(r < -pct for r in recent)
        return False

    def is_opposing_thin_capped(self, side: str) -> bool:
        """True if `side` is the un-paused opposing side AND its
        evidence was thin (n < OPPOSING_MIN_TRADES) at session start
        AND we already entered OPPOSING_THIN_MAX_ENTRIES this session.

        Implements #251a fractional-Kelly cap on the surviving side
        when the directional-pause armed against the other side based
        on a thin opposing-side sample. Industry rule of thumb (Kelly
        criterion lookback) is 50-60 trades for win-prob estimation;
        binomial CI at n=14 is ±26pp — noise. We don't go full Kelly
        on a noisy edge.
        """
        if not getattr(self.cfg, "DIRECTIONAL_PAUSE_ENABLED", True):
            return False
        if self._opposing_thin_side != side:
            return False
        if self._opposing_thin_max <= 0:
            return False
        return self._opposing_thin_count >= self._opposing_thin_max

    def is_rolling_pf_paused(self) -> bool:
        """True if the rolling-PF session-wide pause is armed."""
        if not getattr(self.cfg, "ROLLING_PF_PAUSE_ENABLED", False):
            return False
        return self._rolling_pf_pause_armed

    # ================================================================
    # BUDGET-REGIME HELPERS (Roadmap #165)
    # ================================================================

    def budget_regime(self) -> str:
        """Current budget regime — "TINY" / "SMALL" / "NORMAL" / "LARGE"."""
        return Config.budget_regime(self._budget)

    def effective_adx_threshold(self, side: str | None = None) -> float:
        """Base ADX_MIN_THRESHOLD with regime delta + strong-gap boost applied (#194).

        The strong-gap boost only applies to FADE trades — i.e. trades whose
        side is opposite to the gap direction (BUY on a gap-DOWN day, SELL
        on a gap-UP day). Aligned trades (BUY on gap-UP, SELL on gap-DOWN)
        ride the institutional flow and don't deserve the extra penalty.
        Pass side=None to get the un-boosted base (e.g. for log headers).
        """
        base = float(self.cfg.ADX_MIN_THRESHOLD)
        if self.cfg.BUDGET_REGIME_ENABLED:
            base += float(self.cfg.BUDGET_ADX_THRESHOLD_DELTA.get(self.budget_regime(), 0.0))
        gap_dir = getattr(self, "_strong_gap_direction", None)
        if (
            getattr(self.cfg, "STRONG_GAP_ADX_BOOST_ENABLED", False)
            and gap_dir is not None
            and side is not None
            and self._is_fade_side(side, gap_dir)
        ):
            base += float(self.cfg.STRONG_GAP_ADX_DELTA)
        return max(0.0, base)

    def effective_adx_override_score(self, side: str | None = None) -> float:
        """ADX_OVERRIDE_SCORE with strong-gap boost applied (#194).

        Same direction-aware semantics as effective_adx_threshold(): boost
        applies only to fade-side trades on a strong-gap continuation day.
        """
        base = float(self.cfg.ADX_OVERRIDE_SCORE)
        gap_dir = getattr(self, "_strong_gap_direction", None)
        if (
            getattr(self.cfg, "STRONG_GAP_ADX_BOOST_ENABLED", False)
            and gap_dir is not None
            and side is not None
            and self._is_fade_side(side, gap_dir)
        ):
            base += float(self.cfg.STRONG_GAP_OVERRIDE_DELTA)
        return base

    @staticmethod
    def _is_fade_side(side: str, gap_dir: str) -> bool:
        """True when `side` trades against `gap_dir` (BUY vs DOWN, SELL vs UP)."""
        return (side == "BUY" and gap_dir == "DOWN") or (side == "SELL" and gap_dir == "UP")

    def effective_trade_cap(self) -> int:
        """MAX_TRADES_PER_DAY with regime delta applied (floor at 1)."""
        base = int(self.cfg.MAX_TRADES_PER_DAY)
        if base <= 0 or not self.cfg.BUDGET_REGIME_ENABLED:
            return base
        delta = self.cfg.BUDGET_TRADE_CAP_DELTA.get(self.budget_regime(), 0)
        return max(1, base + int(delta))

    def effective_min_score(self) -> float:
        """V2_MIN_SCORE with regime delta applied."""
        base = float(self.cfg.V2_MIN_SCORE)
        if not self.cfg.BUDGET_REGIME_ENABLED:
            return base
        delta = self.cfg.BUDGET_MIN_SCORE_DELTA.get(self.budget_regime(), 0.0)
        return max(0.0, base + float(delta))

    def effective_max_spread(self) -> float:
        """MAX_SPREAD_PCT with regime delta applied (Roadmap #236).

        Smaller accounts have a higher per-trade charge hurdle
        (~0.27% on Rs.50K), so the spread cap is tightened so the
        spread alone cannot eat the edge. NORMAL/LARGE accounts
        keep the 0.30% default. Floor at 0 (= disabled).
        """
        base = float(self.cfg.MAX_SPREAD_PCT)
        if base <= 0 or not self.cfg.BUDGET_REGIME_ENABLED:
            return base
        delta = float(
            getattr(self.cfg, "BUDGET_SPREAD_DELTA", {}).get(
                self.budget_regime(), 0.0
            )
        )
        return max(0.0, base + delta)

    def effective_min_profit(self) -> float:
        """MIN_EXPECTED_PROFIT with regime delta applied (Roadmap #237).

        Charges scale with trade value, so a Rs.135 floor that is 3×
        round-trip charges on a Rs.16K slot becomes only ~1.5× on a
        Rs.50K slot. The regime delta preserves the 3× ratio as the
        budget grows. Floor at 0 (= disabled).
        """
        base = float(self.cfg.MIN_EXPECTED_PROFIT)
        if base <= 0 or not self.cfg.BUDGET_REGIME_ENABLED:
            return base
        delta = float(
            getattr(self.cfg, "BUDGET_MIN_PROFIT_DELTA", {}).get(
                self.budget_regime(), 0.0
            )
        )
        return max(0.0, base + delta)

    # ================================================================
    # MTM / SESSION-STATE INPUTS (Roadmap #166, #192, #194)
    # ================================================================

    def set_latest_quotes(self, quotes: dict) -> None:
        """Cache the latest live quote dict from the monitor loop (#166).

        Used by effective_day_pnl() so circuit-breaker / soft-stop /
        peak-drawdown can include open-position MTM without every call
        site having to thread quotes through. Safe to call with an
        empty dict — falls back to closed-only day_pnl().
        """
        if isinstance(quotes, dict):
            self._latest_quotes = quotes

    def effective_day_pnl(self) -> float:
        """Day P&L with open-position MTM included when enabled (#166).

        Falls back to closed-only `day_pnl()` when MTM_AWARE_CB_ENABLED
        is False, no quotes have been cached yet, or unrealised_pnl()
        raises (e.g. malformed quote dict — never let the safety gate
        crash the bot).

        Just-opened positions (entered between two monitor ticks) won't
        yet appear in the cached quotes dict — `enter_trade` posts the
        order and the next safety-gate read fires before the next
        `set_latest_quotes()`. Without compensation, `unrealised_pnl`
        raises on every such call until the monitor tick refreshes the
        cache, so we synthesise a break-even quote (`last_price =
        entry_price`) for any open position missing from the cache. A
        freshly opened position is at break-even by definition; MTM
        contribution = 0; the real value lands on the next tick (~10s).
        Avoids the spurious `missing quotes` WARNING burst that
        appeared after every fresh entry on 2026-04-23 (INFY, TATACAP).
        """
        closed = self.day_pnl()
        if not getattr(self.cfg, "MTM_AWARE_CB_ENABLED", False):
            return closed
        quotes = getattr(self, "_latest_quotes", None) or {}
        if not quotes:
            return closed
        # Local copy — never mutate the cached dict.
        augmented = dict(quotes)
        added: list[str] = []
        for pos in self.open_positions():
            key = f"{pos['exchange']}:{pos['symbol']}"
            cached = augmented.get(key) or {}
            if "last_price" not in cached:
                augmented[key] = {"last_price": pos["entry_price"]}
                added.append(key)
        if added:
            self.log.debug(
                f"effective_day_pnl: synthesised break-even quote for "
                f"{len(added)} just-opened position(s) {added} "
                f"(real quote arrives next monitor tick)"
            )
        try:
            return closed + self.unrealised_pnl(augmented)
        except Exception as e:
            self.log.warning(
                f"effective_day_pnl: unrealised_pnl failed ({type(e).__name__}: {e}); "
                f"falling back to closed-only day_pnl"
            )
            return closed

    def record_nifty_adx(self, adx: float) -> None:
        """Stamp a NIFTY ADX reading into the rolling buffer (#192)."""
        try:
            val = float(adx)
        except (TypeError, ValueError):
            return
        if val <= 0:
            return
        self._recent_nifty_adx.append(val)

    def record_chop_exit(self, when: datetime.datetime | None = None) -> None:
        """Stamp a STAGNANT_EXIT / SIGNAL_DECAY exit time for #192."""
        self._recent_chop_exits.append(when or now_ist())

    def set_vix_spike(self, active: bool) -> None:
        """Update VIX intraday-spike pause flag (#211).

        Manager calls this each NIFTY-recheck after `_check_vix_spike()`.
        Centralising the state on the engine lets `enter_trade()` honour
        the same pause as the manager-level opportunity / re-scan paths
        (closing the entry-path hole that #181 originally left open).
        """
        self._vix_spike_active = bool(active)

    def is_vix_spike_active(self) -> bool:
        """True if VIX intraday-spike pause is currently active (#211)."""
        return self._vix_spike_active

    def set_tape_breadth(self, info: dict | None) -> None:
        """Stamp the latest scanner tape-breadth snapshot. Manager forwards
        `scanner.last_tape_breadth` each scan. Pass None on a scan with
        no candidates (gate refuses to bypass on stale data).
        """
        self._tape_breadth = info if isinstance(info, dict) else None

    def is_choppy_morning_paused(self, now: datetime.datetime | None = None) -> bool:
        """True if the choppy-morning entry pause is currently active.

        Two conditions arm a fresh pause:
          1. ≥ CHOPPY_PAUSE_MIN_CONSECUTIVE_SCANS NIFTY ADX readings
             below CHOPPY_PAUSE_ADX_THRESHOLD in the recent buffer.
          2. ≥ CHOPPY_PAUSE_MIN_RECENT_STAGNANT_EXITS chop-exits in
             the last CHOPPY_PAUSE_RECENT_EXIT_LOOKBACK_MINUTES.

        Only arms inside [WINDOW_START, WINDOW_END]; sticks past the
        window once armed inside it. Existing positions unaffected.
        Kill-switch: CHOPPY_MORNING_PAUSE_ENABLED.

        Yields to any active directional-pause bypass (NIFTY-bounce or
        tape-breadth divergence) — those are fresher signals than the
        backward-looking chop heuristic. See cross-gate-coverage memo.
        """
        if not getattr(self.cfg, "CHOPPY_MORNING_PAUSE_ENABLED", False):
            return False
        # Yield to any active directional-pause bypass.
        if (
            self._is_intraday_nifty_bouncing("BUY")
            or self._is_intraday_nifty_bouncing("SELL")
            or self._has_breadth_divergence("BUY")
            or self._has_breadth_divergence("SELL")
        ):
            return False
        t = now or now_ist()
        # If a pause is already armed and still in window, honour it.
        until = self._choppy_pause_until
        if until is not None:
            if t < until:
                return True
            # Expired — clear so the next chop episode can re-arm.
            self._choppy_pause_until = None

        # Check arming window.
        start = (self.cfg.CHOPPY_PAUSE_WINDOW_START_HOUR,
                 self.cfg.CHOPPY_PAUSE_WINDOW_START_MINUTE)
        end   = (self.cfg.CHOPPY_PAUSE_WINDOW_END_HOUR,
                 self.cfg.CHOPPY_PAUSE_WINDOW_END_MINUTE)
        cur   = (t.hour, t.minute)
        if not (start <= cur < end):
            return False

        # Condition 1 — sustained low NIFTY ADX.
        need = int(self.cfg.CHOPPY_PAUSE_MIN_CONSECUTIVE_SCANS)
        thresh = float(self.cfg.CHOPPY_PAUSE_ADX_THRESHOLD)
        adx_buf = list(self._recent_nifty_adx)
        if len(adx_buf) < need:
            return False
        if not all(a < thresh for a in adx_buf[-need:]):
            return False

        # Condition 2 — recent chop exits.
        lookback_min = int(self.cfg.CHOPPY_PAUSE_RECENT_EXIT_LOOKBACK_MINUTES)
        cutoff = t - datetime.timedelta(minutes=lookback_min)
        recent_count = sum(1 for ts in self._recent_chop_exits if ts >= cutoff)
        if recent_count < int(self.cfg.CHOPPY_PAUSE_MIN_RECENT_STAGNANT_EXITS):
            return False

        # Arm the pause.
        self._choppy_pause_until = t + datetime.timedelta(
            minutes=int(self.cfg.CHOPPY_PAUSE_MINUTES)
        )
        self.log.warning(
            f"CHOPPY_MORNING_PAUSE armed — NIFTY ADX last {need} scans "
            f"all < {thresh:.1f} AND {recent_count} chop-exits in last "
            f"{lookback_min} min. New entries paused until "
            f"{self._choppy_pause_until.strftime('%H:%M:%S')}."
        )
        return True

    def record_strong_gap_day(self, direction) -> None:
        """Set / clear the strong-gap continuation direction for the session (#194).

        `direction` is one of: "UP" (gap continues prior-day uptrend),
        "DOWN" (gap continues prior-day downtrend), None / False / ""
        (clear the flag). Manager calls this once at session start after
        classifying the opening NIFTY gap. Idempotent — repeated calls
        with the same direction are safe.

        Backwards-compat: a bare True value defaults to "UP" so older
        callsites don't break (and a warning is logged so we notice).
        """
        if direction is True:
            self.log.warning(
                "record_strong_gap_day(True) is deprecated — pass 'UP' or 'DOWN' "
                "so the boost can be applied direction-aware. Defaulting to 'UP'."
            )
            direction = "UP"
        if direction in (None, False, ""):
            new_dir = None
        elif str(direction).upper() in ("UP", "DOWN"):
            new_dir = str(direction).upper()
        else:
            self.log.warning(f"record_strong_gap_day: ignoring unknown direction {direction!r}")
            return
        prev = getattr(self, "_strong_gap_direction", None)
        self._strong_gap_direction = new_dir
        # Keep _strong_gap_day in sync for any legacy reader.
        self._strong_gap_day = new_dir is not None
        if new_dir and new_dir != prev:
            fade_side = "SELL" if new_dir == "UP" else "BUY"
            self.log.info(
                f"STRONG_GAP_ADX_BOOST armed (gap {new_dir}) — ADX threshold "
                f"+{self.cfg.STRONG_GAP_ADX_DELTA:.1f}, override score "
                f"+{self.cfg.STRONG_GAP_OVERRIDE_DELTA:.1f} for {fade_side} "
                f"trades only (fade direction)."
            )

    # ================================================================
    # CHURN-REDUCTION GATE HELPERS (Roadmap #161-164)
    # ================================================================

    def is_lunch_lull(self, now=None) -> bool:
        """True if current time falls inside the configured lunch-lull window."""
        if not self.cfg.LUNCH_LULL_ENABLED:
            return False
        t = now or now_ist()
        start = (self.cfg.LUNCH_LULL_START_HOUR, self.cfg.LUNCH_LULL_START_MINUTE)
        end   = (self.cfg.LUNCH_LULL_END_HOUR,   self.cfg.LUNCH_LULL_END_MINUTE)
        cur   = (t.hour, t.minute)
        return start <= cur < end

    def is_in_re_entry_cooldown(self, symbol: str, side: str, now=None) -> bool:
        """True if same symbol+side was exited within RE_ENTRY_COOLDOWN_MINUTES."""
        if not self.cfg.RE_ENTRY_COOLDOWN_ENABLED:
            return False
        mins = int(self.cfg.RE_ENTRY_COOLDOWN_MINUTES)
        if mins <= 0:
            return False
        last = self._last_exit_time.get(f"{symbol}_{side}")
        if last is None:
            return False
        t = now or now_ist()
        delta_sec = (t - last).total_seconds()
        return 0 <= delta_sec < mins * 60

    def is_soft_stopped(self) -> bool:
        """True if day loss has breached the soft-stop threshold (Roadmap #163).

        Unlike the hard circuit breaker, soft-stop only blocks NEW entries —
        existing positions continue to be managed. Always returns False when
        DAILY_LOSS_SOFT_STOP_PCT <= 0 (kill-switch).
        """
        soft_pct = float(self.cfg.DAILY_LOSS_SOFT_STOP_PCT)
        if soft_pct <= 0:
            return False
        budget = self._budget
        if budget <= 0:
            return False
        return self.effective_day_pnl() <= -budget * soft_pct / 100

    def is_peak_drawdown_stopped(self) -> bool:
        """True if day P&L has given back too much from its intraday peak (Roadmap #168).

        Tracks `_intraday_peak_pnl = max(peak, day_pnl())` on every call.
        Triggers when give-back from peak exceeds PEAK_DRAWDOWN_STOP_PCT of
        budget AND the peak itself was above PEAK_DRAWDOWN_MIN_PEAK_PCT (so
        we don't trip on tiny early-morning swings).

        Like soft-stop, only blocks NEW entries — existing positions
        continue to be managed. Once tripped the state is sticky for the
        session because peak only ratchets up; if pnl recovers above peak,
        peak rises with it and the gap closes naturally. Returns False when
        PEAK_DRAWDOWN_STOP_PCT <= 0 (kill-switch).
        """
        stop_pct = float(self.cfg.PEAK_DRAWDOWN_STOP_PCT)
        if stop_pct <= 0:
            return False
        budget = self._budget
        if budget <= 0:
            return False
        # MTM-aware (#166): include open-position MTM so the gate fires
        # before five bleeders all hit individual SLs.
        pnl = self.effective_day_pnl()
        peak = max(getattr(self, "_intraday_peak_pnl", 0.0), pnl)
        self._intraday_peak_pnl = peak
        min_peak_rs = budget * float(self.cfg.PEAK_DRAWDOWN_MIN_PEAK_PCT) / 100
        if peak < min_peak_rs:
            return False
        give_back = peak - pnl
        return give_back >= budget * stop_pct / 100

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
        """Unrealised P&L from open positions at current prices, including partial exits.

        Raises ValueError if any open position is missing from `quotes` —
        callers that need a fail-safe (e.g. effective_day_pnl for the
        MTM-aware circuit breaker, #166) must catch and degrade gracefully
        instead of letting silent entry_price fallbacks mask real losses.
        """
        total = 0.0
        missing: list[str] = []
        for pos in self.open_positions():
            key = f"{pos['exchange']}:{pos['symbol']}"
            q   = quotes.get(key)
            if q is None or "last_price" not in q:
                missing.append(key)
                continue
            current = q["last_price"]
            if pos["side"] == "BUY":
                total += (current - pos["entry_price"]) * pos["qty"]
            else:
                total += (pos["entry_price"] - current) * pos["qty"]
            total += pos.get("_partial_pnl", 0)
        if missing:
            raise ValueError(
                f"unrealised_pnl: missing quotes for {len(missing)} "
                f"open position(s): {missing}"
            )
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
        """How much of the budget is not currently allocated (loss-adjusted).

        Roadmap #171: in live mode, also clamp to Zerodha's available
        funds — Claude prompts and budget displays must reflect what
        the broker will actually permit.
        """
        cap_remaining = self.loss_adjusted_budget() - self._total_open_exposure()
        if not self.cfg.DRY_RUN and self._available_funds is not None:
            return min(cap_remaining, self._available_funds)
        return cap_remaining

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
