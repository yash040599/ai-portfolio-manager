"""Chan framework dashboard page.

Read-only status surface for the intraday Chan reset. The page is
artifact-driven: every render reads the current config, backtest-data
manifest/SQLite, replay JSON reports, dry-run analysis DB, and daily
evidence files. That makes EOD trade-review output visible on refresh
without a separate dashboard export step.
"""

from __future__ import annotations

import glob
import html
import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from config import Config, now_ist
from modes.dashboard.nav import render_topnav


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS_BACKTEST = PROJECT_ROOT / "reports" / "backtest"
REPORTS_TRADING = PROJECT_ROOT / "reports" / "trading"
DEFAULT_BACKTEST_DATA_ROOT = PROJECT_ROOT.parent / "ai-portfolio-backtest-data"


def _backtest_root() -> Path:
    raw = os.getenv("BACKTEST_DATA_PATH", "").strip()
    root = Path(raw) if raw else DEFAULT_BACKTEST_DATA_ROOT
    if not root.is_absolute():
        root = (PROJECT_ROOT / root).resolve()
    return root


def _rel(path: str | Path) -> str:
    p = Path(path)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return os.path.relpath(p, PROJECT_ROOT).replace(os.sep, "/")
    except ValueError:
        return str(p)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


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


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


def _fmt_rs(value: Any, *, signed: bool = True) -> str:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return "-"
    if signed:
        sign = "+" if number >= 0 else "-"
        return f"Rs.{sign}{abs(number):,.2f}"
    return f"Rs.{number:,.2f}"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "-"


def _pf_label(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return "inf" if float(value) == float("inf") else f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def _profit_factor(values: list[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v <= 0)
    if losses <= 0:
        return float("inf") if wins > 0 else None
    return wins / losses


def _daily_net_from_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, float] = defaultdict(float)
    for trade in trades:
        ts = str(trade.get("entry_ts") or trade.get("date") or "")
        day = ts[:10]
        if not day:
            continue
        try:
            by_day[day] += float(trade.get("net_pnl_inr", 0) or 0)
        except (TypeError, ValueError):
            pass

    cumulative = 0.0
    out = []
    for day in sorted(by_day):
        net = round(by_day[day], 2)
        cumulative = round(cumulative + net, 2)
        out.append({"date": day, "net": net, "cum": cumulative})
    return out


def _data_manifest() -> dict[str, Any]:
    root = _backtest_root()
    manifest = _load_json(root / "manifest.json")
    intervals = {
        str(item.get("name")): item
        for item in manifest.get("intervals", [])
        if isinstance(item, dict)
    }
    intraday = intervals.get("15minute", {})
    daily = intervals.get("day", {})
    symbols: list[str] = []
    db_path = root / str(intraday.get("file") or "candles/intraday_15m.sqlite")
    conn = _connect_existing(db_path)
    try:
        if conn is not None:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM candles WHERE interval='15minute' "
                "ORDER BY symbol LIMIT 12"
            ).fetchall()
            symbols = [str(row[0]) for row in rows]
    except sqlite3.Error:
        symbols = []
    finally:
        if conn is not None:
            conn.close()

    return {
        "root": root,
        "manifest_path": root / "manifest.json",
        "dataset_version": manifest.get("dataset_version") or "-",
        "status": manifest.get("status") or "missing",
        "intraday": intraday,
        "daily": daily,
        "notes": manifest.get("notes") or [],
        "sample_symbols": symbols,
    }


def _report_scope(path: Path, data: dict[str, Any]) -> str:
    name = path.name.upper()
    if "_ALL_" in name:
        return "ALL"
    symbols = {
        str(t.get("symbol", "")).upper()
        for t in data.get("trades", [])[:50]
        if isinstance(t, dict)
    }
    symbols.discard("")
    if len(symbols) == 1:
        return next(iter(symbols))
    return "MIXED"


