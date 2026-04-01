# ================================================================
# services/technical_indicators.py
# ================================================================
# Technical indicator calculations for V2 candle-based trading.
#
# All functions take a list of candle dicts (OHLCV) and return
# computed values. No external dependencies — pure arithmetic.
#
# Indicators and their trading purpose:
#   EMA (Exponential Moving Average) — trend direction & momentum.
#       9/21 crossover is a standard intraday signal.
#   RSI (Relative Strength Index)     — overbought/oversold detection.
#       Uses Wilder smoothing (the original method from 1978).
#   VWAP (Volume Weighted Avg Price)  — intraday institutional fair value.
#       Stocks above VWAP = institutional buying pressure.
#   SuperTrend                        — ATR-based trend-following system.
#       Provides dynamic support/resistance levels that adapt to
#       volatility. Widely used in Indian market algo trading.
#
# The composite score weights indicators by reliability:
#   SuperTrend change: ±3  (strongest — captures trend reversals)
#   EMA crossover:     ±2  (confirmed momentum shift)
#   RSI extreme:       ±1-3 (overbought/oversold, scaled by severity)
#   VWAP position:     ±1  (institutional bias)
#   Daily EMA bias:    ±1  (higher timeframe confluence)
#   Prev-day S&R:      ±0.5-1 (support/resistance proximity)
#   → Technical score range: ~-12 to +12
# ================================================================

import datetime


# ================================================================
# EMA — EXPONENTIAL MOVING AVERAGE
# ================================================================

def ema(candles: list[dict], period: int, field: str = "close") -> list[float]:
    """
    Computes EMA over the given period.
    Returns a list the same length as candles.

    The first `period` values use a Simple Moving Average (SMA) as seed,
    then true EMA (k = 2/(period+1)) kicks in. This matches the standard
    TradingView / Zerodha Kite EMA implementation.
    """
    values = [c[field] for c in candles]
    if len(values) < period:
        return values[:]

    result = [0.0] * len(values)
    # Seed with SMA for first `period` values
    sma = sum(values[:period]) / period
    result[period - 1] = sma

    multiplier = 2 / (period + 1)
    for i in range(period, len(values)):
        result[i] = (values[i] - result[i - 1]) * multiplier + result[i - 1]

    # Fill warmup period with SMA approximation
    for i in range(period - 1):
        result[i] = sum(values[:i + 1]) / (i + 1)

    return result


def ema_crossover(candles: list[dict], fast: int = 9, slow: int = 21) -> dict:
    """
    Detects EMA crossover signals.

    Returns:
      {
        "signal": "BULLISH_CROSS" | "BEARISH_CROSS" | "NONE",
        "fast_ema": float,   # current fast EMA value
        "slow_ema": float,   # current slow EMA value
        "spread_pct": float, # (fast - slow) / slow × 100
      }
    """
    if len(candles) < slow + 2:
        return {"signal": "NONE", "fast_ema": 0, "slow_ema": 0, "spread_pct": 0}

    fast_ema = ema(candles, fast)
    slow_ema = ema(candles, slow)

    curr_fast = fast_ema[-1]
    prev_fast = fast_ema[-2]
    curr_slow = slow_ema[-1]
    prev_slow = slow_ema[-2]

    spread = ((curr_fast - curr_slow) / curr_slow * 100) if curr_slow > 0 else 0

    if prev_fast <= prev_slow and curr_fast > curr_slow:
        signal = "BULLISH_CROSS"
    elif prev_fast >= prev_slow and curr_fast < curr_slow:
        signal = "BEARISH_CROSS"
    else:
        signal = "NONE"

    return {
        "signal": signal,
        "fast_ema": round(curr_fast, 2),
        "slow_ema": round(curr_slow, 2),
        "spread_pct": round(spread, 2),
    }


# ================================================================
# RSI — RELATIVE STRENGTH INDEX
# ================================================================

