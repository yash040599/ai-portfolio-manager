#!/usr/bin/env python3
"""Options Mode — Phase O-3.2: Expiry-Week Iron Condor Backtest.

STRATEGY: "Regime-Gated Short Iron Condor into Weekly Expiry"
  Sell an OTM call and an OTM put, buy further-OTM wings for protection,
  hold into weekly expiry, square off at the expiry-day close.

  Net credit is collected up front. The trade wins whenever NIFTY settles
  between the two short strikes. Max loss is capped at
  (wing_width - credit) x lot, and is known before entry — this is a
  defined-risk structure, so it never trips the naked-sell hard block.

WHY THIS AND NOT MORE DIRECTIONAL BUYING
----------------------------------------
v1.0 directional buying failed at PF 0.42, and a 30-combo SL/target sweep
found nothing above 0.53 — the gap signal simply is not accurate enough to
pay for theta. A condor inverts the problem: it needs NIFTY to do nothing,
and theta works FOR the position. The regime classifier that hurt equity on
RANGE days (PF 0.62) is exactly the filter a premium seller wants.

  v1.0 needed a good directional signal.  We do not have one.
  This needs NIFTY to stay inside a range.  We can measure that.

WHAT THIS BACKTEST CAN AND CANNOT TELL YOU
------------------------------------------
CANNOT — premiums are synthetic (Black-Scholes + an assumed smile), because
we have no historical NIFTY option chain yet. The credit collected is the
single most important number in the strategy and it is *modelled*. Treat any
PF here as a filter, never as authorisation to trade.

CANNOT — NIFTY 15-minute data is empty in the backtest DB (0 rows), so
intraday path is approximated from daily OHLC. Stop-losses assume the
worst-case touch (the day's extreme is hit before any recovery), which is
deliberately pessimistic.

CAN — tell us whether the idea survives its own assumptions. The sensitivity
sweep re-runs across IV level and skew; an edge that only exists at one
parameter setting is not an edge. That verdict is worth having cheaply,
before spending weeks collecting real chain data.

Usage:
    python scripts/trade/backtest_options_condor.py
    python scripts/trade/backtest_options_condor.py --dte 1 --short-delta 0.20
    python scripts/trade/backtest_options_condor.py --sweep
    python scripts/trade/backtest_options_condor.py --sensitivity
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest_gates import DAILY_DB  # noqa: E402
from backtest_options import classify_day, load_nifty_daily  # noqa: E402
from option_pricing import (  # noqa: E402
    NIFTY_LOT_SIZE, NIFTY_STRIKE_STEP, Leg, bs_delta, parkinson_vol,
    price_strike, smile_vol, trade_charges, verify_charges, years_to_expiry,
)

# ── Walk-forward windows (same split as every other strategy here) ───
WINDOWS = {
    "FULL":  (None, None),
    "TRAIN": ("2024-05-01", "2025-05-31"),
    "TEST":  ("2025-06-01", "2026-05-22"),
}

# ── Defaults ─────────────────────────────────────────────────────────
DEFAULT_DTE = 1              # entry N trading days before expiry
DEFAULT_SHORT_DELTA = 0.20   # sell the ~20-delta wings (≈80% OTM prob)
DEFAULT_WING_POINTS = 200    # protection bought this far beyond the short
DEFAULT_SL_MULT = 2.0        # cut at 2x credit lost; 0 disables
DEFAULT_IV_UPLIFT = 1.35     # realised vol under-states option IV
DEFAULT_LOTS = 1

# Expiry weekday. NSE moved NIFTY weekly expiry from Thursday to Tuesday
# partway through the sample, so a single weekday mis-dates every trade on
# one side of the switch. `--expiry-switch` is the first date on which the
# new weekday applies; confirm the live weekday with
# `record_option_chain.py --probe` (reported Tuesday on 2026-08-08).
DEFAULT_EXPIRY_WEEKDAY = 3        # Mon=0 .. Thu=3, before the switch
DEFAULT_EXPIRY_WEEKDAY_NEW = 1    # Tuesday, after the switch
DEFAULT_EXPIRY_SWITCH = "2025-09-01"

ENTRY_HOURS_LEFT = 6.0       # 09:30 entry ≈ 6h before a 15:30 settlement


# ════════════════════════════════════════════════════════════════════
# EXPIRY CALENDAR
# ════════════════════════════════════════════════════════════════════

def expiry_days(candles: list[dict], weekday: int,
                weekday_new: int | None = None,
                switch_date: str | None = None) -> list[int]:
    """Indices of candles that are weekly expiry days.

    Derived from the traded calendar rather than a hardcoded holiday list:
    the expiry of a week is its last trading day at or before the weekday
    then in force. That automatically absorbs holiday-shifted expiries,
    which is the same rule modes/trade/manager.py applies for equity.

    `weekday_new` + `switch_date` handle NSE's mid-sample move of NIFTY
    weeklies to a different weekday.
    """
    by_week: dict[tuple[int, int], int] = {}
    for i, c in enumerate(candles):
        d = datetime.date.fromisoformat(c["date"])
        wd = weekday
        if weekday_new is not None and switch_date and c["date"] >= switch_date:
            wd = weekday_new
        if d.weekday() > wd:
            continue
        iso = d.isocalendar()
        key = (iso[0], iso[1])
        # Later index in the same ISO week wins → last eligible trading day.
        by_week[key] = i
    return sorted(by_week.values())


# ════════════════════════════════════════════════════════════════════
# STRIKE SELECTION
# ════════════════════════════════════════════════════════════════════

def _round_strike(price: float) -> int:
    return int(round(price / NIFTY_STRIKE_STEP) * NIFTY_STRIKE_STEP)


def select_short_strikes(spot: float, years: float, atm_vol: float,
                         target_delta: float, *, curv: float,
                         slope: float) -> tuple[int, int]:
    """Pick short call / short put strikes nearest `target_delta`.

    Delta-based selection rather than fixed points, because a fixed
    200-point wing is a very different bet at 10% vol than at 20% vol.
    Delta normalises for that automatically, which is why desks quote
    condors as "16-delta" rather than "200 points".
    """
    call_strike = put_strike = _round_strike(spot)
    best_call = best_put = float("inf")

    # Walk outwards far enough to cover deep-OTM at high vol.
    for step in range(0, 81):
        offset = step * NIFTY_STRIKE_STEP

        k_c = _round_strike(spot) + offset
        d_c = abs(bs_delta(spot, k_c, years,
                           smile_vol(spot, k_c, atm_vol, curv=curv, slope=slope),
                           "CE"))
        if abs(d_c - target_delta) < best_call:
            best_call, call_strike = abs(d_c - target_delta), k_c

        k_p = _round_strike(spot) - offset
        d_p = abs(bs_delta(spot, k_p, years,
                           smile_vol(spot, k_p, atm_vol, curv=curv, slope=slope),
                           "PE"))
        if abs(d_p - target_delta) < best_put:
            best_put, put_strike = abs(d_p - target_delta), k_p

    return call_strike, put_strike


# ════════════════════════════════════════════════════════════════════
# POSITION VALUATION
# ════════════════════════════════════════════════════════════════════

def condor_value(spot: float, strikes: dict, years: float, atm_vol: float,
                 *, curv: float, slope: float) -> float:
    """Cost to buy the condor back, per unit.

    Positive = what we owe to close. Entry credit minus this is the P&L.
    """
    sc, sp = strikes["short_call"], strikes["short_put"]
    lc, lp = strikes["long_call"], strikes["long_put"]
    kw = {"curv": curv, "slope": slope}
    return (
        price_strike(spot, sc, years, atm_vol, "CE", **kw)
        + price_strike(spot, sp, years, atm_vol, "PE", **kw)
        - price_strike(spot, lc, years, atm_vol, "CE", **kw)
        - price_strike(spot, lp, years, atm_vol, "PE", **kw)
    )


def settlement_value(spot: float, strikes: dict) -> float:
    """Condor buy-back cost at expiry — pure intrinsic, no time value."""
    sc, sp = strikes["short_call"], strikes["short_put"]
    lc, lp = strikes["long_call"], strikes["long_put"]
    return (
        max(0.0, spot - sc) + max(0.0, sp - spot)
        - max(0.0, spot - lc) - max(0.0, lp - spot)
    )


# ════════════════════════════════════════════════════════════════════
# SIMULATION
# ════════════════════════════════════════════════════════════════════

def simulate(candles: list[dict], *, dte: int, short_delta: float,
             wing: int, sl_mult: float, iv_uplift: float, lots: int,
             skip_regimes: set[str], expiry_weekday: int,
             curv: float, slope: float,
             expiry_weekday_new: int | None = None,
             expiry_switch: str | None = None,
             start: str | None = None, end: str | None = None) -> list[dict]:
    """Run the condor over every weekly expiry in the sample."""
    trades: list[dict] = []
    qty = lots * NIFTY_LOT_SIZE
    exp_idx = expiry_days(candles, expiry_weekday,
                          expiry_weekday_new, expiry_switch)

    for ei in exp_idx:
        entry_i = ei - dte
        if entry_i < 25:            # need 20 bars of vol history + buffer
            continue

        entry_c = candles[entry_i]
        date_str = entry_c["date"]
        if start and date_str < start:
            continue
        if end and date_str > end:
            continue

        # ── Regime gate. Strictly prior bars only — no lookahead. ──
        prev_close = candles[entry_i - 1]["close"]
        regime, _direction = classify_day(
            entry_c, prev_close, candles[entry_i - 6:entry_i])
        if regime in skip_regimes:
            continue

        # ── Vol estimate from bars strictly before entry ───────────
        atm_vol = parkinson_vol(candles[:entry_i], lookback=20) * iv_uplift
        if atm_vol <= 0:
            continue

        spot = entry_c["open"]
        days_left = ei - entry_i
        t_entry = years_to_expiry(days_left, ENTRY_HOURS_LEFT)
        if t_entry <= 0:
            continue

        sc, sp = select_short_strikes(spot, t_entry, atm_vol, short_delta,
                                      curv=curv, slope=slope)
        if sc <= sp:                # vol so high the wings crossed — skip
            continue
        strikes = {
            "short_call": sc, "short_put": sp,
            "long_call": sc + wing, "long_put": sp - wing,
        }

        credit = condor_value(spot, strikes, t_entry, atm_vol,
                              curv=curv, slope=slope)
        if credit <= 0:
            continue

        max_loss_per_unit = wing - credit
        if max_loss_per_unit <= 0:  # mispriced by the model — reject
            continue

        # ── Walk each day from entry to expiry ────────────────────
        exit_value = None
        exit_reason = "SETTLEMENT"
        exit_date = candles[ei]["date"]

        for j in range(entry_i, ei + 1):
            c = candles[j]
            is_expiry_bar = (j == ei)
            hours = ENTRY_HOURS_LEFT if j == entry_i else 0.0
            t_now = years_to_expiry(ei - j, hours)

            if sl_mult > 0 and not is_expiry_bar:
                # Worst-case intraday touch: value the condor at whichever
                # extreme hurts more. Daily bars cannot tell us whether the
                # extreme preceded a recovery, so we assume it did not.
                worst = max(
                    condor_value(c["high"], strikes, t_now, atm_vol,
                                 curv=curv, slope=slope),
                    condor_value(c["low"], strikes, t_now, atm_vol,
                                 curv=curv, slope=slope),
                )
                if worst >= credit * (1 + sl_mult):
                    exit_value = credit * (1 + sl_mult)
                    exit_reason = "STOP_LOSS"
                    exit_date = c["date"]
                    break

            if is_expiry_bar:
                # Square off at the close. Never let it expire ITM: STT on
                # exercise is 0.125% of INTRINSIC, not of premium.
                if sl_mult > 0:
                    worst = max(settlement_value(c["high"], strikes),
                                settlement_value(c["low"], strikes))
                    if worst >= credit * (1 + sl_mult):
                        exit_value = credit * (1 + sl_mult)
                        exit_reason = "STOP_LOSS"
                        exit_date = c["date"]
                        break
                exit_value = settlement_value(c["close"], strikes)
                exit_reason = "SETTLEMENT"
                exit_date = c["date"]

        if exit_value is None:
            continue

        gross = (credit - exit_value) * qty

        legs = [
            Leg("CE", strikes["short_call"], "SELL",
                price_strike(spot, sc, t_entry, atm_vol, "CE", curv=curv, slope=slope),
                max(0.05, exit_value / 2), qty),
            Leg("PE", strikes["short_put"], "SELL",
                price_strike(spot, sp, t_entry, atm_vol, "PE", curv=curv, slope=slope),
                max(0.05, exit_value / 2), qty),
            Leg("CE", strikes["long_call"], "BUY",
                price_strike(spot, strikes["long_call"], t_entry, atm_vol, "CE", curv=curv, slope=slope),
                0.05, qty),
            Leg("PE", strikes["long_put"], "BUY",
                price_strike(spot, strikes["long_put"], t_entry, atm_vol, "PE", curv=curv, slope=slope),
                0.05, qty),
        ]
        charges = trade_charges(legs)
        net = gross - charges

        margin = max_loss_per_unit * qty     # capital genuinely at risk
        trades.append({
            "entry_date": date_str,
            "exit_date": exit_date,
            "regime": regime,
            "spot": round(spot, 2),
            "settle": round(candles[ei]["close"], 2),
            "short_call": sc, "short_put": sp,
            "credit": round(credit, 2),
            "exit_value": round(exit_value, 2),
            "atm_vol": round(atm_vol * 100, 2),
            "gross": round(gross, 2),
            "charges": round(charges, 2),
            "net": round(net, 2),
            "margin": round(margin, 2),
            "return_pct": round(net / margin * 100, 3) if margin > 0 else 0.0,
            "exit_reason": exit_reason,
        })

    return trades


# ════════════════════════════════════════════════════════════════════
# METRICS
# ════════════════════════════════════════════════════════════════════

def metrics(trades: list[dict], label: str) -> dict:
    """Profit factor et al. on absolute rupee P&L.

    Deliberately not reusing backtest_gates.compute_metrics: that works in
    "% of entry price", which is meaningless for a credit spread where the
    entry cost is negative. Return here is measured against margin at risk.
    """
    if not trades:
        return {"label": label, "trades": 0, "note": "No trades"}

    nets = [t["net"] for t in trades]
    rets = [t["return_pct"] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for n in nets:
        equity += n
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        # ~52 weekly expiries a year.
        sharpe = mu / sd * math.sqrt(52) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    by_reason: dict[str, int] = defaultdict(int)
    by_regime: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_reason[t["exit_reason"]] += 1
        by_regime[t["regime"]].append(t["net"])

    regime_stats = {}
    for reg, vals in by_regime.items():
        w = [v for v in vals if v > 0]
        gl = abs(sum(v for v in vals if v <= 0))
        regime_stats[reg] = {
            "trades": len(vals),
            "win_rate": round(len(w) / len(vals) * 100, 1),
            "pf": round(sum(w) / gl, 2) if gl > 0 else float("inf"),
            "net": round(sum(vals), 2),
        }

    return {
        "label": label,
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "profit_factor": round(pf, 2),
        "sharpe": round(sharpe, 2),
        "net_pnl": round(sum(nets), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "max_drawdown": round(max_dd, 2),
        "avg_return_pct": round(sum(rets) / len(rets), 2),
        "avg_margin": round(sum(t["margin"] for t in trades) / len(trades), 2),
        "total_charges": round(sum(t["charges"] for t in trades), 2),
        "exit_reasons": dict(by_reason),
        "regime_stats": regime_stats,
    }


def print_metrics(m: dict) -> None:
    if m.get("trades", 0) == 0:
        print(f"    {m['label']:<26} no trades")
        return
    verdict = "PASS" if m["profit_factor"] >= 1.15 else "FAIL"
    print(f"    {m['label']:<26} n={m['trades']:<4} WR={m['win_rate']:>5.1f}%  "
          f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>6.2f}  "
          f"net=Rs.{m['net_pnl']:>11,.0f}  maxDD=Rs.{m['max_drawdown']:>10,.0f}  "
          f"[{verdict}]")


# ════════════════════════════════════════════════════════════════════# MODEL-FREE DIAGNOSTIC
# ═════════════════════════════════════════════════════════════════════

def diagnose(candles: list[dict], *, dte: int, short_delta: float,
             iv_uplift: float, skip_regimes: set[str], expiry_weekday: int,
             curv: float, slope: float,
             expiry_weekday_new: int | None = None,
             expiry_switch: str | None = None) -> None:
    """Compare realised NIFTY moves against the corridor the model sold.

    A losing backtest is only believable if the losses trace to something
    true about the market. If the observed breach rate matches what the
    chosen delta implies, the pricer is calibrated and the verdict stands;
    if it does not, the P&L is a bug rather than a finding.
    """
    moves: list[float] = []
    rows: list[tuple] = []
    for ei in expiry_days(candles, expiry_weekday,
                          expiry_weekday_new, expiry_switch):
        entry_i = ei - dte
        if entry_i < 25:
            continue
        entry_c = candles[entry_i]
        regime, _ = classify_day(entry_c, candles[entry_i - 1]["close"],
                                 candles[entry_i - 6:entry_i])
        if regime in skip_regimes:
            continue
        atm_vol = parkinson_vol(candles[:entry_i], 20) * iv_uplift
        spot = entry_c["open"]
        t = years_to_expiry(ei - entry_i, ENTRY_HOURS_LEFT)
        sc, sp = select_short_strikes(spot, t, atm_vol, short_delta,
                                      curv=curv, slope=slope)
        settle = candles[ei]["close"]
        moves.append(abs((settle - spot) / spot * 100))
        rows.append((entry_c["date"], spot, sp, sc, settle,
                     (settle - spot) / spot * 100, settle > sc or settle < sp))

    if not moves:
        print("\n  Diagnostic: no samples.")
        return

    moves.sort()
    n = len(moves)
    breaches = sum(1 for r in rows if r[6])
    corridor = sum((r[3] - r[2]) / r[1] * 100 for r in rows) / n

    print(f"\n  Model-free diagnostic ({n} expiries)")
    print(f"  {'-'*104}")
    pct = "  ".join(f"p{q}={moves[min(int(n * q / 100), n - 1)]:.2f}%"
                    for q in (50, 70, 80, 90, 95))
    print(f"    |move| entry open -> expiry close : {pct}  max={moves[-1]:.2f}%")
    print(f"    Short-strike corridor             : {corridor:.2f}% wide "
          f"(+/-{corridor / 2:.2f}%)")
    print(f"    Settlements outside the corridor  : {breaches}/{n} = "
          f"{breaches / n * 100:.1f}%   (delta implies ~{short_delta * 200:.0f}%)")
    print("    If observed ~= implied, the pricer is calibrated and a losing"
          " P&L is a real finding.")


# ═════════════════════════════════════════════════════════════════════# ENTRY POINT
# ════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dte", type=int, default=DEFAULT_DTE,
                   help="entry N trading days before expiry (0 = expiry morning)")
    p.add_argument("--short-delta", type=float, default=DEFAULT_SHORT_DELTA,
                   help="delta of the short wings (0.20 ≈ 80%% OTM probability)")
    p.add_argument("--wing", type=int, default=DEFAULT_WING_POINTS,
                   help="protection distance beyond each short strike, in points")
    p.add_argument("--sl-mult", type=float, default=DEFAULT_SL_MULT,
                   help="cut when loss reaches N x credit (0 = hold to expiry)")
    p.add_argument("--iv-uplift", type=float, default=DEFAULT_IV_UPLIFT,
                   help="multiplier on realised vol to approximate implied vol")
    p.add_argument("--lots", type=int, default=DEFAULT_LOTS)
    p.add_argument("--skip-regimes", default="VOLATILE",
                   help="comma-separated regimes to skip (default: VOLATILE)")
    p.add_argument("--expiry-weekday", type=int, default=DEFAULT_EXPIRY_WEEKDAY,
                   help="Mon=0 .. Thu=3, before the switch date")
    p.add_argument("--expiry-weekday-new", type=int,
                   default=DEFAULT_EXPIRY_WEEKDAY_NEW,
                   help="expiry weekday from --expiry-switch onwards")
    p.add_argument("--expiry-switch", default=DEFAULT_EXPIRY_SWITCH,
                   help="first date the new expiry weekday applies (YYYY-MM-DD)")
    p.add_argument("--smile-curv", type=float, default=None)
    p.add_argument("--smile-slope", type=float, default=None)
    p.add_argument("--flat-vol", action="store_true",
                   help="disable the smile (control run — flatters condors)")
    p.add_argument("--sweep", action="store_true",
                   help="grid over delta / wing / SL / DTE")
    p.add_argument("--sensitivity", action="store_true",
                   help="re-run across IV and skew assumptions")
    p.add_argument("--diagnose", action="store_true",
                   help="model-free check: realised moves vs sold corridor")
    p.add_argument("--save", default="reports/backtest/options_condor.json")
    return p


def _smile_params(args) -> tuple[float, float]:
    from option_pricing import SMILE_CURV, SMILE_SLOPE
    if args.flat_vol:
        return 0.0, 0.0
    curv = SMILE_CURV if args.smile_curv is None else args.smile_curv
    slope = SMILE_SLOPE if args.smile_slope is None else args.smile_slope
    return curv, slope


def main() -> None:
    args = build_parser().parse_args()
    curv, slope = _smile_params(args)
    skip = {s.strip().upper() for s in args.skip_regimes.split(",") if s.strip()}

    candles = load_nifty_daily()
    if len(candles) < 60:
        raise SystemExit(
            f"  Not enough NIFTY daily candles ({len(candles)}) in {DAILY_DB}.\n"
            "  Run scripts/trade/fetch_backtest_candles.py first."
        )

    exp = expiry_days(candles, args.expiry_weekday,
                      args.expiry_weekday_new, args.expiry_switch)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    print("\n  Phase O-3.2 — Weekly Iron Condor into Expiry")
    print(f"  {'='*104}")
    print(f"  Data      : {len(candles)} NIFTY daily bars "
          f"({candles[0]['date']} → {candles[-1]['date']}), {len(exp)} weekly expiries")
    print(f"  Expiry    : {names[args.expiry_weekday]} until {args.expiry_switch}, "
          f"then {names[args.expiry_weekday_new]}")
    print(f"  Structure : short {args.short_delta:.2f}-delta wings, "
          f"{args.wing}pt protection, entry {args.dte}d before expiry, "
          f"{args.lots} lot x {NIFTY_LOT_SIZE}")
    print(f"  Risk      : SL at {args.sl_mult}x credit"
          if args.sl_mult > 0 else "  Risk      : hold to expiry, no SL")
    print(f"  Skipping  : {', '.join(sorted(skip)) or 'nothing'}")
    print(f"  Premiums  : SYNTHETIC (BS + smile curv={curv} slope={slope}, "
          f"IV uplift {args.iv_uplift}x) — NOT market data")
    print()
    print(verify_charges())

    def run(window: str, **overrides) -> list[dict]:
        w_start, w_end = WINDOWS[window]
        params = dict(
            dte=args.dte, short_delta=args.short_delta, wing=args.wing,
            sl_mult=args.sl_mult, iv_uplift=args.iv_uplift, lots=args.lots,
            skip_regimes=skip, expiry_weekday=args.expiry_weekday,
            expiry_weekday_new=args.expiry_weekday_new,
            expiry_switch=args.expiry_switch,
            curv=curv, slope=slope, start=w_start, end=w_end,
        )
        params.update(overrides)
        return simulate(candles, **params)

    # ── Walk-forward ──────────────────────────────────────────────
    print("\n  Walk-forward (net of all charges)")
    print(f"  {'-'*104}")
    results = {}
    for window in ("FULL", "TRAIN", "TEST"):
        m = metrics(run(window), window)
        results[window] = m
        print_metrics(m)

    full = results["FULL"]
    if full.get("trades"):
        print(f"\n  Exit reasons  : {full['exit_reasons']}")
        print(f"  Avg margin    : Rs.{full['avg_margin']:,.0f} per trade")
        print(f"  Avg return    : {full['avg_return_pct']:.2f}% of margin per trade")
        print(f"  Total charges : Rs.{full['total_charges']:,.0f}")
        print("\n  Per-regime (FULL)")
        for reg, s in sorted(full["regime_stats"].items()):
            print(f"    {reg:<10} n={s['trades']:<4} WR={s['win_rate']:>5.1f}%  "
                  f"PF={s['pf']:>5.2f}  net=Rs.{s['net']:>11,.0f}")

    if args.diagnose:
        diagnose(candles, dte=args.dte, short_delta=args.short_delta,
                 iv_uplift=args.iv_uplift, skip_regimes=skip,
                 expiry_weekday=args.expiry_weekday,
                 expiry_weekday_new=args.expiry_weekday_new,
                 expiry_switch=args.expiry_switch, curv=curv, slope=slope)

    # ── Sensitivity: does the edge survive its own assumptions? ───
    if args.sensitivity:
        print("\n  Sensitivity to premium-model assumptions (TEST window)")
        print(f"  {'-'*104}")
        print("  An edge that only appears at one IV/skew setting is a modelling"
              " artefact, not an edge.")
        for uplift in (1.0, 1.2, 1.35, 1.5, 1.8):
            for label, (cc, ss) in (("flat", (0.0, 0.0)),
                                    ("normal", (curv, slope)),
                                    ("steep", (curv * 2, slope * 2))):
                m = metrics(run("TEST", iv_uplift=uplift, curv=cc, slope=ss),
                            f"IV x{uplift} / skew {label}")
                print_metrics(m)

    # ── Parameter sweep ───────────────────────────────────────────
    if args.sweep:
        print("\n  Parameter sweep (TEST window, ranked by PF)")
        print(f"  {'-'*104}")
        rows = []
        for dte in (0, 1, 2, 3):
            for delta in (0.10, 0.15, 0.20, 0.30):
                for wing in (100, 200, 300):
                    for sl in (0.0, 1.5, 2.5):
                        m = metrics(
                            run("TEST", dte=dte, short_delta=delta,
                                wing=wing, sl_mult=sl),
                            f"dte={dte} d={delta} w={wing} sl={sl}")
                        if m.get("trades", 0) >= 15:
                            rows.append(m)
        rows.sort(key=lambda r: r["profit_factor"], reverse=True)
        for m in rows[:15]:
            print_metrics(m)
        if not rows:
            print("    no parameter combination produced >= 15 trades")

    # ── Verdict ───────────────────────────────────────────────────
    test = results["TEST"]
    print(f"\n  {'='*104}")
    if test.get("trades", 0) < 20:
        print(f"  VERDICT: INCONCLUSIVE — only {test.get('trades', 0)} OOS trades. "
              f"Need >= 20 for a read.")
    elif test["profit_factor"] >= 1.15:
        print(f"  VERDICT: PASSES the 1.15 gate OOS (PF {test['profit_factor']}) "
              f"— on SYNTHETIC premiums.")
        print("  Next: collect real chain data (record_option_chain.py) and "
              "re-run before any dry-run.")
    else:
        print(f"  VERDICT: FAILS the 1.15 gate OOS (PF {test['profit_factor']}).")
    print(f"  {'='*104}\n")

    if args.save:
        os.makedirs(os.path.dirname(args.save), exist_ok=True)
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump({
                "params": vars(args) | {"smile_curv": curv, "smile_slope": slope},
                "premium_source": "SYNTHETIC — Black-Scholes + assumed smile",
                "windows": results,
            }, f, indent=2, default=str)
        print(f"  Saved: {args.save}\n")


if __name__ == "__main__":
    main()
