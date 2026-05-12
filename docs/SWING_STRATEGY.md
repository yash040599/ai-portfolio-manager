# Swing Trading Strategy

User-facing reference for the planned `python main.py --mode swing`.

Swing mode will be a separate multi-day equity strategy engine. It
will use completed daily candles, weekly trend confirmation, ATR-based
risk, and a dedicated swing ledger so swing positions are kept separate
from long-term holdings.

> **Status.** This is a strategy/design document. No swing runtime has
> shipped yet. The implementation backlog is tracked in
> [SWING_ROADMAP.md](SWING_ROADMAP.md).

Sister docs:

- Portfolio analyser: [ANALYZE_STRATEGY.md](ANALYZE_STRATEGY.md)
- Intraday trading: [TRADE_STRATEGY.md](TRADE_STRATEGY.md)
- Tax guide: [TRADE_TAX_GUIDE.md](TRADE_TAX_GUIDE.md)

---

## 0. One-line summary

> Swing mode scans NSE equities after market close for multi-day
> setups, ranks candidates using deterministic daily/weekly technical
> signals, computes entry/stop/target/quantity from risk rules, reviews
> open swing positions once per day, and writes a clean action report.
> AI is optional and only adds qualitative thesis/risk context.

Default mode should be NoAI and report-only. Live order placement is a
later explicit `--execute` phase after CNC/GTT safety and ledger
reconciliation are implemented.

---

## 1. What swing mode is, and what it is not

Swing mode is for trades held across multiple days. Typical holding
period: **2 trading days to 8 weeks**.

It is not:

- Intraday trading with slower polling.
- A replacement for long-term portfolio analysis.
- A system that sells any stock simply because it exists in Zerodha
  holdings.
- A short-selling cash-equity strategy. Overnight cash-equity shorts
  are out of scope.
- F&O.

It is:

- Daily-candle-first.
- Long-only initially.
- Risk-first.
- Ledger-backed.
- NoAI deterministic by default.
- AI-assisted only for qualitative overlays.

---

## 2. Operator workflow

### 2.1 Normal daily use

Run swing mode **after market close**, not at market open:

```bash
python main.py --mode swing
```

Best run window: **3:45 PM to 6:00 PM IST**.

The reason is simple: swing signals use completed daily candles. At
market open the daily candle is incomplete, so using it as a primary
signal produces false breakouts, false reversals, and noisy RSI/volume
readings.

The daily run should:

1. Login to Zerodha.
2. Read `data/swing.db` open positions.
3. Fetch current holdings and live prices for reconciliation.
4. Review open swing positions.
5. Fetch daily/weekly candle history.
6. Scan the configured universe.
7. Compute entry, stop, target, quantity, and risk for candidates.
8. Persist every candidate and action.
9. Write `reports/swing/<YYYY>/<MM>/swing_report_DD.txt` and JSON.

### 2.2 Should it run daily?

Yes. Swing mode should be run **once per trading day** after close.

Daily is enough because:

- The primary signal changes only when the daily candle closes.
- Stops and targets are planned around daily/ATR structure, not ticks.
- Over-reviewing multi-day trades creates churn.

Skipping a day is survivable if orders/stops are already placed, but
the report may miss a stop-tighten or exit recommendation. The clean
operational habit is one end-of-day run.

### 2.3 Should it be started at market open?

Not for the main scan.

A future execution-only morning check may be useful for:

- Placing planned CNC limit orders after opening volatility settles.
- Detecting large gap opens.
- Cancelling stale orders.
- Warning that a planned entry is no longer valid.

But market-open should not be the main swing decision point. If a
morning run is added, it should be a separate execution/reconciliation
step, not a fresh strategy scan on incomplete candles.

---

## 3. Position isolation from long-term holdings

This is the most important safety rule.

Intraday mode was naturally isolated because it used MIS positions.
Swing mode will use CNC/delivery positions, which appear inside the
same Zerodha demat holdings as long-term investments. Therefore swing
mode needs its own ledger and must never manage all Zerodha holdings
for a symbol by default.

