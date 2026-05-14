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
> open swing positions once per day, and shows a dashboard action table.
> The user manually executes broker actions, then clicks Done with the
> executed quantity and price so the tool can track the swing lot.
> The dashboard polls Zerodha live quotes for displayed prices and P&L.
> AI is optional, user-initiated only, and only adds qualitative
> thesis/risk/news context.

Default mode is NoAI, dashboard-first, and **report-only by
permanent design** (decision recorded 2026-05-14). The bot **never
places broker orders** for swing trades; the user does that manually
on Zerodha Kite and comes back to click Done / Mark-Exit-Done with
the actual fill numbers. Items that previously tracked execution
automation (CNC order wrappers S12, GTT/OCO S13, broker
reconciliation S14, ledger isolation S5) are in the Removed section
of [SWING_ROADMAP.md](SWING_ROADMAP.md).

Automatic scans are always NoAI. AI runs happen only when the user
explicitly clicks the AI control or passes `--ai` in the terminal.

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

Use the local dashboard **after market close**, not at market open:

```bash
python main.py --mode dashboard
```

Then open:

```text
/swing
```

The terminal equivalent is:

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
8. Persist every candidate and dashboard action item.
9. Show broker-entry instructions and a priority-sorted entry
  recommendation table with live/latest price, entry, stop, target,
  suggested quantity, risk, R:R, setup/action, and reason.
10. Write `reports/swing/<YYYY>/<MM>/swing_report_DD.txt` and JSON.

After the user manually follows a report action in Zerodha, they return
to `/swing`, click Done on that action, and enter the executed quantity
and executed price. That confirmation is what creates or updates the
swing-managed position in the ledger.

Once an entry is confirmed, it moves out of the top recommendation
table and into the open swing book below it. The open swing book keeps
polling Zerodha live prices and shows the current P&L, R multiple, stop,
target, and latest exit/hold recommendation.

The recommendation table is sorted by priority. Rank 1 is the strongest
candidate after score, R:R, liquidity, risk budget, sector/open-risk
constraints, and warnings are considered.

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

Before market close, the scan must not run. The dashboard and CLI should
show a clear "wait for market close" message instead of using partial
daily candles.

### 2.4 How manual actions become tracked positions

The dashboard action table is the operator's work queue. It can contain
actions such as:

| Action | User does outside tool | User confirms in tool |
|--------|------------------------|-----------------------|
| ENTRY | Buy CNC shares in Zerodha | Click Done, enter qty and price |
| TIGHTEN_STOP | Move stop/GTT manually | Click Done, enter confirmed stop price |
| PARTIAL_EXIT | Sell part of the managed quantity | Click Done, enter qty and price |
| FULL_EXIT | Sell the managed quantity | Click Done, enter qty and price |
| SKIP | Take no broker action | Click Skip with optional reason |

Important rule:

```text
No Done confirmation = no swing-managed lot is created or changed.
```

This means a candidate can be recommended without being tracked as an
open swing position. Tracking starts only when the user confirms the
manual execution details through the dashboard or equivalent terminal
command.

Confirmed open swing positions appear below the report table as the
open swing book. They are reviewed on each later run and show live/latest
price, managed quantity, entry, stop, target, unrealised P&L, R multiple,
age, and the current action such as HOLD, TIGHTEN_STOP, PARTIAL_EXIT,
FULL_EXIT, or WATCH.

The open swing book has an Exit/Mark Exit Done control. In report-only
mode, this is not a broker sell button. It confirms that the user has
already exited manually, asks for executed exit quantity and price,
computes gross P&L, delivery/regulatory charges, and net P&L, then
closes or reduces the tracked swing position. The net result contributes
to the realised swing P&L summary at the top of `/swing`.

### 2.5 Automatic EOD scan behavior

The dashboard should reduce the workflow to one daily habit:

- If the dashboard is already running at 15:30 IST or later, it should
  auto-submit one NoAI swing scan for the trading day if no run exists.
- If the dashboard was not open and the user navigates to `/swing` at
  16:30 IST, the page should auto-trigger today's NoAI scan if it has
  not already run.
