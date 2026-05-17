# ================================================================
# modes/swing/ath_scanner.py
# ================================================================
# Dip-buy scanner — entry rule: close X% below the rolling
# 52-week high; exit rule: close Y% above buy price.
#
# Originally shipped 2026-05-14 against ALL-TIME HIGH (`max(highs)`
# over a 10-year lookback) and named ATHScanner. Switched the same
# day to the rolling 52-week high (`max(highs[-N:])` where N defaults
# to `Config.SWING_DIP_LOOKBACK_DAYS = 252`). The class name and
# file name are retained for git-history continuity; `DipBuyScanner`
# is the canonical alias.
#
# This runs alongside the existing technical swing scanner and
# produces SwingCandidate records with setup_type = "52W_DIP"
# (legacy ATH_DIP rows already in `data/swing.db` are still
# readable but are no longer produced).
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
    SETUP_52W_DIP,
)
from modes.swing.risk import generate_broker_instruction, earnings_blackout_symbols
from modes.swing.signals import compute_swing_indicators


# ── Default parameters ──────────────────────────────────────────
#
# These module-level defaults are kept ONLY as the safety net when
# Config does not expose the corresponding knobs (e.g. an old install
# upgraded mid-session). Live runs should always read from Config so
# the user can tune from one place — the values below mirror the
# Config defaults set after the 2026-05-16 finite-capital V2 backtest:
#
#   X (dip)    = 10%   best CAGR/alpha cell in the finite-capital run
#   Y (target) = 20%   paired target in that top-ranked cell
#   ticket     = Rs.20,000 per dip-buy (matches the V2 lot size)
#   lookback   = 252 trading bars ≈ 52 weeks
#
# Source: ../market-research/results_v2/grid_metrics_v2.csv. The V2
# run is still ATH-referenced; live 52w use is a provisional retune
# until the S11 52w finite-cap replay lands.

DEFAULT_DIP_PCT = 10.0          # Buy when close is X% below the 52w high
DEFAULT_TARGET_PCT = 20.0       # Sell when close is Y% above buy
DEFAULT_BUY_AMOUNT = 20_000.0   # Rs. per dip-buy ticket
DEFAULT_LOOKBACK_DAYS = 252     # ~52 weeks of trading bars


def _build_universe(scan_universe: str) -> list[str]:
    symbols = list(NIFTY50)
    if scan_universe in ("NIFTY100", "NIFTY150", "NIFTY200"):
        symbols += NIFTY100_EXTRA
    return symbols


# Daily-candle fetch lookback (calendar days). Kept large so the cache
# stores enough history to satisfy any reasonable
# Config.SWING_DIP_LOOKBACK_DAYS without a re-fetch — the scanner only
# *uses* `Config.SWING_DIP_LOOKBACK_DAYS` worth of bars when computing
# the reference high.
LOOKBACK_CALENDAR_DAYS = 3650


