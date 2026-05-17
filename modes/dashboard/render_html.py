"""Interactive HTML dashboard renderer.

Re-built around a JSON-driven SPA (single page, no router): the Python
side serves an HTML shell once, then a JSON endpoint feeds the panel
data on every date-range / granularity change. Charts rendered
client-side with Chart.js (CDN). Zero new Python deps.

For non-server use the same HTML can still be saved to disk and the
JSON inlined as a `<script>` payload — keeps `--no-open` snapshots
working without a running server.
"""

from __future__ import annotations

import datetime
import json
import webbrowser
from pathlib import Path

from config import Config
from modes.dashboard.nav import render_topnav, topnav_css
from modes.dashboard.metrics import HeadlinePnL
from modes.dashboard.verdict import VerdictResult


_VERDICT_COLOURS = {
    "GREEN": ("#1b8e3a", "#e6f4ea"),
    "AMBER": ("#b06a00", "#fff4e0"),
    "RED":   ("#c62828", "#fdecec"),
    "GREY":  ("#555555", "#f0f0f0"),
}


def build_payload(
    *,
    date_from: str,
    date_to: str,
    granularity: str,
    headline: HeadlinePnL,
    verdict: VerdictResult,
    budget: float,
    verified_day_count: int,
    pending_dates: list[str],
    bucketed: list[tuple[str, float, int]],
    cumulative: list[tuple[str, float]],
    include_provisional: bool,
    strategy_boundaries: list[dict] | None = None,
    strategy_overlay_enabled: bool = True,
) -> dict:
    """Shape the JSON the page consumes.

    Kept separate from `render_shell` so the server can call only this
    on date-range changes (no need to re-emit the HTML / CSS / JS).
    """
    fg, bg = _VERDICT_COLOURS.get(verdict.verdict, _VERDICT_COLOURS["GREY"])
    pct = (headline.net_pnl / budget) if budget > 0 else None
    return {
        "window": {
            "from": date_from,
            "to":   date_to,
            "granularity": granularity,
            "trading_days": headline.trading_days,
            "verified_days": verified_day_count,
            "pending_dates": pending_dates,
            "include_provisional": include_provisional,
        },
        "verdict": {
            "level":              verdict.verdict,
            "headline":           verdict.headline,
            "rationale":          verdict.rationale,
            "current_budget":     verdict.current_budget,
            "recommended_budget": verdict.recommended_budget,
            "failed":             verdict.failed_thresholds,
            "fg":                 fg,
            "bg":                 bg,
        },
        "headline": {
            "trade_count":   headline.trade_count,
            "trading_days":  headline.trading_days,
            "gross_pnl":     headline.gross_pnl,
            "total_charges": headline.total_charges,
            "net_pnl":       headline.net_pnl,
            "net_pct":       pct,
            "budget_avg":    budget,
            "best_day":      list(headline.best_day)  if headline.best_day  else None,
            "worst_day":     list(headline.worst_day) if headline.worst_day else None,
        },
        "charts": {
            "bucketed":   [{"label": l, "net_pnl": p, "trades": n} for l, p, n in bucketed],
            "cumulative": [{"date":  d, "cum":     v}             for d, v    in cumulative],
        },
        "strategy_overlay": {
            "enabled":    bool(strategy_overlay_enabled),
            "boundaries": list(strategy_boundaries or []),
        },
        "research_phase": {
          "stage": str(getattr(Config, "TRADE_RESEARCH_STAGE", "") or ""),
          "label": str(getattr(Config, "TRADE_RESEARCH_PHASE_LABEL", "") or ""),
          "note": str(getattr(Config, "TRADE_RESEARCH_PHASE_NOTE", "") or ""),
          "live_trading_paused": bool(getattr(Config, "TRADE_LIVE_TRADING_PAUSED", False)),
        },
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M IST"),
    }


