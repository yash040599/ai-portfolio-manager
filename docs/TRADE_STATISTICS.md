# Trading Statistics

Last updated: 2026-06-08 (Gap-and-Go activated with RSI filter; config switched from legacy).

## 0. Current Verdict

| Area | Current Read |
|---|---|
| Runtime strategy version | 2.3-2026-06-08-GAP_AND_GO_RSI |
| Active stage | **GAP_AND_GO_DRY_RUN** — Phase 7 Gap-and-Go with RSI BUY ceiling. OOS PF 1.37. Config switched to `NOAI_GAP_AND_GO`. |
| Strategy profile | `NOAI_GAP_AND_GO` (active in config.py) |
| AI mode | Not used — Gap-and-Go is pure rules-based (NoAI) |
| Run command | python main.py --mode trade --dryrun |
| Budget | Rs.50,000 |
| Daily trade cap | 2 trades max (GAP_GO_DAILY_CAP=2; sweep: PF drops with 3+) |
| Square-off | 13:00 IST (GAP_GO_SQUARE_OFF_HOUR=13; sweep: optimal) |
| Loser exit | 12:00 IST (auto: sq-off − 1hr) |
| Trailing stop | DISABLED (sweep: every trail config destroys PF) |
| RSI BUY ceiling | 70.0 (block overbought gap-up buys; PF 1.28→1.37, MaxDD -28%) |
| Worst-case daily loss | ~Rs.933 (2 trades x 2.5% SL), hard circuit breaker at Rs.1,500 |
| Note | 2026-06-08 dry run accidentally used NOAI_LEGACY_FULL (old scores). Config now fixed. |

## 1. Backtest Results (62-Gate Audit, 2026-05-26)

| Metric | Before Audit | After Audit | Change |
|---|---:|---:|---|
| Profit Factor | 0.71 | 0.86 | +21% |
| Trades (annual) | ~18,750 | 970 | -95% (K1=2 cap) |
| Win Rate | 40.5% | 37.8% | Fewer but higher-quality |
| Sharpe | -1.89 | -1.22 | +35% |
| H2 2024 PF | 0.82 | 1.02 | Profitable half |

Key optimizations applied:
- **ATR 2.0** (was 1.5): wider SL reduces whipsaw churn
- **RR 1.8** (was 1.5): higher reward-to-risk improves expectancy
- **K1=2** daily trade cap: eliminates overtrading, PF 0.71 -> 0.81
- **14:00 square-off** (was 15:10): avoids toxic closing volatility
- **13:00 loser exit**: cuts dead weight 1 hour before close
- **RSI gates disabled**: all 4 RSI gates hurt PF in backtest
- **VWAP gates disabled**: trend-fight and extension gates removed profitable trades
- **Signal reversal exit enabled**: cuts losses on thesis invalidation

## 2. Active Configuration

### Enabled Gates
| Gate | Setting | Evidence |
|---|---|---|
| Exchange SL-M | Always on | Instant stop-loss execution on NSE |
| ATR-based SL/target | ATR 2.0 x 14-period 15min | Backtest E1: best per-trade expectancy |
| R:R floor | 1.3:1 uniform | Backtest E1: practical optimum |
| Daily trade cap | 2 trades/day | Backtest K1: PF 0.81 vs 0.71 baseline |
| Signal reversal exit | score >= 7 + pattern | Pro decision: institutional best practice |
| Consecutive SL pause | 3 losses -> 30 min pause | Pro decision: prop-firm standard |
| Circuit breaker | 3% of budget | Always-on safety net |
| LIMIT orders | LTP with 8s timeout, 2 retries | Reduces slippage vs MARKET orders |

### Disabled Gates (by backtest evidence)
| Gate | Reason |
|---|---|
| RSI buy/sell ceilings | G1/G2: inert or harmful (PF worse at every level) |
| RSI buy/sell floors | G3/G4: G4 was biggest hidden PF killer (-10%) |
| VWAP trend-fight | G6: inert, removes <3% of trades |
| VWAP extension block | G7: all values make PF worse |
| All other optional gates | Disabled pending future backtest evidence |

## 3. Break-Even Constraint

At 1.8:1 R:R target with 2.0x ATR SL:

    Break-even WR (before charges) = 1 / (1 + 1.8) = 35.7%
    Break-even WR (after charges)  ~ 42-45%

The backtest shows 37.8% WR - below after-cost break-even but above raw break-even.
The AI quality gate (Gemini picking 2 from 50) is expected to push WR above the
after-cost threshold by filtering out marginal setups that the rule-based scorer passes.

## 4. FY 2026-27 Historical Record

From tax ledger (pre-audit, NoAI mode):

| Metric | Value |
|---|---:|
| Total trades | 184 |
| Net P&L | Rs.-3,929 |
| Charges | Rs.2,591 |

This is the old NoAI baseline. Post-audit AI-mode results will be tracked separately.

## 5. Promotion Metrics

Evidence starts from the first AI-mode live session:

| Metric | Target |
|---|---:|
| Profit factor | >= 1.15 after costs |
| Expectancy | >= Rs.10/trade |
| Trade win rate | >= 40% |
| Profitable-day rate | >= 55% |
| Max drawdown | <= 3% of daily capital |
| Sample size | >= 10 sessions, >= 20 trades |

Capital scaling (Rs.50K -> Rs.1L) requires passing all promotion metrics.

## 6. Update Protocol

After each live trading session:
1. Check daily report in 
eports/trading/
2. Run scripts/trade/promotion_check.py --window 20 when >= 20 trades accumulated
3. Update this doc with latest metrics
4. Capital scaling decision only after promotion metrics pass
