"""Decay-cluster audit (Awaiting-Data trigger tool for STRATEGY_ROADMAP.md #269).

Counts same-side SIGNAL_DECAY clusters in `data/trades.db` (>= 2 same-side
SIGNAL_DECAY exits within 5 min) and grades the post-cluster cohort: every
trade that opened on the same direction within 30 min of the last cluster exit.

Used as the empirical evidence path for the Awaiting-Data #269 promote /
resolve-as-noise trigger. Run as needed on the EOD trade ledger:

    python scripts/decay_cluster_audit.py

No CLI args yet (single-purpose tool, single config above). Read-only DB query.

Per the analyst-review.md discipline, the gate this audit motivates is NOT
shipped until cumulative cluster count >= 7 AND aggregate post-cluster WR <= 30%
AND net P&L < -Rs.300. See STRATEGY_ROADMAP.md #269 for full trigger spec.

Note: exit_time / entry_time columns are HH:MM:SS strings; date is YYYY-MM-DD.
"""
import sqlite3
from datetime import datetime

# Tuneable thresholds (mirror STRATEGY_ROADMAP.md #269 implementation sketch).
CLUSTER_WINDOW_SECONDS = 300         # >= 2 same-side SIGNAL_DECAY within 5 min
FOLLOWUP_WINDOW_SECONDS = 1800       # check next 30 min for same-side re-entry

conn = sqlite3.connect('data/trades.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()


def to_dt(date_str, time_str):
    if not date_str or not time_str:
        return None
    try:
        return datetime.combine(
            datetime.strptime(date_str, "%Y-%m-%d").date(),
            datetime.strptime(time_str, "%H:%M:%S").time(),
        )
    except Exception:
        return None


cur.execute(
    "SELECT id, date, symbol, side, entry_score, entry_time, exit_time, exit_reason, pnl "
    "FROM trades WHERE exit_reason='SIGNAL_DECAY' ORDER BY date, exit_time"
)
decays = [dict(r) for r in cur.fetchall()]
print(f"Total SIGNAL_DECAY trades in ledger: {len(decays)}")
for d in decays:
    print(f"   {d['date']} {d['exit_time']} {d['symbol']:>14s} {d['side']:>4s} pnl Rs.{d['pnl']:+.2f} entry-score {d['entry_score']:+.1f}")

clusters = []
i = 0
while i < len(decays) - 1:
    burst = [decays[i]]
    et0 = to_dt(decays[i]['date'], decays[i]['exit_time'])
    if et0 is None:
        i += 1
        continue
    j = i + 1
    while j < len(decays):
        etj = to_dt(decays[j]['date'], decays[j]['exit_time'])
        if etj is None:
            j += 1
            continue
        if (
            decays[j]['side'] != decays[i]['side']
            or decays[j]['date'] != decays[i]['date']
            or (etj - et0).total_seconds() > CLUSTER_WINDOW_SECONDS
        ):
            break
        burst.append(decays[j])
        j += 1
    if len(burst) >= 2:
        clusters.append(burst)
        i = j
    else:
        i += 1

print()
print(f"Same-side SIGNAL_DECAY clusters (>=2 within 5 min): {len(clusters)}")
print()

cur.execute(
    "SELECT id, date, symbol, side, entry_score, entry_time, exit_time, exit_reason, pnl "
    "FROM trades ORDER BY date, entry_time"
)
all_trades = [dict(r) for r in cur.fetchall()]

total_n = 0
total_pnl = 0.0
total_w = 0
for c in clusters:
    cluster_exits = [to_dt(r['date'], r['exit_time']) for r in c if r['exit_time']]
    cluster_exits = [x for x in cluster_exits if x]
    if not cluster_exits:
        continue
    cluster_end = max(cluster_exits)
    side = c[0]['side']
    cluster_date = c[0]['date']
    next_same_side = []
    for t in all_trades:
        if t['date'] != cluster_date:
            continue
        et = to_dt(t['date'], t['entry_time'])
        if not et:
            continue
        if et > cluster_end and (et - cluster_end).total_seconds() <= FOLLOWUP_WINDOW_SECONDS and t['side'] == side:
            next_same_side.append(t)
    syms = ','.join(t['symbol'] for t in c)
    if next_same_side:
        cp = sum(t['pnl'] or 0 for t in next_same_side)
        cw = sum(1 for t in next_same_side if (t['pnl'] or 0) > 0)
        total_n += len(next_same_side)
        total_pnl += cp
        total_w += cw
        fsyms = ','.join(f"{t['symbol']}({t['pnl']:+.0f})" for t in next_same_side)
        print(f"  {cluster_date} {side} cluster [{syms}] @ {cluster_end.strftime('%H:%M:%S')}")
        print(f"      -> next 30min same-side entries: {len(next_same_side)} {fsyms}")
        print(f"         net Rs.{cp:.2f}, {cw}W/{len(next_same_side)-cw}L")
    else:
        print(f"  {cluster_date} {side} cluster [{syms}] @ {cluster_end.strftime('%H:%M:%S')} -> NO same-side entries in next 30 min")

print()
print(f"Aggregate follow-up after same-side SIGNAL_DECAY cluster (within 30 min):")
print(f"  Trades: {total_n}")
print(f"  Net P&L: Rs.{total_pnl:.2f}")
if total_n:
    print(f"  Wins: {total_w} of {total_n}  ({100*total_w/total_n:.1f}%)")
print()
print("Decision: if aggregate is net-negative AND WR < ~30%, post-decay-cluster cooldown is EV+")
conn.close()