def render_shell(initial_payload: dict, *, server_mode: bool) -> str:
    """Build the HTML document.

    `server_mode=True`  -> page fetches `/api/data` on filter changes.
    `server_mode=False` -> page is fully static (no fetch); date-range
                           changes prompt the user to relaunch with
                           `--serve`. Granularity still works because
                           the embedded daily series is re-bucketed
                           client-side.
    """
    initial_json = json.dumps(initial_payload).replace("</", "<\\/")
    server_flag  = "true" if server_mode else "false"
    return _SHELL_TEMPLATE.replace("__SERVER_FLAG__", server_flag) \
                          .replace("__INITIAL_JSON__", initial_json) \
                          .replace("__TOPNAV_CSS__", topnav_css()) \
                          .replace("__TOPNAV__", render_topnav("/trading"))


def write_and_maybe_open(html_str: str, *, date_to: str,
                         open_browser: bool = True) -> Path:
    """Persist the HTML and (optionally) open it in the default browser."""
    # Three .parent hops: render_html.py -> dashboard/ -> modes/ -> root.
    project_root = Path(__file__).resolve().parent.parent.parent
    out_dir = project_root / "reports" / "dashboard"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"dashboard_{date_to}.html"
    out_path.write_text(html_str, encoding="utf-8")
    if open_browser:
        try:
            webbrowser.open(out_path.as_uri())
        except Exception:
            pass
    return out_path


