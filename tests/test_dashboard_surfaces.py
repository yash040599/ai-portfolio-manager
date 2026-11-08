import json
import re
import shutil
import subprocess
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config import Config
from core.zerodha_client import ZerodhaClient
from modes.dashboard.ai_widget import ai_banner_html
from modes.dashboard.chat_widget import build_chat_prompt, chat_section_html
from modes.dashboard.coffee import PAYEE, UPI_ID, coffee_dialog_html, coffee_script
from modes.dashboard.home_page import (
    _books,
    _connect_card,
    _currency_toggle_html,
    render_home_page,
)
from modes.dashboard.mf_page import _render_nav_chart_card
from modes.dashboard.nav import NAV_ITEMS, is_shell, render_topnav
from modes.dashboard.portfolio_page import render_login_page
from modes.dashboard.privacy import privacy_boot_script, privacy_css, privacy_toggle_html
from modes.dashboard.rupevo import (
    CALLOUTS,
    PAGES,
    RUPEVO_URL,
    rupevo_card,
    rupevo_footer_link,
)
from modes.dashboard.server import RenderCache
from modes.dashboard.table_sort import SORT_KEY_JS
from modes.dashboard.theme import theme_boot_script, theme_css
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

    def test_privacy_masks_windows_linux_and_macos_home_paths(self):
        source = re.search(r"this\.identitySource = String\.raw`([^`]+)`;", privacy_boot_script())
        assert source is not None
        homes = (
            "\\".join(("C:", "Users", "demo-user")),
            "/".join(("C:", "Users", "demo-user")),
            "/" + "/".join(("home", "demo-user")),
            "/" + "/".join(("Users", "demo-user")),
        )
        for home in homes:
            with self.subTest(home=home):
                match = re.search(source.group(1), home + "/portfolio", re.IGNORECASE)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(match.group(0), home)

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
        self.assertIn('renderMasks()', script)
        self.assertIn('this.originals = new WeakMap()', script)
        self.assertNotIn('position: absolute; left: 0; top: 0', privacy_css())
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


