# Trade Strategy Rollout

> Created 2026-05-19 after the user's no-blend reset.  
> **Single source of truth for which gates are on/off and what stage we are at.**
> Every stage has a unique short name (e.g. `S0_PURE_MR`) that is stamped into config (`TRADE_STAGE_NAME`),
> trading reports, Chan evidence files, and the dashboard so we always know where we are on the ladder.

## Rule

1. Only the gates listed under the active stage are enabled.
2. Every other gate is OFF at config level. A disabled gate does not run, does not arm, does not log.
   `LOG_DISABLED_GATES` (default `False`) is the only override for inspection.
3. To move to the next stage, the prior stage must PASS its evidence bar AND we must run a parameter
   sweep on the variables listed in that stage's row.
4. Promotion bar per stage: **10 dry-run sessions AND at least 30 closed simulated trades**, unless the
   stage explicitly says otherwise.
5. A failed stage is FROZEN: we tune ONLY the parameters in that row, we do not skip ahead to the next stage.

## Cross-Cutting Gates That Are Always On (safety, not alpha)

| Setting | Why it cannot be a "stage" knob |
|---|---|
| `TRADE_LIVE_TRADING_PAUSED = True` | Live order placement stays paused until a stage explicitly approves a live pilot. |
| `MAX_LOSS_PER_DAY_PCT = 3.0` | Hard daily circuit breaker. Without it a bad day can blow up an account. |
| `MAX_POSITION_PCT`, `MAX_POSITIONS_OVERRIDE` | Capital cap per position. |
| `MIN_BALANCE_TO_TRADE` | Refuse to trade when account is sub-charge-hurdle. |
| `USE_EXCHANGE_SL`, `USE_LIMIT_ORDERS` | Execution mechanics, not alpha. |
| `MAX_SPREAD_PCT`, `MAX_IMPACT_COST_PCT` | Reject untradable books. |
| `SLIPPAGE_PCT` | Dry-run realism. |
| `RR_HARD_FLOOR`, `RR_TARGET_RATIO`, `RR_GIVEUP_AFTER_FAILS` | Structural R:R sanity. |
| `MIN_SL_DISTANCE_PCT`, `MAX_INTRADAY_SL_PCT` | SL floor/ceiling. |
| `REALISED_PNL_RECOVERY_ENABLED`, `ADOPTED_POSITION_GRACE_MINUTES` | Restart safety. |
| NSE holiday list + expiry-date detection (date math) | Calendar correctness. |
| Square-off scheduling | Intraday positions must close before 15:20. |

## The Ladder