- If a NoAI run is already completed or in progress for the trading day,
  the dashboard must not start another automatic NoAI run.
- AI is separate: if today's run is NoAI and the user explicitly clicks
  Run AI swing analysis, run the analysis again with AI overlay and
  update the AI fields. Do not auto-run AI from timers or page-open logic.
- A manual Run scan button remains available after market close.
- Before market close, the button and CLI should say wait for market
  close and refuse to analyse incomplete daily data.

### 2.6 Broker entry instructions

Above the recommendation table, the dashboard should show a compact
instruction card. The card exists so the user can take the recommendation
in Zerodha without guessing the order form fields.

Baseline manual entry steps:

1. Open Zerodha and choose the recommended symbol on NSE.
2. Use `BUY` with product `CNC` / delivery. Do not use MIS, margin
   intraday, futures, options, or short selling.
3. Use the dashboard's suggested quantity.
4. Use the recommended entry price as the limit/trigger reference.
5. Set or note the stop plan shown by the dashboard. If Zerodha supports
   GTT/OCO for that symbol, prefer broker-side stop/target protection.
6. After the broker order is executed, return to `/swing`, click Done,
   and enter the executed quantity and actual average price.

If Zerodha supports setting an entry trigger for the next market open
for the relevant product, the dashboard should show a second checklist:

1. Place the supported AMO/GTT/trigger-limit style order in Zerodha as
   `BUY CNC` for the suggested quantity.
2. Use the suggested trigger/limit price and validity allowed by Zerodha.
3. Set the stop/GTT plan if the broker flow supports it.
4. In the morning, let Zerodha execute the order if the trigger is met.
5. Return to `/swing`, click Done, and enter the actual executed
   quantity and price so tracking starts from broker reality.

The dashboard must not assume the trigger filled. Tracking starts only
after Done/confirm records the actual fill details.

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

A new swing position is created only from a confirmed action, for
example a dashboard Done click or CLI confirmation that records the
executed quantity and price. A scan recommendation by itself is not a
position.

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
- Zerodha live quotes for displayed prices and P&L.
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

### 5.5 Dip-buy (52-week-high reference)

A stock has fallen meaningfully from its **rolling 52-week-high
close** and is bought as a fixed-rupee position with a fixed-percentage
take-profit.

> Originally shipped 2026-05-14 against the **all-time** high
> (`max(closes)` over the full lookback). Switched the same day to
> the rolling 52-week high (`max(closes[-N:])` where N defaults to
> `Config.SWING_DIP_LOOKBACK_DAYS = 252`). Rationale: the 52w high
> resets every year so the trigger is responsive to the current
> regime — a stock that's been declining for 3 years no longer sits
> silently 60 % below an old ATH ignored by the gate. The 52w high
> is also the canonical large-cap-investor anchor (it's the standard
> breakout-watch level for trend followers), so using it as the
> single reference removes a buy-side / trend-side blind spot in
> one window.

Mechanical rule (the entire strategy):

1. Track the **rolling 52-week-high** of the daily close for each
   stock — `max(closes[-N:])` where N defaults to
   `SWING_DIP_LOOKBACK_DAYS = 252` trading bars (~52 weeks).
   Setting N to a larger number widens the reference window
   (`750` ≈ 3 years, `3650` ≈ 10 years for the legacy ATH behaviour).
2. Enter Rs.`SWING_DIP_BUY_AMOUNT` (default Rs.10,000) on the first
   close that is at least `SWING_DIP_PCT` (default 18 %) below
   that 52w high.
3. Exit the entire position on the first close that is at least
   `SWING_DIP_TARGET_PCT` (default 12 %) above the buy price.
4. After the exit, the name is immediately re-eligible for a fresh
   buy if the rule fires again from a new (or unchanged) 52w high.