### 3.1 Source of truth

Swing-managed quantity comes from:

```text
data/swing.db::swing_positions.managed_qty
```

Zerodha holdings are used only to verify that enough shares exist to
execute a planned swing exit.

### 3.2 Example

User has long-term holding:

```text
INFY total Zerodha holding: 100 shares
```

Swing mode later buys:

```text
INFY swing entry: 10 shares
```

Zerodha now shows:

```text
INFY total holding: 110 shares
```

Swing ledger shows:

```text
INFY swing-managed qty: 10 shares
```

If swing exit fires, the maximum sell quantity is 10. The remaining
100 shares are long-term/unmanaged and must not be touched.

### 3.3 Overlapping symbols

It is allowed for the same symbol to be both:

- A long-term portfolio holding.
- A swing-managed position.

But every report and dashboard view must show the split:

| Quantity | Meaning |
|----------|---------|
| Zerodha total qty | Full demat quantity for the symbol |
| Swing-managed qty | Quantity recorded in `swing_positions` |
| Unmanaged/long-term qty | Zerodha total qty - swing-managed qty |

### 3.4 Fail-closed reconciliation

Before any live swing exit, the tool must verify:

1. The position exists in `swing_positions` and status is OPEN.
2. `managed_qty > 0`.
3. Zerodha holding quantity is at least `managed_qty`.
4. Planned sell quantity is not greater than `managed_qty`.
5. Any mismatch is marked MANUAL_REVIEW.

No automatic order should be placed when the ledger and broker disagree.

---

## 4. Data inputs

### 4.1 Market data

Primary inputs:

- Zerodha holdings.
- Zerodha live quotes.
- Zerodha historical daily candles.
- NIFTY 50 daily candles.
- Sector map from existing `modes/trade/stock_scanner.py`.

Optional later inputs:

- Quarterly fundamentals from analyse seed files.
- News/catalyst context from AI overlay.
- Results/earnings calendar.
- Corporate action detection.

### 4.2 Candle windows

Swing mode should fetch enough daily history for:

| Need | Minimum |
|------|---------|
| RSI(14), ATR(14) | 30 trading days |
| SMA-50 | 80 trading days |
| SMA-200 | 260 trading days |
| 52-week high/low | 252 trading days |
| Weekly trend | 400+ trading days preferred |

Recommended fetch window: 500 trading days when available.

---

## 5. Setup types

Swing mode should start with four setup families.

### 5.1 Breakout

A stock is breaking out of a recent range.

Candidate signs:

- Close above 20-day or 50-day high.
- Volume greater than 1.5x 20-day average.
- Price above SMA-50 and SMA-200.
- Relative strength versus NIFTY is positive.
- Weekly trend is not bearish.

Avoid when:

- Price is already too extended from EMA-20/SMA-50.
- Breakout candle is a wide exhaustion candle with weak close.
- R:R to nearest target zone is below 2R.

### 5.2 Pullback in uptrend

A strong stock pulls back to a reasonable buy zone.

Candidate signs:

- Price above SMA-50 and SMA-200.
- Faster average above slower average, for example SMA-20 > SMA-50.
- Pullback toward EMA-20 or SMA-50.
- RSI between 40 and 60.
- Bullish reversal candle or close back above short average.

Avoid when:

- Close breaks below SMA-50 with volume expansion.
- Weekly trend turns down.
- Pullback is actually a trend break.

### 5.3 Trend continuation

A trend already exists and is continuing without being too extended.

Candidate signs:

- SMA-20 > SMA-50 > SMA-200.
- Higher highs and higher lows.
- Weekly trend aligned.
- Up days show stronger volume than down days.
- Price is not more than a configured extension threshold above EMA-20.

### 5.4 Support reversal

A stock bounces from a major support area.

Candidate signs:

- Near SMA-200, prior swing support, or 52-week support zone.
- Bullish reversal pattern.
- RSI recovering from oversold.
- Reward to prior resistance is at least 2R.

This setup should receive smaller default size than breakout/pullback
because catching reversals is lower-confidence than trading an intact
trend.

