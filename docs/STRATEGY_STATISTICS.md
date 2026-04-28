# Strategy Statistics — Theoretical & Live

> **Purpose.** This document gives an honest, industry-standard probabilistic
> view of how this trading tool is *expected* to perform. It is split into two
> sections:
>
> 1. **Theoretical analysis** — derived purely from the strategy mechanics
>    (gate count, R:R floor, charge profile, base rates of intraday momentum).
>    This is the section we **update every time a new strategy / gate / rule
>    is added** and ask: *did this raise our edge, keep it flat, or shrink it?*
> 2. **Live trade analysis (for reference only)** — actual ledger numbers
>    with an explicit disclaimer that the strategy mix has been a moving
>    target during the early-development weeks, so these numbers are
>    directional, not deterministic.
>
> Last theoretical update: **2026-04-27** (after Roadmap #244 —
> whipsaw counter broadened to count any losing exit, not just
> STOP_LOSS; closes coverage gap exposed when today's session-1 lost
> 4-in-a-row to MOMENTUM_KILL with the guard never firing).

---

## 0. Quick snapshot — theoretical vs live

This table mirrors the **summary card on the dashboard's Statistics page**
(`/theory/statistics`). What the strategy *should* deliver versus what the
live ledger currently shows. Live numbers are auto-refreshed by
`Dashboard/live_stats.py` and cover the **current FY** (verified +
provisional intraday trades).

> Live snapshot below was captured on **2026-04-28** for FY 2026-04-01 →
> 2027-03-31 (129 trades across 18 trading days, source: `intraday_tax_ledger`
> via `scripts/tax_summary.py --intraday`). Refresh the dashboard for the
> live read.

| Metric | Theoretical (target) | Live (current FY) | Status |
|---|---|---|---|
| Win rate                  | 55%                       | **41.9%** (54 W / 73 L of 129, 2 scratched) | 🔴 below |
| Profit Factor             | ≥ 1.50                    | **1.14** (GP Rs.4,058 / GL Rs.3,569)  | 🔴 below |
| Expectancy / trade        | +0.10 R (≈ +Rs.25)        | **−Rs.9.20** (Net −Rs.1,187 / 129)    | 🔴 below |
| P(profitable day)         | ≈ 60%                     | **33.3%** (6 of 18 days)              | 🔴 below |
| Sharpe (annualised)       | 1.5 – 2.5                 | **−3.07** (Sortino −4.29, last refreshed 2026-04-27 — small day-count change, headline within ±2 %) | 🔴 below |
| Max drawdown              | < 10% of capital          | **Rs.1,311** peak-to-trough           | 🟢 within |