def _load_replay_reports() -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for raw in sorted(glob.glob(str(REPORTS_BACKTEST / "*.json"))):
        path = Path(raw)
        name = path.name.lower()
        if "live-vs-replay" in name or "dryrun-vs-replay" in name:
            continue
        data = _load_json(path)
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else None
        if not summary:
            continue
        trades = data.get("trades") if isinstance(data.get("trades"), list) else []
        reports.append({
            "path": path,
            "mtime": path.stat().st_mtime,
            "config_version": data.get("config_version") or "-",
            "config_hash": data.get("config_hash") or "-",
            "strategy_profile": data.get("strategy_profile") or "NOAI_LEGACY_FULL",
            "from": data.get("from") or "-",
            "to": data.get("to") or "-",
            "score_mode": data.get("score_mode") or "-",
            "min_score": data.get("min_score"),
            "scope": _report_scope(path, data),
            "summary": summary,
            "daily": _daily_net_from_trades(trades),
        })
    reports.sort(key=lambda item: item["mtime"], reverse=True)
    return reports


def _selected_replay(reports: list[dict[str, Any]], config_hash: str) -> dict[str, Any] | None:
    current = [r for r in reports if r["config_hash"] == config_hash and r["score_mode"] == "scanner"]
    current_all = [r for r in current if r["scope"] == "ALL"]
    if current_all:
        return current_all[0]
    if current:
        return current[0]
    all_symbol = [r for r in reports if r["scope"] == "ALL" and r["score_mode"] == "scanner"]
    if all_symbol:
        return all_symbol[0]
    return reports[0] if reports else None


def _comparison_reports() -> list[dict[str, Any]]:
    out = []
    for raw in sorted(glob.glob(str(REPORTS_BACKTEST / "*vs-replay*.json"))):
        path = Path(raw)
        data = _load_json(path)
        if not data:
            continue
        replay = data.get("replay", {}) if isinstance(data.get("replay"), dict) else {}
        live = data.get("live", {}) if isinstance(data.get("live"), dict) else {}
        out.append({
            "path": path,
            "mtime": path.stat().st_mtime,
            "status": data.get("status") or "-",
            "data_source": data.get("data_source") or "-",
            "config_hash": replay.get("config_hash") or "-",
            "candidate_rows": live.get("candidate_rows_compared", 0),
            "logical_trade_rows": live.get("logical_trade_rows", 0),
            "tax_trade_rows": live.get("tax_trade_rows", 0),
        })
    out.sort(key=lambda item: item["mtime"], reverse=True)
    return out[:4]


def _dryrun_summary(config_hash: str) -> dict[str, Any]:
    db_path = PROJECT_ROOT / Config.TRADE_ANALYSIS_DB_PATH
    result = {
        "db_path": db_path,
        "candidate_rows": 0,
        "candidate_any_hash": 0,
        "status_counts": {},
        "side_counts": {},
        "rejections": {},
        "trade_rows": 0,
        "sessions": 0,
        "gross_pnl": 0.0,
        "charges": 0.0,
        "net_pnl": 0.0,
        "wins": 0,
        "losses": 0,
        "profit_factor": None,
        "expectancy": None,
        "daily": [],
        "latest_date": None,
    }
    conn = _connect_existing(db_path)
    if conn is None:
        return result
    try:
        if _table_exists(conn, "intraday_candidates"):
            any_rows = conn.execute("SELECT config_hash FROM intraday_candidates").fetchall()
            rows = conn.execute(
                "SELECT date, status, side, rejected_gate FROM intraday_candidates WHERE config_hash=?",
                (config_hash,),
            ).fetchall()
            result["candidate_any_hash"] = len(any_rows)
            result["candidate_rows"] = len(rows)
            result["status_counts"] = dict(Counter(str(r["status"] or "<none>") for r in rows))
            result["side_counts"] = dict(Counter(str(r["side"] or "<none>") for r in rows))
            result["rejections"] = dict(
                Counter(str(r["rejected_gate"] or "<none>") for r in rows if r["status"] == "REJECTED")
            )
        if _table_exists(conn, "dryrun_trade_ledger"):
            rows = conn.execute(
                "SELECT date, gross_pnl, total_charges, net_pnl, side, exit_reason "
                "FROM dryrun_trade_ledger WHERE config_hash=? ORDER BY date, entry_time",
                (config_hash,),
            ).fetchall()
            nets = [float(r["net_pnl"] or 0.0) for r in rows]
            result["trade_rows"] = len(rows)
            result["sessions"] = len({str(r["date"]) for r in rows})
            result["gross_pnl"] = round(sum(float(r["gross_pnl"] or 0.0) for r in rows), 2)
            result["charges"] = round(sum(float(r["total_charges"] or 0.0) for r in rows), 2)
            result["net_pnl"] = round(sum(nets), 2)
            result["wins"] = sum(1 for value in nets if value > 0)
            result["losses"] = sum(1 for value in nets if value <= 0)
            result["profit_factor"] = _profit_factor(nets)
            result["expectancy"] = round(result["net_pnl"] / len(rows), 2) if rows else None

            by_day: dict[str, float] = defaultdict(float)
            for row in rows:
                by_day[str(row["date"])] += float(row["net_pnl"] or 0.0)
            cumulative = 0.0
            daily = []
            for day in sorted(by_day):
                net = round(by_day[day], 2)
                cumulative = round(cumulative + net, 2)
                daily.append({"date": day, "net": net, "cum": cumulative})
            result["daily"] = daily
            result["latest_date"] = max(by_day) if by_day else None
    finally:
        conn.close()
    return result


