#!/usr/bin/env python3
"""Shared option pricing + NSE charge model for options-mode research.

WHY THIS EXISTS
---------------
`backtest_options.py` (v1.0 directional buying) prices only ATM options via
the Brenner-Subrahmanyam approximation. That is fine when every trade is ATM,
but useless for any spread strategy: an iron condor's entire P&L lives in the
OTM wings, and BS-ATM says nothing about them.

This module gives a real European Black-Scholes pricer (Indian index options
are European-settled) plus a volatility smile, so OTM strikes are priced with
the skew that actually exists in the NIFTY chain.

HONESTY NOTE — THIS IS A MODEL, NOT MARKET DATA
-----------------------------------------------
Every premium produced here is synthetic. We do not yet have historical NIFTY
option premiums (see `record_option_chain.py --probe`). Any backtest built on
this module is a *filter* — good enough to kill a bad idea cheaply, not good
enough to authorise real money. The smile parameters below are calibrated by
eye against typical NIFTY weekly quotes and are deliberately exposed as knobs
so callers can run sensitivity sweeps instead of trusting one point estimate.

REGULATORY CONSTANTS
--------------------
Charge rates and lot size change with almost every Union Budget. Each constant
below carries the date it was last checked. Re-verify before trusting a P&L
number; `verify_charges()` prints them all for a quick eyeball.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

# ── Market conventions (VERIFY before trusting P&L) ──────────────────
# Confirmed 2026-08-08 from Kite's own NFO instrument dump via
# `record_option_chain.py --probe`. Do not set this from memory: the
# contract size has moved twice since 2024, config.py still says 25, and
# an earlier guess of 75 was also wrong.
NIFTY_LOT_SIZE = 65              # Kite-reported, 2026-08-08
NIFTY_STRIKE_STEP = 50

RISK_FREE_RATE = 0.065           # ~10y G-Sec, used for discounting only

# ── NSE F&O option charges (per leg unless noted) ────────────────────
# STT on options SALE was raised 0.0625% -> 0.1% of premium (Oct 2024).
# backtest_options.py still uses 0.0625%, i.e. it under-charges every trade.
STT_SELL_PCT = 0.10              # % of sell-side premium turnover
EXCHANGE_TXN_PCT = 0.0495        # % of premium turnover, both sides (NSE)
SEBI_FEE_PCT = 0.0001            # % of turnover (Rs.10 per crore)
STAMP_DUTY_PCT = 0.003           # % of buy-side premium turnover
BROKERAGE_PER_ORDER = 20.0       # Zerodha flat, per executed order
GST_PCT = 18.0                   # on brokerage + exchange + SEBI fees

# STT on options left to expire ITM is charged on INTRINSIC value at
# 0.125%, not on premium. That is an order of magnitude worse than
# squaring off, which is why every strategy here exits before expiry.
STT_EXERCISE_PCT = 0.125

# ── Volatility smile (calibrated by eye — treat as an assumption) ────
# sigma(K) = atm_vol x (1 + curv*u^2 - slope*u),  u = ln(K/S) / 0.02
# so `u` is "how many 2% steps away from spot". Defaults put a 2% OTM
# put ~16% richer than ATM and a 2% OTM call ~4% cheaper, which is the
# usual NIFTY shape (crash insurance is bid, upside is not).
SMILE_CURV = 0.06
SMILE_SLOPE = 0.10
SMILE_UNIT = 0.02                # log-moneyness that counts as "one step"

_N = NormalDist()


# ════════════════════════════════════════════════════════════════════
# BLACK-SCHOLES
# ════════════════════════════════════════════════════════════════════

def bs_price(spot: float, strike: float, years: float, sigma: float,
             kind: str, rate: float = RISK_FREE_RATE) -> float:
    """European option price. Falls back to intrinsic at/after expiry."""
    kind = kind.upper()
    if spot <= 0 or strike <= 0:
        return 0.0
    if years <= 0 or sigma <= 0:
        intrinsic = (spot - strike) if kind == "CE" else (strike - spot)
        return max(0.0, intrinsic)

    vol_t = sigma * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma ** 2) * years) / vol_t
    d2 = d1 - vol_t
    discount = math.exp(-rate * years)

    if kind == "CE":
        return spot * _N.cdf(d1) - strike * discount * _N.cdf(d2)
    return strike * discount * _N.cdf(-d2) - spot * _N.cdf(-d1)


def bs_delta(spot: float, strike: float, years: float, sigma: float,
             kind: str, rate: float = RISK_FREE_RATE) -> float:
    """Option delta. CE in [0,1], PE in [-1,0]."""
    kind = kind.upper()
    if years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        if kind == "CE":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0

    vol_t = sigma * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma ** 2) * years) / vol_t
    return _N.cdf(d1) if kind == "CE" else _N.cdf(d1) - 1.0


# ════════════════════════════════════════════════════════════════════
# VOLATILITY SMILE
# ════════════════════════════════════════════════════════════════════

def smile_vol(spot: float, strike: float, atm_vol: float, *,
              curv: float = SMILE_CURV, slope: float = SMILE_SLOPE) -> float:
    """Implied vol for one strike, given the ATM level.

    Pass ``curv=0, slope=0`` for a flat-vol control run. Flat vol makes a
    condor look better than it is: we sell the near wing and buy the far
    wing, and skew makes the far wing relatively more expensive.
    """
    if spot <= 0 or strike <= 0 or atm_vol <= 0:
        return max(atm_vol, 0.0)
    u = math.log(strike / spot) / SMILE_UNIT
    factor = 1.0 + curv * u * u - slope * u
    return max(0.01, atm_vol * factor)


def price_strike(spot: float, strike: float, years: float, atm_vol: float,
                 kind: str, *, curv: float = SMILE_CURV,
                 slope: float = SMILE_SLOPE) -> float:
    """Smile-adjusted premium for one strike."""
    sigma = smile_vol(spot, strike, atm_vol, curv=curv, slope=slope)
    return bs_price(spot, strike, years, sigma, kind)


def implied_vol(price: float, spot: float, strike: float, years: float,
                kind: str, rate: float = RISK_FREE_RATE,
                lo: float = 0.01, hi: float = 3.0) -> float | None:
    """Invert Black-Scholes for sigma. None when the price is unattainable.

    Bisection rather than Newton: vega collapses for deep-OTM and
    near-expiry contracts, which is precisely the region we care about,
    and a vanishing derivative makes Newton diverge there.
    """
    if price <= 0 or spot <= 0 or strike <= 0 or years <= 0:
        return None
    intrinsic = max(0.0, (spot - strike) if kind.upper() == "CE" else (strike - spot))
    if price < intrinsic:
        return None                      # arbitrage or stale print
    if bs_price(spot, strike, years, hi, kind, rate) < price:
        return None                      # beyond the search range

    for _ in range(80):
        mid = (lo + hi) / 2
        if bs_price(spot, strike, years, mid, kind, rate) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def realised_vol(closes: list[float]) -> float | None:
    """Annualised close-to-close volatility (decimal) over the window."""
    if len(closes) < 3:
        return None
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes))
            if closes[i] > 0 and closes[i - 1] > 0]
    if len(rets) < 2:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


# ════════════════════════════════════════════════════════════════════
# VOLATILITY ESTIMATION
# ════════════════════════════════════════════════════════════════════

def parkinson_vol(candles: list[dict], lookback: int = 20,
                  default: float = 0.14) -> float:
    """Annualised vol (decimal) from daily high/low ranges.

    Parkinson uses the intraday range rather than close-to-close, so it
    needs far fewer bars for the same standard error.
    """
    if not candles:
        return default
    recent = candles[-lookback:] if len(candles) > lookback else candles
    terms = [
        math.log(c["high"] / c["low"]) ** 2
        for c in recent
        if c.get("low", 0) > 0 and c.get("high", 0) > c.get("low", 0)
    ]
    if not terms:
        return default
    daily_var = sum(terms) / (4 * math.log(2) * len(terms))
    return math.sqrt(daily_var) * math.sqrt(252)


def years_to_expiry(days: float, intraday_hours: float = 0.0) -> float:
    """Convert DTE (+ optional remaining hours today) to year fraction.

    A 09:30 entry on expiry day is ~6 trading hours from settlement, which
    is 0.25 of a calendar day — small, but it is the whole reason 0-DTE
    premium is what it is, so it must not be rounded to zero.
    """
    return max(0.0, days + intraday_hours / 24.0) / 365.0


# ════════════════════════════════════════════════════════════════════
# CHARGES
# ════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Leg:
    """One option leg of a trade. ``qty`` is units, not lots."""
    kind: str            # "CE" | "PE"
    strike: float
    side: str            # "BUY" | "SELL"
    entry_premium: float
    exit_premium: float
    qty: int


def leg_charges(leg: Leg) -> float:
    """Round-trip statutory + broker charges for one leg.

    Both directions are charged: an opening SELL closes with a BUY, so STT
    applies to whichever side was the sale. Assumes the position is squared
    off (never exercised) — see STT_EXERCISE_PCT for why that matters.
    """
    open_turnover = leg.entry_premium * leg.qty
    close_turnover = leg.exit_premium * leg.qty
    sell_turnover = open_turnover if leg.side.upper() == "SELL" else close_turnover
    buy_turnover = close_turnover if leg.side.upper() == "SELL" else open_turnover
    turnover = open_turnover + close_turnover

    brokerage = BROKERAGE_PER_ORDER * 2
    stt = sell_turnover * STT_SELL_PCT / 100
    exchange = turnover * EXCHANGE_TXN_PCT / 100
    sebi = turnover * SEBI_FEE_PCT / 100
    stamp = buy_turnover * STAMP_DUTY_PCT / 100
    gst = (brokerage + exchange + sebi) * GST_PCT / 100

    return brokerage + stt + exchange + sebi + stamp + gst


def trade_charges(legs: list[Leg]) -> float:
    """Total charges across every leg of a multi-leg trade."""
    return round(sum(leg_charges(leg) for leg in legs), 2)


def verify_charges() -> str:
    """Print the rate card so the constants can be eyeballed against
    Zerodha's brokerage calculator before anyone trusts a P&L number."""
    return "\n".join([
        "  NSE F&O option charge model (verify against Zerodha calculator):",
        f"    Lot size (NIFTY)      : {NIFTY_LOT_SIZE}",
        f"    Brokerage / order     : Rs.{BROKERAGE_PER_ORDER:.0f}",
        f"    STT (sell, premium)   : {STT_SELL_PCT}%",
        f"    STT (ITM at expiry)   : {STT_EXERCISE_PCT}% of INTRINSIC — avoid",
        f"    Exchange txn          : {EXCHANGE_TXN_PCT}%",
        f"    SEBI fee              : {SEBI_FEE_PCT}%",
        f"    Stamp duty (buy)      : {STAMP_DUTY_PCT}%",
        f"    GST                   : {GST_PCT}% on brokerage+exchange+SEBI",
    ])


if __name__ == "__main__":
    # Sanity check: a NIFTY 24000 weekly at 14% vol should print premiums
    # in the same neighbourhood as a real chain.
    print(verify_charges())
    spot, atm = 24000.0, 0.14
    for label, dte, hours in (("5 DTE", 5, 0), ("1 DTE", 1, 0), ("0 DTE 09:30", 0, 6)):
        t = years_to_expiry(dte, hours)
        print(f"\n  {label}  (T={t:.6f} yr, ATM vol {atm:.0%})")
        for offset in (0, 100, 200, 300):
            ce = price_strike(spot, spot + offset, t, atm, "CE")
            pe = price_strike(spot, spot - offset, t, atm, "PE")
            print(f"    {offset:>4}pt OTM   CE Rs.{ce:7.2f}   PE Rs.{pe:7.2f}")
