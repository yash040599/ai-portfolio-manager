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
    """Support reversal: bounce from SMA-200 / 52w support."""
    if not ind.get("valid"):
        return 0.0, []

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

    # Weekly trend doesn't need to be up for reversals, but check
    if not ind["weekly_trend_up"]:
        score -= 0.5
        reasons.append("Weekly trend still down (lower conviction)")

    return score, reasons


# ── Best setup selector ────────────────────────────────────────

def classify_setup(ind: dict) -> tuple[str, float, list[str]]:
    """Run all four setup detectors and return the best one.

    Returns (setup_type, score, reasons). If no setup qualifies
    (all scores < 2.0), returns ("NONE", 0.0, []).
    """
    setups = [
        ("BREAKOUT",           *score_breakout(ind)),
        ("PULLBACK_UPTREND",   *score_pullback(ind)),
        ("TREND_CONTINUATION", *score_trend_continuation(ind)),
        ("SUPPORT_REVERSAL",   *score_support_reversal(ind)),
    ]

    # Sort by score descending
    setups.sort(key=lambda x: x[1], reverse=True)
    best_type, best_score, best_reasons = setups[0]

    if best_score < 2.0:
        return "NONE", 0.0, ["No qualifying swing setup"]

    return best_type, best_score, best_reasons


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
