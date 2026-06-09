# Options Strategy Reference

> **Created:** 2026-06-09
> **Status:** Phase O-4 complete. Backtest v1.0 FAIL (PF 0.42). Strategy
> improvement required before dry-run.
> **Companion docs:**
> [OPTIONS_GUIDE.md](OPTIONS_GUIDE.md) — plain-English primer,
> [OPTIONS_ROADMAP.md](OPTIONS_ROADMAP.md) — phased rollout plan.

---

## 1. Overview

The options mode (`--mode options`) trades **NIFTY index weekly options** on
NSE's NFO segment. It is a completely separate engine from intraday equity,
with its own capital bucket, risk rules, database, and reports.

| Aspect | Value |
|---|---|
| Instrument | NIFTY 50 weekly options (CE / PE) |
| Exchange | NFO (F&O segment on NSE) |
| Product | MIS (intraday) |
| CLI | `python main.py --mode options` |
| Default mode | DRY_RUN (always; `--live` to override) |
| Code | `modes/options/` (5 modules) |
| DB | `data/options.db` |
| Reports | `reports/options/` |
| Backtest | `scripts/trade/backtest_options.py` |

---

## 2. Strategy v1.0 — Regime-Gated Directional Buying

### Signal

Buy an ATM or slightly OTM NIFTY weekly call or put based on:
1. **Regime classification** (prior 5-day average range + gap signal)
2. **NIFTY trend direction** (gap from previous close)
3. **India VIX filter** (skip when VIX > 25 — premiums too rich)

### Regime Routing

| Regime | How Classified | Action |
|---|---|---|
| **VOLATILE** | Prior 5-day avg intraday range > 1.3% | TRADE — buy directional |
| **TREND** | Gap > 0.3% AND avg range > 0.8% | TRADE — buy directional |
| **RANGE** | Everything else | SKIP — theta eats buyers |

This is the same regime classifier used in equity trading. The key insight:
RANGE days (39% of all days) are actively harmful for option buying because
theta decay dominates when NIFTY doesn't move. We skip them entirely.

### Entry Logic

1. Wait for market open + 15-min entry delay (09:30 IST)
2. Fetch NIFTY 50 quote → compute gap from previous close
3. Classify regime from prior 5 completed days (no lookahead)
4. If VOLATILE or TREND, and gap > 0.3%:
   - Gap up → BULLISH → buy ATM CE
   - Gap down → BEARISH → buy ATM PE
5. Skip if NEUTRAL (gap < 0.3%)

### Strike Selection

- NIFTY strikes at 50-point intervals
- ATM = nearest 50 to current NIFTY price (default, STRIKE_OFFSET=0)
- 1-strike OTM available via config (STRIKE_OFFSET=1)

### Position Sizing

- Budget: Rs.15,000 per trade (OPTIONS_BUDGET_INR)
- Max lots: 1 (OPTIONS_MAX_LOTS) — scale with evidence
- Lot size: 25 (fixed by NSE)
- Typical premium: Rs.150-350 per unit → Rs.3,750-8,750 per lot

### Exit Rules

| Exit Type | Trigger | Priority |
|---|---|---|
| **Stop-loss** | Premium drops 30% from entry | 1 (checked first) |
| **Target** | Premium rises 75% from entry | 2 |
| **Square-off** | 14:00 IST (OPTIONS_SQUARE_OFF_HOUR) | 3 (end of day) |
| **Circuit breaker** | Day P&L exceeds 3% of budget | Immediate |

### Monitoring Loop

- Poll premium every 15 seconds (OPTIONS_POLL_SECONDS)
- On each poll: check SL → check target → log status
- At square-off time: close all remaining positions at market

---

## 3. Premium Model (Backtest)

Since historical option premium data is not available in our backtest DB,
we use a synthetic premium model for backtesting:

### Brenner-Subrahmanyam (1988) ATM Approximation

$$\text{Premium}_{ATM} \approx 0.4 \times \sigma_{annual} \times S \times \sqrt{T}$$

Where:
- $\sigma_{annual}$ = annualised volatility (Parkinson estimator from daily H-L)
- $S$ = NIFTY spot price
- $T$ = days to expiry / 365

At NIFTY 24,000, vol 15%, DTE 5 → premium ≈ Rs.168 per unit.

### Parkinson Volatility Estimator

$$\sigma^2 = \frac{1}{4 \ln 2} \cdot \frac{1}{N} \sum_{i=1}^{N} \left(\ln \frac{H_i}{L_i}\right)^2$$

Uses 20-day lookback on daily high-low data. More efficient than
close-to-close estimator for the same sample size.

### Intraday Premium Movement

$$\Delta P \approx \delta \times \Delta S - \theta_{intraday}$$

