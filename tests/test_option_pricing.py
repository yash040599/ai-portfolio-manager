"""Tests for scripts/trade/option_pricing.py.

Black-Scholes has closed-form properties that hold regardless of inputs, so
these check the maths against theory rather than against recorded output.
Put-call parity in particular would catch almost any sign or discounting
error in the pricer.

Run with:  python -m unittest tests.test_option_pricing
"""

import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "trade"))

from option_pricing import (  # noqa: E402
    NIFTY_LOT_SIZE, RISK_FREE_RATE, Leg, bs_delta, bs_price, implied_vol,
    leg_charges, parkinson_vol, realised_vol, smile_vol, trade_charges,
    years_to_expiry,
)

SPOT = 24_000.0
VOL = 0.14
T = 7 / 365


class BlackScholesTest(unittest.TestCase):
    def test_put_call_parity(self):
        """C - P = S - K*exp(-rT). Catches sign and discounting errors."""
        for strike in (23_000, 23_500, 24_000, 24_500, 25_000):
            call = bs_price(SPOT, strike, T, VOL, "CE")
            put = bs_price(SPOT, strike, T, VOL, "PE")
            expected = SPOT - strike * math.exp(-RISK_FREE_RATE * T)
            self.assertAlmostEqual(call - put, expected, places=6,
                                   msg=f"parity broken at strike {strike}")

    def test_prices_are_never_negative(self):
        for strike in range(20_000, 28_001, 500):
            for kind in ("CE", "PE"):
                self.assertGreaterEqual(bs_price(SPOT, strike, T, VOL, kind), 0.0)

    def test_price_increases_with_volatility(self):
        prev = -1.0
        for vol in (0.05, 0.10, 0.15, 0.25, 0.40):
            price = bs_price(SPOT, SPOT, T, vol, "CE")
            self.assertGreater(price, prev)
            prev = price

    def test_price_increases_with_time(self):
        prev = -1.0
        for days in (1, 3, 7, 30, 90):
            price = bs_price(SPOT, SPOT, days / 365, VOL, "CE")
            self.assertGreater(price, prev)
            prev = price

    def test_calls_decrease_as_strike_rises(self):
        prices = [bs_price(SPOT, k, T, VOL, "CE")
                  for k in range(23_000, 25_001, 250)]
        self.assertEqual(prices, sorted(prices, reverse=True))

    def test_puts_increase_as_strike_rises(self):
        prices = [bs_price(SPOT, k, T, VOL, "PE")
                  for k in range(23_000, 25_001, 250)]
        self.assertEqual(prices, sorted(prices))

    def test_at_expiry_price_is_intrinsic(self):
        self.assertAlmostEqual(bs_price(SPOT, 23_500, 0, VOL, "CE"), 500.0)
        self.assertAlmostEqual(bs_price(SPOT, 24_500, 0, VOL, "CE"), 0.0)
        self.assertAlmostEqual(bs_price(SPOT, 24_500, 0, VOL, "PE"), 500.0)
        self.assertAlmostEqual(bs_price(SPOT, 23_500, 0, VOL, "PE"), 0.0)

    def test_never_worth_less_than_intrinsic(self):
        for strike in range(22_000, 26_001, 500):
            call = bs_price(SPOT, strike, T, VOL, "CE")
            self.assertGreaterEqual(call + 1e-6, max(0.0, SPOT - strike))


class DeltaTest(unittest.TestCase):
    def test_delta_bounds(self):
        for strike in range(21_000, 27_001, 500):
            self.assertTrue(0.0 <= bs_delta(SPOT, strike, T, VOL, "CE") <= 1.0)
            self.assertTrue(-1.0 <= bs_delta(SPOT, strike, T, VOL, "PE") <= 0.0)

    def test_atm_delta_is_about_half(self):
        self.assertAlmostEqual(bs_delta(SPOT, SPOT, T, VOL, "CE"), 0.5, delta=0.05)

    def test_call_delta_rises_as_strike_falls(self):
        deltas = [bs_delta(SPOT, k, T, VOL, "CE")
                  for k in range(23_000, 25_001, 250)]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_deep_itm_call_delta_approaches_one(self):
        self.assertGreater(bs_delta(SPOT, 18_000, T, VOL, "CE"), 0.99)


