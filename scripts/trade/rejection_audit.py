"""
Post-trade rejection audit.

Parses logs/portfolio.log for the given date, finds every entry that the
order engine SKIPPED (rejection warnings), then fetches Zerodha 5-min
candles from the rejection time to market close (15:30 IST) and prints
a verdict for each:

  AVOIDED_LOSS    — hypothetical SL would have triggered
  MISSED_PROFIT   — hypothetical target would have triggered first
  AVOIDED_MILD    — closed below rejection price (no SL hit)
  MISSED_MILD     — closed above rejection price (no target hit)
  NEUTRAL         — within +/- 0.5% drift

Read-only. Does NOT touch the live trading hot path.

Usage:
  python scripts/trade/rejection_audit.py                # today
  python scripts/trade/rejection_audit.py --date 2026-04-20
  python scripts/trade/rejection_audit.py --append-report  # also append to trading_report_DD.txt
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from collections import OrderedDict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from config import Config                       # noqa: E402
from config import now_ist                      # noqa: E402  # IST-aware date
from core.logger import Logger                  # noqa: E402
from core.zerodha_client import ZerodhaClient   # noqa: E402

LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "portfolio.log")

# Matches the standard rejection log line:
#   2026-04-20 13:57:49,081 [OrderEngine] WARNING — SIEMENS: BUY but ...
REJ_RX = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+"
    r"\[OrderEngine\s*\]\s+WARNING\s+[—-]+\s+"
    r"(?P<sym>[A-Z][A-Z0-9&\-]+):\s+(?P<msg>.+)$"
)

# Skip-detection: only treat as rejection if the message ends with
# "Skipping" / "skipping entry" / "skipping re-entry" / etc.
SKIP_MARKERS = (
    "skipping",
    "skipping entry",
    "skipping re-entry",
    "no viable setups",
)

# 1.5% / 1.2% are the live-config defaults; we read them from Config to
# stay in sync if the user tunes them.
SL_PCT     = Config.DEFAULT_STOP_LOSS_PCT     # e.g. 1.5
TARGET_PCT = Config.DEFAULT_TARGET_PCT        # e.g. 1.2
DRIFT_BAND = 0.5                              # NEUTRAL band, percent


def parse_log(date: dt.date) -> list[dict]:
    """Return list of {ts, sym, msg} rejections for the given date."""
    if not os.path.exists(LOG_PATH):
        print(f"WARN: {LOG_PATH} not found", file=sys.stderr)
        return []

    prefix = date.strftime("%Y-%m-%d")
    out: list[dict] = []
    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith(prefix):
                continue
            m = REJ_RX.match(line.rstrip("\n"))
            if not m:
                continue
            msg = m.group("msg")
            low = msg.lower()
            if not any(mk in low for mk in SKIP_MARKERS):
                continue
            out.append({
                "ts":  dt.datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S"),
                "sym": m.group("sym"),
                "msg": msg.rstrip(". "),
            })
    return out


def dedupe_first(rejections: list[dict]) -> list[dict]:
    """Keep only the first rejection per symbol (per day)."""
    seen: OrderedDict[str, dict] = OrderedDict()
    for r in rejections:
        if r["sym"] not in seen:
            seen[r["sym"]] = r
    return list(seen.values())


def fetch_window(kc: ZerodhaClient, sym: str, t_from: dt.datetime,
                 t_to: dt.datetime) -> list[dict]:
    """5-minute candles from t_from → t_to (inclusive). Returns [] on failure."""
    try:
        return kc.get_historical(sym, "NSE", t_from, t_to, "5minute") or []
    except Exception as e:                                  # noqa: BLE001
        print(f"  {sym}: historical fetch failed — {type(e).__name__}: {e}",
              file=sys.stderr)
        return []


def verdict(side: str, ref_price: float, candles: list[dict]) -> dict:
    """
    Walk candles in order. Return verdict dict with keys:
      ref, close, high_aft, low_aft, sl_hit, tgt_hit, sl_first,
      tgt_first, pct_move, label
    side: "BUY" (long) — most rejections are long. We assume BUY for now.
    """
    if not candles:
        return {"label": "NO_DATA"}

    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]

    sl_lvl  = ref_price * (1 - SL_PCT / 100.0)      # for BUY
    tgt_lvl = ref_price * (1 + TARGET_PCT / 100.0)

    sl_first = tgt_first = None
    for i, c in enumerate(candles):
        if sl_first is None and c["low"] <= sl_lvl:
            sl_first = i
        if tgt_first is None and c["high"] >= tgt_lvl:
            tgt_first = i
        if sl_first is not None and tgt_first is not None:
            break

    close = closes[-1]
    pct   = (close - ref_price) / ref_price * 100.0

    if sl_first is not None and (tgt_first is None or sl_first < tgt_first):
        label = "AVOIDED_LOSS"
    elif tgt_first is not None and (sl_first is None or tgt_first < sl_first):
        label = "MISSED_PROFIT"
    elif pct >= DRIFT_BAND:
        label = "MISSED_MILD"
    elif pct <= -DRIFT_BAND:
        label = "AVOIDED_MILD"
    else:
        label = "NEUTRAL"

    return {
        "ref":       ref_price,
        "close":     close,
        "high_aft":  max(highs),
        "low_aft":   min(lows),
        "sl_hit":    sl_first is not None,
        "tgt_hit":   tgt_first is not None,
        "sl_first":  sl_first,
        "tgt_first": tgt_first,
        "pct_move":  pct,
        "label":     label,
    }


def _slot_pnl(ref: float, close: float, slot_rupees: float) -> tuple[int, float]:
    """
    Hypothetical P&L assuming we'd entered a single slot.
      qty = floor(slot_rupees / ref)
      pnl = qty * (close - ref)
    Returns (qty, pnl_rupees).
    """
    if ref <= 0:
        return 0, 0.0
    qty = int(slot_rupees // ref)
    return qty, qty * (close - ref)


def render(date: dt.date, rows: list[dict], slot_rupees: float) -> str:
    """Format the audit table as plain text for stdout / report append."""
    lines: list[str] = []
    sep_top = "=" * 116
    sep_row = "─" * 116
    lines.append(sep_top)
    lines.append(f"  REJECTION AUDIT — {date.isoformat()}")
    lines.append(
        f"  Hypothetical: BUY 1 slot (~Rs.{slot_rupees:,.0f}) at rejection price, "
        f"exit at 15:30 close. SL {SL_PCT}% / Target {TARGET_PCT}%, "
        f"NEUTRAL band ±{DRIFT_BAND}%."
    )
    lines.append(sep_top)
    if not rows:
        lines.append("  No rejections found in log for this date.")
        return "\n".join(lines)

    # Header — fixed widths, P&L column added, reason gets remaining width
    header = (
        f"{'SYMBOL':<11} {'TIME':<8} {'REJ_PX':>10} {'CLOSE':>10} "
        f"{'Δ%':>7} {'P&L Rs':>10}  {'VERDICT':<14}  REASON"
    )
    lines.append(header)
    lines.append(sep_row)

    counts: dict[str, int] = {}
    total_pnl = 0.0
    pnl_avoided = 0.0   # losses avoided (positive number)
    pnl_missed = 0.0    # profit missed (positive number)

    for r in rows:
        v = r.get("verdict", {})
        label = v.get("label", "NO_DATA")
        counts[label] = counts.get(label, 0) + 1
        reason = r["msg"]

        if label == "NO_DATA":
            lines.append(
                f"{r['sym']:<11} {r['ts'].strftime('%H:%M:%S'):<8} "
                f"{'—':>10} {'—':>10} {'—':>7} {'—':>10}  "
                f"{label:<14}  {reason}"
            )
            continue

        qty, pnl = _slot_pnl(v["ref"], v["close"], slot_rupees)
        total_pnl += pnl
        if pnl < 0:
            pnl_avoided += -pnl
        elif pnl > 0:
            pnl_missed += pnl

        lines.append(
            f"{r['sym']:<11} {r['ts'].strftime('%H:%M:%S'):<8} "
            f"{v['ref']:>10.2f} {v['close']:>10.2f} "
            f"{v['pct_move']:>+6.2f}% {pnl:>+10.2f}  "
            f"{label:<14}  {reason}"
        )

    lines.append(sep_row)
    summary = ", ".join(f"{k}={c}" for k, c in sorted(counts.items()))
    lines.append(f"Counts : {summary}")
    good = counts.get("AVOIDED_LOSS", 0) + counts.get("AVOIDED_MILD", 0)
    bad  = counts.get("MISSED_PROFIT", 0) + counts.get("MISSED_MILD", 0)
    lines.append(
        f"Verdict: {good} good rejection(s), {bad} questionable, "
        f"{counts.get('NEUTRAL', 0)} neutral, {counts.get('NO_DATA', 0)} no-data."
    )
    lines.append(
        f"P&L    : Avoided losses Rs.{pnl_avoided:,.2f} | "
        f"Missed profit Rs.{pnl_missed:,.2f} | "
        f"Net (avoided − missed) Rs.{(pnl_avoided - pnl_missed):+,.2f}"
    )
    lines.append(
        "  Note: P&L assumes 1 hypothetical slot per symbol; actual sizing "
        "would have been score-weighted."
    )
    if counts.get("MISSED_PROFIT", 0):
        lines.append(
            "  ⚠ MISSED_PROFIT entries warrant review — the gate that "
            "rejected them may be too strict for that pattern."
        )
    return "\n".join(lines)


# Marker that bounds the audit block in the report file. Used so re-runs
# can REPLACE the existing block instead of appending duplicates.
AUDIT_MARK_BEGIN = "<!-- REJECTION_AUDIT_BEGIN -->"
AUDIT_MARK_END   = "<!-- REJECTION_AUDIT_END -->"


def append_to_report(date: dt.date, text: str) -> str | None:
    """
    Append (or replace) the audit block in today's trading report.
    Idempotent: re-running OVERWRITES the previous block instead of
    duplicating — useful when the script is re-run after a tweak.
    """
    rel = f"reports/trading/{date.year}/{date.month:02d}/trading_report_{date.day:02d}.txt"
    path = os.path.join(PROJECT_ROOT, rel)
    if not os.path.exists(path):
        print(f"WARN: trading report not found at {rel}", file=sys.stderr)
        return None

    block = f"\n{AUDIT_MARK_BEGIN}\n{text}\n{AUDIT_MARK_END}\n"

    with open(path, "r", encoding="utf-8") as f:
        existing = f.read()

    if AUDIT_MARK_BEGIN in existing and AUDIT_MARK_END in existing:
        before, _, rest = existing.partition(AUDIT_MARK_BEGIN)
        _, _, after    = rest.partition(AUDIT_MARK_END)
        new = before.rstrip() + "\n" + block + after.lstrip()
    else:
        # Strip any older non-marked audit section ("REJECTION AUDIT — ...")
        # that may have been appended by an earlier version of this script.
        legacy_marker = f"REJECTION AUDIT — {date.isoformat()}"
        if legacy_marker in existing:
            head = existing.split("\n")
            cut = None
            for i, ln in enumerate(head):
                if legacy_marker in ln:
                    # walk back to the preceding "===" separator
                    cut = i
                    while cut > 0 and not head[cut - 1].startswith("=="):
                        cut -= 1
                    cut = max(cut - 1, 0)
                    break
            if cut is not None:
                existing = "\n".join(head[:cut]).rstrip() + "\n"
        new = existing.rstrip() + "\n" + block

    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    return path


def run_audit(
    date: dt.date | None = None,
    *,
    append_report: bool = True,
    print_to_stdout: bool = True,
    log: Logger | None = None,
    budget: float | None = None,
) -> str:
    """
    Programmatic entry-point for EOD pipeline.

    - Parses logs/portfolio.log for rejections on `date` (default: today).
    - Fetches close prices and computes verdicts.
    - Logs the rendered table line-by-line via `log` (so EOD users see
      it live in the terminal alongside other manager output).
    - Optionally appends/replaces the block in today's trading report.

    Returns the rendered text (empty string if no rejections found).

    Failures are caught — this function never raises. EOD pipeline can
    call it without a try/except wrapper.
    """
    date = date or now_ist().date()
    log  = log or Logger("rej-audit")

    try:
        rejections = dedupe_first(parse_log(date))
    except Exception as e:                                  # noqa: BLE001
        log.warning(f"Rejection audit: log parse failed — {type(e).__name__}: {e}")
        return ""

    if not rejections:
        text = render(date, [], slot_rupees=0.0)
        if print_to_stdout:
            for ln in text.splitlines():
                log.info(ln)
        return text

    # Slot size: budget / max_positions (matches live engine defaults).
    if budget is None:
        budget = float(getattr(Config, "DEFAULT_BUDGET", 50000.0) or 50000.0)
    max_pos = int(getattr(Config, "MAX_POSITIONS", 3) or 3)
    slot_rupees = budget / max(max_pos, 1)

    try:
        kc = ZerodhaClient(Config, log)
        kc.login(interactive=False)
    except Exception as e:                                  # noqa: BLE001
        log.warning(f"Rejection audit: Zerodha login failed — {type(e).__name__}: {e}")
        return ""

    eod = dt.datetime.combine(date, dt.time(15, 30))

    rows: list[dict] = []
    for r in rejections:
        t_from = r["ts"] - dt.timedelta(minutes=5)
        candles = fetch_window(kc, r["sym"], t_from, eod)

        ref_price = None
        if candles:
            for c in candles:
                if c["date"].replace(tzinfo=None) >= r["ts"] - dt.timedelta(minutes=1):
                    ref_price = c["open"]
                    break
            if ref_price is None:
                ref_price = candles[0]["open"]

        if ref_price is None:
            r["verdict"] = {"label": "NO_DATA"}
        else:
            after = [c for c in candles
                     if c["date"].replace(tzinfo=None) >= r["ts"] - dt.timedelta(minutes=1)]
            # Guard: if no candles exist AT or AFTER rejection time, the
            # only candles available are pre-rejection. Verdicts derived
            # from pre-rejection bars would be backwards (we'd be "reading
            # the future from the past"). Mark NO_DATA instead.
            if not after:
                r["verdict"] = {"label": "NO_DATA"}
            else:
                r["verdict"] = verdict("BUY", ref_price, after)
        rows.append(r)

    text = render(date, rows, slot_rupees=slot_rupees)

    if print_to_stdout:
        for ln in text.splitlines():
            log.info(ln)

    if append_report:
        try:
            path = append_to_report(date, text)
            if path:
                log.info(
                    f"Rejection audit appended to "
                    f"{os.path.relpath(path, PROJECT_ROOT)}"
                )
        except Exception as e:                              # noqa: BLE001
            log.warning(
                f"Rejection audit: append to report failed — "
                f"{type(e).__name__}: {e}"
            )

    return text


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", default=None,
                   help="YYYY-MM-DD (default: today, IST)")
    p.add_argument("--append-report", action="store_true",
                   help="Append the audit block to today's trading_report_DD.txt")
    p.add_argument("--budget", type=float, default=None,
                   help="Override budget (Rs) used for hypothetical slot sizing")
    args = p.parse_args()

    date = (dt.date.fromisoformat(args.date) if args.date
            else now_ist().date())

    text = run_audit(
        date,
        append_report=args.append_report,
        print_to_stdout=True,
        budget=args.budget,
    )
    if not text:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