Where:
- $\delta = 0.50$ (ATM delta)
- $\Delta S$ = NIFTY move from open (daily OHLC used for H/L/C scenarios)
- $\theta_{intraday} \approx 0.40 \times \frac{P}{2 \times DTE}$ (40% of daily theta)

---

## 4. NSE Option Charges Model

Round-trip charges for 1 lot (25 units) NIFTY option buying:

| Charge | Formula | Example (Rs.200 premium) |
|---|---|---|
| Brokerage | Rs.20 × 2 orders | Rs.40 |
| GST on brokerage | 18% of brokerage | Rs.7.20 |
| STT (sell side) | 0.0625% × sell premium × qty | Rs.3.13 |
| Exchange txn | 0.053% × (buy + sell turnover) | Rs.5.30 |
| SEBI fee | 0.0001% × total turnover | Rs.0.10 |
| Stamp duty (buy) | 0.003% × buy turnover | Rs.0.15 |
| **Total** | | **~Rs.56 per round-trip** |

At Rs.200 premium (Rs.5,000 per lot), charges are ~1.1% of capital deployed.
This is lower than equity intraday (~0.07% of turnover = ~Rs.10-15 on Rs.15K).

---

## 5. Backtest Results — v1.0 (2026-06-09)

**Data:** 509 NIFTY 50 daily candles (Apr 2024 – May 2026)

| Window | Trades | Win Rate | PF | Sharpe | P&L |
|---|---|---|---|---|---|
| FULL | 147 | 19.7% | 0.42 | -6.37 | -Rs.168,245 |
| TRAIN (2024-2025) | 84 | 14.3% | 0.30 | -8.81 | -Rs.119,145 |
| TEST (2025-2026) | 60 | 30.0% | 0.64 | -3.24 | -Rs.39,409 |

### Per-Regime Breakdown

| Regime | Trades | Win Rate | PF | P&L |
|---|---|---|---|---|
| VOLATILE | 36 | 27.8% | 0.77 | -Rs.14,500 |
| TREND | 111 | 17.1% | 0.33 | -Rs.153,745 |

### Exit Reason Breakdown

| Reason | Count | % |
|---|---|---|
| SL (30% loss) | 113 | 76.9% |
| SQUARE_OFF (EOD) | 19 | 12.9% |
| TARGET (75% gain) | 15 | 10.2% |

### Parameter Sweep (best 5 of 30 combos)

| SL% | Target% | Trades | WR | PF | Sharpe |
|---|---|---|---|---|---|
| 20 | 150 | 147 | 13.6% | 0.53 | -3.72 |
| 25 | 150 | 147 | 17.0% | 0.52 | -4.11 |
| 35 | 150 | 147 | 24.5% | 0.53 | -4.22 |
| 40 | 125 | 147 | 27.2% | 0.53 | -4.36 |
| 40 | 150 | 147 | 27.2% | 0.53 | -4.19 |

**Verdict: FAIL.** No SL/target combination produces PF > 1.0. The signal
(simple gap direction) has insufficient accuracy for option buying.

### Why It Failed — Diagnosis

1. **Win rate too low (~20-30%):** Option buying needs >40% accuracy to
   overcome the asymmetric payoff structure (lose 30% SL vs gain 75% target
   requires 29% win rate to break even, but charges push it to ~35%)
2. **Gap signal is too weak:** A 0.3% gap doesn't reliably predict the day's
   direction — it's barely above noise
3. **Theta drag is relentless:** Even on "right" days, theta eats ~5-15% of
   premium intraday, narrowing the win margin
4. **TREND regime trades poorly:** PF 0.33 — trend classification catches
   many range-ish days that happen to gap slightly

---

## 6. Strategy Improvement Ideas

Ranked by feasibility and expected impact:

### 6.1 VOLATILE-Only Regime (Quick Win)

Current: trade on VOLATILE + TREND days (PF 0.42).
Change: trade on VOLATILE days only (PF 0.77).
Impact: Still sub-1.0 but dramatically reduces losses. Only 36 trades/year
means low exposure to theta.

### 6.2 Stronger Directional Signal (Medium Effort)

Replace simple gap signal with:
- **Prev-day breakout + morning momentum**: BUY CE only when NIFTY breaks
  above yesterday's high with volume
- **NIFTY ADX > 25**: require trending conditions, not just a gap
- **First-candle confirmation**: wait for 09:30 candle to close in gap
  direction before entering (like Gap-and-Go v1.1 does for equity)

### 6.3 NIFTY Futures Instead of Options (Different Track)

NIFTY futures have near-zero STT (CTT 0.01% vs equity 0.025%), no theta
decay, and delta-1 exposure. If the directional signal works at all,
futures should outperform options.
See [TRADE_NEXT_IDEAS.md §A.4](TRADE_NEXT_IDEAS.md).

