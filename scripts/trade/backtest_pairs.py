#!/usr/bin/env python3
"""Phase 6 — Intraday pairs trading (market-neutral stat-arb), OOS.

THESIS (PM call, 2026-06-01): every long/short *momentum* variant we have
tested (regime routing, 5-min, ORB-5, VWAP-trail) tops out at OOS PF ~1.10
because it carries directional **market beta** — when the index chops, the
signal bleeds. A genuinely different, lower-correlation edge is required.
Pairs trading is the canonical candidate: go long the cheap leg and short
the rich leg of a co-moving same-sector pair, so the market move cancels
and only the *relative* mispricing matters. The open question is whether
the relative edge survives paying costs on **two** legs.

METHOD (strict no-lookahead):
  1. Universe = NIFTY50 (15-min candles). Candidate pairs = every
     within-sector combination (SECTOR_MAP).
  2. SELECTION on TRAIN window ONLY:
       - hedge ratio beta = OLS of log(Pa) on log(Pb)   (frozen)
       - require return correlation >= CORR_MIN
       - require mean reversion: Ornstein-Uhlenbeck half-life of the
         spread in [HL_MIN, HL_MAX] bars (rejects random-walk pairs)
     Keep the top TOP_N pairs by correlation.
  3. TRADE on TEST window (OOS) with FROZEN beta:
       - spread_t = log(Pa) - beta*log(Pb)
       - z = (spread - rolling_mean) / rolling_std over a trailing
         causal window (no lookahead)
       - enter when |z| >= ENTRY_Z (short rich leg / long cheap leg),
         exit when |z| <= EXIT_Z (converged) or |z| >= STOP_Z (blowout),
         hard square-off at SQUARE_OFF (intraday MIS, no overnight).
       - costs charged on BOTH legs via Config.calculate_charges.
  4. Metrics: PF, win-rate, expectancy, Sharpe — net of cost. Promotion
     gate PF >= 1.15 AND expectancy > 0 on TEST.

Usage:
    python scripts/trade/backtest_pairs.py
    python scripts/trade/backtest_pairs.py --entry-z 2.5 --top-n 15

Read-only. Out-of-sample by construction. Never touches capital.
"""
from __future__ import annotations

import argparse
import itertools
import math
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from backtest_gates import INTRADAY_DB, load_15m, compute_charges  # noqa: E402
from shared.nifty_universe import get_universe  # noqa: E402

try:
    from modes.trade.stock_scanner import SECTOR_MAP  # noqa: E402
except Exception:
    SECTOR_MAP = {}

# ── Windows (same OOS split as the rest of the audit) ─────────
TRAIN = ("2024-05-27", "2025-05-31")
TEST = ("2025-06-01", "2026-05-22")

# ── Strategy params (defaults; sweepable via CLI) ─────────────
TRADE_VALUE = 15_000          # per leg notional
CORR_MIN = 0.70               # TRAIN return correlation floor
HL_MIN, HL_MAX = 2.0, 120.0   # OU half-life band (bars), rejects random walks
TOP_N = 20                    # number of pairs to trade
ROLL_WINDOW = 50              # trailing bars for z-score (causal)
ENTRY_Z = 2.0
EXIT_Z = 0.5
STOP_Z = 3.5
ENTRY_START = (9, 45)         # allow warmup
ENTRY_END = (14, 30)
SQUARE_OFF = (15, 15)
GATE_PF = 1.15

# ── Math helpers (pure python, no numpy dependency) ───────────

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _ols_beta(x: list[float], y: list[float]) -> float:
    """Slope of y ~ a + b*x (hedge ratio)."""
    n = len(x)
    if n < 2:
        return 0.0
    mx, my = _mean(x), _mean(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y, strict=True))
    return sxy / sxx if sxx > 0 else 0.0


def _corr(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mx, my = _mean(x), _mean(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y, strict=True))
    den = math.sqrt(sxx * syy)
    return sxy / den if den > 0 else 0.0


def _ou_half_life(spread: list[float]) -> float:
    """Estimate mean-reversion half-life via OU regression:
    d(spread)_t = a + b*spread_{t-1} + e ; half-life = -ln(2)/b.
    Returns +inf if not mean-reverting (b >= 0)."""
    if len(spread) < 30:
        return math.inf
    lag = spread[:-1]
    delta = [spread[i] - spread[i - 1] for i in range(1, len(spread))]
    b = _ols_beta(lag, delta)
    if b >= 0:
        return math.inf
    return -math.log(2) / b


