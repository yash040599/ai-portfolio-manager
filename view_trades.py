"""Print all trading data (intraday trades) from the database."""
import sqlite3
import os

DB_PATH = os.path.join("data", "trades.db")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM trades ORDER BY date, symbol").fetchall()

if not rows:
    print("No trading data found.")
    conn.close()
    exit()

print(f"\n{'='*120}")
print(f"  INTRADAY TRADES  ({len(rows)} records)")
print(f"{'='*120}")
print(f"  {'DATE':<12} {'SYMBOL':<15} {'SIDE':<6} {'QTY':>5} {'ENTRY':>10} {'EXIT':>10} {'P&L':>10} {'EXIT REASON':<25} {'MARKET CONDITION':<25} {'CONFIDENCE'}")
print(f"  {'-'*12} {'-'*15} {'-'*6} {'-'*5} {'-'*10} {'-'*10} {'-'*10} {'-'*25} {'-'*25} {'-'*10}")

total_pnl = 0
for r in rows:
    pnl = r["pnl"] or 0
    total_pnl += pnl
    pnl_str = f"₹{pnl:+.2f}"
    print(f"  {r['date']:<12} {r['symbol']:<15} {r['side']:<6} {r['qty']:>5} {r['entry_price']:>10.2f} {(r['exit_price'] or 0):>10.2f} {pnl_str:>10} {(r['exit_reason'] or '')::<25} {(r['market_condition'] or ''):<25} {r['claude_confidence'] or ''}")

print(f"\n  {'TOTAL P&L':>60}: ₹{total_pnl:+.2f}")
print(f"  {'TOTAL TRADES':>60}: {len(rows)}")
winners = sum(1 for r in rows if (r["pnl"] or 0) > 0)
losers = sum(1 for r in rows if (r["pnl"] or 0) < 0)
print(f"  {'WINNERS / LOSERS':>60}: {winners} / {losers}")
print(f"{'='*120}\n")

conn.close()
