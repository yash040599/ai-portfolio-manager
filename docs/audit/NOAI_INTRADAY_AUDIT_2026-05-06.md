# NoAI Intraday Audit - 2026-05-06 - GPT 5.5 XHigh

Scope: phase-2 intraday trading only. No AI-mode recommendations, no
portfolio-analysis phase, no dashboard/tax product review except where a
script feeds intraday decisions or post-trade learning.

This was a fresh code-and-data audit. Subagents reviewed scoring, entry
gates, monitor/exits, and docs/scripts separately; all serious claims below
were re-checked against current code or the local trade database.

## Executive View

The live problem is real: `intraday_tax_ledger` shows 25 trading days, only
7 profitable, total net Rs.-2,211.32. The last 10 trading days are 1/10
profitable with net Rs.-2,085.44. The recent failure window is concentrated:

| Window | Ledger rows | Winning rows | Net |
|---|---:|---:|---:|
| 2026-04-22 to 2026-05-05 | 54 | 10 | Rs.-2,141.11 |
| 2026-04-27 to 2026-05-06 | 41 | 7 | Rs.-1,451.17 |

The scoring system is not uniformly broken across all history, but it broke
badly in the recent regime. For scored trades from 2026-04-22 to 2026-05-05:

| Abs entry score | Trades | Win rate | PnL |
|---|---:|---:|---:|
| <5 | 6 | 50.0% | Rs.+241.41 |
| 5-6 | 3 | 33.3% | Rs.+19.29 |
| 6-7 | 11 | 9.1% | Rs.-472.13 |
| 7-8 | 9 | 33.3% | Rs.-319.85 |
| 8-9 | 7 | 14.3% | Rs.-374.57 |
| 9+ | 8 | 12.5% | Rs.-475.42 |

The recent scores are therefore anti-correlated with profitability. That is
not a small threshold issue; it means the score is overweighting the wrong
morning patterns for the current regime.

## Changes Made In This Pass

These are low-risk safety/code-quality fixes already applied:

| Area | Change | Why |
|---|---|---|
| Live quote validation | `enter_trade()` now tries Zerodha quote reads 3 times, then skips live entries when `last_price` is still invalid | No trade should enter on stale candle math when the live quote cannot be recovered |
| Spread/depth | Spread and impact-cost gates now try 3 times for missing/malformed depth, then fail closed | Missing depth after 3 attempts means liquidity is unknown, not safe |
| Net R:R charges | Charge estimate now uses planned entry and planned target turnover, side-aware | Previous code charged `entry * qty` on both legs, understating costs |
| Scanner price filter | Symbols with missing live LTP get 3 quote attempts and are skipped only if still missing | Prevents stale candle-only candidates from reaching entry while tolerating flaky quote reads |
| Comment bloat | Largest duplicated exit-gate docstrings were shortened | Detailed rationale already lives in strategy docs |
| Dead duplicate | Removed duplicate `_strong_gap_day` assignment | Small cleanup, no behavior change |

## Verified False Alarms From Subagents

The following severe-looking claims were not true in current code:

| Claim | Verdict |
|---|---|
| Opposing-thin gate exists but is not called | False. `enter_trade()` calls `is_opposing_thin_capped(side)` immediately after directional pause. |
| `LATE_ENTRY_NO_RESCUE_FLOOR_ENABLED` is currently enabled | False. Current config sets it to `False` with the post-audit rationale. |
| Empty Zerodha positions response continues into close detection | False. `sync_external_positions()` returns immediately when `net=[]` while bot tracks open positions. |
| Gap-coherence is disabled by default | False. Current config sets `GAP_COHERENCE_GATE_ENABLED = True`. |
| Directional-pause breadth bypass lacks config | False. `DIRECTIONAL_PAUSE_BREADTH_BYPASS_ENABLED` and thresholds exist. |

## Runtime Strategy Inventory

### Data Ingest

