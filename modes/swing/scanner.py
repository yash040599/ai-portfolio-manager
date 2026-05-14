# ================================================================
# modes/swing/scanner.py
# ================================================================
# Swing candidate scanner. Fetches daily candles for the configured
# universe, runs setup detection + risk sizing, and returns a
# priority-sorted list of SwingCandidate records.
#
# Reuses shared.candle_cache + shared.technical_indicators.
# SWING_ROADMAP S6 + S7.
# ================================================================

from __future__ import annotations

import datetime
import json

from config import Config, now_ist
from core.logger import Logger
from core.zerodha_client import ZerodhaClient
from shared.candle_cache import CandleCache
from modes.trade.stock_scanner import (
    NIFTY50, NIFTY100_EXTRA, NIFTY150_EXTRA, NIFTY200_EXTRA, SECTOR_MAP,
)
from modes.swing.signals import compute_swing_indicators, classify_setup
from modes.swing.risk import (
    compute_entry_risk, check_portfolio_limits, generate_broker_instruction,
    earnings_blackout_symbols,
)
from modes.swing.types import SwingCandidate, SwingAction, ACTION_ENTRY, STATUS_PENDING


# ── Universe builder ────────────────────────────────────────────

def _build_universe(scan_universe: str) -> list[str]:
    """Build the symbol list based on Config.SCAN_UNIVERSE."""
    symbols = list(NIFTY50)
    if scan_universe in ("NIFTY100", "NIFTY150", "NIFTY200"):
        symbols += NIFTY100_EXTRA
    if scan_universe in ("NIFTY150", "NIFTY200"):
        symbols += NIFTY150_EXTRA
    if scan_universe == "NIFTY200":
        symbols += NIFTY200_EXTRA
    return symbols


# Daily candle lookback (calendar days).
#
# Set to 3650 (~10 years) so the running max-close used for the
# `ath_price` / `dip_from_ath_pct` fields lines up with the ATH dip-buy
# scanner's lookback (`modes/swing/ath_scanner.py::LOOKBACK_CALENDAR_DAYS`).
# The earlier 750-day window was fine for technical indicators (RSI /
# SMA / ATR / RS) but produced a *different* "ATH" than the ATH
# scanner did for the same symbol — so the unified `% Below ATH`
# column on the dashboard could show two values for the same name
# depending on which scanner won the persistence ordering.
# Verified by the 2026-05-14 swing-diff review.
#
# Cache impact is negligible: ~3000 daily rows per symbol × NIFTY 200
# = ~600k rows in SQLite, one-time fill.
LOOKBACK_CALENDAR_DAYS = 3650


