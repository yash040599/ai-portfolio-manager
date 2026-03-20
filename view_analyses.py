"""Print all portfolio analysis data from the database."""
import sqlite3
import os

DB_PATH = os.path.join("data", "trades.db")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM portfolio_analyses ORDER BY date, symbol").fetchall()

if not rows:
    print("No portfolio analysis data found.")
    conn.close()
    exit()

print(f"\n{'='*140}")
print(f"  PORTFOLIO ANALYSES  ({len(rows)} records)")
print(f"{'='*140}")

current_date = None
for r in rows:
    if r["date"] != current_date:
        current_date = r["date"]
        print(f"\n  ── {current_date} {'─'*120}")
        print(f"  {'SYMBOL':<15} {'ACTION':<15} {'CONVICTION':<12} {'STATUS':<12} {'CURR PRICE':>10} {'INVESTED':>12} {'CURRENT':>12} {'P&L':>10} {'P&L%':>8} {'HORIZON':<12} {'TARGET':<12}")
        print(f"  {'-'*15} {'-'*15} {'-'*12} {'-'*12} {'-'*10} {'-'*12} {'-'*12} {'-'*10} {'-'*8} {'-'*12} {'-'*12}")

    pnl = r["stock_pnl"] or 0
    pnl_pct = r["stock_pnl_pct"] or 0
    pnl_str = f"₹{pnl:+.0f}"
    pnl_pct_str = f"{pnl_pct:+.1f}%"

    print(f"  {r['symbol']:<15} {(r['action'] or 'HOLD'):<15} {(r['conviction'] or ''):<12} {(r['action_taken'] or ''):<12} {(r['current_price'] or 0):>10.2f} {(r['invested_value'] or 0):>12.2f} {(r['current_value'] or 0):>12.2f} {pnl_str:>10} {pnl_pct_str:>8} {(r['horizon'] or ''):<12} {(r['target_price'] or ''):<12}")

# Summary per date
print(f"\n{'='*140}")
print("  SUMMARY BY DATE")
print(f"{'='*140}")

dates = conn.execute("SELECT DISTINCT date FROM portfolio_analyses ORDER BY date").fetchall()
for d in dates:
    date = d["date"]
    stats = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN action_taken = 'DONE' THEN 1 ELSE 0 END) as done,
            SUM(CASE WHEN action_taken = 'NOT ACTED' THEN 1 ELSE 0 END) as not_acted,
            SUM(CASE WHEN action_taken = 'PENDING' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN action_taken = 'N/A' THEN 1 ELSE 0 END) as na,
            SUM(stock_pnl) as total_pnl
        FROM portfolio_analyses WHERE date = ?
    """, (date,)).fetchone()
    print(f"  {date}:  {stats['total']} stocks | DONE: {stats['done']}  NOT ACTED: {stats['not_acted']}  PENDING: {stats['pending']}  N/A: {stats['na']}  | Portfolio P&L: ₹{(stats['total_pnl'] or 0):+,.0f}")

print(f"{'='*140}\n")

conn.close()
