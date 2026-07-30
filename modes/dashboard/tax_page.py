"""Tax dashboard page — /tax route.

Single-page HTML with:
  * FY selector + "Other FY income" text input (live recompute via /api/tax)
  * FY summary card (intraday net, charges breakdown, capital gains)
  * Slab-wise tax projection given user-entered other income
  * "Tax attributable to intraday slice" — the headline number
  * ITR-3 / Schedule BP copy-friendly field list (with click-to-copy)
  * Documents checklist
  * Cross-link to /theory/tax-guide for the regulatory reference

Pure stdlib HTML/JS. No new deps.
"""

from __future__ import annotations

import html
import json

from modes.dashboard.nav import render_topnav, topnav_css
from modes.dashboard.tax import (
    CESS_RATE,
    REBATE_CEILING_BY_FY,
    SLABS_BY_FY,
    STD_DEDUCTION_SALARY,
    TaxComputation,
    compute_fy_summary,
    compute_tax,
    latest_known_fy,
    rebate_ceiling_for_fy,
    slabs_for_fy,
)
from modes.dashboard.tax.fy_summary import FYSummary
from shared.tax_db import current_fy, fy_label


# ── Helpers ────────────────────────────────────────────────────

def _fmt_rs(x: float, *, signed: bool = False, decimals: int = 2) -> str:
    if x is None:
        return "—"
    if signed:
        sign = "+" if x >= 0 else "−"
        return f"Rs.{sign}{abs(x):,.{decimals}f}"
    return f"Rs.{x:,.{decimals}f}"


def _fmt_pct(rate: float) -> str:
    return f"{rate*100:.0f}%"


def _available_fys() -> list[int]:
    """FYs we know slabs for + the current FY (deduplicated, descending)."""
    fys = set(SLABS_BY_FY.keys())
    fys.add(current_fy())
    return sorted(fys, reverse=True)


# ── Main render ────────────────────────────────────────────────

def render_tax_page(*, other_income: float = 0.0,
                    fy_start: int | None = None,
                    is_salaried: bool = True) -> str:
    """Render the /tax dashboard page."""

    fy_start = fy_start or current_fy()
    summary = compute_fy_summary(fy_start)
    speculative_income = max(0.0, summary.intraday.net_pnl)

    # Two projections: WITH and WITHOUT the intraday slice.
    # Difference = "tax attributable to intraday this FY".
    proj_with = compute_tax(
        fy_start=fy_start,
        other_income=other_income,
        intraday_net=speculative_income,
        capital_gains_short_term=max(0.0, summary.capital_gains.stcg_net),
        capital_gains_long_term=max(0.0, summary.capital_gains.ltcg_net),
        is_salaried=is_salaried,
    )
    proj_without = compute_tax(
        fy_start=fy_start,
        other_income=other_income,
        intraday_net=0.0,
        capital_gains_short_term=max(0.0, summary.capital_gains.stcg_net),
        capital_gains_long_term=max(0.0, summary.capital_gains.ltcg_net),
        is_salaried=is_salaried,
    )
    intraday_tax = round(proj_with.total_tax - proj_without.total_tax, 2)

    from modes.dashboard.theme import (
        theme_boot_script, theme_css, theme_overrides_css,
    )
    return (
      _PAGE
      .replace("__THEME_BOOT__", theme_boot_script())
      .replace("__THEME_CSS__", theme_css())
      .replace("__THEME_OVERRIDES__", theme_overrides_css())
      .replace("__TOPNAV_CSS__", topnav_css())
      .replace("__BODY__", _body(
        summary=summary,
        proj_with=proj_with,
        proj_without=proj_without,
        intraday_tax=intraday_tax,
        other_income=other_income,
        is_salaried=is_salaried,
      ))
    )