| Zerodha source | Current usage | Audit note |
|---|---|---|
| `quote()` live quotes | LTP, day OHLC, volume, best bid/ask depth, top-5 depth | Entry now tries 3 times, then fails closed if LTP/depth is still missing. Still no timestamp validation because Kite quote payload does not provide a reliable per-field timestamp here. |
| `historical_data()` intraday | 15-min candles for scoring, patterns, ATR, exits | Good foundation, but scoring should track whether the latest candle is from today. Pre-market scans can otherwise look stronger than they are until stale-score recheck catches them. |
| `historical_data()` daily | daily EMA, previous-day S/R, gap context, RVol baseline | Daily volume is too coarse for first-hour RVol. Need per-stock intraday volume curves. |
| `positions()` | manual adoption, external-close detection, partial-close detection | Empty-response guard exists. Exit price for external closes remains approximate until `verify_trades.py` reconciles. |
| `orders()` | fill price, SL-M status, crash recovery, duplicate prevention | Good, but should be the source for external-close fill prices whenever possible. |
| margins/funds | budget/funds refresh | Good separation between configured cap and available funds. |
| instruments/tick size | entry limit rounding | Good. Could use circuit metadata if available, but live quote OHLC is enough for current guard. |

More Zerodha data to leverage:

- Persist quote/depth snapshots for accepted and rejected entries. Right now the daily rejection audit estimates end-of-day drift, but not whether the order book was already warning us.
- Build per-symbol/per-hour volume baselines from historical 15-min candles instead of daily pro-rating.
- Use order book fills/orders as primary truth for external close price before falling back to live LTP.
- Add WebSocket ticks for exit execution and near-SL/target responsiveness, but only after the strategy edge is fixed.

### Scoring Path

Actual NoAI flow:

1. Fetch universe from `SCAN_UNIVERSE`.
2. Fetch live quotes and price-filter by LTP.
3. Fetch 15-min candles and daily candles.
4. Detect candlestick patterns with volume and freshness decay.
5. Compute technical score from EMA, RSI, VWAP, SuperTrend, ADX, MACD,
   daily EMA, previous-day S/R, ORB, gap, hourly EMA, Bollinger squeeze,
   Fibonacci, VWAP bands, extended-move penalty, and RSI hard cap.
6. Add pattern score to technical score.
7. Apply pattern-vs-tech contradiction penalty.
8. Add RVol bonus/penalty when enough same-day candles exist.
9. Filter by score floor.
10. Apply tape-breadth penalty.
11. Apply sector momentum and sector-rank bias.
12. Apply NIFTY trend hard filter.
13. Apply sector diversification.
14. Apply score momentum penalty/tiebreak.
15. Select NoAI primary and fallback candidates.
16. Apply score-weighted sizing to primary candidates.
17. Observation period and stale-score filter re-score opening candidates.
18. `enter_trade()` runs the entry gates.

### Scoring Review

