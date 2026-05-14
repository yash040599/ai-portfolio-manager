# ================================================================
# modes/swing/signals.py
# ================================================================
# Swing setup detection on daily/weekly candles. Each function
# returns a score component (float) and a list of reason strings.
#
# Four setup families (SWING_STRATEGY §5):
#   1. Breakout        — close above 20/50-day high with volume
#   2. Pullback        — pullback to EMA-20/SMA-50 in uptrend
#   3. Trend cont.     — trending with SMAs stacked, not extended
#   4. Support reversal — bounce off SMA-200 / 52w support
#
# All functions take the same candle list (daily, oldest-first)
# and the same indicator dict. Pure arithmetic — no API calls.
# ================================================================

from __future__ import annotations

from shared.technical_indicators import ema, rsi as compute_rsi


# ── Indicator computation ───────────────────────────────────────

def compute_swing_indicators(daily_candles: list[dict],
                             nifty_candles: list[dict] | None = None,
                             ) -> dict:
    """Compute all swing-relevant indicators from daily candles.

    Returns a dict that other signal functions consume. The candle
    list must be oldest-first and at least 200 bars for full SMA-200.
    """
    if len(daily_candles) < 30:
        return {"valid": False, "reason": "insufficient daily history"}

    closes = [c["close"] for c in daily_candles]
    highs  = [c["high"]  for c in daily_candles]
    lows   = [c["low"]   for c in daily_candles]
    vols   = [c.get("volume", 0) for c in daily_candles]

    n = len(closes)
    current = closes[-1]

    # Moving averages
    ema_20  = ema(daily_candles, 20)[-1]
    sma_50  = sum(closes[-50:]) / min(50, n)   if n >= 50  else current
    sma_200 = sum(closes[-200:]) / min(200, n) if n >= 200 else current

    # RSI
    rsi_val = compute_rsi(daily_candles, period=14)

    # ATR(14)
    atr_14 = _atr(daily_candles, 14)

    # Volume ratio
    avg_vol_20 = sum(vols[-20:]) / max(1, min(20, len(vols)))
    vol_ratio = (vols[-1] / avg_vol_20) if avg_vol_20 > 0 else 1.0

    # Recent high/low
    high_20d = max(highs[-20:]) if n >= 20 else max(highs)
    high_50d = max(highs[-50:]) if n >= 50 else max(highs)
    low_52w  = min(lows[-252:])  if n >= 252 else min(lows)
    high_52w = max(highs[-252:]) if n >= 252 else max(highs)

    # NR7 — Narrow Range 7 (S26). True when today's daily range
    # (high − low) is the smallest of the last 7 daily candles.
    # Used as a volume-contraction proxy on BREAKOUT setups: a
    # breakout fired AFTER a multi-day range contraction is the
    # higher-EV variant (Mark Minervini's VCP logic). Simple
    # boolean so the BREAKOUT scorer can fold it in cleanly.
    nr7 = False
    if n >= 7:
        ranges7 = [highs[-i] - lows[-i] for i in range(1, 8)]
        if ranges7 and ranges7[0] > 0:
            nr7 = ranges7[0] == min(ranges7)

    # Weekly trend (simple: is the 10-week SMA rising?)
    weekly_trend_up = True
    if n >= 55:
        wk10_now  = sum(closes[-50:]) / 50
        wk10_prev = sum(closes[-55:-5]) / 50
        weekly_trend_up = wk10_now > wk10_prev

    # Relative strength vs NIFTY
    rel_strength = 0.0
    if nifty_candles and len(nifty_candles) >= 60:
        stock_ret = (closes[-1] / closes[-60] - 1) * 100 if closes[-60] > 0 else 0
        nifty_cls = [c["close"] for c in nifty_candles]
        nifty_ret = (nifty_cls[-1] / nifty_cls[-60] - 1) * 100 if nifty_cls[-60] > 0 else 0
        rel_strength = stock_ret - nifty_ret

    return {
        "valid":          True,
        "current":        current,
        "ema_20":         ema_20,
        "sma_50":         sma_50,
        "sma_200":        sma_200,
        "rsi":            rsi_val,
        "atr_14":         atr_14,
        "vol_ratio":      vol_ratio,
        "high_20d":       high_20d,
        "high_50d":       high_50d,
        "low_52w":        low_52w,
        "high_52w":       high_52w,
        "nr7":            nr7,
        "weekly_trend_up": weekly_trend_up,
        "rel_strength":   rel_strength,
        "closes":         closes,
        "highs":          highs,
        "lows":           lows,
    }