def _evidence_snapshots() -> list[dict[str, Any]]:
    paths = sorted(glob.glob(str(REPORTS_TRADING / "**" / "chan_evidence_*_dryrun.json"), recursive=True))
    rows = []
    for raw in paths[-8:]:
        path = Path(raw)
        data = _load_json(path)
        if not data:
            continue
        candidates = data.get("candidates", {}) if isinstance(data.get("candidates"), dict) else {}
        outcomes = data.get("outcomes", {}) if isinstance(data.get("outcomes"), dict) else {}
        rows.append({
            "date": data.get("date") or "-",
            "status": data.get("status") or "-",
            "config_hash": data.get("config_hash") or "-",
            "candidates": candidates.get("rows_matching_hash", 0),
            "outcomes": outcomes.get("rows", 0),
            "net_pnl": outcomes.get("net_pnl_inr", 0.0),
            "flags": data.get("red_flags") or [],
            "path": path,
        })
    rows.sort(key=lambda item: item["date"], reverse=True)
    return rows


def _conclusions(selected: dict[str, Any] | None, dryrun: dict[str, Any], current_hash: str) -> list[tuple[str, str]]:
    points: list[tuple[str, str]] = []
    if selected is None:
        points.append(("Backtest", "No replay JSON is available yet. Run scanner-mode replay to populate this section."))
    else:
        summary = selected["summary"]
        if selected["config_hash"] != current_hash:
            points.append((
                "Backtest",
                "Current simple-MR profile has no full replay report yet. The visible all-symbol report is legacy blended NoAI evidence, not proof for the new baseline.",
            ))
            points.append((
                "Legacy result",
                "The old blended scanner found many raw winners, but slippage, spread, and charges turned it strongly negative after costs.",
            ))
        elif float(summary.get("net_pnl_inr") or 0) > 0 and float(summary.get("net_profit_factor") or 0) >= 1.15:
            points.append(("Backtest", "Current-profile replay is positive after costs and can move to a wider replay window."))
        else:
            points.append(("Backtest", "Current-profile replay does not pass promotion metrics yet; treat it as measurement evidence, not a live edge."))

    if dryrun["trade_rows"] == 0:
        points.append(("Dry-run", "Forward dry-run sample for the current profile is still at 0 trades. No conclusion about live expectancy is allowed yet."))
    else:
        pf = dryrun.get("profit_factor") or 0
        exp = dryrun.get("expectancy") or 0
        if pf >= 1.15 and exp >= 10:
            points.append(("Dry-run", "Forward dry-run is currently above the promotion bar, but the fixed sample must finish before any live pilot."))
        else:
            points.append(("Dry-run", "Forward dry-run is below the promotion bar so far; keep collecting before changing features."))
    points.append(("Data", "Intraday replay data is wide enough for plumbing checks, not strategy promotion: 100 symbols but only 2026-04-07 to 2026-04-24 of 15-minute candles."))
    return points


def _status_class(status: str) -> str:
    status = status.upper()
    if status in {"READY", "PASS"}:
        return "ok"
    if status in {"DATA_GAP", "REVIEW_REQUIRED"}:
        return "warn"
    return "muted"


def _pn(value: float) -> str:
    return "pos" if value > 0 else ("neg" if value < 0 else "")


def _metric(label: str, value: str, hint: str = "") -> str:
    hint_html = f'<div class="metric-hint">{html.escape(hint)}</div>' if hint else ""
    return (
        '<div class="metric">'
        f'<div class="metric-label">{html.escape(label)}</div>'
        f'<div class="metric-value">{value}</div>'
        f'{hint_html}</div>'
    )


