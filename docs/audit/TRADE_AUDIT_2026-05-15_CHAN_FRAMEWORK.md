# Chan-Framework Intraday Audit - 2026-05-15

Scope: default intraday trading mode, especially `python main.py --mode trade` / NoAI. This audit uses the framework from Ernest P. Chan's *Algorithmic Trading: Winning Strategies and Their Rationale*: a trading idea needs an economic reason, a replayable implementation, transaction-cost-aware testing, out-of-sample validation, and a measured promotion path before real capital is increased.

Source note: I did not use pirated PDFs. I used legitimate public metadata, including the author page, which describes the book as focused on real strategy examples, why each strategy was developed, how it was implemented, and how it was coded. The strategy-family mapping below uses the book's well-known themes: backtesting discipline, mean reversion, momentum, pairs/statistical arbitrage, seasonality, and HFT/microstructure.

## Executive Verdict

| Question | Verdict | Reason |
|---|---|---|
| Is the current live bot profitable? | No | Latest read-only metrics show current FY net about Rs.-3,928.68 after charges, promotion check FAIL, and last-9-day net about Rs.-3,947.51. |
| Is the current bot a clean Chan-style strategy? | No | It is a large combined score plus many gates. Good risk controls exist, but entry edge is not isolated by strategy family. |
| Should we add more live gates now? | No | Chan's process would first make the hypothesis replayable, then test it after costs. More gates now would deepen overfit risk. |
| Should capital be scaled? | No | `promotion_check.py --window 20` is FAIL: PF 0.839, expectancy Rs.-6.11/trade, day win rate 30.0%. |
| What is the right next step? | Research reset | Freeze live strategy tuning, keep evidence plumbing, isolate one strategy family, and trade only after backtest plus forward sample clears gates. |

## Current Code Walk-Through

### 1. Day Orchestration

`modes/trade/manager.py` owns the trading day:

1. Validates config and logs into Zerodha.
2. Fetches account funds and applies budget regime.
3. Waits for pre-market / market-open timing.
4. Runs a scanner pass to build trade plans.
5. Waits through the observation window and stale-score recheck.
6. Calls the order engine to attempt entries.
7. Monitors open positions until square-off.
8. Reconciles, records, verifies, runs rejection audit, and writes reports.

NoAI is the default path. AI mode still exists, but it is not the path to fix profitability because the NoAI math must first stand on its own.

### 2. Scanner And Candidate Selection

`modes/trade/stock_scanner.py` builds candidates from the NIFTY universe:

1. Filters by live price and quote availability.
2. Fetches cached/live 15-minute candles and daily candles.
3. Detects candlestick patterns and technical indicators.
4. Builds one `combined_score` from pattern score plus technical score.
5. Applies contradiction penalties, relative-volume adjustment, tape breadth, sector momentum, sector rank bias, NIFTY trend filtering, sector diversification, and score momentum.
6. Writes candidate telemetry rows for survivors.
7. In NoAI mode, chooses primary and fallback candidates, assigns side from score sign, computes preliminary SL/target/qty, and sends them to the entry loop.

Important audit point: this is not one strategy. It mixes momentum, mean reversion, breadth, sector, gap, and pattern logic into one score. That makes post-loss diagnosis hard because a win or loss cannot be cleanly attributed to one tested idea.

### 3. Entry Engine

`modes/trade/order_engine.py` owns entry checks and live execution. The entry method runs roughly 44 checks, including:

- multi-day and side-specific pauses;
- burst cap;
- choppy-morning and VIX pauses;
- live quote, spread, depth, and impact-cost validation;
- RVol confirmation;
- ATR SL/target and ATR sizing;
- gross and net risk:reward checks;
- charge-aware minimum target;
- budget, position, duplicate, sector, direction, short-cutoff, re-entry, RSI, pattern, ADX/DI, gap, and VWAP gates;
- order placement, actual fill reconciliation, and exchange SL-M placement.

This is the strongest engineering surface in the bot. It is not the main suspected weakness. The suspected weakness is the entry model feeding it.

### 4. Monitor And Exits

The monitor loop manages open risk through:

- exchange SL-M plus software SL checks;
- target exits;
- partial profit plus trailing stop;
- time-decay target adjustment;
- momentum kill;
- stagnant exits;
- signal-reversal and signal-decay exits;
- candle-protect, sector-cascade protect, and NIFTY regime protect;
- late-day loser exit;
- final square-off.

These exits are mostly damage control. The recent ledger shows many STOP_LOSS, MOMENTUM_KILL, and STAGNANT_EXIT losses, which says the bot is entering too many weak setups, not that exit plumbing alone can fix the strategy.

### 5. Evidence Tools Already Present

The May 11 audit shipped the tools needed for a Chan-style reset:

| Tool | Status | Use |
|---|---|---|
| `scripts/trade/backtest.py` | Present, simplified | Replay-safe A/B harness; not full live scoring yet. |
| `intraday_candidates` telemetry | Present | Candidate-level feature/outcome store. |
| `Config.snapshot_hash()` | Present | Links every run to exact config version/hash. |
| `scripts/trade/promotion_check.py` | Present | Objective PASS/FAIL gate for scaling or relaxing risk. |
| `scripts/trade/exit_coverage_check.py` | Present | Required when exit gates change. |
| `scripts/trade/rejection_audit.py` | Present | Checks whether skipped entries were helpful or harmful. |

The missing piece is full-fidelity replay and a strategy-family split. Without those, every threshold tweak is still guesswork.

## Chan Framework Versus Current Tool

| Book principle | What it means here | Current state | Decision |
|---|---|---|---|
| Strategy rationale before code | A trade should have one clear reason to exist. | Combined score blends many reasons. | Split strategies by family. |
| Backtest before live tuning | Test the exact hypothesis after costs before live changes. | Backtest MVP exists but scoring is simplified. | Build full-fidelity replay before changing score weights. |
| Transaction costs matter | Small edges disappear after brokerage, STT, spread, and slippage. | Cost checks exist; live PF still fails. | Keep cost gates; require PF pass before scale. |
| Avoid data snooping | Repeated retuning on the same losing window creates false confidence. | Stability windows exist but prior tuning was frequent. | Freeze live strategy tuning. |
| Mean reversion | Trade stretch away from fair value when there is evidence of reversion. | Present only as pieces: RSI, VWAP bands, no-chase gates. | First isolated strategy to test. |
| Momentum | Trade continuation after validated trend/breakout. | Current tool leans heavily here; recent results poor. | Pause as a standalone live thesis until replay proves regimes where it works. |
| Pairs/statistical arbitrage | Trade relative mispricing between linked instruments. | Not implemented. | Later phase only; needs pair selection and hedge logic. |
| Seasonality | Trade recurring calendar effects. | Not implemented. | Later, likely not first for NSE intraday equity. |
| HFT/microstructure | Needs low-latency tick/order-book data and robust simulation. | Kite REST + 15-min candles is not HFT. | Telemetry only until WebSocket/tick replay exists. |

## Strategy Family Decision

The first new staged strategy should be mean reversion, not another all-in-one momentum score.

Reasoning:

- Chan's framework favors simple, testable hypotheses with a rationale. A VWAP/standard-deviation mean-reversion idea can be stated and tested cleanly.
- Current losses show high-churn momentum false positives. A continuation strategy may still work, but only in specific regimes we have not isolated.
- Kite REST, 15-minute candles, and 10-second polling are not suitable for HFT-like edge. Trying to compete on speed is the wrong path.
- Mean reversion can be made cost-aware: trade only when distance to fair value is larger than charges, spread, and slippage.

The first hypothesis to test should be:

> Liquid NSE names that stretch to a statistically meaningful VWAP band and then show exhaustion should revert enough intraday to pay charges, provided spread/depth are clean and the market regime is not strongly trending against the reversion.

This is not approved for live trading yet. It becomes live only after full-fidelity replay and a forward sample pass the promotion gate.

## What Is Already Paused

These config states already match the reset posture:

| Feature | Current state | Keep this state? | Why |
|---|---|---|---|
| Score-weighted sizing | `SCORE_WEIGHTED_SIZING_ENABLED = False` | Yes | Score edge is not stable enough to size bigger by score. |
| Rolling-PF full-day blackout | `ROLLING_PF_PAUSE_ENABLED = False` | Yes | Prior replay showed it was net-negative once directional pause existed. |
| Late no-rescue score clamp | `LATE_ENTRY_NO_RESCUE_FLOOR_ENABLED = False` | Yes | Prior EV audit contradicted the premise. |
| Intraday volume baseline | `INTRADAY_VOLUME_BASELINE_ENABLED = False` | Yes until DB is built and validated | Infrastructure exists, but enabling before baseline quality is known would hide another variable. |

## What Should Be Paused By Policy Now

These are planning decisions, not code changes in this audit pass:

| Area | Policy for reset phase |
|---|---|
| New live gates | Do not add unless they fix a verified bug or close a proven safety hole. |
| Combined-score weight tuning | Do not tune live until full-fidelity replay exists. |
| Capital scale-up | Not allowed until `promotion_check.py` returns PASS on a fresh forward window. |
| AI trade selection | Not part of the supported reset path. Keep optional, but do not use it to justify live edge. |
| HFT/WebSocket work | Defer until strategy edge is positive. Speed does not fix negative expectancy. |
| Pairs/stat-arb/seasonality | Research-only until the baseline replay pipeline can test them. |

## Staged Plan

### Phase 0 - Research Reset And Freeze