# ── Setup detectors ─────────────────────────────────────────────

def score_breakout(ind: dict) -> tuple[float, list[str]]:
    """Breakout: close above 20/50-day high with volume."""
    if not ind.get("valid"):
        return 0.0, []

    score = 0.0
    reasons: list[str] = []
    c = ind["current"]

    # Close above 20-day high
    if c > ind["high_20d"] * 0.998:
        score += 2.0
        reasons.append(f"Close above 20d high ({ind['high_20d']:.1f})")
    # Close above 50-day high (stronger)
    if c > ind["high_50d"] * 0.998:
        score += 1.5
        reasons.append(f"Close above 50d high ({ind['high_50d']:.1f})")

    # Volume confirmation
    if ind["vol_ratio"] >= 1.5:
        score += 1.5
        reasons.append(f"Volume {ind['vol_ratio']:.1f}x avg")
    elif ind["vol_ratio"] >= 1.2:
        score += 0.5
        reasons.append(f"Volume {ind['vol_ratio']:.1f}x avg (moderate)")

    # NR7 contraction → expansion (S26).
    # A breakout that fires AFTER a multi-day range contraction is
    # the higher-EV variant — quiet base, then volume + price step
    # out together. Only count when both conditions are present
    # (NR7 alone is not enough; we still need today's volume to
    # confirm the breakout).
    if ind.get("nr7") and ind["vol_ratio"] >= 1.2:
        score += 1.0
        reasons.append("NR7 contraction → expansion (volume confirms)")

    # Above key MAs
    if c > ind["sma_50"] and c > ind["sma_200"]:
        score += 1.0
        reasons.append("Above SMA-50 and SMA-200")

    # Relative strength
    if ind["rel_strength"] > 5:
        score += 1.0
        reasons.append(f"RS vs NIFTY +{ind['rel_strength']:.1f}%")

    # Weekly trend
    if ind["weekly_trend_up"]:
        score += 0.5
        reasons.append("Weekly trend up")

    # Penalty: too extended from EMA-20
    ext_pct = (c / ind["ema_20"] - 1) * 100 if ind["ema_20"] > 0 else 0
    if ext_pct > 8:
        score -= 2.0
        reasons.append(f"Extended {ext_pct:.1f}% above EMA-20")

    return score, reasons


def score_pullback(ind: dict) -> tuple[float, list[str]]:
    """Pullback in uptrend: price pulls back to EMA-20/SMA-50."""
    if not ind.get("valid"):
        return 0.0, []

    score = 0.0
    reasons: list[str] = []
    c = ind["current"]

    # Must be in uptrend
    if not (c > ind["sma_200"] and ind["sma_50"] > ind["sma_200"]):
        return 0.0, []

    # Pullback proximity to EMA-20
    dist_ema20 = abs(c / ind["ema_20"] - 1) * 100 if ind["ema_20"] > 0 else 99
    if dist_ema20 <= 3.0:
        score += 3.0
        reasons.append(f"Pulled back to EMA-20 ({dist_ema20:.1f}% away)")
    elif dist_ema20 <= 5.0:
        score += 1.5
        reasons.append(f"Near EMA-20 ({dist_ema20:.1f}% away)")

    # Pullback proximity to SMA-50
    dist_sma50 = abs(c / ind["sma_50"] - 1) * 100 if ind["sma_50"] > 0 else 99
    if dist_sma50 <= 2.0:
        score += 2.0
        reasons.append(f"Pulled back to SMA-50 ({dist_sma50:.1f}% away)")

    # RSI in buy zone
    rsi = ind["rsi"]
    if 40 <= rsi <= 60:
        score += 1.5
        reasons.append(f"RSI {rsi:.0f} in pullback zone")

    # Weekly trend
    if ind["weekly_trend_up"]:
        score += 0.5
        reasons.append("Weekly trend up")

    # RS vs NIFTY
    if ind["rel_strength"] > 3:
        score += 0.5
        reasons.append(f"RS vs NIFTY +{ind['rel_strength']:.1f}%")

    return score, reasons