def render_tax_api(*, other_income: float, fy_start: int | None,
                   is_salaried: bool) -> dict:
    """JSON payload for /api/tax — drives the live recompute."""
    fy_start = fy_start or current_fy()
    summary = compute_fy_summary(fy_start)
    speculative_income = max(0.0, summary.intraday.net_pnl)

    proj_with = compute_tax(
        fy_start=fy_start, other_income=other_income,
        intraday_net=speculative_income,
        capital_gains_short_term=max(0.0, summary.capital_gains.stcg_net),
        capital_gains_long_term=max(0.0, summary.capital_gains.ltcg_net),
        is_salaried=is_salaried,
    )
    proj_without = compute_tax(
        fy_start=fy_start, other_income=other_income,
        intraday_net=0.0,
        capital_gains_short_term=max(0.0, summary.capital_gains.stcg_net),
        capital_gains_long_term=max(0.0, summary.capital_gains.ltcg_net),
        is_salaried=is_salaried,
    )

    return {
        "fy_start": fy_start,
        "fy_label": summary.fy_label,
        "other_income": other_income,
        "is_salaried": is_salaried,
        "intraday_net": summary.intraday.net_pnl,
        "speculative_income_for_slab": speculative_income,
        "projection": _proj_dict(proj_with),
        "projection_without_intraday": _proj_dict(proj_without),
        "intraday_tax_attributable": round(proj_with.total_tax - proj_without.total_tax, 2),
        "rebate_ceiling": rebate_ceiling_for_fy(fy_start),
    }


def _proj_dict(p: TaxComputation) -> dict:
    return {
        "gross_total_income": p.gross_total_income,
        "standard_deduction": p.standard_deduction,
        "taxable_income": p.taxable_income,
        "slab_tax": p.slab_tax,
        "rebate_87a": p.rebate_87a,
        "tax_after_rebate": p.tax_after_rebate,
        "surcharge": p.surcharge,
        "cess": p.cess,
        "total_tax": p.total_tax,
        "marginal_rate": p.marginal_rate,
        "effective_rate": p.effective_rate,
    }


# ── Body fragments ─────────────────────────────────────────────

def _fy_options(active: int) -> str:
    parts = []
    for fy in _available_fys():
        sel = " selected" if fy == active else ""
        parts.append(f'<option value="{fy}"{sel}>{html.escape(fy_label(fy))}</option>')
    return "".join(parts)


def _slab_table(fy_start: int, taxable: float) -> str:
    slabs = slabs_for_fy(fy_start)
    rows = []
    prev = 0.0
    cumulative_tax = 0.0
    remaining = taxable
    for upper, rate in slabs:
        upper_disp = "∞" if upper is None else f"Rs.{upper:,.0f}"
        slab_width = (upper - prev) if upper is not None else max(0.0, remaining)
        amount_in_slab = min(remaining, slab_width) if upper is not None else max(0.0, remaining)
        amount_in_slab = max(0.0, amount_in_slab)
        tax_in_slab = amount_in_slab * rate
        cumulative_tax += tax_in_slab
        remaining = max(0.0, remaining - amount_in_slab) if upper is not None else 0.0
        active_cls = " class=\"hot\"" if amount_in_slab > 0 else ""
        rows.append(
            f"<tr{active_cls}>"
            f"<td>Rs.{prev:,.0f} – {upper_disp}</td>"
            f"<td class=\"r\">{_fmt_pct(rate)}</td>"
            f"<td class=\"r\">{_fmt_rs(amount_in_slab, decimals=0)}</td>"
            f"<td class=\"r\">{_fmt_rs(tax_in_slab, decimals=0)}</td>"
            f"</tr>"
        )
        if upper is None:
            break
        prev = upper
    return f"""
<table class="md-table">
  <thead><tr><th>Slab</th><th class="r">Rate</th>
              <th class="r">Income in slab</th><th class="r">Tax</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
"""


def _intraday_card(s: FYSummary) -> str:
    i = s.intraday
    if i.trade_count == 0:
        return f"""
<section class="card">
  <h2>Intraday — Speculative Business Income</h2>
  <p class="muted">No intraday trades found in {html.escape(s.window_from)} to {html.escape(s.window_to)}.</p>
</section>
"""
    return f"""
<section class="card">
  <h2>Intraday — Speculative Business Income <span class="pill">{html.escape(s.fy_label)}</span></h2>
  <table class="kv">
    <tr><td>Trading days</td><td class="r">{i.trading_days}</td></tr>
    <tr><td>Total trades</td><td class="r">{i.trade_count}
        <span class="muted">({i.verified_count} verified, {i.trade_count - i.verified_count} provisional)</span></td></tr>
    <tr><td>Gross P&amp;L</td><td class="r {_pn_cls(i.gross_pnl)}">{_fmt_rs(i.gross_pnl, signed=True)}</td></tr>
    <tr><td>Total regulatory charges</td><td class="r warn">−{_fmt_rs(i.charges.total)}</td></tr>
    <tr><td>Net P&amp;L</td><td class="r {_pn_cls(i.net_pnl)} bold">{_fmt_rs(i.net_pnl, signed=True)}</td></tr>
    <tr><td>Speculative turnover (Section 43(5))</td><td class="r">{_fmt_rs(i.speculative_turnover)}</td></tr>
  </table>

  <h3>Charge breakdown (deductible expenses for ITR-3)</h3>
  <table class="kv">
    <tr><td>Brokerage</td><td class="r">{_fmt_rs(i.charges.brokerage)}</td></tr>
    <tr><td>STT</td><td class="r">{_fmt_rs(i.charges.stt)}</td></tr>
    <tr><td>Exchange transaction</td><td class="r">{_fmt_rs(i.charges.exchange_txn)}</td></tr>
    <tr><td>GST</td><td class="r">{_fmt_rs(i.charges.gst)}</td></tr>
    <tr><td>SEBI charges</td><td class="r">{_fmt_rs(i.charges.sebi)}</td></tr>
    <tr><td>Stamp duty</td><td class="r">{_fmt_rs(i.charges.stamp_duty)}</td></tr>
  </table>
</section>
"""


