# Ideations -- Future Money Engines

> **Status:** Planning / research only -- no code changes implied.
> **Context:** V2 intraday improvements stay in [TRADE_ROADMAP.md](TRADE_ROADMAP.md).
> This file is for broader future product directions that can make money beyond
> the current intraday bot.

---

## Hard Constraints Added 2026-04-28

These constraints override older V3/Future ideas in this file.

1. **No F&O ideas.** Do not propose options, futures, options-chain execution,
   covered calls, futures hedges, options selling, pledged-margin F&O, or
   overnight shorting workflows.
2. **Phase 1 remains FYI-only.** It may analyse the existing Zerodha portfolio,
   but it must not generate executable orders for long-term holdings.
3. **Long-term invested stocks are not touched.** New money engines must use a
   separate capital bucket and a separate ledger/reporting namespace.
4. **Scanner/paper mode first.** Any new engine starts as recommendation-only
   and paper-tracked for at least 30-60 days before live order placement.
5. **Execution through Kite API still needs the static-IP VM.** Advisory scans
   can run anywhere if they do not use Kite APIs; automated order placement must
   run from a whitelisted machine/IP.

---

## In Plain English -- The Three Broad Ideas

The repo now has three broad future tracks:

| Track | Purpose | Execution Boundary |
|---|---|---|
| **A1. V3 AI Intraday Research** | Make the existing intraday bot smarter using ML/news/order-book context. | Still Phase 2 intraday; no F&O. |
| **A2. Delivery Swing Trading Engine** | Buy separate CNC positions, hold for days/weeks, sell on target/stop/trend failure. | Separate short/medium-term bucket; never touches Phase 1 holdings. |
| **A3. ETF Rotation Engine** | Rotate a separate capital bucket between liquid ETFs based on trend/momentum. | Separate CNC ETF bucket; low-turnover allocation engine. |

The important pivot: at Rs.50k, pure intraday fights regulatory charges,
brokerage, spread, and slippage. The next money-making experiments should
therefore emphasize **lower turnover**, **larger expected move per decision**,
and **separate accounting**.

---

## Existing Assets We Can Reuse

| Asset | Reuse |
|---|---|
| `core/zerodha_client.py` | Login, funds, holdings, quotes, historical candles, order placement. Needs `product=CNC` support before live delivery/ETF execution. |
| `shared/technical_indicators.py` | Trend, RSI, ADX, VWAP, and other indicators can be reused on daily/60-min candles. |
| `shared/candle_cache.py` | Historical candle store can support swing/ETF scans and paper backtests. |
| `modes/trade/report_writer.py` | Pattern for writing JSON/text reports can be copied into `reports/swing/` and `reports/rotation/`. |
| `modes/dashboard/` | Existing read-only analytics layer can grow separate panels for swing and rotation P&L. |
| Tax ledger/scripts | Delivery and ETF trades become capital gains, not speculative intraday income; they need separate tax handling from MIS trades. |

---

## A1 -- V3 AI Intraday Research (Existing Future Track)

### Plain English

V3 keeps the current intraday engine but changes what AI is used for. Today V2
mostly uses deterministic formulas. V3 should use ML for numeric pattern
learning and Claude for narrative/context reading.

This track is still about improving Phase 2 intraday. It is **not** the main
answer to the small-budget charge problem, because it remains on the same
intraday cost battlefield.

### What Stays In Scope

1. **ML scoring model** -- learn weights from historical trades instead of
   hand-tuned indicator weights.
2. **Claude for news/sentiment** -- read headlines, earnings announcements,
   corporate action text, and macro context.
3. **Order-book/depth context** -- cash-equity depth/imbalance only, no F&O
   chain or options OI.
4. **Multi-asset context** -- use gold, USD/INR, crude, global index cues, or
   sector ETFs as context; do not trade derivatives from this module.

### What Is Explicitly Out Of Scope

- Options chain signals.
- Expiry-day max pain/PCR/OI strategies.
- Covered calls.
- Futures hedges.
- Options strategy lab.
- Pairs trading that needs a short/futures leg.