def score_trend_continuation(ind: dict) -> tuple[float, list[str]]:
    """Trend continuation: SMAs stacked, not extended."""
    if not ind.get("valid"):
        return 0.0, []

    score = 0.0
    reasons: list[str] = []
    c = ind["current"]

    # SMA stack: 20 > 50 > 200
    if not (ind["ema_20"] > ind["sma_50"] > ind["sma_200"]):
        return 0.0, []

    score += 2.0
    reasons.append("SMAs stacked: EMA-20 > SMA-50 > SMA-200")

    # Not too extended from EMA-20
    ext_pct = (c / ind["ema_20"] - 1) * 100 if ind["ema_20"] > 0 else 0
    if ext_pct <= 5:
        score += 1.5
        reasons.append(f"Not extended ({ext_pct:.1f}% from EMA-20)")
    elif ext_pct <= 8:
        score += 0.5
        reasons.append(f"Slightly extended ({ext_pct:.1f}% from EMA-20)")
    else:
        score -= 1.0
        reasons.append(f"Too extended ({ext_pct:.1f}% from EMA-20)")

    # Volume on up days stronger
    if ind["vol_ratio"] >= 1.0:
        score += 0.5
        reasons.append(f"Volume OK ({ind['vol_ratio']:.1f}x)")

    # Weekly trend
    if ind["weekly_trend_up"]:
        score += 1.0
        reasons.append("Weekly trend aligned")

    # RS vs NIFTY
    if ind["rel_strength"] > 3:
        score += 0.5
        reasons.append(f"RS vs NIFTY +{ind['rel_strength']:.1f}%")

    return score, reasons


def score_support_reversal(ind: dict) -> tuple[float, list[str]]:
    """Support reversal: bounce from SMA-200 / 52w support.

    Hard gate (S27, 2026-05-14): the weekly trend MUST already be
    turning up (10-week SMA rising) before this setup admits a
    candidate. Earlier the soft "score -= 0.5; reasons += 'lower
    conviction'" path admitted reversals on a still-falling weekly
    tape — the textbook "catching a falling knife". A real trend
    turn (10-week SMA rising again) is the only valid reversal
    trigger; without it, RSI lifting from oversold is just an
    oscillator twitch on an active downtrend.
    """
    if not ind.get("valid"):
        return 0.0, []
    if not ind.get("weekly_trend_up"):
        return 0.0, []   # hard gate

    score = 0.0
    reasons: list[str] = []
    c = ind["current"]

    # Near SMA-200
    dist_200 = abs(c / ind["sma_200"] - 1) * 100 if ind["sma_200"] > 0 else 99
    if dist_200 <= 3.0:
        score += 2.0
        reasons.append(f"Near SMA-200 ({dist_200:.1f}% away)")

    # Near 52w low
    dist_low = (c / ind["low_52w"] - 1) * 100 if ind["low_52w"] > 0 else 99
    if dist_low <= 10:
        score += 1.5
        reasons.append(f"Within {dist_low:.1f}% of 52w low")

    # RSI recovering from oversold
    rsi = ind["rsi"]
    if rsi <= 40 and rsi >= 25:
        score += 1.5
        reasons.append(f"RSI {rsi:.0f} recovering from oversold")

    # Weekly-trend-up confirmation reason (the gate already passed
    # above; this surfaces it in the explanation so the user can
    # see why the reversal is no-longer-falling-knife).
    reasons.append("Weekly trend turned up (10-week SMA rising)")

    return score, reasons


