"""Dry-run strategy dashboard page.

Per-strategy P&L, stats, and configuration surface for dry-run data.
Strategy classification comes from two sources:
  1. Trading report JSONs (authoritative for trade P&L + actual strategy)
  2. intraday_candidates DB table (candidate funnel: scored/entered/rejected)

The dropdown lets the user pick a strategy and see its performance
in isolation — essential when multiple strategies are being dry-run
in parallel.
"""

from __future__ import annotations

import glob
import html
import json
import os
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

from config import Config, now_ist
from modes.dashboard.nav import render_topnav


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_DB = PROJECT_ROOT / Config.TRADE_ANALYSIS_DB_PATH
REPORTS_TRADING = PROJECT_ROOT / "reports" / "trading"


# ── Strategy definitions ──────────────────────────────────────────
# Maps strategy_type → human description + which config knobs matter.

STRATEGY_META: dict[str, dict[str, Any]] = {
    "NOAI_LEGACY_FULL": {
        "label": "NoAI Legacy (Blended Score)",
        "desc": "Original blended score from all indicators — candle patterns + technical score. OOS PF 0.82.",
        "config_keys": [
            "SCAN_UNIVERSE", "MIN_SCORE", "CANDLE_INTERVAL",
            "MAX_TRADES_PER_DAY",
            "ATR_MULTIPLIER", "RR_TARGET_RATIO", "RR_HARD_FLOOR",
            "DEFAULT_STOP_LOSS_PCT", "DEFAULT_TARGET_PCT",
            "SQUARE_OFF_HOUR", "SQUARE_OFF_MINUTE",
            "LOSER_EXIT_HOUR", "LOSER_EXIT_MINUTE",
            "TRAIL_AFTER_RISK_MULTIPLE",
        ],
    },
    "NOAI_GAP_AND_GO": {
        "label": "NoAI Gap-and-Go v1.0.0",
        "desc": "Gap + volume alpha v1.0.0. Entry at 09:30 LTP, no gap-hold or score checks. Universe NIFTY50. OOS PF 1.37.",
        "config_keys": [
            "SCAN_UNIVERSE",
            "GAP_GO_MIN_GAP_PCT", "GAP_GO_MAX_GAP_PCT",
            "GAP_GO_VOLUME_MULTIPLE", "GAP_GO_DAILY_CAP",
            "GAP_GO_RSI_BUY_CEILING", "GAP_GO_RSI_SELL_FLOOR",
            "ATR_MULTIPLIER", "RR_TARGET_RATIO", "RR_HARD_FLOOR",
            "GAP_GO_SQUARE_OFF_HOUR", "GAP_GO_SQUARE_OFF_MINUTE",
            "TRAIL_AFTER_RISK_MULTIPLE",
        ],
    },
    "NOAI_SIMPLE_MR_BASELINE": {
        "label": "NoAI Simple Mean-Reversion Baseline",
        "desc": "Mean-reversion baseline for Stage 1 research.",
        "config_keys": [
            "SCAN_UNIVERSE", "MIN_SCORE", "CANDLE_INTERVAL",
            "MAX_TRADES_PER_DAY",
            "ATR_MULTIPLIER", "RR_TARGET_RATIO",
            "SQUARE_OFF_HOUR", "SQUARE_OFF_MINUTE",
        ],
    },
}

# ── Gap-and-Go versioned meta (v1.1.0+) ────────────────────────────
# Any NOAI_GAP_AND_GO_X.Y.Z profile gets this meta + version suffix.
_GAP_GO_VERSIONED_CONFIG_KEYS = [
    "SCAN_UNIVERSE",
    "GAP_GO_MIN_GAP_PCT", "GAP_GO_MAX_GAP_PCT",
    "GAP_GO_VOLUME_MULTIPLE", "GAP_GO_DAILY_CAP",
    "GAP_GO_RSI_BUY_CEILING", "GAP_GO_RSI_SELL_FLOOR",
    "GAP_GO_ENTRY_AFTER_CANDLE_CLOSE", "GAP_GO_GAP_HOLD_MIN_PCT",
    "GAP_GO_SCORE_CONTRADICTION_BLOCK", "GAP_GO_USE_CANDLE_CLOSE_PRICE",
    "GAP_GO_BROAD_GAP_THRESHOLD", "GAP_GO_BROAD_VOL_MULTIPLE",
    "ATR_MULTIPLIER", "RR_TARGET_RATIO", "RR_HARD_FLOOR",
    "GAP_GO_SQUARE_OFF_HOUR", "GAP_GO_SQUARE_OFF_MINUTE",
    "TRAIL_AFTER_RISK_MULTIPLE",
]


def _get_strategy_meta(strategy_type: str) -> dict[str, Any]:
    """Look up strategy metadata, with fallback for versioned gap-and-go profiles."""
    if strategy_type in STRATEGY_META:
        return STRATEGY_META[strategy_type]
    # Versioned gap-and-go: NOAI_GAP_AND_GO_1.1.0, NOAI_GAP_AND_GO_1.1.1, etc.
    if strategy_type.startswith("NOAI_GAP_AND_GO_"):
        version = strategy_type.split("NOAI_GAP_AND_GO_")[-1]
        # Build version-appropriate description
        if version >= "1.2":
            desc = f"Gap + volume alpha v{version}. Adaptive vol on broad-gap days. NIFTY100. OOS PF 1.30."
        elif version >= "1.1.1":
            desc = f"Gap + volume alpha v{version}. Universe expanded to NIFTY100. OOS PF 1.62."
        elif version >= "1.1":
            desc = f"Gap + volume alpha v{version}. Entry at candle close, gap-hold, score-contra. NIFTY50. OOS PF 1.55."
        else:
            desc = f"Gap + volume alpha v{version}. NIFTY50. OOS PF 1.28."
        return {
            "label": f"NoAI Gap-and-Go v{version}",
            "desc": desc,
            "config_keys": _GAP_GO_VERSIONED_CONFIG_KEYS,
        }
    return {"label": strategy_type, "desc": "", "config_keys": []}