# Plain-string template (no f-string) so JS braces don't need doubling.
_SHELL_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Portfolio Manager — Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #fafbfc; --fg: #1c1f23; --muted: #6a7280;
    --card: #ffffff; --line: #e5e7eb; --soft: #f0f1f3;
  }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         background: var(--bg); color: var(--fg); margin: 0; padding: 24px; }
  .wrap { max-width: 1080px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em;
       color: var(--muted); margin: 28px 0 8px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 16px; }
  .card { background: var(--card); border: 1px solid var(--line);
          border-radius: 8px; padding: 18px 20px; }
  .controls { display: flex; gap: 14px; flex-wrap: wrap; align-items: end;
              margin-bottom: 18px; padding: 14px 18px;
              background: var(--card); border: 1px solid var(--line); border-radius: 8px; }
  .controls label { display: block; font-size: 11px; color: var(--muted);
                    text-transform: uppercase; letter-spacing: 0.05em;
                    margin-bottom: 4px; }
  .controls input, .controls select, .controls button {
    font: inherit; padding: 6px 10px; border: 1px solid var(--line);
    border-radius: 5px; background: white; }
  .controls button { background: #1c1f23; color: white; cursor: pointer;
                     border-color: #1c1f23; padding: 7px 16px; }
  .controls button.alt { background: white; color: #1c1f23; }
  .controls .preset { font-size: 12px; padding: 5px 10px; }
  .verdict { padding: 22px 24px; }
  .verdict .pill { display: inline-block; color: white; font-weight: 700;
                   padding: 4px 12px; border-radius: 999px;
                   font-size: 12px; letter-spacing: 0.04em; }
  .verdict h3 { margin: 10px 0 6px; font-size: 20px; }
  .verdict p  { margin: 4px 0; font-size: 14px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 14px; }
  .kv { display: flex; justify-content: space-between; padding: 6px 0;
        border-bottom: 1px dashed var(--line); font-size: 14px; }
  .kv:last-child { border-bottom: none; }
  .kv .v { font-variant-numeric: tabular-nums; font-weight: 500; }
  .pos { color: #1b8e3a; } .neg { color: #c62828; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
           font-size: 12px; font-weight: 600; }
  .badge.ok   { background: #e6f4ea; color: #1b8e3a; }
  .badge.warn { background: #fff4e0; color: #b06a00; }
  ul.dates { margin: 6px 0 0 18px; padding: 0; font-size: 13px; }
  .fail { background: #fdecec; border: 1px solid #f4c0c0; padding: 10px 14px;
          border-radius: 6px; margin-top: 10px; font-size: 13px; }
  .fail ul { margin: 4px 0 0 18px; padding: 0; }
  .charts { display: grid; grid-template-columns: 1fr; gap: 18px; }
  .chart-card { padding: 14px 18px; }
  .chart-card .title { font-size: 13px; color: var(--muted);
                       margin-bottom: 8px; font-weight: 600;
                       text-transform: uppercase; letter-spacing: 0.05em; }
  canvas { max-height: 320px; }
  .bucket-hint { font-size: 12px; color: var(--muted); margin-top: 8px; font-style: italic; }
  .day-detail { margin-top: 14px; padding: 14px 16px; background: #f8f9fb;
                border: 1px solid var(--line); border-radius: 6px; }
  .day-detail .head { display: flex; justify-content: space-between; align-items: center;
                      margin-bottom: 10px; }
  .day-detail .head h3 { margin: 0; font-size: 16px; }
  .day-detail .summary { color: var(--muted); font-size: 13px; margin-bottom: 10px; }
  .day-detail .close { background: none; border: 1px solid var(--line); cursor: pointer;
                       padding: 3px 10px; border-radius: 4px; font-size: 12px; }
  .day-detail table { width: 100%; border-collapse: collapse; font-size: 13px;
                      font-variant-numeric: tabular-nums; }
  .day-detail th { text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--line);
                   font-weight: 600; color: var(--muted); font-size: 11px;
                   text-transform: uppercase; letter-spacing: 0.04em; }
  .day-detail td { padding: 6px 8px; border-bottom: 1px solid var(--line); }
  .day-detail tr.win  td:last-child { color: #1b8e3a; font-weight: 600; }
  .day-detail tr.loss td:last-child { color: #c62828; font-weight: 600; }
  .day-detail tr.expand-row td { background: #fff; padding: 8px 12px;
                                 border-bottom: 1px solid var(--line); font-size: 12px;
                                 color: var(--muted); }
  .day-detail tr.trade-row { cursor: pointer; }
  .day-detail tr.trade-row:hover td { background: #eef2ff; }
  .day-detail .pending-tag { background: #fff4e0; color: #b06a00; padding: 1px 6px;
                              border-radius: 3px; font-size: 11px; margin-left: 6px; }
  footer { color: var(--muted); font-size: 12px; margin-top: 32px; text-align: center; }
  code { background: #f0f1f3; padding: 1px 6px; border-radius: 3px; font-size: 12px; }
  .static-banner { background: #fff4e0; border: 1px solid #f0d28a; padding: 8px 14px;
                   border-radius: 6px; font-size: 13px; margin-bottom: 14px; }
  .static-banner.reset { background: #eef4ff; border-color: #cfd9eb; color: #1c1f23; }
  .static-banner.reset strong { color: #1c4ed8; }
  __TOPNAV_CSS__
</style>
</head>
<body>
<div class="wrap">
  __TOPNAV__
  <h1>AI Portfolio Manager — Trading P&amp;L Dashboard</h1>
  <div class="sub" id="window-sub">…</div>

  <div id="static-banner-host"></div>

  <div class="controls">
    <div>
      <label for="from">From</label>
      <input type="date" id="from">
    </div>
    <div>
      <label for="to">To</label>
      <input type="date" id="to">
    </div>
    <div>
      <label for="granularity">Granularity</label>
      <select id="granularity">
        <option value="daily">Daily</option>
        <option value="weekly">Weekly</option>
        <option value="monthly">Monthly</option>
      </select>
    </div>
    <div>
      <label for="verified">Source</label>
      <select id="verified">
        <option value="all">All trades (verified + provisional)</option>
        <option value="verified">Verified only (tax-grade)</option>
      </select>
    </div>
    <button id="apply">Apply</button>
    <div>
      <label for="preset">Quick range</label>
      <select id="preset">
        <option value="">Custom — use From / To above</option>
        <option value="fy0">This FY</option>
        <option value="fy-1">Previous FY</option>
        <option value="fy-2">FY before previous</option>
        <option value="month">This month</option>
        <option value="lastmonth">Last month</option>
        <option value="7d">Last 7 days</option>
        <option value="30d">Last 30 days</option>
        <option value="90d">Last 90 days</option>
        <option value="all">All time</option>
      </select>
    </div>
  </div>

  <div class="card verdict" id="verdict-card">…</div>

  <h2>Headline P&amp;L</h2>
  <div class="card" id="headline-card">…</div>

  <h2>Charts</h2>
  <div class="charts">
    <div class="card chart-card">
      <div class="title">Cumulative net P&amp;L (daily)</div>
      <canvas id="cum-chart"></canvas>
    </div>
    <div class="card chart-card">
      <div class="title" id="bucket-title">Net P&amp;L per bucket</div>
      <canvas id="bucket-chart"></canvas>
      <div class="bucket-hint" id="bucket-hint">Tip: click any bar to drill into that day's trades.</div>
      <div id="day-detail" class="day-detail" hidden></div>
    </div>
  </div>

  <div id="pending-host"></div>

  <footer id="footer">…</footer>
</div>

<script>
const SERVER_MODE = __SERVER_FLAG__;
const INITIAL = __INITIAL_JSON__;
let cumChart = null, bucketChart = null;

function fmtRs(n) {
  const sign = n >= 0 ? "+" : "−";
  return "Rs." + sign + Math.abs(n).toLocaleString("en-IN",
    {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
function pctStr(p) { return p == null ? "—" : (p*100).toFixed(2) + "%"; }
function escapeHTML(s) { return String(s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}[c])); }

function render(payload) {
  const w = payload.window, v = payload.verdict, h = payload.headline;

  const resetHost = document.getElementById("static-banner-host");
  const rp = payload.research_phase || {};
  if (rp.label) {
    const phase = (rp.stage ? rp.stage + " - " : "") + rp.label;
    const pause = rp.live_trading_paused ? "Live trading paused" : "Live trading enabled";
    resetHost.innerHTML = '<div class="static-banner reset"><strong>'
      + escapeHTML(phase) + '</strong> · ' + escapeHTML(pause)
      + (rp.note ? ' · ' + escapeHTML(rp.note) : '') + '</div>';
  } else {
    resetHost.innerHTML = "";
  }

  document.getElementById("window-sub").innerHTML =
    "Window <strong>" + escapeHTML(w.from) + " → " + escapeHTML(w.to) + "</strong>"
    + " · " + w.trading_days + " trading day(s)"
    + ' · <span class="badge ' + (w.include_provisional ? "warn" : "ok") + '">'
    + (w.include_provisional ? "INCLUDES PROVISIONAL" : "VERIFIED ONLY") + "</span>"
    + " · verified " + w.verified_days + ", pending " + w.pending_dates.length;

  const vc = document.getElementById("verdict-card");
  vc.style.background = v.bg;
  vc.style.borderColor = v.fg + "33";
  let failed = "";
  if (v.level !== "GREEN" && v.failed && v.failed.length) {
    failed = '<div class="fail"><strong>Blocked by:</strong><ul>'
      + v.failed.map(t => "<li>" + escapeHTML(t) + "</li>").join("") + "</ul></div>";
  }
  vc.innerHTML =
    '<span class="pill" style="background:' + v.fg + '">' + escapeHTML(v.level) + '</span>'
    + '<h3 style="color:' + v.fg + '">' + escapeHTML(v.headline) + '</h3>'
    + '<p>' + escapeHTML(v.rationale) + '</p>'
    + '<div style="font-size:13px;color:var(--muted);margin-top:8px">'
    + 'Current budget: <strong>Rs.' + v.current_budget.toLocaleString("en-IN") + '</strong>'
    + ' → Recommended: <strong>Rs.' + v.recommended_budget.toLocaleString("en-IN") + '</strong>'
    + '</div>' + failed;

  if (h.trade_count === 0) {
    document.getElementById("headline-card").innerHTML =
      "<p style='color:var(--muted);margin:0'>No trades in window.</p>";
  } else {
    const pnlClass = h.net_pnl >= 0 ? "pos" : "neg";
    const best = h.best_day || ["—", 0], worst = h.worst_day || ["—", 0];
    document.getElementById("headline-card").innerHTML =
      '<div class="grid"><div>'
      + '<div class="kv"><span>Trades</span><span class="v">' + h.trade_count + '</span></div>'
      + '<div class="kv"><span>Trading days</span><span class="v">' + h.trading_days + '</span></div>'
      + '<div class="kv"><span>Gross P&amp;L</span><span class="v">' + fmtRs(h.gross_pnl) + '</span></div>'
      + '<div class="kv"><span>Charges</span><span class="v">' + fmtRs(-h.total_charges) + '</span></div>'
      + '<div class="kv"><span>Net P&amp;L</span><span class="v ' + pnlClass + '">'
      + fmtRs(h.net_pnl) + ' (' + pctStr(h.net_pct) + ' of avg Rs.'
      + Math.round(h.budget_avg).toLocaleString("en-IN") + ')</span></div>'
      + '</div><div>'
      + '<div class="kv"><span>Best day</span><span class="v pos">' + fmtRs(best[1])
      + ' <small style="color:var(--muted);font-weight:400">' + escapeHTML(best[0]) + '</small></span></div>'
      + '<div class="kv"><span>Worst day</span><span class="v neg">' + fmtRs(worst[1])
      + ' <small style="color:var(--muted);font-weight:400">' + escapeHTML(worst[0]) + '</small></span></div>'
      + '</div></div>';
  }

  document.getElementById("bucket-title").textContent =
    "Net P&L per " + w.granularity + " bucket";
  drawCum(payload.charts.cumulative, payload.strategy_overlay);
  drawBucket(payload.charts.bucketed);
  // Filter changed -> any open day-detail is now stale; collapse it.
  closeDayDetail();

  const ph = document.getElementById("pending-host");
  if (w.pending_dates.length) {
    const intro = w.include_provisional
      ? "These trading day(s) are <strong>included as provisional</strong>; numbers may shift on T+1 reconciliation."
      : "These trading day(s) are <strong>excluded</strong> from headline numbers above.";
    ph.innerHTML =
      '<h2>Pending sheet verification (' + w.pending_dates.length + ')</h2>'
      + '<div class="card"><p style="margin-top:0">' + intro + '</p>'
      + '<ul class="dates">' + w.pending_dates.map(d => "<li>" + escapeHTML(d) + "</li>").join("") + "</ul>"
      + '<p style="margin-bottom:0;color:var(--muted);font-size:13px">'
      + 'To finalise: download Zerodha Console → Reports → Tax P&amp;L, '
      + 'drop the xlsx into <code>data/ZerodhaTaxPL/</code>, then run '
      + '<code>python scripts/shared/import_zerodha_taxpnl.py --fy &lt;YYYY&gt;</code>.'
      + '</p></div>';
  } else {
    ph.innerHTML = "";
  }

  document.getElementById("footer").textContent =
    "Generated " + payload.generated_at + " · read-only · sheet-verified rows are tax-grade.";

  if (document.activeElement.tagName !== "INPUT") {
    document.getElementById("from").value = w.from;
    document.getElementById("to").value = w.to;
    document.getElementById("granularity").value = w.granularity;
    document.getElementById("verified").value = w.include_provisional ? "all" : "verified";
  }
}

function drawCum(series, overlay) {
  if (cumChart) cumChart.destroy();
  overlay = overlay || {enabled:false, boundaries:[]};
  const boundaryByDate = {};
  if (overlay.enabled) {
    for (const b of (overlay.boundaries || [])) boundaryByDate[b.date] = b;
  }
  // Custom Chart.js plugin: thin grey vertical line at every date in
  // `boundaryByDate`. Drawn after the dataset so the line sits above
  // the area fill but below the tooltip / hover point. Roadmap D13.
  const versionLinePlugin = {
    id: "strategyVersionLines",
    afterDatasetsDraw(chart) {
      if (!overlay.enabled) return;
      const xScale = chart.scales.x, yScale = chart.scales.y;
      if (!xScale || !yScale) return;
      const ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = "rgba(80,80,80,0.45)";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      const labels = chart.data.labels || [];
      for (let i = 0; i < labels.length; i++) {
        if (boundaryByDate[labels[i]]) {
          const x = xScale.getPixelForValue(i);
          ctx.beginPath();
          ctx.moveTo(x, yScale.top);
          ctx.lineTo(x, yScale.bottom);
          ctx.stroke();
        }
      }
      ctx.restore();
    },
  };
  cumChart = new Chart(document.getElementById("cum-chart"), {
    type: "line",
    data: {
      labels: series.map(p => p.date),
      datasets: [{
        label: "Cumulative net P&L (Rs.)",
        data: series.map(p => p.cum),
        fill: true,
        borderColor: "#1c4ed8",
        backgroundColor: "rgba(28,78,216,0.08)",
        tension: 0.2, pointRadius: 2,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: {
          afterLabel: ctx => {
            const b = boundaryByDate[ctx.label];
            if (!b) return "";
            const subj = b.subject ? b.subject : "(commit subject not in local git)";
            return "Strategy version: " + b.sha + "\n" + subj;
          },
        } },
      },
      scales: { y: { ticks: { callback: v => "Rs." + v } } },
    },
    plugins: [versionLinePlugin],
  });
}
function drawBucket(buckets) {
  if (bucketChart) bucketChart.destroy();
  bucketChart = new Chart(document.getElementById("bucket-chart"), {
    type: "bar",
    data: {
      labels: buckets.map(b => b.label),
      datasets: [{
        label: "Net P&L (Rs.)",
        data: buckets.map(b => b.net_pnl),
        backgroundColor: buckets.map(b => b.net_pnl >= 0 ? "#1b8e3a" : "#c62828"),
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      onClick: (evt, els) => {
        if (!els.length) return;
        onBucketClick(buckets[els[0].index]);
      },
      onHover: (evt, els) => {
        evt.native.target.style.cursor = els.length ? "pointer" : "default";
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: {
          afterLabel: ctx => buckets[ctx.dataIndex].trades + " trade(s)\nClick to drill in",
        } },
      },
      scales: { y: { ticks: { callback: v => "Rs." + v } } },
    },
  });
}

// ── Drill-down: bar click -> per-day trade detail ────────────────
let activeDate = null;

function onBucketClick(bucket) {
  const gran = document.getElementById("granularity").value;
  const host = document.getElementById("day-detail");
  if (gran !== "daily") {
    host.hidden = false;
    host.innerHTML = '<div class="summary">Drill-down is only available in <strong>daily</strong>'
      + ' granularity. Switch granularity to <code>Daily</code> and click a bar to see'
      + ' that day\'s trades.</div>';
    return;
  }
  // Toggle: clicking the same bar again collapses the panel.
  if (activeDate === bucket.label) {
    host.hidden = true;
    host.innerHTML = "";
    activeDate = null;
    return;
  }
  activeDate = bucket.label;
  loadDayDetail(bucket.label);
}

async function loadDayDetail(date) {
  const host = document.getElementById("day-detail");
  host.hidden = false;
  host.innerHTML = '<div class="summary">Loading ' + escapeHTML(date) + '…</div>';
  if (!SERVER_MODE) {
    host.innerHTML = '<div class="summary">Per-trade detail needs the live server.'
      + ' Re-run <code>python main.py --mode dashboard</code> (without <code>--no-open</code>).</div>';
    return;
  }
  const verified = document.getElementById("verified").value;
  const params = new URLSearchParams({ date, verified });
  try {
    const res = await fetch("/api/day?" + params.toString());
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderDayDetail(await res.json());
  } catch (e) {
    host.innerHTML = '<div class="summary">Failed to load ' + escapeHTML(date)
      + ': ' + escapeHTML(String(e)) + '</div>';
  }
}

function renderDayDetail(d) {
  const host = document.getElementById("day-detail");
  if (d.trade_count === 0) {
    host.innerHTML =
      '<div class="head"><h3>' + escapeHTML(d.date) + '</h3>'
      + '<button class="close" onclick="closeDayDetail()">close ✕</button></div>'
      + '<div class="summary">No trades on this date'
      + (d.report_found ? "" : " (no trading report on disk)") + '.</div>';
    return;
  }
  const pnlClass = d.net_pnl >= 0 ? "pos" : "neg";
  const summary =
    '<strong>' + d.trade_count + '</strong> trade(s) · '
    + '<span class="' + pnlClass + '">' + fmtRs(d.net_pnl) + ' net</span>'
    + ' (gross ' + fmtRs(d.gross_pnl) + ', charges ' + fmtRs(-d.charges) + ')'
    + ' · ' + d.winners + ' win / ' + d.losers + ' loss'
    + (d.report_found ? "" : ' · <span class="pending-tag">no report on disk</span>');

  const rows = d.trades.map((t, i) => {
    const cls = t.net_pnl > 0 ? "win" : (t.net_pnl < 0 ? "loss" : "");
    const ent = t.entry_time ? t.entry_time.slice(11, 16) : "—";
    const ext = t.exit_time  ? t.exit_time.slice(11, 16)  : "—";
    const pendBadge = t.sheet_verified === "verified" ? ""
      : ' <span class="pending-tag">provisional</span>';
    const extras = [];
    if (t.entry_score != null) extras.push("entry score " + Number(t.entry_score).toFixed(1));
    if (t.entry_rsi   != null) extras.push("RSI " + Number(t.entry_rsi).toFixed(1));
    if (t.exit_score  != null) extras.push("exit score " + Number(t.exit_score).toFixed(1));
    const expandText = (t.rationale || extras.length)
      ? (extras.join(" · ") + (t.rationale ? (extras.length ? " — " : "") + t.rationale : ""))
      : "";
    let html = '<tr class="trade-row ' + cls + '" data-idx="' + i + '">'
      + '<td>' + ent + ' → ' + ext + '</td>'
      + '<td><strong>' + escapeHTML(t.symbol) + '</strong>' + pendBadge + '</td>'
      + '<td>' + escapeHTML(t.side) + '</td>'
      + '<td>' + t.qty + '</td>'
      + '<td>' + t.entry_price.toFixed(2) + ' → ' + t.exit_price.toFixed(2) + '</td>'
      + '<td>' + escapeHTML(t.exit_reason || "—") + '</td>'
      + '<td>' + fmtRs(t.net_pnl) + '</td>'
      + '</tr>';
    if (expandText) {
      html += '<tr class="expand-row" data-idx="' + i + '" hidden>'
        + '<td colspan="7">' + escapeHTML(expandText) + '</td></tr>';
    }
    return html;
  }).join("");

  host.innerHTML =
    '<div class="head"><h3>' + escapeHTML(d.date) + '</h3>'
    + '<button class="close" onclick="closeDayDetail()">close ✕</button></div>'
    + '<div class="summary">' + summary + '</div>'
    + '<table><thead><tr>'
    + '<th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th>'
    + '<th>Entry → Exit</th><th>Exit reason</th><th>Net P&L</th>'
    + '</tr></thead><tbody>' + rows + '</tbody></table>';

  host.querySelectorAll("tr.trade-row").forEach(tr => {
    tr.addEventListener("click", () => {
      const idx = tr.dataset.idx;
      const ext = host.querySelector('tr.expand-row[data-idx="' + idx + '"]');
      if (ext) ext.hidden = !ext.hidden;
    });
  });
  host.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeDayDetail() {
  const host = document.getElementById("day-detail");
  host.hidden = true; host.innerHTML = ""; activeDate = null;
}

async function refresh() {
  const params = new URLSearchParams({
    from: document.getElementById("from").value,
    to:   document.getElementById("to").value,
    granularity: document.getElementById("granularity").value,
    verified: document.getElementById("verified").value,
  });
  if (!SERVER_MODE) {
    document.getElementById("static-banner-host").innerHTML =
      '<div class="static-banner">Static snapshot — date-range / source changes need '
      + '<code>python main.py --mode dashboard --serve</code> to be live. '
      + 'Granularity still works (re-buckets the embedded data).</div>';
    const g = document.getElementById("granularity").value;
    INITIAL.charts.bucketed = rebucket(INITIAL.charts.cumulative, g);
    INITIAL.window.granularity = g;
    render(INITIAL);
    return;
  }
  const res = await fetch("/api/data?" + params.toString());
  if (!res.ok) { alert("Failed: " + res.status); return; }
  render(await res.json());
}

function rebucket(cum, gran) {
  const daily = [];
  let prev = 0;
  for (const p of cum) { daily.push({date: p.date, pnl: p.cum - prev}); prev = p.cum; }
  if (gran === "daily") {
    return daily.map(d => ({label: d.date, net_pnl: +d.pnl.toFixed(2), trades: 1}));
  }
  const buckets = new Map();
  for (const d of daily) {
    let key;
    if (gran === "monthly") key = d.date.slice(0,7);
    else {
      const t = new Date(d.date + "T00:00:00Z");
      const day = (t.getUTCDay() + 6) % 7;
      t.setUTCDate(t.getUTCDate() - day + 3);
      const week1 = new Date(Date.UTC(t.getUTCFullYear(),0,4));
      const w = 1 + Math.round(((t - week1)/86400000 - 3 + ((week1.getUTCDay()+6)%7))/7);
      key = t.getUTCFullYear() + "-W" + String(w).padStart(2,"0");
    }
    const cur = buckets.get(key) || {label: key, net_pnl: 0, trades: 0};
    cur.net_pnl += d.pnl; cur.trades += 1;
    buckets.set(key, cur);
  }
  return [...buckets.values()].sort((a,b) => a.label.localeCompare(b.label))
    .map(b => ({...b, net_pnl: +b.net_pnl.toFixed(2)}));
}

document.getElementById("apply").addEventListener("click", refresh);
document.getElementById("preset").addEventListener("change", (ev) => {
  const v = ev.target.value;
  if (!v) return;  // "Custom" — leave From/To untouched
  const today = new Date();
  const fmt = d => d.toISOString().slice(0, 10);
  const from = document.getElementById("from");
  const to   = document.getElementById("to");
  // Indian FY: Apr 1 → Mar 31. fy0 = current FY, fy-1 = previous, fy-2 = the one before.
  if (v.startsWith("fy")) {
    const offset = parseInt(v.slice(2), 10) || 0;  // "fy0" → 0, "fy-1" → -1, "fy-2" → -2
    const baseY = today.getMonth() >= 3 ? today.getFullYear() : today.getFullYear() - 1;
    const y = baseY + offset;
    from.value = y + "-04-01";
    to.value   = (y + 1) + "-03-31";
  } else if (v === "month") {
    from.value = fmt(new Date(today.getFullYear(), today.getMonth(), 1));
    to.value   = fmt(today);
  } else if (v === "lastmonth") {
    const first = new Date(today.getFullYear(), today.getMonth() - 1, 1);
    const last  = new Date(today.getFullYear(), today.getMonth(), 0);  // day 0 of this month = last day of prev
    from.value = fmt(first);
    to.value   = fmt(last);
  } else if (v === "7d") {
    const d = new Date(today); d.setDate(d.getDate() - 6);
    from.value = fmt(d); to.value = fmt(today);
  } else if (v === "30d") {
    const d = new Date(today); d.setDate(d.getDate() - 29);
    from.value = fmt(d); to.value = fmt(today);
  } else if (v === "90d") {
    const d = new Date(today); d.setDate(d.getDate() - 89);
    from.value = fmt(d); to.value = fmt(today);
  } else if (v === "all") {
    // Earliest plausible trade date for this project (well before first live run).
    from.value = "2020-01-01";
    to.value   = fmt(today);
  }
  refresh();
});

render(INITIAL);
</script>
</body>
</html>
"""


__all__ = ["build_payload", "render_shell", "write_and_maybe_open"]