def _pn_cls(x: float) -> str:
    if x > 0:
        return "ok"
    if x < 0:
        return "warn"
    return ""


def _projection_card(proj_with: TaxComputation, proj_without: TaxComputation,
                     intraday_tax: float, fy_start: int,
                     other_income: float, is_salaried: bool,
                     summary: FYSummary) -> str:
    rebate_ceiling = rebate_ceiling_for_fy(fy_start)
    rebate_note = ""
    if proj_with.taxable_income <= rebate_ceiling:
        rebate_note = (
            f'<div class="rebate-note">Section 87A rebate fully covers tax on '
            f'taxable income up to Rs.{rebate_ceiling:,.0f} — net tax payable is zero on '
            'the slab portion.</div>'
        )

    return f"""
<section class="card">
  <h2>Tax Projection — current inputs</h2>
  <table class="kv">
    <tr><td>FY</td><td class="r mono">{html.escape(summary.fy_label)}</td></tr>
    <tr><td>Other FY income (entered)</td><td class="r mono" id="cell-other-income">{_fmt_rs(other_income, decimals=0)}</td></tr>
    <tr><td>Speculative income (intraday net, only if positive)</td><td class="r mono">{_fmt_rs(max(0.0, summary.intraday.net_pnl), decimals=0)}</td></tr>
    <tr><td>Standard deduction (salaried)</td><td class="r mono">−{_fmt_rs(proj_with.standard_deduction, decimals=0)}</td></tr>
    <tr><td><strong>Taxable income</strong></td><td class="r mono bold" id="cell-taxable">{_fmt_rs(proj_with.taxable_income, decimals=0)}</td></tr>
  </table>

  <h3>Slab breakdown</h3>
  <div class="table-scroll" id="slab-table-wrap">{_slab_table(fy_start, proj_with.taxable_income)}</div>

  <table class="kv">
    <tr><td>Slab tax</td><td class="r mono" id="cell-slab-tax">{_fmt_rs(proj_with.slab_tax)}</td></tr>
    <tr><td>Section 87A rebate</td><td class="r mono ok" id="cell-rebate">−{_fmt_rs(proj_with.rebate_87a)}</td></tr>
    <tr><td>Surcharge</td><td class="r mono" id="cell-surcharge">{_fmt_rs(proj_with.surcharge)}</td></tr>
    <tr><td>Health &amp; Education cess (4%)</td><td class="r mono" id="cell-cess">{_fmt_rs(proj_with.cess)}</td></tr>
    <tr><td><strong>Total tax payable</strong></td><td class="r mono bold" id="cell-total-tax">{_fmt_rs(proj_with.total_tax)}</td></tr>
    <tr><td>Marginal rate (next rupee)</td><td class="r mono" id="cell-marginal">{_fmt_pct(proj_with.marginal_rate)}</td></tr>
    <tr><td>Effective rate (total / gross)</td><td class="r mono" id="cell-effective">{proj_with.effective_rate*100:.2f}%</td></tr>
  </table>
  {rebate_note}

  <div class="highlight">
    <div class="big-label">Tax attributable to intraday this FY</div>
    <div class="big-value" id="cell-intraday-tax">{_fmt_rs(intraday_tax, decimals=0)}</div>
    <div class="big-sub">= Total tax with intraday minus tax without intraday.
      This is what will effectively be deducted from your intraday earnings at your current bracket.</div>
  </div>
</section>
"""


