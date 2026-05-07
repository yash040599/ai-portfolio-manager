"""Full-ledger score-bucket audit (analyst lens, 2026-05-08).

Prints per-side WR, P&L, expectancy by `|entry_score|` bucket so we can
see whether the score-bucket inversion documented in roadmap #252 still
holds on the FULL 158-trade ledger (not just the 9-day audit window).

Read-only sqlite query against data/trades.db. No CLI args.
"""
import sqlite3
from collections import defaultdict

conn = sqlite3.connect("data/trades.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute(
    "SELECT id, date, symbol, side, entry_score, pnl, exit_reason "
    "FROM trades WHERE entry_score IS NOT NULL AND pnl IS NOT NULL "
    "ORDER BY date, id"
)
rows = [dict(r) for r in cur.fetchall()]
print(f"Total trades with entry_score + pnl: {len(rows)}")
print(f"Date range: {rows[0]['date']} -> {rows[-1]['date']}")
print()

# Bucket helpers
def bucket(s):
    a = abs(s)
    if a >= 9.0:
        return "9.0+   "
    if a >= 8.0:
        return "8.0-9.0"
    if a >= 7.0:
        return "7.0-8.0"
    if a >= 6.0:
        return "6.0-7.0"
    if a >= 5.0:
        return "5.0-6.0"
    return "<5.0   "


def bucket_finer(s):
    a = abs(s)
    if a >= 10.0:
        return "10.0+   "
    if a >= 9.0:
        return "9.0-10.0"
    if a >= 8.5:
        return "8.5-9.0 "
    if a >= 8.0:
        return "8.0-8.5 "
    if a >= 7.5:
        return "7.5-8.0 "
    if a >= 7.0:
        return "7.0-7.5 "
    if a >= 6.5:
        return "6.5-7.0 "
    if a >= 6.0:
        return "6.0-6.5 "
    if a >= 5.0:
        return "5.0-6.0 "
    return "<5.0    "


def summarise(label, trades):
    if not trades:
        print(f"{label}: (no trades)")
        return
    print(f"{label} (n={len(trades)})")
    print(f"  bucket    n     WR      sumP&L    expect  losers")
    by = defaultdict(list)
    for t in trades:
        by[bucket(t["entry_score"])].append(t)
    for b in sorted(by, key=lambda k: -float(k.split("-")[0].replace("+", "").replace("<", "0").strip())):
        cohort = by[b]
        n = len(cohort)
        w = sum(1 for t in cohort if t["pnl"] > 0)
        s = sum(t["pnl"] for t in cohort)
        e = s / n
        big_losers = sum(1 for t in cohort if t["pnl"] < -50)
        print(f"  {b}  {n:>3}  {100*w/n:>5.1f}%  Rs.{s:>+7.0f}  {e:>+6.1f}    {big_losers}")
    print()


print("=" * 65)
print("FULL LEDGER (BUY + SELL combined)")
print("=" * 65)
summarise("ALL", rows)

print("=" * 65)
print("BUY ONLY")
print("=" * 65)
summarise("BUY", [t for t in rows if t["side"] == "BUY"])

print("=" * 65)
print("SELL ONLY")
print("=" * 65)
summarise("SELL", [t for t in rows if t["side"] == "SELL"])

# Finer-grained BUY-side breakdown around the 8.5 cutoff
print("=" * 65)
print("BUY ONLY — fine-grained around 8.5 cutoff")
print("=" * 65)
buy = [t for t in rows if t["side"] == "BUY"]
print(f"BUY (n={len(buy)})")
print(f"  bucket      n     WR      sumP&L    expect")
by = defaultdict(list)
for t in buy:
    by[bucket_finer(t["entry_score"])].append(t)
for b in sorted(by, key=lambda k: -float(k.split("-")[0].replace("+", "").replace("<", "0").strip())):
    cohort = by[b]
    n = len(cohort)
    w = sum(1 for t in cohort if t["pnl"] > 0)
    s = sum(t["pnl"] for t in cohort)
    e = s / n
    print(f"  {b}  {n:>3}  {100*w/n:>5.1f}%  Rs.{s:>+7.0f}  {e:>+6.1f}")
print()

# Specific cohort the |score|>=8.5 score-cap experiment would block
print("=" * 65)
print("SCORE-CAP EXPERIMENT (refuse |score| >= 8.5 hypothetical)")
print("=" * 65)
blocked = [t for t in rows if abs(t["entry_score"]) >= 8.5]
admitted = [t for t in rows if abs(t["entry_score"]) < 8.5]
for label, cohort in [("BLOCKED", blocked), ("ADMITTED", admitted)]:
    if cohort:
        n = len(cohort)
        w = sum(1 for t in cohort if t["pnl"] > 0)
        s = sum(t["pnl"] for t in cohort)
        big_l = sum(1 for t in cohort if t["pnl"] < -100)
        print(f"  {label:>9} n={n:>3}  WR {100*w/n:>5.1f}%  Rs.{s:>+7.0f}  expect Rs.{s/n:>+6.1f}  big-losers (<-100): {big_l}")
print()

# Same for >= 8.0 (alternate cutoff)
print("=" * 65)
print("SCORE-CAP EXPERIMENT (refuse |score| >= 8.0 hypothetical)")
print("=" * 65)
blocked = [t for t in rows if abs(t["entry_score"]) >= 8.0]
admitted = [t for t in rows if abs(t["entry_score"]) < 8.0]
for label, cohort in [("BLOCKED", blocked), ("ADMITTED", admitted)]:
    if cohort:
        n = len(cohort)
        w = sum(1 for t in cohort if t["pnl"] > 0)
        s = sum(t["pnl"] for t in cohort)
        big_l = sum(1 for t in cohort if t["pnl"] < -100)
        print(f"  {label:>9} n={n:>3}  WR {100*w/n:>5.1f}%  Rs.{s:>+7.0f}  expect Rs.{s/n:>+6.1f}  big-losers (<-100): {big_l}")
print()

# BUY-only score-cap
print("=" * 65)
print("BUY-ONLY SCORE-CAP (|BUY score| >= 8.5)")
print("=" * 65)
buys = [t for t in rows if t["side"] == "BUY"]
blocked = [t for t in buys if abs(t["entry_score"]) >= 8.5]
admitted = [t for t in buys if abs(t["entry_score"]) < 8.5]
for label, cohort in [("BLOCKED", blocked), ("ADMITTED", admitted)]:
    if cohort:
        n = len(cohort)
        w = sum(1 for t in cohort if t["pnl"] > 0)
        s = sum(t["pnl"] for t in cohort)
        print(f"  {label:>9} n={n:>3}  WR {100*w/n:>5.1f}%  Rs.{s:>+7.0f}  expect Rs.{s/n:>+6.1f}")
print()

conn.close()