### 6.4 Pivot to Selling — Expiry Day Iron Condor (Phase O-6)

On RANGE days (39% of all days), sell OTM iron condors on Thursday.
Theta is on YOUR side. The regime classifier that hurts equity (PF 0.62)
and option buying (skip) becomes the EDGE for selling.
Requires: margin capital (~Rs.25K-40K), multi-leg order support, and
completing Phase O-3.2 backtest.

### 6.5 VIX-Conditional Strategy Routing (Advanced)

| VIX | RANGE | VOLATILE | TREND |
|---|---|---|---|
| Low (<14) | Iron condor | Buy directional | Buy directional |
| Med (14-20) | Iron condor | Buy directional | Small or skip |
| High (>20) | Skip | Buy straddle | Buy directional |

### 6.6 Calendar Spread / Diagonal Spread

Buy next-week's option, sell this-week's. Profits from differential theta
decay. Defined risk, market-neutral-ish. Needs multi-leg support (Phase O-6).

---

## 7. Config Reference

All options config lives in `config.py` under the `OPTIONS_*` prefix.

| Config | Type | Default | Purpose |
|---|---|---|---|
| `OPTIONS_BUDGET_INR` | int | 15,000 | Max capital per trade |
| `OPTIONS_MAX_LOTS` | int | 1 | Scale with evidence |
| `OPTIONS_NIFTY_LOT_SIZE` | int | 25 | Fixed by NSE |
| `OPTIONS_INDEX` | str | "NIFTY" | Only NIFTY for now |
| `OPTIONS_SL_PCT_OF_PREMIUM` | float | 30.0 | SL = 30% loss |
| `OPTIONS_TARGET_PCT_OF_PREMIUM` | float | 75.0 | Target = 75% gain |
| `OPTIONS_MAX_LOSS_PER_DAY_PCT` | float | 3.0 | Circuit breaker |
| `OPTIONS_NIFTY_STRIKE_STEP` | int | 50 | Strike intervals |
| `OPTIONS_STRIKE_OFFSET_STEPS` | int | 0 | 0=ATM, 1=OTM |
| `OPTIONS_EXPIRY_PREFERENCE` | str | "WEEKLY" | Thursday expiry |
| `OPTIONS_MIN_DTE` | int | 1 | Min days to expiry |
| `OPTIONS_VIX_MAX` | float | 25.0 | VIX filter |
| `OPTIONS_SQUARE_OFF_HOUR` | int | 14 | 14:00 IST |
| `OPTIONS_SQUARE_OFF_MINUTE` | int | 0 | |
| `OPTIONS_ENTRY_DELAY_MINUTES` | int | 15 | After 09:15 |
| `OPTIONS_POLL_SECONDS` | int | 15 | Monitor frequency |
| `OPTIONS_DRY_RUN` | bool | True | Default: no real orders |
| `OPTIONS_NAKED_SELL_ALLOWED` | bool | False | HARD BLOCK |

---

## 8. Module Architecture

```
modes/options/
├── __init__.py
├── manager.py               # OptionsManager — day lifecycle orchestrator
├── option_scanner.py         # OptionScanner — regime gate + strike selection
├── order_engine.py           # OptionsOrderEngine — BUY-only, naked sell block
├── performance_tracker.py    # OptionsPerformanceTracker — SQLite data/options.db
└── report_writer.py          # OptionsReportWriter — reports/options/
```

### Database: `data/options.db`

| Table | Purpose |
|---|---|
| `option_trades` | All closed trades (date, symbol, strike, expiry, premium, P&L) |
| `option_candidates` | Candidate audit trail (scan results, accept/reject) |

### Reports: `reports/options/<year>/<month>/`

| File | Format |
|---|---|
| `options_report_DD.txt` | Human-readable P&L + trade details |
| `options_data_DD.json` | Machine-readable (dashboard consumption) |

---

## 9. Safety Features

| Feature | Implementation |
|---|---|
| **DRY_RUN default** | `OPTIONS_DRY_RUN = True` — must explicitly `--live` |
| **Naked sell hard block** | `order_engine.py` rejects SELL without protection leg |
| **BUY-only Phase O-4** | Order engine rejects any non-BUY order |
| **Circuit breaker** | Day P&L exceeds OPTIONS_MAX_LOSS_PER_DAY_PCT → halt |
| **Budget cap** | OPTIONS_BUDGET_INR enforced per-trade and per-day |
| **Max lots** | OPTIONS_MAX_LOTS caps concurrent exposure |
| **Graceful shutdown** | Ctrl+C → square off all → generate report |
| **VIX cap** | Skip when India VIX > OPTIONS_VIX_MAX |
| **Min DTE** | Skip 0-DTE contracts (OPTIONS_MIN_DTE ≥ 1) |
