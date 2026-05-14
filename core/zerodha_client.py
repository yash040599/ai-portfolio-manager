# ================================================================
# core/zerodha_client.py
# ================================================================
# All Zerodha Kite API interactions in one place.
#
# Phase 1 uses:  login, get_holdings, get_quotes, get_historical
# Phase 2 adds:  place_order, cancel_order, get_positions
#                (stubbed below with NotImplementedError)
#
# Every other class that needs Zerodha data calls this client.
# Nothing else imports kiteconnect directly.
# ================================================================

import os
import json
import time
import datetime
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from config     import Config, now_ist
from core.logger import Logger


class ZerodhaClient:

    TOKEN_FILE = os.path.join("data", "access_token.json")

    def __init__(self, config: type[Config], log: Logger):
        self.cfg   = config
        self.log   = log
        self._kite = None   # set by login()

        # Instrument token cache — loaded once, reused per session
        self._nse_tokens: dict | None = None
        self._bse_tokens: dict | None = None
        self._tick_sizes: dict | None = None   # "NSE:SYMBOL" → tick_size

        # Rate-limit throttle for historical API (Zerodha ~3 req/sec)
        self._last_historical_call: float = 0.0

    # ================================================================
    # LOGIN
    # ================================================================
    # Opens Zerodha's browser login flow. Saves the access token for
    # the day — subsequent calls within the same day skip the browser.
    # Zerodha tokens expire at midnight; next-day runs trigger re-login.
    #
    # If you see "Incorrect api_key or access_token":
    #   Delete access_token.json and re-run.
    # ================================================================

    def login(self, interactive: bool = True):
        from kiteconnect import KiteConnect
        self._kite = KiteConnect(api_key=self.cfg.ZERODHA_API_KEY)

        # Reuse today's saved token if available
        if os.path.exists(self.TOKEN_FILE):
            with open(self.TOKEN_FILE) as f:
                saved = json.load(f)
            if saved.get("date") == str(now_ist().date()):
                self.log.success("Using saved Zerodha login token from today")
                self._kite.set_access_token(saved["token"])
                return

        # No valid token — need OAuth flow
        login_url = self._kite.login_url()

        # ---------- Programmatic flows (AUTO / ASSISTED) ----------
        # If the user has put KITE_USER_ID + KITE_PASSWORD in .env we can
        # drive the login form ourselves and skip the browser entirely.
        # On any failure we silently fall through to the legacy b/m prompt.
        user_id  = getattr(self.cfg, "KITE_USER_ID", "") or ""
        password = getattr(self.cfg, "KITE_PASSWORD", "") or ""
        seed     = getattr(self.cfg, "KITE_TOTP_SECRET", "") or ""

        if user_id and password:
            mode = "AUTO" if seed else "ASSISTED"
            self.log.info(f"Attempting Kite {mode} login (env-driven)…")
            try:
                self._login_programmatic(login_url, user_id, password, seed, mode)
                return
            except Exception as e:
                self.log.warning(f"{mode} login failed: {e}. Falling back to browser/manual prompt.")

        if interactive:
            self.log.info("Zerodha login required (token expired or missing).")
            print()
            print(f"  Choose login method:")
            print(f"    b = Open browser on this machine (default)")
            print(f"    m = Manual / headless (paste URL from another device)")
            print(f"    q = Quit")
            try:
                answer = input("  Choice [b/m/q]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "q"

            if answer == "q":
                raise RuntimeError("User skipped Zerodha login.")
            elif answer == "m":
                self._login_manual(login_url)
                return
            # else: fall through to browser login

            self._login_browser(login_url)
            return

        # interactive=False — caller (live_quotes, swing_capital
        # fetch, scan_one, etc.) explicitly opted out of any
        # blocking IO. We must NOT fall through to `_login_browser()`
        # in this branch — pre-S42 (2026-05-14) the dashboard render
        # path silently launched a browser when the saved token was
        # invalid because the indented `_login_browser` line below
        # was reached regardless. Raise instead so the caller can
        # surface a Re-login toast via `core.error_sink`.
        raise RuntimeError(
            "Zerodha login required but interactive=False. Open the "
            "Login page (Auth pill on any dashboard page) and complete "
            "the manual paste-back flow."
        )

    # ── Login helpers ─────────────────────────────────────────────

    def _exchange_and_save(self, request_token: str):
        """Exchange request_token for access_token and persist it."""
        session = self._kite.generate_session(
            request_token, api_secret=self.cfg.ZERODHA_API_SECRET
        )
        self._kite.set_access_token(session["access_token"])

        with open(self.TOKEN_FILE, "w") as f:
            json.dump({
                "token": session["access_token"],
                "date":  str(now_ist().date()),
            }, f)

        self.log.success("Logged in to Zerodha successfully")

    def _login_browser(self, login_url: str):
        """Browser-based login — opens a local HTTP server to catch the redirect."""
        self.log.info("Opening Zerodha login in browser...")
        self.log.info(f"If it doesn't open automatically: {login_url}")

        captured = []

        class _TokenHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                params = parse_qs(urlparse(self.path).query)
                token  = params.get("request_token", [None])[0]
                if token:
                    captured.append(token)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"<h2>Login successful! Close this tab.</h2>")
            def log_message(self, *args):
                pass

        try:
            server = HTTPServer(("localhost", 8080), _TokenHandler)
        except OSError as e:
            self.log.error(
                f"Cannot start login server on port 8080: {e}. "
                f"Port may be in use. Try manual login mode instead."
            )
            self._login_manual(login_url)
            return

        server.timeout = 300
        webbrowser.open(login_url)

        self.log.info("Waiting for Zerodha login in browser (5 min timeout)...")
        deadline = now_ist() + datetime.timedelta(minutes=5)
        try:
            while not captured:
                server.handle_request()
                if now_ist() >= deadline:
                    raise RuntimeError(
                        "Zerodha login timed out after 5 minutes. "
                        "Re-run the script when you can complete the browser login."
                    )
        finally:
            server.server_close()

        self._exchange_and_save(captured[0])

    def _login_manual(self, login_url: str):
        """
        Headless / manual login for SSH-only VMs.
        User opens the login URL on any device, completes login,
        and pastes the redirect URL back into the terminal.
        """
        print()
        print(f"  ┌─ MANUAL LOGIN ──────────────────────────────────────")
        print(f"  │")
        print(f"  │  1. Open this URL in any browser (phone/laptop):")
        print(f"  │")
        print(f"  │     {login_url}")
        print(f"  │")
        print(f"  │  2. Log in to Zerodha (credentials + TOTP)")
        print(f"  │")
        print(f"  │  3. After login, the browser will show an error page")
        print(f"  │     (localhost refused to connect) — that's normal.")
        print(f"  │     Copy the FULL URL from the browser address bar")
        print(f"  │     and paste it below.")
        print(f"  │")
        print(f"  └────────────────────────────────────────────────────")
        print()

        try:
            redirect_url = input("  Paste redirect URL: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise RuntimeError("User cancelled manual login.")

        # Extract request_token from the pasted URL
        params = parse_qs(urlparse(redirect_url).query)
        request_token = params.get("request_token", [None])[0]

        if not request_token:
            raise RuntimeError(
                "Could not find request_token in the URL you pasted. "
                "Make sure you copied the full URL from the browser address bar."
            )

        self._exchange_and_save(request_token)

    # ---- Programmatic login (AUTO + ASSISTED) ---------------------

    KITE_LOGIN_HOST = "https://kite.zerodha.com"

    def _login_programmatic(
        self,
        login_url: str,
        user_id: str,
        password: str,
        totp_seed: str,
        mode: str,
    ):
        """
        Drives the Kite web login form ourselves. Two modes:
          AUTO     — totp_seed is set; we compute the 6-digit code via pyotp.
          ASSISTED — totp_seed is empty; we prompt for the code (user reads it
                     from their authenticator app or Kite mobile PIN screen).
        On success: caches the access token and returns.
        On any failure raises RuntimeError so the caller can fall back.
        """
        try:
            import requests   # only required for the programmatic flow
        except ImportError:
            raise RuntimeError("`requests` not installed (pip install requests)")

        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (kite-login)"})

        # Step 1 — password
        r = session.post(
            f"{self.KITE_LOGIN_HOST}/api/login",
            data={"user_id": user_id, "password": password},
            timeout=10,
        )
        if r.status_code != 200 or r.json().get("status") != "success":
            raise RuntimeError(f"step 1 (password) HTTP {r.status_code}: {r.text[:200]}")
        d1 = r.json().get("data") or {}
        request_id = d1.get("request_id")
        twofa_type = (d1.get("twofa_type") or "totp").lower()

        # Step 2 — 2FA code
        twofa_value = self._two_factor_code(totp_seed, mode)
        r = session.post(
            f"{self.KITE_LOGIN_HOST}/api/twofa",
            data={
                "user_id":      user_id,
                "request_id":   request_id,
                "twofa_value":  twofa_value,
                "twofa_type":   twofa_type,
                "skip_session": "",
            },
            timeout=10,
        )
        if r.status_code != 200 or r.json().get("status") != "success":
            raise RuntimeError(f"step 2 (2FA) HTTP {r.status_code}: {r.text[:200]}")

        # Step 3 — walk redirect chain to capture request_token
        from urllib.parse import parse_qs as _qs, urlparse as _up
        r = session.get(login_url, allow_redirects=False, timeout=10)
        request_token = None
        for _ in range(6):
            if r.status_code not in (301, 302, 303, 307, 308):
                break
            loc = r.headers.get("Location", "")
            params = _qs(_up(loc).query)
            if "request_token" in params:
                request_token = params["request_token"][0]
                break
            if loc.startswith("/"):
                loc = self.KITE_LOGIN_HOST + loc
            if not loc.startswith(self.KITE_LOGIN_HOST):
                raise RuntimeError(f"step 3 redirect left kite.zerodha.com: {loc[:120]}")
            r = session.get(loc, allow_redirects=False, timeout=10)

        if not request_token:
            raise RuntimeError("step 3 (redirect) no request_token captured")

        # Step 4 — exchange + cache (reuses existing helper)
        self._exchange_and_save(request_token)

    def login_assisted_with_otp(self, otp: str) -> None:
        """Dashboard-friendly assisted login: password from env, OTP from caller.

        Unlike `_login_programmatic` which uses `input()` for ASSISTED mode,
        this method accepts the 6-digit OTP as a string parameter so the
        dashboard can pass it from a form field. Requires KITE_USER_ID and
        KITE_PASSWORD in .env.
        """
        from kiteconnect import KiteConnect
        self._kite = KiteConnect(api_key=self.cfg.ZERODHA_API_KEY)
        login_url = self._kite.login_url()

        user_id  = getattr(self.cfg, "KITE_USER_ID", "") or ""
        password = getattr(self.cfg, "KITE_PASSWORD", "") or ""

        if not user_id or not password:
            raise RuntimeError("KITE_USER_ID and KITE_PASSWORD must be set in .env")
        if not otp or not otp.strip().isdigit() or len(otp.strip()) != 6:
            raise RuntimeError(f"Invalid OTP: expected 6 digits, got '{otp}'")

        try:
            import requests
        except ImportError:
            raise RuntimeError("`requests` not installed (pip install requests)")

        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (kite-login)"})

        # Step 1 — password
        r = session.post(
            f"{self.KITE_LOGIN_HOST}/api/login",
            data={"user_id": user_id, "password": password},
            timeout=10,
        )
        if r.status_code != 200 or r.json().get("status") != "success":
            raise RuntimeError(f"Password step failed: {r.text[:200]}")
        d1 = r.json().get("data") or {}
        request_id = d1.get("request_id")
        twofa_type = (d1.get("twofa_type") or "totp").lower()

        # Step 2 — 2FA with the provided OTP
        r = session.post(
            f"{self.KITE_LOGIN_HOST}/api/twofa",
            data={
                "user_id":      user_id,
                "request_id":   request_id,
                "twofa_value":  otp.strip(),
                "twofa_type":   twofa_type,
                "skip_session": "",
            },
            timeout=10,
        )
        if r.status_code != 200 or r.json().get("status") != "success":
            raise RuntimeError(f"OTP verification failed: {r.text[:200]}")

        # Step 3 — follow redirects to capture request_token
        from urllib.parse import parse_qs as _qs, urlparse as _up
        r = session.get(login_url, allow_redirects=False, timeout=10)
        request_token = None
        for _ in range(6):
            if r.status_code not in (301, 302, 303, 307, 308):
                break
            loc = r.headers.get("Location", "")
            params = _qs(_up(loc).query)
            if "request_token" in params:
                request_token = params["request_token"][0]
                break
            if loc.startswith("/"):
                loc = self.KITE_LOGIN_HOST + loc
            if not loc.startswith(self.KITE_LOGIN_HOST):
                raise RuntimeError(f"Redirect left kite.zerodha.com: {loc[:120]}")
            r = session.get(loc, allow_redirects=False, timeout=10)

        if not request_token:
            raise RuntimeError("Could not capture request_token from redirect chain")

        # Step 4 — exchange + save
        self._exchange_and_save(request_token)

    def _two_factor_code(self, totp_seed: str, mode: str) -> str:
        """AUTO mode computes via pyotp; ASSISTED mode prompts the user."""
        if mode == "AUTO":
            try:
                import pyotp
            except ImportError:
                raise RuntimeError("AUTO mode needs pyotp (pip install pyotp)")
            return pyotp.TOTP(totp_seed).now()

        # ASSISTED — prompt for 6 digits
        print()
        print("  Open your authenticator app (Apple Passwords / Authy / Google Auth)")
        print("  or read the 6-digit code from your Kite mobile app.")
        print()
        for _attempt in range(3):
            try:
                code = input("  Enter 6-digit code: ").strip()
            except (EOFError, KeyboardInterrupt):
                raise RuntimeError("user cancelled assisted login")
            if code.isdigit() and len(code) == 6:
                return code
            print(f"  -> '{code}' is not 6 digits, try again.")
        raise RuntimeError("3 invalid codes entered")

    def force_relogin(self):
        """Deletes the cached token and triggers a fresh browser login."""
        if os.path.exists(self.TOKEN_FILE):
            os.remove(self.TOKEN_FILE)
            self.log.info("Deleted stale access token")
        self.login()

    # ================================================================
    # HOLDINGS
    # ================================================================

    def get_holdings(self) -> list[dict]:
        """
        Returns all stocks in your demat account.
        Each dict contains: symbol, exchange, quantity, avg_buy_price,
        current_price, current_value, invested_value, pnl, pnl_percent.
        """
        self._require_login()
        raw = self._kite.holdings()

        holdings = []
        for h in raw:
            avg  = h.get("average_price", 0)
            last = h.get("last_price",    0)
            qty  = h.get("quantity",      0)
            pnl  = h.get("pnl",          0)

            holdings.append({
                "symbol":         h["tradingsymbol"],
                "exchange":       h.get("exchange", "NSE"),
                "quantity":       qty,
                "avg_buy_price":  round(avg,  2),
                "current_price":  round(last, 2),
                "current_value":  round(qty * last, 2),
                "invested_value": round(qty * avg,  2),
                "pnl":            round(pnl, 2),
                "pnl_percent":    round((pnl / (qty * avg)) * 100, 2)
                                  if qty > 0 and avg > 0 else 0,
            })

        return holdings

    # ================================================================
    # LIVE QUOTES
    # ================================================================

    def get_quotes(self, stocks: list[dict]) -> dict:
        """
        Fetches live prices for ALL stocks in ONE Kite API call.
        stocks = list of {"symbol": "TCS", "exchange": "NSE"} dicts.
        Returns raw Kite quote dict keyed by "EXCHANGE:SYMBOL".

        This single-call approach is the core speed advantage of the
        paid plan over Yahoo Finance (which needs one call per stock).
        """
        self._require_login()
        instruments = [f"{s['exchange']}:{s['symbol']}" for s in stocks]
        result      = {}

        # Kite allows max 500 instruments per quote call
        for i in range(0, len(instruments), 500):
            result.update(self._kite.quote(instruments[i:i + 500]))

        return result

    # ----------------------------------------------------------------
    # Typed quote API (Roadmap #261)
    # ----------------------------------------------------------------
    # Five+ sites in the codebase parse the raw Kite quote dict to
    # pull out last_price / volume / depth / spread / impact-cost.
    # Each one re-implements its own fail-safe defaulting and shape
    # checking. That has produced subtle bugs (e.g. reading "depth"
    # on a non-dict, treating missing average_price as 0).
    #
    # The `Quote` dataclass below is the single canonical view of a
    # Kite quote payload. `get_typed_quotes()` is the new entry point;
    # callers ready to migrate get a typed object with helper methods
    # (`best_bid`, `best_ask`, `spread_pct`, `impact_cost_pct`).
    # The raw `get_quotes()` API is kept for backward compatibility
    # so existing call sites need not change in this pass.
    def get_typed_quotes(
        self,
        stocks: list[dict],
        max_retries: int = 3,
    ) -> dict[str, "Quote"]:
        """
        Wraps `get_quotes_safe()` and converts each raw Kite payload
        into a `Quote` dataclass. Returns an empty dict on total
        failure (callers must check; never None — typed contract).
        Skips entries with non-dict payloads (Kite occasionally
        returns sparse responses for illiquid names).
        """
        raw = self.get_quotes_safe(stocks, max_retries=max_retries) or {}
        out: dict[str, Quote] = {}
        for key, payload in raw.items():
            if not isinstance(payload, dict):
                continue
            try:
                out[key] = Quote.from_kite_dict(key, payload)
            except Exception as e:
                # Never raise from the typed wrapper; fall back to
                # exclusion so the caller behaves like quote was
                # missing (a known fail-closed code path).
                self.log.debug(f"Quote parse failed for {key}: {e}")
        return out

    def get_quotes_safe(
        self,
        stocks: list[dict],
        max_retries: int = 3,
        delay_seconds: float = 1.0,
    ) -> dict | None:
        """
        Fetches quotes with automatic retry.
        Returns the quotes dict, or None if all attempts fail.
        """
        max_retries = max(1, int(max_retries))
        last_error = None
        relogged = False

        for attempt in range(1, max_retries + 1):
            try:
                return self.get_quotes(stocks)
            except Exception as e:
                last_error = e
                msg = str(e).lower()
                if (
                    ("api_key" in msg or "access_token" in msg)
                    and not relogged
                ):
                    self.log.info("Token appears invalid — forcing re-login...")
                    self.force_relogin()
                    relogged = True

                if attempt < max_retries:
                    wait = delay_seconds * attempt
                    self.log.warning(
                        f"Quote fetch failed (attempt {attempt}/{max_retries}): "
                        f"{e} | Retrying in {wait:.0f}s..."
                    )
                    time.sleep(wait)

        self.log.error(
            f"Quote fetch failed after {max_retries} attempts: {last_error}"
        )
        return None

    # ================================================================
    # HISTORICAL DATA
    # ================================================================

    def get_historical(
        self,
        symbol:    str,
        exchange:  str,
        from_date: datetime.date | datetime.datetime,
        to_date:   datetime.date | datetime.datetime,
        interval:  str = "day",
    ) -> list[dict]:
        """
        Fetches OHLCV candles for one stock over a date range.
        Returns list of dicts: {date, open, high, low, close, volume}.
        Requires connect_paid plan — raises RuntimeError otherwise.

        Rate-limited to ~3 req/sec to stay within Zerodha's API limits.
        Uses a simple timestamp-based throttle (no sleep if enough time
        has passed since the last call).

        Supported intervals:
          minute, 3minute, 5minute, 10minute, 15minute,
          30minute, 60minute, day
        For intraday intervals, pass datetime objects with time
        components for from_date/to_date.
        """
        self._require_login()

        if not self.cfg.zerodha()["historical_data"]:
            raise RuntimeError(
                "Historical data requires zerodha_plan = 'connect_paid'. "
                "Update ZERODHA_PLAN in config.py."
            )

        token = self._get_instrument_token(symbol, exchange)
        if not token:
            self.log.warning(f"No instrument token found for {symbol} ({exchange})")
            return []

        # Throttle: ensure at least 350ms between historical API calls
        # (Zerodha rate limit is ~3 req/sec)
        now = time.time()
        elapsed = now - self._last_historical_call
        if elapsed < 0.35:
            time.sleep(0.35 - elapsed)
        self._last_historical_call = time.time()

        return self._kite.historical_data(
            instrument_token = token,
            from_date        = from_date,
            to_date          = to_date,
            interval         = interval,
        )

    # ================================================================
    # INSTRUMENT TOKEN LOOKUP
    # ================================================================

    def load_instruments(self) -> tuple[dict, dict]:
        """
        Loads the full NSE and BSE instrument lists from Kite.
        Returns (nse_tokens, bse_tokens) — dicts mapping symbol → token.

        Called once per session by MarketData (not per stock).
        Results are cached on self so subsequent calls are instant.
        """
        self._require_login()

        if self._nse_tokens is None:
            self.log.info("Loading instrument list (one-time)...")
            nse_instruments = self._kite.instruments("NSE")
            bse_instruments = self._kite.instruments("BSE")
            self._nse_tokens = {
                i["tradingsymbol"]: i["instrument_token"]
                for i in nse_instruments
            }
            self._bse_tokens = {
                i["tradingsymbol"]: i["instrument_token"]
                for i in bse_instruments
            }
            self._tick_sizes = {}
            for i in nse_instruments:
                self._tick_sizes[f"NSE:{i['tradingsymbol']}"] = i.get("tick_size", 0.05)
            for i in bse_instruments:
                self._tick_sizes[f"BSE:{i['tradingsymbol']}"] = i.get("tick_size", 0.05)

        return self._nse_tokens, self._bse_tokens

    def _get_instrument_token(self, symbol: str, exchange: str) -> int | None:
        """Internal helper — loads instrument cache if needed."""
        nse, bse = self.load_instruments()
        tokens   = nse if exchange == "NSE" else bse
        return tokens.get(symbol)

    def get_tick_size(self, symbol: str, exchange: str = "NSE") -> float:
        """Returns the tick size for a symbol (defaults to 0.05 if unknown)."""
        self.load_instruments()  # ensure cache is populated
        return self._tick_sizes.get(f"{exchange}:{symbol}", 0.05)

    def round_to_tick(self, price: float, tick: float) -> float:
        """Round price to the nearest multiple of tick size."""
        return round(round(price / tick) * tick, 2)

    # ================================================================
    # ORDER METHODS — Phase 2
    # ================================================================
    # place_order sends a real order to Zerodha via Kite API.
    # The OrderEngine decides whether to call this (live mode) or
    # just log the order (dry-run mode). This class always executes.
    #
    # cancel_order and get_positions are used by the monitor loop
    # and square-off logic.
    # ================================================================

    def place_order(
        self,
        symbol:     str,
        exchange:   str,
        qty:        int,
        side:       str,           # "BUY" or "SELL"
        order_type: str = "MARKET",
        price:      float = 0,
        max_retries: int = 3,
    ) -> str:
        """
        Places an intraday (MIS) order on Zerodha with retry logic.

        Args:
            symbol:     Trading symbol e.g. "RELIANCE"
            exchange:   "NSE" or "BSE"
            qty:        Number of shares
            side:       "BUY" or "SELL"
            order_type: "MARKET" or "LIMIT"
            price:      Required if order_type is "LIMIT"
            max_retries: Number of times to retry on failure (default 3)

        Returns:
            Zerodha order ID string on success.

        Raises:
            RuntimeError if order placement fails after all retries.

        Note: product="MIS" means intraday — Zerodha auto-squares
        any MIS position at 3:20 PM if you don't close it yourself.
        """
        self._require_login()

        transaction = (
            self._kite.TRANSACTION_TYPE_BUY if side.upper() == "BUY"
            else self._kite.TRANSACTION_TYPE_SELL
        )

        order_params = {
            "tradingsymbol":    symbol,
            "exchange":         exchange,
            "transaction_type": transaction,
            "quantity":         qty,
            "product":          self._kite.PRODUCT_MIS,     # Intraday
            "order_type":       self._kite.ORDER_TYPE_MARKET,
            "validity":         self._kite.VALIDITY_DAY,
        }

        # For LIMIT orders, round price to instrument tick size and set
        if order_type.upper() == "LIMIT" and price > 0:
            order_params["order_type"] = self._kite.ORDER_TYPE_LIMIT
            tick = self.get_tick_size(symbol, exchange)
            order_params["price"] = self.round_to_tick(price, tick)

        # Market protection is mandatory for MARKET/SL-M orders via API.
        # -1 = automatic protection applied by Zerodha per their guidelines.
        if order_params["order_type"] in (
            self._kite.ORDER_TYPE_MARKET,
            getattr(self._kite, "ORDER_TYPE_SLM", "SL-M"),
        ):
            order_params["market_protection"] = -1

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                order_id = self._kite.place_order(
                    variety=self._kite.VARIETY_REGULAR,
                    **order_params,
                )
                self.log.success(
                    f"Zerodha order placed: {side} {qty}x {symbol} | ID: {order_id}"
                )
                return str(order_id)

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    # CRITICAL: before retrying, check whether the
                    # previous attempt actually reached Zerodha. A network
                    # timeout reading the response means the order may
                    # already be live on the exchange — a blind retry
                    # would create a DUPLICATE position. Look for any
                    # order matching (symbol, side, qty) placed within
                    # the last 90 s. If found, return its ID instead of
                    # placing a second order.
                    dup = self._find_recent_matching_order(
                        symbol=symbol, side=side, qty=qty,
                        order_type=order_params["order_type"],
                        max_age_seconds=90,
                    )
                    if dup:
                        self.log.warning(
                            f"Zerodha order retry: previous attempt "
                            f"({side} {qty}x {symbol}) appears to have "
                            f"reached the exchange (order {dup} found). "
                            f"Returning existing order ID — NOT retrying."
                        )
                        return str(dup)

                    wait = attempt * 2  # 2s, 4s backoff
                    self.log.warning(
                        f"Zerodha order failed (attempt {attempt}/{max_retries}): "
                        f"{side} {qty}x {symbol} — {e} | Retrying in {wait}s..."
                    )
                    time.sleep(wait)
                else:
                    self.log.error(
                        f"Zerodha order FAILED after {max_retries} attempts: "
                        f"{side} {qty}x {symbol} — {e}"
                    )

        raise RuntimeError(
            f"Order placement failed after {max_retries} retries: {last_error}"
        ) from last_error

    def _find_recent_matching_order(
        self,
        symbol:           str,
        side:             str,
        qty:              int,
        order_type:       str,
        max_age_seconds:  int = 90,
    ) -> str | None:
        """Return the order_id of the most recent order matching
        (symbol, side, qty, order_type) placed within `max_age_seconds`,
        or None if no match exists.

        Used by `place_order` to detect duplicate-fire scenarios where
        a network timeout swallowed the response of a successful
        placement. Without this guard the retry loop would create a
        second live order.

        Fail-safe: any exception fetching orders returns None (caller
        falls back to retrying — accepting the duplicate-risk on top of
        whatever original failure motivated the retry, which is no
        worse than the legacy behaviour). The exception is logged at
        WARNING because duplicate-order detection is safety-critical;
        a silent failure would mask degraded protection.
        """
        try:
            orders = self._kite.orders() or []
        except Exception as e:
            self.log.warning(
                f"_find_recent_matching_order: kite.orders() failed "
                f"({type(e).__name__}: {e}) — duplicate-order detection "
                f"degraded for this retry. Caller will fall back to plain retry."
            )
            return None
        try:
            from config import now_ist
            now = now_ist()
        except Exception as e:
            self.log.warning(
                f"_find_recent_matching_order: clock read failed "
                f"({type(e).__name__}: {e}) — skipping duplicate check."
            )
            return None
        # Match exchange transaction type
        wanted_txn = (
            self._kite.TRANSACTION_TYPE_BUY if side.upper() == "BUY"
            else self._kite.TRANSACTION_TYPE_SELL
        )
        # Order types like "MARKET", "SL-M", "LIMIT" come back as the same
        # string Kite expects on placement, so equality is fine.
        candidates = []
        for o in orders:
            try:
                if o.get("tradingsymbol") != symbol:
                    continue
                if o.get("transaction_type") != wanted_txn:
                    continue
                if int(o.get("quantity", 0) or 0) != int(qty):
                    continue
                if o.get("order_type") != order_type:
                    continue
                # Skip explicitly-rejected/cancelled orders — they aren't
                # live on the exchange so a retry is safe.
                if o.get("status") in ("REJECTED", "CANCELLED"):
                    continue
                ts = o.get("order_timestamp")
                if ts is None:
                    continue
                # Kite returns naive datetime in IST (or string).
                if isinstance(ts, str):
                    try:
                        ts = datetime.datetime.fromisoformat(ts)
                    except Exception:
                        continue
                age = (now - ts).total_seconds()
                if 0 <= age <= max_age_seconds:
                    candidates.append((age, str(o.get("order_id") or "")))
            except Exception:
                continue
        if not candidates:
            return None
        # Most recent match
        candidates.sort()
        return candidates[0][1] or None

    def cancel_order(self, order_id: str):
        """
        Cancels a pending order by its Zerodha order ID.
        Logs a warning if the order is already executed/cancelled.
        """
        self._require_login()
        try:
            self._kite.cancel_order(
                variety=self._kite.VARIETY_REGULAR,
                order_id=order_id,
            )
            self.log.success(f"Order cancelled: {order_id}")
        except Exception as e:
            # "order does not exist" is the expected case during EOD
            # square-off cleanup: the SL-M may have already been filled
            # or auto-cancelled by Zerodha. Demote to debug so the
            # user-facing log isn't polluted on every clean shutdown.
            msg = str(e).lower()
            if "does not exist" in msg or "already" in msg:
                self.log.debug(f"Order {order_id} already terminal: {e}")
            else:
                self.log.warning(f"Could not cancel order {order_id}: {e}")

    def place_sl_m_order(
        self,
        symbol:        str,
        exchange:      str,
        qty:           int,
        side:          str,            # "BUY" or "SELL"
        trigger_price: float,
    ) -> str | None:
        """
        Places an SL-M (stop-loss market) intraday order on Zerodha.
        This order sits on the exchange and triggers instantly when
        price hits the trigger_price — no polling delay.

        Args:
            symbol:        Trading symbol e.g. "RELIANCE"
            exchange:      "NSE" or "BSE"
            qty:           Number of shares
            side:          "BUY" (to cover a short) or "SELL" (to stop a long)
            trigger_price: Price at which the SL-M triggers

        Returns:
            Zerodha order ID string on success, None on failure.
        """
        self._require_login()

        transaction = (
            self._kite.TRANSACTION_TYPE_BUY if side.upper() == "BUY"
            else self._kite.TRANSACTION_TYPE_SELL
        )

        # Round trigger to instrument tick size (e.g. 0.05 or 0.10)
        tick = self.get_tick_size(symbol, exchange)
        trigger_price = self.round_to_tick(trigger_price, tick)

        # Two attempts with a 1-second pause between them. Kite occasionally
        # returns a transient 502 / connection-reset on order placement;
        # one retry catches >90% of those without risking a duplicate
        # order (the per-call log makes any duplicate immediately
        # visible to the operator, and Zerodha's intraday duplicate-order
        # rejection would surface in the second attempt's exception).
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                order_id = self._kite.place_order(
                    variety=self._kite.VARIETY_REGULAR,
                    tradingsymbol=symbol,
                    exchange=exchange,
                    transaction_type=transaction,
                    quantity=qty,
                    product=self._kite.PRODUCT_MIS,
                    order_type=getattr(self._kite, "ORDER_TYPE_SLM", "SL-M"),
                    trigger_price=trigger_price,
                    validity=self._kite.VALIDITY_DAY,
                    market_protection=-1,
                )
                self.log.success(
                    f"SL-M order placed: {side} {qty}x {symbol} "
                    f"trigger Rs.{trigger_price:.2f} | ID: {order_id}"
                    + (f" (attempt {attempt})" if attempt > 1 else "")
                )
                return str(order_id)
            except Exception as e:
                last_exc = e
                if attempt == 1:
                    self.log.warning(
                        f"SL-M order attempt {attempt}/2 failed for "
                        f"{side} {qty}x {symbol} trigger "
                        f"Rs.{trigger_price:.2f} — retrying in 1s: "
                        f"{type(e).__name__}: {e}"
                    )
                    time.sleep(1)
        # Both attempts failed — fall through to the final ERROR log.
        self.log.error(
            f"SL-M order FAILED: {side} {qty}x {symbol} "
            f"trigger Rs.{trigger_price:.2f} — {last_exc}"
        )
        return None

    def modify_order(
        self,
        order_id:      str,
        trigger_price: float | None = None,
        price:         float | None = None,
        quantity:      int | None = None,
        symbol:        str | None = None,
        exchange:      str | None = None,
    ) -> bool:
        """
        Modifies a pending order on Zerodha (e.g. update SL-M trigger).

        Returns True on success, False on failure.
        """
        self._require_login()

        kwargs = {}
        if trigger_price is not None:
            tick = self.get_tick_size(symbol, exchange) if symbol and exchange else 0.05
            kwargs["trigger_price"] = self.round_to_tick(trigger_price, tick)
        if price is not None:
            kwargs["price"] = price
        if quantity is not None:
            kwargs["quantity"] = quantity

        try:
            self._kite.modify_order(
                variety=self._kite.VARIETY_REGULAR,
                order_id=order_id,
                **kwargs,
            )
            self.log.info(f"Order modified: {order_id} | {kwargs}")
            return True
        except Exception as e:
            self.log.warning(f"Could not modify order {order_id}: {e}")
            return False

    def get_positions(self) -> dict:
        """
        Returns current day's positions from Zerodha.
        Returns dict with 'net' and 'day' position lists.
        """
        self._require_login()
        return self._kite.positions()

    # ================================================================
    # FUNDS & MARGINS
    # ================================================================

    def get_available_funds(self) -> float:
        """
        Returns available margin in the equity segment.
        Uses Kite Connect /user/margins endpoint.

        Returns 'available.live_balance' which includes cash,
        intraday payin, and collateral — the actual usable amount
        for placing new orders.
        """
        self._require_login()
        margins = self._kite.margins(segment="equity")
        return float(margins["available"]["live_balance"])

    # ================================================================
    # ACCOUNT SNAPSHOT
    # ================================================================

    def print_account_snapshot(self):
        """
        Prints a quick overview of the Zerodha account:
        available balance, portfolio size, invested vs current value.

        Returns the available funds amount (or 0 if fetch failed).
        Reusable by both Phase 1 (analyser) and Phase 2 (manager).
        """
        self.log.section("ACCOUNT SNAPSHOT")

        funds = 0.0
        try:
            funds = self.get_available_funds()
            self.log.info(f"Available balance: \u20b9{funds:,.2f}")
        except Exception:
            self.log.warning("Could not fetch available balance")

        try:
            holdings = self.get_holdings()
            if holdings:
                invested = sum(h["invested_value"] for h in holdings)
                current  = sum(h["current_value"]  for h in holdings)
                pnl      = current - invested
                pnl_pct  = (pnl / invested * 100) if invested > 0 else 0
                pnl_color = "\033[92m" if pnl >= 0 else "\033[91m"
                reset     = "\033[0m"

                self.log.info(f"Stocks in portfolio: {len(holdings)}")
                self.log.info(f"Invested value     : \u20b9{invested:,.2f}")
                self.log.info(f"Current value      : \u20b9{current:,.2f}")
                self.log.info(
                    f"Portfolio P&L      : {pnl_color}\u20b9{pnl:+,.2f} "
                    f"({pnl_pct:+.2f}%){reset}"
                )
            else:
                self.log.info("No stocks in portfolio")
        except Exception:
            self.log.warning("Could not fetch portfolio holdings")

        return funds

    # ================================================================
    # ORDER FILL PRICE
    # ================================================================

    def get_order_fill_price(self, order_id: str, timeout: int = 15) -> float | None:
        """
        Polls Zerodha order trades to get the actual average fill price.
        MARKET orders fill almost instantly, but we retry a few times
        in case there's a brief delay.

        Returns the weighted-average fill price, or None if not filled.
        """
        import time
        self._require_login()

        for attempt in range(timeout):
            try:
                trades = self._kite.order_trades(order_id)
                if trades:
                    # Weighted average price across all fills
                    total_qty   = sum(t["quantity"] for t in trades)
                    total_value = sum(t["quantity"] * t["average_price"] for t in trades)
                    if total_qty > 0:
                        avg_price = round(total_value / total_qty, 2)
                        self.log.info(
                            f"Fill confirmed: Order {order_id} | "
                            f"Avg price: \u20b9{avg_price:.2f} ({len(trades)} fill(s))"
                        )
                        return avg_price
            except Exception:
                pass  # Order may not be in terminal state yet

            if attempt < timeout - 1:
                time.sleep(1)

        self.log.warning(f"Could not get fill price for order {order_id} after {timeout}s")
        return None

    def get_order_filled_qty(self, order_id: str) -> int | None:
        """Returns total filled quantity for an order, or None on failure."""
        self._require_login()
        try:
            trades = self._kite.order_trades(order_id)
            if trades:
                return sum(t["quantity"] for t in trades)
            return 0
        except Exception:
            return None

    def get_order_status(self, order_id: str) -> str | None:
        """
        Returns the current status string for an order, e.g. "COMPLETE",
        "OPEN", "TRIGGER PENDING", "CANCELLED", "REJECTED", or None on failure.
        Uses Kite's order_history() which returns all status transitions —
        we read the latest (terminal) status.
        """
        self._require_login()
        try:
            history = self._kite.order_history(order_id)
            if history:
                return history[-1].get("status")
            return None
        except Exception:
            return None

    def get_orders(self) -> list[dict]:
        """
        Fetches all orders placed today (pending, completed, cancelled, rejected).
        Used by startup reconciliation to find orphan SL-M orders.

        Returns a list of order dicts. Each dict has at minimum:
          order_id, tradingsymbol, exchange, transaction_type (BUY/SELL),
          quantity, order_type (MARKET/LIMIT/SL/SL-M), product (MIS/CNC),
          status (OPEN, TRIGGER PENDING, COMPLETE, CANCELLED, REJECTED),
          trigger_price, price, average_price, order_timestamp.

        Returns [] on API failure (never raises) so startup can continue.
        """
        self._require_login()
        try:
            return list(self._kite.orders() or [])
        except Exception as e:
            self.log.warning(f"get_orders() failed: {e}")
            return []

    # ================================================================
    # END-OF-DAY TRADE RECONCILIATION
    # ================================================================

    def get_todays_trades(self) -> list[dict]:
        """
        Fetches all executed trades for today from Zerodha.
        Returns list of dicts with: tradingsymbol, exchange,
        transaction_type (BUY/SELL), quantity, average_price,
        order_id, product, fill_timestamp.

        Uses Kite's trades() endpoint which gives actual fills
        (not orders — one order can have multiple fills).
        """
        self._require_login()
        try:
            raw = self._kite.trades()
            return raw or []
        except Exception as e:
            self.log.error(f"Failed to fetch today's trades from Zerodha: {e}")
            return []

    def get_todays_positions(self) -> list[dict]:
        """
        Fetches today's day-level positions from Zerodha.
        Each position has: tradingsymbol, exchange, product,
        buy_quantity, sell_quantity, buy_price, sell_price,
        quantity (net), pnl, realised, unrealised, etc.
        """
        self._require_login()
        try:
            data = self._kite.positions()
            return data.get("day", [])
        except Exception as e:
            self.log.error(f"Failed to fetch today's positions from Zerodha: {e}")
            return []

    # ================================================================
    # INTERNAL HELPERS
    # ================================================================

    def _require_login(self):
        """Raises a clear error if login() hasn't been called yet."""
        if self._kite is None:
            raise RuntimeError(
                "ZerodhaClient not logged in. "
                "Call login() before using any other methods."
            )


# ================================================================
# TYPED MARKET-DATA OBJECTS (Roadmap #261)
# ================================================================
# Single canonical view of a Kite quote payload. The scanner, engine,
# manager, and audit scripts have so far each parsed the raw quote
# dict in subtly different ways:
#   - some default missing fields to 0, others to None;
#   - some treat `last_price=0` as "no price", others let it through;
#   - some read `depth.buy[0]` as a dict, others as a list-of-numbers.
# Those drifts are the source class of "fail-open when we should have
# fail-closed" bugs. `Quote` + `DepthLevel` collapse all parsing into
# one place with explicit defaults and helper methods for the
# decisions the bot actually makes (best bid/ask, spread %, impact
# cost over qty).
#
# Migration plan: callers are converted incrementally to
# `ZerodhaClient.get_typed_quotes()` over the next few items. The raw
# `get_quotes()` API is retained verbatim so no existing call site
# needs to move on day one.
# ================================================================

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DepthLevel:
    """One level of the order book — never NaN, never None."""
    price: float = 0.0
    quantity: int = 0
    orders: int = 0

    @classmethod
    def from_kite_dict(cls, raw: dict) -> "DepthLevel":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            price=_safe_float(raw.get("price"), 0.0),
            quantity=_safe_int(raw.get("quantity"), 0),
            orders=_safe_int(raw.get("orders"), 0),
        )

    @property
    def is_valid(self) -> bool:
        """Treat zero-price OR zero-qty as fail-closed depth."""
        return self.price > 0 and self.quantity > 0