### A1.1 ML Scoring Model

**What it is:** Replace the hand-tuned composite score with a trained tabular
model that outputs probability of profit or expected value.

**Approach:**

- Model: XGBoost / LightGBM / sklearn HistGradientBoosting.
- Features: existing indicators, candle-pattern outputs, time of day, day of
  week, NIFTY regime, sector strength, score history, entry-delay movement,
  spread, impact cost, and opening-gap features.
- Label: trade outcome after charges, plus exit reason and max adverse /
  favorable excursion if available.
- Output: probability of profitable trade, expected net Rs., or expected R.

**Why it might help:**

- Learns nonlinear interactions that fixed weights miss.
- Tells us which indicators actually matter.
- Can identify dead-weight rules that add churn but no edge.

**Why not now:**

- Needs #24 backtesting and enough labelled trades.
- Minimum useful live sample: roughly 200-500 trades/candidates.
- With too little data it will overfit and look smart while losing money.

### A1.2 Claude For News / Narrative

**What it is:** Move Claude away from numeric stock-picking and toward text
understanding.

**Use cases:**

- Morning news scan: classify stock-specific news as bullish, bearish,
  irrelevant, or avoid.
- Results awareness: avoid entering right before results unless explicitly
  event-driven.
- Macro narrative: budget day, election day, RBI policy, global risk-off.
- Corporate action reading: splits, buybacks, OFS, demergers, promoter pledge
  changes, management commentary.

**Guardrails:**

- Claude narrative can only bias or block; it should not blindly force trades.
- Every text-derived decision must be logged for later audit.
- Start with a "news risk banner" rather than auto-execution.

### A1.3 Order-Book / Depth Context

**What it is:** Use top-of-book and 5-level depth context as an entry-quality
filter or timing aid.

**Signals:**

- Bid/ask imbalance.
- Depth evaporation before entry.
- Spread widening.
- Large visible orders near entry / stop levels.
- Volume-at-price zones if enough data is collected.

**Why it might help:** The current intraday engine already cares about spread
and impact cost. Depth context could reduce bad fills and fake breakouts.

**Why not now:** It is still intraday optimization, not a new money engine.

### A1 Expected Money Impact

This is not a separate return stream. Its value is measured as improvement to
Phase 2 intraday:

| Metric | Target Before Promotion |
|---|---|
| Win rate | +5-10 percentage points vs V2 baseline, or no promotion |
| Profit factor | +0.2 or more vs V2 baseline after charges |
| Trade count | Same or lower; no churn increase |
| Max drawdown | Same or lower |
| Validation | Backtest + paper/live shadow before execution |

### A1 Build Steps

