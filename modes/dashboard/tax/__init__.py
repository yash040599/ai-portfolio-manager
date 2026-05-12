"""Tax module — versioned slabs + per-FY summary + projection engine.

See ``modes/dashboard/docs/DASHBOARD_ROADMAP.md`` items D16-D17 for the design.
"""
from modes.dashboard.tax.slabs import (  # noqa: F401
    CESS_RATE,
    REBATE_CEILING_BY_FY,
    SLABS_BY_FY,
    STD_DEDUCTION_SALARY,
    TaxComputation,
    compute_tax,
    latest_known_fy,
    rebate_ceiling_for_fy,
    slabs_for_fy,
)
from modes.dashboard.tax.fy_summary import (  # noqa: F401
    FYSummary,
    compute_fy_summary,
)