class SwingScanner:
    """Scans the universe for swing candidates using completed daily candles."""

    def __init__(
        self,
        config: type[Config],
        zerodha: ZerodhaClient,
        log: Logger,
    ):
        self.cfg = config
        self.zerodha = zerodha
        self.log = log
        self._cache = CandleCache()

    def scan(
        self,
        *,
        swing_capital: float = 100_000.0,
        existing_positions: list[dict] | None = None,
        nifty_candles: list[dict] | None = None,
        candle_to_date: datetime.date | None = None,
    ) -> tuple[list[SwingCandidate], list[SwingAction]]:
        """Run the full swing scan.

        Returns (candidates, actions) where candidates includes both
        accepted and rejected records, and actions contains ENTRY
        actions for accepted candidates, priority-sorted.

        `candle_to_date`: when set, fetch candles only up to this date
        (exclusive of today's incomplete candle). When None, uses today.
        """
        if existing_positions is None:
            existing_positions = []

        universe = _build_universe(
            getattr(self.cfg, "SCAN_UNIVERSE", "NIFTY100"))

        self.log.info(f"Swing scan: {len(universe)} symbols, "
                      f"capital Rs.{swing_capital:,.0f}")

        # Fetch NIFTY candles for relative strength
        if nifty_candles is None:
            nifty_candles = self._fetch_daily_candles(
                "NIFTY 50", "NSE", to_date=candle_to_date)

        # Track symbols already in open positions
        open_symbols = {p.get("symbol", "") for p in existing_positions}

        # Earnings-blackout symbols (S25). One read per scan — the
        # function returns `{SYMBOL: 'YYYY-MM-DD'}` for everything
        # announcing in the next 3 calendar days. Empty when the
        # kill-switch is off OR the user hasn't populated the calendar.
        scan_date = candle_to_date or now_ist().date()
        blackout = earnings_blackout_symbols(today=scan_date, cfg=self.cfg)
        if blackout:
            self.log.info(
                f"Earnings blackout active for {len(blackout)} symbol(s) "
                f"in next 3 days: {', '.join(sorted(blackout)[:6])}"
                + (" ..." if len(blackout) > 6 else "")
            )

        candidates: list[SwingCandidate] = []
        accepted: list[SwingCandidate] = []

        for symbol in universe:
            try:
                candles = self._fetch_daily_candles(
                    symbol, "NSE", to_date=candle_to_date)
                if len(candles) < 50:
                    continue

                # Earnings blackout — pre-indicator skip so we don't
                # waste cycles classifying a setup we're going to drop.
                if symbol in blackout:
                    candidates.append(SwingCandidate(
                        symbol=symbol, setup_type="NONE", score=0,
                        status="REJECTED",
                        rejected_reason=f"Earnings on {blackout[symbol]} (T+0..2)",
                        close_price=candles[-1].get("close", 0) or 0,
                        sector=SECTOR_MAP.get(symbol, "OTHER"),
                    ))
                    continue

                ind = compute_swing_indicators(candles, nifty_candles)
                if not ind.get("valid"):
                    continue

                # All-time high (running max of close prices over the
                # full available history) — used as a context column on
                # the unified entry-recommendations table so a technical
                # candidate also surfaces "% below ATH" alongside its
                # setup score, and lets the ATH dip-buy strategy and
                # the technical scan share the same dashboard row.
                _closes_all = [c["close"] for c in candles if c.get("close")]
                _ath_price = max(_closes_all) if _closes_all else 0.0
                _dip_pct = (
                    ((_ath_price - ind["current"]) / _ath_price) * 100.0
                    if _ath_price > 0 else 0.0
                )

                setup_type, score, reasons = classify_setup(ind)
                if setup_type == "NONE":
                    candidates.append(SwingCandidate(
                        symbol=symbol, setup_type="NONE", score=0,
                        status="REJECTED", rejected_reason="No qualifying setup",
                        close_price=ind["current"],
                        sector=SECTOR_MAP.get(symbol, "OTHER"),
                        ath_price=round(_ath_price, 2),
                        dip_from_ath_pct=round(_dip_pct, 2),
                    ))
                    continue

                # Skip if already in open book
                if symbol in open_symbols:
                    candidates.append(SwingCandidate(
                        symbol=symbol, setup_type=setup_type, score=score,
                        status="REJECTED",
                        rejected_reason="Already in open swing book",
                        close_price=ind["current"],
                        sector=SECTOR_MAP.get(symbol, "OTHER"),
                        ath_price=round(_ath_price, 2),
                        dip_from_ath_pct=round(_dip_pct, 2),
                    ))
                    continue

                # Risk sizing
                risk = compute_entry_risk(
                    current_price=ind["current"],
                    atr_14=ind["atr_14"],
                    sma_50=ind["sma_50"],
                    sma_200=ind["sma_200"],
                    low_52w=ind["low_52w"],
                    high_52w=ind["high_52w"],
                    setup_type=setup_type,
                    swing_capital=swing_capital,
                )

                sector = SECTOR_MAP.get(symbol, "OTHER")

                c = SwingCandidate(
                    symbol=symbol,
                    exchange="NSE",
                    setup_type=setup_type,
                    score=round(score, 2),
                    close_price=ind["current"],
                    entry_price=risk.entry_price,
                    stop_price=risk.stop_price,
                    target_price=risk.target_price,
                    risk_rupees=risk.risk_rupees,
                    reward_rupees=risk.reward_rupees,
                    rr_ratio=risk.rr_ratio,
                    suggested_qty=risk.suggested_qty,
                    sector=sector,
                    sma_50=ind["sma_50"],
                    sma_200=ind["sma_200"],
                    ema_20=ind["ema_20"],
                    rsi_daily=ind["rsi"],
                    atr_14=ind["atr_14"],
                    relative_strength=ind["rel_strength"],
                    volume_ratio=ind["vol_ratio"],
                    high_20d=ind["high_20d"],
                    high_50d=ind["high_50d"],
                    low_52w=ind["low_52w"],
                    high_52w=ind["high_52w"],
                    weekly_trend_up=ind["weekly_trend_up"],
                    ath_price=round(_ath_price, 2),
                    dip_from_ath_pct=round(_dip_pct, 2),
                    reasons=reasons,
                )

                if risk.rejected:
                    c.status = "REJECTED"
                    c.rejected_reason = risk.rejected_reason
                    candidates.append(c)
                    continue

                # Portfolio-level checks
                ok, reason = check_portfolio_limits(
                    new_risk_rupees=risk.risk_rupees,
                    new_position_value=risk.position_value,
                    new_sector=sector,
                    existing_positions=existing_positions,
                    swing_capital=swing_capital,
                )
                if not ok:
                    c.status = "REJECTED"
                    c.rejected_reason = reason
                    candidates.append(c)
                    continue

                c.status = "ACCEPTED"
                c.broker_instruction_json = json.dumps(
                    generate_broker_instruction(
                        symbol=symbol, exchange="NSE",
                        qty=risk.suggested_qty,
                        entry_price=risk.entry_price,
                        stop_price=risk.stop_price,
                        target_price=risk.target_price,
                    ), default=str)

                candidates.append(c)
                accepted.append(c)

            except Exception as exc:
                self.log.warning(f"Swing scan {symbol}: {exc}")
                continue

        # Priority rank: sort accepted by score descending
        accepted.sort(key=lambda c: c.score, reverse=True)
        for rank, c in enumerate(accepted, 1):
            c.priority_rank = rank
            c.priority_score = c.score

        # Build ENTRY actions for accepted candidates
        ts = now_ist().isoformat()
        actions: list[SwingAction] = []
        for c in accepted:
            actions.append(SwingAction(
                symbol=c.symbol,
                exchange=c.exchange,
                action_type=ACTION_ENTRY,
                status=STATUS_PENDING,
                suggested_qty=c.suggested_qty,
                suggested_price=c.entry_price,
                suggested_stop=c.stop_price,
                suggested_target=c.target_price,
                priority_rank=c.priority_rank,
                live_price=c.close_price,
                broker_instruction_json=c.broker_instruction_json,
                created_at=ts,
            ))

        self.log.info(f"Swing scan complete: {len(candidates)} seen, "
                      f"{len(accepted)} accepted")

        return candidates, actions

    # ── Candle fetching ─────────────────────────────────────────

    def _fetch_daily_candles(self, symbol: str,
                             exchange: str,
                             to_date: datetime.date | None = None,
                             ) -> list[dict]:
        """Fetch daily candles using cache + Zerodha API fallback.

        `to_date`: upper bound (inclusive). Defaults to today. Set to
        yesterday when the market is still open so only completed
        daily candles are used.
        """
        end_date = to_date or now_ist().date()
        from_date = end_date - datetime.timedelta(days=LOOKBACK_CALENDAR_DAYS)

        # Try cache first
        cached = self._cache.get_cached_candles(
            symbol, exchange, "day", from_date, end_date)
        if len(cached) >= 200:
            return cached

        # Fetch from Zerodha and cache
        try:
            candles = self.zerodha.get_historical(
                symbol, exchange,
                from_date=from_date,
                to_date=end_date,
                interval="day",
            )
            if candles:
                self._cache.store_candles(symbol, exchange, "day", candles)
            return candles or cached
        except Exception as exc:
            self.log.warning(f"Zerodha daily fetch {symbol}: {exc}")
            return cached
