# ================================================================
# services/candle_patterns.py
# ================================================================
# Pure-math candlestick pattern detection for V2 trading strategy.
#
# All functions take a list of candle dicts (from Zerodha historical
# API) and return detected patterns with signal strength.
#
# Candle dict format: {date, open, high, low, close, volume}
#
# Patterns detected (based on Steve Nison's Japanese Candlestick
# Charting Techniques — the standard reference):
#   Single-candle:  Hammer, Inverted Hammer, Shooting Star,
#                   Hanging Man, Doji, Marubozu
#   Multi-candle:   Bullish Engulfing, Bearish Engulfing,
#                   Morning Star, Evening Star,
#                   Three White Soldiers, Three Black Crows,
#                   Bullish/Bearish Harami
#
# Each detector returns a dict:
#   {"pattern": str, "signal": "BULLISH"|"BEARISH", "strength": 1-3}
#   strength 1 = weak/confirmation needed, 2 = moderate, 3 = strong
#
# IMPORTANT: When used in pre-market scans, the "last candle" is from
# the previous trading day's close. Patterns detected are carry-over
# setups that suggest opening direction. During live monitoring,
# patterns are detected on fresh intraday candles (5-min/15-min).
#
# No external dependencies — everything is plain arithmetic on OHLCV.
# ================================================================


def body(c: dict) -> float:
    """Absolute size of the real body."""
    return abs(c["close"] - c["open"])


def upper_shadow(c: dict) -> float:
    """Length of the upper wick."""
    return c["high"] - max(c["close"], c["open"])


def lower_shadow(c: dict) -> float:
    """Length of the lower wick."""
    return min(c["close"], c["open"]) - c["low"]


def candle_range(c: dict) -> float:
    """Total high-low range."""
    return c["high"] - c["low"]


def is_bullish(c: dict) -> bool:
    """Close > Open."""
    return c["close"] > c["open"]


def is_bearish(c: dict) -> bool:
    """Close < Open."""
    return c["close"] < c["open"]


def midpoint(c: dict) -> float:
    """Midpoint of the body."""
    return (c["close"] + c["open"]) / 2


def body_pct(c: dict) -> float:
    """Body as % of total range. Returns 0 if flat candle."""
    r = candle_range(c)
    return (body(c) / r * 100) if r > 0 else 0


# ================================================================
# SINGLE-CANDLE PATTERNS
# ================================================================

def detect_doji(c: dict) -> dict | None:
    """
    Doji: body is < 10% of total range.
    Signals indecision — potential reversal.
    """
    r = candle_range(c)
    if r <= 0:
        return None
    if body(c) / r < 0.10:
        return {"pattern": "DOJI", "signal": "NEUTRAL", "strength": 1}
    return None


def detect_hammer(candles: list[dict]) -> dict | None:
    """
    Hammer: appears in a downtrend. Small body at the top,
    long lower shadow (>= 2× body), small/no upper shadow.
    Bullish reversal signal.
    """
    if len(candles) < 5:
        return None
    c = candles[-1]
    r = candle_range(c)
    if r <= 0:
        return None

    b = body(c)
    ls = lower_shadow(c)
    us = upper_shadow(c)

    # Must have lower shadow >= 2× body and upper shadow < body
    if b > 0 and ls >= 2 * b and us <= b * 0.5:
        # Check if in a downtrend (last 4 candles trending down)
        if _is_downtrend(candles[-5:-1]):
            return {"pattern": "HAMMER", "signal": "BULLISH", "strength": 2}
    return None


def detect_inverted_hammer(candles: list[dict]) -> dict | None:
    """
    Inverted Hammer: appears in a downtrend. Small body at the bottom,
    long upper shadow (>= 2× body), small/no lower shadow.
    Bullish reversal signal.
    """
    if len(candles) < 5:
        return None
    c = candles[-1]
    r = candle_range(c)
    if r <= 0:
        return None

    b = body(c)
    us = upper_shadow(c)
    ls = lower_shadow(c)

    if b > 0 and us >= 2 * b and ls <= b * 0.5:
        if _is_downtrend(candles[-5:-1]):
            return {"pattern": "INVERTED_HAMMER", "signal": "BULLISH", "strength": 2}
    return None


def detect_shooting_star(candles: list[dict]) -> dict | None:
    """
    Shooting Star: appears in an uptrend. Small body at the bottom,
    long upper shadow (>= 2× body). Bearish reversal signal.
    """
    if len(candles) < 5:
        return None
    c = candles[-1]
    r = candle_range(c)
    if r <= 0:
        return None

    b = body(c)
    us = upper_shadow(c)
    ls = lower_shadow(c)

    if b > 0 and us >= 2 * b and ls <= b * 0.5:
        if _is_uptrend(candles[-5:-1]):
            return {"pattern": "SHOOTING_STAR", "signal": "BEARISH", "strength": 2}
    return None