# ── 52-week-high proximity (additive scoring component) ────────
#
# Added 2026-05-14. Returns a (bonus, penalty, reasons) tuple that
# the four core detectors can fold in. Continuation-friendly setups
# (BREAKOUT, TREND_CONTINUATION) treat closeness to the 52w high as
# a *bonus*; mean-reversion setups (PULLBACK, SUPPORT_REVERSAL) use
# the same number as a *penalty* — a stock perched at its 52w high
# has by definition not pulled back, and reversal setups should not
# trigger up there.
#
# The user explicitly asked for 52w-high proximity to be a scored
# input ("having 52week high data point also is good can we rate
# that in scoring also?"). Per the financial-analyst lens this is a
# canonical large-cap signal — most institutional momentum buyers
# add at the 52w break, so positions stalling within ~3% of the 52w
# high get continuation-priced before the actual breakout candle.

def score_52w_high_proximity(ind: dict) -> tuple[float, list[str]]:
    """Bonus when current close is near the 52w high.

    Returns (bonus_score, reasons). The same magnitude is used as
    a *penalty* for mean-reversion setups via the negative of the
    return value — see classify_setup() for the wiring.
    """
    if not ind.get("valid"):
        return 0.0, []

    h52 = ind.get("high_52w") or 0
    c = ind.get("current") or 0
    if h52 <= 0 or c <= 0:
        return 0.0, []

    # `dist_pct` is positive when current is below the 52w high
    # (the common case), zero when at the high, negative when above
    # (a fresh 52w-high close).
    dist_pct = (h52 - c) / h52 * 100.0

    # Stronger bonus the closer we are. Ladder picked to keep the
    # contribution comparable to the existing volume / RS bumps
    # (~+0.5 to +2.0) so adding it doesn't dominate any single
    # setup's score.
    if dist_pct <= 0:
        # Closing AT or ABOVE the prior 52w high — fresh-high day,
        # the strongest continuation tape there is.
        return 2.0, [f"Closed at fresh 52w high (Rs.{h52:,.2f})"]
    if dist_pct <= 1.5:
        return 1.5, [f"Within 1.5% of 52w high (Rs.{h52:,.2f}; -{dist_pct:.1f}%)"]
    if dist_pct <= 3.0:
        return 1.0, [f"Within 3% of 52w high (Rs.{h52:,.2f}; -{dist_pct:.1f}%)"]
    if dist_pct <= 5.0:
        return 0.5, [f"Within 5% of 52w high (Rs.{h52:,.2f}; -{dist_pct:.1f}%)"]
    return 0.0, []


# ── Best setup selector ────────────────────────────────────────

# Setups that BENEFIT from being close to the 52w high (continuation
# side): a stock perched near its 52w high is exactly what these
# setups are looking to buy.
_HIGH_PROXIMITY_BONUS_SETUPS = {"BREAKOUT", "TREND_CONTINUATION"}

# Setups that should be PENALISED when too close to the 52w high
# (mean-reversion side): a "pullback" or "reversal" trigger that
# fires within 3% of the 52w high is by definition not a real
# pullback, it's a continuation in disguise — ranking it here
# would let a fully-extended name slip through under the wrong
# label. We zero out the penalty when dist_pct > 5%.
_HIGH_PROXIMITY_PENALTY_SETUPS = {"PULLBACK_UPTREND", "SUPPORT_REVERSAL"}