class TestRupevoLinks(unittest.TestCase):
    """The personal dashboard points visitors to Rupevo, its public site."""

    NEW_TAB = ('target="_blank"', 'rel="noopener noreferrer"', 'referrerpolicy="no-referrer"')

    @staticmethod
    def anchors(markup):
        return re.findall(r"<a [^>]*>", markup)

    def assert_safe_external(self, anchor):
        self.assertRegex(anchor, r'href="https://yash040599\.github\.io/Rupevo/[^"?]*"')
        for attribute in self.NEW_TAB:
            self.assertIn(attribute, anchor)

    def test_destinations_are_fixed_rupevo_pages_without_query_data(self):
        self.assertEqual(RUPEVO_URL, "https://yash040599.github.io/Rupevo/")
        for key, page in PAGES.items():
            with self.subTest(page=key):
                self.assertTrue(page.url.startswith(RUPEVO_URL))
                self.assertNotIn("?", page.url)
                self.assertIn(page.url.partition("#")[2], {"", "compare", "plan", "lumpsum"})
        for context, callout in CALLOUTS.items():
            with self.subTest(context=context):
                self.assertTrue(callout.pages and set(callout.pages) <= set(PAGES))

    def test_every_navigation_links_rupevo_once_in_a_new_tab(self):
        self.assertIn(".rv-card", theme_css())
        for here in ("/", "/portfolio", "/mf", "/us", "/swing", "/tax", "/trading",
                     "/dryrun", "/theory/project", "Login"):
            with self.subTest(page=here):
                links = [a for a in self.anchors(render_topnav(here)) if "nav-rupevo" in a]
                self.assertEqual(len(links), 1)
                self.assertIn(f'href="{RUPEVO_URL}"', links[0])
                self.assert_safe_external(links[0])

    def test_page_cards_open_the_matching_rupevo_page(self):
        primary = {"login": "", "portfolio": "india/", "swing": "india/",
                   "mf": "mf/#compare", "us": "us/", "tax": "tax/rsu/"}
        self.assertEqual(set(primary), set(CALLOUTS))
        for context, path in primary.items():
            card = rupevo_card(context)
            links = self.anchors(card)
            with self.subTest(context=context):
                self.assertIn(f'href="{RUPEVO_URL}{path}"', links[0])
                self.assertIn('class="rv-cta"', links[0])
                for link in links:
                    self.assert_safe_external(link)
                self.assertIn(f'data-rupevo="{context}"', card)
                self.assertIn("data-privacy-public", card)
                self.assertIn("not investment or tax advice", card)
        mf_links = [re.search(r'href="([^"]+)"', a).group(1) for a in self.anchors(rupevo_card("mf"))]
        self.assertEqual(mf_links, [RUPEVO_URL + path for path in ("mf/#compare", "mf/#plan", "mf/#lumpsum")])

    def test_home_points_to_rupevo_in_the_footer_and_when_not_connected(self):
        footer = [a for a in self.anchors(rupevo_footer_link()) if "rv-inline" in a]
        self.assertEqual(len(footer), 1)
        self.assertIn(f'href="{RUPEVO_URL}"', footer[0])
        self.assert_safe_external(footer[0])
        offline = _connect_card({"auth": {"valid": False, "reason": "missing", "has_saved_creds": False,
                                          "user_id": "", "login_url": ""}})
        self.assertIn("Try Rupevo", offline)
        for link in (a for a in self.anchors(offline) if "rv-inline" in a):
            self.assert_safe_external(link)
        connected = _connect_card({"auth": {"valid": True, "token_date": "2026-11-08"}})
        self.assertNotIn("Rupevo", connected)

    def test_login_page_offers_rupevo_to_visitors_without_zerodha(self):
        with patch("modes.dashboard.portfolio_page.os.path.exists", return_value=False), \
             patch.multiple(Config, ZERODHA_API_KEY="", KITE_USER_ID="", KITE_PASSWORD=""):
            page = render_login_page()
        self.assertEqual(page.count('data-rupevo="login"'), 1)
        self.assertIn("No Zerodha account? Try Rupevo", page)
        self.assertLess(page.index('data-rupevo="login"'), page.index("Manual login"))


class TestSortableTables(unittest.TestCase):
    def test_every_page_shell_ships_sorting(self):
        self.assertIn("window.dashboardTableSort", theme_boot_script())
        self.assertIn("th[data-sortable]", theme_css())
        self.assertIn("table.kv, table.kvtable", theme_boot_script())

    @unittest.skipUnless(shutil.which("node"), "Node.js runs the browser sort parser")
    def test_sort_keys_understand_dashboard_formats(self):
        cases = {
            "₹1,23,456": 123456, "-₹1,234": -1234, "₹-1,234": -1234, "−2.1%": -2.1,
            "+12.34%": 12.34, "₹1.23 Cr": 1.23e7, "₹12.3 L": 1.23e6, "$1.2B": 1.2e9,
            "45K": 45000, "22.4x": 22.4, "1,234 shares": 1234, "Rs. 2,500": 2500,
            "+₹1,234 (+5.6%)": 1234, "•••• (+5.6%)": 5.6, "10:15": 36900,
            "—": None, "": None, "n/a": None, "Loading…": None,
            "HDFCBANK": "text", "52W_DIP": "text", "••••": "text", "2026-11-08": "date",
        }
        script = (SORT_KEY_JS + "\nprocess.stdout.write(JSON.stringify("
                  + json.dumps(list(cases)) + ".map(dashSortKey)));")
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                                encoding="utf-8", check=True)
        for (text, expected), key in zip(cases.items(), json.loads(result.stdout), strict=True):
            with self.subTest(text=text):
                if expected is None:
                    self.assertIsNone(key)
                elif expected == "text":
                    self.assertNotIn("num", key)
                elif expected == "date":
                    self.assertTrue(key.get("date"))
                else:
                    self.assertAlmostEqual(key["num"], expected)