**Read this as reference only.** The 129 trades above were taken across
multiple iterations of the strategy (V1 → V2 → roadmap items #161 … #245
plus today's #188 sign-flip-decay fix in commit `09483df`). Gates were
added, removed, tightened, and loosened in flight — so this is **not** a
clean backtest of the current code. The clean-strategy benchmark only
starts from now (post-#245 plus the #188 sign-flip patch). Full caveat
in §4.

---

## 1. Industry-standard metrics we track

Most quant desks and retail platforms (TradeStation, NinjaTrader, MultiCharts,
Zerodha Streak, TradingView) report the same five-to-eight headline numbers.
We use the same vocabulary so a third-party analyst can read this doc with
zero translation cost.

| # | Metric | Formula | What it tells you | Acceptable band (intraday equity) |
|---|---|---|---|---|
| 1 | **Total Net P&L** | Gross profit − gross loss − charges | Bottom line. Easy to game with one outlier; never read in isolation. | Positive over ≥ 30 trading days |
| 2 | **Profit Factor (PF)** | Gross profit ÷ gross loss (incl. charges) | How many ₹ of profit per ₹ of loss. **Must be > 1** to be profitable. | 1.3 = decent, 1.5 = good, 2.0+ = great |
| 3 | **Win Rate (% Profitable)** | Winning trades ÷ total trades | Hit rate. Meaningless without R:R context. | 45–60 % typical for momentum intraday |
| 4 | **Expectancy (Avg Net Profit per Trade)** | Total net P&L ÷ trade count | Average ₹ each trade contributes. The single most important metric. | > 0 after charges |
| 5 | **Maximum Drawdown (MDD)** | Largest peak-to-trough equity decline | Worst-case pain. Determines position sizing & psychological survivability. | < 15 % of capital for retail intraday |
| 6 | **Sharpe Ratio** | (Mean daily return − rf) ÷ stdev daily return × √252 | Risk-adjusted return. Standard institutional benchmark. | > 1.0 acceptable, > 2.0 excellent |
| 7 | **Sortino Ratio** | (Mean daily return − rf) ÷ stdev of *negative* returns × √252 | Like Sharpe but only penalises downside vol. Better for asymmetric strategies. | > 1.5 acceptable |
| 8 | **Calmar Ratio** | Annualised return ÷ max drawdown | Reward per unit of worst-case risk. | > 0.5 acceptable, > 1.0 good |
| 9 | **R-Multiple Expectancy** | (Win% × Avg_Win_R) − (Loss% × Avg_Loss_R) | Expectancy expressed in "R" (initial-risk units). Strategy-agnostic. | > 0.2 R per trade |
| 10 | **Kelly Fraction** | (W / Avg_Loss) − ((1−W) / Avg_Win) | Theoretically optimal bet size; we cap at 0.5 × Kelly in practice. | 0.05–0.25 of capital per trade |

*Sources:*
[Investopedia — Strategy Performance Reports](https://www.investopedia.com/articles/fundamental-analysis/10/strategy-performance-reports.asp),
[Wikipedia — Sharpe ratio](https://en.wikipedia.org/wiki/Sharpe_ratio),
CFA Institute, *Quantitative Investment Analysis*.

---

## 2. Theoretical edge analysis

### 2.1 Base rates we start from

| Quantity | Value | Source |
|---|---|---|
| Random-coin-flip win rate on a 1-min momentum signal | **~50 %** | Efficient-Market null hypothesis |
| Empirical win rate of *unfiltered* ATR-breakout intraday on NSE liquid 250 | **40–48 %** | Published quant studies (Bouchaud, Lillo); Zerodha Varsity |
| Empirical win rate of *filtered* multi-gate momentum (≥ 5 confirmations) | **52–58 %** | Same as above, with selection bias acknowledged |
| Avg charges per round-trip on Rs.50,000 notional MIS | **~Rs.55–80** | Zerodha brokerage calculator (STT + brokerage + GST + SEBI + stamp) |
| Hard R:R floor enforced on every entry | **1.3** | `Config.RR_HARD_FLOOR` (post #243) |
| Default ATR-derived R:R | **1.5** | `Config.RR_TARGET_RATIO` |

### 2.2 Break-even win rate (the "do we have an edge?" question)

For a strategy with reward-to-risk ratio **R**, the break-even win rate **before**
charges is:

$$W_{be}^{\text{gross}} = \frac{1}{1 + R}$$

For our hard floor R = 1.3 → $W_{be}^{\text{gross}}$ = **43.5 %**.
For default R = 1.5 → $W_{be}^{\text{gross}}$ = **40 %**.

Adding charges. If charges = **c** (in R-units; on Rs.50K notional with
Rs.250 stop and ~Rs.65 round-trip charges, c ≈ 0.26):

$$W_{be}^{\text{net}} = \frac{1 + c}{1 + R}$$

→ At R=1.5, c=0.26: $W_{be}^{\text{net}}$ ≈ **50.4 %**.
→ At R=1.3, c=0.26: $W_{be}^{\text{net}}$ ≈ **54.8 %**.

**Implication for the tool:** every trade that enters at the *bare minimum*
1.3 R:R floor needs the gating system to deliver a **≥ 55 % conditional
win rate** on selected setups for the trade to be net profitable. This is
the central design constraint behind every gate.

### 2.3 Gate-by-gate edge contribution (theoretical)

The pre-trade pipeline currently runs **40 sequential gates** (post #225
late-entry simplification + #243 R:R-floor collapse, see [STRATEGY_V2.md](STRATEGY_V2.md)).
Each gate is one of three types:

| Gate type | Role | EV impact (theoretical) |
|---|---|---|
| **Hard reject** (e.g. R:R floor, ADX, score floor, VWAP) | Removes negative-EV setups | +0.05 to +0.15 R per surviving trade |
| **Score adjuster** (sector bias, tape breadth, contradiction penalty) | Re-ranks within survivors | +0.02 to +0.05 R |
| **Risk manager** (sector cap, position cap, daily loss soft-stop) | Caps tail loss | Reduces MDD by 20–40 % |

The gates do not compose multiplicatively (they are not independent — a
chop-tape filter and an ADX filter overlap). A reasonable model is:

$$W_{\text{conditional}} = W_{\text{base}} + \sum_{i=1}^{N} \alpha_i \cdot (1 - \rho_i)$$

where $\alpha_i$ is the marginal lift per gate (typically 0.5–1.5 % win-rate
points) and $\rho_i$ is its overlap with prior gates. With 40 gates and an
average marginal lift of ≈ 0.4 %-points (after overlap discount), we expect
a conditional win rate **lift of 8–15 %-points** over the unfiltered base.

| Scenario | Base WR | Estimated lift | Expected WR | EV (R per trade, R:R=1.5, c=0.26) |
|---|---|---|---|---|
| Pessimistic (gates underperform, base = 42 %) | 42 % | +8 % | **50 %** | **−0.01 R** (essentially break-even net) |
| Base case (median assumption, base = 45 %) | 45 % | +10 % | **55 %** | **+0.10 R** (≈ +Rs.25/trade on Rs.50K notional) |
| Optimistic (gates fire well, base = 47 %) | 47 % | +13 % | **60 %** | **+0.20 R** (≈ +Rs.50/trade) |

**Headline answer to "what are the chances of profit on a single day":**
At base case (55 % win rate, 5 trades/day average), the probability that the
day finishes net positive is approximately:

$$P(\text{day net}+) \approx \sum_{k=\lceil 5 W_{be} \rceil}^{5} \binom{5}{k} W^k (1-W)^{5-k}$$

For W = 0.55 and W_be ≈ 0.50: **P(profitable day) ≈ 60 %**.
For W = 0.50 (pessimistic): **P(profitable day) ≈ 50 %** (coin flip).
For W = 0.60 (optimistic): **P(profitable day) ≈ 70 %**.

**This is the number to track.** Every new gate / rule should either *raise*
this number, keep it flat (and improve another metric like MDD), or be
rejected.

### 2.4 Theoretical Sharpe / Sortino ceiling

With a 0.10 R per-trade expectancy, 5 trades/day, ~250 trading days/year, and
an empirical daily return stdev of ~0.8 % (matches NSE intraday momentum
literature), the *theoretical* annualised Sharpe ceiling is:

$$\text{Sharpe} \approx \frac{0.10 \times 5 \times 250 \times \text{R}_\text{rs}}{\sigma_{\text{daily}} \times \sqrt{250}}$$

For R in rupees on Rs.50K notional ≈ Rs.250 risk → annual gross ≈ Rs.31,250 ≈
62 % of capital. With σ_daily ≈ 0.8 %, σ_annual ≈ 12.6 %.
Sharpe ceiling ≈ **(0.62 − 0.07) / 0.126 ≈ 4.4** (gross, before slippage).

Realistic post-friction Sharpe target: **1.5 – 2.5**. Anything sustained
above 3 over > 6 months should trigger a *suspicion audit* (overfitting,
look-ahead bias, or unaccounted tail risk — see Lo, *Statistics of Sharpe
Ratios*, 2002).

### 2.5 Strategy-by-strategy edge contribution table

This is the table we **update on every strategy change**.
"Δ EV" is in R-multiples per trade vs the prior baseline.
"Δ MDD" is reduction (positive) or increase (negative) vs prior.

| Strategy / Gate | Type | Δ EV (R/trade) | Δ MDD | Notes |
|---|---|---|---|---|
| Score floor (`V2_MIN_SCORE = 2.0`, budget-adjusted: TINY +1.0, SMALL +0.5; late-entry +1.0 after 10:00 IST) | Hard reject | +0.06 | −5 % | Removes weakest candidates after pattern + technical scoring; budget regime tightens it on small accounts |
| ATR-multiplier SL + RR_TARGET_RATIO=1.5 | R:R structure | +0.04 | −3 % | Sets the asymmetric bet shape |
| RR_HARD_FLOOR=1.3 (post #243) | Hard reject | +0.05 | −2 % | Floor below which no trade fires |
| ADX threshold (per-stock + chop floor) | Hard reject | +0.04 | −8 % | Avoids range-bound P&L bleeders |
| VWAP statistical-band gate (#201) | Hard reject | +0.03 | −3 % | Filters extended chases |
| Gap-coherence gate (#173) | Hard reject | +0.02 | −4 % | Blocks counter-gap losers (HDFCBANK class) |
| Pattern-direction veto (#190) | Hard reject | +0.02 | −2 % | Blocks bearish-candle BUYs |
| Tape-breadth filter (#212) | Score adjust | +0.03 | −2 % | Down-weights minority-side trades |
| Sector-relative-strength bias (#218) | Score adjust | +0.02 | 0 % | Re-ranks within survivors |
| Choppy-morning entry pause (#192) | Hard reject | +0.02 | −5 % | Skips low-edge first hour on chop days |
| Strong-gap ADX boost (#194) | Hard reject | +0.01 | −2 % | Blocks fade trades on strong-gap days |
| Earnings-day blackout (#219) | Hard reject | +0.02 | −3 % | Avoids event-driven gap risk |
| MTM-aware circuit-breaker (#166) | Risk mgmt | 0 | −15 % | Caps daily MTM loss |
| Per-symbol re-entry cooldown (30m) | Hard reject | +0.01 | −3 % | Prevents revenge-trading same symbol |
| Average-down prevention (#195) | Hard reject | +0.02 | −4 % | Blocks low-conviction re-entries |
| Stagnant Tier-1/2 exits (#172) | Exit rule | +0.04 | −3 % | Cuts dead positions, frees capital |
| Momentum kill (#198/#233) | Exit rule | +0.03 | −5 % | Cuts trend-flip losers early |
| Loss-streak guard (#20 + #244 broadening) | Risk mgmt | 0 | −4 % | Pauses 30 min after 3 consecutive losing exits (any reason); cuts whipsaw-day tail |
| TARGET_DECAY_PCT (post-1pm tightening on open positions) | Exit rule | +0.02 | −2 % | Protects afternoon profits |
| Late-entry score bump (+1.0, #239) | Hard reject | +0.02 | −2 % | Raises bar for limited-time-to-target trades |
| 14:45 LOSER_EXIT + 15:10 SQUARE_OFF | Exit rule | 0 | −3 % | Exits stale + auction-tax avoidance |
| **Estimated cumulative theoretical edge** | | **≈ +0.46 R** | **−74 %** | But independent gates overlap, so see §2.3 |
| **After overlap discount (×0.25)** | | **≈ +0.11 R** | **−40 %** | Matches §2.3 base case |

### 2.6 What raises vs harms theoretical probability

**Raises P(profit):**
- Adding a *hard-reject* gate that targets a documented loss pattern with
  ≥ 5 prior live examples (e.g., HDFCBANK gap-coherence, cluster-extension #234).
- Tightening exit rules (stagnant cuts, momentum kill) — reduces adverse
  variance without affecting win rate.
- Risk-management caps (MTM CB, sector cap, per-symbol cooldown) — reduces
  MDD without affecting expectancy much.

**Keeps it flat:**
- Refactors / consolidations (e.g., #243 R:R-floor collapse — same behaviour,
  less code).
- Score adjusters that re-rank within the surviving set.

**Harms P(profit) (red flags):**
- Adding a gate that *blocks* trades without prior live evidence (speculative
  filters). Each unnecessary gate trims our trade count → worsens
  trades-per-day, raising the variance of daily P&L.
- Loosening a gate without before/after evidence (the lens we caught in #189).
- Pre-shrinking entry targets (the #242 anti-pattern: target compression
  below the always-on R:R floor → 100 % rejection of late entries).

---

## 3. Probability snapshot — *as of 2026-04-27*

| Question | Theoretical answer |
|---|---|
| What's the expected P&L on a single day? | **+0.10 R/trade × 5 trades = +0.50 R/day ≈ +Rs.125 net** on Rs.50K notional |
| What's the probability the day finishes net positive? | **≈ 60 %** (base case, 55 % WR) |
| What's the probability of a profitable week (5 days)? | **≈ 79 %** (binomial with daily P=0.60, ≥ 3 winning days) |
| What's the probability of a profitable month (~ 22 trading days)? | **≈ 94 %** |
| Expected MDD over 100 trades | **~10 % of capital** (post all risk-mgmt gates) |
| Expected annualised Sharpe (post-friction) | **1.5 – 2.5** target band |

These numbers are the **theoretical expected output** of the current code,
not a prediction or guarantee. Live distribution will differ because:

1. The base rate (45 % unfiltered WR) is a *literature estimate* for NSE
   liquid 250 — our actual universe and indicators may differ.
2. Gate overlap is *modelled* (×0.25 discount), not measured.
3. Slippage, partial fills, and broker-side rejections are not in the model.

---

## 4. Live trade analysis (reference only — strategy mix varied)

> **Disclaimer.** The trades captured in the SQLite ledger
> (`data/trades.db`, table `intraday_tax_ledger`) were taken across **multiple iterations of the strategy** (V1 → V2 → V2 +
> Roadmap #161 … #243). Gates were added, removed, tightened, and
> loosened in flight. **Do NOT read these numbers as a clean backtest of
> the current code.** They are useful for two narrow purposes:
>
> 1. Sanity-check: are the live numbers in the *same order of magnitude*
>    as the §3 theoretical snapshot?
> 2. Spot anomalies: any metric that's wildly off-prediction is a flag
>    for a hidden bug (charge mis-attribution, exit-reason mis-tagging, …).

### 4.1 Where to read the live numbers

- **Top of this page (§0 Quick snapshot)** — the table at the very top is
  auto-rendered on the dashboard from `Dashboard/live_stats.py` and shows
  the current FY in real time. That is the canonical live read.
- **Dashboard home** ([`/`](/)) — interactive day-by-day P&L, per-trade
  drilldown, exit-reason split. Use this to inspect any specific window.
- **CLI** — `python scripts/tax_summary.py --intraday` prints the same
  numbers as a flat report (handy when the dashboard server is not running).
- **Source of truth** — `Dashboard/data_layer.py::fetch_trades()` reads
  the ledger; `Dashboard/live_stats.py::compute_live_stats()` aggregates
  it. Both are pure functions with no side effects, so re-running them
  always produces the current snapshot.

### 4.2 Cross-check rules

When the §0 numbers and the §3 theoretical snapshot diverge by more than
**±20 %** on any of the four core metrics (win rate, profit factor,
expectancy, max drawdown), open an analyst-review pass:

1. Was a gate removed or loosened in the last 30 trading days without an
   entry in §2.5?
2. Are recent trades skewed toward one exit reason (e.g., `LOSER_EXIT`
   spike → entry quality drift, or `MOMENTUM_KILL` spike → trend regime
   change)? Inspect via `python scripts/view_trades.py --recent 30`.
3. Has the trade count per day collapsed (< 2/day rolling)? That's a
   "gates over-rejecting" signal (the #242 anti-pattern).

**Why this section is short.** The numbers themselves live in §0 above
and on the dashboard — repeating them here would just go stale. This
section exists to define the *contract* between the live data and the
theoretical model, not to mirror the data.

---

## 5. Update protocol

Whenever a Roadmap item ships that affects strategy behaviour:

1. Identify whether it is **EV-positive**, **EV-neutral**, or **EV-negative**
   (the analyst lens — see [copilot/analyst-review.md](../copilot/analyst-review.md)).
2. Append / update the relevant row in §2.5.
3. Update the §3 probability snapshot if the change moves the headline number
   by more than ±2 %.
4. If the change *removes* a gate, also note it under §2.6 ("Harms" or "Keeps
   flat") with the structural reasoning.
5. Bump the "Last theoretical update" date at the top.

This doc is the single place where we ask "did our edge improve?" — and
the answer must be backed by the §2.5 table, not by anecdote.