| Strategy | Status | Reason |
|---|---|---|
| EMA + SuperTrend + MACD trend stack | Tune | These are highly correlated on the same 15-min frame. They can produce high conviction from one underlying trend impulse. Use a trend-cluster cap or de-correlate second/third signal. |
| RSI scoring plus RSI entry hard blocks | Keep, tune | RSI cap is useful. But RSI can be both a mean-reversion signal and a no-chase block. Keep the hard block; reduce raw RSI score weight until backtested. |
| Candlestick patterns | Keep, cap | Pattern detection is useful, but pattern score has no explicit hard cap and can stack with correlated tech. Cap net pattern contribution and suppress stale previous-day pattern contribution in displayed pre-market scores. |
| Pattern contradiction penalty + pattern veto | Keep | This is one of the cleaner ways to stop visual reversal patterns from being outvoted by indicators. |
| RVol bonus | Tune | Daily-volume pro-rating is weak in the first hour. Replace with hour-bucket historical intraday volume curves. Until then, treat early RVol as confirmation-only, not a +1 score bonus. |
| ORB | Keep, tune | ORB is industry-standard, and code suppresses it until enough today candles exist. Add explicit freshness metadata to avoid cached-score confusion. |
| Gap analysis | Keep | Good as a context feature and gap-coherence gate input. Do not overweight it without measuring gap-fill behavior on NSE names. |
| VWAP position and VWAP bands | Keep, simplify | Bands should dominate simple above/below VWAP. Code does that, but the logic is hard to read. Keep one clear VWAP feature in scoring and one adaptive entry gate. |
| Extended-move penalty | Tune | Current penalty only hits chasing. Contrarian trades after large moves are also dangerous. Backtest symmetric or softer all-extended penalties. |
| Sector momentum and sector-rank bias | Pause or shrink | These are small but easy to overfit from tiny samples. Keep sector caps; reduce sector score nudges until measured. |
| Tape-breadth filter | Keep | This is closer to market microstructure logic than single-stock noise. The 30-40% uncertain band is sensible. |
| Direction allocation gap rule | Tune | `score_gap >= 3` creates a cliff. Use hysteresis or continuous allocation. |
| Score-weighted sizing | Pause for live, keep in dry-run/backtest | Recent data says high score is not reliable. Sizing bigger by score magnifies the exact failure mode. |

### Entry Gate Inventory

All current NoAI candidates pass through these runtime gate groups in order:

1. Rolling-PF pause (disabled by config).
2. Directional auto-pause with NIFTY-bounce and tape-breadth bypass.
3. Opposing-thin fractional-Kelly cap.
4. Entry-burst cap.
5. Choppy-morning pause.
6. VIX spike pause.
7. Late-entry score tightening.
8. Lunch-lull skip.
9. Daily-loss soft-stop.
10. Peak-drawdown stop.
11. Live price validation.
12. Circuit-limit guard.
13. Bid-ask spread.
14. Impact-cost/depth.
15. RVol confirmation.
16. ATR SL/target.
17. SL side sanity.
18. ATR risk sizing.
19. Gross R:R floor.
20. Minimum expected profit.
21. Dry-run slippage simulation.
22. Budget cap and quantity reduction.
23. Max open positions.
24. Duplicate symbol guard.
25. Sector concentration.
26. Direction diversification.
27. Short cutoff.
28. Max re-entries and declining same-side score.
29. RSI contradiction gates.
30. Pattern-direction veto.
31. ADX + DI gate.
32. Gap-coherence gate.
33. Daily/expiry trade cap.
34. Stagnant churn guard.
35. Re-entry cooldown.
36. Average-down prevention.
37. VWAP trend/extension/fresh-reversal guard.
38. VWAP statistical-band gate.
39. Net-of-charges R:R.
40. Charge-aware minimum target.
41. Order placement and fill reconciliation.
42. SL/target scaling to actual fill.
43. Exchange SL-M placement.
44. Entry counters and state stamping.

Financial view:

- Strong keep: quote/depth validation, R:R floor, charge floor, SL-M, budget cap, max positions, sector cap, short cutoff, stale-score guard, pattern veto, ADX/DI, VWAP, daily trade cap, cooldowns.
- Needs tuning: lunch-lull threshold, ADX override, direction diversification, VWAP fixed extension vs band overlap, late-entry score bump.
- Paused/disabled should remain paused: rolling-PF full-day blackout, late-entry no-rescue clamp, any new score threshold raise based only on the latest bad day.

### Exit And Monitor Inventory

Runtime exit/management checks:

1. External position sync/adoption.
2. Quote fetch and MTM cache update.
3. Momentum kill before SL/target.
4. Stop-loss.
5. Target hit.
6. Trailing stop and partial booking.
7. Time-decay target adjustment for open positions.
8. Circuit breaker.
9. Late-day loser exit.
10. Candle re-score.
11. Signal-reversal exit.
12. Signal-decay/sign-flip exit.
13. Auto-protect on contrary signal.
14. Sector-cascade protective SL tightening.
15. NIFTY regime protect.
16. NoAI stagnant Tier 1/Tier 2 exit.
17. Opportunity and partial rescans.
18. Square-off.
19. Report writing, tax-ledger fill, trade verification, rejection audit.

