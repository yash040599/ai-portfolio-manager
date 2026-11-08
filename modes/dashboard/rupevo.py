"""Links from the personal dashboard to Rupevo, its public companion site.

The dashboard is personal: it reads one person's Zerodha account, so it
needs a broker login. Rupevo (https://yash040599.github.io/Rupevo/) is the
login-free, general-information side of the same research: market-wide
rankings built on this project's scoring engines, a direct-plan
mutual-fund comparison and tax tools for foreign RSUs. Pages link to the
matching Rupevo page so anyone without an account can still try it.

Privacy contract: every Rupevo address is a fixed string defined here.
Never build one from personal data (held symbols, amounts, folios or fund
codes). Links open in a new tab without a referrer.
"""

from __future__ import annotations

from dataclasses import dataclass
import html


RUPEVO_URL = "https://yash040599.github.io/Rupevo/"

_NEW_TAB = 'target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer"'
_EXTERNAL = ('<span class="rv-ext" aria-hidden="true">&#8599;</span>'
             '<span class="rv-sr"> (opens in a new tab)</span>')
_MARK = '<span class="rv-mark" aria-hidden="true">R</span>'


@dataclass(frozen=True)
class RupevoPage:
    """One public Rupevo destination, addressed relative to `RUPEVO_URL`."""

    label: str
    path: str
    summary: str

    @property
    def url(self) -> str:
        return RUPEVO_URL + self.path


PAGES: dict[str, RupevoPage] = {
    "home": RupevoPage(
        "Explore Rupevo", "",
        "Market rankings, fund comparisons and tax tools, explained"),
    "india": RupevoPage(
        "Nifty 100 ranking", "india/",
        "Daily technical ranking of the Nifty 100: setups, scores, risk grades and 52-week dips"),
    "us": RupevoPage(
        "US stock ranking", "us/",
        "The NASDAQ-100 and the NYSE's 100 largest US companies on a long-term scorecard"),
    "mf": RupevoPage(
        "Fund comparison", "mf/#compare",
        "Every direct-plan fund ranked against the funds that do the same job"),
    "mf_plan": RupevoPage(
        "SIP split by age", "mf/#plan",
        "A SIP split across fund categories by age and risk"),
    "mf_lumpsum": RupevoPage(
        "Lump-sum check", "mf/#lumpsum",
        "Each index's P/E against its own history"),
    "tax": RupevoPage(
        "Tax tools", "tax/",
        "Step-by-step help with the trickier parts of an Indian tax return"),
    "tax_rsu": RupevoPage(
        "Foreign RSU tax", "tax/rsu/",
        "Vesting, US dividends, sales and Schedule FA for a foreign employer's RSUs"),
}


@dataclass(frozen=True)
class RupevoCallout:
    """A page-specific invitation: why Rupevo helps there, and where to go."""

    title: str
    text: str
    pages: tuple[str, ...]  # keys into PAGES; the first is the main action


CALLOUTS: dict[str, RupevoCallout] = {
    "login": RupevoCallout(
        "No Zerodha account? Try Rupevo",
        "This dashboard reads your own broker account, so it needs a Zerodha "
        "login. Rupevo is its free public companion: market-wide rankings built "
        "on this project's scoring engines, a mutual-fund comparison and tax "
        "tools, with no login and nothing to install.",
        ("home", "india", "mf", "us"),
    ),
    "portfolio": RupevoCallout(
        "See the whole Nifty 100, not just your holdings",
        "Rupevo ranks every Nifty 100 stock daily, with technical setups, scores, "
        "risk grades and 52-week dips. Use it to look beyond the stocks you own, "
        "or share it with someone who does not use this tool.",
        ("india",),
    ),
    "swing": RupevoCallout(
        "A public view of this scan",
        "Rupevo publishes a daily Nifty 100 ranking built on this swing scanner's "
        "engine. It is screener-style: scores and grades only, with no entry, stop "
        "or target levels and no buy or sell calls.",
        ("india",),
    ),
    "mf": RupevoCallout(
        "Which fund house? Compare on Rupevo",
        "Rupevo ranks every direct-plan equity, gold and silver fund against the "
        "funds that do the same job, suggests a SIP split by age and risk, and "
        "checks whether index valuations look cheap or expensive for a lump sum.",
        ("mf", "mf_plan", "mf_lumpsum"),
    ),
    "us": RupevoCallout(
        "Rank the wider US market",
        "Rupevo applies this page's long-term scorecard to the NASDAQ-100 and the "
        "100 largest NYSE companies: quality, valuation against the sector, growth, "
        "momentum, financial strength and risk.",
        ("us",),
    ),
    "tax": RupevoCallout(
        "Foreign RSUs and Schedule FA",
        "This page projects tax on your trades. For a foreign employer's RSUs "
        "(vesting, US dividends and Form 67, sales under the 24-month rule, and "
        "Schedule FA), Rupevo has step-by-step tools. Broker files are read in "
        "your browser and never uploaded.",
        ("tax_rsu", "tax"),
    ),
}


def _link(page: RupevoPage, cls: str, label: str | None = None) -> str:
    return (
        f'<a class="{cls}" href="{html.escape(page.url)}" {_NEW_TAB} '
        f'title="{html.escape(page.summary)}">'
        f'{html.escape(label or page.label)}{_EXTERNAL}</a>'
    )