class TestBuyMeACoffee(unittest.TestCase):
    def test_every_navigation_offers_coffee(self):
        for here in ("/", "/portfolio", "/mf", "/us", "/swing", "/tax", "/trading", "/dryrun"):
            with self.subTest(page=here):
                nav = render_topnav(here)
                self.assertEqual(nav.count('class="nav-coffee"'), 1)
                self.assertIn('onclick="openDashboardCoffee()"', nav)

    def test_dialog_uses_rupevos_public_upi_details_without_pay_links(self):
        dialog = coffee_dialog_html()
        for content in (UPI_ID, PAYEE, "<svg", "Scan with any UPI app", "data-privacy-public"):
            with self.subTest(content=content):
                self.assertIn(content, dialog)
        self.assertNotIn('href="upi:', dialog)
        script = coffee_script()
        self.assertEqual(script.count("</script>"), 1)
        self.assertIn("window.openDashboardCoffee", script)
        self.assertIn(script, theme_boot_script())


class TestAppShell(unittest.TestCase):
    """Rupevo-style shell: an app bar plus a navigation rail on every page."""

    def test_shell_lists_every_page_and_marks_only_the_current_one(self):
        expected = {"/": "/", "/portfolio/DEMO": "/portfolio", "Mutual Funds": "/mf",
                    "/swing": "/swing", "/us/DEMO": "/us", "/trading": "/trading",
                    "Dry Run": "/dryrun", "/tax": "/tax", "/theory/statistics": "/theory/project",
                    "Login": None}
        for here, current in expected.items():
            nav = render_topnav(here)
            with self.subTest(page=here):
                self.assertTrue(is_shell(nav))
                self.assertLess(nav.index("</header>"), nav.index('<nav class="navbar"'))
                for _label, href, _icon, _hint in NAV_ITEMS:
                    self.assertIn(f'class="nav-item" href="{href}"', nav)
                marked = re.findall(r'<a class="nav-item" href="([^"]+)"[^>]*aria-current="page"', nav)
                self.assertEqual(marked, [current] if current else [])
                self.assertNotIn("history.back", nav)

    def test_page_status_pills_sit_in_the_app_bar_before_the_shared_controls(self):
        nav = render_topnav("/us", after_links='<span class="fx-badge">USD/INR</span>')
        status = nav.index('<div class="appbar-status"><span class="fx-badge">')
        self.assertLess(status, nav.index("data-privacy-toggle"))
        self.assertLess(nav.index("data-privacy-toggle"), nav.index('class="nav-coffee"'))
        self.assertLess(nav.index('class="nav-coffee"'), nav.index("</header>"))
        self.assertNotIn("appbar-status", render_topnav("/tax"))

    def test_privacy_toggle_is_an_icon_button_whose_icons_survive_refresh(self):
        toggle = privacy_toggle_html()
        for contract in ("data-privacy-toggle", "data-privacy-icon", 'aria-label="Hide amounts"',
                         "toggleDashboardPrivacy()", "ic-eye", "ic-eye-off"):
            with self.subTest(contract=contract):
                self.assertIn(contract, toggle)
        script = privacy_boot_script()
        self.assertIn("button.hasAttribute('data-privacy-icon')", script)
        self.assertIn("button.textContent = this.hidden ? 'Show amounts' : 'Hide amounts'", script)

    def test_shell_css_pads_the_body_only_when_the_shell_is_present(self):
        from modes.dashboard.theme import theme_overrides_css

        css = theme_overrides_css()
        for contract in ("body:has(.appbar)", ".appbar {", ".navbar {", "@media (min-width: 840px)",
                         "@media (max-width: 839px)", "table.holdings th { top: var(--appbar-h); }"):
            with self.subTest(contract=contract):
                self.assertIn(contract, css)
        self.assertNotIn("nav.topnav", css + theme_css())

    def test_ask_ai_and_model_switcher_are_collapsed_rows(self):
        widget = chat_section_html("portfolio")
        self.assertTrue(widget.lstrip().startswith('<details class="chat-widget ask-ai"'))
        self.assertNotIn("<details class=\"chat-widget ask-ai\" open", widget)
        for cls in ("chat-build", "chat-copy", "chat-question", "chat-output", "chat-msg"):
            with self.subTest(cls=cls):
                self.assertIn(cls, widget)
        self.assertNotIn('style="border', widget)
        banner = ai_banner_html()
        self.assertTrue(banner.startswith('<details class="ai-widget">'))
        for element_id in ("ai-label", "ai-provider", "ai-plan", "ai-apply", "ai-free", "ai-msg"):
            with self.subTest(element_id=element_id):
                self.assertEqual(banner.count(f'id="{element_id}"'), 1)


