"""Dashboard-only US-stock page constants.

These settings used to live on `Config` in the top-level `config.py`,
but they have nothing to do with the intraday-trading strategy or the
Chan-framework rollout. They are read only by `modes/dashboard/us_*`
modules, so they live here to keep the trading `Config` surface lean.

Per the 2026-05-20 config simplification: knobs that are not in
`docs/TRADE_STRATEGY_ROLLOUT.md` should not pollute the trading Config.
"""

from __future__ import annotations

# US dashboard page sizing — per-stock USD ticket for the single-stock
# technical-analysis view and the suggested share count.
US_TICKET_AMOUNT: float = 500.0

# Default universe for the US scan (matches `_build_universe` keys in
# `modes/dashboard/us_analysis.py`).
US_SCAN_UNIVERSE: str = "US100"

# Cap on the number of accepted candidates the Claude overlay processes
# per US scan, mirroring `SWING_AI_MAX_CANDIDATES` for the Swing scan.
US_AI_MAX_CANDIDATES: int = 5