def detect_hanging_man(candles: list[dict]) -> dict | None:
    """
    Hanging Man: appears in an uptrend. Same shape as a hammer
    but context makes it bearish.
    """
    if len(candles) < 5:
        return None
    c = candles[-1]
    r = candle_range(c)
    if r <= 0:
        return None

    b = body(c)
    ls = lower_shadow(c)
    us = upper_shadow(c)

    if b > 0 and ls >= 2 * b and us <= b * 0.5:
        if _is_uptrend(candles[-5:-1]):
            return {"pattern": "HANGING_MAN", "signal": "BEARISH", "strength": 2}
    return None


def detect_marubozu(c: dict) -> dict | None:
    """
    Marubozu: body is > 90% of range — very little/no shadow.
    Strong directional conviction.
    """
    r = candle_range(c)
    if r <= 0:
        return None
    if body(c) / r > 0.90:
        signal = "BULLISH" if is_bullish(c) else "BEARISH"
        return {"pattern": "MARUBOZU", "signal": signal, "strength": 3}
    return None


# ================================================================
# MULTI-CANDLE PATTERNS
# ================================================================

def detect_bullish_engulfing(candles: list[dict]) -> dict | None:
    """
    Bullish Engulfing: 2-candle pattern.
    Prev = bearish, current = bullish.
    Current body fully engulfs previous body.
    Stronger after a downtrend.
    """
    if len(candles) < 3:
        return None
    prev, curr = candles[-2], candles[-1]

    if is_bearish(prev) and is_bullish(curr):
        if curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
            strength = 3 if _is_downtrend(candles[-5:-2]) else 2
            return {"pattern": "BULLISH_ENGULFING", "signal": "BULLISH", "strength": strength}
    return None


def detect_bearish_engulfing(candles: list[dict]) -> dict | None:
    """
    Bearish Engulfing: 2-candle pattern.
    Prev = bullish, current = bearish.
    Current body fully engulfs previous body.
    """
    if len(candles) < 3:
        return None
    prev, curr = candles[-2], candles[-1]

    if is_bullish(prev) and is_bearish(curr):
        if curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
            strength = 3 if _is_uptrend(candles[-5:-2]) else 2
            return {"pattern": "BEARISH_ENGULFING", "signal": "BEARISH", "strength": strength}
    return None


def detect_morning_star(candles: list[dict]) -> dict | None:
    """
    Morning Star: 3-candle pattern (bullish reversal).
    1. Large bearish candle
    2. Small-bodied candle (star) that gaps down
    3. Large bullish candle that closes above midpoint of candle 1
    """
    if len(candles) < 3:
        return None
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    r1 = candle_range(c1)

    if r1 <= 0:
        return None

    # c1: big bearish, c2: small body, c3: big bullish
    if (is_bearish(c1) and body_pct(c1) > 50
            and body_pct(c2) < 30
            and is_bullish(c3) and body_pct(c3) > 50
            and c3["close"] > midpoint(c1)):
        return {"pattern": "MORNING_STAR", "signal": "BULLISH", "strength": 3}
    return None


def detect_evening_star(candles: list[dict]) -> dict | None:
    """
    Evening Star: 3-candle pattern (bearish reversal).
    1. Large bullish candle
    2. Small-bodied candle (star) that gaps up
    3. Large bearish candle that closes below midpoint of candle 1
    """
    if len(candles) < 3:
        return None
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    r1 = candle_range(c1)

    if r1 <= 0:
        return None

    if (is_bullish(c1) and body_pct(c1) > 50
            and body_pct(c2) < 30
            and is_bearish(c3) and body_pct(c3) > 50
            and c3["close"] < midpoint(c1)):
        return {"pattern": "EVENING_STAR", "signal": "BEARISH", "strength": 3}
    return None


def detect_three_white_soldiers(candles: list[dict]) -> dict | None:
    """
    Three White Soldiers: 3 consecutive bullish candles,
    each opening within prior body and closing higher.
    Strong bullish continuation / reversal.
    """
    if len(candles) < 3:
        return None
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]

    if (is_bullish(c1) and is_bullish(c2) and is_bullish(c3)
            and c2["close"] > c1["close"] and c3["close"] > c2["close"]
            and c2["open"] > c1["open"] and c3["open"] > c2["open"]
            and body_pct(c1) > 40 and body_pct(c2) > 40 and body_pct(c3) > 40):
        return {"pattern": "THREE_WHITE_SOLDIERS", "signal": "BULLISH", "strength": 3}
    return None


def detect_three_black_crows(candles: list[dict]) -> dict | None:
    """
    Three Black Crows: 3 consecutive bearish candles,
    each opening within prior body and closing lower.
    Strong bearish continuation / reversal.
    """
    if len(candles) < 3:
        return None
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]

    if (is_bearish(c1) and is_bearish(c2) and is_bearish(c3)
            and c2["close"] < c1["close"] and c3["close"] < c2["close"]
            and c2["open"] < c1["open"] and c3["open"] < c2["open"]
            and body_pct(c1) > 40 and body_pct(c2) > 40 and body_pct(c3) > 40):
        return {"pattern": "THREE_BLACK_CROWS", "signal": "BEARISH", "strength": 3}
    return None


