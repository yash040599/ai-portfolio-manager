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
from modes.swing.conviction import grade as grade_candidate
from modes.swing.risk import (
    compute_entry_risk, check_portfolio_limits, generate_broker_instruction,
    earnings_blackout_symbols,
)
from modes.swing.types import (
    SwingCandidate, SwingAction, ACTION_ENTRY, STATUS_PENDING,
    SETUP_52W_DIP,
)
from shared.quant_metrics import profile as quant_profile
from shared.technical_indicators import adx as compute_adx


# ── Conviction + risk grading ───────────────────────────────────

def _attach_grades(candidate: SwingCandidate, *, ind: dict,
                   candles: list[dict], nifty_candles: list[dict] | None,
                   setup_score: float, setup_type: str,
                   usd: bool = False) -> None:
    """Compute the quant profile + conviction/risk grade and stamp them
    onto `candidate`. Failure-silent: a grading error must never drop an
    otherwise valid candidate, it just leaves the grade blank."""
    try:
        quant = quant_profile(
            candles, nifty_candles or None,
            risk_free_pct=4.5 if usd else 6.5,
        )
    except Exception:
        quant = {}

    # ADX is the one indicator the swing detectors never computed, and
    # it is the standard way to tell a real trend from chop.
    enriched = dict(ind)
    try:
        adx_val = (compute_adx(candles, period=14) or {}).get("adx")
        if adx_val is not None:
            enriched["adx"] = float(adx_val)
            candidate.reasons = list(candidate.reasons or [])
    except Exception:
        pass

    try:
        result = grade_candidate(
            enriched,
            setup_score=setup_score,
            setup_type=setup_type,
            quant=quant,
            entry_price=candidate.entry_price or candidate.close_price,
            stop_price=candidate.stop_price,
            usd=usd,
        )
    except Exception:
        return

    candidate.conviction = round(result.conviction, 1)
    candidate.conviction_grade = result.conviction_grade
    candidate.risk_score = round(result.risk, 1)
    candidate.risk_grade = result.risk_grade
    try:
        candidate.conviction_json = json.dumps(result.to_dict())
        candidate.quant_json = json.dumps(
            {k: (round(v, 4) if isinstance(v, float) else v)
             for k, v in quant.items()}
        )
    except (TypeError, ValueError):
        pass


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
                  f"ticket Rs.{swing_capital:,.0f}/stock")

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

                # 52-week high context (S44 hardening, 2026-05-14):
                # Use the rolling N-day high (default 252 trading bars
                # ≈ 52 weeks) as the reference, NOT the full-history
                # close max. Pre-S44 the column on the dashboard was
                # labelled "% Below 52w High" but actually computed
                # the dip from the all-history max — so a NIFTY 50
                # name 14% below its 2024 ATH (still 8% above its
                # rolling 52w high) read as "14% below 52w high".
                # The dip-buy scanner already uses the same lookback
                # for its qualification threshold; the technical
                # scanner now matches.
                _lookback = max(20, int(getattr(
                    self.cfg, "SWING_DIP_LOOKBACK_DAYS", 252)))
                _closes_all = [c["close"] for c in candles if c.get("close")]
                _ref_window = (_closes_all[-_lookback:]
                               if len(_closes_all) >= _lookback
                               else _closes_all)
                _ath_price = max(_ref_window) if _ref_window else 0.0
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
                _attach_grades(
                    c, ind=ind, candles=candles,
                    nifty_candles=nifty_candles, setup_score=score,
                    setup_type=setup_type,
                )
                is_add_more = symbol in open_symbols
                if is_add_more:
                    c.reasons = list(c.reasons or []) + [
                        "Already held: eligible add-more candidate; "
                        "confirming a buy will average into the open swing book."
                    ]

                if risk.rejected:
                    c.status = "REJECTED"
                    c.rejected_reason = risk.rejected_reason
                    candidates.append(c)
                    continue

                # Portfolio-level checks
                portfolio_capital = swing_capital * max(1, len(universe))
                ok, reason = check_portfolio_limits(
                    new_risk_rupees=risk.risk_rupees,
                    new_position_value=risk.position_value,
                    new_sector=sector,
                    existing_positions=existing_positions,
                    swing_capital=portfolio_capital,
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
            is_add_more = c.symbol in open_symbols
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
                notes=(
                    "Add-more candidate: already in open swing book; "
                    "confirmed buy will average the open position."
                    if is_add_more else ""
                ),
            ))

        self.log.info(f"Swing scan complete: {len(candidates)} seen, "
                      f"{len(accepted)} accepted")

        return candidates, actions

    # ── Single-symbol scan (S38 search-box flow) ───────────────

    def scan_one(
        self,
        symbol: str,
        *,
        swing_capital: float = 100_000.0,
        existing_positions: list[dict] | None = None,
        candle_to_date: datetime.date | None = None,
    ) -> tuple[SwingCandidate, SwingAction | None]:
        """Run the full per-stock pipeline against ONE ticker.

        Returns `(candidate, action)`:
          * candidate is always present (status carries the verdict —
            ACCEPTED / REJECTED / NONE).
          * action is the ENTRY action for ACCEPTED, otherwise None.

        This is the workhorse behind the dashboard's single-stock
        search box (S38). Re-uses every helper the universe scan
        uses so the result is identical to what the universe scan
        would produce for the same name on the same day. Even
        rejected results are still returned so the user can see
        why their pick didn't qualify.

        Symbol does NOT need to be in `Config.SCAN_UNIVERSE` — the
        pipeline only needs daily candles from Zerodha + a sector
        lookup (defaulted to "OTHER" when the symbol isn't in
        `SECTOR_MAP`).
        """
        if existing_positions is None:
            existing_positions = []

        symbol = (symbol or "").strip().upper()
        sector = SECTOR_MAP.get(symbol, "OTHER")
        open_symbols = {p.get("symbol", "") for p in existing_positions}

        # Pre-flight: earnings blackout (one symbol only — cheap).
        from datetime import date as _date
        scan_date = candle_to_date or now_ist().date()
        from modes.swing.risk import earnings_blackout_symbols
        blackout = earnings_blackout_symbols(today=scan_date, cfg=self.cfg)
        if symbol in blackout:
            cand = SwingCandidate(
                symbol=symbol, setup_type="NONE", score=0,
                status="REJECTED",
                rejected_reason=f"Earnings on {blackout[symbol]} (T+0..2)",
                close_price=0, sector=sector,
            )
            return cand, None

        candles = self._fetch_daily_candles(symbol, "NSE", to_date=candle_to_date)
        if len(candles) < 50:
            cand = SwingCandidate(
                symbol=symbol, setup_type="NONE", score=0,
                status="REJECTED",
                rejected_reason=(
                    f"Not enough daily history ({len(candles)} bars; need >=50). "
                    "Symbol may be unlisted or recently IPO'd."
                ),
                close_price=0, sector=sector,
            )
            return cand, None

        nifty_candles = self._fetch_daily_candles("NIFTY 50", "NSE",
                                                  to_date=candle_to_date)
        ind = compute_swing_indicators(candles, nifty_candles)
        if not ind.get("valid"):
            cand = SwingCandidate(
                symbol=symbol, setup_type="NONE", score=0,
                status="REJECTED",
                rejected_reason=ind.get("reason", "Indicators not computable"),
                close_price=candles[-1].get("close", 0) or 0,
                sector=sector,
            )
            return cand, None

        # Reference high (rolling 252-day max-close — matches the
        # universe scan path; see the matching block in `scan()` for
        # the S44 rationale).
        _lookback = max(20, int(getattr(
            self.cfg, "SWING_DIP_LOOKBACK_DAYS", 252)))
        _closes_all = [c["close"] for c in candles if c.get("close")]
        _ref_window = (_closes_all[-_lookback:]
                       if len(_closes_all) >= _lookback
                       else _closes_all)
        _ath_price = max(_ref_window) if _ref_window else 0.0
        _dip_pct = (
            ((_ath_price - ind["current"]) / _ath_price) * 100.0
            if _ath_price > 0 else 0.0
        )

        setup_type, score, reasons = classify_setup(ind)
        if setup_type == "NONE":
            # Fall through to the dip-buy strategy (the universe scan
            # runs both technical AND dip-buy in series; scan_one
            # previously ran ONLY the technical scanner, which meant
            # a name like SBIN at -21% from the 52w high was rejected
            # as "No qualifying setup" instead of being surfaced as
            # a 52W_DIP candidate. Origin: 2026-05-14 user search
            # for SBIN. Threshold + lookback come from the same
            # Config knobs the universe DipBuyScanner uses.
            from modes.swing.signals import (
                score_breakout, score_pullback,
                score_trend_continuation, score_support_reversal,
            )
            # `generate_broker_instruction` is already imported at
            # module level (`from modes.swing.risk import ...`); a
            # second `from .. import ..` here would silently shadow
            # the module-level binding for the WHOLE function body
            # because Python treats any name assigned anywhere in a
            # function as local for the entire function. That broke
            # the successful-technical path (which is below this
            # branch) with `UnboundLocalError`. Don't re-import.
            dip_pct_cfg = float(getattr(self.cfg, "SWING_DIP_PCT", 10.0))
            target_pct_cfg = float(getattr(self.cfg, "SWING_DIP_TARGET_PCT", 20.0))
            buy_amount_cfg = float(getattr(self.cfg, "SWING_DIP_BUY_AMOUNT", 20000.0))
            lookback_cfg = max(20, int(getattr(self.cfg, "SWING_DIP_LOOKBACK_DAYS", 252)))
            ref_window = (ind["closes"][-lookback_cfg:]
                          if len(ind["closes"]) >= lookback_cfg
                          else ind["closes"])
            ref_high = max(ref_window) if ref_window else 0.0
            dip_from_ref = (
                ((ref_high - ind["current"]) / ref_high) * 100.0
                if ref_high > 0 else 0.0
            )
            if dip_from_ref >= dip_pct_cfg:
                # Build a 52W_DIP candidate. Mirror the DipBuyScanner
                # math (10% hard stop, +Y% target, fixed-rupee qty)
                # so the result is identical to what the universe
                # scan would have produced.
                stop = round(ind["current"] * 0.90, 2)
                target = round(ind["current"] * (1 + target_pct_cfg / 100.0), 2)
                qty = max(1, int(buy_amount_cfg / ind["current"]))
                risk_per = ind["current"] - stop
                reward_per = target - ind["current"]
                rr = (reward_per / risk_per) if risk_per > 0 else 0
                dip_reasons = [
                    f"Stock is {dip_from_ref:.1f}% below its "
                    f"{lookback_cfg}-day high of Rs.{ref_high:,.2f}",
                    f"Dip-buy strategy: buy when {dip_pct_cfg:.0f}%+ "
                    f"below 52w high (finite-cap V2 default 10%)",
                    f"Target: sell when price rises {target_pct_cfg:.0f}% "
                    f"from buy (finite-cap V2 default 20%)",
                    f"Buy Rs.{buy_amount_cfg:,.0f} = {qty} shares at "
                    f"Rs.{ind['current']:,.2f}",
                ]
                if symbol in open_symbols:
                    dip_reasons.append(
                        "Already held: eligible add-more candidate; "
                        "confirming a buy will average into the open swing book."
                    )
                cand = SwingCandidate(
                    symbol=symbol, exchange="NSE",
                    setup_type=SETUP_52W_DIP,
                    score=round(dip_from_ref, 1),
                    close_price=ind["current"],
                    entry_price=ind["current"],
                    stop_price=stop, target_price=target,
                    risk_rupees=round(risk_per * qty, 2),
                    reward_rupees=round(reward_per * qty, 2),
                    rr_ratio=round(rr, 2),
                    suggested_qty=qty, sector=sector,
                    sma_50=ind["sma_50"], sma_200=ind["sma_200"],
                    ema_20=ind["ema_20"], rsi_daily=ind["rsi"],
                    atr_14=ind["atr_14"],
                    relative_strength=ind["rel_strength"],
                    volume_ratio=ind["vol_ratio"],
                    high_20d=ind["high_20d"], high_50d=ind["high_50d"],
                    low_52w=ind["low_52w"], high_52w=ind["high_52w"],
                    weekly_trend_up=ind["weekly_trend_up"],
                    ath_price=round(ref_high, 2),
                    dip_from_ath_pct=round(dip_from_ref, 2),
                    reasons=dip_reasons,
                    status="ACCEPTED",
                    broker_instruction_json=json.dumps(
                        generate_broker_instruction(
                            symbol=symbol, exchange="NSE",
                            qty=qty, entry_price=ind["current"],
                            stop_price=stop, target_price=target,
                        ), default=str),
                )
                cand.priority_rank = 1
                cand.priority_score = cand.score
                action = SwingAction(
                    symbol=cand.symbol, exchange=cand.exchange,
                    action_type=ACTION_ENTRY, status=STATUS_PENDING,
                    suggested_qty=cand.suggested_qty,
                    suggested_price=cand.entry_price,
                    suggested_stop=cand.stop_price,
                    suggested_target=cand.target_price,
                    priority_rank=1,
                    live_price=cand.close_price,
                    broker_instruction_json=cand.broker_instruction_json,
                    created_at=now_ist().isoformat(),
                    notes=(
                        f"52w-dip add-more single-stock analyse for {symbol}"
                        if symbol in open_symbols
                        else f"52w-dip single-stock analyse for {symbol}"
                    ),
                )
                return cand, action

            # Neither technical nor dip-buy — return an enriched
            # rejection with per-setup score breakdown so the user
            # sees WHAT was close to qualifying instead of a bare
            # "No qualifying setup".
            sb = score_breakout(ind)
            sp = score_pullback(ind)
            stc = score_trend_continuation(ind)
            ssr = score_support_reversal(ind)
            score_breakdown = (
                f"BREAKOUT={sb[0]:.1f}, "
                f"PULLBACK={sp[0]:.1f}, "
                f"TREND_CONT={stc[0]:.1f}, "
                f"SUPPORT_REV={ssr[0]:.1f}"
            )
            dip_short = (
                f"; below 52w-dip threshold ({dip_from_ref:.1f}% < "
                f"{dip_pct_cfg:.0f}%)" if ref_high > 0 else ""
            )
            cand = SwingCandidate(
                symbol=symbol, setup_type="NONE", score=0,
                status="REJECTED",
                rejected_reason=(
                    f"No qualifying setup (need any score >= 2.0): "
                    f"{score_breakdown}{dip_short}"
                ),
                close_price=ind["current"], sector=sector,
                ath_price=round(_ath_price, 2),
                dip_from_ath_pct=round(_dip_pct, 2),
                sma_50=ind["sma_50"], sma_200=ind["sma_200"],
                ema_20=ind["ema_20"], rsi_daily=ind["rsi"],
                atr_14=ind["atr_14"], relative_strength=ind["rel_strength"],
                volume_ratio=ind["vol_ratio"],
                high_20d=ind["high_20d"], high_50d=ind["high_50d"],
                low_52w=ind["low_52w"], high_52w=ind["high_52w"],
                weekly_trend_up=ind["weekly_trend_up"],
            )
            return cand, None

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

        cand = SwingCandidate(
            symbol=symbol, exchange="NSE",
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
            sma_50=ind["sma_50"], sma_200=ind["sma_200"],
            ema_20=ind["ema_20"], rsi_daily=ind["rsi"],
            atr_14=ind["atr_14"], relative_strength=ind["rel_strength"],
            volume_ratio=ind["vol_ratio"],
            high_20d=ind["high_20d"], high_50d=ind["high_50d"],
            low_52w=ind["low_52w"], high_52w=ind["high_52w"],
            weekly_trend_up=ind["weekly_trend_up"],
            ath_price=round(_ath_price, 2),
            dip_from_ath_pct=round(_dip_pct, 2),
            reasons=reasons,
        )
        _attach_grades(
            cand, ind=ind, candles=candles, nifty_candles=nifty_candles,
            setup_score=score, setup_type=setup_type,
        )

        if symbol in open_symbols:
            cand.reasons = list(cand.reasons or []) + [
                "Already held: eligible add-more candidate; "
                "confirming a buy will average into the open swing book."
            ]
        if risk.rejected:
            cand.status = "REJECTED"
            cand.rejected_reason = risk.rejected_reason
            return cand, None
        portfolio_capital = swing_capital * 100.0
        ok, reason = check_portfolio_limits(
            new_risk_rupees=risk.risk_rupees,
            new_position_value=risk.position_value,
            new_sector=sector,
            existing_positions=existing_positions,
            swing_capital=portfolio_capital,
        )
        if not ok:
            cand.status = "REJECTED"
            cand.rejected_reason = reason
            return cand, None

        cand.status = "ACCEPTED"
        cand.broker_instruction_json = json.dumps(
            generate_broker_instruction(
                symbol=symbol, exchange="NSE",
                qty=risk.suggested_qty,
                entry_price=risk.entry_price,
                stop_price=risk.stop_price,
                target_price=risk.target_price,
            ), default=str)
        cand.priority_rank = 1
        cand.priority_score = cand.score

        action = SwingAction(
            symbol=cand.symbol, exchange=cand.exchange,
            action_type=ACTION_ENTRY, status=STATUS_PENDING,
            suggested_qty=cand.suggested_qty,
            suggested_price=cand.entry_price,
            suggested_stop=cand.stop_price,
            suggested_target=cand.target_price,
            priority_rank=1,
            live_price=cand.close_price,
            broker_instruction_json=cand.broker_instruction_json,
            created_at=now_ist().isoformat(),
            notes=(
                f"Add-more single-stock analyse for {symbol}"
                if symbol in open_symbols else f"Single-stock analyse for {symbol}"
            ),
        )
        return cand, action

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