# ── Data prep ─────────────────────────────────────────────────

def _in_window(date_str: str, window: tuple[str, str]) -> bool:
    return window[0] <= date_str <= window[1]


def _aligned(series_a: list[dict], series_b: list[dict],
             window: tuple[str, str]) -> list[tuple]:
    """Intersect two 15-min series by timestamp within `window`.
    Returns sorted list of (ts, price_a, price_b)."""
    by_ts_b = {c["ts"]: c["close"] for c in series_b
               if _in_window(c["ts"].date().isoformat(), window)}
    out = []
    for c in series_a:
        if not _in_window(c["ts"].date().isoformat(), window):
            continue
        pb = by_ts_b.get(c["ts"])
        if pb is not None and c["close"] > 0 and pb > 0:
            out.append((c["ts"], c["close"], pb))
    out.sort(key=lambda r: r[0])
    return out


# ── Pair selection (TRAIN only) ───────────────────────────────

def select_pairs(prices: dict[str, list[dict]], symbols: list[str],
                 top_n: int = TOP_N, corr_min: float = CORR_MIN,
                 hl_max: float = HL_MAX) -> list[dict]:
    """Return frozen pair specs chosen on the TRAIN window."""
    # group symbols by sector
    by_sector: dict[str, list[str]] = defaultdict(list)
    for s in symbols:
        if s in prices:
            by_sector[SECTOR_MAP.get(s, "OTHER")].append(s)

    candidates = []
    for sector, syms in by_sector.items():
        if sector == "OTHER" or len(syms) < 2:
            continue
        for a, b in itertools.combinations(sorted(syms), 2):
            rows = _aligned(prices[a], prices[b], TRAIN)
            if len(rows) < 200:
                continue
            la = [math.log(r[1]) for r in rows]
            lb = [math.log(r[2]) for r in rows]
            # return correlation
            ra = [la[i] - la[i - 1] for i in range(1, len(la))]
            rb = [lb[i] - lb[i - 1] for i in range(1, len(lb))]
            corr = _corr(ra, rb)
            if corr < corr_min:
                continue
            beta = _ols_beta(lb, la)        # log(Pa) ~ beta*log(Pb)
            if beta <= 0:
                continue
            spread = [la[i] - beta * lb[i] for i in range(len(la))]
            hl = _ou_half_life(spread)
            if not (HL_MIN <= hl <= hl_max):
                continue
            candidates.append({
                "a": a, "b": b, "sector": sector,
                "beta": beta, "corr": corr, "half_life": hl,
                "n_train": len(rows),
            })

    candidates.sort(key=lambda c: -c["corr"])
    return candidates[:top_n]


def diagnose_pairs(prices: dict[str, list[dict]], symbols: list[str]) -> None:
    """Print corr / beta / half-life for ALL same-sector pairs (no filters)
    so thresholds can be calibrated to the data."""
    by_sector: dict[str, list[str]] = defaultdict(list)
    for s in symbols:
        if s in prices:
            by_sector[SECTOR_MAP.get(s, "OTHER")].append(s)
    rows_out = []
    for sector, syms in by_sector.items():
        if sector == "OTHER" or len(syms) < 2:
            continue
        for a, b in itertools.combinations(sorted(syms), 2):
            rows = _aligned(prices[a], prices[b], TRAIN)
            if len(rows) < 200:
                continue
            la = [math.log(r[1]) for r in rows]
            lb = [math.log(r[2]) for r in rows]
            ra = [la[i] - la[i - 1] for i in range(1, len(la))]
            rb = [lb[i] - lb[i - 1] for i in range(1, len(lb))]
            corr = _corr(ra, rb)
            beta = _ols_beta(lb, la)
            spread = [la[i] - beta * lb[i] for i in range(len(la))]
            hl = _ou_half_life(spread)
            rows_out.append((corr, hl, a, b, sector, beta))
    rows_out.sort(key=lambda r: -r[0])
    print(f"\n  === DIAGNOSTIC: all {len(rows_out)} same-sector pairs "
          f"(TRAIN), sorted by corr ===")
    print(f"  {'Pair':<26}{'Sector':<10}{'corr':>6}{'beta':>7}{'HL(bars)':>10}")
    for corr, hl, a, b, sector, beta in rows_out[:40]:
        hl_s = f"{hl:.0f}" if math.isfinite(hl) else "inf"
        print(f"  {a+'/'+b:<26}{sector:<10}{corr:>6.2f}{beta:>7.2f}{hl_s:>10}")