Goal: stop the live tuning loop.

Actions:

1. Mark the supported trading posture as `Chan Research Reset` in docs and dashboard/status surfaces.
2. Keep NoAI/equal-sizing as the only supported path for evidence collection.
3. Keep safety gates on; do not remove risk controls without replay.
4. Keep score-weighted sizing, rolling-PF blackout, no-rescue clamp, and intraday volume baseline disabled.
5. Require every future strategy experiment to have a strategy id, config hash, backtest result, and forward result.

Exit criteria:

- Docs and runtime status agree on the active phase.
- Candidate telemetry is healthy on every trading day.
- Promotion check is still treated as a hard gate.

### Phase 1 - Full-Fidelity Replay

Goal: make live decisions replayable before changing strategy.

Actions:

1. Upgrade `scripts/trade/backtest.py` so it can call the live scanner logic with a replay clock instead of simplified scoring.
2. Persist enough candidate features to replay accepted and rejected trades by config hash.
3. Add transaction-cost, spread, slippage, and square-off modeling to the replay summary.
4. Add a comparison report: live ledger vs replay under the same config.

Exit criteria:

- Replay can approximately reproduce candidate order and trade count for recent sessions.
- A 30-60 session run prints WR, PF, expectancy, max drawdown, and exit reason split.
- Backtest output is clearly marked as in-sample or out-of-sample.

### Phase 2 - Mean-Reversion V1

Goal: test one simple strategy family from the book framework.

Actions:

1. Create a separate strategy id, for example `MEAN_REVERSION_V1`.
2. Entry concept: VWAP band stretch plus RSI/exhaustion confirmation, with spread/depth and charge cushion.
3. Disable momentum/ORB entry contribution inside this strategy. They may remain as risk/context fields, not as entry reasons.
4. Backtest against current all-in-one NoAI baseline.
5. Forward-run in dry-run first.

Exit criteria:

- Backtest PF >= 1.15 after costs over at least 60 sessions.
- Out-of-sample or walk-forward segment remains positive.
- Forward dry-run has at least 20 sessions and passes the promotion gate.

### Phase 3 - Momentum/ORB V1

Goal: reintroduce trend following only where it works.

Actions:

1. Create `MOMENTUM_ORB_V1` as a separate strategy id.
2. Use ORB, EMA/SuperTrend/MACD, ADX, and breadth as a continuation hypothesis.
3. Explicitly restrict regimes where the replay shows false breakout damage.
4. Do not blend it with mean reversion until both pass alone.

Exit criteria:

- Momentum works in a clearly defined regime after costs.
- It passes separately before any ensemble allocation is considered.

### Phase 4 - Pairs / Statistical Arbitrage Research

Goal: explore book-style relative-value strategies only after replay discipline exists.

Actions:

1. Build pair selection from stable relationships, not eyeballed sector names.
2. Test spread stationarity, hedge ratio, and transaction costs.
3. Paper-trade first because shorting constraints and borrow/cover risk matter intraday.

Exit criteria:

- Pair spread has a measurable reversion half-life.
- Backtest includes both legs, costs, slippage, and square-off constraints.

### Phase 5 - Seasonality And Calendar Effects

Goal: test recurring NSE patterns without assuming they transfer from futures examples.

Actions:

1. Study expiry days, opening windows, lunch lull, and close behavior as data features.
2. Treat them as filters or strategy ids only if backtest shows standalone edge.

Exit criteria:

- Calendar effect is positive after costs and not explained by one outlier month.

### Phase 6 - Microstructure / HFT-Style Telemetry

Goal: use order-book data only as evidence until the infrastructure can support it.

Actions:

1. Log order-book pressure and top-of-book imbalance at candidate and entry time.
2. Defer WebSocket execution until expectancy is positive.
3. Never describe Kite REST plus 15-minute candles as HFT.

Exit criteria:

- Tick or quote-stream replay exists.
- Microstructure feature improves short-horizon fill/entry quality out of sample.

## First Code Change Recommended

The first code change should be operational, not a new entry rule:

1. Add an explicit strategy phase/version label such as `CHAN_RESEARCH_RESET` to config/report/dashboard output.
2. Make the default supported mode in docs and status: NoAI, equal sizing, evidence collection, no capital scale.
3. Add a startup warning if telemetry is unhealthy during the reset phase.
4. Keep all existing trade behavior unchanged unless the user explicitly approves dry-run-only enforcement.

This gives us a clean before/after timestamp without contaminating the strategy itself. After that, Phase 1 full-fidelity replay is the real implementation work.

## Bottom Line

The current tool has serious engineering work in place, but it does not yet follow the book's research process. The fix is not another clever gate. The fix is to stop blending strategy families, prove one hypothesis at a time, and only let live capital follow when the metrics pass after costs.