def _progress(label: str, done: int, target: int) -> str:
    pct = 0 if target <= 0 else min(100, round(done / target * 100, 1))
    return f"""
<div class="progress-row">
  <div class="progress-head"><span>{html.escape(label)}</span><strong>{done}/{target}</strong></div>
  <div class="bar"><span style="width:{pct}%"></span></div>
</div>
"""


def _render_report_table(reports: list[dict[str, Any]], current_hash: str) -> str:
    if not reports:
        return '<div class="empty">No replay JSON reports found.</div>'
    rows = []
    for report in reports[:6]:
        summary = report["summary"]
        current = "yes" if report["config_hash"] == current_hash else "no"
        net_pnl = float(summary.get("net_pnl_inr") or 0)
        rows.append(
            "<tr>"
            f"<td>{html.escape(report['scope'])}</td>"
            f"<td>{html.escape(str(report['from']))} to {html.escape(str(report['to']))}</td>"
            f"<td>{html.escape(str(report['strategy_profile']))}</td>"
            f"<td>{html.escape(str(report['config_hash']))}</td>"
            f"<td>{current}</td>"
            f"<td class='r'>{_fmt_int(summary.get('trades'))}</td>"
            f"<td class='r'>{_fmt_pct(summary.get('net_wr_pct'))}</td>"
            f"<td class='r'>{_pf_label(summary.get('net_profit_factor'))}</td>"
            f"<td class='r {_pn(net_pnl)}'>{_fmt_rs(summary.get('net_pnl_inr'))}</td>"
            "</tr>"
        )
    return """
<div class="table-scroll"><table class="data-table">
  <thead><tr><th>Scope</th><th>Window</th><th>Profile</th><th>Hash</th><th>Current</th><th class="r">Trades</th><th class="r">Net WR</th><th class="r">Net PF</th><th class="r">Net P&amp;L</th></tr></thead>
  <tbody>""" + "".join(rows) + "</tbody></table></div>"


def _render_evidence_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="empty">No daily dry-run Chan evidence files yet.</div>'
    body = []
    for row in rows:
        flag = "; ".join(str(x) for x in row["flags"][:2])
        net_pnl = float(row["net_pnl"] or 0)
        body.append(
            "<tr>"
            f"<td>{html.escape(str(row['date']))}</td>"
            f"<td><span class='pill {_status_class(str(row['status']))}'>{html.escape(str(row['status']))}</span></td>"
            f"<td>{html.escape(str(row['config_hash']))}</td>"
            f"<td class='r'>{_fmt_int(row['candidates'])}</td>"
            f"<td class='r'>{_fmt_int(row['outcomes'])}</td>"
            f"<td class='r {_pn(net_pnl)}'>{_fmt_rs(row['net_pnl'])}</td>"
            f"<td>{html.escape(flag)}</td>"
            "</tr>"
        )
    return """
<div class="table-scroll"><table class="data-table">
  <thead><tr><th>Date</th><th>Status</th><th>Hash</th><th class="r">Candidates</th><th class="r">Outcomes</th><th class="r">Net P&amp;L</th><th>Flags</th></tr></thead>
  <tbody>""" + "".join(body) + "</tbody></table></div>"


def _render_comparison_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="empty">No live/dryrun-vs-replay comparison reports yet.</div>'
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{html.escape(str(row['data_source']))}</td>"
            f"<td><span class='pill {_status_class(str(row['status']))}'>{html.escape(str(row['status']))}</span></td>"
            f"<td>{html.escape(str(row['config_hash']))}</td>"
            f"<td class='r'>{_fmt_int(row['candidate_rows'])}</td>"
            f"<td class='r'>{_fmt_int(row['logical_trade_rows'])}</td>"
            f"<td class='r'>{_fmt_int(row['tax_trade_rows'])}</td>"
            "</tr>"
        )
    return """
<div class="table-scroll"><table class="data-table compact">
  <thead><tr><th>Source</th><th>Status</th><th>Replay hash</th><th class="r">Candidates</th><th class="r">Logical trades</th><th class="r">Tax/outcome rows</th></tr></thead>
  <tbody>""" + "".join(body) + "</tbody></table></div>"


