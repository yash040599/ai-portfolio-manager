# Trading Statistics

This doc is rendered on the dashboard theory/statistics page. It now reflects the 2026-05-15 Chan Research Reset: live evidence first, theory second, and no capital scaling until promotion metrics pass after costs.

## 0. Current Verdict

| Area | Current Read |
|---|---|
| Runtime strategy version | `v1.0-2026-05-11` |
| Planning posture | Stage 0 Chan Research Reset is active in runtime/report/dashboard status. |
| Supported live path | Paused: no new live trades until replay/forward evidence allows the next staged method. |
| Broker API posture | Zerodha trading/dev APIs are not assumed available; use read-only/local evidence unless the user recharges them for broker-side testing. |
| Promotion status | FAIL on the latest 20-session window. |
| Capital scaling | Blocked. |
| New live alpha gates | Blocked unless they fix a verified bug or safety hole. |

Plain-English reading: the tool has a lot of risk engineering, but the current entry/scoring edge is not validated. The statistics page should not describe the strategy as profitable until the live or forward-sample metrics prove it.

## 1. Latest Live Snapshot

Captured on 2026-05-15 with read-only CLI commands.

### FY 2026-27 Tax Ledger

Command:

```powershell
.\.venv\Scripts\python.exe scripts\shared\tax_summary.py --intraday
```

| Metric | Value | Verdict |
|---|---:|---|
| Total trades | 184 (180 verified, 4 unverified) | Enough to say the current mixed strategy is not working. |
| Gross P&L | Rs.-1,232.98 | Negative before charges. |
| Regulatory charges | Rs.2,590.70 | Charges are larger than the gross loss. |
| Claude API costs | Rs.105.00 | Small, but included in net. |
| Net profit before tax | Rs.-3,928.68 | Losing. |
| ITR turnover | Rs.10,742.90 | Tax/stat reference. |

### Promotion Gate

Command:

```powershell
.\.venv\Scripts\python.exe scripts\trade\promotion_check.py --window 20
```

| Metric | Current | Required | Status |
|---|---:|---:|---|
| Profit factor | 0.839 | >= 1.15 | FAIL |
| Expectancy | Rs.-6.11/trade | >= Rs.10/trade | FAIL |
| Trade win rate | 39.86% | >= 40% | Borderline fail |
| Profitable-day rate | 30.0% | >= 55% | FAIL |
| Max drawdown | Rs.2,788.33 (2.435% of capital) | <= 3% | Pass |
| Window P&L | Rs.-842.80 on 138 trades | Positive | FAIL |

Only drawdown is acceptable. The strategy is failing on edge: PF, expectancy, and profitable-day rate.

### Recent Analyst Pulse

Command:

```powershell
.\.venv\Scripts\python.exe scripts\trade\analyst_pulse.py
```

| Recent Window | Read |
|---|---|
| Last 9 days | 88 trades, net Rs.-3,947.51. |
| BUY side | 13.0% win rate, structurally weak. |
| SELL side | 41.2% win rate, better but still not enough to call proven. |
| Main loss reasons | STOP_LOSS, EXTERNAL_CLOSE, and STAGNANT_EXIT. |
| Weakest behavior noted | 9:30-11:00 AM entries and holds under 30 minutes. |

Interpretation: the bot is not mainly failing because it lacks more exits. It is entering too many trades whose thesis breaks quickly.

## 2. What The Numbers Mean

| Question | Answer |
|---|---|
| Do we have a validated profitable strategy today? | No. |
| Should score-weighted sizing come back? | No. The latest live evidence still does not justify larger sizing by score. |
| Should we tune more thresholds live? | No. That risks overfitting the same losing window. |
| Should the current all-in-one NoAI score remain the main research baseline? | Yes, as a baseline to beat, not as a strategy to scale. |
| What should be tested first? | A separate mean-reversion strategy with full-fidelity replay and forward sample. |

The old theory was: enough gates should lift selected trades toward a 55% win rate and positive expectancy. The live ledger has not validated that. From this reset onward, theoretical edge estimates are treated as hypotheses, not conclusions.

