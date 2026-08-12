import unittest
from types import SimpleNamespace
from unittest.mock import patch

from modes.dashboard.chat_widget import build_chat_prompt, chat_section_html
from modes.dashboard.home_page import _currency_toggle_html
from modes.dashboard.mf_page import _render_nav_chart_card


class TestDashboardPromptBuilder(unittest.TestCase):
    def test_home_prompt_uses_portfolio_advice_brief(self):
        with patch(
            "modes.dashboard.chat_widget._home_overview_block",
            return_value="=== NET WORTH ===\n- Total: Rs.100",
        ):
            prompt = build_chat_prompt("home", "", "What should I fix first?")

        self.assertIn("Indian equities and mutual funds", prompt)
        self.assertIn("What should I fix first?", prompt)
        self.assertIn("Flag concentration and overlap risk", prompt)
        self.assertIn("=== NET WORTH ===", prompt)
        self.assertNotIn("buy more / hold / trim / exit", prompt)

    def test_home_widget_targets_home_scope(self):
        widget = chat_section_html("home")

        self.assertIn('data-chat-scope="home"', widget)
        self.assertIn("everything I own", widget)
        self.assertIn("Build prompt", widget)


class TestDashboardControls(unittest.TestCase):
    def test_home_currency_toggle_uses_available_fx_rate(self):
        toggle = _currency_toggle_html({"fx": {"rate": 83.25}})

        self.assertIn('id="home-currency-toggle"', toggle)
        self.assertIn("83.25/USD", toggle)
        self.assertIn("USD", toggle)
        self.assertIn("INR", toggle)

    def test_mf_nav_picker_lists_schemes(self):
        book = SimpleNamespace(
            schemes=[
                SimpleNamespace(scheme_code="1001", fund="Alpha Fund"),
                SimpleNamespace(scheme_code="1002", fund="Beta Fund"),
            ]
        )

        card = _render_nav_chart_card(book)

        self.assertIn('id="mf-nav-picker"', card)
        self.assertIn('value="1001"', card)
        self.assertIn("Alpha Fund", card)
        self.assertIn("Beta Fund", card)


if __name__ == "__main__":
    unittest.main()