| Stage | Name | What it adds vs prior stage | Parameter sweep variables (one family at a time) | Min sample to promote |
|---:|---|---|---|---|
| 0 | `S0_PURE_MR` | Simple MR alpha only: VWAP-stretch + RSI-exhaustion. No vetoes, no day-loss soft stops, no mid-day exits, no sizing modifiers. Fixed SL%/Target%. | `SIMPLE_MR_MIN_SCORE` {2.5, 3.0, 3.5}; `SIMPLE_MR_MIN_VWAP_DEV_PCT` {0.30, 0.35, 0.45}; `SIMPLE_MR_RSI_BUY_MAX` {35, 40, 45} (mirror SELL); `SIMPLE_MR_REQUIRE_VWAP_BAND` {True, False} | 10 sessions and 30 trades |
| 1 | `S1_MR_SLT` | Adds Stop-Loss / Target tuning study. | `DEFAULT_STOP_LOSS_PCT` {1.0, 1.5, 2.0}; `DEFAULT_TARGET_PCT` {1.0, 1.5, 2.0}; `RR_TARGET_RATIO` {1.3, 1.5, 1.8} | 10 sessions |
| 2 | `S2_MR_ATR` | Adds ATR-based SL/target distance. Equal sizing still. | `ATR_MULTIPLIER` {1.2, 1.5, 1.8}; `ATR_PERIOD` {10, 14, 21}; `MIN_SL_DISTANCE_PCT` {0.5, 0.8, 1.0} | 10 sessions |
| 3 | `S3_MR_ATRSIZE` | Adds ATR-based position sizing (`ATR_SIZING_ENABLED`). | `RISK_PER_TRADE_PCT` {0.3, 0.5, 0.75}; `BUDGET_REGIME_ENABLED` study {True, False} | 10 sessions |
| 4 | `S4_MR_CHARGE` | Adds charge-aware R:R veto. | `MIN_PROFIT_CHARGE_MULTIPLE` {0, 2.0, 3.0}; `MIN_EXPECTED_PROFIT` {0, 75, 135} | 10 sessions |
| 5 | `S5_MR_DAYRISK` | Adds day-level risk: soft-stop, peak-drawdown, consecutive-loss, MTM-aware CB. | `DAILY_LOSS_SOFT_STOP_PCT` {0, 1.0, 1.5, 2.0}; `PEAK_DRAWDOWN_STOP_PCT` {0, 1.0, 1.5, 2.0}; `CONSECUTIVE_SL_PAUSE_COUNT` {0, 3, 5}; `MTM_AWARE_CB_ENABLED` | 10 sessions |
| 6 | `S6_MR_MIDEXIT` | Adds mid-life exits: stagnant, momentum-kill. | `STAGNANT_EXIT_MINUTES` {0, 30, 45, 60}; `STAGNANT_HARD_MAX_ENABLED`; `MOMENTUM_KILL_ENABLED`; `MOMENTUM_KILL_WINDOW_MINUTES` {3, 5, 10} | 10 sessions |
| 7 | `S7_MR_SIGEXIT` | Adds signal-reversal and signal-decay exits. | `SIGNAL_REVERSAL_SCORE` {5, 7, 9}; `SIGNAL_DECAY_FRACTION` {0.3, 0.4, 0.5}; `SIGNAL_DECAY_MIN_HOLD_MINUTES` {15, 30, 45} | 10 sessions |
| 8 | `S8_MR_TIMEOFDAY` | Adds time-of-day filters: lunch lull, late-entry tightening, short-cutoff. | `LUNCH_LULL_SCORE_OVERRIDE` {5.0, 5.7, 6.5}; `LATE_ENTRY_HOUR` {10, 11, 12}; `LATE_ENTRY_MIN_SCORE_BUMP` {0.5, 1.0, 1.5}; `SHORT_ENTRY_CUTOFF_HOUR` {13, 14, 15, 16} | 10 sessions |
| 9 | `S9_MR_CONTEXT` | Adds market-context filters: choppy-morning pause, breadth, sector-rank, sector-cascade exit. | `CHOPPY_PAUSE_ADX_THRESHOLD` {14, 16, 18}; `BREADTH_BEARISH_BUY_RATIO` {0.25, 0.30, 0.35}; `SECTOR_RANK_BIAS_STEP` {0.05, 0.10, 0.20}; `SECTOR_CASCADE_DROP_THRESHOLD` {1.5, 2.0, 3.0} | 10 sessions |
| 10 | `S10_MR_PERFPAUSE` | Adds multi-day performance pauses (directional pause + rolling-PF). | `DIRECTIONAL_PAUSE_WR_THRESHOLD` {0.25, 0.30, 0.40}; `DIRECTIONAL_PAUSE_LOOKBACK_DAYS` {5, 7, 10}; `ROLLING_PF_PAUSE_ENABLED` study | 20 sessions (multi-day gate) |
| 11 | `S11_MR_EXPIRY` | Adds Thursday-expiry adjustments. | `EXPIRY_ATR_BUMP`; `EXPIRY_SCORE_BUMP`; `EXPIRY_MAX_TRADES_PER_DAY`; `EXPIRY_MIN_SL_DISTANCE_PCT` | 4 expiry Thursdays |
| 12 | `S12_LIVE_PILOT` | First live pilot at smallest practical capital. | None — config frozen from S11. | 10 live sessions and 20 closed trades |

## Stage 0 (`S0_PURE_MR`) — current

What runs:

- Scanner: `_scan_noai_simple_mr` only. The pattern/technical score path is computed for telemetry, but its score is overwritten by the Simple MR score and the pattern penalty is silent.
- Entry: only the structural execution checks fire — price validation, R:R hard-floor, SL distance floor/ceiling, spread/impact, budget, slot caps.
- Position life: no stagnant exit, no momentum kill, no signal reversal/decay exit, no sector cascade, no late-day loser cull. Only fixed ATR-derived SL/target and end-of-day square-off.
- Day risk: only `MAX_LOSS_PER_DAY_PCT = 3.0` hard circuit breaker.
- Sizing: equal slots, no ATR sizing, no budget-regime delta, no score-weighted sizing, no loss-adjusted budget.

What is OFF (every one of these is at config-level — they do not arm, do not log):

