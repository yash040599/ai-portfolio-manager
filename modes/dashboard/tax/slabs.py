"""Versioned Indian income-tax slabs (new regime, default from FY 2023-24).

Single source of truth for the dashboard tax page. When the Union Budget
changes slabs (typically Feb), add a new entry to ``SLABS_BY_FY`` and
``REBATE_BY_FY`` keyed by the FY-start year. The lookup helper falls
back to the latest known FY if an unknown future FY is requested.

References:
- Union Budget 2025 (effective FY 2025-26 onwards).
- Section 87A rebate up to total income Rs.12,00,000 (new regime).
- Health & Education cess: 4% on total tax + surcharge.
- Standard deduction (salaried only): Rs.75,000.
- Surcharge bands (new regime, capped at 25% by Budget 2023):
    50L  - 1Cr  -> 10%
    1Cr  - 2Cr  -> 15%
    2Cr  - 5Cr  -> 25%
    > 5Cr       -> 25% (cap)

This module is pure data + pure functions. No DB, no IO.
"""

from __future__ import annotations

from dataclasses import dataclass


# Slab = (upper_bound_inclusive_or_None_for_open_top, marginal_rate_fraction).
# An entry of (None, 0.30) means "above the previous bound, taxed at 30%".
Slab = tuple[float | None, float]


# Budget 2025 slabs — effective FY 2025-26 onwards.
_SLABS_2025_26: list[Slab] = [
    (4_00_000,   0.00),
    (8_00_000,   0.05),
    (12_00_000,  0.10),
    (16_00_000,  0.15),
    (20_00_000,  0.20),
    (24_00_000,  0.25),
    (None,       0.30),
]

# Budget 2024 slabs — effective FY 2024-25 only.
_SLABS_2024_25: list[Slab] = [
    (3_00_000,   0.00),
    (7_00_000,   0.05),
    (10_00_000,  0.10),
    (12_00_000,  0.15),
    (15_00_000,  0.20),
    (None,       0.30),
]


SLABS_BY_FY: dict[int, list[Slab]] = {
    2024: _SLABS_2024_25,
    2025: _SLABS_2025_26,
    2026: _SLABS_2025_26,  # unchanged in Budget 2026 (placeholder until announced)
}


# Section 87A rebate ceiling — total income up to this is fully rebated.
REBATE_CEILING_BY_FY: dict[int, float] = {
    2024: 7_00_000,
    2025: 12_00_000,
    2026: 12_00_000,
}


CESS_RATE = 0.04          # 4% Health & Education cess
STD_DEDUCTION_SALARY = 75_000  # Budget 2024 raised this from 50K


# Surcharge thresholds (new regime, capped at 25%).
_SURCHARGE_BANDS: list[tuple[float, float]] = [
    (50_00_000,    0.00),
    (1_00_00_000,  0.10),
    (2_00_00_000,  0.15),
    (None,         0.25),
]


@dataclass(frozen=True)
class TaxComputation:
    """Result of running ``compute_tax``."""
    fy_start: int
    gross_total_income: float          # all heads added together (pre-deduction)
    standard_deduction: float          # 75K if salaried else 0
    taxable_income: float              # gross - std deduction
    slab_tax: float                    # before rebate / surcharge / cess
    rebate_87a: float                  # negative deduction from slab_tax
    tax_after_rebate: float
    surcharge: float
    cess: float
    total_tax: float                   # rounded final liability
    marginal_rate: float               # rate on the LAST rupee of taxable income
    effective_rate: float              # total_tax / gross_total_income
    slabs_used: list[Slab]


def latest_known_fy() -> int:
    return max(SLABS_BY_FY.keys())


def slabs_for_fy(fy_start: int) -> list[Slab]:
    if fy_start in SLABS_BY_FY:
        return SLABS_BY_FY[fy_start]
    # fall back to the latest known FY
    return SLABS_BY_FY[latest_known_fy()]


def rebate_ceiling_for_fy(fy_start: int) -> float:
    if fy_start in REBATE_CEILING_BY_FY:
        return REBATE_CEILING_BY_FY[fy_start]
    return REBATE_CEILING_BY_FY[latest_known_fy()]