def rsi(candles: list[dict], period: int = 14) -> float:
    """
    Computes the RSI (0–100) using the standard Wilder smoothing method.
    Returns -1 if insufficient data.

    RSI > 70 → overbought (bearish signal)
    RSI < 30 → oversold (bullish signal)
    """
    if len(candles) < period + 1:
        return -1

    closes = [c["close"] for c in candles]
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Initial average gain/loss (SMA of first `period` changes)
    gains = [max(0, c) for c in changes[:period]]
    losses = [max(0, -c) for c in changes[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder smoothing for remaining changes
    for c in changes[period:]:
        avg_gain = (avg_gain * (period - 1) + max(0, c)) / period
        avg_loss = (avg_loss * (period - 1) + max(0, -c)) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def rsi_signal(candles: list[dict], period: int = 14) -> dict:
    """
    Returns RSI value with an actionable signal.

    Returns:
      {
        "rsi": float,
        "signal": "OVERBOUGHT" | "OVERSOLD" | "NEUTRAL",
        "strength": 1-3,
      }
    """
    val = rsi(candles, period)
    if val < 0:
        return {"rsi": -1, "signal": "NEUTRAL", "strength": 0}

    if val >= 80:
        return {"rsi": val, "signal": "OVERBOUGHT", "strength": 3}
    elif val >= 70:
        return {"rsi": val, "signal": "OVERBOUGHT", "strength": 2}
    elif val <= 20:
        return {"rsi": val, "signal": "OVERSOLD", "strength": 3}
    elif val <= 30:
        return {"rsi": val, "signal": "OVERSOLD", "strength": 2}
    else:
        return {"rsi": val, "signal": "NEUTRAL", "strength": 0}


# ================================================================
# VWAP — VOLUME WEIGHTED AVERAGE PRICE
# ================================================================

def vwap(candles: list[dict]) -> float:
    """
    Computes VWAP from intraday candles.
    VWAP = Σ(typical_price × volume) / Σ(volume)
    Typical price = (high + low + close) / 3

    Returns 0 if no volume data.
    """
    cum_tp_vol = 0.0
    cum_vol = 0

    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3
        vol = c.get("volume", 0)
        cum_tp_vol += tp * vol
        cum_vol += vol

    if cum_vol == 0:
        return 0.0
    return round(cum_tp_vol / cum_vol, 2)


def vwap_signal(candles: list[dict]) -> dict:
    """
    Computes VWAP and returns signal based on current price vs VWAP.

    Price above VWAP → bullish (stock stronger than average)
    Price below VWAP → bearish (stock weaker than average)
    Near VWAP (within 0.3%) → potential mean-reversion zone

    Returns:
      {
        "vwap": float,
        "price": float,         # latest close
        "deviation_pct": float, # (price - vwap) / vwap × 100
        "signal": "ABOVE_VWAP" | "BELOW_VWAP" | "AT_VWAP",
      }
    """
    if not candles:
        return {"vwap": 0, "price": 0, "deviation_pct": 0, "signal": "AT_VWAP"}

    v = vwap(candles)
    price = candles[-1]["close"]

    if v <= 0:
        return {"vwap": 0, "price": price, "deviation_pct": 0, "signal": "AT_VWAP"}

    dev = (price - v) / v * 100

    if abs(dev) < 0.3:
        signal = "AT_VWAP"
    elif dev > 0:
        signal = "ABOVE_VWAP"
    else:
        signal = "BELOW_VWAP"

    return {
        "vwap": v,
        "price": round(price, 2),
        "deviation_pct": round(dev, 2),
        "signal": signal,
    }


# ================================================================
# SUPERTREND
# ================================================================

def supertrend(candles: list[dict], period: int = 10, multiplier: float = 3.0) -> dict:
    """
    Computes the SuperTrend indicator (Olivier Seban).

    SuperTrend plots a single line that flips between support (UP trend)
    and resistance (DOWN trend) based on ATR volatility bands:
      Upper band = HL2 + (multiplier × ATR)
      Lower band = HL2 - (multiplier × ATR)

    The key innovation: bands are "locked" in the protective direction
    (lower band only moves up, upper band only moves down) until a
    close breaks through, which triggers a trend reversal.

    Parameters (10, 3.0) are the most commonly used defaults in Indian
    market algo trading. Period 10 on 15-min candles = 2.5 hour lookback.

    Returns:
      {
        "value": float,  # current SuperTrend level (support or resistance)
        "trend": "UP" | "DOWN",
        "signal": "BULLISH" | "BEARISH" | "NONE",  # only on trend change
      }
    """
    if len(candles) < period + 1:
        return {"value": 0, "trend": "NONE", "signal": "NONE"}

    # Calculate ATR
    true_ranges = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return {"value": 0, "trend": "NONE", "signal": "NONE"}

    # ATR using SMA (simplified, starting from index `period-1`)
    atr_values = [0.0] * len(true_ranges)
    atr_values[period - 1] = sum(true_ranges[:period]) / period
    for i in range(period, len(true_ranges)):
        atr_values[i] = (atr_values[i - 1] * (period - 1) + true_ranges[i]) / period

    # Compute SuperTrend bands
    # We align: candles[1:] has index i → true_ranges[i-1], atr_values[i-1]
    # Let's work with indices over candles[1:]
    n = len(candles)
    upper_band = [0.0] * n
    lower_band = [0.0] * n
    st = [0.0] * n
    trend = [1] * n  # 1 = UP, -1 = DOWN

    for i in range(period + 1, n):
        atr_idx = i - 2  # offset because true_ranges starts from candles[1]
        if atr_idx < 0 or atr_idx >= len(atr_values):
            continue
        atr = atr_values[atr_idx]
        hl2 = (candles[i]["high"] + candles[i]["low"]) / 2
        basic_upper = hl2 + multiplier * atr
        basic_lower = hl2 - multiplier * atr

        # Final upper band: min of current basic_upper and prev upper (if prev close <= prev upper)
        if upper_band[i - 1] > 0 and candles[i - 1]["close"] <= upper_band[i - 1]:
            upper_band[i] = min(basic_upper, upper_band[i - 1])
        else:
            upper_band[i] = basic_upper

        # Final lower band: max of current basic_lower and prev lower (if prev close >= prev lower)
        if lower_band[i - 1] > 0 and candles[i - 1]["close"] >= lower_band[i - 1]:
            lower_band[i] = max(basic_lower, lower_band[i - 1])
        else:
            lower_band[i] = basic_lower

        # Determine trend
        close = candles[i]["close"]
        if trend[i - 1] == 1:
            # Was UP trend — continue UP unless close drops below lower band
            if close < lower_band[i]:
                trend[i] = -1
                st[i] = upper_band[i]
            else:
                trend[i] = 1
                st[i] = lower_band[i]
        else:
            # Was DOWN trend — continue DOWN unless close rises above upper band
            if close > upper_band[i]:
                trend[i] = 1
                st[i] = lower_band[i]
            else:
                trend[i] = -1
                st[i] = upper_band[i]

    curr_trend = trend[-1]
    prev_trend = trend[-2] if len(trend) >= 2 else curr_trend

    signal = "NONE"
    if prev_trend == -1 and curr_trend == 1:
        signal = "BULLISH"
    elif prev_trend == 1 and curr_trend == -1:
        signal = "BEARISH"

    return {
        "value": round(st[-1], 2),
        "trend": "UP" if curr_trend == 1 else "DOWN",
        "signal": signal,
    }


# ================================================================
# PREVIOUS DAY SUPPORT / RESISTANCE
# ================================================================

def prev_day_sr_score(
    candles_day: list[dict],
    current_price: float,
    proximity_pct: float = 0.5,
) -> dict:
    """
    Checks if the current price is near the previous day's high, low,
    or pivot point — natural support/resistance levels.

    - Near prev day's high (within proximity_pct%) → resistance for longs
    - Near prev day's low  (within proximity_pct%) → support for shorts
    - Pivot = (H + L + C) / 3 — institutional reference level.

    Returns:
      {
        "score": float,       # positive = support (bullish), negative = resistance (bearish)
        "prev_high": float,
        "prev_low": float,
        "pivot": float,
        "signal": "AT_RESISTANCE" | "AT_SUPPORT" | "ABOVE_PIVOT" | "BELOW_PIVOT" | "NONE",
      }
    """
    if not candles_day or len(candles_day) < 1 or current_price <= 0:
        return {"score": 0, "prev_high": 0, "prev_low": 0, "pivot": 0, "signal": "NONE"}

    # Zerodha daily API returns only completed candles (no today partial).
    # So [-1] is the most recent completed trading day (= yesterday),
    # which is what we want for previous-day S&R levels.
    prev = candles_day[-1]
    prev_high = prev["high"]
    prev_low = prev["low"]
    prev_close = prev["close"]
    pivot = round((prev_high + prev_low + prev_close) / 3, 2)

    score = 0.0
    signal = "NONE"

    dist_high_pct = abs(current_price - prev_high) / prev_high * 100 if prev_high > 0 else 999
    dist_low_pct = abs(current_price - prev_low) / prev_low * 100 if prev_low > 0 else 999

    # If near both (tiny-range day), pick whichever level is closer
    near_high = dist_high_pct <= proximity_pct
    near_low = dist_low_pct <= proximity_pct

    if near_high and near_low:
        # Near both — pick the closer level
        if dist_high_pct <= dist_low_pct:
            score -= 1
            signal = "AT_RESISTANCE"
        else:
            score += 1
            signal = "AT_SUPPORT"
    elif near_high:
        score -= 1  # at resistance — headwind for longs
        signal = "AT_RESISTANCE"
    elif near_low:
        score += 1  # at support — tailwind for longs
        signal = "AT_SUPPORT"

    # Pivot bias (if not already near H/L)
    if signal == "NONE" and pivot > 0:
        if current_price > pivot:
            score += 0.5
            signal = "ABOVE_PIVOT"
        elif current_price < pivot:
            score -= 0.5
            signal = "BELOW_PIVOT"

    return {
        "score": score,
        "prev_high": prev_high,
        "prev_low": prev_low,
        "pivot": pivot,
        "signal": signal,
    }


# ================================================================
# COMPOSITE SCORE
# ================================================================

def compute_technical_score(
    candles_15m: list[dict],
    candles_day: list[dict] | None = None,
    current_price: float | None = None,
) -> dict:
    """
    Computes a composite technical score from multiple indicators
    using 15-minute intraday candles + optional daily candles.

    Score range: ~-12 to +12
      Positive = bullish setup
      Negative = bearish setup
      |score| >= 5 = strong signal

    Returns:
      {
        "score": float,
        "signal": "STRONG_BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG_SELL",
        "ema_cross": dict,
        "rsi": dict,
        "vwap": dict,
        "supertrend": dict,
        "prev_day_sr": dict,
      }
    """
    score = 0.0

    # EMA crossover (9/21 on 15m candles)
    ema_data = ema_crossover(candles_15m, fast=9, slow=21)
    if ema_data["signal"] == "BULLISH_CROSS":
        score += 2
    elif ema_data["signal"] == "BEARISH_CROSS":
        score -= 2
    elif ema_data["spread_pct"] > 0.5:
        score += 1  # fast above slow = mild bullish
    elif ema_data["spread_pct"] < -0.5:
        score -= 1

    # RSI (14-period on 15m candles)
    rsi_data = rsi_signal(candles_15m, period=14)
    if rsi_data["signal"] == "OVERSOLD":
        score += rsi_data["strength"]
    elif rsi_data["signal"] == "OVERBOUGHT":
        score -= rsi_data["strength"]

    # VWAP — must use today's candles only (VWAP resets daily)
    today = datetime.date.today()
    today_candles = []
    for c in candles_15m:
        dt = c.get("date")
        if dt is None:
            continue
        cdate = dt.date() if hasattr(dt, "date") else dt
        if cdate == today:
            today_candles.append(c)
    vwap_data = vwap_signal(today_candles) if today_candles else vwap_signal(candles_15m)
    if vwap_data["signal"] == "ABOVE_VWAP":
        score += 1
    elif vwap_data["signal"] == "BELOW_VWAP":
        score -= 1

    # SuperTrend (10, 3.0 on 15m candles)
    st_data = supertrend(candles_15m, period=10, multiplier=3.0)
    if st_data["signal"] == "BULLISH":
        score += 3
    elif st_data["signal"] == "BEARISH":
        score -= 3
    elif st_data["trend"] == "UP":
        score += 1
    elif st_data["trend"] == "DOWN":
        score -= 1

    # Daily EMA trend bias (if available)
    if candles_day and len(candles_day) >= 22:
        day_ema = ema_crossover(candles_day, fast=9, slow=21)
        if day_ema["spread_pct"] > 1:
            score += 1
        elif day_ema["spread_pct"] < -1:
            score -= 1

    # Previous day's high/low as support/resistance
    price = current_price or (candles_15m[-1]["close"] if candles_15m else 0)
    sr_data = prev_day_sr_score(candles_day, price) if candles_day else {
        "score": 0, "prev_high": 0, "prev_low": 0, "pivot": 0, "signal": "NONE"
    }
    score += sr_data["score"]

    # Map score to signal
    if score >= 5:
        signal = "STRONG_BUY"
    elif score >= 2:
        signal = "BUY"
    elif score <= -5:
        signal = "STRONG_SELL"
    elif score <= -2:
        signal = "SELL"
    else:
        signal = "NEUTRAL"

    return {
        "score": round(score, 1),
        "signal": signal,
        "ema_cross": ema_data,
        "rsi": rsi_data,
        "vwap": vwap_data,
        "supertrend": st_data,
        "prev_day_sr": sr_data,
    }
