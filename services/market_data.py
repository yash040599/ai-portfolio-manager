# ================================================================
# services/market_data.py
# ================================================================
# Enriches a portfolio with live prices and 1-year market history.
#
# Routes automatically between two data sources:
#   Kite live   → when zerodha_plan = "connect_paid"
#                 One API call for ALL stocks (fast)
#   Yahoo Finance → when zerodha_plan = "personal_free"
#                   One HTTP request per stock (slower, free)
#
# Both sources produce the same output fields so nothing else in
# the codebase needs to know which source was used:
#
#   current_price, current_value, pnl, pnl_percent
#   52w_high, 52w_low, avg_volume, price_trend, momentum
#   price_source, sector
#   pe_ratio, pb_ratio, market_cap_cr  (if plan includes PE ratios)
# ================================================================

import time
import datetime

from config              import Config
from core.logger         import Logger
from core.zerodha_client import ZerodhaClient


class MarketData:

    def __init__(self, config: type[Config], zerodha: ZerodhaClient, log: Logger):
        self.cfg     = config
        self.zerodha = zerodha
        self.log     = log

    # ================================================================
    # PUBLIC ENTRY POINT
    # ================================================================

    def enrich(self, portfolio: list[dict]) -> list[dict]:
        """
        Enriches every stock in the portfolio with prices and history.
        Routes to Kite or Yahoo based on zerodha_plan in config.py.
        Always call this method — not the private ones below.
        """
        source = self.cfg.zerodha()["price_source"]
        if source == "kite_live":
            return self._enrich_kite(portfolio)
        else:
            return self._enrich_yfinance(portfolio)

    # ================================================================
    # KITE LIVE ENRICHMENT  (connect_paid plan)
    # ================================================================

    def _enrich_kite(self, portfolio: list[dict]) -> list[dict]:
        """
        Two-step enrichment using Kite's paid APIs:

        Step 1 — kite.quote()
          Fetches live prices for ALL stocks in a single API call.
          This is the key speed advantage: 15 stocks = 1 call, not 15.

        Step 2 — kite.historical_data()
          Fetches 1-year OHLCV per stock for trend + momentum stats.
          Instrument list is loaded once upfront via ZerodhaClient,
          not per stock.
        """

        # ── Step 1: Live quotes ────────────────────────────────────
        self.log.info("Fetching live quotes from Kite (single API call)...")
        quotes = self.zerodha.get_quotes(portfolio)

        for stock in portfolio:
            key = f"{stock['exchange']}:{stock['symbol']}"
            q   = quotes.get(key, {})

            if q:
                ohlc = q.get("ohlc", {})
                stock["current_price"]  = round(q.get("last_price", stock["current_price"]), 2)
                stock["current_value"]  = round(stock["quantity"] * stock["current_price"], 2)
                stock["pnl"]            = round(stock["current_value"] - stock["invested_value"], 2)
                stock["pnl_percent"]    = round(
                    (stock["pnl"] / stock["invested_value"]) * 100, 2
                ) if stock["invested_value"] > 0 else 0
                stock["day_open"]       = ohlc.get("open", "N/A")
                stock["day_high"]       = ohlc.get("high", "N/A")
                stock["day_low"]        = ohlc.get("low",  "N/A")
                stock["volume"]         = q.get("volume", "N/A")
                stock["price_source"]   = "kite_live"
            else:
                self.log.warning(f"No live quote returned for {stock['symbol']}")
                stock["price_source"] = "kite_missing"

        self.log.success("Live prices applied")

        # ── Step 2: 1-year historical data ────────────────────────
        self.log.info("Fetching 1-year historical data from Kite...")
        one_year_ago = datetime.date.today() - datetime.timedelta(days=365)

        for stock in portfolio:
            symbol = stock["symbol"]
            try:
                hist = self.zerodha.get_historical(
                    symbol    = symbol,
                    exchange  = stock["exchange"],
                    from_date = one_year_ago,
                    to_date   = datetime.date.today(),
                )
                if hist:
                    self._apply_history_stats(stock, hist)

            except Exception as e:
                self.log.warning(f"Historical data unavailable for {symbol}: {e}")

            time.sleep(0.3)   # Respect Kite rate limits

        self.log.success("Historical data applied")
        return portfolio

    # ================================================================
    # YAHOO FINANCE ENRICHMENT  (personal_free plan fallback)
    # ================================================================

    def _enrich_yfinance(self, portfolio: list[dict]) -> list[dict]:
        """
        One HTTP request per stock via Yahoo Finance.
        Prices are ~15 minutes delayed but the service is free.
        Also pulls PE, PB, and market cap if plan supports them.
        """
        try:
            import yfinance as yf
        except ImportError:
            self.log.error("yfinance not installed — run: pip install yfinance")
            return portfolio

        self.log.info("Fetching data via Yahoo Finance (free, ~15 min delay)...")
        include_pe = self.cfg.claude()["include_pe_ratios"]

        for stock in portfolio:
            symbol    = stock["symbol"]
            # Yahoo uses .NS for NSE stocks and .BO for BSE stocks
            yf_symbol = symbol + (".NS" if stock["exchange"] == "NSE" else ".BO")

            try:
                ticker = yf.Ticker(yf_symbol)
                hist   = ticker.history(period="1y")
                info   = ticker.info

                if not hist.empty:
                    closes = hist["Close"].tolist()
                    stock["current_price"]  = round(closes[-1], 2)
                    stock["current_value"]  = round(stock["quantity"] * stock["current_price"], 2)
                    stock["pnl"]            = round(stock["current_value"] - stock["invested_value"], 2)
                    stock["pnl_percent"]    = round(
                        (stock["pnl"] / stock["invested_value"]) * 100, 2
                    ) if stock["invested_value"] > 0 else 0
                    stock["price_source"]   = "yfinance"

                    # Convert to the same record format as Kite historical data
                    hist_records = [
                        {"high": h, "low": l, "close": c, "volume": int(v)}
                        for h, l, c, v in zip(
                            hist["High"].tolist(),
                            hist["Low"].tolist(),
                            hist["Close"].tolist(),
                            hist["Volume"].tolist(),
                        )
                    ]
                    self._apply_history_stats(stock, hist_records)

                else:
                    self.log.warning(f"No price history returned for {symbol}")
                    stock["price_source"] = "yfinance_no_data"

                stock["sector"] = info.get("sector", "Unknown")

                if include_pe:
                    stock["pe_ratio"]      = info.get("trailingPE",  "N/A")
                    stock["pb_ratio"]      = info.get("priceToBook", "N/A")
                    stock["market_cap_cr"] = (
                        round(info["marketCap"] / 1e7, 0)
                        if info.get("marketCap") else "N/A"
                    )

            except Exception as e:
                self.log.warning(f"Could not fetch data for {symbol}: {e}")
                stock["price_source"] = "unavailable"

            time.sleep(0.5)   # Polite pause — Yahoo rate-limits aggressive scrapers

        self.log.success("Yahoo Finance data applied")
        return portfolio

    # ================================================================
    # SHARED HISTORY STATS
    # ================================================================

    @staticmethod
    def _apply_history_stats(stock: dict, hist: list[dict]):
        """
        Computes 52-week range, average volume, 1-year price trend,
        and 30-day momentum from a list of OHLCV records.

        Used by BOTH enrichers — same logic, same output fields,
        regardless of whether data came from Kite or Yahoo.

        Momentum definition:
          STRONG  → last 30-day avg > prior 30-day avg by more than 5%
          WEAK    → last 30-day avg < prior 30-day avg by more than 5%
          NEUTRAL → within ±5%
        """
        highs   = [d["high"]   for d in hist]
        lows    = [d["low"]    for d in hist]
        closes  = [d["close"]  for d in hist]
        volumes = [d["volume"] for d in hist]

        stock["52w_high"]    = round(max(highs), 2)
        stock["52w_low"]     = round(min(lows),  2)
        stock["avg_volume"]  = int(sum(volumes) / len(volumes)) if volumes else 0
        stock["price_trend"] = "UP" if closes[-1] > closes[0] else "DOWN"

        if len(closes) >= 60:
            recent   = sum(closes[-30:])   / 30
            previous = sum(closes[-60:-30]) / 30
            stock["momentum"] = (
                "STRONG"  if recent > previous * 1.05 else
                "WEAK"    if recent < previous * 0.95 else
                "NEUTRAL"
            )
        else:
            stock["momentum"] = "INSUFFICIENT_DATA"
