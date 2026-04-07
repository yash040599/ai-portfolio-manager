"""
View performance analytics from the trades database.

Shows trade-level data plus aggregated statistics: daily P&L,
win rate, average win/loss, performance by exit reason, by side,
by market condition, and indicator correlation analysis.

Usage
─────
    python scripts/view_performance.py              # all trades
    python scripts/view_performance.py --days 7     # last 7 days
    python scripts/view_performance.py --date 2026-04-08  # specific date
    python scripts/view_performance.py --summary    # daily summary only (no trade details)
"""

import argparse
import json
import sqlite3
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "trades.db")


def get_trades(date_filter: str | None = None, last_n_days: int | None = None) -> list:
    """Fetch trades from DB with optional date filtering."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if date_filter:
        rows = conn.execute(
            "SELECT * FROM trades WHERE date=? ORDER BY entry_time", (date_filter,)
        ).fetchall()
    elif last_n_days:
        rows = conn.execute(
            "SELECT * FROM trades WHERE date >= date('now', ?) ORDER BY date, entry_time",
            (f"-{last_n_days} days",),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM trades ORDER BY date, entry_time").fetchall()

    conn.close()
    return rows


def print_trades(rows: list):
    """Print individual trade details with indicator snapshots."""
    if not rows:
        print("\n  No trades found.\n")
        return

    print(f"\n{'=' * 130}")
    print(f"  TRADE DETAILS  ({len(rows)} trades)")
    print(f"{'=' * 130}")
    print(f"  {'DATE':<12} {'SYMBOL':<12} {'SIDE':<5} {'QTY':>4} "
          f"{'ENTRY':>8} {'EXIT':>8} {'P&L':>9} {'REASON':<15} "
          f"{'TIME':>11} {'SCORE':>6} {'RSI':>5} {'CONDITION':<20}")
    print(f"  {'-' * 12} {'-' * 12} {'-' * 5} {'-' * 4} "
          f"{'-' * 8} {'-' * 8} {'-' * 9} {'-' * 15} "
          f"{'-' * 11} {'-' * 6} {'-' * 5} {'-' * 20}")

    for r in rows:
        pnl = r["pnl"] or 0
        color = "\033[92m" if pnl > 0 else "\033[91m" if pnl < 0 else ""
        reset = "\033[0m" if color else ""
        entry_t = r["entry_time"] or ""
        exit_t = r["exit_time"] or ""
        time_range = f"{entry_t}-{exit_t}" if entry_t else ""
        score = f"{r['entry_score']:.1f}" if r["entry_score"] is not None else "-"
        rsi = f"{r['entry_rsi']:.0f}" if r["entry_rsi"] is not None else "-"

        print(f"  {r['date']:<12} {r['symbol']:<12} {r['side']:<5} {r['qty']:>4} "
              f"{r['entry_price']:>8.2f} {(r['exit_price'] or 0):>8.2f} "
              f"{color}₹{pnl:>+8.2f}{reset} {(r['exit_reason'] or ''):<15} "
              f"{time_range:>11} {score:>6} {rsi:>5} {(r['market_condition'] or ''):<20}")

    print()


def print_daily_summary(rows: list):
    """Show per-day P&L summary."""
    if not rows:
        return

    # Group by date
    days: dict[str, list] = {}
    for r in rows:
        days.setdefault(r["date"], []).append(r)

    print(f"\n{'=' * 90}")
    print(f"  DAILY P&L SUMMARY  ({len(days)} day(s))")
    print(f"{'=' * 90}")
    print(f"  {'DATE':<12} {'TRADES':>6} {'WINS':>5} {'LOSSES':>6} "
          f"{'WIN %':>6} {'GROSS P&L':>10} {'AVG WIN':>9} {'AVG LOSS':>9} "
          f"{'BEST':>9} {'WORST':>9}")
    print(f"  {'-' * 12} {'-' * 6} {'-' * 5} {'-' * 6} "
          f"{'-' * 6} {'-' * 10} {'-' * 9} {'-' * 9} "
          f"{'-' * 9} {'-' * 9}")

    total_pnl = 0
    total_wins = 0
    total_trades = 0

    for date in sorted(days.keys()):
        trades = days[date]
        pnls = [t["pnl"] or 0 for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        day_pnl = sum(pnls)
        total_pnl += day_pnl
        total_wins += len(wins)
        total_trades += len(trades)

        win_rate = len(wins) / len(trades) * 100 if trades else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        best = max(pnls) if pnls else 0
        worst = min(pnls) if pnls else 0

        color = "\033[92m" if day_pnl > 0 else "\033[91m" if day_pnl < 0 else ""
        reset = "\033[0m" if color else ""

        print(f"  {date:<12} {len(trades):>6} {len(wins):>5} {len(losses):>6} "
              f"{win_rate:>5.0f}% {color}₹{day_pnl:>+9.2f}{reset} "
              f"₹{avg_win:>+8.2f} ₹{avg_loss:>+8.2f} "
              f"₹{best:>+8.2f} ₹{worst:>+8.2f}")

    # Totals
    overall_win_rate = total_wins / total_trades * 100 if total_trades else 0
    color = "\033[92m" if total_pnl > 0 else "\033[91m"
    reset = "\033[0m"
    print(f"  {'-' * 88}")
    print(f"  {'TOTAL':<12} {total_trades:>6} {total_wins:>5} "
          f"{total_trades - total_wins:>6} {overall_win_rate:>5.0f}% "
          f"{color}₹{total_pnl:>+9.2f}{reset}")
    print()


def print_exit_reason_stats(rows: list):
    """Show P&L breakdown by exit reason."""
    if not rows:
        return

    reasons: dict[str, list[float]] = {}
    for r in rows:
        reason = r["exit_reason"] or "UNKNOWN"
        reasons.setdefault(reason, []).append(r["pnl"] or 0)

    print(f"  {'EXIT REASON BREAKDOWN':}")
    print(f"  {'-' * 70}")
    print(f"  {'REASON':<20} {'COUNT':>6} {'TOTAL P&L':>10} {'AVG P&L':>9} {'WIN %':>6}")
    print(f"  {'-' * 20} {'-' * 6} {'-' * 10} {'-' * 9} {'-' * 6}")

    for reason in sorted(reasons.keys()):
        pnls = reasons[reason]
        total = sum(pnls)
        avg = total / len(pnls) if pnls else 0
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100 if pnls else 0
        print(f"  {reason:<20} {len(pnls):>6} ₹{total:>+9.2f} ₹{avg:>+8.2f} {wr:>5.0f}%")
    print()


def print_side_stats(rows: list):
    """Show P&L breakdown by trade side (BUY vs SELL)."""
    if not rows:
        return

    sides: dict[str, list[float]] = {}
    for r in rows:
        sides.setdefault(r["side"], []).append(r["pnl"] or 0)

    print(f"  {'SIDE BREAKDOWN':}")
    print(f"  {'-' * 50}")
    print(f"  {'SIDE':<6} {'COUNT':>6} {'TOTAL P&L':>10} {'AVG P&L':>9} {'WIN %':>6}")
    print(f"  {'-' * 6} {'-' * 6} {'-' * 10} {'-' * 9} {'-' * 6}")

    for side in sorted(sides.keys()):
        pnls = sides[side]
        total = sum(pnls)
        avg = total / len(pnls) if pnls else 0
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100 if pnls else 0
        print(f"  {side:<6} {len(pnls):>6} ₹{total:>+9.2f} ₹{avg:>+8.2f} {wr:>5.0f}%")
    print()


def print_indicator_correlation(rows: list):
    """Show how entry score and RSI correlate with trade outcomes."""
    scored = [r for r in rows if r["entry_score"] is not None]
    if not scored:
        return

    print(f"  {'INDICATOR CORRELATION':}")
    print(f"  {'-' * 60}")

    # Score ranges
    ranges = [
        ("Score < -5 (strong sell)", lambda r: (r["entry_score"] or 0) < -5),
        ("Score -5 to -2", lambda r: -5 <= (r["entry_score"] or 0) < -2),
        ("Score -2 to +2 (weak)", lambda r: -2 <= (r["entry_score"] or 0) <= 2),
        ("Score +2 to +5", lambda r: 2 < (r["entry_score"] or 0) <= 5),
        ("Score > +5 (strong buy)", lambda r: (r["entry_score"] or 0) > 5),
    ]

    print(f"  {'SCORE RANGE':<25} {'COUNT':>6} {'WIN %':>6} {'AVG P&L':>9}")
    print(f"  {'-' * 25} {'-' * 6} {'-' * 6} {'-' * 9}")

    for label, pred in ranges:
        matching = [r for r in scored if pred(r)]
        if not matching:
            continue
        pnls = [r["pnl"] or 0 for r in matching]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100
        avg = sum(pnls) / len(pnls)
        print(f"  {label:<25} {len(matching):>6} {wr:>5.0f}% ₹{avg:>+8.2f}")

    # RSI ranges
    rsi_scored = [r for r in rows if r["entry_rsi"] is not None]
    if rsi_scored:
        print()
        rsi_ranges = [
            ("RSI < 30 (oversold)", lambda r: (r["entry_rsi"] or 50) < 30),
            ("RSI 30-50", lambda r: 30 <= (r["entry_rsi"] or 50) < 50),
            ("RSI 50-70", lambda r: 50 <= (r["entry_rsi"] or 50) < 70),
            ("RSI > 70 (overbought)", lambda r: (r["entry_rsi"] or 50) >= 70),
        ]
        print(f"  {'RSI RANGE':<25} {'COUNT':>6} {'WIN %':>6} {'AVG P&L':>9}")
        print(f"  {'-' * 25} {'-' * 6} {'-' * 6} {'-' * 9}")
        for label, pred in rsi_ranges:
            matching = [r for r in rsi_scored if pred(r)]
            if not matching:
                continue
            pnls = [r["pnl"] or 0 for r in matching]
            wins = sum(1 for p in pnls if p > 0)
            wr = wins / len(pnls) * 100
            avg = sum(pnls) / len(pnls)
            print(f"  {label:<25} {len(matching):>6} {wr:>5.0f}% ₹{avg:>+8.2f}")

    print()


def main():
    parser = argparse.ArgumentParser(description="View performance analytics from trades DB")
    parser.add_argument("--date", help="Show trades for specific date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Show last N days of trades")
    parser.add_argument("--summary", action="store_true", help="Show daily summary only (no trade details)")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"\n  No database found at {DB_PATH}")
        print(f"  Run the bot first to generate trade data.\n")
        return

    rows = get_trades(date_filter=args.date, last_n_days=args.days)

    if not rows:
        label = f"for {args.date}" if args.date else f"in last {args.days} days" if args.days else ""
        print(f"\n  No trades found {label}.\n")
        return

    # Daily summary always shown
    print_daily_summary(rows)

    # Trade details (unless --summary)
    if not args.summary:
        print_trades(rows)

    # Aggregate stats
    print_exit_reason_stats(rows)
    print_side_stats(rows)
    print_indicator_correlation(rows)


if __name__ == "__main__":
    # Handle Windows terminal encoding for ₹ symbol
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    main()
