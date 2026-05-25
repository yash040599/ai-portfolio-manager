# Backtest Results: Baseline (No Gates)

**Date run**: 2026-05-25
**Gate audit doc**: [TRADE_GATE_AUDIT.md](../TRADE_GATE_AUDIT.md)

---

## What This Tests

The baseline runs the simplified scoring engine (EMA crossover +
RSI + momentum + SuperTrend proxy) on all NIFTY 50 stocks using
2 years of 15-min candle data. No entry filters, no exit gates,
no risk limits — just raw signal + ATR-based SL/target + EOD
square-off.

This establishes the "floor" that every gate must improve upon.

### Parameters

| Parameter | Value |
|-----------|-------|
| Scoring | EMA(9/21) cross + RSI(14) + 1h momentum + SuperTrend proxy |
| MIN_SCORE | 2.0 |
| ATR_MULTIPLIER | 1.5 |
| RR_TARGET_RATIO | 1.5 |
| RR_FLOOR | 0 (disabled) |
| Entry window | 10:00 - 14:30 IST |
| Square-off | 15:10 IST |
| Universe | NIFTY 50 (50 stocks) |
| Period | May 2024 - May 2026 (2 years) |
| Capital | Rs.50,000 |

---

## Backtest Results

### Without Costs (raw signal quality)

| Metric | Value |
|--------|-------|
| Total trades | 37,777 |
| Win rate | 46.8% |
| **Profit factor** | **1.05** |
| Expectancy | +0.013% per trade |
| Total return | +493.87% |
| CAGR | +145.96% |
| Max drawdown | 157.28% |
| Sharpe ratio | +2.68 |

### With NSE Intraday Costs

| Metric | Value |
|--------|-------|
| Total trades | 37,777 |
| Win rate | 40.5% |
| **Profit factor** | **0.71** |
| Expectancy | -0.094% per trade |
| Total return | -3,553.58% |
| CAGR | -100.00% |
| Max drawdown | 3,624.15% |
| Sharpe ratio | -19.26 |

---

## Conclusion

**The raw signal has a tiny edge (PF 1.05) that is completely
destroyed by trading costs.**

37,777 trades in 2 years = ~75 trades/day across 50 stocks. Each
trade has ~0.07% round-trip cost (STT + brokerage + GST + exchange
charges). The 0.013% per-trade edge cannot survive this.

### Implications for Gate Optimization

1. **Trade frequency must drop dramatically** — from ~75/day to
   ~3-5/day. Every gate that reduces low-quality trades improves
   the cost ratio.

2. **MIN_SCORE must be higher** — the current 2.0 lets in too many
   weak signals. Need at least 5.0+ for after-cost viability.

3. **Cost-aware gates (E4, E5) are critical** — trades where
   expected profit doesn't cover charges must be rejected.

4. **The scoring function itself may need improvement** — PF 1.05
   is a very thin edge. The real scanner uses 28 indicators vs
   our simplified 4-indicator proxy.

---

*Raw data: `reports/backtest/gate_test_baseline.json`*

---

## Code Review

**Status: FUNCTIONAL — this is what the trade mode runs today**

The baseline scoring (EMA cross + RSI + momentum) is a simplified
proxy of the real scanner's 28-indicator pipeline. The actual
trade mode code in `modes/trade/stock_scanner.py::analyse_candle_snapshot()`
computes all 14 candle patterns + 14 technical indicators for a
richer composite score.

| Component | Backtester | Actual Trade Mode |
|-----------|-----------|-------------------|
| EMA crossover | EMA(9/21) | EMA(9/21) |
| RSI | RSI(14) | RSI(14) |
| Momentum | 1h close-to-close | 1h close-to-close |
| SuperTrend | Simplified proxy | SuperTrend(7, 2.0) |
| Candle patterns | NOT included | 14 patterns (hammer, engulfing, etc.) |
| MACD | NOT included | MACD(12,26,9) |
| Bollinger Bands | NOT included | BB(20,2) |
| StochRSI | NOT included | StochRSI(14,14,3,3) |
| ORB/Gap/Hourly EMA | NOT included | Included |
| VWAP bands | Simplified | VWAP + 1/2 sigma bands |

**Note**: Backtest results understate the real scanner's signal
quality because the backtester uses ~4 indicators vs the scanner's
28. Gate verdicts based on this baseline are conservative.