@dataclass
class Quote:
    """
    Canonical typed view of a Kite quote payload. All numeric fields
    default to 0.0 (or 0 / "" for non-floats). All depth lists default
    to empty list (NEVER `None`) so callers can iterate safely.

    Use `Quote.from_kite_dict()` to construct; never instantiate the
    fields manually unless you're writing a test fixture.
    """
    instrument: str = ""              # e.g. "NSE:RELIANCE"
    instrument_token: int = 0
    last_price: float = 0.0
    average_price: float = 0.0        # broker session VWAP
    volume: int = 0
    buy_quantity: int = 0
    sell_quantity: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0                # previous-day close
    last_quantity: int = 0
    last_trade_time: str = ""
    timestamp: str = ""
    bids: List[DepthLevel] = field(default_factory=list)
    asks: List[DepthLevel] = field(default_factory=list)
    raw: dict = field(default_factory=dict)   # original payload, for debug

    # ── construction ────────────────────────────────────────────

    @classmethod
    def from_kite_dict(cls, instrument: str, raw: dict) -> "Quote":
        """
        Parse a single Kite quote payload (one value of the
        `kite.quote()` return dict). Robust against missing keys,
        non-dict depth, and stringified numerics. Stores the raw
        payload too so callers can fall back to legacy parsing
        during migration.
        """
        ohlc = raw.get("ohlc", {}) if isinstance(raw, dict) else {}
        depth = raw.get("depth", {}) if isinstance(raw, dict) else {}
        bids_raw = depth.get("buy", []) if isinstance(depth, dict) else []
        asks_raw = depth.get("sell", []) if isinstance(depth, dict) else []

        return cls(
            instrument=instrument,
            instrument_token=_safe_int(raw.get("instrument_token"), 0),
            last_price=_safe_float(raw.get("last_price"), 0.0),
            average_price=_safe_float(raw.get("average_price"), 0.0),
            volume=_safe_int(raw.get("volume"), 0),
            buy_quantity=_safe_int(raw.get("buy_quantity"), 0),
            sell_quantity=_safe_int(raw.get("sell_quantity"), 0),
            open=_safe_float(ohlc.get("open"), 0.0),
            high=_safe_float(ohlc.get("high"), 0.0),
            low=_safe_float(ohlc.get("low"), 0.0),
            close=_safe_float(ohlc.get("close"), 0.0),
            last_quantity=_safe_int(raw.get("last_quantity"), 0),
            last_trade_time=str(raw.get("last_trade_time") or ""),
            timestamp=str(raw.get("timestamp") or ""),
            bids=[DepthLevel.from_kite_dict(b) for b in (bids_raw or [])],
            asks=[DepthLevel.from_kite_dict(a) for a in (asks_raw or [])],
            raw=raw if isinstance(raw, dict) else {},
        )

    # ── derived views (the actual decisions the bot makes) ──────

    @property
    def is_priced(self) -> bool:
        """LTP > 0 — fail-closed gate input for entry quote check."""
        return self.last_price > 0

    @property
    def best_bid(self) -> float:
        """Highest priced bid level, or 0.0 if depth empty."""
        for b in self.bids:
            if b.is_valid:
                return b.price
        return 0.0

    @property
    def best_ask(self) -> float:
        """Lowest priced ask level, or 0.0 if depth empty."""
        for a in self.asks:
            if a.is_valid:
                return a.price
        return 0.0

    @property
    def has_two_sided_book(self) -> bool:
        """Both bid and ask levels present and priced."""
        return self.best_bid > 0 and self.best_ask > 0 and self.best_ask >= self.best_bid

    def spread_pct(self) -> float:
        """
        Returns (ask-bid)/mid * 100. Returns 0.0 when book is missing
        — caller MUST check `has_two_sided_book` first if they need
        fail-closed behaviour.
        """
        if not self.has_two_sided_book:
            return 0.0
        mid = (self.best_bid + self.best_ask) / 2.0
        if mid <= 0:
            return 0.0
        return (self.best_ask - self.best_bid) / mid * 100.0

    def impact_cost_pct(self, qty: int, side: str) -> float:
        """
        Walk top-5 levels of the relevant side and compute the
        weighted-average fill price for `qty`, returning the deviation
        from LTP as a percent (positive = adverse).

        side = 'BUY'  → walks ask side (we lift offers).
        side = 'SELL' → walks bid side (we hit bids).

        Returns 0.0 when book is empty or insufficient depth — caller
        MUST verify the book is non-empty for fail-closed behaviour.
        """
        if qty <= 0 or self.last_price <= 0:
            return 0.0
        levels = self.asks if side == "BUY" else self.bids
        if not levels:
            return 0.0
        remaining = qty
        cost = 0.0
        filled = 0
        for lev in levels:
            if not lev.is_valid:
                continue
            take = min(remaining, lev.quantity)
            cost += take * lev.price
            filled += take
            remaining -= take
            if remaining <= 0:
                break
        if filled == 0:
            return 0.0
        avg_fill = cost / filled
        if side == "BUY":
            return (avg_fill - self.last_price) / self.last_price * 100.0
        return (self.last_price - avg_fill) / self.last_price * 100.0


def _safe_float(v, default: float) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v, default: int) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default
