"""Tests for shared/quant_metrics.py.

These metrics feed the portfolio scorecard and the swing conviction grades,
so a silent arithmetic error changes what the tool tells you to buy.

Most assertions here are invariants or closed-form results rather than
golden numbers, so they check the maths is *right* instead of merely
recording what it currently does.

Run with:  python -m unittest tests.test_quant_metrics
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import quant_metrics as qm  # noqa: E402


def candles(closes, high_mult=1.01, low_mult=0.99, volume=100_000):
    """Build OHLCV candles around a close series."""
    return [
        {"open": c, "high": c * high_mult, "low": c * low_mult,
         "close": c, "volume": volume}
        for c in closes
    ]


def compounding(start=100.0, daily=0.001, n=300):
    return [start * (1 + daily) ** i for i in range(n)]


class ExtractionTest(unittest.TestCase):
    def test_closes_of_skips_bad_rows(self):
        rows = [{"close": 10}, {"close": None}, {"close": "x"},
                {"close": 0}, {"close": 12}]
        self.assertEqual(qm.closes_of(rows), [10.0, 12.0])

    def test_series_accepts_candles_or_floats(self):
        self.assertEqual(qm._series([1.0, 2.0]), [1.0, 2.0])
        self.assertEqual(qm._series(candles([1.0, 2.0])), [1.0, 2.0])

    def test_daily_returns_length_and_value(self):
        rets = qm.daily_returns([100.0, 110.0, 121.0])
        self.assertEqual(len(rets), 2)
        for r in rets:
            self.assertAlmostEqual(r, 0.10, places=9)


class ReturnTest(unittest.TestCase):
    def test_period_return_is_exact(self):
        # 11 points -> lookback 10 spans the whole series.
        series = [100.0] * 1 + [0] * 0
        series = [100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109, 150.0]
        self.assertAlmostEqual(qm.period_return_pct(series, 10), 50.0, places=9)

    def test_period_return_needs_enough_history(self):
        self.assertIsNone(qm.period_return_pct([100.0, 101.0], 10))
        self.assertIsNone(qm.period_return_pct([100.0] * 20, 0))

    def test_momentum_12_1_excludes_the_last_month(self):
        """A spike confined to the final month must not move 12-1 momentum."""
        base = compounding(n=300)
        spiked = list(base)
        for i in range(len(spiked) - qm.LOOKBACK_1M, len(spiked)):
            spiked[i] *= 2.0
        self.assertAlmostEqual(qm.momentum_12_1_pct(base),
                               qm.momentum_12_1_pct(spiked), places=9)

    def test_relative_strength_is_the_difference(self):
        stock = [100.0 + i for i in range(80)]
        bench = [100.0 + i * 0.5 for i in range(80)]
        rs = qm.relative_strength_pct(stock, bench, 60)
        expected = (qm.period_return_pct(stock, 60)
                    - qm.period_return_pct(bench, 60))
        self.assertAlmostEqual(rs, expected, places=9)


class RiskTest(unittest.TestCase):
    def test_flat_series_has_zero_volatility(self):
        self.assertAlmostEqual(qm.annualised_volatility_pct([100.0] * 200),
                               0.0, places=9)

    def test_volatility_annualises_by_root_252(self):
        # Alternating +1%/-1% has a known daily stdev.
        closes = [100.0]
        for i in range(200):
            closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
        rets = qm.daily_returns(closes)
        expected = qm._stdev(rets) * math.sqrt(252) * 100
        self.assertAlmostEqual(qm.annualised_volatility_pct(closes, window=len(closes)),
                               expected, places=6)

    def test_max_drawdown_on_a_known_path(self):
        # 100 -> 120 -> 60 : worst peak-to-trough is 50%.
        closes = [100.0] * 25 + [120.0] + [60.0] + [90.0] * 5
        self.assertAlmostEqual(qm.max_drawdown_pct(closes), 50.0, places=6)

    def test_max_drawdown_is_never_negative(self):
        self.assertGreaterEqual(qm.max_drawdown_pct(compounding()), 0.0)

    def test_drawdown_from_high_is_zero_at_a_new_high(self):
        self.assertAlmostEqual(qm.drawdown_from_high_pct(compounding()),
                               0.0, places=9)

    def test_drawdown_from_high_measures_the_gap(self):
        closes = [100.0] * 30 + [200.0] + [150.0]
        self.assertAlmostEqual(qm.drawdown_from_high_pct(closes), 25.0, places=6)

    def test_sharpe_is_none_without_variance(self):
        self.assertIsNone(qm.sharpe_ratio([100.0] * 300))

    def test_sortino_needs_actual_downside_observations(self):
        """A series that never falls has no downside deviation to measure,
        so Sortino is undefined rather than infinite."""
        self.assertIsNone(qm.sortino_ratio(compounding(daily=0.002, n=300)))

    def test_sortino_exceeds_sharpe_when_upside_dominates(self):
        """Sortino only penalises downside, so a series whose volatility is
        mostly upside must score better on Sortino than on Sharpe."""
        import random
        random.seed(5)
        closes = [100.0]
        for _ in range(300):
            # Frequent small losses, occasional large gains.
            r = random.gauss(0.004, 0.004) if random.random() < 0.25 else -0.0005
            closes.append(closes[-1] * (1 + r))
        sharpe = qm.sharpe_ratio(closes)
        sortino = qm.sortino_ratio(closes)
        self.assertIsNotNone(sharpe)
        self.assertIsNotNone(sortino)
        self.assertGreater(sortino, sharpe)

    def test_short_history_returns_none_not_zero(self):
        short = [100.0, 101.0, 102.0]
        for fn in (qm.annualised_volatility_pct, qm.max_drawdown_pct,
                   qm.sharpe_ratio, qm.sortino_ratio):
            self.assertIsNone(fn(short), f"{fn.__name__} should return None")


class BenchmarkTest(unittest.TestCase):
    def test_identical_series_gives_beta_and_correlation_one(self):
        series = compounding(n=300)
        beta, corr = qm.beta_and_correlation(series, series)
        self.assertAlmostEqual(beta, 1.0, places=6)
        self.assertAlmostEqual(corr, 1.0, places=6)

    def test_double_amplitude_gives_beta_two(self):
        import random
        random.seed(11)
        bench = [100.0]
        stock = [100.0]
        for _ in range(300):
            r = random.gauss(0, 0.01)
            bench.append(bench[-1] * (1 + r))
            stock.append(stock[-1] * (1 + 2 * r))
        beta, corr = qm.beta_and_correlation(stock, bench)
        self.assertAlmostEqual(beta, 2.0, places=1)
        self.assertAlmostEqual(corr, 1.0, places=2)

    def test_insufficient_overlap_returns_none(self):
        beta, corr = qm.beta_and_correlation([100.0] * 10, [100.0] * 10)
        self.assertIsNone(beta)
        self.assertIsNone(corr)


class TrendTest(unittest.TestCase):
    def test_sma_matches_manual_mean(self):
        closes = [float(i) for i in range(1, 11)]
        self.assertAlmostEqual(qm.sma(closes, 5), sum(closes[-5:]) / 5, places=9)

    def test_sma_needs_full_window(self):
        self.assertIsNone(qm.sma([1.0, 2.0], 5))

    def test_uptrend_is_a_golden_cross(self):
        state = qm.trend_state(compounding(daily=0.002, n=400))
        self.assertEqual(state["state"], "GOLDEN_CROSS")
        self.assertTrue(state["above_sma_50"])
        self.assertTrue(state["above_sma_200"])

    def test_downtrend_is_a_death_cross(self):
        state = qm.trend_state(compounding(daily=-0.002, n=400))
        self.assertEqual(state["state"], "DEATH_CROSS")

    def test_unknown_without_200_bars(self):
        self.assertEqual(qm.trend_state([100.0] * 100)["state"], "UNKNOWN")

    def test_range_position_bounds(self):
        self.assertAlmostEqual(qm.range_position_pct(150, 100, 200), 50.0)
        self.assertAlmostEqual(qm.range_position_pct(200, 100, 200), 100.0)
        self.assertAlmostEqual(qm.range_position_pct(100, 100, 200), 0.0)
        # Outside the range clamps rather than exceeding 0-100.
        self.assertAlmostEqual(qm.range_position_pct(250, 100, 200), 100.0)
        self.assertIsNone(qm.range_position_pct(150, 200, 100))


class TradabilityTest(unittest.TestCase):
    def test_atr_on_constant_range(self):
        rows = [{"high": 105.0, "low": 95.0, "close": 100.0, "volume": 1}
                for _ in range(30)]
        self.assertAlmostEqual(qm.atr(rows, 14), 10.0, places=9)

    def test_atr_pct_scales_by_price(self):
        rows = [{"high": 105.0, "low": 95.0, "close": 100.0, "volume": 1}
                for _ in range(30)]
        self.assertAlmostEqual(qm.atr_pct(rows, 14), 10.0, places=9)

    def test_atr_needs_period_plus_one(self):
        rows = [{"high": 1.0, "low": 0.9, "close": 1.0, "volume": 1}
                for _ in range(5)]
        self.assertIsNone(qm.atr(rows, 14))

    def test_avg_daily_turnover_is_price_times_volume(self):
        rows = [{"close": 100.0, "volume": 1000} for _ in range(20)]
        self.assertAlmostEqual(qm.avg_daily_turnover(rows), 100_000.0, places=6)

    def test_volume_trend_ratio_detects_pickup(self):
        rows = ([{"close": 100.0, "volume": 1000} for _ in range(60)]
                + [{"close": 100.0, "volume": 3000} for _ in range(20)])
        self.assertGreater(qm.volume_trend_ratio(rows, fast=20, slow=60), 1.0)

    def test_volume_trend_needs_slow_window(self):
        rows = [{"close": 100.0, "volume": 1000} for _ in range(10)]
        self.assertIsNone(qm.volume_trend_ratio(rows, fast=20, slow=60))


if __name__ == "__main__":
    unittest.main()