def classify_setup(ind: dict) -> tuple[str, float, list[str]]:
    """Run all four setup detectors and return the best one.

    Returns (setup_type, score, reasons). If no setup qualifies
    (all scores < 2.0), returns ("NONE", 0.0, []).

    52w-high proximity bonus / penalty (2026-05-14):
      - Continuation setups (BREAKOUT, TREND_CONTINUATION) get the
        positive bonus from `score_52w_high_proximity()`.
      - Mean-reversion setups (PULLBACK_UPTREND, SUPPORT_REVERSAL)
        get the same magnitude as a *penalty* — a "pullback" near
        the 52w high is not a pullback, it's an extended continuation.
    """
    base_setups = [
        ("BREAKOUT",           *score_breakout(ind)),
        ("PULLBACK_UPTREND",   *score_pullback(ind)),
        ("TREND_CONTINUATION", *score_trend_continuation(ind)),
        ("SUPPORT_REVERSAL",   *score_support_reversal(ind)),
    ]

    h52_bonus, h52_reasons = score_52w_high_proximity(ind)

    setups = []
    for name, score, reasons in base_setups:
        if h52_bonus > 0:
            if name in _HIGH_PROXIMITY_BONUS_SETUPS:
                score += h52_bonus
                reasons = list(reasons) + h52_reasons
            elif name in _HIGH_PROXIMITY_PENALTY_SETUPS:
                score -= h52_bonus
                reasons = list(reasons) + [
                    f"Penalty: too close to 52w high ({h52_reasons[0]})"
                ]
        setups.append((name, score, reasons))

    # Sort by score descending
    setups.sort(key=lambda x: x[1], reverse=True)
    best_type, best_score, best_reasons = setups[0]

    if best_score < 2.0:
        return "NONE", 0.0, ["No qualifying swing setup"]

    return best_type, best_score, best_reasons


# ── Sector rotation bonus (S28) ────────────────────────────────
#
# The candidate's `sector` field is captured by both scanners but
# was previously unused in scoring. This helper is called from
# SwingManager once per scan, AFTER both scanners produce their
# candidate lists, to add a +0.5 bonus to accepted candidates
# sitting in the top-ranked sectors by today's mean relative
# strength. Manager-level so we have access to the full universe's
# RS map without any per-symbol lookup.

# Configuration — number of leading sectors to bonus, and the size
# of the bonus. Picked to match the existing volume / RS bumps so
# the modifier never single-handedly flips a candidate's verdict.
SECTOR_LEADER_TOP_N        = 3
SECTOR_LEADER_BONUS        = 0.5
# Minimum candidates per sector before its mean RS is considered
# meaningful — a sector with one outlier shouldn't drag the rest
# of the book up. Picked low enough that a thin sector still gets
# a vote.
SECTOR_LEADER_MIN_SAMPLES  = 2


def compute_sector_rs(candidates: list) -> dict[str, float]:
    """Return `{SECTOR: mean_relative_strength}` from a candidate
    list (any status). Sectors with fewer than
    `SECTOR_LEADER_MIN_SAMPLES` candidates are excluded so a single
    outlier can't flip the ranking.

    Pure helper — no dependency on Config. Keeps SwingManager
    free of arithmetic so a future swap to a real sector-index
    feed (S33-style) drops in cleanly here.
    """
    by_sector: dict[str, list[float]] = {}
    for c in candidates:
        sector = getattr(c, "sector", "") or "OTHER"
        rs = float(getattr(c, "relative_strength", 0.0))
        if sector == "OTHER":
            continue
        by_sector.setdefault(sector, []).append(rs)
    return {
        sec: sum(rs_list) / len(rs_list)
        for sec, rs_list in by_sector.items()
        if len(rs_list) >= SECTOR_LEADER_MIN_SAMPLES
    }


def top_n_sectors_by_rs(sector_rs: dict[str, float],
                        n: int = SECTOR_LEADER_TOP_N) -> list[str]:
    """Return the top-N sector names by mean RS, descending."""
    return [sec for sec, _ in sorted(sector_rs.items(),
                                     key=lambda kv: kv[1],
                                     reverse=True)[:n]]


# ── ATR helper ─────────────────────────────────────────────────

def _atr(candles: list[dict], period: int = 14) -> float:
    """Average True Range over `period` daily candles."""
    if len(candles) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if not trs:
        return 0.0
    # Wilder smoothing for ATR
    atr_val = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return atr_val
