import unittest
from unittest.mock import patch

from modes.trade.manager import PortfolioManager


class _FallbackConfig:
    TRADE_STRATEGY_PROFILE = "NOAI_GAP_AND_GO_1.2.0"
    SQUARE_OFF_HOUR = 13
    SQUARE_OFF_MINUTE = 0
    LOSER_EXIT_HOUR = 12


class _LogCapture:
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []

    def info(self, message):
        self.info_messages.append(message)

    def warning(self, message):
        self.warning_messages.append(message)


class TestTradeFallbackConsent(unittest.TestCase):
    def setUp(self):
        self.manager = PortfolioManager.__new__(PortfolioManager)
        self.manager.cfg = type("FallbackConfig", (), dict(vars(_FallbackConfig)))
        self.manager.log = _LogCapture()
        self.manager._gap_go = True
        self.manager._scan_failed = True

    @patch("builtins.print")
    @patch("builtins.input", return_value="")
    def test_blank_choice_stops_without_mutating_strategy(self, _input, print_mock):
        approved = self.manager._offer_legacy_fallback("no gap candidates")

        self.assertFalse(approved)
        self.assertTrue(self.manager._gap_go)
        self.assertEqual(
            self.manager.cfg.TRADE_STRATEGY_PROFILE,
            "NOAI_GAP_AND_GO_1.2.0",
        )
        warning = print_mock.call_args.args[0]
        self.assertIn("OOS PF: 0.82", warning)
        self.assertIn("PF below 1.00 means the strategy lost money", warning)

    @patch("builtins.print")
    @patch("builtins.input", return_value="1")
    def test_explicit_consent_activates_legacy_fallback(self, _input, _print):
        approved = self.manager._offer_legacy_fallback("no gap candidates")

        self.assertTrue(approved)
        self.assertFalse(self.manager._gap_go)
        self.assertEqual(
            self.manager.cfg.TRADE_STRATEGY_PROFILE,
            "NOAI_LEGACY_FULL",
        )
        self.assertEqual(self.manager.cfg.SQUARE_OFF_HOUR, 14)
        self.assertEqual(self.manager.cfg.SQUARE_OFF_MINUTE, 0)
        self.assertEqual(self.manager.cfg.LOSER_EXIT_HOUR, 13)
        self.assertFalse(self.manager._scan_failed)
        self.assertIn("OOS PF 0.82", self.manager.log.warning_messages[0])


if __name__ == "__main__":
    unittest.main()