def detect_bullish_harami(candles: list[dict]) -> dict | None:
    """
    Bullish Harami: 2-candle pattern.
    Prev = large bearish, curr = small bullish contained within prev body.
    """
    if len(candles) < 2:
        return None
    prev, curr = candles[-2], candles[-1]

    if (is_bearish(prev) and is_bullish(curr)
            and body_pct(prev) > 50
            and curr["open"] > prev["close"] and curr["close"] < prev["open"]
            and body(curr) < body(prev) * 0.5):
        return {"pattern": "BULLISH_HARAMI", "signal": "BULLISH", "strength": 1}
    return None


def detect_bearish_harami(candles: list[dict]) -> dict | None:
    """
    Bearish Harami: 2-candle pattern.
    Prev = large bullish, curr = small bearish contained within prev body.
    """
    if len(candles) < 2:
        return None
    prev, curr = candles[-2], candles[-1]

    if (is_bullish(prev) and is_bearish(curr)
            and body_pct(prev) > 50
            and curr["open"] < prev["close"] and curr["close"] > prev["open"]
            and body(curr) < body(prev) * 0.5):
        return {"pattern": "BEARISH_HARAMI", "signal": "BEARISH", "strength": 1}
    return None


# ================================================================
# TREND HELPERS
# ================================================================

def _is_downtrend(candles: list[dict]) -> bool:
    """At least 60% of the candles have lower closes than the prior candle.
    Used to validate reversal patterns — a Hammer is only bullish
    if it appears after a decline. Uses closing prices (not open/close
    direction) because closing price reflects actual settlement."""
    if len(candles) < 2:
        return False
    closes = [c["close"] for c in candles]
    down_count = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
    return down_count >= len(closes) * 0.6


def _is_uptrend(candles: list[dict]) -> bool:
    """At least 60% of the candles have higher closes than the prior candle.
    Used to validate reversal patterns — a Shooting Star is only bearish
    if it appears after a rally."""
    if len(candles) < 2:
        return False
    closes = [c["close"] for c in candles]
    up_count = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    return up_count >= len(closes) * 0.6


# ================================================================
# MASTER DETECTOR
# ================================================================

ALL_SINGLE = [detect_doji, detect_marubozu]
ALL_CONTEXT = [
    detect_hammer, detect_inverted_hammer,
    detect_shooting_star, detect_hanging_man,
    detect_bullish_engulfing, detect_bearish_engulfing,
    detect_morning_star, detect_evening_star,
    detect_three_white_soldiers, detect_three_black_crows,
    detect_bullish_harami, detect_bearish_harami,
]


def detect_all(candles: list[dict]) -> list[dict]:
    """
    Runs all pattern detectors on the given candle list.
    Returns list of detected pattern dicts, sorted by strength (highest first).

    Example return:
      [{"pattern": "BULLISH_ENGULFING", "signal": "BULLISH", "strength": 3},
       {"pattern": "HAMMER", "signal": "BULLISH", "strength": 2}]
    """
    if not candles:
        return []

    found = []

    # Single-candle patterns (only need the last candle)
    for fn in ALL_SINGLE:
        result = fn(candles[-1])
        if result:
            found.append(result)

    # Multi-candle / context-aware patterns
    for fn in ALL_CONTEXT:
        result = fn(candles)
        if result:
            found.append(result)

    found.sort(key=lambda x: x["strength"], reverse=True)
    return found


def summarise_signals(patterns: list[dict]) -> dict:
    """
    Summarises a list of detected patterns into a net signal.
    Bullish patterns add their strength, bearish patterns subtract.

    Returns:
      {
        "net_signal": "BULLISH" | "BEARISH" | "NEUTRAL",
        "score": float,         # positive = bullish, negative = bearish
        "patterns": list[str],  # pattern names found
        "strongest": dict | None,  # highest-strength pattern
      }

    Score magnitude depends on how many patterns are detected and
    their strengths. Typical range is -6 to +6 (multiple strong
    patterns stacking). This is combined with the technical
    indicator score in stock_scanner_v2 for final ranking.
    """
    if not patterns:
        return {"net_signal": "NEUTRAL", "score": 0, "patterns": [], "strongest": None}

    score = 0
    names = []
    for p in patterns:
        names.append(p["pattern"])
        if p["signal"] == "BULLISH":
            score += p["strength"]
        elif p["signal"] == "BEARISH":
            score -= p["strength"]

    if score > 0:
        net = "BULLISH"
    elif score < 0:
        net = "BEARISH"
    else:
        net = "NEUTRAL"

    return {
        "net_signal": net,
        "score": score,
        "patterns": names,
        "strongest": patterns[0] if patterns else None,
    }