class DipBuyScanner:
    """Scans for stocks currently X% below their rolling 52-week high.

    Despite the previous class name (`ATHScanner`, kept as an alias
    below for any external import), this scanner now uses
    `Config.SWING_DIP_LOOKBACK_DAYS` (default 252 trading bars ≈ 52
    weeks) as the lookback window for the reference high. This makes
    the trigger responsive to the current market regime rather than
    silently sitting on a 5-year-old all-time peak.
    """

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
        dip_pct: float | None = None,
        target_pct: float | None = None,
        buy_amount: float | None = None,
        lookback_days: int | None = None,
        existing_symbols: set[str] | None = None,
        candle_to_date: datetime.date | None = None,
    ) -> tuple[list[SwingCandidate], list[SwingAction]]:
        """Find stocks currently X% below their rolling 52-week high.

        Returns (candidates, actions) — same shape as SwingScanner.scan()
        so they integrate into the same pipeline.

        When dip_pct / target_pct / buy_amount / lookback_days are not
        provided they fall through to Config
        (SWING_DIP_PCT / SWING_DIP_TARGET_PCT / SWING_DIP_BUY_AMOUNT /
        SWING_DIP_LOOKBACK_DAYS), so a single edit to config.py
        retunes both the scanner and the dashboard preview.
        """
        if existing_symbols is None:
            existing_symbols = set()

        # Resolve effective parameters (caller > Config > module fallback).
        if dip_pct is None:
            dip_pct = float(getattr(self.cfg, "SWING_DIP_PCT", DEFAULT_DIP_PCT))
        if target_pct is None:
            target_pct = float(getattr(self.cfg, "SWING_DIP_TARGET_PCT", DEFAULT_TARGET_PCT))
        if buy_amount is None:
            buy_amount = float(getattr(self.cfg, "SWING_DIP_BUY_AMOUNT", DEFAULT_BUY_AMOUNT))
        if lookback_days is None:
            lookback_days = int(getattr(self.cfg, "SWING_DIP_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS))
        # Hard floors so a stale/zero Config can't silently degrade the rule.
        lookback_days = max(20, lookback_days)

        universe = _build_universe(
            getattr(self.cfg, "SCAN_UNIVERSE", "NIFTY100"))

        self.log.info(
            f"Dip scan: {len(universe)} symbols, "
            f"dip={dip_pct}%, target={target_pct}%, "
            f"reference={lookback_days}d high"
        )

        # Earnings-blackout symbols (S25). Same lookup the technical
        # scanner uses; reused so a name announcing tomorrow can't be
        # entered as a "dip-buy" today either. The 10% hard stop on
        # dip-buys is a flat percentage, not ATR-aware, so a result-
        # day gap on a freshly-bought dip is the textbook way to lose
        # twice the planned risk overnight.
        scan_date = candle_to_date or now_ist().date()
        blackout = earnings_blackout_symbols(today=scan_date, cfg=self.cfg)
        if blackout:
            self.log.info(
                f"Dip-scan earnings blackout: {len(blackout)} symbol(s) "
                f"in next 3 days"
            )

        # Fetch NIFTY 50 candles ONCE for the whole dip scan so every
        # candidate's `relative_strength` can be computed against the
        # benchmark. Pre-S42 (2026-05-14) the dip scanner called
        # `compute_swing_indicators(candles)` with no nifty argument,
        # which silently set `rel_strength=0.0` on every dip-buy
        # candidate — and the detail page's "Beating the market?"
        # row always showed +0.0% vs NIFTY for any 52W_DIP. The
        # technical scanner already does this once-per-scan; the dip
        # scanner now matches.
        nifty_candles = self._fetch_daily_candles(
            "NIFTY 50", "NSE", to_date=candle_to_date)

        candidates: list[SwingCandidate] = []
        accepted: list[SwingCandidate] = []

        for symbol in universe:
            try:
                candles = self._fetch_daily_candles(
                    symbol, "NSE", to_date=candle_to_date)
                if len(candles) < 50:
                    continue

                # Earnings blackout — pre-computation skip.
                if symbol in blackout:
                    candidates.append(SwingCandidate(
                        symbol=symbol,
                        setup_type=SETUP_52W_DIP,
                        score=0,
                        status="REJECTED",
                        rejected_reason=f"Earnings on {blackout[symbol]} (T+0..2)",
                        close_price=candles[-1].get("close", 0) or 0,
                        sector=SECTOR_MAP.get(symbol, "OTHER"),
                    ))
                    continue

                closes = [c["close"] for c in candles]
                highs = [c["high"] for c in candles]
                current = closes[-1]

                # Reference high = rolling max-CLOSE over the last
                # `lookback_days` trading bars. Was previously
                # `max(highs)` (full-history all-time high) before the
                # 2026-05-14 switch to a 52-week-high reference.
                # Note we use `closes` (not intraday `highs`) because:
                #   * the strategy fires on close-based dip-buys, so
                #     comparing close-vs-close removes the intraday-spike
                #     bias that an `high` reference would introduce, and
                #   * the standalone backtest in `market-research/`
                #     used `max(closes)` for the same reason.
                ref_window = closes[-lookback_days:] if len(closes) >= lookback_days else closes
                ref_high = max(ref_window) if ref_window else 0.0
                if ref_high <= 0:
                    continue

                # How far below the rolling 52w high?
                dip_from_ref = ((ref_high - current) / ref_high) * 100

                sector = SECTOR_MAP.get(symbol, "OTHER")

                # Not enough dip — skip
                if dip_from_ref < dip_pct:
                    candidates.append(SwingCandidate(
                        symbol=symbol,
                        setup_type=SETUP_52W_DIP,
                        score=0,
                        status="REJECTED",
                        rejected_reason=(
                            f"Only {dip_from_ref:.1f}% below 52w high "
                            f"(need {dip_pct:.0f}%+)"),
                        close_price=current,
                        sector=sector,
                        ath_price=round(ref_high, 2),  # legacy field name; holds 52w high
                        dip_from_ath_pct=round(dip_from_ref, 2),
                    ))
                    continue

                # Already in open book
                if symbol in existing_symbols:
                    candidates.append(SwingCandidate(
                        symbol=symbol,
                        setup_type=SETUP_52W_DIP,
                        score=round(dip_from_ref, 1),
                        status="REJECTED",
                        rejected_reason="Already in open swing book",
                        close_price=current,
                        sector=sector,
                        ath_price=round(ref_high, 2),
                        dip_from_ath_pct=round(dip_from_ref, 2),
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
                    f"Stock is {dip_from_ref:.1f}% below its 52-week high of Rs.{ref_high:,.2f}",
                    f"Dip-buy strategy: buy when {dip_pct:.0f}%+ below 52w high "
                    f"(finite-cap V2 default: 10% dip)",
                    f"Target: sell when price rises {target_pct:.0f}% from buy "
                    f"(finite-cap V2 default: 20% gain)",
                    f"Buy Rs.{buy_amount:,.0f} worth = {qty} shares at Rs.{current:,.2f}",
                ]

                # Compute indicators for the detail page (pass NIFTY
                # candles so `rel_strength` is populated — see the
                # `nifty_candles` block above for the pre-S42 bug).
                ind = compute_swing_indicators(candles, nifty_candles)
                _sma_50 = ind.get("sma_50", 0) if ind.get("valid") else 0
                _sma_200 = ind.get("sma_200", 0) if ind.get("valid") else 0
                _ema_20 = ind.get("ema_20", 0) if ind.get("valid") else 0
                _rsi = ind.get("rsi", 0) if ind.get("valid") else 0
                _atr = ind.get("atr_14", 0) if ind.get("valid") else 0
                _vol_ratio = ind.get("vol_ratio", 0) if ind.get("valid") else 0
                _rs = ind.get("rel_strength", 0) if ind.get("valid") else 0
                _weekly_up = ind.get("weekly_trend_up", False) if ind.get("valid") else False

                c = SwingCandidate(
                    symbol=symbol,
                    exchange="NSE",
                    setup_type=SETUP_52W_DIP,
                    score=round(dip_from_ref, 1),  # higher dip = higher score
                    close_price=current,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    target_price=target_price,
                    risk_rupees=round(risk_per_share * qty, 2),
                    reward_rupees=round(reward_per_share * qty, 2),
                    rr_ratio=round(rr, 2),
                    suggested_qty=qty,
                    sector=sector,
                    sma_50=_sma_50,
                    sma_200=_sma_200,
                    ema_20=_ema_20,
                    rsi_daily=_rsi,
                    atr_14=_atr,
                    relative_strength=_rs,
                    volume_ratio=_vol_ratio,
                    high_20d=ind.get("high_20d", 0) if ind.get("valid") else 0,
                    high_50d=ind.get("high_50d", 0) if ind.get("valid") else 0,
                    high_52w=ref_high,
                    low_52w=min([c["low"] for c in candles[-252:]]) if len(candles) >= 252 else min([c["low"] for c in candles]),
                    weekly_trend_up=_weekly_up,
                    ath_price=round(ref_high, 2),
                    dip_from_ath_pct=round(dip_from_ref, 2),
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
                self.log.warning(f"Dip scan {symbol}: {exc}")
                continue

        # Priority: sort by biggest dip from 52w high
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
                notes=f"52w dip: {c.score:.1f}% below 52w high",
            ))

        self.log.info(
            f"Dip scan complete: {len(candidates)} seen, "
            f"{len(accepted)} at {dip_pct}%+ below 52w high"
        )

        return candidates, actions

    def _fetch_daily_candles(self, symbol: str, exchange: str,
                             to_date: datetime.date | None = None,
                             ) -> list[dict]:
        """Fetch daily candles — long lookback so the cache stores
        enough history for any reasonable
        `Config.SWING_DIP_LOOKBACK_DAYS` without re-fetching.
        """
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
            self.log.warning(f"Dip fetch {symbol}: {exc}")
            return cached


# ── Legacy alias ────────────────────────────────────────────────
# `ATHScanner` was the pre-2026-05-14 class name. Kept as an alias
# so external imports (other scripts, copilot skills, the user's own
# scratch code) continue to work after the rename to `DipBuyScanner`.
# Both names point at the same class — there is no "ATH mode" left.
ATHScanner = DipBuyScanner