def _itr3_card(s: FYSummary) -> str:
    """ITR-3 → Schedule BP copy-friendly fields (verified + provisional)."""
    i = s.intraday
    fields = [
        ("Speculative gross P&L (signed)", i.gross_pnl),
        ("Total deductible charges (brokerage + STT + exch + GST + SEBI + stamp)", i.charges.total),
        ("Net speculative income (gross − charges)", i.net_pnl),
        ("Speculative turnover (absolute-sum method, Section 43(5))", i.speculative_turnover),
        ("Brokerage", i.charges.brokerage),
        ("STT", i.charges.stt),
        ("Exchange transaction charges", i.charges.exchange_txn),
        ("GST", i.charges.gst),
        ("SEBI charges", i.charges.sebi),
        ("Stamp duty", i.charges.stamp_duty),
    ]
    rows = "".join(
        f'<tr><td>{html.escape(label)}</td>'
        f'<td class="r mono"><span class="copy" title="Click to copy" '
        f'onclick="copyVal(this)">{val:.2f}</span></td></tr>'
        for label, val in fields
    )

    cg = s.capital_gains
    cg_block = ""
    if cg.stcg_trade_count or cg.ltcg_trade_count:
        cg_block = f"""
<h3>Schedule CG — Capital Gains</h3>
<table class="kv">
  <tr><td>STCG profit (gross)</td><td class="r mono"><span class="copy" onclick="copyVal(this)">{cg.stcg_profit:.2f}</span></td></tr>
  <tr><td>STCG loss</td><td class="r mono"><span class="copy" onclick="copyVal(this)">{cg.stcg_loss:.2f}</span></td></tr>
  <tr><td>STCG net</td><td class="r mono"><span class="copy" onclick="copyVal(this)">{cg.stcg_net:.2f}</span></td></tr>
  <tr><td>LTCG profit (gross)</td><td class="r mono"><span class="copy" onclick="copyVal(this)">{cg.ltcg_profit:.2f}</span></td></tr>
  <tr><td>LTCG loss</td><td class="r mono"><span class="copy" onclick="copyVal(this)">{cg.ltcg_loss:.2f}</span></td></tr>
  <tr><td>LTCG net</td><td class="r mono"><span class="copy" onclick="copyVal(this)">{cg.ltcg_net:.2f}</span></td></tr>
</table>
"""

    return f"""
<section class="card">
  <h2>ITR-3 copy-friendly values <span class="pill">click any number to copy</span></h2>
  <p class="muted">Section 43(5) speculative business income — declare under Schedule BP.
    Reuse these when filling the form. Ground rule: every figure here is computed from
    the trade ledger; cross-check against your Zerodha Tax P&amp;L PDF before filing.</p>

  <h3>Schedule BP — Speculative Business</h3>
  <table class="kv">{rows}</table>

  {cg_block}
</section>
"""


def _docs_checklist() -> str:
    items = [
        "Zerodha Console → Tax P&L (PDF) — official authoritative reference.",
        "Zerodha Console → Tradebook (CSV) — trade-by-trade record.",
        "Zerodha Console → Ledger (CSV) — money-in/money-out for the FY.",
        "Kite Connect API bills — 12 months (deductible business expense).",
        "Anthropic / Claude API invoices — 12 months (deductible business expense).",
        "Internet bill / electricity / proportional rent if claiming home office.",
        "Bank statements showing transfers to/from broker.",
        "Form 26AS + AIS from <a href='https://www.incometaxindia.gov.in' target='_blank' rel='noopener'>incometaxindia.gov.in</a> — verify TDS.",
        "Last year's ITR (for carry-forward of speculative loss, if any).",
    ]
    lis = "".join(f"<li>{x}</li>" for x in items)
    return f"""
<section class="card">
  <h2>Documents checklist for filing</h2>
  <ul class="checklist">{lis}</ul>
</section>
"""


def _theory_link_card() -> str:
    return """
<section class="card theory-banner">
  <div>
    <div class="banner-eyebrow">Regulatory reference</div>
    <div class="banner-title">📖 See the Tax Guide for the full theory</div>
    <div class="banner-sub">Slabs, Section 87A rebate, Section 43(5), ITR-3 schedules,
      audit thresholds, advance-tax schedule, deductible expenses, speculative loss
      carry-forward — all explained in the docs page.</div>
  </div>
  <a class="banner-cta" href="/theory/tax-guide">Open Tax Guide →</a>
</section>
"""