def render_chan_page() -> str:
    version, config_hash = Config.snapshot_hash()
    profile = str(getattr(Config, "TRADE_STRATEGY_PROFILE", ""))
    manifest = _data_manifest()
    reports = _load_replay_reports()
    selected = _selected_replay(reports, config_hash)
    comparisons = _comparison_reports()
    dryrun = _dryrun_summary(config_hash)
    evidence = _evidence_snapshots()
    conclusions = _conclusions(selected, dryrun, config_hash)

    intraday = manifest["intraday"]
    daily = manifest["daily"]
    selected_summary = selected["summary"] if selected else {}
    selected_current = bool(selected and selected["config_hash"] == config_hash)
    selected_label = "Current profile replay" if selected_current else "Latest available all-symbol replay"
    chart_payload = {
        "backtestDaily": selected["daily"] if selected else [],
        "dryrunDaily": dryrun["daily"],
    }
    chart_json = json.dumps(chart_payload, allow_nan=False).replace("</", "<\\/")

    session_target = 5
    trade_target = 30
    dryrun_status = "READY" if dryrun["trade_rows"] >= trade_target and dryrun["sessions"] >= session_target else "COLLECTING"
    if dryrun["trade_rows"] == 0:
        dryrun_status = "DATA_GAP"

    conclusion_rows = "".join(
        f"<li><strong>{html.escape(title)}:</strong> {html.escape(text)}</li>"
        for title, text in conclusions
    )
    samples = ", ".join(manifest["sample_symbols"][:8]) or "-"
    latest_evidence = evidence[0] if evidence else None
    latest_evidence_text = (
        f"{latest_evidence['date']} / {latest_evidence['status']}"
        if latest_evidence else "No evidence file yet"
    )
    selected_net = float(selected_summary.get("net_pnl_inr") or 0)

    body = f"""
<h1 class="page-title">Chan Framework Status</h1>
<div class="sub">Stage 1.7 research dashboard for intraday trade mode. Generated {html.escape(now_ist().strftime('%Y-%m-%d %H:%M IST'))}.</div>

<section class="status-strip">
  <div><span>Stage</span><strong>{html.escape(str(getattr(Config, 'TRADE_STAGE_NAME', '-')))}</strong></div>
  <div><span>Profile</span><strong>{html.escape(profile)}</strong></div>
  <div><span>Config</span><strong>{html.escape(version)} / {html.escape(config_hash)}</strong></div>
  <div><span>Live trading</span><strong>{'Paused' if getattr(Config, 'TRADE_LIVE_TRADING_PAUSED', False) else 'Enabled'}</strong></div>
</section>

<section class="grid two">
  <div class="card">
    <h2>Current Decision</h2>
    <p class="lead">Production base strategy: none. Forward dry-run base: <code>{html.escape(profile)}</code>.</p>
    <ul class="plain">{conclusion_rows}</ul>
  </div>
  <div class="card">
    <h2>L0 Dry-Run Progress</h2>
    <div class="pill {_status_class(dryrun_status)}">{html.escape(dryrun_status)}</div>
    {_progress('Sessions', int(dryrun['sessions']), session_target)}
    {_progress('Closed simulated trades', int(dryrun['trade_rows']), trade_target)}
    <div class="muted small">Sparse-sample fallback remains 10 sessions if fewer than 30 trades close in the first 5 sessions.</div>
  </div>
</section>

<section class="grid four">
  {_metric('Dry-run candidates', _fmt_int(dryrun['candidate_rows']), f"current hash; any hash {dryrun['candidate_any_hash']}")}
  {_metric('Dry-run trades', _fmt_int(dryrun['trade_rows']), f"latest evidence: {latest_evidence_text}")}
  {_metric('Dry-run net P&L', f"<span class='{_pn(float(dryrun['net_pnl']))}'>{_fmt_rs(dryrun['net_pnl'])}</span>", f"PF {_pf_label(dryrun['profit_factor'])}, expectancy {_fmt_rs(dryrun['expectancy']) if dryrun['expectancy'] is not None else '-'}")}
  {_metric('Selected replay net P&L', f"<span class='{_pn(selected_net)}'>{_fmt_rs(selected_summary.get('net_pnl_inr'))}</span>", selected_label)}
</section>

<section class="card">
  <h2>Backtest Data We Have</h2>
  <div class="grid two compact-grid">
    <table class="kv"><tbody>
      <tr><td>Data root</td><td>{html.escape(_rel(manifest['root']))}</td></tr>
      <tr><td>Dataset version</td><td>{html.escape(str(manifest['dataset_version']))}</td></tr>
      <tr><td>Intraday 15m rows</td><td>{_fmt_int(intraday.get('rows'))}</td></tr>
      <tr><td>Intraday symbols</td><td>{_fmt_int(intraday.get('symbols'))}</td></tr>
      <tr><td>Intraday range</td><td>{html.escape(str((intraday.get('date_range') or {}).get('start', '-')))} to {html.escape(str((intraday.get('date_range') or {}).get('end', '-')))}</td></tr>
    </tbody></table>
    <table class="kv"><tbody>
      <tr><td>Daily rows</td><td>{_fmt_int(daily.get('rows'))}</td></tr>
      <tr><td>Daily symbols</td><td>{_fmt_int(daily.get('symbols'))}</td></tr>
      <tr><td>Daily range</td><td>{html.escape(str((daily.get('date_range') or {}).get('start', '-')))} to {html.escape(str((daily.get('date_range') or {}).get('end', '-')))}</td></tr>
      <tr><td>Sample symbols</td><td>{html.escape(samples)}</td></tr>
      <tr><td>Promotion caveat</td><td>15m seed is short; use for plumbing until a wider dataset lands.</td></tr>
    </tbody></table>
  </div>
  <div class="note"><strong>Why RELIANCE appears:</strong> RELIANCE is not the only backtest data. It appears because several validation commands intentionally used <code>--symbol RELIANCE</code> as a quick single-symbol smoke test. The all-symbol replay files and the SQLite dataset cover the broader NIFTY cache.</div>
</section>

<section class="grid two">
  <div class="card chart-card">
    <h2>Replay Cumulative Net P&amp;L</h2>
    <div class="chart-canvas-wrap"><canvas id="backtest-chart"></canvas></div>
  </div>
  <div class="card chart-card">
    <h2>Dry-Run Cumulative Net P&amp;L</h2>
    <div class="chart-canvas-wrap"><canvas id="dryrun-chart"></canvas></div>
  </div>
</section>

<section class="card">
  <h2>Replay Reports</h2>
  {_render_report_table(reports, config_hash)}
</section>

<section class="grid two">
  <div class="card">
    <h2>Live / Dry-Run vs Replay</h2>
    {_render_comparison_table(comparisons)}
  </div>
  <div class="card">
    <h2>Daily Dry-Run Evidence</h2>
    {_render_evidence_table(evidence)}
  </div>
</section>

<section class="card">
  <h2>Daily Update Path</h2>
  <table class="kv"><tbody>
    <tr><td>Dry-run candidate rows</td><td><code>{html.escape(_rel(Config.TRADE_ANALYSIS_DB_PATH))}::intraday_candidates</code></td></tr>
    <tr><td>Dry-run outcome rows</td><td><code>{html.escape(_rel(Config.TRADE_ANALYSIS_DB_PATH))}::dryrun_trade_ledger</code></td></tr>
    <tr><td>EOD evidence files</td><td><code>reports/trading/YYYY/MM/chan_evidence_DD_dryrun.*</code></td></tr>
    <tr><td>Actual dashboard/tax P&amp;L</td><td><code>data/trades.db::intraday_tax_ledger</code> only; dry-run is excluded.</td></tr>
  </tbody></table>
</section>
<script>window.CHAN_DATA = {chart_json};</script>
{_SCRIPT}
"""
    return _wrap("Chan Framework", body)


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
  {_topnav('/chan')}
  {body}
  <footer>AI Portfolio Manager - read-only Chan framework dashboard</footer>
