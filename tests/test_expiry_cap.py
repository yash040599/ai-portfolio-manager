"""Regression test for the expiry daily-trade-cap bug.

Bug (fixed 2026-05-26): on expiry days the daily K1 cap was computed as
``min(base_cap, EXPIRY_MAX_TRADES_PER_DAY)``. With the audit default of
``EXPIRY_MAX_TRADES_PER_DAY == 0`` this collapsed the cap to 0, and the
downstream guard ``if max_daily > 0`` then skipped the check entirely —
silently allowing UNLIMITED trades on the most volatile / expensive day
of the week.

The fix: ``EXPIRY_MAX_TRADES_PER_DAY`` only *tightens* the base cap, and
only when it is a positive value. When it is 0 we fall back to the base
K1 cap. This is exercised through ``OrderEngine._effective_daily_trade_cap()``.

Run with:  python -m unittest tests.test_expiry_cap
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from core.logger import Logger  # noqa: E402
from modes.trade.order_engine import OrderEngine  # noqa: E402


class _CfgStub:
    """Minimal config carrying just the knobs the cap logic reads.

    We copy the real defaults off ``Config`` so the test tracks the live
    config, then override only what each scenario needs.
    """

    def __init__(self, **overrides):
        self.MAX_TRADES_PER_DAY = int(Config.MAX_TRADES_PER_DAY)
        self.EXPIRY_MAX_TRADES_PER_DAY = int(Config.EXPIRY_MAX_TRADES_PER_DAY)
        self.BUDGET_REGIME_ENABLED = bool(Config.BUDGET_REGIME_ENABLED)
        self.BUDGET_TRADE_CAP_DELTA = dict(Config.BUDGET_TRADE_CAP_DELTA)
        self.MAX_BUDGET_INR = float(Config.MAX_BUDGET_INR)
        self.CHOPPY_PAUSE_MIN_CONSECUTIVE_SCANS = int(
            Config.CHOPPY_PAUSE_MIN_CONSECUTIVE_SCANS
        )
        self._expiry_applied = False
        for k, v in overrides.items():
            setattr(self, k, v)


def _make_engine(cfg) -> OrderEngine:
    """Build an OrderEngine with a no-op Zerodha stub (no network)."""
    zerodha = types.SimpleNamespace()
    return OrderEngine(cfg, zerodha, Logger("test_expiry_cap"))


class ExpiryCapTest(unittest.TestCase):
    def test_non_expiry_uses_base_cap(self):
        cfg = _CfgStub(MAX_TRADES_PER_DAY=2, EXPIRY_MAX_TRADES_PER_DAY=0)
        cfg._expiry_applied = False
        engine = _make_engine(cfg)
        self.assertEqual(engine._effective_daily_trade_cap(), 2)

    def test_expiry_with_zero_cap_falls_back_to_base(self):
        # This is the regression case. Before the fix this returned 0,
        # which disabled the cap entirely.
        cfg = _CfgStub(MAX_TRADES_PER_DAY=2, EXPIRY_MAX_TRADES_PER_DAY=0)
        cfg._expiry_applied = True
        engine = _make_engine(cfg)
        self.assertEqual(
            engine._effective_daily_trade_cap(), 2,
            "expiry day with EXPIRY_MAX_TRADES_PER_DAY=0 must keep the base cap, "
            "not disable it",
        )

    def test_expiry_with_positive_cap_tightens(self):
        cfg = _CfgStub(MAX_TRADES_PER_DAY=5, EXPIRY_MAX_TRADES_PER_DAY=1)
        cfg._expiry_applied = True
        engine = _make_engine(cfg)
        self.assertEqual(engine._effective_daily_trade_cap(), 1)

    def test_expiry_positive_cap_never_loosens(self):
        # A higher expiry cap must not raise the base cap.
        cfg = _CfgStub(MAX_TRADES_PER_DAY=2, EXPIRY_MAX_TRADES_PER_DAY=9)
        cfg._expiry_applied = True
        engine = _make_engine(cfg)
        self.assertEqual(engine._effective_daily_trade_cap(), 2)

    def test_cap_zero_means_no_cap(self):
        cfg = _CfgStub(MAX_TRADES_PER_DAY=0, EXPIRY_MAX_TRADES_PER_DAY=0)
        cfg._expiry_applied = True
        engine = _make_engine(cfg)
        self.assertEqual(engine._effective_daily_trade_cap(), 0)


if __name__ == "__main__":
    unittest.main()
