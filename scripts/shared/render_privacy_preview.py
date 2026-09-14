"""Render dashboard pages to static files for offline privacy review.

No server, no broker calls beyond the pages' own snapshot-first paths.
Output lands in reports/, which is gitignored in the code repository.
"""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def render_all(destination: Path) -> list[Path]:
    from modes.dashboard.dryrun_page import render_dryrun_page
    from modes.dashboard.home_page import render_home_page
    from modes.dashboard.mf_page import render_mf_page
    from modes.dashboard.portfolio_page import render_portfolio_page
    from modes.dashboard.swing_page import render_swing_page
    from modes.dashboard.tax_page import render_tax_page_v2
    from modes.dashboard.theory_page import PAGES, render_theory_page
    from modes.dashboard.us_page import render_us_page

    renderers = {
        "home": render_home_page,
        "portfolio": render_portfolio_page,
        "mf": render_mf_page,
        "swing": render_swing_page,
        "us": render_us_page,
        "tax": render_tax_page_v2,
        "dryrun": render_dryrun_page,
    }
    renderers.update({f"theory_{slug}": (lambda s=slug: render_theory_page(s)) for slug in PAGES})

    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, render in renderers.items():
        path = destination / f"{name}.html"
        try:
            path.write_text(render(), encoding="utf-8")
        except Exception as error:  # a dead book must not stop the review
            path.write_text(f"<!doctype html><p>render failed: {error!r}</p>", encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for path in render_all(PROJECT_ROOT / "reports" / "dashboard" / "privacy_review"):
        print(path.relative_to(PROJECT_ROOT).as_posix())