## 3. Break-Even Constraint

The arithmetic still matters.

At a 1.3 reward:risk floor, before charges, break-even win rate is about:

```text
1 / (1 + 1.3) = 43.5%
```

After charges, the bot often needs roughly 50-55% win rate on selected trades to make money. The latest promotion window is 39.86%, and the latest profitable-day rate is 30.0%, so the system is below the required band.

This is why adding more capital, using score-weighted sizing, or loosening risk gates is not allowed now.

## 4. Paused / Disabled Features

Current config states that match the reset posture:

| Feature | Current State | Decision |
|---|---|---|
| Live trading | `TRADE_LIVE_TRADING_PAUSED = True` | Keep paused until a staged strategy earns promotion. |
| Score-weighted sizing | `SCORE_WEIGHTED_SIZING_ENABLED = False` | Keep disabled. |
| Rolling-PF full-day pause | `ROLLING_PF_PAUSE_ENABLED = False` | Keep disabled unless new evidence proves incremental value. |
| Late no-rescue floor | `LATE_ENTRY_NO_RESCUE_FLOOR_ENABLED = False` | Keep disabled; prior EV audit contradicted it. |
| Intraday volume baseline | `INTRADAY_VOLUME_BASELINE_ENABLED = False` | Keep disabled until the baseline DB is built and validated. |

Policy pauses during reset:

| Area | Policy |
|---|---|
| New live entry gates | Do not add without replay or verified safety evidence. |
| AI selection | Optional tool path only; not accepted as proof of strategy edge. |
| HFT/WebSocket work | Deferred until expectancy is positive. |
| Strategy blending | Do not blend mean reversion, momentum, and microstructure until each passes alone. |

Stage 1 data policy:

| Area | Policy |
|---|---|
| `market-research` repo | Standalone daily ATH-dip research using `yfinance` and current NIFTY 50 membership. Use as reference/seed material, not an intraday replay runtime dependency. |
| Backtest data storage | Use the separate private repo `https://github.com/yash040599/ai-portfolio-backtest-data` for normalized replay-ready datasets. |
| Main repo runtime access | Read from local gitignored `backtest_data/`, not GitHub on every replay run. |
| Linux VM access | Pull the same repo locally with `python scripts/shared/sync_backtest_data.py --ssh` before replay/trading workflows need historical data. |
| Existing operational data repo | Keep separate from the backtest-data repo so reports/tokens/current ignored data do not mix with large historical datasets. |
| First format | CSV metadata plus SQLite candle stores; avoid parquet-first until the dependency/tooling choice is deliberate. |

## 5. Metrics To Track From Here

Every staged strategy should report these numbers separately by `strategy_id` and config hash:

| Metric | Promotion Bar |
|---|---:|
| Minimum sample | >= 30 trades and ideally >= 20 sessions |
| Profit factor | >= 1.15 after costs |
| Expectancy | >= Rs.10/trade |
| Profitable-day rate | >= 55% |
| Trade win rate | >= 40% |
| Max drawdown | <= 3% of average daily capital |
| Cost drag | Charges must be shown separately from gross P&L |

The key change is separation. A mean-reversion test, a momentum test, and a future pairs test must not be merged into one score and then judged as if we know which idea worked.

## 6. Update Protocol

When a new strategy or risk change ships:

1. Record the runtime version and config hash.
2. Update this doc only with measured results, not expected benefits.
3. Add the strategy change to [docs/TRADE_EVOLUTION.md](TRADE_EVOLUTION.md) if it changes trading or evidence behavior.
4. Keep [docs/TRADE_ROADMAP.md](TRADE_ROADMAP.md) staged; do not recreate a giant pending backlog.
5. Run the promotion gate before any capital scaling or risk relaxation.

Useful commands:

```powershell
.\.venv\Scripts\python.exe scripts\shared\tax_summary.py --intraday
.\.venv\Scripts\python.exe scripts\trade\promotion_check.py --window 20
.\.venv\Scripts\python.exe scripts\trade\analyst_pulse.py
```
