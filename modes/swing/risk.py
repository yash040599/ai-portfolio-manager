# ================================================================
# modes/swing/risk.py
# ================================================================
# Swing risk engine — stop, target, position size, portfolio limits.
#
# SWING_STRATEGY §7 / SWING_ROADMAP S8.
#
# All functions are pure arithmetic. No API calls.
# ================================================================

from __future__ import annotations

import math
from dataclasses import dataclass


# ── Configurable defaults ───────────────────────────────────────

DEFAULT_RISK_PER_TRADE_PCT  = 0.5    # 0.5% of swing capital
DEFAULT_MAX_POSITION_PCT    = 15.0   # 15% of swing capital
DEFAULT_MAX_TOTAL_RISK_PCT  = 5.0    # 5% total open risk
DEFAULT_MAX_SECTOR_PCT      = 30.0   # 30% max sector exposure
DEFAULT_MIN_RR              = 2.0    # minimum R:R
DEFAULT_ATR_STOP_MULT       = 2.0    # stop = entry - 2 × ATR


@dataclass
class RiskResult:
    """Output of the risk computation for one candidate."""
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    risk_per_share: float = 0.0
    reward_per_share: float = 0.0
    rr_ratio: float = 0.0
    suggested_qty: int = 0
    risk_rupees: float = 0.0
    reward_rupees: float = 0.0
    position_value: float = 0.0
    rejected: bool = False
    rejected_reason: str = ""


def compute_entry_risk(
    *,
    current_price: float,
    atr_14: float,
    sma_50: float,
    sma_200: float,
    low_52w: float,
    high_52w: float,
    setup_type: str,
    swing_capital: float,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
    min_rr: float = DEFAULT_MIN_RR,
    atr_mult: float = DEFAULT_ATR_STOP_MULT,
) -> RiskResult:
    """Compute entry price, stop, target, and position size for a
    swing candidate.

    Entry = today's close (the user will enter near this).
    Stop  = max(entry - atr_mult × ATR, below nearest structural level).
    Target = entry + min_rr × risk_per_share (or prior resistance).
    Qty   = risk_budget / risk_per_share, capped by max position value.
    """
    result = RiskResult(entry_price=current_price)

    if current_price <= 0 or atr_14 <= 0 or swing_capital <= 0:
        result.rejected = True
        result.rejected_reason = "Invalid price/ATR/capital"
        return result

    # Stop: ATR-based floor
    atr_stop = current_price - atr_mult * atr_14

    # Structural stop: below recent swing low approximation
    # For simplicity, use max(ATR stop, 95% of current) — the scanner
    # can refine with actual swing-low detection later.
    structural_stop = current_price * 0.95

    # Take the tighter stop (higher value = less risk)
    stop = max(atr_stop, structural_stop)

    # Don't allow stop above 98% of entry (too tight for swing)
    if stop > current_price * 0.98:
        stop = current_price - atr_14  # fallback to 1× ATR

    # Don't allow stop below 85% of entry (too wide)
    if stop < current_price * 0.85:
        stop = current_price * 0.85

    result.stop_price = round(stop, 2)
    result.risk_per_share = round(current_price - stop, 2)

    if result.risk_per_share <= 0:
        result.rejected = True
        result.rejected_reason = "Zero or negative risk per share"
        return result

    # Target: at least min_rr × risk
    target = current_price + min_rr * result.risk_per_share

    # If near 52w high, cap target slightly above it
    if high_52w > 0 and target > high_52w * 1.15:
        target = high_52w * 1.10

    result.target_price = round(target, 2)
    result.reward_per_share = round(target - current_price, 2)
    result.rr_ratio = round(
        result.reward_per_share / result.risk_per_share, 2
    ) if result.risk_per_share > 0 else 0.0

    if result.rr_ratio < min_rr:
        result.rejected = True
        result.rejected_reason = f"R:R {result.rr_ratio:.1f} < {min_rr:.1f}"
        return result

    # Position sizing
    risk_budget = swing_capital * (risk_per_trade_pct / 100.0)
    qty = int(risk_budget / result.risk_per_share)

    # Cap by max position value
    max_value = swing_capital * (max_position_pct / 100.0)
    max_qty_by_value = int(max_value / current_price) if current_price > 0 else 0
    qty = min(qty, max_qty_by_value)

    if qty <= 0:
        result.rejected = True
        result.rejected_reason = "Quantity rounds to zero"
        return result

    result.suggested_qty = qty
    result.risk_rupees = round(qty * result.risk_per_share, 2)
    result.reward_rupees = round(qty * result.reward_per_share, 2)
    result.position_value = round(qty * current_price, 2)

    return result


def check_portfolio_limits(
    *,
    new_risk_rupees: float,
    new_position_value: float,
    new_sector: str,
    existing_positions: list[dict],
    swing_capital: float,
    max_total_risk_pct: float = DEFAULT_MAX_TOTAL_RISK_PCT,
    max_sector_pct: float = DEFAULT_MAX_SECTOR_PCT,
) -> tuple[bool, str]:
    """Check if adding a new position would breach portfolio limits.

    Returns (ok, reason). `existing_positions` should be a list of
    dicts with keys: risk_rupees, position_value, sector, symbol.
    """
    total_existing_risk = sum(p.get("risk_rupees", 0) for p in existing_positions)
    total_risk = total_existing_risk + new_risk_rupees
    max_risk = swing_capital * (max_total_risk_pct / 100.0)

    if total_risk > max_risk:
        return False, (
            f"Total open risk Rs.{total_risk:,.0f} would exceed "
            f"{max_total_risk_pct}% cap (Rs.{max_risk:,.0f})"
        )

    # Sector exposure
    sector_value = sum(
        p.get("position_value", 0) for p in existing_positions
        if p.get("sector") == new_sector
    ) + new_position_value
    max_sector_value = swing_capital * (max_sector_pct / 100.0)

    if sector_value > max_sector_value:
        return False, (
            f"Sector {new_sector} exposure Rs.{sector_value:,.0f} "
            f"would exceed {max_sector_pct}% cap (Rs.{max_sector_value:,.0f})"
        )

    return True, ""


def generate_broker_instruction(
    *,
    symbol: str,
    exchange: str,
    qty: int,
    entry_price: float,
    stop_price: float,
    target_price: float,
) -> dict:
    """Generate the broker instruction card content."""
    return {
        "action": "BUY",
        "product": "CNC",
        "exchange": exchange,
        "symbol": symbol,
        "qty": qty,
        "order_type": "LIMIT",
        "limit_price": round(entry_price, 2),
        "validity": "DAY",
        "stop_plan": {
            "type": "GTT recommended, or manual note",
            "stop_price": round(stop_price, 2),
            "target_price": round(target_price, 2),
        },
        "steps": [
            f"1. Open Zerodha → search {symbol} on {exchange}",
            f"2. BUY with product CNC (delivery). NOT MIS/F&O.",
            f"3. Qty: {qty}",
            f"4. Limit price: Rs.{entry_price:,.2f} (adjust to market if needed)",
            f"5. Set stop/GTT at Rs.{stop_price:,.2f}",
            f"6. Target zone: Rs.{target_price:,.2f}",
            "7. After order fills, return to /swing → click Done → enter actual qty + price",
        ],
    }
