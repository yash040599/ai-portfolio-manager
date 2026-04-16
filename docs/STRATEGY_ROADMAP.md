# Strategy Roadmap

Research-backed improvements for the V2 intraday trading bot. Sources: Investopedia, Zerodha Varsity, Toby Crabel (ORB), institutional intraday practices, and real trade data analysis (80+ live trades, April 2026).

---

## Status Overview

### Pending (3 items)

| # | Improvement | Priority | Impact | Effort |
|---|------------|----------|--------|--------|
| 24 | Backtesting framework — replay V2 scoring on historical data | LOW | Highest | High |
| 41 | Holiday-shifted expiry detection — Wed instead of Thu, ~3 days/year | LOW | Low | Low |
| 44 | WebSocket tick data — real-time SL/target vs 10s polling | MEDIUM | High | High |

### Removed (8 items — not worth implementing)

| # | Item | Reason |
|---|------|--------|
| 37 | Correlation-based sizing | Redundant — sector cap + direction diversification already prevent correlated drawdowns |
| 39 | ATR percentile ranking | ATR already capped at MAX_INTRADAY_SL_PCT (2.5%). Percentile adds marginal value |
| 40 | Claude prompt feedback loop | AI mode not default. NoAI doesn't use Claude |
| 45 | Multi-day score trend | Score momentum (#36) handles intraday RoC. Multi-day adds complexity for marginal intraday gain |
| 46 | Smart square-off timing | Late-day loser exit (#27) already exits losers early |
| 47 | Budget auto-scaling by win rate | Loss-adjusted sizing (#15) + dynamic sizing (#58) already cover this |
| 57 | VWAP exclude incomplete candle | Negligible impact on cumulative VWAP. VWAP SD bands smooth noise |
| 89 | Increase circuit breaker to 4% | Config change, not a feature. Edit `MAX_LOSS_PER_DAY_PCT` in config.py |

### Completed (114 items)

| # | Improvement | Category |
|---|------------|----------|
| **Indicators (20)** | | |
| 1 | Volume confirmation for candle patterns | Indicators |
| 2 | Relative Volume (RVol) as stock filter | Indicators |
| 3 | Pattern freshness decay (1.0/0.7/0.4×) | Indicators |
| 4 | Previous day H/L/C support/resistance | Indicators |
| 6 | Opening Range Breakout (ORB, 2nd candle) | Indicators |
| 7 | MACD histogram confirmation | Indicators |
| 9 | Pre-market gap analysis | Indicators |
| 17 | Multi-timeframe alignment (hourly EMA) | Indicators |
| 18 | Bollinger Band squeeze detection | Indicators |
| 28 | ADX trend strength filter | Indicators |
| 30 | 3-day candle lookback (MACD/BB warmup) | Indicators |
| 31 | Today-candle-count guard for early scans | Indicators |
| 33 | Fibonacci retracement levels (directional) | Indicators |
| 34 | VWAP standard deviation bands (±1σ, ±2σ) | Indicators |
| 36 | Score momentum — RoC + deceleration penalty | Indicators |
| 51 | Extended move penalty (directional, >2% = -3) | Indicators |
| 52 | RSI extreme hard cap (≥75 caps at +3) | Indicators |
| 61 | SuperTrend configurable (7, 2.0 for intraday) | Indicators |
| 94 | StochRSI for entry timing (info-only) | Indicators |
| 95 | Sector momentum filter (±0.5 boost) | Indicators |
| **Risk Management (14)** | | |
| 5 | NIFTY trend hard filter (against-trend needs ≥3) | Risk |
| 8 | Sector diversification (max 2/sector) | Risk |
| 14 | Stagnant position exit (NoAI, 45 min) | Risk |
| 15 | Loss-adjusted position sizing | Risk |
| 16 | Circuit breaker cooldown (30 min resume, max 2 trips) | Risk |
| 19 | Max circuit breaker trips per day | Risk |
| 20 | Consecutive SL pause / whipsaw guard (3 SLs → 30 min) | Risk |
| 21 | Dynamic score threshold after losses (NoAI) | Risk |
| 22 | Regime-shift SL tightening | Risk |
| 26 | Sector cap enforcement at entry time | Risk |
| 27 | Late-day loser exit (2:45 PM) | Risk |
| 53 | Direction diversification (score-aware, gap ≥3 = all slots) | Risk |
| 64 | Short position cutoff (1 PM) | Risk |
| 65 | Pre-trade minimum profit check (Rs.50) | Risk |
| **Execution (28)** | | |
| 10 | Partial profit taking (1/3 at 1.5R, trail 50%) | Execution |
| 11 | Periodic opportunity scanning (30 min, free slots) | Execution |
| 13 | Minimum capital deployment guidance | Execution |
| 32 | Late-entry target reduction (1 PM: -20%, 2 PM: -25%) | Execution |
| 35 | Bid-ask spread check (max 0.3%) | Execution |
| 49 | ATR-only SL/target (pure ATR, no merge) | Execution |
| 50 | Late-entry + time-decay mutual exclusion | Execution |
| 54 | Fewer trades, bigger size (MAX_POSITIONS 5→3) | Execution |
| 55 | LIMIT orders for entry (with MARKET fallback) | Execution |
| 56 | Scan universe price filter (Rs.100 min) | Execution |
| 58 | Dynamic position sizing by budget | Execution |
| 59 | R:R 1.5:1 configurable | Execution |
| 60 | Exchange SL-M orders (instant SL on NSE) | Execution |
| 66 | Entry delay 15→5 min | Execution |
| 67 | Trail step 65→50% | Execution |
| 68 | Time-decay 40→25% | Execution |
| 73 | Fallback candidate pool (all pre-filtered) | Execution |
| 74 | Periodic manual trade sync (every 15 min) | Execution |
| 82 | Stagnant exit 90→45 min | Execution |
| 90 | Default target 1.5→1.2% | Execution |
| 91 | Claude review time 20→30 min (AI only) | Execution |
| 92 | R:R floor (time-based + adaptive + mid-day retry) | Execution |
| 93 | Volume confirmation at entry (RVol ≥ 0.7x) | Execution |
| 96 | Claude: rank/veto role + StochRSI confluence (AI only) | Execution |
| 97 | Feed 15-min re-scan data to Claude review (AI only) | Execution |
| 103 | Candidate pool: return all pre-filtered | Execution |
| 104 | Fallback promotion on budget drop | Execution |
| 107 | Score-weighted position sizing (simplified Kelly) | Execution |
| **Market Intelligence (6)** | | |
| 12 | Continuous NIFTY regime monitoring (every 15 min) | Market Intel |
| 23 | India VIX volatility regime detection | Market Intel |
| 29 | Thursday F&O expiry-day handling (+0.3 ATR, -1 pos, +0.5 score) | Market Intel |
| 42 | Pre-open auction data (9:08 gap detection) | Market Intel |
| 76 | Smart direction diversification (score-aware) | Market Intel |
| 78 | FII/DII flow bias (pre-market intelligence) | Market Intel |
| **Infrastructure (8)** | | |
| 25 | Trade journaling + performance analytics | Infra |
| 38 | Improved slippage model for dry run | Infra |
| 43 | Real-time trade verification script | Infra |
| 75 | --max budget CLI flag | Infra |
| 79 | Per-trade charge calculation (tax ledger) | Infra |
| 80 | EXTERNAL position unique order_id | Infra |
| 81 | Sheet import updates charges on P&L match | Infra |
| 110–111 | SQLite WAL mode + trades dedup constraint | Infra |
| **Bug Fixes (20)** | | |
| 69 | SL sanity check after entry price override | Bug Fix |
| 70 | SL-M partial fill verification | Bug Fix |
| 71 | Fill price SL cap re-validation | Bug Fix |
| 72 | Store initial_sl at entry time | Bug Fix |
| 77 | Entry count logging fix | Bug Fix |
| 83–88 | SL-M error handling, pending ID tracking, exception safety, directional validation, reconcile API handling, quote fetch handling | Bug Fix |
| 98 | Extended move penalty direction fix | Bug Fix |
| 99 | Morning/Evening Star gap validation | Bug Fix |
| 100 | Three White Soldiers/Crows body check (Nison) | Bug Fix |
| 101 | Observation filter bypass log level | Bug Fix |
| 102 | ATR pure mode (remove SL merge) | Bug Fix |
| 105–106 | Late-entry R:R floor + tier 2 reduction 35→25% | Bug Fix |
| 108 | Direction filter fallback fix | Bug Fix |
| 109 | R:R mid-day retry guard (morning excluded) | Bug Fix |
| 112 | Dynamic tick size for LIMIT orders (was hardcoded 0.05) | Bug Fix |
| 113 | Defensive tick rounding in place_order() | Bug Fix |
| 114 | fill_intraday_ledger skip dates with ZV_ rows (prevent VM/local dup) | Bug Fix |
| **Strategy Improvements (5)** | | |
| 115 | RSI contradiction filter (no SELL RSI>70, no BUY RSI<30) | Risk |
| 116 | Declining re-entry block (score delta < 0 → skip) | Risk |
| 117 | Post-1pm SELL slot → BUY reallocation (score ≥ 4.0 guard) | Execution |
| 118 | Expiry-day stagnant timer extension (+15 min) | Execution |
| 119 | Expiry score bump raised 0.5 → 1.0 | Risk |
| **Observability (2)** | | |
| 120 | Next scan timestamps in monitor logs (candle + opportunity) | Infra |
| 121 | round_to_tick made public API, Kite avg_volume gap documented | Infra |
| **Strategy Gap Fixes (8)** | | |
| 122 | Expiry entry delay (15 min vs 5 min normal) | Risk |
| 123 | Expiry max trades cap (5/day) | Risk |
| 124 | Daily trade cap (12/day) to prevent churn | Risk |
| 125 | VWAP trend block (no BUY below VWAP, no SELL above VWAP) | Risk |
| 126 | Midday lull stagnant timer extension (12:00-1:30 +15 min) | Execution |
| 127 | MIN_EXPECTED_PROFIT raised 50 → 75 (2× charges) | Execution |
| 128 | Stagnant churn guard (no re-enter stagnant exits same direction) | Risk |
| 129 | Net-of-charges R:R check (effective R:R ≥ 1.0:1 after costs) | Risk |

---

## Pending — Details

### 24. Backtesting Framework
- **Priority**: LOW (deferred — use live trade analytics first)
- **Gap**: No way to measure which indicators actually contribute to winning trades.
- **Fix**: Replay V2 scoring on historical 15-min data, simulate ATR-based entries/exits, compute win rate per indicator combination.
- **Source**: Every professional quant desk backtests before going live.
- **Note**: We have 80+ live trades with full indicator snapshots in SQLite. Use `python scripts/view_performance.py --summary` to identify patterns before building a full framework.

### 41. Holiday-Shifted Expiry Detection
- **Priority**: LOW (~3 days/year edge case)
- **Gap**: Thursday expiry detection uses `weekday == 3`. When Thursday is an NSE holiday, expiry shifts to Wednesday.
- **Fix**: Maintain a list of actual expiry dates from NSE published calendar alongside the holiday list.

### 44. WebSocket Tick Data
- **Priority**: MEDIUM (implement when polling latency causes measurable slippage)
- **Gap**: 10-second polling can miss rapid SL/target breaches during news events.
- **Fix**: Use Zerodha WebSocket (up to 3000 instruments) for real-time tick data on open position symbols. SL/target checks on every tick.
- **Note**: Exchange SL-M orders (#60) already handle instant SL execution. WebSocket mainly improves target hits and trailing SL responsiveness.