Calibration. The defaults were picked from a 10-year, 121-combo X/Y
backtest over the current NIFTY 50, run in the standalone
[market-research](https://github.com/yash040599/market-research)
repo. The original heatmap was computed against the **ATH** reference;
the post-COVID 5-year slice of the same data shows the **52w-high**
variant of the rule tracks the ATH variant within ~150 bps XIRR in
the (X∈[16,20], Y∈[10,13]) sweet-spot — which is why the (18, 12)
default carries over. **Every cell of the X∈[10,20] × Y∈[10,20] grid
was profitable** on XIRR; the sweet-spot corner is X=18–20 %,
Y=10–13 %:

| | Best XIRR (ATH backtest) | NIFTY-50 reference |
|---|---|---|
| (X=20, Y=10) | 29.5 % | 13-14 % CAGR |
| (X=18, Y=12) **(default)** | 25.6 % | |
| (X=10, Y=10) (worst) | 20.0 % | |

Default (18, 12) was chosen over (20, 10) because dip frequency at
X=18 is roughly twice that at X=20 (more shots over a multi-year
horizon) and Y=12 retains comfortable headroom over real-world
charges + execution noise on a Rs.10,000 ticket. See `config.py`'s
"Swing — Dip-buy parameters" block for the verbatim rationale, and
the `swing-review.md` skill (Step 7) for the procedure that re-runs
the backtest against the 52w-high variant before the next live
parameter shift.

Two non-obvious things this setup deliberately does *not* do:

- **No fundamental filter.** The strategy buys names purely on
  price-from-52w-high; quality is provided by the universe
  (NIFTY 50 / 100). The reviewer (and the AI overlay when enabled)
  is responsible for catching dip-from-corp-action false positives —
  splits, bonuses, demergers — before confirming.
- **No simultaneous-position cap.** Multiple dip-buy positions can
  be open at once. The 10y ATH backtest's peak simultaneous capital
  was ~₹4 lakh on a 48-stock universe at ₹10k a clip — that's
  the cash buffer the user must keep available. The 52w-high variant
  fires *less often* than the ATH variant (yearly reference resets
  retire stale dips), so realised peak capital should be lower.

Candidate signs (live):

- Close ≤ rolling-52w-high × (1 − `SWING_DIP_PCT`/100).
- Symbol not already in the open swing book.
- Symbol not already accepted by the technical scanner this run
  (the technical scanner runs first and gets first dibs on the
  name; the unified `priority_rank` puts technical above dip-buy
  in the dashboard table).

Risk plan baked into the candidate:

- Stop = entry × 0.90 (10 % hard floor below the dip-buy).
- Target = entry × (1 + `SWING_DIP_TARGET_PCT` / 100).
- Quantity = `SWING_DIP_BUY_AMOUNT` ÷ entry (integer floor, min 1).

### 5.6 52-week-high proximity bonus / penalty (cross-setup modifier)

Independent of the four base setups + the dip-buy strategy, every
candidate's score is adjusted by a 52-week-high proximity component
computed in `signals.py::score_52w_high_proximity()`:

| Distance from 52w high | Bonus |
|---|---|
| Closing AT or ABOVE the prior 52w high (fresh-high day) | +2.0 |
| Within 1.5 % below | +1.5 |
| Within 3 % below | +1.0 |
| Within 5 % below | +0.5 |
| > 5 % below | 0.0 |

Wired in `classify_setup()` as:

- **Bonus** for continuation setups (`BREAKOUT`, `TREND_CONTINUATION`)
  — a stock perched near its 52w high is exactly what these setups
  are looking to buy. Most institutional momentum buyers add at the
  52w break, so positions stalling within ~3% of the 52w high get
  continuation-priced before the actual breakout candle.
- **Penalty (same magnitude)** for mean-reversion setups
  (`PULLBACK_UPTREND`, `SUPPORT_REVERSAL`) — a "pullback" or "reversal"
  trigger that fires within 3 % of the 52w high is by definition not a
  pullback, it's an extended continuation in disguise. Penalising it
  prevents fully-extended names slipping through under the wrong
  label.

Magnitude (+0.5 to +2.0) was picked to match the existing
volume/relative-strength bumps so adding the modifier never
single-handedly flips a setup's verdict.


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

Exit or plan exit using an industry-standard exit stack. The tool
should not simply say HOLD until a hard stop or distant target. It
should review every confirmed swing position after each completed daily
candle and recommend exit/partial-exit/stop-tighten when the setup has
changed.

Exit or plan exit when:

- Price closes below stop.
- Price hits target zone.
- ATR or swing-low trailing stop is hit.
- Daily close breaks SMA-50 or EMA-20 after a trend setup, depending
  on setup type.
- Weekly trend breaks.
- Relative strength versus NIFTY deteriorates materially.
- Bearish reversal candle appears at resistance with high volume.
- Higher-high/higher-low structure breaks.
- No progress after configured time stop, e.g. 10 trading days.
- Gap-down or event risk invalidates the setup.
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

Run semantics:

- Default scans are NoAI.
- Automatic 15:30/page-open scans are NoAI, even if the dashboard has an
  AI checkbox available.
- AI runs require the user to tick AI mode/click Run AI swing analysis,
  or pass `--ai` in the terminal.
- If today's last run is NoAI and the user requests AI, rerun for the
  same trading day with AI overlay and update the candidate/action AI
  fields.
- If today's last run is already AI, simply opening the page must not
  rerun AI. A later force-rerun control can be explicit.
- AI can add news/catalyst context, risk warnings, thesis, and ranking
  notes, but numeric fields remain NoAI-owned.

### 9.1 AI cost cap (`SWING_AI_MAX_CANDIDATES`)

A NIFTY 100 swing scan after a market correction can flag dozens of
ATH-dip candidates. Without a cap, the AI overlay would loop through
every accepted candidate at `Config.CLAUDE_COST_PER_CALL` rupees a
shot — comfortably north of Rs.150 per scan on the Pro plan.

Origin (2026-05-14): the user ran AI swing mode once, watched it
loop "no stop" for several minutes, Ctrl+C'd it, and got no report.
Two structural fixes shipped together:

1. `Config.SWING_AI_MAX_CANDIDATES` (default 15) caps how many
   accepted candidates the overlay will Claude. The cap selects
   the top-N by unified `priority_rank` (technical first, ATH-dip
   after) so the budget always lands on the strongest signals.
2. The manager now writes a *pre-AI snapshot* of the run
   (candidates + actions + positions) to `data/swing.db` BEFORE
   the AI overlay starts. A Ctrl+C / network failure mid-overlay
   therefore still produces a saved scan + a written report — the
   AI fields are simply blank for the candidates that didn't get
   their turn.

Worst-case live cost preview (visible on the dashboard `/swing` page
above the **Run Scan** button):

| Click | Calls | Cost (Pro plan, ~Rs.3/call) |
|---|---|---|
| Single-stock AI on a swing detail page | 1 | ~Rs.3 |
| Full scan with AI overlay (capped at 15) | ≤ 15 | ~Rs.45 |
| Full scan WITHOUT the cap (~50 accepts after a correction) | ~50 | ~Rs.150 |

To widen the cap, edit `Config.SWING_AI_MAX_CANDIDATES`. To narrow
it (e.g. on the Free Haiku plan you may want N=20 for ~Rs.20),
same knob — no other code edit required.

The dashboard JS confirm dialog echoes the same numbers from the
server-side Config so the click and the run agree on the worst
case before the run starts.

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
3. Realised swing P&L: gross P&L, charges, net P&L.
4. Risk summary: deployed capital, open risk, sector exposure.
5. New entry candidates: accepted setups with live/latest price,
   entry/stop/target/qty.
6. Rejections: top candidates rejected and why.
7. AI overlay: optional qualitative context.
8. Manual action checklist.

---

## 11. Dashboard surface

Primary dashboard page:

```text
/swing
```

Expected sections:

- Realised swing P&L summary: gross P&L, charges, net P&L.
- Live quote timestamp and stale-token warning when Zerodha polling fails.
- Run scan button. Before market close it must say wait for market
  close and refuse to scan incomplete daily candles.
- AI mode checkbox or Run AI swing analysis button. It is off by default
  and never used by automatic scans.
- Broker-entry instruction card above the recommendation table, covering
  `BUY`, `CNC`/delivery, NSE symbol, suggested quantity, entry/trigger
  price, stop/GTT reminder, and Done confirmation.
- Optional Zerodha trigger checklist when AMO/GTT/trigger-limit entry is
  supported for next market open.
- Today's entry recommendation table with action id, symbol, setup, score,
  live/latest price, entry, stop, target, suggested quantity, risk,
  R:R, and reason.
- Pending action controls: Done, Skip, Manual review.
- Done form asking at minimum executed quantity and executed price for
  entry/exit actions, or confirmed stop price for stop-only actions.
- Open swing positions below the recommendation table, with live Zerodha
  current price, unrealised P&L, R multiple, stop, target, age, and
  current exit/hold recommendation.
- Exit/Mark Exit Done control on each open swing row. It closes tracking
  only after the user enters executed exit quantity and price.
- Candidate detail chart with entry/stop/target markers.
- Sector exposure.
- Open risk at stop.

The dashboard must clearly distinguish:

- Long-term portfolio holdings from analyse mode.
- Intraday P&L from trade mode.
- Swing positions from swing mode.

The dashboard may write to `data/swing.db` for action confirmations,
skips, and manual-review flags. It **never** places broker orders
(swing mode is permanently report-only; the user trades on Kite
manually).

The dashboard should poll Zerodha live quotes for symbols visible in
the recommendation table and open swing book. Displayed prices and P&L
come from the latest broker quote, with an as-of timestamp. Signal
generation still waits for completed daily candles.

Recommendations are priority-sorted. Rank 1 should be displayed first.

---

## 12. CLI plan

Initial CLI shape:

```bash
python main.py --mode swing
python main.py --mode swing --ai
python main.py --mode swing --nifty 50
python main.py --mode swing --nifty 100
```

Manual action parity with the dashboard:

```bash
python main.py --mode swing --actions
python main.py --mode swing --positions
python main.py --mode swing --confirm <ACTION_ID> --qty 10 --price 1450.50
python main.py --mode swing --confirm <ACTION_ID> --stop 1420.00
python main.py --mode swing --close-position <POSITION_ID> --qty 10 --price 1510.25
python main.py --mode swing --live
python main.py --mode swing --skip <ACTION_ID> --reason "not taken"
python main.py --mode swing --manual-review <ACTION_ID> --reason "broker mismatch"
```

Dashboard buttons and terminal commands must call the same service
layer. There should not be separate dashboard-only logic for creating
or updating swing-managed lots.

All automated dashboard-triggered scans use the non-AI form. `--ai` is
manual only.

> **No `--execute` flag is planned.** Swing mode is report-only by
> design (see SWING_ROADMAP Removed section for the full rationale).
> If the operator wants to automate execution they should switch to
> `--mode trade` for intraday or use the broker UI for delivery.

---

## 13. Hard safety rules

1. Swing mode never uses MIS.
2. Swing mode never squares off all positions at 3:10 PM.
3. Swing mode never sells more than `swing_positions.managed_qty`.
4. Swing mode never treats all Zerodha holdings as swing positions.
5. Swing mode fails closed on broker/ledger quantity mismatch.
6. **Swing mode is report-only by permanent design** — never places
   broker orders. Operator trades manually on Kite.
7. NoAI numeric fields are the source of truth.
8. AI is qualitative only.
9. Every candidate and rejection is persisted.
10. Backtest/replay (S11) is purely a strategy-validation aid; it
    runs offline against historical data and never touches the
    broker.
11. A recommended entry/exit becomes tracked only after Done/confirm
    records executed quantity and price; a stop-only action needs the
    confirmed stop price.
12. Dashboard action buttons write only to the swing ledger
  (`data/swing.db`). They never call the broker — execution is
  manual on Kite, by permanent design.
13. Displayed current prices and P&L must come from Zerodha live quotes
  whenever the dashboard has a valid token.
14. EOD scans must refuse to run before market close.
15. Realised swing P&L must be net of delivery/regulatory charges.
16. Entry recommendations must be priority-sorted.
17. Dashboard instructions must tell the user to use `BUY` + `CNC` /
  delivery, never MIS/F&O, and must explain the Done confirmation.
18. AI is never automatic; the user must explicitly initiate every AI run.

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