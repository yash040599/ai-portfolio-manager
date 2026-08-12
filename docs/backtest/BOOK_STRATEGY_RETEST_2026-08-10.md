# India-Focused Book Strategy Retest

Date: 2026-08-10  
Status: complete; no strategy promoted

## Question

Do the concrete intraday configurations publicly attributed to Indrazith
Shantharaj add an after-cost edge beyond the strategy families already tested in
this repository?

## Scope and Method

- Universe: NIFTY100 cash equities with locally cached 15-minute candles.
- TRAIN: 2024-05-27 through 2025-05-31.
- TEST/OOS: 2025-06-01 through 2026-05-22.
- Portfolio construction: at most two selected trades per day.
- Evaluation: net of the repository's canonical Indian/Zerodha cost model.
- Promotion gate: OOS profit factor (PF) at least 1.15, with credible forward
  evidence still required before deployment.
- Reproduction: `python scripts/trade/backtest_book_orb.py`.

This is a portability test, not a reproduction of the author's reported Bank
Nifty futures tests. Instrument, execution, leverage, sample period, and costs
differ. A failed NIFTY100 result rejects deployment in this project; it does not
prove that every possible implementation on Bank Nifty futures fails.

## Existing Coverage

The broad strategy families were already represented before this retest:

| Book idea | Existing repository test | Why another test was justified |
|---|---|---|
| First-hour range breakout | `backtest_fhrb.py` | Existing version uses pre-breakout volume, opposite-range stop, RR target, and earlier square-off. |
| First-candle momentum | `backtest_ocm.py` | Existing version uses body/volume filters and a structural stop, not the book's RSI rule. |
| EMA trend entry | `backtest_ema_pullback.py` | Existing version is a 9/21 pullback with MACD, StochRSI, ADX, and ATR exits, not a 6/60 crossover. |

The separate harness therefore tested only the materially different rule sets,
rather than relabelling prior family-level evidence as an exact reproduction.

## Configurations Retested

1. One-hour opening-range breakout: breakout candle must close beyond the first
   hour's range; enter beyond that candle; fixed 1% stop; end-of-day exit. The
   directional wick filter was tested at none, at most 20%, and at most 10%.
2. First-candle breakout: enter beyond the first 15-minute candle when RSI
   confirms direction; fixed 0.5% stop; end-of-day exit. Thresholds were tested
   at 60/40, 55/35, and 55/45.
3. EMA 6/60 crossover: enter on the crossover; fixed 0.5% stop; end-of-day exit.

## Results

### TRAIN

| Configuration | Trades | WR | PF | Expectancy/trade | MaxDD | Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| ORB, no wick filter | 502 | 40.8% | 0.72 | -0.126% | 63.25% | -2.88 |
| ORB, wick <=20% | 502 | 41.4% | 0.75 | -0.114% | 57.59% | -2.62 |
| ORB, wick <=10% | 502 | 41.6% | 0.78 | -0.099% | 58.35% | -2.09 |
| First candle, RSI 60/40 | 502 | 29.7% | 0.94 | -0.027% | 30.54% | -0.53 |
| First candle, RSI 55/35 | 502 | 29.7% | 0.94 | -0.027% | 30.54% | -0.53 |
| First candle, RSI 55/45 | 502 | 29.7% | 0.94 | -0.027% | 30.54% | -0.53 |
| EMA 6/60 crossover | 500 | 29.0% | 0.80 | -0.082% | 42.00% | -2.03 |

### TEST/OOS

| Configuration | Trades | WR | PF | Expectancy/trade | MaxDD | Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| ORB, no wick filter | 477 | 41.5% | 0.65 | -0.157% | 82.41% | -4.02 |
| ORB, wick <=20% | 476 | 41.8% | 0.66 | -0.148% | 79.35% | -3.77 |
| ORB, wick <=10% | 475 | 42.7% | 0.70 | -0.127% | 75.66% | -3.20 |
| First candle, RSI 60/40 | 477 | 30.4% | 0.98 | -0.006% | 34.80% | -0.13 |
| First candle, RSI 55/35 | 478 | 30.3% | 0.98 | -0.007% | 34.80% | -0.15 |
| First candle, RSI 55/45 | 478 | 30.3% | 0.98 | -0.007% | 34.80% | -0.15 |
| EMA 6/60 crossover | 476 | 27.7% | 0.68 | -0.130% | 62.07% | -3.28 |

## Interpretation

- The directional wick filter improves ORB monotonically, which is consistent
  with the book's candle-quality intuition, but PF 0.70 remains decisively
  negative after costs.
- First-candle RSI is closest to breakeven, but no nearby threshold reaches PF
  1.0 in TRAIN or TEST, much less the 1.15 gate.
- The RSI variants select effectively the same top opportunities because the
  daily cap is saturated by earlier qualifying signals. This explains the flat
  sensitivity result and argues against a wider threshold optimization sweep.
- EMA 6/60 fails in both windows and exhibits severe drawdown.
- Gap-and-Go v1.2.0 remains the strongest tested intraday strategy in the
  repository at OOS PF 1.30, Sharpe 1.29, and MaxDD 11.58%.

## India-Specific Reading Value

Indian-market books and Zerodha Varsity remain useful for market conventions,
execution constraints, volume interpretation, key levels, and risk discipline.
The most concrete additional Varsity rules found in this review were already
covered by existing research families: relative volume versus a 10-period
average, price-action confirmation, and short-period EMA crossover systems.

CPR is a separately testable idea: use prior-period OHLC, treat price above TC
as bullish and below BC as bearish, and prefer pullbacks to TC/BC over chasing.
It should enter the normal research queue only if a complete entry, stop, exit,
ranking, and cost specification is frozen first. It is not evidence that any of
the book configurations above passed.

## Verdict

Do not promote, tune, or integrate any tested book configuration. Do not change
the active Gap-and-Go profile. The book contributes useful process and candidate
ideas, but the exact missing configurations add no robust after-cost OOS edge on
the project's tradable NIFTY100 surface.

The separate capital conclusion is unchanged: even the passing Gap-and-Go
strategy should not be run at Rs.50,000 while its expected annual net P&L is
about Rs.3,621 against the Rs.6,000 annual Kite subscription.

## Public References

- Indrazith Shantharaj, *How to Make Money in Intraday Trading* (public retailer
  metadata and publicly visible review descriptions were used to identify the
  parameterized rules; no unauthorized copy was used).
- Zerodha Varsity, "Moving Averages":
  https://zerodha.com/varsity/chapter/moving-averages/
- Zerodha Varsity, "Volumes":
  https://zerodha.com/varsity/chapter/volumes/
- Zerodha Varsity, "The Central Pivot Range":
  https://zerodha.com/varsity/chapter/the-central-pivot-range/