Exit review:

- SL-M plus software SL is the right safety architecture.
- Momentum kill is useful but should be watched carefully; in scored trades it is 0/8 wins and Rs.-323.74, meaning it cuts losers but does not prove the entry model is fixed.
- Stagnant exits are doing damage control, not edge generation: 38 scored cases, Rs.-147.38. That is a symptom of weak entries drifting, not a standalone problem.
- TARGET_HIT carries the system: 13 scored target hits, Rs.+2,446.69. The bot can win when it catches a real runner; the task is to stop false high-score runners.
- Signal decay is mixed and should stay, but any change must run `scripts/exit_coverage_check.py`.

## Highest Priority Strategy Recommendations

1. Pause score-weighted sizing in live NoAI until score/PnL correlation is
   positive over a rolling sample. Equal or capped sizing is better when
   high scores recently lost the most.
2. Add a scoring backtest/sweep before changing thresholds again. At minimum
   test 60-90 days over: score weights, score floor, sector bias on/off,
   RVol bonus on/off, trend-cluster cap, direction-allocation hysteresis.
3. Create a feature/outcome table for every candidate, not only entered
   trades. The rejection audit is useful but too late and too coarse.
4. De-correlate scoring. Treat EMA, SuperTrend, MACD, hourly EMA, and ORB as
   one trend cluster with a cap, not independent proof.
5. Replace daily-volume pro-rated RVol with per-symbol intraday volume curves.
6. Make pre-market score display explicit: "pre-open score, requires fresh
   recheck". The initial observation stale-score guard helps execution, but
   the logs still teach the operator to trust stale-looking high scores.
7. Keep risk gates, but stop adding more gates before measuring the scoring
   edge. Recent losses are not primarily from being under-protected; they are
   from entering names whose score did not represent edge.

## Code And Docs Hygiene

Immediate cleanups done in this pass were intentionally small. Larger cleanup
should be a separate mechanical phase:

- Move long config decision histories into a strategy decision log. `config.py`
  should have the default, the kill-switch, and a one-line behavior note.
- Keep `docs/STRATEGY_V2.md` as the strategy reference, but prune the giant
  sync-note block at the top into dated changelog files.
- Keep `copilot/pre-trade-checklist.md`, `copilot/code-map.md`, and
  `copilot/review-cycle.md` if they are actively used. Move human-only review
  prompts to `docs/review/` or mark them non-runtime.
- Mark likely phase-1 scripts as deprecated if kept: `scripts/view_analyses.py`
   looks nonessential to phase-2 NoAI. `scripts/import_reports_to_db.py` was
   later deleted in the 2026-05-11 HFT-readiness audit because it was a stale
   one-time JSON-to-SQLite importer.
- Preserve `scripts/rejection_audit.py`, `scripts/verify_trades.py`,
  `scripts/exit_coverage_check.py`, and `scripts/strategy_stability_check.py`.

## What I Would Not Do Yet

- Do not raise `V2_MIN_SCORE` blindly. Recent higher scores lost more.
- Do not re-enable rolling-PF full-day blackout from fear. It is already
  documented as net-negative once directional pause exists.
- Do not add another late-entry gate until the score model is calibrated.
- Do not use Claude/AI mode as the answer. The NoAI math must stand on its own.

## Suggested Next Work Packages

1. Backtest harness: replay historical 15-min candles and produce candidate
   feature rows for entered and rejected trades.
2. Scoring v3 experiment: trend-cluster cap, pattern cap, no early RVol bonus,
   score-weight sizing off, direction hysteresis.
3. Data reliability layer: central quote validator in `ZerodhaClient` returning
   typed quote/depth objects instead of raw dicts at entry time.
4. Comment/docs cleanup: move history out of `config.py` and remove duplicated
   strategy prose from code docstrings.
