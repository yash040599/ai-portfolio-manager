"""Tests for modes/analyze/scoring.py — the six-pillar factor scorecard.

The design property worth protecting here is graceful degradation: a
missing P/E must never score as a zero. `_blend` renormalises over the
inputs that are actually present, and `score()` renormalises over covered
pillars, so a half-populated holding is rated on what is known rather than
punished for what is not.

Run with:  python -m unittest tests.test_scoring
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modes.analyze.scoring import (  # noqa: E402
    PILLAR_WEIGHTS, RATING_BANDS, RISK_BANDS, _band, _blend, _clamp,
    _linear, score,
)

STRONG = {
    "return_1m_pct": 8.0, "return_3m_pct": 20.0, "return_6m_pct": 35.0,
    "return_12m_pct": 60.0, "momentum_12_1_pct": 55.0,
    "rs_3m_pct": 12.0, "rs_6m_pct": 15.0, "rs_12m_pct": 20.0,
    "volatility_30d_pct": 18.0, "volatility_90d_pct": 20.0,
    "max_drawdown_1y_pct": 12.0, "drawdown_from_high_pct": 3.0,
    "sharpe": 1.8, "sortino": 2.4, "beta": 0.9, "correlation": 0.6,
    "up_capture_pct": 115.0, "down_capture_pct": 80.0,
    "trend_state": "GOLDEN_CROSS", "above_sma_50": True,
    "above_sma_200": True, "days_since_cross": 40,
    "range_position_pct": 88.0, "atr_pct": 1.6,
    "avg_daily_turnover": 5e8, "volume_trend_ratio": 1.3,
    "rsi_daily": 58.0, "pe": 18.0, "dividend_yield_pct": 1.5,
    "sector": "IT", "market_cap_tier": "LARGE",
    "weight_pct": 6.0, "pnl_pct": 25.0,
}

WEAK = dict(STRONG, **{
    "return_1m_pct": -10.0, "return_3m_pct": -22.0, "return_6m_pct": -35.0,
    "return_12m_pct": -45.0, "momentum_12_1_pct": -40.0,
    "rs_3m_pct": -15.0, "rs_6m_pct": -18.0, "rs_12m_pct": -25.0,
    "volatility_30d_pct": 55.0, "volatility_90d_pct": 60.0,
    "max_drawdown_1y_pct": 55.0, "drawdown_from_high_pct": 48.0,
    "sharpe": -0.9, "sortino": -1.1, "beta": 1.9,
    "up_capture_pct": 70.0, "down_capture_pct": 140.0,
    "trend_state": "DEATH_CROSS", "above_sma_50": False,
    "above_sma_200": False, "range_position_pct": 8.0,
    "atr_pct": 5.5, "rsi_daily": 28.0, "pe": 80.0,
    "market_cap_tier": "SMALL", "avg_daily_turnover": 2e6,
})


class PrimitiveTest(unittest.TestCase):
    def test_clamp_bounds(self):
        self.assertEqual(_clamp(-10), 0.0)
        self.assertEqual(_clamp(150), 100.0)
        self.assertEqual(_clamp(42), 42)

    def test_linear_maps_endpoints(self):
        self.assertAlmostEqual(_linear(0, 0, 10), 0.0)
        self.assertAlmostEqual(_linear(10, 0, 10), 100.0)
        self.assertAlmostEqual(_linear(5, 0, 10), 50.0)

    def test_linear_handles_inverted_scales(self):
        """Passing worst > best is how "lower is better" metrics are scored."""
        self.assertAlmostEqual(_linear(10, 50, 10), 100.0)
        self.assertAlmostEqual(_linear(50, 50, 10), 0.0)

    def test_linear_passes_none_through(self):
        self.assertIsNone(_linear(None, 0, 10))

    def test_linear_degenerate_range_is_neutral(self):
        self.assertEqual(_linear(5, 10, 10), 50.0)

    def test_blend_ignores_missing_inputs(self):
        """The core property: a None input must not act like a zero."""
        both, cov_both = _blend([(80.0, 1.0), (80.0, 1.0)])
        one, cov_one = _blend([(80.0, 1.0), (None, 1.0)])
        self.assertAlmostEqual(both, 80.0)
        self.assertAlmostEqual(one, 80.0)
        self.assertAlmostEqual(cov_both, 1.0)
        self.assertAlmostEqual(cov_one, 0.5)

    def test_blend_with_no_data_returns_none(self):
        value, coverage = _blend([(None, 1.0), (None, 2.0)])
        self.assertIsNone(value)
        self.assertEqual(coverage, 0.0)

    def test_blend_is_weighted(self):
        value, _ = _blend([(100.0, 3.0), (0.0, 1.0)])
        self.assertAlmostEqual(value, 75.0)

    def test_band_picks_the_first_threshold_met(self):
        self.assertEqual(_band(90, RATING_BANDS), "STRONG BUY")
        self.assertEqual(_band(62, RATING_BANDS), "BUY")
        self.assertEqual(_band(45, RATING_BANDS), "HOLD")
        self.assertEqual(_band(0, RATING_BANDS), "SELL")

    def test_band_thresholds_are_descending(self):
        for bands in (RATING_BANDS, RISK_BANDS):
            values = [t for t, _ in bands]
            self.assertEqual(values, sorted(values, reverse=True))
            self.assertEqual(values[-1], 0.0, "bands must cover zero")


class ScoreTest(unittest.TestCase):
    def test_composite_is_bounded(self):
        for metrics in (STRONG, WEAK, {}):
            self.assertTrue(0.0 <= score(metrics).composite <= 100.0)

    def test_risk_score_is_bounded(self):
        for metrics in (STRONG, WEAK, {}):
            self.assertTrue(0.0 <= score(metrics).risk_score <= 100.0)

    def test_strong_outranks_weak(self):
        self.assertGreater(score(STRONG).composite, score(WEAK).composite)

    def test_weak_carries_more_risk(self):
        self.assertGreater(score(WEAK).risk_score, score(STRONG).risk_score)

    def test_full_data_gives_full_coverage(self):
        self.assertAlmostEqual(score(STRONG).coverage_pct, 100.0, delta=0.01)

    def test_empty_metrics_do_not_raise(self):
        card = score({})
        self.assertIsInstance(card.rating, str)
        self.assertLess(card.coverage_pct, 100.0)

    def test_missing_valuation_does_not_score_as_zero(self):
        """Losing a whole pillar should lower coverage and re-weight the
        rest, not drag the composite towards zero.

        Note coverage is measured per PILLAR, not per input: the valuation
        pillar still counts as covered on dividend yield alone, so both of
        its inputs have to go for the pillar to drop out.
        """
        without_valuation = dict(STRONG)
        without_valuation.pop("pe")
        without_valuation.pop("dividend_yield_pct")

        full = score(STRONG)
        partial = score(without_valuation)

        self.assertLess(partial.coverage_pct, full.coverage_pct)
        # Valuation carries 14 of 100 points, so coverage should drop by that.
        self.assertAlmostEqual(partial.coverage_pct, 100.0 - 14.0, delta=0.01)
        # The composite must stay in the same neighbourhood, not collapse.
        self.assertGreater(partial.composite, full.composite - 15.0)

    def test_dropping_one_valuation_input_keeps_the_pillar(self):
        without_pe = dict(STRONG)
        without_pe.pop("pe")
        self.assertAlmostEqual(score(without_pe).coverage_pct, 100.0, delta=0.01)

    def test_rating_matches_the_displayed_composite(self):
        """The card prints a rounded composite, so the band must be taken
        on the rounded value or the number and the label disagree."""
        for metrics in (STRONG, WEAK, {}):
            card = score(metrics)
            self.assertEqual(card.rating, _band(round(card.composite), RATING_BANDS))

    def test_risk_grade_matches_the_displayed_risk_score(self):
        for metrics in (STRONG, WEAK):
            card = score(metrics)
            self.assertEqual(card.risk_grade,
                             _band(round(card.risk_score), RISK_BANDS))

    def test_conviction_is_low_without_data(self):
        self.assertEqual(score({}).conviction, "Low")

    def test_conviction_is_high_on_a_decisive_full_card(self):
        self.assertEqual(score(STRONG).conviction, "High")

    def test_pillar_weights_are_exposed_and_sum_sensibly(self):
        card = score(STRONG)
        self.assertEqual(len(card.pillars), len(PILLAR_WEIGHTS))
        self.assertAlmostEqual(sum(p.weight for p in card.pillars),
                               sum(PILLAR_WEIGHTS.values()), places=6)

    def test_to_dict_is_serialisable(self):
        import json
        json.dumps(score(STRONG).to_dict())


class PositionSizingTest(unittest.TestCase):
    def test_oversized_position_scores_worse_than_balanced(self):
        """Position fit exists so a good stock held at 25% of the book is
        still flagged; concentration must cost points."""
        balanced = score(dict(STRONG, weight_pct=5.0))
        concentrated = score(dict(STRONG, weight_pct=30.0))
        self.assertGreater(balanced.composite, concentrated.composite)

    def test_concentration_raises_risk(self):
        balanced = score(dict(STRONG, weight_pct=5.0))
        concentrated = score(dict(STRONG, weight_pct=30.0))
        self.assertGreaterEqual(concentrated.risk_score, balanced.risk_score)


if __name__ == "__main__":
    unittest.main()
