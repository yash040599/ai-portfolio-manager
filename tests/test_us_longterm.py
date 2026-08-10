"""Tests for modes/us/longterm.py — the US buy-and-hold scorecard.

Two properties matter most here.

1. **Graceful degradation.** Yahoo's fundamental coverage is patchy, so a
   missing EV/EBITDA must never be scored as a zero. `_blend`
   renormalises over the inputs present, `score_long_term` renormalises
   over covered pillars, and a thinly-covered name has its rating capped
   so a two-field model cannot masquerade as high conviction.

2. **Horizon.** This model must not drift back into swing behaviour. The
   action vocabulary is accumulation-based, and a strong business on a
   weak chart must still outrank a weak business on a strong chart.

Run with:  python -m unittest tests.test_us_longterm
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modes.us.longterm import (  # noqa: E402
    PILLAR_WEIGHTS, SECTOR_PE_MEDIAN, _blend, _linear, action_for,
    score_long_term,
)


def _series(start: float, days: int, daily_drift: float) -> list[float]:
    """Deterministic price path — no randomness in tests."""
    out = [start]
    for _ in range(days - 1):
        out.append(out[-1] * (1.0 + daily_drift))
    return out


QUALITY_CO = {
    "sector": "Technology",
    "trailing_pe": 24.0, "ev_to_ebitda": 14.0, "price_to_book": 6.0,
    "fcf_yield_pct": 4.5, "roe_pct": 32.0, "roa_pct": 15.0,
    "gross_margin_pct": 68.0, "operating_margin_pct": 34.0,
    "net_margin_pct": 28.0, "fcf_margin_pct": 25.0,
    "debt_to_equity": 0.25, "current_ratio": 2.1,
    "total_cash": 90.0, "total_debt": 40.0,
    "revenue_growth_pct": 18.0, "earnings_growth_pct": 25.0,
    "beta": 0.95,
}

WEAK_CO = {
    "sector": "Technology",
    "trailing_pe": 70.0, "ev_to_ebitda": 34.0, "price_to_book": 14.0,
    "fcf_yield_pct": 0.2, "roe_pct": 2.0, "roa_pct": 0.5,
    "gross_margin_pct": 18.0, "operating_margin_pct": 1.0,
    "net_margin_pct": 0.5, "fcf_margin_pct": 0.4,
    "debt_to_equity": 2.8, "current_ratio": 0.8,
    "total_cash": 10.0, "total_debt": 120.0,
    "revenue_growth_pct": -8.0, "earnings_growth_pct": -20.0,
    "beta": 1.9,
}


class TestPrimitives(unittest.TestCase):

    def test_linear_maps_both_directions(self):
        self.assertAlmostEqual(_linear(35.0, 0.0, 35.0), 100.0)
        self.assertAlmostEqual(_linear(0.0, 0.0, 35.0), 0.0)
        # Lower-is-better: pass worst > best.
        self.assertAlmostEqual(_linear(0.1, 2.5, 0.1), 100.0)
        self.assertAlmostEqual(_linear(2.5, 2.5, 0.1), 0.0)

    def test_linear_clamps_outside_range(self):
        self.assertEqual(_linear(999.0, 0.0, 35.0), 100.0)
        self.assertEqual(_linear(-999.0, 0.0, 35.0), 0.0)

    def test_linear_none_in_none_out(self):
        self.assertIsNone(_linear(None, 0.0, 1.0))

    def test_blend_ignores_missing_and_renormalises(self):
        score, covered = _blend([(80.0, 0.5), (None, 0.5)])
        self.assertAlmostEqual(score, 80.0)   # not 40.0
        self.assertAlmostEqual(covered, 0.5)

    def test_blend_all_missing(self):
        score, covered = _blend([(None, 1.0)])
        self.assertIsNone(score)
        self.assertEqual(covered, 0.0)

    def test_pillar_weights_sum_to_100(self):
        self.assertAlmostEqual(sum(PILLAR_WEIGHTS.values()), 100.0)


class TestScoring(unittest.TestCase):

    def setUp(self):
        self.up = _series(100.0, 600, 0.0008)
        self.flat = _series(100.0, 600, 0.0)
        self.down = _series(100.0, 600, -0.0008)

    def test_quality_business_outranks_weak_one(self):
        good = score_long_term(QUALITY_CO, self.up, self.flat)
        bad = score_long_term(WEAK_CO, self.up, self.flat)
        self.assertGreater(good.composite, bad.composite)
        self.assertIn(good.rating, ("HIGH CONVICTION", "ACCUMULATE"))

    def test_business_quality_outweighs_chart(self):
        """A good business on a poor chart must beat the reverse.

        This is the property that separates this model from the swing
        scorer it replaced — momentum is 16 of 100, not the whole score.
        """
        good_co_bad_chart = score_long_term(QUALITY_CO, self.down, self.flat)
        weak_co_good_chart = score_long_term(WEAK_CO, self.up, self.flat)
        self.assertGreater(good_co_bad_chart.composite,
                           weak_co_good_chart.composite)

    def test_missing_fundamentals_do_not_zero_the_score(self):
        chart_only = score_long_term({}, self.up, self.flat)
        self.assertGreater(chart_only.composite, 0.0)
        self.assertLess(chart_only.coverage_pct, 50.0)

    def test_thin_coverage_caps_the_rating(self):
        """Half a model must not produce a confident call."""
        chart_only = score_long_term({}, self.up, self.flat)
        self.assertNotIn(chart_only.rating, ("HIGH CONVICTION", "ACCUMULATE"))

    def test_full_coverage_reported(self):
        full = score_long_term(QUALITY_CO, self.up, self.flat)
        self.assertGreaterEqual(full.coverage_pct, 99.0)

    def test_valuation_is_relative_to_sector(self):
        """A bank on 14x is fair; the same 14x in software is cheap."""
        cheap_for_tech = dict(QUALITY_CO, sector="Technology", trailing_pe=14.0)
        fair_for_bank = dict(QUALITY_CO, sector="Financial Services",
                             trailing_pe=14.0)
        tech = score_long_term(cheap_for_tech, self.up, self.flat)
        bank = score_long_term(fair_for_bank, self.up, self.flat)
        self.assertEqual(tech.valuation_band, "CHEAP vs sector")
        self.assertEqual(bank.valuation_band, "FAIR vs sector")

    def test_every_sector_median_is_positive(self):
        for sector, median in SECTOR_PE_MEDIAN.items():
            self.assertGreater(median, 0, f"{sector} median must be positive")

    def test_high_beta_and_drawdown_raise_risk(self):
        calm = score_long_term(QUALITY_CO, self.flat, self.flat)
        wild = score_long_term(dict(QUALITY_CO, beta=2.4), self.flat, self.flat)
        self.assertGreater(wild.risk_score, calm.risk_score)

    def test_composite_stays_in_range(self):
        for fundamentals in (QUALITY_CO, WEAK_CO, {}):
            for closes in (self.up, self.flat, self.down):
                s = score_long_term(fundamentals, closes, self.flat)
                self.assertGreaterEqual(s.composite, 0.0)
                self.assertLessEqual(s.composite, 100.0)
                self.assertGreaterEqual(s.risk_score, 0.0)
                self.assertLessEqual(s.risk_score, 100.0)

    def test_short_history_does_not_crash(self):
        s = score_long_term(QUALITY_CO, [100.0, 101.0, 102.0], None)
        self.assertGreaterEqual(s.composite, 0.0)


class TestActions(unittest.TestCase):
    """Action vocabulary must stay long-horizon, never swing."""

    def setUp(self):
        self.up = _series(100.0, 600, 0.0008)
        self.flat = _series(100.0, 600, 0.0)

    def test_no_swing_vocabulary_leaks_through(self):
        banned = {"BUY_CANDIDATE", "WAIT", "NO_SETUP"}
        for fundamentals in (QUALITY_CO, WEAK_CO, {}):
            s = score_long_term(fundamentals, self.up, self.flat)
            for owned in (True, False):
                self.assertNotIn(action_for(s, owned=owned), banned)

    def test_strong_business_is_accumulate(self):
        s = score_long_term(QUALITY_CO, self.up, self.flat)
        self.assertEqual(action_for(s), "ACCUMULATE")

    def test_owned_and_unowned_differ_when_weak(self):
        s = score_long_term(WEAK_CO, self.flat, self.flat)
        self.assertEqual(action_for(s, owned=False), "AVOID")
        self.assertEqual(action_for(s, owned=True), "TRIM")


if __name__ == "__main__":
    unittest.main()