1. Finish V2 backtesting framework (#24) first.
2. Export labelled feature rows from live and historical trade candidates.
3. Train an interpretable tabular model on trade outcome labels.
4. Compare ML probability vs current composite score on historical/paper data.
5. Add Claude news/sentiment as a separate feature, not as a direct trade picker.
6. Deploy in shadow mode before it affects live entries.

---

## A2 -- Delivery Swing Trading Engine (Cash Equity / CNC)

### Plain English

Delivery swing trading buys stocks for delivery (`CNC`), holds them for a few
days or weeks, and sells when the move plays out or the setup fails.

Example:

```text
Capital bucket: Rs.50,000
Buy: 20 shares of TATACONSUM at Rs.1,000 = Rs.20,000
Reason: strong uptrend, pulled back to support, volume rising
Stop: Rs.960
Target: Rs.1,080
Expected hold: 5-15 trading days
Exit: target / stop / trend-failure / time-stop
```

This is **not** Phase 1 portfolio management. It must never sell, trim, add to,
or rebalance existing long-term holdings. It uses a separate capital bucket.

### Why This Exists

Intraday with Rs.50k needs many small wins and gets punished by brokerage,
STT, GST, exchange charges, spread, and slippage. Swing trading makes fewer
decisions and targets larger moves, so each correct decision has more room to
pay for costs.

### Expected Money Range

These are planning assumptions, not promises.

| Capital | Reasonable Annual Target | Monthly Shape | Drawdown Risk |
|---:|---:|---|---|
| Rs.50k | 12-25% if edge exists; 5-10% if average | Good months Rs.1k-Rs.3k; bad months -Rs.2k to -Rs.5k | 5-15% |
| Rs.2L | Rs.24k-Rs.50k/year if edge exists | Good months Rs.4k-Rs.12k | 5-15% |
| Rs.5L+ | More worthwhile as a system | Larger compounding impact | Strategy-dependent |

Typical trade shape:

| Item | Target |
|---|---|
| Hold time | 5-20 trading days |
| Stop distance | 2-4% |
| Target distance | 5-10% |
| Minimum R:R | 1.5R-2R |
| Positions on Rs.50k | 2-3 positions of Rs.15k-Rs.25k |

Example: a 6% winner on Rs.25k makes roughly Rs.1,500 gross before charges; a
3% loser on Rs.25k loses roughly Rs.750 gross. The math is cleaner than trying
to capture Rs.100 intraday after multiple small costs.

### How It Differs From Intraday

| Intraday Bot | Delivery Swing Engine |
|---|---|
| MIS product | CNC delivery product |
| Must square off same day | Holds across days/weeks |
| Many small trades | Few larger trades |
| Live monitor all day | Mostly EOD review + optional morning execution |
| Speculative business income | Capital gains treatment |
| High charge sensitivity at small budget | Lower turnover, lower churn |

### EOD Workflow

EOD means **end of day**, after market close, when the daily candle is final.
The swing engine does not need to watch every tick in the MVP.

Daily workflow:

1. After 3:30 PM, fetch final daily candles.
2. Update open swing positions.
3. Check exits:
   - target reached,
   - stop/trend level violated,
   - daily close below invalidation level,
   - time-stop after N days without progress,
   - NIFTY/sector regime turned hostile.
4. Scan for new candidates.
5. Produce tomorrow's action plan.
6. Save a report under `reports/swing/YYYY/MM/`.
7. Paper-track every recommendation whether or not it is executed.

Morning workflow, once execution is supported:

1. Load yesterday's approved plan.
2. Confirm available cash in the swing bucket.
3. Place CNC buy/sell orders only if user-approved.
4. Record fills and update the swing ledger.

### VM / Static-IP Requirement

| Usage | VM / Static IP Needed? | Reason |
|---|---|---|
| Scanner-only with public data | No | No Kite API. |
| Scanner using Kite historical/quotes | Yes | Kite Connect app IP whitelist applies. |
| Manual order placement in Kite app/web | No for the tool | User executes outside the API. |
| API-based CNC execution | Yes | Same Kite/static-IP requirement as Phase 2. |
| VM running all day | Not for MVP | Scheduled EOD/morning jobs are enough. |

### Modes

| Mode | Command | Behaviour |
|---|---|---|
| Scanner-only | `python main.py --mode swing --scan` | Generates candidates and paper-tracks outcomes. No order placement. |
| Plan | `python main.py --mode swing --plan` | Prints next-day buy/sell/hold plan. User can execute manually. |
| Assisted execution | `python main.py --mode swing --execute` | Places CNC orders only after explicit terminal confirmation. |
| Full auto | Future only | VM places approved CNC orders on schedule after enough evidence. |

### Signals To Consider

| Signal Family | Details |
|---|---|
| Trend | Price above 20DMA/50DMA; 20DMA above 50DMA; rising moving averages. |
| Relative strength | Stock outperforming NIFTY over 5/10/20 days. |
| Breakout | Close above 20-day high with volume > 1.5x average. |
| Pullback | Strong stock pulls back to 20EMA/50DMA and holds. |
| Volume | Breakout/pullback confirmed by rising volume or relative volume. |
| Sector strength | Prefer stocks in sectors beating NIFTY. |
| Market regime | Avoid fresh longs when NIFTY is below key trend filters. |
| Event awareness | Avoid blind entries right before results unless event-driven by design. |

### Risk Rules

| Rule | Starting Point |
|---|---|
| Separate capital | `SWING_MAX_BUDGET_INR`, independent of `MAX_BUDGET_INR`. |
| Max positions | 2-5, depending on capital. |
| Per-position cap | 20-40% of swing bucket. |
| Stop | ATR-based or swing-low based. |
| Target | Minimum 1.5R-2R. |
| Time stop | Exit after 10-20 trading days if no progress. |
| Averaging down | Not allowed. |
| Long-term holdings | Never touched. |
| MTF/leverage | Not allowed in this engine. |

### Data / DB / Reporting Needed

| Component | Purpose |
|---|---|
| `swing_candidates` table | Every scanner recommendation, including rejected/paper-only ideas. |
| `swing_positions` table | Open/closed swing positions. |
| `reports/swing/` | Daily action plans and results. |
| Dashboard panel | P&L, win rate, drawdown, open positions, paper-vs-executed comparison. |
| Capital-gains ledger integration | Delivery trades need STCG/LTCG treatment, not intraday speculative treatment. |

Fields to store:

- symbol, exchange, setup type,
- scan date, planned entry, actual entry,
- stop, target, R:R,
- capital allocated,
- signal features,
- exit date, exit reason,
- gross P&L, charges, net P&L,
- paper vs executed flag.

### Build Steps

1. Add config: `SWING_MAX_BUDGET_INR`, universe, max positions, hold limit,
   stop/target rules.
2. Build scanner on daily candles using existing indicator infrastructure.
3. Add `swing_candidates` and `swing_positions` DB tables.
4. Add text/JSON reports under `reports/swing/`.
5. Paper-track first for 30-60 days.
6. Add dashboard comparison vs NIFTY and ETF rotation.
7. Add `product=CNC` support in `ZerodhaClient.place_order()`.
8. Add manual-confirm execution.
9. Only after paper/live evidence, consider scheduled VM execution.

### Promotion Criteria

Do not enable live execution until the scanner has at least:

| Metric | Threshold |
|---|---|
| Paper period | 30-60 calendar days minimum. |
| Trade count | 20+ completed paper setups. |
| Profit factor | > 1.3 after estimated charges. |
| Max drawdown | < 10-12% on the swing bucket. |
| Benchmark | Beats NIFTY buy-and-hold over the same period, or has materially lower drawdown. |

---

## A3 -- ETF Rotation Engine (Cash Equity / CNC)

### Plain English

ETF rotation chooses which broad ETF baskets to hold instead of choosing
individual stocks. It periodically rotates a separate capital bucket into the
strongest ETF(s), and moves away from weak ones.

Example:

```text
This review:
- GOLDBEES: strongest momentum, above 50DMA
- NIFTYBEES: acceptable trend
- BANKBEES: weak, below 50DMA

Target allocation:
- 60% GOLDBEES
- 40% NIFTYBEES
- 0% BANKBEES
```

### Why This Exists

ETF rotation is calmer than stock swing trading. It has lower single-stock
risk, fewer trades, and is easier to backtest. It is not the highest-upside
idea, but it may be the most robust cash-market compounding engine.

### Candidate ETF Universe

Final inclusion must depend on liquidity, spread, tracking error, and enough
history.

| Bucket | Examples |
|---|---|
| Broad equity | `NIFTYBEES`, `JUNIORBEES` |
| Banking | `BANKBEES` |
| Sector ETFs | IT, pharma, auto, consumption, PSU/bank ETFs if liquid enough |
| Defensive / alternative | `GOLDBEES`, silver ETFs |
| Cash-like | Cash or a liquid ETF if suitable and liquid |

### Expected Money Range

These are planning assumptions, not promises.

| Capital | Reasonable Annual Target | Monthly Shape | Drawdown Risk |
|---:|---:|---|---|
| Rs.50k | 8-16% | Average Rs.300-Rs.650/month, lumpy | 3-10% |
| Rs.2L | Rs.16k-Rs.32k/year | More meaningful, still calm | 3-10% |
| Rs.5L+ | Scales better than intraday | Smoother than stock swing | 3-12% |

### ETF Rotation vs Delivery Swing

| Delivery Swing | ETF Rotation |
|---|---|
| Picks individual stocks | Picks baskets/sectors/assets |
| Higher return potential | Lower single-stock risk |
| More filters/news awareness | Cleaner rules |
| 2-20 day holds | Weekly/monthly holds |
| More trades | Fewer trades |
| More volatile | Easier to backtest |

### Rebalance Workflow

ETF rotation should run weekly or monthly, not constantly.

1. Fetch ETF daily candles.
2. Compute momentum:
   - 1-month,
   - 3-month,
   - 6-month.
3. Compute trend filters:
   - above 50DMA,
   - above 200DMA if enough history.
4. Penalize high volatility / deep drawdown.
5. Rank ETFs.
6. Allocate to top 1-3 ETFs.
7. If no ETF passes trend filters, hold cash/cash-like bucket.
8. Avoid rebalancing unless allocation drift exceeds threshold.
9. Save a report and paper-track results.

### VM / Static-IP Requirement

| Usage | VM / Static IP Needed? | Reason |
|---|---|---|
| Scanner-only with public data | No | No Kite API. |
| Scanner using Kite data | Yes | Kite Connect app IP whitelist applies. |
| Manual ETF orders | No for the tool | User executes outside the API. |
| API-based CNC execution | Yes | Same Kite/static-IP requirement as Phase 2. |
| VM running forever | No | Weekly/monthly scheduled run is enough. |

### Modes

| Mode | Command | Behaviour |
|---|---|---|
| Scanner-only | `python main.py --mode rotate --scan` | Ranks ETFs and prints target allocation. |
| Paper portfolio | `python main.py --mode rotate --paper` | Tracks hypothetical rebalances. |
| Plan | `python main.py --mode rotate --plan` | Prints buy/sell quantities for manual execution. |
| Assisted execution | `python main.py --mode rotate --execute` | Places CNC ETF orders after confirmation. |

### Rotation Rules To Consider

| Rule | Starting Point |
|---|---|
| Momentum score | Weighted 1M + 3M + 6M return. |
| Trend eligibility | Must be above 50DMA; optional 200DMA filter. |
| Max holdings | Top 1-3 ETFs. |
| Min allocation | Avoid tiny positions that create DP/charge noise. |
| Min holding period | Avoid churn; e.g. no rebalance before 2-4 weeks unless risk-off. |
| Drift threshold | Rebalance only when target/current allocation differs enough. |
| Risk-off state | Move to cash/cash-like bucket if all candidates fail trend filter. |
| Liquidity | Prefer liquid ETFs even if a niche ETF scores better. |

### Costs / Tax Considerations

- Delivery brokerage is zero at Zerodha for retail equity delivery, but statutory
  charges still exist.
- DP charges apply on sell per ISIN.
- Low turnover is central to the edge.
- Gains are capital gains, not speculative intraday income.
- ETF rotation P&L must be tracked separately from intraday MIS reports.

### Data / DB / Reporting Needed

| Component | Purpose |
|---|---|
| ETF universe config | Defines eligible ETFs and liquidity constraints. |
| `rotation_signals` table | Stores every rebalance recommendation. |
| `rotation_positions` table | Tracks paper and live ETF allocations. |
| `reports/rotation/` | Weekly/monthly allocation reports. |
| Dashboard panel | Rotation equity curve vs NIFTY buy-and-hold. |

Fields to store:

- ETF symbol,
- score components,
- rank,
- target allocation,
- actual allocation,
- rebalance reason,
- gross/net P&L,
- benchmark return.

### Build Steps

1. Add ETF universe config.
2. Build ETF historical price loader.
3. Build momentum/trend ranking engine.
4. Build target allocation calculator.
5. Add paper portfolio tracking.
6. Add reports under `reports/rotation/`.
7. Add dashboard comparison vs NIFTY and swing scanner.
8. Add CNC execution only after paper tracking proves useful.

### Promotion Criteria

Do not enable live execution until the scanner has at least:

| Metric | Threshold |
|---|---|
| Paper period | 2-3 months minimum. |
| Rebalance count | 4+ completed review cycles. |
| Benchmark | Beats NIFTY buy-and-hold on risk-adjusted basis. |
| Max drawdown | Lower than NIFTY over the same period. |
| Churn | Low enough that DP/statutory charges do not dominate. |

---

## ⚠️ 2026-06-06 Update: F&O Constraint Under Review

The "No F&O" hard constraint (2026-04-28) is being revisited. After completing
Phases 0-6 of intraday equity backtesting (best OOS PF = 1.10, still <1.15
gate), the structural cost disadvantage of equity MIS on NSE has been
identified as the primary blocker. Options may solve this.

See **[TRADE_NEXT_IDEAS.md](TRADE_NEXT_IDEAS.md)** for:
- Section A: Remaining intraday equity ideas to test before concluding
- Section B: Options trading research for a potential new `--mode options`

The F&O constraint remains in effect for CODE — no options code will be written
until the ideas in TRADE_NEXT_IDEAS.md are evaluated and approved.

---

## Out Of Scope / Removed From Ideation

These were previously mentioned or tempting, but are now explicitly rejected
under the 2026-04-28 constraints.

| Idea | Reason |
|---|---|
| Options chain signals | F&O-linked; no longer allowed. |
| Expiry-day PCR/max-pain/OI logic | F&O-linked; no longer allowed. |
| High-OI stock filter from options data | F&O-linked; no longer allowed. |
| Covered calls on existing holdings | F&O and touches long-term holdings. |
| Futures/options hedge engine | F&O; out of scope. |
| Options strategy lab | F&O; out of scope. |
| Pledged collateral / margin workflows | Mostly useful for F&O; out of scope. |
| Pairs trading with short/futures leg | Requires shorting/F&O/SLB complexity; not aligned now. |
| MTF swing trading | Adds interest drag; avoid until cash-only systems prove edge. |

---

## Summary Table

| # | Feature | Category | Impact | Effort | Depends On |
|---|---|---|---|---|---|
| A1 | V3 AI Intraday Research | Existing V3 | High | High | Backtesting #24 + 200-500 labelled trades/candidates |
| A2 | Delivery Swing Trading Engine | New Money Engine | High | Medium | Daily candle scanner + paper ledger + CNC support |
| A3 | ETF Rotation Engine | New Money Engine | Medium-High | Medium | ETF universe + paper ledger + benchmark dashboard |

---

## Implementation Order

1. Keep Phase 2 intraday stable; do not add speculative intraday tweaks without
   evidence.
2. Build **ETF Rotation scanner** first because it is easiest to backtest and
   lowest stress.
3. Build **Delivery Swing scanner** second because it has higher return
   potential but more stock-specific risk.
4. Paper-track both for 30-60 days minimum.
5. Compare:
   - paper return vs NIFTY,
   - max drawdown,
   - number of trades,
   - charges/tax friction,
   - time spent,
   - correlation with existing intraday bot performance.
6. Promote only the better engine to manual-confirm execution.
7. Add full VM execution only after manual/live results are stable.
8. Keep V3 AI Intraday research separate; it depends on backtesting and more
   labelled trade history.

---

## First MVP Recommendation

Build a recommendation-only mode before adding live execution:

```text
python main.py --mode opportunities
```

Sections:

1. ETF rotation candidates.
2. Delivery swing candidates.
3. Event/corporate-action watchlist later, if still desired.
4. Idle cash note later, if useful.

No orders at first. The goal is to discover whether ETF rotation or delivery
swing has better paper edge before spending engineering time on execution.