</div>
{error_toast_script()}
</body>
</html>"""


def _topnav(here: str) -> str:
    return render_topnav(here)


_STYLE = r"""
:root { --bg:#f7f8fa; --fg:#1c1f23; --muted:#6a7280; --card:#ffffff;
        --line:#e5e7eb; --ok:#1b8e3a; --warn:#b06a00; --neg:#c62828;
        --blue:#1c4ed8; --soft:#f0f1f3; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background:var(--bg); color:var(--fg); margin:0; padding:24px; line-height:1.5; }
.wrap { max-width:1180px; margin:0 auto; }
nav.topnav { display:flex; gap:14px; align-items:center; padding:10px 16px; background:var(--card); border:1px solid var(--line); border-radius:8px; margin-bottom:18px; font-size:14px; flex-wrap:wrap; }
nav.topnav a,
nav.topnav button.nav-back { color:var(--fg); text-decoration:none; font-weight:500; }
nav.topnav a:hover { text-decoration:underline; }
nav.topnav button.nav-back { font:inherit; padding:4px 9px; border:1px solid var(--line); border-radius:5px; background:white; cursor:pointer; }
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
.card, .metric, .status-strip { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:18px 20px; }
.card h2 { margin:0 0 10px; font-size:17px; }
.lead { margin-top:0; }
.plain { margin:10px 0 0 18px; padding:0; font-size:13.5px; }
.plain li { margin:5px 0; }
.status-strip { display:grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap:12px; margin-bottom:16px; }
.status-strip span, .metric-label { display:block; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.06em; }
.status-strip strong { display:block; font-size:13px; margin-top:4px; overflow-wrap:anywhere; }
.metric-value { font-size:20px; font-weight:700; font-variant-numeric:tabular-nums; margin-top:4px; }
.metric-hint, .muted, .small { color:var(--muted); font-size:12px; }
.pos { color:var(--ok); } .neg { color:var(--neg); }
.pill { display:inline-block; padding:3px 9px; border-radius:999px; font-size:11px; font-weight:700; background:var(--soft); color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
.pill.ok { background:#e6f4ea; color:var(--ok); }
.pill.warn { background:#fff4e0; color:var(--warn); }
.progress-row { margin:12px 0; }
.progress-head { display:flex; justify-content:space-between; font-size:13px; margin-bottom:5px; }
.bar { height:9px; background:#eef0f3; border-radius:999px; overflow:hidden; }
.bar span { display:block; height:100%; background:var(--blue); }
.table-scroll { overflow-x:auto; }
table.data-table, table.kv { width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }
table.data-table th { text-align:left; padding:7px 9px; border-bottom:2px solid var(--line); color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
table.data-table td, table.kv td { padding:7px 9px; border-bottom:1px solid var(--line); vertical-align:top; }
table.kv td:first-child { color:var(--muted); width:36%; }
.r { text-align:right; }
.note { margin-top:12px; padding:10px 12px; background:#eef4ff; border-left:3px solid var(--blue); border-radius:0 4px 4px 0; font-size:13px; }
.empty { color:var(--muted); font-size:13px; padding:10px 0; }
/* Cumulative-P&L chart: Chart.js with responsive:true + maintainAspectRatio:false
   needs a parent with fixed pixel height; otherwise canvas grows unbounded. */
.chart-card { display:flex; flex-direction:column; }
.chart-canvas-wrap { position:relative; height:260px; width:100%; }
.chart-canvas-wrap canvas { width:100% !important; height:100% !important; }
code { background:var(--soft); padding:1px 5px; border-radius:3px; font-size:12px; }
footer { text-align:center; color:var(--muted); font-size:12px; margin-top:28px; }
"""


_SCRIPT = r"""
<script>
(function () {
  const data = window.CHAN_DATA || {backtestDaily: [], dryrunDaily: []};
  function emptyChart(canvasId, label) {
    const el = document.getElementById(canvasId);
    const ctx = el.getContext('2d');
    ctx.clearRect(0, 0, el.width, el.height);
    ctx.font = '13px -apple-system, Segoe UI, sans-serif';
    ctx.fillStyle = '#6a7280';
    ctx.fillText(label, 16, 34);
  }
  function drawLine(canvasId, rows, label) {
    if (!rows || !rows.length) { emptyChart(canvasId, 'No data yet'); return; }
    new Chart(document.getElementById(canvasId), {
      type: 'line',
      data: {
        labels: rows.map(r => r.date),
        datasets: [{
          label: label,
          data: rows.map(r => r.cum),
          borderColor: '#1c4ed8',
          backgroundColor: 'rgba(28,78,216,0.08)',
          fill: true,
          tension: 0.2,
          pointRadius: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { ticks: { callback: v => (v < 0 ? '-' : '') + 'Rs.' + Math.abs(v).toLocaleString('en-IN') } } }
      }
    });
  }
  drawLine('backtest-chart', data.backtestDaily, 'Replay cumulative net P&L');
  drawLine('dryrun-chart', data.dryrunDaily, 'Dry-run cumulative net P&L');
})();
</script>
"""


__all__ = ["render_chan_page"]