def rupevo_nav_link() -> str:
    """Shared-navigation entry, rendered on every dashboard page."""
    home = PAGES["home"]
    return (
        f'<a class="nav-rupevo" href="{html.escape(home.url)}" {_NEW_TAB} '
        'title="Rupevo: free public rankings, fund comparison and tax tools. '
        'No login. Opens in a new tab.">'
        f'{_MARK}Rupevo{_EXTERNAL}</a>'
    )


def rupevo_card(context: str) -> str:
    """Page-specific Rupevo card; `context` is a key of `CALLOUTS`."""
    callout = CALLOUTS[context]
    actions = "".join(
        _link(PAGES[key], "rv-cta" if index == 0 else "rv-alt")
        for index, key in enumerate(callout.pages)
    )
    return (
        f'<section class="rv-card" data-rupevo="{html.escape(context)}" '
        'data-privacy-public aria-label="Rupevo, the public companion site">'
        '<div class="rv-copy">'
        f'<div class="rv-eyebrow">{_MARK}Rupevo &middot; free, no login</div>'
        f'<div class="rv-title">{html.escape(callout.title)}</div>'
        f'<div class="rv-text">{html.escape(callout.text)}</div>'
        '</div>'
        f'<div class="rv-actions">{actions}</div>'
        '<div class="rv-note">Public, educational information, not investment '
        'or tax advice. Opens in a new tab; nothing from this dashboard is '
        'sent to Rupevo.</div>'
        '</section>'
    )


def rupevo_login_hint() -> str:
    """One-line pointer for HQ's "Zerodha not connected" card."""
    return (
        '<p class="rv-hint" data-privacy-public>No Zerodha account, or showing '
        f'someone around? {_link(PAGES["home"], "rv-inline", "Try Rupevo")} '
        '&mdash; free public rankings, fund comparison and tax tools, no login.</p>'
    )


def rupevo_tile() -> str:
    """HQ tool tile; matches the markup of the internal tiles."""
    home = PAGES["home"]
    return (
        f'<a class="tile k-rv" href="{html.escape(home.url)}" {_NEW_TAB} '
        'title="Opens Rupevo in a new tab">'
        '<strong>Rupevo</strong>'
        '<span>Free public site: Nifty 100 and US rankings, fund comparison '
        'and tax tools. No login, easy to share.</span>'
        f'<em>Open{_EXTERNAL}</em></a>'
    )


def rupevo_css() -> str:
    """Component styles; `theme_css()` includes them on every page."""
    return r"""
/* ── Rupevo (public companion site) ─────────────────────────── */
.rv-mark { display: inline-grid; place-items: center; flex: none;
           width: 16px; height: 16px; border-radius: 5px;
           font-size: 10px; font-weight: 800; line-height: 1; color: #fff;
           background: linear-gradient(135deg, #2f5fe0, #6d5ae0); }
.rv-ext { margin-left: 3px; font-size: .9em; }
.rv-sr { position: absolute; width: 1px; height: 1px; overflow: hidden;
         clip: rect(0 0 0 0); white-space: nowrap; }
nav.topnav a.nav-rupevo { display: inline-flex; align-items: center; gap: 6px;
                          margin-left: 4px; border: 1px solid var(--line); }
.rv-card { position: relative; overflow: hidden; display: flex; flex-wrap: wrap;
           align-items: center; gap: 12px 20px; margin: 22px 0 16px;
           padding: 17px 18px 14px; color: var(--fg); background: var(--card);
           border: 1px solid var(--line); border-radius: var(--radius);
           box-shadow: var(--shadow-sm); }
.rv-card::before { content: ""; position: absolute; top: 0; left: 0; right: 0;
                   height: 3px; background: linear-gradient(90deg, #2f5fe0, #6d5ae0); }
.rv-copy { flex: 1 1 360px; min-width: 0; }
.rv-eyebrow { display: flex; align-items: center; gap: 7px; font-size: 11px;
              font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
              color: var(--muted); }
.rv-title { margin: 6px 0 4px; font-size: 16px; font-weight: 700; }
.rv-text { max-width: 78ch; font-size: 13px; line-height: 1.5; color: var(--fg-2); }
.rv-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.rv-card a.rv-cta, .rv-card a.rv-alt {
  display: inline-flex; align-items: center; padding: 8px 14px;
  border-radius: var(--radius-sm); font-size: 13px; font-weight: 600;
  text-decoration: none; white-space: nowrap;
  transition: filter .15s ease, background .12s ease; }
.rv-card a.rv-cta { color: var(--accent-fg); border: 1px solid transparent;
  background: linear-gradient(180deg, var(--accent),
              color-mix(in srgb, var(--accent) 86%, #000)); }
.rv-card a.rv-cta:hover { filter: brightness(1.06); }
.rv-card a.rv-alt { color: var(--fg); background: var(--card);
                    border: 1px solid var(--line-strong); }
.rv-card a.rv-alt:hover { background: var(--soft); }
.rv-note { flex-basis: 100%; font-size: 11.5px; color: var(--muted); }
.rv-hint { margin: 12px 0 0; font-size: 12.5px; color: var(--muted); }
.rv-hint a.rv-inline { font-weight: 600; text-decoration: none; }
.rv-hint a.rv-inline:hover { text-decoration: underline; }
.tile.k-rv::after { background: linear-gradient(90deg, #0b57d0, #6d5ae0); }
"""


__all__ = [
    "CALLOUTS",
    "PAGES",
    "RUPEVO_URL",
    "RupevoCallout",
    "RupevoPage",
    "rupevo_card",
    "rupevo_css",
    "rupevo_login_hint",
    "rupevo_nav_link",
    "rupevo_tile",
]
