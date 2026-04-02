"""View candle cache contents — symbols, intervals, date ranges, row counts."""
import argparse
import sqlite3
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "candle_cache.db")


def main():
    parser = argparse.ArgumentParser(description="View candle cache contents")
    parser.add_argument("--symbol", help="Filter by symbol (e.g., RELIANCE)")
    parser.add_argument("--interval", help="Filter by interval (e.g., day, 15minute)")
    parser.add_argument("--candles", action="store_true",
                        help="Show individual candles (use with --symbol)")
    parser.add_argument("--last", type=int, default=20,
                        help="Number of recent candles to show (default: 20)")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print("No candle cache found. Run the bot or --test mode first.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Total stats
    total = conn.execute("SELECT COUNT(*) as cnt FROM candle_cache").fetchone()["cnt"]
    if total == 0:
        print("Candle cache is empty.")
        conn.close()
        return

    db_size = os.path.getsize(DB_PATH)
    size_str = f"{db_size / 1024:.0f} KB" if db_size < 1048576 else f"{db_size / 1048576:.1f} MB"

    print(f"\n{'='*90}")
    print(f"  CANDLE CACHE  ({total:,} candles | {size_str})")
    print(f"  {DB_PATH}")
    print(f"{'='*90}")

    # Build WHERE clause
    conditions = []
    params = []
    if args.symbol:
        conditions.append("symbol = ?")
        params.append(args.symbol.upper())
    if args.interval:
        conditions.append("interval = ?")
        params.append(args.interval)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Show individual candles if requested
    if args.candles:
        if not args.symbol:
            print("\n  Use --symbol with --candles to see individual candles.")
            print(f"  Example: python scripts/view_candle_cache.py --symbol RELIANCE --candles")
            conn.close()
            return

        rows = conn.execute(
            f"""SELECT symbol, interval, candle_date, open, high, low, close, volume
                FROM candle_cache {where}
                ORDER BY interval, candle_date DESC
                LIMIT ?""",
            params + [args.last],
        ).fetchall()

        if not rows:
            print(f"\n  No cached candles for {args.symbol.upper()}")
            conn.close()
            return

        print(f"\n  Last {min(args.last, len(rows))} candles for {args.symbol.upper()}")
        print(f"  {'INTERVAL':<12} {'DATE':<22} {'OPEN':>10} {'HIGH':>10} {'LOW':>10} {'CLOSE':>10} {'VOLUME':>12}")
        print(f"  {'-'*12} {'-'*22} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")

        for r in reversed(rows):
            print(
                f"  {r['interval']:<12} {r['candle_date']:<22} "
                f"{r['open']:>10.2f} {r['high']:>10.2f} {r['low']:>10.2f} "
                f"{r['close']:>10.2f} {r['volume']:>12,}"
            )
    else:
        # Summary view — one row per symbol+interval
        rows = conn.execute(
            f"""SELECT symbol, exchange, interval,
                       COUNT(*) as candles,
                       MIN(candle_date) as first_date,
                       MAX(candle_date) as last_date,
                       MIN(close) as min_close,
                       MAX(close) as max_close
                FROM candle_cache {where}
                GROUP BY symbol, exchange, interval
                ORDER BY symbol, interval""",
            params,
        ).fetchall()

        if not rows:
            print(f"\n  No matching data.")
            conn.close()
            return

        symbols = len(set(r["symbol"] for r in rows))
        intervals = sorted(set(r["interval"] for r in rows))

        print(f"  Symbols: {symbols} | Intervals: {', '.join(intervals)}")
        print()
        print(f"  {'SYMBOL':<15} {'INTERVAL':<12} {'CANDLES':>8} {'FIRST DATE':<22} {'LAST DATE':<22} {'CLOSE RANGE':>22}")
        print(f"  {'-'*15} {'-'*12} {'-'*8} {'-'*22} {'-'*22} {'-'*22}")

        for r in rows:
            close_range = f"₹{r['min_close']:,.2f} — ₹{r['max_close']:,.2f}"
            print(
                f"  {r['symbol']:<15} {r['interval']:<12} {r['candles']:>8} "
                f"{r['first_date']:<22} {r['last_date']:<22} {close_range:>22}"
            )

        print(f"\n  Total: {len(rows)} symbol-interval combos, {total:,} candles")

    print(f"{'='*90}\n")
    conn.close()


if __name__ == "__main__":
    main()