# ── Trade simulation (TEST/OOS) ───────────────────────────────

def _leg_charge(entry: float, exit_p: float) -> float:
    qty = max(1, int(TRADE_VALUE / entry))
    return compute_charges(entry * qty, exit_p * qty)


def _leg_gross(side: str, entry: float, exit_p: float) -> float:
    qty = max(1, int(TRADE_VALUE / entry))
    if side == "LONG":
        return (exit_p - entry) * qty
    return (entry - exit_p) * qty


def simulate_pair(rows: list[tuple], beta: float, *,
                  entry_z: float, exit_z: float, stop_z: float,
                  roll_window: int) -> list[dict]:
    """Trade one pair over an aligned (ts, pa, pb) series. Market-neutral:
    when z>0 (A rich vs B) -> SHORT A / LONG B ; when z<0 -> LONG A / SHORT B."""
    trades: list[dict] = []
    if len(rows) < roll_window + 5:
        return trades

    spreads = [math.log(pa) - beta * math.log(pb) for _, pa, pb in rows]

    in_pos = False
    dir_a = ""          # LONG or SHORT on leg A (B is opposite)
    e_pa = e_pb = 0.0
    e_ts = None

    def _close(i, reason):
        nonlocal in_pos
        ts, pa, pb = rows[i]
        side_b = "SHORT" if dir_a == "LONG" else "LONG"
        gross = _leg_gross(dir_a, e_pa, pa) + _leg_gross(side_b, e_pb, pb)
        charges = _leg_charge(e_pa, pa) + _leg_charge(e_pb, pb)
        net = gross - charges
        capital = 2 * TRADE_VALUE
        trades.append({
            "entry_ts": e_ts.isoformat(), "exit_ts": ts.isoformat(),
            "dir_a": dir_a, "gross": round(gross, 2),
            "charges": round(charges, 2), "net_pnl": round(net, 2),
            "net_pnl_pct": round(net / capital * 100, 4), "reason": reason,
        })
        in_pos = False

    for i in range(roll_window, len(rows)):
        ts, pa, pb = rows[i]
        hm = (ts.hour, ts.minute)

        # EOD square-off (also closes anything still open)
        if hm >= SQUARE_OFF:
            if in_pos:
                _close(i, "EOD_SQUARE_OFF")
            continue

        win = spreads[i - roll_window:i]
        mu = _mean(win)
        var = sum((s - mu) ** 2 for s in win) / len(win)
        sd = math.sqrt(var)
        if sd <= 0:
            continue
        z = (spreads[i] - mu) / sd

        if in_pos:
            # converged or blew out
            if abs(z) <= exit_z:
                _close(i, "CONVERGED")
            elif abs(z) >= stop_z:
                _close(i, "STOP_Z")
            continue

        # entry window only
        if not (ENTRY_START <= hm <= ENTRY_END):
            continue
        if abs(z) < entry_z or abs(z) >= stop_z:
            continue
        # z>0 => A rich => short A / long B ; z<0 => long A / short B
        dir_a = "SHORT" if z > 0 else "LONG"
        e_pa, e_pb, e_ts = pa, pb, ts
        in_pos = True

    return trades


# ── Metrics ───────────────────────────────────────────────────