---

## 6. Scoring model

NoAI scoring should be transparent and explainable.

Suggested first-pass score components:

| Component | Direction |
|-----------|-----------|
| Daily trend above SMA-50/SMA-200 | Positive |
| Weekly trend aligned | Positive |
| Breakout above 20/50-day high | Positive |
| Pullback to EMA-20/SMA-50 in uptrend | Positive |
| Relative strength vs NIFTY | Positive |
| Volume confirmation | Positive |
| Bullish candle pattern at support | Positive |
| Price too extended from moving average | Negative |
| R:R below 2.0 | Hard reject |
| Below SMA-200 without reversal setup | Negative or reject |
| Earnings/event risk unknown | Warning or reject later |

The report should show both:

- Numeric score.
- Human-readable reasons.

Example:

```text
TCS - PULLBACK_UPTREND - Score 8.2
Why: Above SMA-200, weekly uptrend intact, pullback to EMA-20,
RSI 48, bullish engulfing, R:R 2.6.
```

---

## 7. Risk model

Swing mode is risk-first. A candidate without a clean stop is rejected.

### 7.1 Stop

Use the more sensible structural stop from:

- Below recent swing low.
- Below support zone.
- `entry - 2 * ATR(14)` for long trades.
- Below SMA-50/SMA-200 only when that level is structurally relevant.

The stop should not be so tight that normal daily noise triggers it,
and not so wide that position size becomes meaningless.

### 7.2 Target

Minimum target rule:

```text
R:R >= 2.0
```

Preferred:

```text
R:R >= 2.5
```

Target sources:

- Prior swing high.
- Measured range breakout projection.
- 52-week high zone.
- ATR multiple.
- Trailing stop for trend continuation.

### 7.3 Position size

Position size should be based on rupee risk, not equal allocation.

Formula:

```text
risk_per_share = abs(entry_price - stop_price)
risk_budget = swing_capital * risk_per_trade_pct
qty = floor(risk_budget / risk_per_share)
```

Suggested defaults:

| Setting | Default |
|---------|---------|
| Risk per trade | 0.5% of swing capital |
| Max single position value | 10-15% of swing capital |
| Max total open risk | 5% of swing capital |
| Max sector exposure | 25-30% of swing capital |
| Minimum R:R | 2.0 |

### 7.4 Portfolio-level risk checks

Before accepting a candidate:

- Reject if total open swing risk would exceed max risk.
- Reject if sector exposure would exceed cap.
- Reject if symbol already has an open swing position.
- Warn if symbol overlaps with a large long-term holding.
- Reject if quantity rounds to zero.

---

## 8. Position review and exits

Every run reviews open swing positions before scanning new entries.

### 8.1 Review actions

| Action | Meaning |
|--------|---------|
| HOLD | Setup intact; no change |
| TIGHTEN_STOP | Move stop up because price advanced or structure improved |
| PARTIAL_EXIT | Book part of position near target/extension |
| FULL_EXIT | Exit planned due to stop, trend break, target, or thesis failure |
| WATCH | No action, but risk/thesis warning exists |

### 8.2 Exit triggers

Exit or plan exit when:

- Price closes below stop.
- Price hits target zone.
- Trailing stop is hit.
- Daily close breaks SMA-50 after a trend setup.
- Weekly trend breaks.
- No progress after configured time stop, e.g. 10 trading days.
- AI/news overlay flags a material thesis break, if AI mode is used.

### 8.3 Stop movement

Initial stop can only tighten, never loosen, unless the user explicitly
marks a manual override.

Typical trailing rule:

- At +1R: move stop toward breakeven or reduce risk.
- At +2R: partial exit or trail below recent swing low.
- For strong trends: trail under EMA-20/SMA-50 or higher swing lows.

---

## 9. AI overlay

AI mode is optional:

```bash
python main.py --mode swing --ai
```

Claude receives fixed NoAI data and fills qualitative fields only:

- Thesis.
- Risks.
- Recent news/catalysts.
- Peer comparison.
- Why this setup may fail.
- Whether the technical setup conflicts with fundamentals/news.

