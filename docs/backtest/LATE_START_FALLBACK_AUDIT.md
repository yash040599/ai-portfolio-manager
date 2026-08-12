# Late-Start Fallback Audit

## Decision

The recommended default is to open no new positions when Gap-and-Go starts
after its 10:15 cutoff. Neither tested late-start candidate produced stable
positive expectancy net of costs. An operator may explicitly override that
recommendation and run the legacy blended-score strategy, the best
runtime-compatible fallback currently implemented, after seeing its OOS PF
0.82 warning.

## Previous-Day Breakout

The existing previous-day high/low breakout was replayed with its earliest
entry moved from 10:00 to 10:15:

```powershell
.\.venv\Scripts\python.exe scripts\trade\backtest_prev_day_breakout.py --entry-start 10:15
```

| Window / route | Trades | PF | Expectancy | Return | Max DD |
|---|---:|---:|---:|---:|---:|
| TRAIN / all | 497 | 0.93 | -0.030% | -14.94% | 32.75% |
| TEST / all | 472 | 0.85 | -0.057% | -26.68% | 32.09% |
| TRAIN / volatile only | 228 | 0.96 | -0.022% | -5.06% | 29.01% |
| TEST / volatile only | 98 | 0.82 | -0.088% | -8.67% | 15.12% |

The apparent volatile-only edge at the original 10:00 start (TEST PF 1.18)
does not survive the actual fallback boundary. Regime labels in this research
harness also use full-sample thresholds, so they are not a production-safe
gate.

## Delayed Gap-and-Go v1.2.0

The frozen NIFTY100 adaptive-volume signal was then replayed with opening gap
and volume qualification unchanged while execution, RSI, gap-hold, trend
contradiction, stop, and target calculations moved to later candles:

```powershell
.\.venv\Scripts\python.exe scripts\trade\backtest_broad_gap_vol.py --late-entry-sweep
```

No regime filter was used because the research regime requires morning data
through 10:45 and is unavailable at the earliest fallback decision.

| Entry candle | TRAIN trades | TRAIN PF | TEST trades | TEST PF |
|---|---:|---:|---:|---:|
| 09:30 baseline | 194 | 0.76 | 179 | 1.30 |
| 10:15 | 120 | 0.54 | 127 | 1.02 |
| 10:30 | 103 | 1.47 | 107 | 0.74 |
| 10:45 | 96 | 1.08 | 110 | 0.62 |
| 11:00 | 94 | 1.00 | 99 | 0.58 |

No delayed entry clears the PF 1.15 promotion gate in both TRAIN and TEST.
The 10:30 result reverses from TRAIN PF 1.47 to TEST PF 0.74, which rejects it
as an unstable fallback rather than supporting further tuning.

## Runtime Policy

`PortfolioManager` offers the same explicit choice when the tool starts after
the 10:15 cutoff or a completed Gap-and-Go scan finds no candidates:

1. Run `NOAI_LEGACY_FULL` for the rest of the session.
2. Stop and open no new trades (default and recommended).

The prompt states OOS PF 0.82, full combined PF 0.86, and that PF below 1.00
lost money after costs. Blank input, invalid input, EOF, and Ctrl+C all choose
stop. Data-fetch or scan failures remain fail-closed and do not offer a
fallback. On explicit approval, the fallback restores its tested 14:00
square-off and 13:00 loser exit before rescanning current market data.