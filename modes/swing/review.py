# ================================================================
# modes/swing/review.py
# ================================================================
# Open-position review engine (SWING_ROADMAP S9).
#
# Each daily run reviews existing swing positions before scanning
# for new entries. Produces an action recommendation per position:
# HOLD, TIGHTEN_STOP, PARTIAL_EXIT, FULL_EXIT, or WATCH.
#
# Uses an industry-standard exit stack — not just "hold until
# stop or target" (SWING_STRATEGY §8.2).
# ================================================================

from __future__ import annotations

import datetime

from modes.swing.signals import compute_swing_indicators, _atr
from modes.swing.types import (
    SwingAction, SwingPosition,
    ACTION_HOLD, ACTION_TIGHTEN_STOP, ACTION_PARTIAL_EXIT,
    ACTION_FULL_EXIT, ACTION_WATCH, STATUS_PENDING,
)
from config import now_ist


# ── Configurable thresholds ─────────────────────────────────────

TIME_STOP_DAYS = 10       # no progress after N trading days
TRAIL_1R_TIGHTEN = True   # at +1R, move stop toward breakeven
TRAIL_2R_PARTIAL = True   # at +2R, recommend partial exit


def review_position(
    pos: SwingPosition,
    daily_candles: list[dict],
    nifty_candles: list[dict] | None = None,
) -> SwingAction:
    """Review one open swing position. Returns a recommended action.

    For ATH_DIP positions (detected by notes containing 'ATH'), uses
    the simple target-based exit. For technical swing positions, uses
    the full industry-standard exit stack.
    """
    ts = now_ist().isoformat()
    action = SwingAction(
        position_id=pos.position_id,
        symbol=pos.symbol,
        exchange=pos.exchange,
        action_type=ACTION_HOLD,
        status=STATUS_PENDING,
        created_at=ts,
    )

    if not daily_candles or len(daily_candles) < 10:
        action.notes = "Insufficient candle data for review"
        return action

    current = daily_candles[-1]["close"]

    # ── ATH dip positions: simple target/stop exit ──────────────
    is_ath = "ATH" in (pos.notes or "").upper()
    if is_ath:
        entry = pos.entry_price
        target = pos.target_price or (entry * 1.15)
        stop = pos.stop_price

        if current <= stop:
            action.action_type = ACTION_FULL_EXIT
            action.suggested_price = current
            action.suggested_qty = pos.managed_qty
            action.notes = (f"ATH position hit stop: Rs.{current:,.2f} "
                            f"<= Rs.{stop:,.2f}. Exit to limit loss.")
            return action

        if current >= target * 0.98:
            action.action_type = ACTION_FULL_EXIT
            action.suggested_price = current
            action.suggested_qty = pos.managed_qty
            pnl_pct = ((current / entry) - 1) * 100
            action.notes = (f"ATH position near target: Rs.{current:,.2f} "
                            f"(+{pnl_pct:.1f}% from entry). Take profits.")
            return action

        # Still holding — show progress
        pnl_pct = ((current / entry) - 1) * 100
        to_target = ((target / current) - 1) * 100
        action.notes = (f"ATH position: {pnl_pct:+.1f}% from entry, "
                        f"{to_target:.1f}% to target. Hold.")
        return action

    ind = compute_swing_indicators(daily_candles, nifty_candles)
    if not ind.get("valid"):
        action.notes = "Invalid indicators"
        return action

    current = ind["current"]
    entry = pos.entry_price
    stop = pos.stop_price
    target = pos.target_price or (entry + 2 * (entry - stop))

    if entry <= 0 or stop <= 0:
        action.notes = "Invalid entry/stop"
        return action

    risk_per_share = entry - stop
    if risk_per_share <= 0:
        action.notes = "Invalid risk (stop >= entry)"
        return action

    r_multiple = (current - entry) / risk_per_share
    reasons: list[str] = []

    # ── Exit stack (highest priority first) ─────────────────────

    # 1. Hard stop breach
    if current < stop:
        action.action_type = ACTION_FULL_EXIT
        action.suggested_price = current
        action.suggested_qty = pos.managed_qty
        action.notes = (f"Price Rs.{current:,.2f} below stop Rs.{stop:,.2f}. "
                        "Exit to limit loss.")
        return action

    # 2. Target zone reached
    if target > 0 and current >= target * 0.98:
        action.action_type = ACTION_PARTIAL_EXIT
        action.suggested_price = current
        action.suggested_qty = max(1, pos.managed_qty // 3)
        action.notes = (f"Near target Rs.{target:,.2f}. Book partial; "
                        "trail rest with tighter stop.")
        return action

    # 3. SMA-50 break for trend setups
    if ind["sma_50"] > 0 and current < ind["sma_50"] * 0.98:
        if ind["ema_20"] < ind["sma_50"]:
            action.action_type = ACTION_FULL_EXIT
            action.suggested_price = current
            action.suggested_qty = pos.managed_qty
            action.notes = (f"Price broke SMA-50 ({ind['sma_50']:.2f}) "
                            "and EMA-20 crossed below. Trend broken.")
            return action

    # 4. Weekly trend break
    if not ind["weekly_trend_up"]:
        reasons.append("Weekly trend turned down")

    # 5. RS vs NIFTY deterioration
    if ind["rel_strength"] < -5:
        reasons.append(f"RS vs NIFTY weak ({ind['rel_strength']:+.1f}%)")

    # 6. Time stop
    age_days = 0
    if pos.entry_date:
        try:
            entry_dt = datetime.datetime.fromisoformat(pos.entry_date).date()
            age_days = (now_ist().date() - entry_dt).days
        except (TypeError, ValueError):
            pass

    if age_days > TIME_STOP_DAYS and r_multiple < 0.5:
        action.action_type = ACTION_FULL_EXIT
        action.suggested_price = current
        action.suggested_qty = pos.managed_qty
        action.notes = (f"Time stop: {age_days} days, only {r_multiple:.1f}R. "
                        "No progress — exit to free capital.")
        return action

    # ── Trailing stop / tighten ─────────────────────────────────

    # At +1R: suggest tightening stop toward breakeven
    if TRAIL_1R_TIGHTEN and r_multiple >= 1.0:
        new_stop = entry + 0.5 * risk_per_share  # halfway to breakeven+
        if new_stop > stop:
            action.action_type = ACTION_TIGHTEN_STOP
            action.suggested_stop = round(new_stop, 2)
            action.notes = (f"At {r_multiple:.1f}R. Tighten stop from "
                            f"Rs.{stop:,.2f} to Rs.{new_stop:,.2f} "
                            "(reduce risk).")
            return action

    # At +2R: suggest partial exit
    if TRAIL_2R_PARTIAL and r_multiple >= 2.0:
        action.action_type = ACTION_PARTIAL_EXIT
        action.suggested_price = current
        action.suggested_qty = max(1, pos.managed_qty // 3)
        action.notes = (f"At {r_multiple:.1f}R. Book 1/3 partial; "
                        "trail remainder under recent swing low.")
        return action

    # ── Watch conditions ────────────────────────────────────────

    if reasons:
        action.action_type = ACTION_WATCH
        action.notes = "Watch: " + "; ".join(reasons)
        return action

    # ── Default: HOLD ───────────────────────────────────────────

    action.action_type = ACTION_HOLD
    action.notes = (f"Thesis intact. {r_multiple:.1f}R, RSI {ind['rsi']:.0f}, "
                    f"{'above' if current > ind['sma_50'] else 'below'} SMA-50.")
    return action