`ADX_ENTRY_GATE_ENABLED`, `VWAP_BAND_GATE_ENABLED`, `PATTERN_VETO_ENABLED`,
`PATTERN_CONTRADICTION_PENALTY_ENABLED`, `BREADTH_FILTER_ENABLED`, `SECTOR_RANK_BIAS_ENABLED`,
`SECTOR_CASCADE_EXIT_ENABLED`, `GAP_COHERENCE_GATE_ENABLED`, `STRONG_GAP_ADX_BOOST_ENABLED`,
`EARNINGS_BLACKOUT_ENABLED`, `RVOL_TIME_NORMALIZATION_ENABLED`, `INTRADAY_VOLUME_BASELINE_ENABLED`,
`LUNCH_LULL_ENABLED`, `LATE_ENTRY_TIGHTENING_ENABLED`, `LATE_ENTRY_NO_RESCUE_FLOOR_ENABLED`,
`VIX_SPIKE_ENTRY_PAUSE_ENABLED`, `CIRCUIT_LIMIT_GUARD_ENABLED`, `RE_ENTRY_COOLDOWN_ENABLED`,
`AVG_DOWN_PREVENTION_ENABLED`, `ENTRY_BURST_CAP_ENABLED`, `CHOPPY_MORNING_PAUSE_ENABLED`,
`FRESH_ENTRY_RECHECK_ENABLED`, `DIRECTIONAL_PAUSE_ENABLED`, `ROLLING_PF_PAUSE_ENABLED`,
`MTM_AWARE_CB_ENABLED`, `ATR_SIZING_ENABLED`, `BUDGET_REGIME_ENABLED`, `LOSS_SIZING_ENABLED`,
`SCORE_WEIGHTED_SIZING_ENABLED`, `STAGNANT_HARD_MAX_ENABLED`, `SIGNAL_REVERSAL_EXIT_ENABLED`,
`SIGNAL_DECAY_EXIT_ENABLED`, `MOMENTUM_KILL_ENABLED`, `VWAP_DRIFT_CHECK_ENABLED`,
`REJECTION_AUDIT_ENABLED`.

Numeric kill-switches set to 0 / wide for Stage 0:
`MIN_PROFIT_CHARGE_MULTIPLE = 0`, `MIN_EXPECTED_PROFIT = 0`, `MAX_TRADES_PER_DAY = 0`,
`STAGNANT_EXIT_MINUTES = 0`, `TARGET_DECAY_AFTER_HOUR = 24`, `DAILY_LOSS_SOFT_STOP_PCT = 0`,
`PEAK_DRAWDOWN_STOP_PCT = 0`, `CONSECUTIVE_SL_PAUSE_COUNT = 0`, `LOSS_SCORE_BUMP_PCT = 0`,
`SHORT_ENTRY_CUTOFF_HOUR = 16`, `VIX_HIGH_SCORE_BUMP = 0`, `VIX_HIGH_POSITION_REDUCTION = 0`,
`EXPIRY_SCORE_BUMP = 0`, `EXPIRY_ATR_BUMP = 0`, `EXPIRY_MAX_TRADES_PER_DAY = 0`,
`TRAIL_AFTER_RISK_MULTIPLE = 0` (no partial profit / trail in S0).

## Promotion Gates (apply at every stage 0-11)

- After-cost profit factor `>= 1.15`
- Expectancy `>= Rs.10/trade`
- Profitable-day rate `>= 55%`
- Trade win rate `>= 40%`
- Max drawdown `<= 3%` of average daily capital
- No single-day outlier explains more than 30% of net P&L

A stage with PF below 1.0 over a full sample is retired, not promoted.

## Post-Trade Automation Contract

The end-of-day dry-run path must:

1. Append closed dry-run trades to `data/trade_analysis.db::dryrun_trade_ledger` (idempotent).
2. Stamp every row with `config_hash`, `config_version`, AND `stage_name` so we can cohort-analyse.
3. Write `reports/trading/YYYY/MM/chan_evidence_DD_dryrun.{json,md}` with `stage_name` in the header.
4. NEVER write to `data/trades.db` or `intraday_tax_ledger` from dry-run.
5. The dashboard's L0 progress card reads `stage_name` from config and counts only rows with that
   stage name. Rows tagged with a different stage are evidence for that other stage, not for the current one.

## Today's State (2026-05-19)

- Stage name: `S0_PURE_MR`
- Prior dry-run on 2026-05-19 was generated under a contaminated config (many non-MR gates active);
  those artifacts were purged. The L0 sample restarts at zero from the next dry-run session.
- Decision recorded in [docs/TRADE_EVOLUTION.md](TRADE_EVOLUTION.md) as `T2.0`.