def metrics(trades: list[dict], label: str) -> dict:
    n = len(trades)
    if n == 0:
        return {"label": label, "trades": 0, "wr": 0, "pf": 0,
                "exp": 0, "sharpe": 0, "net": 0}
    pnls = [t["net_pnl"] for t in trades]
    pcts = [t["net_pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_win / gross_loss if gross_loss > 0 else math.inf
    mu = _mean(pcts)
    sd = math.sqrt(sum((p - mu) ** 2 for p in pcts) / n) if n > 1 else 0.0
    sharpe = (mu / sd * math.sqrt(n)) if sd > 0 else 0.0
    return {
        "label": label, "trades": n,
        "wr": round(len(wins) / n * 100, 1),
        "pf": round(pf, 3), "exp": round(mu, 4),
        "sharpe": round(sharpe, 2), "net": round(sum(pnls), 0),
    }


def _print_row(m: dict) -> None:
    print(f"  {m['label']:<18}{m['trades']:>8}{m['wr']:>7}{m['pf']:>8}"
          f"{m['exp']:>10}{m['sharpe']:>8}{m['net']:>12}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 6 pairs trading backtest")
    ap.add_argument("--universe", default="NIFTY50")
    ap.add_argument("--entry-z", type=float, default=ENTRY_Z)
    ap.add_argument("--exit-z", type=float, default=EXIT_Z)
    ap.add_argument("--stop-z", type=float, default=STOP_Z)
    ap.add_argument("--roll-window", type=int, default=ROLL_WINDOW)
    ap.add_argument("--top-n", type=int, default=TOP_N)
    ap.add_argument("--corr-min", type=float, default=CORR_MIN)
    ap.add_argument("--hl-max", type=float, default=HL_MAX)
    ap.add_argument("--diagnose", action="store_true",
                    help="print corr/beta/half-life for all same-sector pairs")
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print(f"\n  Loading {len(symbols)} symbols (15-min)...")
    prices: dict[str, list[dict]] = {}
    for s in symbols:
        c = load_15m(INTRADAY_DB, s)
        if c:
            prices[s] = c
    print(f"  Loaded {len(prices)} symbols.")

    if args.diagnose:
        diagnose_pairs(prices, symbols)
        return

    pairs = select_pairs(prices, symbols, top_n=args.top_n,
                         corr_min=args.corr_min, hl_max=args.hl_max)
    print(f"\n  === PAIR SELECTION (TRAIN only, corr>={args.corr_min}, "
          f"half-life {HL_MIN:.0f}-{args.hl_max:.0f} bars) ===")
    print(f"  Selected {len(pairs)} pairs (top {args.top_n} by correlation):")
    for p in pairs:
        print(f"    {p['a']:<11}/{p['b']:<11} {p['sector']:<9} "
              f"corr={p['corr']:.2f} beta={p['beta']:.2f} "
              f"HL={p['half_life']:.0f}b")
    if not pairs:
        print("  No pairs cleared selection. Loosen CORR_MIN / half-life band.")
        return

    # Trade selected pairs OOS (TEST) and in-sample (TRAIN) for overfit check.
    all_train: list[dict] = []
    all_test: list[dict] = []
    per_pair = []
    for p in pairs:
        rows_tr = _aligned(prices[p["a"]], prices[p["b"]], TRAIN)
        rows_te = _aligned(prices[p["a"]], prices[p["b"]], TEST)
        tr = simulate_pair(rows_tr, p["beta"], entry_z=args.entry_z,
                           exit_z=args.exit_z, stop_z=args.stop_z,
                           roll_window=args.roll_window)
        te = simulate_pair(rows_te, p["beta"], entry_z=args.entry_z,
                           exit_z=args.exit_z, stop_z=args.stop_z,
                           roll_window=args.roll_window)
        all_train.extend(tr)
        all_test.extend(te)
        per_pair.append((p, metrics(te, f"{p['a']}/{p['b']}")))

    hdr = (f"{'Book':<18}{'Trades':>8}{'WR%':>7}{'PF':>8}{'Exp%':>10}"
           f"{'Sharpe':>8}{'NetRs':>12}")

    print(f"\n  === PORTFOLIO (all {len(pairs)} pairs, net of 2-leg cost) ===")
    print("  " + hdr)
    print("  " + "-" * (len(hdr)))
    _print_row(metrics(all_train, "TRAIN (in-samp)"))
    _print_row(metrics(all_test, "TEST  (OOS)"))

    print("\n  === PER-PAIR (TEST/OOS) ===")
    print("  " + hdr)
    print("  " + "-" * (len(hdr)))
    for _, m in sorted(per_pair, key=lambda x: -x[1]["pf"]):
        _print_row(m)

    # ── Verdict ───────────────────────────────────────────────
    te = metrics(all_test, "TEST")
    print(f"\n  === PHASE 6 VERDICT (OOS, gate PF>={GATE_PF}) ===")
    print(f"  Portfolio OOS: PF {te['pf']}, expectancy {te['exp']}%/trade, "
          f"{te['trades']} trades, Sharpe {te['sharpe']}, net Rs.{te['net']:.0f}")
    if te["pf"] >= GATE_PF and te["exp"] > 0:
        print("  RESULT: CLEARS the gate. Market-neutral edge survives "
              "two-leg costs OOS. Candidate for dry-run + sizing study.")
    elif te["pf"] >= 1.0:
        print(f"  RESULT: POSITIVE but below gate (PF {te['pf']} < {GATE_PF}). "
              f"Edge exists net of cost but thin — worth tuning (entry-z, "
              f"top-n, pair filters) before accept/reject.")
    else:
        print("  RESULT: Below 1.0 OOS. Two-leg cost drag swamps the relative "
              "edge at these params. Tune or reject.")
    print()


if __name__ == "__main__":
    main()