def _body(*, summary: FYSummary, proj_with: TaxComputation,
          proj_without: TaxComputation, intraday_tax: float,
          other_income: float, is_salaried: bool) -> str:
    salaried_checked = "checked" if is_salaried else ""
    topnav = render_topnav(
        '/tax',
        after_links='<span class="muted small">Pure projection &mdash; does not file or pay anything.</span>',
    )
    return f"""
{topnav}

<h1 class="page-title">Tax — {html.escape(summary.fy_label)}</h1>
<div class="sub">
  Income-tax estimate for this FY built from your live trade ledger plus the other
  income you enter below. New-regime slabs, Section 87A rebate, surcharge and
  Health &amp; Education cess (4%) are applied as per Budget 2025 (effective FY 2025-26+).
</div>

<section class="card inputs">
  <h2>Inputs</h2>
  <form id="tax-form" onsubmit="return false;">
    <label class="field">
      <span>Financial Year</span>
      <select name="fy_start" id="fy-select" onchange="recompute()">
        {_fy_options(summary.fy_start)}
      </select>
    </label>
    <label class="field grow">
      <span>Other FY income (Rs.) — salary gross + interest + rent + everything except intraday/CG</span>
      <input type="number" name="other_income" id="other-income" min="0" step="1000"
             value="{other_income:.0f}" oninput="recomputeDebounced()">
    </label>
    <label class="field check">
      <input type="checkbox" id="is-salaried" {salaried_checked} onchange="recompute()">
      <span>Salaried (apply Rs.{STD_DEDUCTION_SALARY:,} standard deduction)</span>
    </label>
  </form>
  <div class="hint">Numbers below recompute live as you type.</div>
</section>

{_intraday_card(summary)}

{_projection_card(proj_with, proj_without, intraday_tax, summary.fy_start, other_income, is_salaried, summary)}

{_itr3_card(summary)}

{_docs_checklist()}

{_theory_link_card()}

<footer>
  Disclaimer: pure heuristic projection. Verify against your Zerodha Tax P&amp;L PDF and consult
  a CA before filing. Source code: <code>modes/dashboard/tax_page.py</code> + <code>modes/dashboard/tax/</code>.
</footer>
"""