def _slab_tax(taxable: float, slabs: list[Slab]) -> tuple[float, float]:
    """Return (tax, marginal_rate) for ``taxable`` under ``slabs``."""
    if taxable <= 0:
        return 0.0, 0.0
    tax = 0.0
    prev = 0.0
    marginal = 0.0
    for upper, rate in slabs:
        if upper is None or taxable <= upper:
            tax += (taxable - prev) * rate
            marginal = rate
            return tax, marginal
        tax += (upper - prev) * rate
        prev = upper
        marginal = rate
    return tax, marginal


def _surcharge(tax: float, taxable: float) -> float:
    rate = 0.0
    for upper, r in _SURCHARGE_BANDS:
        if upper is None or taxable <= upper:
            rate = r
            break
    return tax * rate


def compute_tax(
    *,
    fy_start: int,
    other_income: float,
    intraday_net: float,
    capital_gains_short_term: float = 0.0,
    capital_gains_long_term: float = 0.0,
    is_salaried: bool = True,
) -> TaxComputation:
    """Compute estimated total tax under the new regime.

    ``other_income`` is everything outside intraday + listed-equity capital
    gains (salary gross, bank interest, rent, etc.). Intraday net is added
    as speculative business income (fully taxed at slab). Capital gains
    are passed through here for completeness — they are taxed at flat
    rates outside slab in reality, so we add them to gross income for
    surcharge/cess purposes only and then layer the flat-rate tax on top.
    For now we keep this simple: STCG (eq) at 20% (Budget 2024 raised
    from 15%), LTCG (eq) at 12.5% over Rs.1.25L exemption.
    """

    other_income = max(0.0, float(other_income))
    intraday_net = float(intraday_net)
    stcg_eq = max(0.0, float(capital_gains_short_term))
    ltcg_eq = max(0.0, float(capital_gains_long_term))

    slabs = slabs_for_fy(fy_start)
    rebate_ceiling = rebate_ceiling_for_fy(fy_start)

    std_deduction = STD_DEDUCTION_SALARY if is_salaried else 0.0
    # Speculative loss can only offset speculative profit; here we
    # treat negative intraday_net as zero contribution to slab income
    # but surface the loss separately in fy_summary.
    speculative_income = max(0.0, intraday_net)
    gross = other_income + speculative_income
    taxable = max(0.0, gross - std_deduction)

    slab_tax, marginal = _slab_tax(taxable, slabs)

    rebate = 0.0
    if taxable <= rebate_ceiling:
        rebate = slab_tax
    tax_after_rebate = slab_tax - rebate

    # Capital-gains flat-rate tax (Budget 2024 rates, applied from 23-Jul-2024).
    # We attach them OUTSIDE slab tax; rebate 87A does NOT cover them.
    ltcg_taxable = max(0.0, ltcg_eq - 1_25_000)
    cg_tax = stcg_eq * 0.20 + ltcg_taxable * 0.125

    pre_cess = tax_after_rebate + cg_tax
    surcharge = _surcharge(pre_cess, taxable + stcg_eq + ltcg_eq)
    cess = (pre_cess + surcharge) * CESS_RATE
    total_tax = round(pre_cess + surcharge + cess, 2)

    effective = (total_tax / gross) if gross > 0 else 0.0

    return TaxComputation(
        fy_start=fy_start,
        gross_total_income=round(gross, 2),
        standard_deduction=std_deduction,
        taxable_income=round(taxable, 2),
        slab_tax=round(slab_tax, 2),
        rebate_87a=round(rebate, 2),
        tax_after_rebate=round(tax_after_rebate, 2),
        surcharge=round(surcharge, 2),
        cess=round(cess, 2),
        total_tax=total_tax,
        marginal_rate=marginal,
        effective_rate=effective,
        slabs_used=slabs,
    )


__all__ = [
    "Slab",
    "SLABS_BY_FY",
    "REBATE_CEILING_BY_FY",
    "CESS_RATE",
    "STD_DEDUCTION_SALARY",
    "TaxComputation",
    "compute_tax",
    "slabs_for_fy",
    "rebate_ceiling_for_fy",
    "latest_known_fy",
]
