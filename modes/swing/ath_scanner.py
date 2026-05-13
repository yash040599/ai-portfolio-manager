# ================================================================
# modes/swing/ath_scanner.py
# ================================================================
# All-Time-High dip-buy scanner (ATH strategy).
#
# Strategy: buy Rs.10,000 worth of a stock when it falls X% from
# its all-time high. Sell when it rises Y% from buy price.
#
# This runs alongside the existing technical swing scanner and
# produces SwingCandidate records with setup_type = "ATH_DIP".
# Tracked in the same open swing book.
# ================================================================

from __future__ import annotations

import datetime
import json

from config import Config, now_ist
from core.logger import Logger
from core.zerodha_client import ZerodhaClient
from shared.candle_cache import CandleCache
from modes.trade.stock_scanner import (
    NIFTY50, NIFTY100_EXTRA, SECTOR_MAP,
)
from modes.swing.types import (
    SwingCandidate, SwingAction, ACTION_ENTRY, STATUS_PENDING,
)
from modes.swing.risk import generate_broker_instruction


# ── Default parameters ──────────────────────────────────────────

DEFAULT_DIP_PCT = 15.0        # Buy when stock falls 15% from ATH
DEFAULT_TARGET_PCT = 15.0     # Sell when stock rises 15% from buy
DEFAULT_BUY_AMOUNT = 10_000.0 # Rs.10,000 per position


def _build_universe(scan_universe: str) -> list[str]:
    symbols = list(NIFTY50)
    if scan_universe in ("NIFTY100", "NIFTY150", "NIFTY200"):
        symbols += NIFTY100_EXTRA
    return symbols


LOOKBACK_CALENDAR_DAYS = 3650  # ~10 years for ATH computation


class ATHScanner:
    """Scans for stocks that have dipped X% from their all-time high."""

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
        dip_pct: float = DEFAULT_DIP_PCT,
        target_pct: float = DEFAULT_TARGET_PCT,
        buy_amount: float = DEFAULT_BUY_AMOUNT,
        existing_symbols: set[str] | None = None,
        candle_to_date: datetime.date | None = None,
    ) -> tuple[list[SwingCandidate], list[SwingAction]]:
        """Find stocks currently X% below their all-time high.

        Returns (candidates, actions) — same shape as SwingScanner.scan()
        so they integrate into the same pipeline.
        """
        if existing_symbols is None:
            existing_symbols = set()

        universe = _build_universe(
            getattr(self.cfg, "SCAN_UNIVERSE", "NIFTY100"))

        self.log.info(f"ATH scan: {len(universe)} symbols, "
                      f"dip={dip_pct}%, target={target_pct}%")

        candidates: list[SwingCandidate] = []
        accepted: list[SwingCandidate] = []

        for symbol in universe:
            try:
                candles = self._fetch_daily_candles(
                    symbol, "NSE", to_date=candle_to_date)
                if len(candles) < 50:
                    continue

                closes = [c["close"] for c in candles]
                highs = [c["high"] for c in candles]
                current = closes[-1]

                # All-time high from the full history
                ath = max(highs)
                if ath <= 0:
                    continue

                # How far below ATH?
                dip_from_ath = ((ath - current) / ath) * 100

                sector = SECTOR_MAP.get(symbol, "OTHER")

                # Not enough dip — skip
                if dip_from_ath < dip_pct:
                    candidates.append(SwingCandidate(
                        symbol=symbol,
                        setup_type="ATH_DIP",
                        score=0,
                        status="REJECTED",
                        rejected_reason=(
                            f"Only {dip_from_ath:.1f}% below ATH "
                            f"(need {dip_pct:.0f}%+)"),
                        close_price=current,
                        sector=sector,
                    ))
                    continue

                # Already in open book
                if symbol in existing_symbols:
                    candidates.append(SwingCandidate(
                        symbol=symbol,
                        setup_type="ATH_DIP",
                        score=round(dip_from_ath, 1),
                        status="REJECTED",
                        rejected_reason="Already in open swing book",
                        close_price=current,
                        sector=sector,
                    ))
                    continue

                # Compute entry plan
                entry_price = current
                stop_price = round(current * 0.90, 2)  # 10% hard stop below buy
                target_price = round(current * (1 + target_pct / 100), 2)
                qty = max(1, int(buy_amount / current))
                risk_per_share = entry_price - stop_price
                reward_per_share = target_price - entry_price
                rr = (reward_per_share / risk_per_share
                      if risk_per_share > 0 else 0)

                reasons = [
                    f"Stock is {dip_from_ath:.1f}% below its all-time high of Rs.{ath:,.2f}",
                    f"ATH dip-buy strategy: buy when {dip_pct:.0f}%+ below ATH",
                    f"Target: sell when price rises {target_pct:.0f}% from buy price",
                    f"Buy Rs.{buy_amount:,.0f} worth = {qty} shares at Rs.{current:,.2f}",
                ]

                c = SwingCandidate(
                    symbol=symbol,
                    exchange="NSE",
                    setup_type="ATH_DIP",
                    score=round(dip_from_ath, 1),  # higher dip = higher score
                    close_price=current,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    target_price=target_price,
                    risk_rupees=round(risk_per_share * qty, 2),
                    reward_rupees=round(reward_per_share * qty, 2),
                    rr_ratio=round(rr, 2),
                    suggested_qty=qty,
                    sector=sector,
                    high_52w=max(highs[-252:]) if len(highs) >= 252 else ath,
                    low_52w=min([c["low"] for c in candles[-252:]]) if len(candles) >= 252 else min([c["low"] for c in candles]),
                    reasons=reasons,
                    status="ACCEPTED",
                    broker_instruction_json=json.dumps(
                        generate_broker_instruction(
                            symbol=symbol, exchange="NSE",
                            qty=qty, entry_price=entry_price,
                            stop_price=stop_price, target_price=target_price,
                        ), default=str),
                )

                candidates.append(c)
                accepted.append(c)

            except Exception as exc:
                self.log.warning(f"ATH scan {symbol}: {exc}")
                continue

        # Priority: sort by biggest dip from ATH
        accepted.sort(key=lambda c: c.score, reverse=True)
        for rank, c in enumerate(accepted, 1):
            c.priority_rank = rank
            c.priority_score = c.score

        # Build ENTRY actions
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
                notes=f"ATH dip: {c.score:.1f}% below ATH",
            ))

        self.log.info(f"ATH scan complete: {len(candidates)} seen, "
                      f"{len(accepted)} at {dip_pct}%+ below ATH")

        return candidates, actions

    def _fetch_daily_candles(self, symbol: str, exchange: str,
                             to_date: datetime.date | None = None,
                             ) -> list[dict]:
        """Fetch daily candles — long lookback for ATH computation."""
        end_date = to_date or now_ist().date()
        from_date = end_date - datetime.timedelta(days=LOOKBACK_CALENDAR_DAYS)

        cached = self._cache.get_cached_candles(
            symbol, exchange, "day", from_date, end_date)
        if len(cached) >= 200:
            return cached

        try:
            candles = self.zerodha.get_historical(
                symbol, exchange,
                from_date=from_date, to_date=end_date,
                interval="day",
            )
            if candles:
                self._cache.store_candles(symbol, exchange, "day", candles)
            return candles or cached
        except Exception as exc:
            self.log.warning(f"ATH fetch {symbol}: {exc}")
            return cached
