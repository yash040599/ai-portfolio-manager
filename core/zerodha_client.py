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
import datetime
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from config     import Config
from core.logger import Logger


class ZerodhaClient:

    TOKEN_FILE = "access_token.json"

    def __init__(self, config: type[Config], log: Logger):
        self.cfg   = config
        self.log   = log
        self._kite = None   # set by login()

        # Instrument token cache — loaded once, reused per session
        self._nse_tokens: dict | None = None
        self._bse_tokens: dict | None = None

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

    def login(self):
        from kiteconnect import KiteConnect
        self._kite = KiteConnect(api_key=self.cfg.ZERODHA_API_KEY)

        # Reuse today's saved token if available
        if os.path.exists(self.TOKEN_FILE):
            with open(self.TOKEN_FILE) as f:
                saved = json.load(f)
            if saved.get("date") == str(datetime.date.today()):
                self.log.success("Using saved Zerodha login token from today")
                self._kite.set_access_token(saved["token"])
                return

        # No valid token — open browser OAuth flow
        login_url = self._kite.login_url()
        self.log.info(f"Opening Zerodha login in browser...")
        self.log.info(f"If it doesn't open automatically: {login_url}")

        captured = []

        # Minimal local server — sole purpose is to catch the redirect token
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
                pass  # Suppress HTTP server noise in terminal

        server = HTTPServer(("localhost", 8080), _TokenHandler)
        webbrowser.open(login_url)

        self.log.info("Waiting for Zerodha login in browser...")
        while not captured:
            server.handle_request()

        # Exchange one-time request_token for a reusable access_token
        session = self._kite.generate_session(
            captured[0], api_secret=self.cfg.ZERODHA_API_SECRET
        )
        self._kite.set_access_token(session["access_token"])

        # Persist for today's subsequent runs
        with open(self.TOKEN_FILE, "w") as f:
            json.dump({
                "token": session["access_token"],
                "date":  str(datetime.date.today()),
            }, f)

        self.log.success("Logged in to Zerodha successfully")

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
                                  if avg > 0 else 0,
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

    # ================================================================
    # HISTORICAL DATA
    # ================================================================

    def get_historical(
        self,
        symbol:    str,
        exchange:  str,
        from_date: datetime.date,
        to_date:   datetime.date,
        interval:  str = "day",
    ) -> list[dict]:
        """
        Fetches OHLCV daily candles for one stock over a date range.
        Returns list of dicts: {date, open, high, low, close, volume}.
        Requires connect_paid plan — raises RuntimeError otherwise.
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
            self._nse_tokens = {
                i["tradingsymbol"]: i["instrument_token"]
                for i in self._kite.instruments("NSE")
            }
            self._bse_tokens = {
                i["tradingsymbol"]: i["instrument_token"]
                for i in self._kite.instruments("BSE")
            }

        return self._nse_tokens, self._bse_tokens

    def _get_instrument_token(self, symbol: str, exchange: str) -> int | None:
        """Internal helper — loads instrument cache if needed."""
        nse, bse = self.load_instruments()
        tokens   = nse if exchange == "NSE" else bse
        return tokens.get(symbol)

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
    ) -> str:
        """
        Places an intraday (MIS) order on Zerodha.

        Args:
            symbol:     Trading symbol e.g. "RELIANCE"
            exchange:   "NSE" or "BSE"
            qty:        Number of shares
            side:       "BUY" or "SELL"
            order_type: "MARKET" or "LIMIT"
            price:      Required if order_type is "LIMIT"

        Returns:
            Zerodha order ID string on success.

        Raises:
            RuntimeError if order placement fails.

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

        # For LIMIT orders, set the price
        if order_type.upper() == "LIMIT" and price > 0:
            order_params["order_type"] = self._kite.ORDER_TYPE_LIMIT
            order_params["price"] = price

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
            self.log.error(f"Zerodha order failed: {side} {qty}x {symbol} — {e}")
            raise RuntimeError(f"Order placement failed: {e}") from e

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
            self.log.warning(f"Could not cancel order {order_id}: {e}")

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
    # INTERNAL HELPERS
    # ================================================================

    def _require_login(self):
        """Raises a clear error if login() hasn't been called yet."""
        if self._kite is None:
            raise RuntimeError(
                "ZerodhaClient not logged in. "
                "Call login() before using any other methods."
            )