def _connect_existing(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    return col in cols


# ── Report JSON loader (authoritative trade data) ────────────────

def _load_dryrun_reports() -> list[dict[str, Any]]:
    """Load all dry-run trading report JSONs. Each contains positions,
    P&L breakdown, config (including strategy_profile), and date."""
    reports: list[dict[str, Any]] = []
    for raw in sorted(glob.glob(
        str(REPORTS_TRADING / "**" / "*_dry_run.json"), recursive=True
    )):
        path = Path(raw)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("mode") != "dry_run":
            continue
        cfg = data.get("config") or {}
        positions = data.get("positions") or []
        pnl_data = data.get("pnl") or {}
        # The strategy_profile in the report is what the bot actually
        # wrote at end of session — but it can be wrong when the
        # manager fell back (e.g., gap-go → legacy). The user can
        # correct this. For now, trust the report.
        strategy = str(cfg.get("strategy_profile") or "NOAI_LEGACY_FULL")
        date = str(data.get("date") or "")
        if not date:
            continue

        trades = []
        for p in positions:
            if not isinstance(p, dict):
                continue
            if p.get("status") != "CLOSED":
                # Include open positions too
                pass
            trades.append({
                "date": date,
                "symbol": p.get("symbol", ""),
                "side": p.get("side", ""),
                "entry_price": p.get("entry_price"),
                "exit_price": p.get("exit_price"),
                "entry_time": p.get("entry_time"),
                "exit_time": p.get("exit_time"),
                "exit_reason": p.get("exit_reason", ""),
                "pnl": float(p.get("pnl") or 0.0),
                "status": p.get("status", "CLOSED"),
            })

        reports.append({
            "date": date,
            "strategy_profile": strategy,
            "config": cfg,
            "trades": trades,
            "gross_pnl": float(pnl_data.get("gross_pnl") or 0.0),
            "net_profit": float(pnl_data.get("net_profit") or 0.0),
            "charges": float(
                (pnl_data.get("charges") or {}).get("total_costs") or 0.0
            ),
            "path": str(path),
        })
    return reports


# ── Candidate funnel from DB ──────────────────────────────────────

def _candidate_funnel(strategy_type: str,
                      report_dates: set[str]) -> dict[str, Any]:
    """Read candidate funnel stats from intraday_candidates DB,
    filtered by dates that belong to this strategy (from reports)."""
    result = {
        "total_candidates": 0,
        "status_counts": {},
        "side_counts": {},
        "rejections": {},
    }
    conn = _connect_existing(ANALYSIS_DB)
    if conn is None:
        return result
    try:
        if not _table_exists(conn, "intraday_candidates"):
            return result
        if not report_dates:
            return result

        placeholders = ",".join("?" for _ in report_dates)
        rows = conn.execute(
            f"SELECT date, status, side, rejected_gate "
            f"FROM intraday_candidates "
            f"WHERE date IN ({placeholders})",
            tuple(sorted(report_dates)),
        ).fetchall()

        result["total_candidates"] = len(rows)
        result["status_counts"] = dict(Counter(
            str(r["status"] or "<none>") for r in rows
        ))
        result["side_counts"] = dict(Counter(
            str(r["side"] or "<none>") for r in rows
        ))
        result["rejections"] = dict(Counter(
            str(r["rejected_gate"] or "<none>")
            for r in rows if r["status"] == "REJECTED"
        ))
    finally:
        conn.close()
    return result


# ── Main data aggregation ─────────────────────────────────────────

def _available_strategies() -> list[str]:
    """Return distinct strategy_profile values from dry-run reports."""
    reports = _load_dryrun_reports()
    types = {r["strategy_profile"] for r in reports}
    # Also add the current profile so it always appears in dropdown
    types.add(str(getattr(Config, "TRADE_STRATEGY_PROFILE", "NOAI_LEGACY_FULL")))
    return sorted(types)


def _strategy_stats(strategy_type: str) -> dict[str, Any]:
    """Aggregate stats for a given strategy from trading report JSONs."""
    all_reports = _load_dryrun_reports()
    reports = [r for r in all_reports if r["strategy_profile"] == strategy_type]

    result: dict[str, Any] = {
        "strategy_type": strategy_type,
        "total_candidates": 0,
        "status_counts": {},
        "side_counts": {},
        "rejections": {},
        "sessions": len(reports),
        "entered_trades": 0,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "win_rate": None,
        "gross_pnl": 0.0,
        "net_pnl": 0.0,
        "profit_factor": None,
        "expectancy": None,
        "avg_win": None,
        "avg_loss": None,
        "max_win": None,
        "max_loss": None,
        "best_day": None,
        "worst_day": None,
        "daily": [],
        "trades": [],
        "latest_date": None,
        "first_date": None,
        "config_hashes": [],
    }

    if not reports:
        return result

    # ── Collect all trades from reports ────────────────────────
    all_trades: list[dict[str, Any]] = []
    report_dates: set[str] = set()
    config_hashes: set[str] = set()

    for r in reports:
        report_dates.add(r["date"])
        cfg = r.get("config") or {}
        h = str(cfg.get("strategy_config_hash") or "")
        if h:
            config_hashes.add(h)
        all_trades.extend(r["trades"])

    result["config_hashes"] = sorted(config_hashes)
    result["first_date"] = min(report_dates)
    result["latest_date"] = max(report_dates)
    result["entered_trades"] = len(all_trades)

    # Store the latest report's config for display
    # (so dashboard shows what was actually used, not current live config)
    latest_report = max(reports, key=lambda r: r["date"])
    result["report_config"] = latest_report.get("config") or {}

    # Closed = has exit_price
    closed = [t for t in all_trades if t.get("exit_price") is not None]
    open_trades = [t for t in all_trades if t.get("exit_price") is None]
    result["closed_trades"] = len(closed)
    result["trades"] = all_trades  # show all (including open)

    # ── P&L stats from closed trades ──────────────────────────
    pnl_values = [float(t.get("pnl") or 0.0) for t in closed]

    if pnl_values:
        wins_list = [v for v in pnl_values if v > 0]
        losses_list = [v for v in pnl_values if v < 0]
        result["wins"] = len(wins_list)
        result["losses"] = len(losses_list)
        result["breakeven"] = len([v for v in pnl_values if v == 0])
        result["gross_pnl"] = round(
            sum(float(r.get("gross_pnl") or 0.0) for r in reports), 2
        )
        result["net_pnl"] = round(
            sum(float(r.get("net_profit") or 0.0) for r in reports), 2
        )
        result["win_rate"] = round(
            len(wins_list) / len(pnl_values) * 100, 1
        )
        result["avg_win"] = round(
            sum(wins_list) / len(wins_list), 2
        ) if wins_list else None
        result["avg_loss"] = round(
            sum(losses_list) / len(losses_list), 2
        ) if losses_list else None
        result["max_win"] = round(max(pnl_values), 2)
        result["max_loss"] = round(min(pnl_values), 2)

        total_wins = sum(wins_list)
        total_losses = -sum(losses_list)
        if total_losses > 0:
            result["profit_factor"] = round(total_wins / total_losses, 2)
        elif total_wins > 0:
            result["profit_factor"] = float("inf")
        result["expectancy"] = round(
            sum(pnl_values) / len(pnl_values), 2
        )

    # ── Daily P&L series (from report net_profit) ─────────────
    by_day: dict[str, float] = {}
    for r in reports:
        by_day[r["date"]] = round(float(r.get("net_profit") or 0.0), 2)

    cumulative = 0.0
    daily = []
    for day in sorted(by_day):
        net = by_day[day]
        cumulative = round(cumulative + net, 2)
        daily.append({"date": day, "net": net, "cum": cumulative})
    result["daily"] = daily

    if by_day:
        result["best_day"] = round(max(by_day.values()), 2)
        result["worst_day"] = round(min(by_day.values()), 2)

    # ── Candidate funnel from DB ──────────────────────────────
    funnel = _candidate_funnel(strategy_type, report_dates)
    result["total_candidates"] = funnel["total_candidates"]
    result["status_counts"] = funnel["status_counts"]
    result["side_counts"] = funnel["side_counts"]
    result["rejections"] = funnel["rejections"]

    return result


def _current_config_values(keys: list[str], report_config: dict | None = None) -> list[dict[str, str]]:
    """Read config values for display.

    When *report_config* is provided (from the latest trading report),
    values are sourced from what the bot actually ran with — not the
    current live Config, which may have changed since the dry-run.

    The report stores a flat dict with lowercase keys (e.g. 'universe',
    'strategy_profile'). We map common config keys to their report
    equivalents. For keys not found in the report we fall back to
    the live Config.
    """
    # Map Config key names → report JSON key names
    _REPORT_KEY_MAP: dict[str, str] = {
        "SCAN_UNIVERSE": "universe",
        "TRADE_STRATEGY_PROFILE": "strategy_profile",
        "STRATEGY_CONFIG_VERSION": "strategy_config_version",
        "MAX_POSITIONS": "max_positions",
        "DEFAULT_STOP_LOSS_PCT": "stop_loss_pct",
        "DEFAULT_TARGET_PCT": "target_pct",
    }
    out = []
    for k in keys:
        val = None
        if report_config:
            # Try mapped key first, then lowercase version
            rk = _REPORT_KEY_MAP.get(k)
            if rk and rk in report_config:
                val = report_config[rk]
            elif k.lower() in report_config:
                val = report_config[k.lower()]
        if val is None:
            val = getattr(Config, k, "<not set>")
        out.append({"key": k, "value": str(val)})
    return out


# ── HTML rendering ────────────────────────────────────────────────

def _fmt_rs(value: Any, *, signed: bool = True) -> str:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return "-"
    if signed:
        return f"₹{number:+,.2f}"
    return f"₹{number:,.2f}"


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "-"


def _pf_label(value: Any) -> str:
    if value is None:
        return "-"
    try:
        v = float(value)
        return "∞" if v == float("inf") else f"{v:.2f}"
    except (TypeError, ValueError):
        return "-"


def _pn(value: float) -> str:
    return "pos" if value > 0 else ("neg" if value < 0 else "")


def _metric_card(label: str, value: str, hint: str = "",
                 css_class: str = "") -> str:
    hint_html = (
        f'<div class="metric-hint">{html.escape(hint)}</div>' if hint else ""
    )
    return (
        f'<div class="metric {html.escape(css_class)}">'
        f'<div class="metric-label">{html.escape(label)}</div>'
        f'<div class="metric-value">{value}</div>'
        f'{hint_html}</div>'
    )


def _load_options_backtest() -> dict[str, Any]:
    """Load options backtest results from reports/backtest/options_bt_*.json."""
    result: dict[str, Any] = {}
    bt_dir = PROJECT_ROOT / "reports" / "backtest"
    for name in ["options_bt_full.json", "options_bt_train.json", "options_bt_test.json"]:
        path = bt_dir / name
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                key = name.replace("options_bt_", "").replace(".json", "").upper()
                result[key] = data
            except (OSError, json.JSONDecodeError):
                pass
    return result


def render_dryrun_page() -> str:
    """Render the full dry-run strategy dashboard page."""
    strategies = _available_strategies()
    all_stats: dict[str, dict[str, Any]] = {}
    for st in strategies:
        all_stats[st] = _strategy_stats(st)

    # If no data yet, also show the current config strategy
    current_profile = str(
        getattr(Config, "TRADE_STRATEGY_PROFILE", "NOAI_LEGACY_FULL")
    )
    if current_profile not in strategies:
        strategies.append(current_profile)
        all_stats[current_profile] = _strategy_stats(current_profile)

    # Build strategy options for dropdown
    options_html = ""
    for st in strategies:
        meta = _get_strategy_meta(st)
        label = meta.get("label", st)
        count = all_stats[st]["closed_trades"]
        net = all_stats[st]["net_pnl"]
        badge = f" ({count} trades, {_fmt_rs(net)})" if count > 0 else " (no trades)"
        options_html += (
            f'<option value="{html.escape(st)}">'
            f'{html.escape(label)}{html.escape(badge)}</option>\n'
        )

    # JSON data for all strategies
    chart_data: dict[str, Any] = {}
    for st, stats in all_stats.items():
        chart_data[st] = {
            "daily": stats["daily"],
            "trades": stats["trades"],
            "stats": {
                "total_candidates": stats["total_candidates"],
                "entered_trades": stats["entered_trades"],
                "closed_trades": stats["closed_trades"],
                "sessions": stats["sessions"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "breakeven": stats["breakeven"],
                "win_rate": stats["win_rate"],
                "gross_pnl": stats["gross_pnl"],
                "net_pnl": stats["net_pnl"],
                "profit_factor": stats["profit_factor"],
                "expectancy": stats["expectancy"],
                "avg_win": stats["avg_win"],
                "avg_loss": stats["avg_loss"],
                "max_win": stats["max_win"],
                "max_loss": stats["max_loss"],
                "best_day": stats["best_day"],
                "worst_day": stats["worst_day"],
                "first_date": stats["first_date"],
                "latest_date": stats["latest_date"],
                "status_counts": stats["status_counts"],
                "side_counts": stats["side_counts"],
                "rejections": stats["rejections"],
                "config_hashes": stats["config_hashes"],
            },
        }
    chart_json = json.dumps(chart_data, allow_nan=False).replace("</", "<\\/")

    # Strategy config sections
    config_sections: dict[str, list[dict[str, str]]] = {}
    for st in strategies:
        meta = _get_strategy_meta(st)
        keys = meta.get("config_keys", [])
        report_cfg = all_stats[st].get("report_config")
        config_sections[st] = _current_config_values(keys, report_config=report_cfg)
    config_json = json.dumps(config_sections).replace("</", "<\\/")

    # Strategy meta
    meta_json = json.dumps({
        st: {
            "label": _get_strategy_meta(st).get("label", st),
            "desc": _get_strategy_meta(st).get("desc", ""),
        }
        for st in strategies
    }).replace("</", "<\\/")

    # Options backtest data
    options_bt = _load_options_backtest()
    options_bt_json = json.dumps(options_bt, allow_nan=False, default=str).replace("</", "<\\/")

    body = f"""
<h1 class="page-title">Dry-Run Strategy Dashboard</h1>
<div class="sub">Per-strategy P&amp;L and statistics for dry-run data. Generated {html.escape(now_ist().strftime('%Y-%m-%d %H:%M IST'))}.</div>

<section class="card selector-card">
  <h2>Mode &amp; Strategy</h2>
  <div class="selector-row">
    <select id="mode-select" onchange="onModeChange()" style="min-width:180px">
      <option value="intraday" selected>Intraday Equity</option>
      <option value="options">Options (NIFTY)</option>
    </select>
    <select id="strategy-select" onchange="onStrategyChange()">
      {options_html}
    </select>
    <span id="strategy-desc" class="strategy-desc"></span>
  </div>
</section>

<section id="options-backtest-section" class="card" style="display:none">
  <h2>Options Backtest Results — v1.0</h2>
  <div id="options-bt-content"></div>
</section>

<section id="config-section" class="card">
  <h2>Strategy Configuration</h2>
  <div id="config-table-wrap"></div>
</section>

<section id="stats-cards" class="grid four"></section>

<section class="grid two">
  <div id="stats-detail" class="card">
    <h2>Detailed Stats</h2>
    <table id="detail-table" class="kv"><tbody></tbody></table>
  </div>
  <div class="card">
    <h2>Pipeline Funnel</h2>
    <table id="funnel-table" class="kv"><tbody></tbody></table>
  </div>
</section>

<section class="grid two">
  <div class="card chart-card">
    <h2>Cumulative P&amp;L</h2>
    <div class="chart-canvas-wrap"><canvas id="cum-chart"></canvas></div>
  </div>
  <div class="card chart-card">
    <h2>Daily P&amp;L</h2>
    <div class="chart-canvas-wrap"><canvas id="daily-chart"></canvas></div>
  </div>
</section>

<section class="card">
  <h2>Rejection Breakdown</h2>
  <div class="grid two">
    <div class="chart-canvas-wrap" style="height:220px"><canvas id="reject-chart"></canvas></div>
    <div id="reject-table-wrap"></div>
  </div>
</section>

<section class="card">
  <h2>Trade Log</h2>
  <div class="table-scroll">
    <table id="trade-table" class="data-table">
      <thead><tr>
        <th>Date</th><th>Symbol</th><th>Side</th>
        <th class="r">Entry</th><th class="r">Exit</th>
        <th>Entry Time</th><th>Exit Time</th>
        <th>Exit Reason</th><th class="r">P&amp;L</th>
      </tr></thead>
      <tbody id="trade-tbody"></tbody>
    </table>
  </div>
</section>

<script>
window.DRYRUN_DATA = {chart_json};
window.DRYRUN_CONFIG = {config_json};
window.DRYRUN_META = {meta_json};
window.OPTIONS_BT = {options_bt_json};
</script>
{_SCRIPT}
"""
    return _wrap("Dry-Run Strategies", body)


def _wrap(title: str, body: str) -> str:
    from modes.dashboard.error_toast import error_toast_html, error_toast_script
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Portfolio Manager - {html.escape(title)}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>{_STYLE}</style>
</head>
<body>
{error_toast_html()}
<div class="wrap">
  {render_topnav('/dryrun')}
  {body}
  <footer>AI Portfolio Manager — dry-run strategy dashboard</footer>
</div>
{error_toast_script()}
</body>
</html>"""


_STYLE = r"""
:root { --bg:#f7f8fa; --fg:#1c1f23; --muted:#6a7280; --card:#ffffff;
        --line:#e5e7eb; --ok:#1b8e3a; --warn:#b06a00; --neg:#c62828;
        --blue:#1c4ed8; --soft:#f0f1f3; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
       background:var(--bg); color:var(--fg); margin:0; padding:24px;
       line-height:1.5; }
.wrap { max-width:1180px; margin:0 auto; }
nav.topnav { display:flex; gap:14px; align-items:center; padding:10px 16px;
             background:var(--card); border:1px solid var(--line);
             border-radius:8px; margin-bottom:18px; font-size:14px;
             flex-wrap:wrap; }
nav.topnav a, nav.topnav button.nav-back { color:var(--fg);
             text-decoration:none; font-weight:500; }
nav.topnav a:hover { text-decoration:underline; }
nav.topnav button.nav-back { font:inherit; padding:4px 9px;
             border:1px solid var(--line); border-radius:5px;
             background:white; cursor:pointer; }
nav.topnav button.nav-back:hover { background:var(--soft); }
nav.topnav .here { color:var(--muted); }
nav.topnav .sep { color:var(--muted); }
nav.topnav .spacer { flex:1; }
h1.page-title { font-size:24px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:18px; }
.grid { display:grid; gap:16px; margin-bottom:16px; }
.grid.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.grid.four { grid-template-columns: repeat(4, minmax(0, 1fr)); }
@media (max-width:900px) { .grid.two, .grid.four { grid-template-columns:1fr; } }
.card, .metric { background:var(--card); border:1px solid var(--line);
                 border-radius:8px; padding:18px 20px; }
.card h2 { margin:0 0 10px; font-size:17px; }
.selector-card { }
.selector-row { display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
.selector-row select { font:inherit; font-size:15px; padding:8px 12px;
                       border:1px solid var(--line); border-radius:6px;
                       background:white; min-width:300px; }
.strategy-desc { color:var(--muted); font-size:13px; }
.metric-label { display:block; color:var(--muted); font-size:11px;
                text-transform:uppercase; letter-spacing:.06em; }
.metric-value { font-size:20px; font-weight:700;
                font-variant-numeric:tabular-nums; margin-top:4px; }
.metric-hint { color:var(--muted); font-size:12px; margin-top:2px; }
.pos { color:var(--ok); } .neg { color:var(--neg); }
.table-scroll { overflow-x:auto; }
table.data-table, table.kv { width:100%; border-collapse:collapse;
       font-size:13px; font-variant-numeric:tabular-nums; }
table.data-table th { text-align:left; padding:7px 9px;
       border-bottom:2px solid var(--line); color:var(--muted);
       font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
table.data-table td, table.kv td { padding:7px 9px;
       border-bottom:1px solid var(--line); vertical-align:top; }
table.kv td:first-child { color:var(--muted); width:40%; }
.r { text-align:right; }
.chart-card { display:flex; flex-direction:column; }
.chart-canvas-wrap { position:relative; height:260px; width:100%; }
.chart-canvas-wrap canvas { width:100% !important; height:100% !important; }
.empty { color:var(--muted); font-size:13px; padding:10px 0; }
code { background:var(--soft); padding:1px 5px; border-radius:3px;
       font-size:12px; }
footer { text-align:center; color:var(--muted); font-size:12px;
         margin-top:28px; }
.pill { display:inline-block; padding:3px 9px; border-radius:999px;
        font-size:11px; font-weight:700; background:var(--soft);
        color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
.pill.ok { background:#e6f4ea; color:var(--ok); }
.pill.warn { background:#fff4e0; color:var(--warn); }
.pill.neg { background:#fce4ec; color:var(--neg); }
"""


_SCRIPT = r"""
<script>
(function () {
  const allData = window.DRYRUN_DATA || {};
  const allConfig = window.DRYRUN_CONFIG || {};
  const allMeta = window.DRYRUN_META || {};
  const optionsBT = window.OPTIONS_BT || {};
  let cumChart = null, dailyChart = null, rejectChart = null;

  // ── Mode switching (Intraday / Options) ────────────────────
  function onModeChange() {
    const mode = document.getElementById('mode-select').value;
    const intradaySections = [
      'strategy-select', 'config-section', 'stats-cards',
    ];
    const optSection = document.getElementById('options-backtest-section');
    // Show/hide strategy dropdown
    document.getElementById('strategy-select').style.display =
      mode === 'intraday' ? '' : 'none';

    // Show/hide intraday sections
    ['config-section', 'stats-cards'].forEach(function(id) {
      const el = document.getElementById(id);
      if (el) el.style.display = mode === 'intraday' ? '' : 'none';
    });
    // Show/hide all .grid sections and trade card
    document.querySelectorAll('section.grid, section.card:not(.selector-card):not(#options-backtest-section)').forEach(function(el) {
      if (el.id === 'options-backtest-section') return;
      el.style.display = mode === 'intraday' ? '' : 'none';
    });

    // Options section
    if (optSection) {
      optSection.style.display = mode === 'options' ? '' : 'none';
      if (mode === 'options') renderOptionsBT();
    }

    // Re-render intraday if switching back
    if (mode === 'intraday') {
      const sel = document.getElementById('strategy-select');
      if (sel && sel.value) render(sel.value);
    }
  }
  window.onModeChange = onModeChange;

  function renderOptionsBT() {
    const wrap = document.getElementById('options-bt-content');
    if (!wrap) return;
    const full = optionsBT['FULL'];
    if (!full) {
      wrap.innerHTML = '<div class="empty">No options backtest data. Run: <code>python scripts/trade/backtest_options.py</code></div>';
      return;
    }
    const s = full;
    const pf = s.profit_factor || 0;
    const pfClass = pf >= 1.15 ? 'ok' : (pf >= 1.0 ? 'warn' : 'neg');
    const pnlClass = (s.total_pnl || 0) >= 0 ? 'pos' : 'neg';

    let html = '<div class="grid four">';
    html += metricHTML('Total Trades', fmtInt(s.total_trades), s.date_range || '');
    html += metricHTML('Win Rate', fmtPct(s.win_rate), s.wins + 'W / ' + s.losses + 'L');
    html += metricHTML('Profit Factor', '<span class="' + pfClass + '">' + fmtPF(pf) + '</span>', 'Gate: 1.15');
    html += metricHTML('Sharpe', (s.sharpe || 0).toFixed(2), '');
    html += '</div>';

    html += '<div class="grid two"><div class="card"><h2>Summary</h2><table class="kv"><tbody>';
    const rows = [
      ['Strategy', 'Regime-Gated Directional Buying v1.0'],
      ['Net P&L', '<span class="' + pnlClass + '">' + fmtRs(s.total_pnl) + '</span>'],
      ['Total Charges', fmtRs(s.total_charges)],
      ['Gross Profit', fmtRs(s.gross_profit)],
      ['Gross Loss', fmtRs(s.gross_loss)],
      ['Avg Win', fmtRs(s.avg_win)],
      ['Avg Loss', fmtRs(s.avg_loss)],
      ['Max Drawdown', fmtRs(s.max_drawdown)],
    ];
    rows.forEach(function(r) { html += '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td></tr>'; });
    html += '</tbody></table></div>';

    // Exit reasons + regime breakdown
    html += '<div class="card"><h2>Breakdown</h2><table class="kv"><tbody>';
    const exits = s.exit_reasons || {};
    Object.keys(exits).sort().forEach(function(k) {
      const pct = s.total_trades > 0 ? (exits[k] / s.total_trades * 100).toFixed(1) : '0';
      html += '<tr><td>Exit: ' + k + '</td><td>' + exits[k] + ' (' + pct + '%)</td></tr>';
    });
    const regimes = s.regime_stats || {};
    Object.keys(regimes).sort().forEach(function(k) {
      const rs = regimes[k];
      html += '<tr><td>Regime: ' + k + '</td><td>' + rs.trades + ' trades, PF ' + fmtPF(rs.pf) + ', WR ' + fmtPct(rs.win_rate) + '</td></tr>';
    });
    html += '</tbody></table></div></div>';

    // Strategy params
    const params = s.params || {};
    html += '<div class="card"><h2>Strategy Configuration</h2><table class="kv"><tbody>';
    html += '<tr><td><code>OPTIONS_SL_PCT_OF_PREMIUM</code></td><td>' + (params.sl_pct || 30) + '%</td></tr>';
    html += '<tr><td><code>OPTIONS_TARGET_PCT_OF_PREMIUM</code></td><td>' + (params.target_pct || 75) + '%</td></tr>';
    html += '<tr><td><code>OPTIONS_MIN_DTE</code></td><td>' + (params.dte || 5) + '</td></tr>';
    html += '<tr><td><code>CAPITAL_PER_TRADE</code></td><td>Rs.' + (params.capital_per_trade || 10000).toLocaleString('en-IN') + '</td></tr>';
    html += '<tr><td><code>LOT_SIZE</code></td><td>' + (params.lot_size || 25) + '</td></tr>';
    html += '<tr><td><code>SKIP_REGIMES</code></td><td>' + JSON.stringify(params.skip_regimes || ['RANGE']) + '</td></tr>';
    html += '</tbody></table></div>';

    // Verdict
    const verdict = pf >= 1.15 ? 'PASS' : (pf >= 1.0 ? 'MARGINAL' : 'FAIL');
    const verdictClass = pf >= 1.15 ? 'ok' : (pf >= 1.0 ? 'warn' : 'neg');
    html += '<div class="card"><span class="pill ' + verdictClass + '">' + verdict + '</span> ';
    html += 'PF ' + fmtPF(pf) + ' — ';
    if (pf >= 1.15) html += 'Strategy passes gate. Proceed to dry-run.';
    else if (pf >= 1.0) html += 'Positive but below 1.15 gate. Needs improvement.';
    else html += 'Strategy loses money after costs. Directional option buying with simple gap signal does not overcome theta + Indian charges. Need better signal or pivot to selling strategies.';
    html += '</div>';

    wrap.innerHTML = html;
  }

  function fmtRs(v) {
    if (v == null) return '-';
    const n = parseFloat(v);
    const sign = n >= 0 ? '+' : '-';
    return sign + '₹' + Math.abs(n).toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2});
  }
  function fmtPct(v) { return v == null ? '-' : v.toFixed(1) + '%'; }
  function fmtPF(v) {
    if (v == null) return '-';
    if (v === Infinity) return '∞';
    return v.toFixed(2);
  }
  function fmtInt(v) { return v == null ? '-' : v.toLocaleString('en-IN'); }
  function pnClass(v) { return v > 0 ? 'pos' : (v < 0 ? 'neg' : ''); }

  function pillClass(v) {
    if (v > 0) return 'ok';
    if (v < 0) return 'neg';
    return '';
  }

  function metricHTML(label, value, hint) {
    const h = hint ? '<div class="metric-hint">' + hint + '</div>' : '';
    return '<div class="metric"><div class="metric-label">' + label +
           '</div><div class="metric-value">' + value + '</div>' + h + '</div>';
  }

  function onStrategyChange() {
    const sel = document.getElementById('strategy-select').value;
    render(sel);
  }
  window.onStrategyChange = onStrategyChange;

  function render(strategyType) {
    const d = allData[strategyType];
    const meta = allMeta[strategyType] || {};
    const cfg = allConfig[strategyType] || [];

    // Description
    document.getElementById('strategy-desc').textContent = meta.desc || '';

    // Config table
    const cfgWrap = document.getElementById('config-table-wrap');
    if (cfg.length === 0) {
      cfgWrap.innerHTML = '<div class="empty">No strategy-specific config keys defined.</div>';
    } else {
      let tbl = '<table class="kv"><tbody>';
      cfg.forEach(function(c) {
        tbl += '<tr><td><code>' + c.key + '</code></td><td><strong>' + c.value + '</strong></td></tr>';
      });
      tbl += '</tbody></table>';
      cfgWrap.innerHTML = tbl;
    }

    if (!d) {
      document.getElementById('stats-cards').innerHTML =
        '<div class="card empty" style="grid-column:1/-1">No data for this strategy yet.</div>';
      document.getElementById('detail-table').querySelector('tbody').innerHTML = '';
      document.getElementById('funnel-table').querySelector('tbody').innerHTML = '';
      document.getElementById('trade-tbody').innerHTML =
        '<tr><td colspan="9" class="empty">No trades yet.</td></tr>';
      destroyCharts();
      return;
    }

    const s = d.stats;

    // Stats cards
    const pnl = s.net_pnl || 0;
    const cards = [
      metricHTML('Closed Trades', fmtInt(s.closed_trades),
                 s.sessions + ' session' + (s.sessions !== 1 ? 's' : '')),
      metricHTML('Win Rate', fmtPct(s.win_rate),
                 s.wins + 'W / ' + s.losses + 'L' + (s.breakeven ? ' / ' + s.breakeven + 'BE' : '')),
      metricHTML('Net P&L',
                 '<span class="' + pnClass(pnl) + '">' + fmtRs(pnl) + '</span>',
                 'PF ' + fmtPF(s.profit_factor)),
      metricHTML('Expectancy', fmtRs(s.expectancy),
                 'per trade average'),
    ];
    document.getElementById('stats-cards').innerHTML = cards.join('');

    // Detail table
    const detailRows = [
      ['Date range', (s.first_date || '-') + ' → ' + (s.latest_date || '-')],
      ['Avg win', fmtRs(s.avg_win)],
      ['Avg loss', fmtRs(s.avg_loss)],
      ['Max win', fmtRs(s.max_win)],
      ['Max loss', fmtRs(s.max_loss)],
      ['Best day', fmtRs(s.best_day)],
      ['Worst day', fmtRs(s.worst_day)],
      ['Gross P&L', fmtRs(s.gross_pnl)],
      ['Config hashes', (s.config_hashes || []).join(', ') || '-'],
    ];
    let dtHtml = '';
    detailRows.forEach(function(r) {
      dtHtml += '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td></tr>';
    });
    document.getElementById('detail-table').querySelector('tbody').innerHTML = dtHtml;

    // Funnel table
    const sc = s.status_counts || {};
    const funnelRows = [
      ['Total candidates scanned', fmtInt(s.total_candidates)],
      ['SCORED', fmtInt(sc['SCORED'] || 0)],
      ['ENTERED', fmtInt(sc['ENTERED'] || 0)],
      ['REJECTED', fmtInt(sc['REJECTED'] || 0)],
      ['Closed trades', fmtInt(s.closed_trades)],
      ['BUY side', fmtInt((s.side_counts || {})['BUY'] || 0)],
      ['SELL side', fmtInt((s.side_counts || {})['SELL'] || 0)],
    ];
    let fnHtml = '';
    funnelRows.forEach(function(r) {
      fnHtml += '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td></tr>';
    });
    document.getElementById('funnel-table').querySelector('tbody').innerHTML = fnHtml;

    // Charts
    destroyCharts();
    drawCumChart(d.daily);
    drawDailyChart(d.daily);
    drawRejectChart(s.rejections || {});

    // Trade table
    const trades = d.trades || [];
    const tbody = document.getElementById('trade-tbody');
    if (trades.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" class="empty">No trades yet.</td></tr>';
    } else {
      let html = '';
      // Show newest first
      const sorted = trades.slice().reverse();
      sorted.forEach(function(t) {
        const pnlVal = parseFloat(t.pnl || 0);
        html += '<tr>' +
          '<td>' + (t.date || '-') + '</td>' +
          '<td>' + (t.symbol || '-') + '</td>' +
          '<td>' + (t.side || '-') + '</td>' +
          '<td class="r">' + (t.entry_price ? '₹' + parseFloat(t.entry_price).toFixed(2) : '-') + '</td>' +
          '<td class="r">' + (t.exit_price ? '₹' + parseFloat(t.exit_price).toFixed(2) : '-') + '</td>' +
          '<td>' + (t.entry_time || '-').substring(11, 19) + '</td>' +
          '<td>' + (t.exit_time || '-').substring(11, 19) + '</td>' +
          '<td>' + (t.exit_reason || '-') + '</td>' +
          '<td class="r ' + pnClass(pnlVal) + '">' + fmtRs(pnlVal) + '</td>' +
          '</tr>';
      });
      tbody.innerHTML = html;
    }

    // Rejection table
    const rejections = s.rejections || {};
    const rejectWrap = document.getElementById('reject-table-wrap');
    const rKeys = Object.keys(rejections).sort(function(a,b) { return rejections[b] - rejections[a]; });
    if (rKeys.length === 0) {
      rejectWrap.innerHTML = '<div class="empty">No rejections recorded.</div>';
    } else {
      let rtbl = '<table class="data-table"><thead><tr><th>Gate</th><th class="r">Count</th><th class="r">%</th></tr></thead><tbody>';
      const total = rKeys.reduce(function(s,k) { return s + rejections[k]; }, 0);
      rKeys.forEach(function(k) {
        const cnt = rejections[k];
        const pct = total > 0 ? (cnt / total * 100).toFixed(1) : '0.0';
        rtbl += '<tr><td><code>' + k + '</code></td><td class="r">' + cnt + '</td><td class="r">' + pct + '%</td></tr>';
      });
      rtbl += '</tbody></table>';
      rejectWrap.innerHTML = rtbl;
    }
  }

  function destroyCharts() {
    if (cumChart) { cumChart.destroy(); cumChart = null; }
    if (dailyChart) { dailyChart.destroy(); dailyChart = null; }
    if (rejectChart) { rejectChart.destroy(); rejectChart = null; }
  }

  function drawCumChart(daily) {
    const el = document.getElementById('cum-chart');
    if (!daily || !daily.length) {
      const ctx = el.getContext('2d');
      ctx.clearRect(0, 0, el.width, el.height);
      ctx.font = '13px -apple-system, Segoe UI, sans-serif';
      ctx.fillStyle = '#6a7280';
      ctx.fillText('No data yet', 16, 34);
      return;
    }
    cumChart = new Chart(el, {
      type: 'line',
      data: {
        labels: daily.map(function(r) { return r.date; }),
        datasets: [{
          label: 'Cumulative P&L',
          data: daily.map(function(r) { return r.cum; }),
          borderColor: '#1c4ed8',
          backgroundColor: 'rgba(28,78,216,0.08)',
          fill: true, tension: 0.2, pointRadius: 3
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { ticks: { callback: function(v) { return (v < 0 ? '-' : '') + '₹' + Math.abs(v).toLocaleString('en-IN'); } } }
        }
      }
    });
  }

  function drawDailyChart(daily) {
    const el = document.getElementById('daily-chart');
    if (!daily || !daily.length) {
      const ctx = el.getContext('2d');
      ctx.clearRect(0, 0, el.width, el.height);
      ctx.font = '13px -apple-system, Segoe UI, sans-serif';
      ctx.fillStyle = '#6a7280';
      ctx.fillText('No data yet', 16, 34);
      return;
    }
    dailyChart = new Chart(el, {
      type: 'bar',
      data: {
        labels: daily.map(function(r) { return r.date; }),
        datasets: [{
          label: 'Daily P&L',
          data: daily.map(function(r) { return r.net; }),
          backgroundColor: daily.map(function(r) {
            return r.net >= 0 ? 'rgba(27,142,58,0.7)' : 'rgba(198,40,40,0.7)';
          }),
          borderRadius: 3
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { ticks: { callback: function(v) { return (v < 0 ? '-' : '') + '₹' + Math.abs(v).toLocaleString('en-IN'); } } }
        }
      }
    });
  }

  function drawRejectChart(rejections) {
    const el = document.getElementById('reject-chart');
    const keys = Object.keys(rejections);
    if (!keys.length) {
      const ctx = el.getContext('2d');
      ctx.clearRect(0, 0, el.width, el.height);
      ctx.font = '13px -apple-system, Segoe UI, sans-serif';
      ctx.fillStyle = '#6a7280';
      ctx.fillText('No rejections', 16, 34);
      return;
    }
    const sorted = keys.sort(function(a,b) { return rejections[b] - rejections[a]; }).slice(0, 8);
    const colors = ['#1c4ed8','#c62828','#b06a00','#1b8e3a','#7c3aed','#0891b2','#db2777','#6b7280'];
    rejectChart = new Chart(el, {
      type: 'doughnut',
      data: {
        labels: sorted,
        datasets: [{
          data: sorted.map(function(k) { return rejections[k]; }),
          backgroundColor: colors.slice(0, sorted.length)
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'right', labels: { font: { size: 11 } } } }
      }
    });
  }

  // Initial render with first strategy
  const sel = document.getElementById('strategy-select');
  if (sel && sel.value) render(sel.value);
})();
</script>
"""


__all__ = ["render_dryrun_page"]