class ImpliedVolTest(unittest.TestCase):
    def test_round_trip_recovers_the_input_vol(self):
        for strike in (23_000, 24_000, 25_000):
            for vol in (0.08, 0.14, 0.25):
                price = bs_price(SPOT, strike, T, vol, "CE")
                recovered = implied_vol(price, SPOT, strike, T, "CE")
                self.assertIsNotNone(recovered)
                self.assertAlmostEqual(recovered, vol, places=4)

    def test_below_intrinsic_is_unattainable(self):
        self.assertIsNone(implied_vol(100.0, SPOT, 23_000, T, "CE"))

    def test_non_positive_inputs_return_none(self):
        self.assertIsNone(implied_vol(0.0, SPOT, 24_000, T, "CE"))
        self.assertIsNone(implied_vol(50.0, SPOT, 24_000, 0.0, "CE"))


class SmileTest(unittest.TestCase):
    def test_atm_vol_is_unchanged(self):
        self.assertAlmostEqual(smile_vol(SPOT, SPOT, VOL), VOL, places=9)

    def test_flat_parameters_disable_the_smile(self):
        for strike in (22_000, 24_000, 26_000):
            self.assertAlmostEqual(
                smile_vol(SPOT, strike, VOL, curv=0.0, slope=0.0), VOL, places=9)

    def test_downside_is_bid_up_relative_to_upside(self):
        """NIFTY skew: crash insurance costs more than upside."""
        put_wing = smile_vol(SPOT, SPOT * 0.96, VOL)
        call_wing = smile_vol(SPOT, SPOT * 1.04, VOL)
        self.assertGreater(put_wing, call_wing)

    def test_vol_never_goes_non_positive(self):
        for strike in range(12_000, 40_001, 1_000):
            self.assertGreater(smile_vol(SPOT, strike, VOL), 0.0)


class VolEstimatorTest(unittest.TestCase):
    def test_parkinson_defaults_on_empty_input(self):
        self.assertAlmostEqual(parkinson_vol([], default=0.14), 0.14)

    def test_parkinson_rises_with_range(self):
        tight = [{"high": 101.0, "low": 99.0} for _ in range(30)]
        wide = [{"high": 110.0, "low": 90.0} for _ in range(30)]
        self.assertGreater(parkinson_vol(wide), parkinson_vol(tight))

    def test_realised_vol_of_a_flat_series_is_zero(self):
        self.assertAlmostEqual(realised_vol([100.0] * 50), 0.0, places=9)

    def test_realised_vol_needs_history(self):
        self.assertIsNone(realised_vol([100.0]))

    def test_years_to_expiry_counts_intraday_hours(self):
        self.assertAlmostEqual(years_to_expiry(0, 6.0), 0.25 / 365, places=9)
        self.assertAlmostEqual(years_to_expiry(7, 0.0), 7 / 365, places=9)
        self.assertEqual(years_to_expiry(-5), 0.0)


class ChargesTest(unittest.TestCase):
    def test_charges_are_positive(self):
        leg = Leg("CE", 24_000, "SELL", 100.0, 50.0, NIFTY_LOT_SIZE)
        self.assertGreater(leg_charges(leg), 0.0)

    def test_selling_costs_more_than_buying_at_equal_turnover(self):
        """STT falls on the sale, so an opening SELL that closes cheap pays
        more than an opening BUY that closes cheap."""
        sell = Leg("CE", 24_000, "SELL", 100.0, 10.0, NIFTY_LOT_SIZE)
        buy = Leg("CE", 24_000, "BUY", 100.0, 10.0, NIFTY_LOT_SIZE)
        self.assertGreater(leg_charges(sell), leg_charges(buy))

    def test_four_legs_cost_more_than_one(self):
        one = [Leg("CE", 24_000, "SELL", 100.0, 50.0, NIFTY_LOT_SIZE)]
        four = one * 4
        # trade_charges rounds the total, so allow a few paise of drift.
        self.assertAlmostEqual(trade_charges(four), trade_charges(one) * 4,
                               delta=0.05)

    def test_fixed_brokerage_dominates_at_small_size(self):
        """~8 orders x Rs.20 is most of a condor's cost, which is why
        scaling notional barely improves its profit factor."""
        small = trade_charges([Leg("CE", 24_000, "SELL", 30.0, 10.0, NIFTY_LOT_SIZE)])
        # Brokerage alone is Rs.40 for the round trip, before GST.
        self.assertGreater(small, 40.0)
        self.assertLess(small, 120.0)

    def test_charges_scale_sub_linearly_with_size(self):
        one_lot = trade_charges([Leg("CE", 24_000, "SELL", 100.0, 50.0, NIFTY_LOT_SIZE)])
        ten_lot = trade_charges([Leg("CE", 24_000, "SELL", 100.0, 50.0, NIFTY_LOT_SIZE * 10)])
        self.assertLess(ten_lot, one_lot * 10)


if __name__ == "__main__":
    unittest.main()
