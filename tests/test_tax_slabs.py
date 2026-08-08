"""Tests for the Indian income-tax computation behind the dashboard /tax page.

These are the numbers a user copies into an ITR filing, so they are worth
pinning precisely. The most important test here is
``test_take_home_never_decreases``: it encodes *why* Section 87A marginal
relief exists, rather than asserting one hand-computed figure.

Run with:  python -m unittest tests.test_tax_slabs
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modes.dashboard.tax.slabs import (  # noqa: E402
    CESS_RATE, STD_DEDUCTION_SALARY, compute_tax, latest_known_fy,
    rebate_ceiling_for_fy, slabs_for_fy,
)

FY = 2025


def _tax(income: float, **kw) -> float:
    kw.setdefault("is_salaried", False)
    return compute_tax(fy_start=FY, other_income=income, intraday_net=0, **kw).total_tax


class SlabArithmeticTest(unittest.TestCase):
    def test_zero_income_pays_nothing(self):
        self.assertEqual(_tax(0), 0.0)

    def test_slab_boundaries_sum_correctly(self):
        # FY25-26 new regime, taxable 24L:
        #   4-8L @5% = 20,000, 8-12 @10% = 40,000, 12-16 @15% = 60,000,
        #   16-20 @20% = 80,000, 20-24 @25% = 100,000  ->  300,000 slab tax.
        r = compute_tax(fy_start=FY, other_income=24_00_000, intraday_net=0,
                        is_salaried=False)
        self.assertAlmostEqual(r.slab_tax, 3_00_000, places=2)
        self.assertAlmostEqual(r.total_tax, 3_00_000 * (1 + CESS_RATE), places=2)

    def test_marginal_rate_is_top_band(self):
        r = compute_tax(fy_start=FY, other_income=30_00_000, intraday_net=0,
                        is_salaried=False)
        self.assertEqual(r.marginal_rate, 0.30)

    def test_standard_deduction_only_for_salaried(self):
        salaried = compute_tax(fy_start=FY, other_income=20_00_000,
                               intraday_net=0, is_salaried=True)
        other = compute_tax(fy_start=FY, other_income=20_00_000,
                            intraday_net=0, is_salaried=False)
        self.assertEqual(salaried.standard_deduction, STD_DEDUCTION_SALARY)
        self.assertEqual(other.standard_deduction, 0.0)
        self.assertLess(salaried.total_tax, other.total_tax)


class Rebate87ATest(unittest.TestCase):
    """Regression cover for the marginal-relief fix (2026-08)."""

    def test_at_ceiling_tax_is_zero(self):
        ceiling = rebate_ceiling_for_fy(FY)
        self.assertEqual(_tax(ceiling), 0.0)

    def test_just_above_ceiling_pays_only_the_excess(self):
        # Without marginal relief this jumped to ~Rs.64,000 on Rs.10,000
        # of extra income.
        ceiling = rebate_ceiling_for_fy(FY)
        excess = 10_000
        tax = _tax(ceiling + excess)
        self.assertAlmostEqual(tax, excess * (1 + CESS_RATE), places=2)

    def test_relief_phases_out(self):
        # Once slab tax falls below the excess, relief is exhausted and
        # the full slab tax applies again.
        r = compute_tax(fy_start=FY, other_income=13_00_000, intraday_net=0,
                        is_salaried=False)
        self.assertEqual(r.rebate_87a, 0.0)
        self.assertAlmostEqual(r.total_tax, r.slab_tax * (1 + CESS_RATE), places=2)

    def test_no_cliff_above_the_ceiling(self):
        """Tax just above the ceiling may never exceed the excess earned.

        This is exactly what the s.87A proviso guarantees, and it is what
        the pre-fix code violated: at Rs.12.05L it charged ~Rs.63,000 of
        tax on Rs.5,000 of extra income.

        Cess sits *outside* marginal relief, so the ceiling is
        excess x (1 + cess) rather than the bare excess.
        """
        ceiling = rebate_ceiling_for_fy(FY)
        for excess in (1_000, 5_000, 10_000, 25_000, 50_000, 74_000):
            tax = _tax(ceiling + excess)
            self.assertLessEqual(
                tax, excess * (1 + CESS_RATE) + 1e-6,
                f"tax {tax:,.0f} exceeds excess {excess:,} at income "
                f"{ceiling + excess:,}",
            )

    def test_marginal_rate_never_exceeds_cess_adjusted_full_rate(self):
        """Across the relief band the marginal rate tops out at 1 + cess.

        Anything above that is a cliff, which is the failure mode this
        whole mechanism exists to prevent.
        """
        prev = None
        for income in range(11_50_000, 14_00_001, 5_000):
            tax = _tax(income)
            if prev is not None:
                d_income = income - prev[0]
                d_tax = tax - prev[1]
                self.assertLessEqual(
                    d_tax, d_income * (1 + CESS_RATE) + 1e-6,
                    f"marginal rate above {1 + CESS_RATE:.0%} between "
                    f"{prev[0]:,} and {income:,}",
                )
            prev = (income, tax)


class SpeculativeIncomeTest(unittest.TestCase):
    def test_intraday_profit_is_taxed_at_slab(self):
        base = compute_tax(fy_start=FY, other_income=20_00_000,
                           intraday_net=0, is_salaried=False)
        with_profit = compute_tax(fy_start=FY, other_income=20_00_000,
                                  intraday_net=1_00_000, is_salaried=False)
        self.assertGreater(with_profit.total_tax, base.total_tax)

    def test_intraday_loss_does_not_reduce_slab_tax(self):
        """Speculative losses may only offset speculative gains, so they
        must not shelter salary income."""
        base = compute_tax(fy_start=FY, other_income=20_00_000,
                           intraday_net=0, is_salaried=False)
        with_loss = compute_tax(fy_start=FY, other_income=20_00_000,
                                intraday_net=-5_00_000, is_salaried=False)
        self.assertEqual(with_loss.total_tax, base.total_tax)


class CapitalGainsTest(unittest.TestCase):
    def test_ltcg_exemption_applies(self):
        under = compute_tax(fy_start=FY, other_income=5_00_000, intraday_net=0,
                            capital_gains_long_term=1_00_000, is_salaried=False)
        self.assertEqual(under.total_tax, 0.0)

    def test_ltcg_above_exemption_is_taxed(self):
        over = compute_tax(fy_start=FY, other_income=5_00_000, intraday_net=0,
                           capital_gains_long_term=3_00_000, is_salaried=False)
        # (3L - 1.25L exemption) @ 12.5%, plus cess.
        self.assertAlmostEqual(over.total_tax,
                               1_75_000 * 0.125 * (1 + CESS_RATE), places=2)

    def test_stcg_taxed_at_twenty_percent(self):
        r = compute_tax(fy_start=FY, other_income=5_00_000, intraday_net=0,
                        capital_gains_short_term=2_00_000, is_salaried=False)
        self.assertAlmostEqual(r.total_tax,
                               2_00_000 * 0.20 * (1 + CESS_RATE), places=2)


class FyLookupTest(unittest.TestCase):
    def test_unknown_future_fy_falls_back_to_latest(self):
        self.assertEqual(slabs_for_fy(2099), slabs_for_fy(latest_known_fy()))
        self.assertEqual(rebate_ceiling_for_fy(2099),
                         rebate_ceiling_for_fy(latest_known_fy()))

    def test_slabs_are_ordered_and_open_topped(self):
        for fy in (2024, 2025, 2026):
            slabs = slabs_for_fy(fy)
            bounds = [b for b, _ in slabs if b is not None]
            self.assertEqual(bounds, sorted(bounds), f"FY{fy} bounds unsorted")
            self.assertIsNone(slabs[-1][0], f"FY{fy} has no open top band")


if __name__ == "__main__":
    unittest.main()
