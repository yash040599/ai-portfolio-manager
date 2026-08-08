"""Tests for shared/technical_indicators.py.

These drive live entry and exit decisions in trade mode, so the invariants
here matter more than any single value. In particular
``test_ema_returns_one_value_per_candle`` guards the assumption that
``macd_histogram`` relies on when it zips the two EMA series together with
``strict=True`` — if EMA ever returns a shorter warm-up series, MACD would
silently misalign fast and slow bars.

Run with:  python -m unittest tests.test_technical_indicators
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import technical_indicators as ti  # noqa: E402


def make(closes, spread=0.01, volume=10_000):
    return [
        {"open": c, "high": c * (1 + spread), "low": c * (1 - spread),
         "close": c, "volume": volume}
        for c in closes
    ]


RISING = make([100.0 * (1.01 ** i) for i in range(120)])
FALLING = make([100.0 * (0.99 ** i) for i in range(120)])
FLAT = make([100.0] * 120)


class EmaTest(unittest.TestCase):
    def test_ema_returns_one_value_per_candle(self):
        """MACD zips fast and slow EMAs with strict=True; that is only safe
        because both are exactly len(candles) long."""
        for period in (5, 9, 12, 21, 26):
            self.assertEqual(len(ti.ema(RISING, period)), len(RISING),
                             f"period {period} changed the series length")

    def test_ema_of_a_flat_series_is_the_level(self):
        values = ti.ema(FLAT, 9)
        self.assertAlmostEqual(values[-1], 100.0, places=9)

    def test_ema_tracks_below_a_rising_series(self):
        values = ti.ema(RISING, 21)
        self.assertLess(values[-1], RISING[-1]["close"])

    def test_ema_shorter_than_period_returns_raw_values(self):
        short = make([1.0, 2.0, 3.0])
        self.assertEqual(ti.ema(short, 10), [1.0, 2.0, 3.0])

    def test_fast_ema_leads_slow_in_an_uptrend(self):
        fast = ti.ema(RISING, 9)[-1]
        slow = ti.ema(RISING, 21)[-1]
        self.assertGreater(fast, slow)


class RsiTest(unittest.TestCase):
    def test_rsi_is_bounded(self):
        for series in (RISING, FALLING, FLAT):
            value = ti.rsi(series)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 100.0)

    def test_uninterrupted_gains_pin_rsi_high(self):
        self.assertGreater(ti.rsi(RISING), 95.0)

    def test_uninterrupted_losses_pin_rsi_low(self):
        self.assertLess(ti.rsi(FALLING), 5.0)

    def test_rsi_signal_reports_a_zone(self):
        self.assertIn(ti.rsi_signal(RISING)["signal"], {
            "OVERBOUGHT", "OVERSOLD", "NEUTRAL", "NONE",
            "BULLISH", "BEARISH",
        })


class VwapTest(unittest.TestCase):
    def test_vwap_sits_inside_the_days_range(self):
        value = ti.vwap(RISING)
        lows = min(c["low"] for c in RISING)
        highs = max(c["high"] for c in RISING)
        self.assertGreaterEqual(value, lows)
        self.assertLessEqual(value, highs)

    def test_vwap_of_a_flat_series_is_the_level(self):
        self.assertAlmostEqual(ti.vwap(FLAT), 100.0, places=6)

    def test_vwap_weights_by_volume(self):
        """A huge-volume bar should pull VWAP towards its own price."""
        rows = make([100.0] * 10)
        rows.append({"open": 200.0, "high": 200.0, "low": 200.0,
                     "close": 200.0, "volume": 10_000_000})
        self.assertGreater(ti.vwap(rows), 150.0)


class MacdTest(unittest.TestCase):
    def test_insufficient_history_returns_neutral(self):
        result = ti.macd_histogram(make([100.0] * 10))
        self.assertEqual(result["signal"], "NONE")
        self.assertEqual(result["histogram"], 0)

    def test_result_shape_is_stable(self):
        result = ti.macd_histogram(RISING)
        self.assertEqual(set(result), {"histogram", "prev_histogram",
                                       "signal", "momentum"})

    def test_histogram_equals_macd_line_minus_signal_line(self):
        """Recompute the histogram independently to check the arithmetic."""
        fast, slow, sig_period = 12, 26, 9
        macd_line = [f - s for f, s in
                     zip(ti.ema(RISING, fast), ti.ema(RISING, slow), strict=True)]
        sig = [0.0] * len(macd_line)
        sig[sig_period - 1] = sum(macd_line[:sig_period]) / sig_period
        k = 2 / (sig_period + 1)
        for i in range(sig_period, len(macd_line)):
            sig[i] = (macd_line[i] - sig[i - 1]) * k + sig[i - 1]
        self.assertAlmostEqual(ti.macd_histogram(RISING)["histogram"],
                               round(macd_line[-1] - sig[-1], 4), places=4)

    def test_signal_label_follows_histogram_sign(self):
        """`signal` reports MOMENTUM (histogram sign), not trend direction.

        Documented in TRADE_STRATEGY.md as "growing histogram confirms
        trend; shrinking warns of exhaustion". Note this means a falling
        stock whose decline is decelerating reports BULLISH — see
        test_accelerating_decline_is_bearish for the contrast.
        """
        result = ti.macd_histogram(RISING)
        self.assertEqual(result["signal"],
                         "BULLISH" if result["histogram"] > 0 else "BEARISH")

    def test_accelerating_decline_is_bearish(self):
        accelerating = make([100.0 * (0.999 ** (i * i / 50)) for i in range(120)])
        self.assertEqual(ti.macd_histogram(accelerating)["signal"], "BEARISH")

    def test_macd_line_is_negative_in_a_downtrend(self):
        """Trend direction lives in the MACD line, which this function does
        not return. Guards the underlying EMAs rather than the label."""
        macd_line = [f - s for f, s in
                     zip(ti.ema(FALLING, 12), ti.ema(FALLING, 26), strict=True)]
        self.assertLess(macd_line[-1], 0.0)


class AdxTest(unittest.TestCase):
    def test_adx_is_bounded(self):
        value = ti.adx(RISING).get("adx", 0)
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    def test_trending_market_scores_above_flat(self):
        self.assertGreater(ti.adx(RISING).get("adx", 0),
                           ti.adx(FLAT).get("adx", 0))


class RobustnessTest(unittest.TestCase):
    """Empty and tiny inputs must degrade, never raise: these run inside
    the live scan loop where one bad symbol must not kill the session."""

    def test_indicators_survive_empty_input(self):
        for fn in (ti.rsi, ti.vwap, ti.macd_histogram, ti.adx,
                   ti.ema_crossover, ti.bollinger_squeeze):
            try:
                fn([])
            except Exception as exc:  # noqa: BLE001 - that is the assertion
                self.fail(f"{fn.__name__} raised on empty input: {exc!r}")

    def test_indicators_survive_single_candle(self):
        one = make([100.0])
        for fn in (ti.rsi, ti.vwap, ti.macd_histogram, ti.adx,
                   ti.ema_crossover, ti.bollinger_squeeze):
            try:
                fn(one)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"{fn.__name__} raised on one candle: {exc!r}")


if __name__ == "__main__":
    unittest.main()