AI must not invent prices, stops, quantities, or R:R. Those come from
NoAI math.

---

## 10. Reports

Swing report files:

```text
reports/swing/<YYYY>/<MM>/swing_report_DD.txt
reports/swing/<YYYY>/<MM>/swing_data_DD.json
```

Sections:

1. Header: run time, mode, universe, market regime.
2. Swing book: open positions and daily action.
3. Risk summary: deployed capital, open risk, sector exposure.
4. New candidates: accepted setups with entry/stop/target/qty.
5. Rejections: top candidates rejected and why.
6. AI overlay: optional qualitative context.
7. Manual action checklist.

---

## 11. Dashboard surface

Later dashboard page:

```text
/swing
```

Expected sections:

- Open swing positions.
- Today's required actions.
- New candidates.
- Candidate detail chart with entry/stop/target markers.
- Sector exposure.
- Open risk at stop.
- Realised swing P&L.
- Run swing scan button.

The dashboard must clearly distinguish:

- Long-term portfolio holdings from analyse mode.
- Intraday P&L from trade mode.
- Swing positions from swing mode.

---

## 12. CLI plan

Initial CLI shape:

```bash
python main.py --mode swing
python main.py --mode swing --ai
python main.py --mode swing --nifty 50
python main.py --mode swing --nifty 100
```

Future execution flags:

```bash
python main.py --mode swing --execute
python main.py --mode swing --dryrun
```

`--execute` must remain blocked until CNC/GTT wrappers and ledger
reconciliation are complete.

---

## 13. Hard safety rules

1. Swing mode never uses MIS.
2. Swing mode never squares off all positions at 3:10 PM.
3. Swing mode never sells more than `swing_positions.managed_qty`.
4. Swing mode never treats all Zerodha holdings as swing positions.
5. Swing mode fails closed on broker/ledger quantity mismatch.
6. Swing mode starts report-only; live execution is explicit.
7. NoAI numeric fields are the source of truth.
8. AI is qualitative only.
9. Every candidate and rejection is persisted.
10. Backtest/replay comes before live execution.

---

## 14. Glossary

- **Swing trade** - A trade held for multiple days to weeks, aiming to
  capture a price swing rather than an intraday move.
- **CNC** - Cash and Carry / delivery product. Unlike MIS, it can be
  held overnight.
- **MIS** - Margin Intraday Square-off. Intraday-only product; not used
  by swing mode.
- **GTT** - Good Till Triggered order. Used to place longer-lived stop
  or target instructions with the broker.
- **OCO** - One Cancels Other. A paired stop/target structure where
  execution of one cancels the other.
- **ATR** - Average True Range. Measures recent volatility; used for
  stop distance and position sizing.
- **R** - Initial risk per share or per trade. If entry is Rs.100 and
  stop is Rs.95, risk is Rs.5 per share. A target at Rs.110 is 2R.
- **Relative strength** - Stock performance compared with NIFTY over a
  lookback period.
- **Managed quantity** - The exact quantity swing mode is allowed to
  manage for a symbol, stored in `data/swing.db`.
- **Unmanaged quantity** - Shares in Zerodha holdings that are not part
  of the swing ledger, usually long-term portfolio holdings.

---

## 15. Where code will live

| You want to change | Planned file |
|--------------------|--------------|
| Swing orchestrator | `modes/swing/manager.py` |
| Candidate scanner | `modes/swing/scanner.py` |
| Setup definitions | `modes/swing/signals.py` |
| Stop/target/qty math | `modes/swing/risk.py` |
| Dataclasses/schema types | `modes/swing/types.py` |
| SQLite persistence | `modes/swing/persistence.py` |
| Report layout | `modes/swing/report.py` |
| Claude overlay prompt | `modes/swing/ai_overlay.py` |
| Zerodha CNC/GTT wrappers | `core/zerodha_client.py` |
| Dashboard page | `modes/dashboard/swing_page.py` |
| Roadmap | `docs/SWING_ROADMAP.md` |