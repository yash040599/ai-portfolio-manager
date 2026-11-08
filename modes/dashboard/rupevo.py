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
    """Navigation-rail entry, rendered on every dashboard page."""
    home = PAGES["home"]
    return (
        f'<a class="nav-item nav-rupevo" href="{html.escape(home.url)}" {_NEW_TAB} '
        'title="Rupevo: free public rankings, fund comparison and tax tools. '
        'No login. Opens in a new tab.">'
        f'<span class="nav-icon">{_MARK}</span>'
        f'<span class="nav-label">Rupevo{_EXTERNAL}</span></a>'
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
        '<span class="rv-icon" aria-hidden="true">R</span>'
        '<div class="rv-copy">'
        '<div class="rv-eyebrow">Rupevo &middot; free, no login</div>'
        f'<div class="rv-title">{html.escape(callout.title)}</div>'
        f'<div class="rv-text">{html.escape(callout.text)}</div>'
        '<div class="rv-note">Public, educational information, not investment '
        'or tax advice. Opens in a new tab; nothing from this dashboard is '
        'sent to Rupevo.</div>'
        '</div>'
        f'<div class="rv-actions">{actions}</div>'
        '</section>'
    )


def rupevo_login_hint() -> str:
    """One-line pointer for HQ's "Zerodha not connected" card."""
    return (
        '<p class="rv-hint" data-privacy-public>No Zerodha account, or showing '
        f'someone around? {_link(PAGES["home"], "rv-inline", "Try Rupevo")} '
        '&mdash; free public rankings, fund comparison and tax tools, no login.</p>'
    )


def rupevo_footer_link() -> str:
    """HQ footer pointer: Rupevo is the version to share with other people."""
    return (
        '<span class="rv-foot" data-privacy-public>Showing this to someone? '
        f'{_link(PAGES["home"], "rv-inline", "Rupevo")} is the free public '
        'version: no login, nothing to install.</span>'
    )


def rupevo_css() -> str:
    """Component styles; `theme_css()` includes them on every page."""
    return r"""
/* ── Rupevo (public companion site) ─────────────────────────── */
.rv-mark { display: inline-grid; place-items: center; flex: none;
           width: 24px; height: 24px; border-radius: 8px;
           font-family: var(--font-display); font-size: 13px; font-weight: 700;
           line-height: 1; color: var(--accent-fg); background: var(--accent); }
.rv-ext { margin-left: 2px; font-size: .9em; }
.rv-sr { position: absolute; width: 1px; height: 1px; overflow: hidden;
         clip: rect(0 0 0 0); white-space: nowrap; }
.rv-card { display: grid; grid-template-columns: auto minmax(0, 1fr) auto;
           align-items: center; gap: 4px 16px; margin: 4px 0 16px;
           padding: 16px 20px; color: var(--fg); background: var(--card);
           border: 1px solid var(--line); border-radius: var(--radius); }
.rv-icon { display: grid; place-items: center; width: 40px; height: 40px;
           border-radius: 12px; background: var(--tonal); color: var(--on-tonal);
           font-family: var(--font-display); font-size: 18px; font-weight: 500; }
.rv-copy { min-width: 0; }
.rv-eyebrow { font-size: 12px; line-height: 16px; font-weight: 500; color: var(--muted); }
.rv-title { margin: 2px 0; font-family: var(--font-display); font-size: 16px;
            line-height: 22px; font-weight: 500; }
.rv-text { max-width: 90ch; font-size: 13.5px; line-height: 20px; color: var(--fg-2); }
.rv-note { margin-top: 4px; font-size: 12px; line-height: 16px; color: var(--muted); }
.rv-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.rv-card a.rv-cta, .rv-card a.rv-alt {
  display: inline-flex; align-items: center; min-height: 36px; padding: 0 18px;
  border-radius: var(--radius-pill); font-size: 13.5px; font-weight: 500;
  text-decoration: none; white-space: nowrap;
  transition: box-shadow .15s ease, background-color .15s ease; }
.rv-card a.rv-cta { color: var(--accent-fg); background: var(--accent);
                    border: 1px solid transparent; }
.rv-card a.rv-cta:hover { box-shadow: var(--shadow-sm); }
.rv-card a.rv-alt { color: var(--accent); background: transparent;
                    border: 1px solid var(--line-strong); }
.rv-card a.rv-alt:hover { background: var(--accent-soft); }
.rv-hint { margin: 12px 0 0; font-size: 12.5px; color: var(--muted); }
.rv-hint a.rv-inline, .rv-foot a.rv-inline { font-weight: 500; text-decoration: none; }
.rv-hint a.rv-inline:hover, .rv-foot a.rv-inline:hover { text-decoration: underline; }
@media (max-width: 719px) {
  .rv-card { grid-template-columns: auto minmax(0, 1fr); align-items: start; }
  .rv-actions { grid-column: 1 / -1; justify-content: flex-start; margin-top: 8px; }
}
"""


__all__ = [
    "CALLOUTS",
    "PAGES",
    "RUPEVO_URL",
    "RupevoCallout",
    "RupevoPage",
    "rupevo_card",
    "rupevo_css",
    "rupevo_footer_link",
    "rupevo_login_hint",
    "rupevo_nav_link",
]