# ── Page template (CSS + JS shell) ─────────────────────────────

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tax — AI Portfolio Manager</title>
__THEME_BOOT__
<style>
  __THEME_CSS__
  :root { --ok: #1b8e3a; --warn: #c62828; --hot: #fff8e1; }
  html[data-theme="dark"] { --ok: #34d39f; --warn: #ff7b72; --hot: #33260f; }
  * { box-sizing: border-box; }
  body { font-family: var(--font);
         background: var(--bg); color: var(--fg); margin: 0; padding: 24px;
         line-height: 1.55; }
  .wrap { max-width: 1080px; margin: 0 auto; }
  __TOPNAV_CSS__
  nav.topnav .small { font-size: 12px; }

  h1.page-title { font-size: 24px; margin: 4px 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
  .muted { color: var(--muted); }
  .ok    { color: var(--ok); }
  .warn  { color: var(--warn); }
  .bold  { font-weight: 700; }
  .mono  { font-variant-numeric: tabular-nums;
           font-family: ui-monospace, Menlo, Consolas, monospace; }
  .r     { text-align: right; }
  .pill  { font-size: 11px; background: var(--soft); color: var(--muted);
           padding: 2px 8px; border-radius: 999px; margin-left: 8px;
           text-transform: uppercase; letter-spacing: 0.05em; }

  .card { background: var(--card); border: 1px solid var(--line);
          border-radius: 10px; padding: 20px 24px; margin-bottom: 18px;
          box-shadow: var(--shadow-sm); }
  .card h2 { font-size: 17px; margin: 0 0 12px; color: var(--fg); }
  .card h3 { font-size: 14px; margin: 18px 0 8px; color: var(--muted);
             text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
  .card p  { margin: 6px 0; }
  table.kv { width: 100%; border-collapse: collapse;
             font-size: 13.5px; font-variant-numeric: tabular-nums; }
  table.kv td { padding: 7px 6px; border-bottom: 1px dashed var(--line);
                vertical-align: top; }
  table.kv tr:last-child td { border-bottom: none; }

  table.md-table { width: 100%; border-collapse: collapse; font-size: 13.5px;
                   font-variant-numeric: tabular-nums; }
  table.md-table th { text-align: left; padding: 8px 10px;
                      border-bottom: 2px solid var(--line);
                      background: var(--card-2); font-weight: 600;
                      font-size: 12.5px; color: var(--fg-2); }
  table.md-table th.r { text-align: right; }
  table.md-table td { padding: 7px 10px; border-bottom: 1px solid var(--line); }
  table.md-table tr.hot td { background: var(--hot); font-weight: 600; }
  .table-scroll { overflow-x: auto; margin: 8px 0 14px; }

  .inputs form { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; }
  .field { display: flex; flex-direction: column; gap: 4px; font-size: 13px;
           color: var(--muted); }
  .field.grow { flex: 1; min-width: 280px; }
  .field input[type=number], .field select {
        font: inherit; padding: 8px 10px; border: 1px solid var(--line);
        border-radius: 5px; background: white; min-width: 180px;
        font-variant-numeric: tabular-nums; }
  .field.check { flex-direction: row; align-items: center; gap: 8px; color: var(--fg); }
  .inputs .hint { font-size: 12px; color: var(--muted); margin-top: 8px; }

  .rebate-note { margin-top: 12px; padding: 8px 12px; background: #e7f6ec;
                 border-left: 3px solid var(--ok); font-size: 12.5px;
                 color: #18532b; border-radius: 0 4px 4px 0; }

  .highlight { margin-top: 16px; padding: 16px 18px;
               background: linear-gradient(180deg, #fff8e1, #fdf3c7);
               border: 1px solid #ecd071; border-radius: 8px; }
  .big-label { font-size: 12px; text-transform: uppercase;
               letter-spacing: 0.06em; color: #6a5300; font-weight: 600; }
  .big-value { font-size: 28px; font-weight: 700; color: #5b4500;
               font-variant-numeric: tabular-nums; margin: 2px 0; }
  .big-sub   { font-size: 12px; color: #6a5300; }

  .copy { cursor: pointer; padding: 1px 6px; border-radius: 3px;
          background: var(--soft); border: 1px dashed transparent; }
  .copy:hover { border-color: var(--line); background: #e9eef5; }
  .copy.copied { background: #d8f0df; border-color: var(--ok); color: var(--ok); }

  ul.checklist { margin: 6px 0; padding-left: 22px; font-size: 13.5px; }
  ul.checklist li { margin: 4px 0; }

  .theory-banner { display: flex; align-items: center; gap: 18px;
                   background: #eef3ff; border-color: #c7d4ff; }
  .banner-eyebrow { font-size: 11px; text-transform: uppercase;
                    letter-spacing: 0.06em; color: #2945a8; font-weight: 600; }
  .banner-title { font-size: 17px; font-weight: 600; margin: 2px 0; }
  .banner-sub { font-size: 13px; color: #2c3a6a; }
  .banner-cta { padding: 10px 16px; background: #1c3aa1; color: white;
                text-decoration: none; border-radius: 6px; font-weight: 600;
                white-space: nowrap; }
  .banner-cta:hover { background: #15307f; }

  footer { color: var(--muted); font-size: 12px; margin-top: 18px;
           text-align: center; }
  __THEME_OVERRIDES__
</style>
</head>
<body>
<div class="wrap" id="root">
__BODY__
</div>

<script>
let _debounceTimer = null;
function recomputeDebounced() {
  clearTimeout(_debounceTimer);
  _debounceTimer = setTimeout(recompute, 250);
}

function fmtRs(x, decimals) {
  if (x == null || isNaN(x)) return "—";
  decimals = (decimals == null) ? 2 : decimals;
  return "Rs." + Number(x).toLocaleString("en-IN", {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals
  });
}
function fmtRsSigned(x, decimals) {
  if (x == null || isNaN(x)) return "—";
  decimals = (decimals == null) ? 2 : decimals;
  const sign = x >= 0 ? "+" : "−";
  return "Rs." + sign + Math.abs(x).toLocaleString("en-IN", {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals
  });
}
function fmtPct(rate) { return (rate * 100).toFixed(0) + "%"; }

async function recompute() {
  const fy = document.getElementById("fy-select").value;
  const oi = document.getElementById("other-income").value || "0";
  const sal = document.getElementById("is-salaried").checked ? "1" : "0";
  const url = `/api/tax?fy=${encodeURIComponent(fy)}&other_income=${encodeURIComponent(oi)}&is_salaried=${sal}`;
  try {
    const resp = await fetch(url, { cache: "no-store" });
    if (!resp.ok) return;
    const data = await resp.json();
    applyData(data);
  } catch (e) {
    console.error("tax recompute failed", e);
  }
}

function applyData(d) {
  const p = d.projection;
  setText("cell-other-income", fmtRs(d.other_income, 0));
  setText("cell-taxable", fmtRs(p.taxable_income, 0));
  setText("cell-slab-tax", fmtRs(p.slab_tax));
  document.getElementById("cell-rebate").textContent = "−" + fmtRs(p.rebate_87a);
  setText("cell-surcharge", fmtRs(p.surcharge));
  setText("cell-cess", fmtRs(p.cess));
  setText("cell-total-tax", fmtRs(p.total_tax));
  setText("cell-marginal", fmtPct(p.marginal_rate));
  setText("cell-effective", (p.effective_rate * 100).toFixed(2) + "%");
  setText("cell-intraday-tax", fmtRs(d.intraday_tax_attributable, 0));
  rebuildSlabTable(p.taxable_income, d.fy_start);
}
function setText(id, v) {
  const el = document.getElementById(id);
  if (el) el.textContent = v;
}

// Slab tables are rendered server-side initially. For the live update we
// reuse the slabs data embedded as a JSON island below.
const SLABS = __SLABS_JSON__;
function rebuildSlabTable(taxable, fyStart) {
  const slabs = SLABS[fyStart] || SLABS[Object.keys(SLABS).pop()];
  if (!slabs) return;
  let prev = 0, remaining = taxable, rows = "";
  for (const [upper, rate] of slabs) {
    const upperDisp = (upper == null) ? "∞" : "Rs." + upper.toLocaleString("en-IN");
    const slabWidth = (upper == null) ? Math.max(0, remaining) : (upper - prev);
    let amount = (upper == null) ? Math.max(0, remaining) : Math.min(remaining, slabWidth);
    amount = Math.max(0, amount);
    const taxIn = amount * rate;
    remaining = (upper == null) ? 0 : Math.max(0, remaining - amount);
    const cls = amount > 0 ? ' class="hot"' : "";
    rows += `<tr${cls}><td>Rs.${prev.toLocaleString("en-IN")} – ${upperDisp}</td>` +
            `<td class="r">${(rate*100).toFixed(0)}%</td>` +
            `<td class="r">${fmtRs(amount, 0)}</td>` +
            `<td class="r">${fmtRs(taxIn, 0)}</td></tr>`;
    if (upper == null) break;
    prev = upper;
  }
  document.getElementById("slab-table-wrap").innerHTML =
    `<table class="md-table">
      <thead><tr><th>Slab</th><th class="r">Rate</th>
                  <th class="r">Income in slab</th><th class="r">Tax</th></tr></thead>
      <tbody>${rows}</tbody>
     </table>`;
}

function copyVal(el) {
  const text = el.textContent.trim();
  navigator.clipboard.writeText(text).then(() => {
    el.classList.add("copied");
    const old = el.textContent;
    el.textContent = "✓ copied";
    setTimeout(() => { el.textContent = old; el.classList.remove("copied"); }, 900);
  });
}

// FY change hard-reloads (so server-rendered FY-specific rows are correct).
document.getElementById("fy-select").addEventListener("change", function () {
  const fy = this.value;
  const oi = document.getElementById("other-income").value || "0";
  const sal = document.getElementById("is-salaried").checked ? "1" : "0";
  window.location.href = `/tax?fy=${fy}&other_income=${oi}&is_salaried=${sal}`;
});
</script>
</body>
</html>
"""


# Inject slabs JSON into the JS template after the rest is composed.
def render_tax_page_v2(*, other_income: float = 0.0,  # noqa: D401
                       fy_start: int | None = None,
                       is_salaried: bool = True) -> str:
    """Public entry - substitutes the SLABS_JSON island too."""
    html_out = render_tax_page(
        other_income=other_income, fy_start=fy_start, is_salaried=is_salaried
    )
    slabs_payload = {
        fy: [[upper, rate] for upper, rate in slabs]
        for fy, slabs in SLABS_BY_FY.items()
    }
    return html_out.replace("__SLABS_JSON__", json.dumps(slabs_payload))


__all__ = ["render_tax_page_v2", "render_tax_api"]
