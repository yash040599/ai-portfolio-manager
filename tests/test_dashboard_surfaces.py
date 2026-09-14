import unittest
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.zerodha_client import ZerodhaClient
from modes.dashboard.chat_widget import build_chat_prompt, chat_section_html
from modes.dashboard.home_page import _currency_toggle_html
from modes.dashboard.mf_page import _render_nav_chart_card
from modes.dashboard.nav import render_topnav
from modes.dashboard.privacy import privacy_boot_script, privacy_css
from modes.dashboard.server import RenderCache
from modes.dashboard.theme import theme_boot_script
from modes.dashboard.theory_page import DEFAULT_PAGE, render_theory_page


class TestRenderCache(unittest.TestCase):
    def test_expiry_uses_injected_monotonic_clock(self):
        clock = Mock(return_value=100.0)
        cache = RenderCache(ttl_seconds=10, clock=clock)
        cache.put("page:home", b"snapshot", "text/html")
        self.assertEqual(cache.get("page:home"), (b"snapshot", "text/html"))
        clock.return_value = 110.0
        self.assertIsNone(cache.get("page:home"))

    def test_capacity_and_scoped_invalidation(self):
        cache = RenderCache(max_entries=2)
        cache.put("page:home", b"home", "text/html")
        cache.put("api:quotes", b"{}", "application/json")
        cache.put("page:tax", b"tax", "text/html")
        self.assertIsNone(cache.get("page:home"))
        cache.invalidate("page:")
        self.assertIsNone(cache.get("page:tax"))
        self.assertIsNotNone(cache.get("api:quotes"))
        cache.invalidate()
        self.assertIsNone(cache.get("api:quotes"))


class TestDashboardBrokerLogin(unittest.TestCase):
    def test_noninteractive_login_never_starts_assisted_authentication(self):
        config = SimpleNamespace(
            ZERODHA_API_KEY="example", KITE_USER_ID="demo", KITE_PASSWORD="demo",
            KITE_TOTP_SECRET="",
        )
        client = ZerodhaClient(config, Mock())
        with patch("core.zerodha_client.os.path.exists", return_value=False), \
             patch.dict(sys.modules, {"kiteconnect": SimpleNamespace(KiteConnect=Mock())}), \
             patch.object(client, "_login_programmatic") as assisted, \
             patch("builtins.input", side_effect=AssertionError("Unexpected credential prompt")):
            with self.assertRaisesRegex(RuntimeError, "interactive=False"):
                client.login(interactive=False)
        assisted.assert_not_called()


class TestDashboardPromptBuilder(unittest.TestCase):
    def test_us_prompt_uses_long_term_scorecard_not_swing_stops(self):
        position = SimpleNamespace(symbol="DEMO", managed_qty=2, entry_price=100, stop_price=90, target_price=120)
        analysis = {"rating": "ACCUMULATE", "action": "HOLD", "risk_grade": "MODERATE",
                    "valuation_band": "FAIR vs sector", "coverage_pct": 80, "score": 72,
                    "current_price": 110, "indicators": {}}
        with patch("modes.dashboard.us_page._us_positions", return_value=[position]), \
             patch("modes.dashboard.us_analysis.analyse_us_symbol", return_value=analysis), \
             patch("modes.dashboard.us_analysis.get_usd_inr_rate", return_value={}):
            for scope in ("us", "us_detail"):
                prompt = build_chat_prompt(scope, "DEMO", "Review the business")
                with self.subTest(scope=scope):
                    self.assertIn("long-term investment", prompt)
                    self.assertIn("multi-year investment", prompt)
                    self.assertNotIn("swing-trad", prompt)
                    self.assertNotIn("My stop:", prompt)
                    self.assertNotIn("Risk:reward", prompt)
                    self.assertNotIn("target $", prompt)
            self.assertIn("ACCUMULATE", prompt)

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


class TestProjectTheoryPage(unittest.TestCase):
    def test_project_is_default_and_never_reads_personal_live_statistics(self):
        with patch("modes.dashboard.theory_page.compute_live_stats", side_effect=AssertionError("Personal data read")):
            page = render_theory_page()

        self.assertEqual(DEFAULT_PAGE, "project")
        for content in ("The portfolio pivot", "Why intraday is paused", "Options: research before risk", "Rs.50,000", "Rs.-2,379", "0.82", "1.30", "screen privacy", 'data-privacy-public'):
            with self.subTest(content=content):
                self.assertIn(content, page)
        self.assertIn('href="#why-intraday-is-paused"', page)
        self.assertIn('id="why-intraday-is-paused"', page)
        self.assertNotIn("__PAGE_", page)
        self.assertNotIn("Edit the markdown", page)

    def test_theory_navigation_opens_the_project_story(self):
        self.assertIn('href="/theory/project"', render_topnav("/portfolio"))


class TestDashboardControls(unittest.TestCase):
    def test_shared_shell_restores_dashboard_privacy_before_paint(self):
        script = theme_boot_script()

        self.assertIn("class DashboardPrivacy", script)
        self.assertIn("this.key = 'dashPrivacy'", script)
        self.assertIn("getItem('homePrivacy')", script)
        self.assertIn("window.addEventListener('storage'", script)
        self.assertLess(script.index("class DashboardPrivacy"), script.index("var KEY = 'dashTheme'"))

    def test_every_navigation_exposes_the_same_privacy_control(self):
        for page in ("/", "/portfolio", "/mf", "/us", "/swing", "/tax", "/trading", "/dryrun", "/theory/statistics"):
            with self.subTest(page=page):
                navigation = render_topnav(page)
                self.assertIn("data-privacy-toggle", navigation)
                self.assertIn("toggleDashboardPrivacy()", navigation)

    def test_privacy_fails_closed_when_storage_is_unavailable(self):
        self.assertIn("catch (error) {\n        return true;", privacy_boot_script())

    def test_privacy_covers_refreshes_tooltips_inputs_and_private_charts(self):
        script = privacy_boot_script()
        for contract in ("MutationObserver", "prepareTables(roots)", "protectAttributes(roots)", "protectInputs()", "maskText(roots)", "pageshow", "row.cells.length === 2", "quantitySource", ".chat-copy"):
            with self.subTest(contract=contract):
                self.assertIn(contract, script)
        self.assertIn('canvas:not([data-privacy-public])', privacy_css())
        self.assertIn('visibility: hidden', privacy_css())
        self.assertNotIn('blur(', privacy_css())
        self.assertIn('const publicPrice = /^', script)
        self.assertNotIn('publicMetric', script)
        self.assertIn('personalRoots()', script)
        self.assertIn("this.scopes = '[data-personal]", script)
        self.assertNotIn("createTreeWalker(document.body", script)
        self.assertIn('Array.from(row.cells).slice(1)', script)
        self.assertIn("input.setAttribute('aria-hidden', 'true')", script)

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