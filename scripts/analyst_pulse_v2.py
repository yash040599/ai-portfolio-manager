"""Analyst pulse — last 9 trading days deep slice.

Read-only. Run: python scripts/analyst_pulse_v2.py

Pulls from data/trades.db two tables:
  trades              — bot's own logical trades (one row per round-trip), has
                        side, entry_score, market_condition, exit_reason.
  intraday_tax_ledger — Zerodha-canonical fills, has gross/net/charges per row.

Sections:
  S1  By-day net summary (last 9 days)
  S2  By-side (BUY vs SELL) win-rate / expectancy / total pnl
  S3  By-exit-reason count + sum + avg
  S4  Score-bucket profile: |entry_score| 6-7 / 7-8 / 8-9 / 9-10 win-rate
  S5  By-time-of-day window: 09:30-10:00 / 10:00-11:00 / 11-12 / 12-13:30 / 13:30-15:00 / 15:00-15:30
  S6  Hold-time bucket: <5min / 5-15 / 15-30 / 30-60 / >60
  S7  Charges-vs-gross gap each day (does the strategy bleed cash?)
  S8  Per-symbol repeat-offenders
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

DB = Path(__file__).resolve().parent.parent / "data" / "trades.db"
SINCE = "2026-04-22"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def fmt_money(x: float) -> str:
    sign = "-" if x < 0 else "+"
    return f"{sign}Rs.{abs(x):>9.2f}"


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def s1_by_day(con: sqlite3.Connection) -> None:
    section("S1  By-day net summary (last 9 days)")
    rows = con.execute(
        """
        SELECT date,
               COUNT(*)             AS n,
               SUM(gross_pnl)       AS gross,
               SUM(total_charges)   AS charges,
               SUM(net_pnl)         AS net
        FROM intraday_tax_ledger
        WHERE date >= ?
        GROUP BY date
        ORDER BY date
        """,
        (SINCE,),
    ).fetchall()
    print(f"{'Date':<12} {'N':>4} {'Gross':>12} {'Charges':>10} {'Net':>12}")
    tot_n = tot_gross = tot_charges = tot_net = 0
    for r in rows:
        print(
            f"{r['date']:<12} {r['n']:>4} {fmt_money(r['gross']):>12} "
            f"{r['charges']:>10.2f} {fmt_money(r['net']):>12}"
        )
        tot_n += r['n']; tot_gross += r['gross']; tot_charges += r['charges']; tot_net += r['net']
    print("-" * 56)
    print(f"{'TOTAL':<12} {tot_n:>4} {fmt_money(tot_gross):>12} {tot_charges:>10.2f} {fmt_money(tot_net):>12}")


def s2_by_side(con: sqlite3.Connection) -> None:
    section("S2  By-side (BUY vs SELL) — last 9 days, from intraday_tax_ledger")
    rows = con.execute(
        """
        SELECT side,
               COUNT(*)              AS n,
               SUM(CASE WHEN gross_pnl > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(gross_pnl)        AS gross,
               SUM(total_charges)    AS charges,
               SUM(net_pnl)          AS net
        FROM intraday_tax_ledger
        WHERE date >= ?
        GROUP BY side
        ORDER BY side
        """,
        (SINCE,),
    ).fetchall()
    print(f"{'Side':<6} {'N':>4} {'Wins':>5} {'WR%':>6} {'Gross':>12} {'Charges':>9} {'Net':>12} {'Net/trade':>10}")
    for r in rows:
        wr = r['wins'] / r['n'] * 100 if r['n'] else 0
        npt = r['net'] / r['n'] if r['n'] else 0
        print(
            f"{r['side']:<6} {r['n']:>4} {r['wins']:>5} {wr:>5.1f}% "
            f"{fmt_money(r['gross']):>12} {r['charges']:>9.2f} "
            f"{fmt_money(r['net']):>12} {fmt_money(npt):>10}"
        )


def s3_by_exit_reason(con: sqlite3.Connection) -> None:
    section("S3  By-exit-reason — last 9 days")
    rows = con.execute(
        """
        SELECT exit_reason,
               COUNT(*)             AS n,
               SUM(CASE WHEN gross_pnl > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(gross_pnl)       AS gross,
               AVG(gross_pnl)       AS avg_g,
               SUM(net_pnl)         AS net
        FROM intraday_tax_ledger
        WHERE date >= ?
        GROUP BY exit_reason
        ORDER BY net ASC
        """,
        (SINCE,),
    ).fetchall()
    print(f"{'Exit':<28} {'N':>4} {'Wins':>5} {'WR%':>6} {'Gross':>12} {'Avg':>10} {'Net':>12}")
    for r in rows:
        wr = r['wins'] / r['n'] * 100 if r['n'] else 0
        print(
            f"{(r['exit_reason'] or 'NULL'):<28} {r['n']:>4} {r['wins']:>5} {wr:>5.1f}% "
            f"{fmt_money(r['gross']):>12} {fmt_money(r['avg_g']):>10} "
            f"{fmt_money(r['net']):>12}"
        )


def s4_by_score_bucket(con: sqlite3.Connection) -> None:
    section("S4  By |entry_score| bucket — last 9 days, from trades table (logical)")
    rows = con.execute(
        """
        SELECT entry_score, side, pnl, exit_reason, symbol, date, market_condition, entry_rsi
        FROM trades
        WHERE date >= ?
        """,
        (SINCE,),
    ).fetchall()
    print(f"  Total logical trades: {len(rows)}")
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        s = abs(r['entry_score'] or 0)
        if s < 6: b = '<6.0'
        elif s < 7: b = '6.0-7.0'
        elif s < 8: b = '7.0-8.0'
        elif s < 9: b = '8.0-9.0'
        else:        b = '9.0+'
        buckets[b].append(r['pnl'] or 0.0)
    print(f"  {'Bucket':<10} {'N':>4} {'Wins':>5} {'WR%':>6} {'SumP&L':>12} {'AvgP&L':>10}")
    for b in ['9.0+', '8.0-9.0', '7.0-8.0', '6.0-7.0', '<6.0']:
        if b not in buckets:
            continue
        pnls = buckets[b]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100
        print(
            f"  {b:<10} {len(pnls):>4} {wins:>5} {wr:>5.1f}% "
            f"{fmt_money(sum(pnls)):>12} {fmt_money(mean(pnls)):>10}"
        )


def s5_by_time_window(con: sqlite3.Connection) -> None:
    section("S5  By entry-time window — last 9 days, from trades table")
    rows = con.execute(
        """
        SELECT entry_time, pnl, side
        FROM trades
        WHERE date >= ?
        """,
        (SINCE,),
    ).fetchall()
    windows = [
        ("09:30-10:00", "09:30", "10:00"),
        ("10:00-11:00", "10:00", "11:00"),
        ("11:00-12:00", "11:00", "12:00"),
        ("12:00-13:30", "12:00", "13:30"),
        ("13:30-15:00", "13:30", "15:00"),
        ("15:00-15:30", "15:00", "15:30"),
    ]
    print(f"  {'Window':<14} {'N':>4} {'Wins':>5} {'WR%':>6} {'SumP&L':>12}")
    for name, lo, hi in windows:
        bucket: list[float] = []
        for r in rows:
            t = (r['entry_time'] or '')[:5]
            if not t:
                continue
            if lo <= t < hi:
                bucket.append(r['pnl'] or 0.0)
        if not bucket:
            continue
        wins = sum(1 for p in bucket if p > 0)
        wr = wins / len(bucket) * 100
        print(f"  {name:<14} {len(bucket):>4} {wins:>5} {wr:>5.1f}% {fmt_money(sum(bucket)):>12}")


def s6_by_hold_time(con: sqlite3.Connection) -> None:
    section("S6  By hold-time bucket — last 9 days")
    rows = con.execute(
        """
        SELECT entry_time, exit_time, pnl, exit_reason
        FROM trades
        WHERE date >= ? AND entry_time IS NOT NULL AND exit_time IS NOT NULL
        """,
        (SINCE,),
    ).fetchall()
    buckets: dict[str, list[float]] = defaultdict(list)
    def parse(t: str) -> datetime | None:
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(t, fmt)
            except Exception:
                pass
        return None
    for r in rows:
        a = parse(r['entry_time']); b = parse(r['exit_time'])
        if not a or not b:
            continue
        mins = (b - a).total_seconds() / 60.0
        if mins < 0:
            mins += 24 * 60
        if mins < 5: k = '<5min'
        elif mins < 15: k = '5-15min'
        elif mins < 30: k = '15-30min'
        elif mins < 60: k = '30-60min'
        elif mins < 120: k = '60-120min'
        else:            k = '>120min'
        buckets[k].append(r['pnl'] or 0.0)
    print(f"  {'Hold':<10} {'N':>4} {'Wins':>5} {'WR%':>6} {'SumP&L':>12} {'AvgP&L':>10}")
    for k in ['<5min', '5-15min', '15-30min', '30-60min', '60-120min', '>120min']:
        if k not in buckets:
            continue
        pnls = buckets[k]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100
        print(
            f"  {k:<10} {len(pnls):>4} {wins:>5} {wr:>5.1f}% "
            f"{fmt_money(sum(pnls)):>12} {fmt_money(mean(pnls)):>10}"
        )


def s7_charges_drag(con: sqlite3.Connection) -> None:
    section("S7  Charges drag per day (does friction kill marginal-gross days?)")
    rows = con.execute(
        """
        SELECT date,
               COUNT(*)             AS n,
               SUM(gross_pnl)       AS gross,
               SUM(total_charges)   AS charges,
               SUM(net_pnl)         AS net
        FROM intraday_tax_ledger
        WHERE date >= ?
        GROUP BY date
        ORDER BY date
        """,
        (SINCE,),
    ).fetchall()
    print(f"  {'Date':<12} {'Gross':>10} {'Chrg':>8} {'Net':>10} {'Chrg/Trd':>9} {'Verdict'}")
    for r in rows:
        cpt = r['charges'] / r['n'] if r['n'] else 0
        if r['gross'] > 0 and r['net'] < 0:
            verdict = "*** charges flipped a winner to a loser"
        elif r['gross'] > 0:
            verdict = "edge held through friction"
        elif r['gross'] < 0 and abs(r['gross']) < r['charges']:
            verdict = "* charges > absolute gross loss"
        else:
            verdict = "real strategy loss (charges minor)"
        print(
            f"  {r['date']:<12} {fmt_money(r['gross']):>10} "
            f"{r['charges']:>8.2f} {fmt_money(r['net']):>10} {cpt:>9.2f}  {verdict}"
        )


def s8_repeat_offenders(con: sqlite3.Connection) -> None:
    section("S8  Per-symbol repeat losers (last 9 days, sum of net_pnl)")
    rows = con.execute(
        """
        SELECT symbol,
               COUNT(*)             AS n,
               SUM(CASE WHEN gross_pnl > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(gross_pnl)       AS gross,
               SUM(net_pnl)         AS net
        FROM intraday_tax_ledger
        WHERE date >= ?
        GROUP BY symbol
        HAVING n >= 2
        ORDER BY net ASC
        LIMIT 12
        """,
        (SINCE,),
    ).fetchall()
    print(f"  {'Symbol':<14} {'N':>3} {'W':>3} {'WR%':>6} {'Gross':>11} {'Net':>11}")
    for r in rows:
        wr = r['wins'] / r['n'] * 100 if r['n'] else 0
        print(
            f"  {r['symbol']:<14} {r['n']:>3} {r['wins']:>3} {wr:>5.1f}% "
            f"{fmt_money(r['gross']):>11} {fmt_money(r['net']):>11}"
        )


def main() -> None:
    con = connect()
    s1_by_day(con)
    s2_by_side(con)
    s3_by_exit_reason(con)
    s4_by_score_bucket(con)
    s5_by_time_window(con)
    s6_by_hold_time(con)
    s7_charges_drag(con)
    s8_repeat_offenders(con)


if __name__ == "__main__":
    main()