class TestHomeOverview(unittest.TestCase):
    BOOKS = {
        "india": {"available": True, "age_days": 0, "top": []},
        "us": {"rows": []},
        "swing_india": {"rows": [], "positions": 0, "live": False, "unrealised": 0.0, "watchlist": 0},
        "mf": {"rows": [], "schemes": 0, "external_count": 0, "monthly_sip": 0.0, "nav_as_of": ""},
    }

    def test_books_are_one_tabbed_card_inside_the_privacy_scope(self):
        books = _books(self.BOOKS)
        self.assertIn('<section class="card books"', books)
        tabs = re.findall(r'<button class="book-tab"[^>]*>', books)
        self.assertEqual(len(tabs), 4)
        self.assertEqual(sum('aria-selected="true"' in tab for tab in tabs), 1)
        self.assertEqual(books.count('role="tabpanel"'), 4)
        self.assertEqual(books.count(" hidden>"), 3)
        for element_id in ("book-india", "book-mf", "book-us", "book-swing", "mf-foot", "swing-foot"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', books)

    def test_connected_zerodha_is_a_status_chip_with_relogin(self):
        chip = _connect_card({"auth": {"valid": True, "token_date": "2026-11-08"}})
        self.assertIn('id="connect-card"', chip)
        self.assertIn("connect ok", chip)
        self.assertIn('href="/login"', chip)
        self.assertNotIn("<form", chip)

    def test_page_keeps_every_id_its_script_updates(self):
        auth = {"valid": False, "reason": "missing", "has_saved_creds": False,
                "user_id": "", "login_url": "", "token_date": ""}
        market = {"ist": "10:00", "ist_date": "Mon, 09 Nov 2026", "india_open": True,
                  "india_label": "NSE open", "us_open": False, "us_label": "US closed"}
        with patch("modes.dashboard.home_page.build_summary", side_effect=RuntimeError("offline")), \
             patch("modes.dashboard.home_summary.auth_state", return_value=auth), \
             patch("modes.dashboard.home_summary.market_state", return_value=market):
            page = render_home_page()
        for element_id in (
            "home-bootstrap", "kpi-networth", "kpi-networth-split", "kpi-unrealised",
            "kpi-unrealised-pct", "kpi-india", "kpi-india-pnl", "kpi-india-count", "kpi-mf",
            "kpi-mf-pnl", "kpi-mf-count", "kpi-us", "kpi-us-pnl", "kpi-us-count", "r-swing",
            "r-us", "r-intraday", "r-total", "book-india", "book-us", "book-swing", "book-mf",
            "mf-foot", "swing-foot", "chart-geo", "geo-legend", "home-stamp", "home-error",
            "home-refresh", "home-auto", "connect-card", "cur-usd-pill", "cur-inr-pill",
        ):
            with self.subTest(element_id=element_id):
                self.assertEqual(page.count(f'id="{element_id}"'), 1)
        self.assertLess(page.index('<header class="appbar">'), page.index("<main"))
        self.assertIn('href="#connect-card"', page)
        self.assertNotIn('class="tile', page)
        self.assertEqual(len(re.findall(r"<button[^>]*data-privacy-toggle", page)), 1)
        for scope in ('class="kpis"', 'class="card books"', 'class="facts realised"', 'id="geo-legend"'):
            with self.subTest(scope=scope):
                self.assertIn(scope, page)


if __name__ == "__main__":
    unittest.main()