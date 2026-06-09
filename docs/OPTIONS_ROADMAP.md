# Options Mode — Roadmap

> **Created:** 2026-06-06 | **Updated:** 2026-06-09
> **Status:** Code complete (Phase O-4). Backtest v1.0 ran — PF 0.42 (FAIL).
> Strategy needs improvement before dry-run.
> **Context:** Intraday equity Gap-and-Go v1.1 passes OOS PF 1.55.
> Options mode built as a separate engine (`--mode options`).
> See [OPTIONS_GUIDE.md](OPTIONS_GUIDE.md) for plain-English primer.

---

## Current Posture

| Area | Status |
|---|---|
| Stage | **Phase O-4 — Code complete, backtest FAIL** |
| Code | `modes/options/` — manager, scanner, order engine, tracker, report writer |
| Backtest | v1.0 directional buying: PF 0.42 FULL, PF 0.64 OOS (FAIL) |
| Dashboard | Dry-run page has Intraday/Options mode switcher |
| CLI | `python main.py --mode options` (dry-run default) |
| Next step | Improve signal (OFI, momentum, VWAP) or pivot to selling |
| Capital | **Rs.0 — no live trading until strategy passes 1.15 gate** |

---

## Backtest Results — v1.0 Directional Buying (2026-06-09)

**Strategy:** Regime-Gated Directional NIFTY Option Buying
**Data:** 509 NIFTY 50 daily candles (Apr 2024 – May 2026)
**Premium model:** Brenner-Subrahmanyam ATM approximation + Parkinson vol

| Window | Trades | Win Rate | PF | Sharpe | P&L |
|---|---|---|---|---|---|
| FULL | 147 | 19.7% | 0.42 | -6.37 | -Rs.168,245 |
| TRAIN (2024-2025) | 84 | 14.3% | 0.30 | -8.81 | -Rs.119,145 |
| TEST (2025-2026) | 60 | 30.0% | 0.64 | -3.24 | -Rs.39,409 |

**Per-regime:** VOLATILE PF 0.77 (36 trades) > TREND PF 0.33 (111 trades)

**Verdict: FAIL.** Directional option buying with a simple gap signal does
not overcome theta decay + Indian regulatory charges (STT, exchange fees).

**Key learnings:**
1. VOLATILE regime routing IS valuable (PF 0.77 vs 0.33 in TREND)
2. OOS performance (PF 0.64) is better than in-sample (PF 0.30) — regime
   split is robust, not overfit
3. Win rate (~20-30%) too low for option buying payoff structure
4. Need either: better signal (>50% accuracy), or pivot to selling (theta collection)

---

## Capital Plan — Start Small, Scale With Evidence

**Philosophy:** We will NOT start with Rs.50K. We start with the absolute
minimum (1 lot, ~Rs.5K-15K premium per trade) and scale up ONLY when evidence
says we should.

| Stage | Capital at Risk (per trade) | Max Daily Loss | Scale-Up Trigger |
|---|---|---|---|
| **Paper trading** | Rs.0 (simulation only) | Rs.0 | 30+ trades, PF ≥ 1.20 |
| **Live Stage 1** | 1 lot (~Rs.5K-15K premium) | Rs.5K hard cap | 20+ live trades, PF ≥ 1.15 |
| **Live Stage 2** | 2 lots (~Rs.10K-30K) | Rs.10K hard cap | 50+ live trades, PF ≥ 1.20, Sharpe > 0.5 |
| **Live Stage 3** | 3-4 lots (~Rs.15K-50K) | Rs.15K hard cap | 100+ live trades, PF ≥ 1.25, 3 profitable months |

**Hard rules:**
- Never risk more than 30% of options capital on a single trade
- Daily loss circuit breaker = 3% of options capital
- If any stage shows PF < 1.0 after 20 trades → pause + review
- Scale DOWN immediately if drawdown exceeds 15% of options capital

---

## Buy vs Sell — Phase-Gated

| Phase | Allowed | Why |
|---|---|---|
| Paper + Live Stage 1-2 | **BUY ONLY** (calls and puts) | Max loss = premium paid. Safe. No margin surprise. |
| Live Stage 3+ | **BUY + defined-risk SELL** (spreads, iron condors) | Both legs placed atomically. Max loss known upfront. |
| **NEVER** | **Naked selling** | Unlimited loss potential. Hard-blocked in code — engine will refuse. |

**Defined-risk sell** means every sold option MUST have a paired bought option
(further OTM) that caps the maximum loss. Example:
- Sell NIFTY 24500 CE at Rs.100 → unlimited risk if naked
- Buy NIFTY 24700 CE at Rs.40 → max loss capped at (200 - 60) × 25 = Rs.3,500

The code will enforce: `if selling and no protection_leg → reject order`.

---

## Strategy Plan

### Strategy 1: Directional Option Buying (VOLATILE/TREND Days)

**What:** Buy ATM or slightly OTM NIFTY call/put using our regime classifier
+ NIFTY trend signal.

**How:**
- Morning: regime classifier labels the day
- On VOLATILE or TREND days: determine NIFTY direction from scanner
- Buy ATM weekly call (bullish) or put (bearish)
- SL: 30% of premium (e.g., bought at Rs.200, exit at Rs.140)
- Target: 50-100% gain on premium (exit at Rs.300-400)
- Hard square-off: 14:00 IST (same as equity)

**Edge hypothesis:** Our regime classifier lifts equity PF from 0.82 → 1.10
on VOLATILE days. With options leverage, a 0.5% NIFTY move = ~25-50% option
premium move. The same directional signal with better payoff structure.

**Backtest:** Need NIFTY option premium history (from Zerodha or Sensibull).

### Strategy 2: Expiry Day Iron Condor (RANGE Days)

**What:** On RANGE days (39% of all days), sell OTM iron condors on Thursday
expiry. Collect theta that melts to zero by 3:30 PM.

**How:**
- Thursday morning: regime classifier labels day as RANGE
- Sell OTM call (200+ points away) + sell OTM put (200+ points away)
- Buy further OTM call + put for protection (100 points further out)
- Net credit collected = profit if NIFTY stays in range
- Max loss = width of spread minus credit (defined, known upfront)

**Edge hypothesis:** RANGE days are our WORST equity regime (PF 0.62) but
IDEAL for premium selling. Theta crush on expiry day is extreme — OTM options
lose 80%+ of value if NIFTY stays flat. Our regime classifier has a job: RANGE
days → sell premium. VOLATILE days → skip selling (gamma too high).

**Backtest:** Need historical option premiums on expiry days.

**Capital:** Iron condor margin ≈ Rs.25K-40K per position (feasible).

### Strategy 3: VIX-Based Strategy Selection (Advanced — Later)

**What:** Combine regime + VIX level to auto-select strategy.

| VIX Level | RANGE Day | VOLATILE Day | TREND Day |
|---|---|---|---|
| Low (<14) | Iron condor (sell) | Buy directional | Buy directional |
| Medium (14-20) | Iron condor (sell) | Buy directional | Skip or small size |
| High (>20) | Skip (premium rich but risky) | Buy straddle | Buy directional |

**Backtest:** Needs VIX + option premium + regime history combined dataset.

---

## Technical Requirements

### Zerodha API Changes (ZerodhaClient)

| What | Current | Needed | Done? |
|---|---|---|---|
| Instrument loading | `instruments("NSE")`, `instruments("BSE")` | Add `instruments("NFO")` | ✅ `load_nfo_instruments()` |
| Place order product | `PRODUCT_MIS` hardcoded | Support `PRODUCT_MIS` and `PRODUCT_NRML` | ✅ `place_option_order()` |
| Place order exchange | `"NSE"` / `"BSE"` | Add `"NFO"` | ✅ |
| Symbol format | `"RELIANCE"` | `"NIFTY2560524000CE"` (option symbol format) | ✅ `_build_option_symbol()` |
| Option chain | Not fetched | Add `get_option_chain(index, expiry)` helper | ✅ via NFO token cache |
| Greeks | Not available | Compute from premium + VIX or use Sensibull API | ⚠️ Synthetic model only |

### Module: `modes/options/` ✅ COMPLETE

| File | Purpose | Done? |
|---|---|---|
| `manager.py` | Day lifecycle: morning regime check → strategy select → enter → monitor → exit | ✅ |
| `order_engine.py` | Option order execution, position tracking, multi-leg management | ✅ (buy-only) |
| `option_scanner.py` | Strike selection, premium analysis, Greeks estimation | ✅ |
| `performance_tracker.py` | SQLite persistence (separate `options.db`) | ✅ |
| `report_writer.py` | Reports under `reports/options/` | ✅ |

Full strategy reference: [OPTIONS_STRATEGY.md](OPTIONS_STRATEGY.md)

### New Database: `data/options.db`

| Table | Purpose |
|---|---|
| `option_trades` | All trades (date, symbol, strike, expiry, CE/PE, buy/sell, premium, qty, P&L) |
| `option_candidates` | Candidate audit trail (like `intraday_candidates` for equity) |
| `option_chain_snapshots` | Historical option chain data for backtesting |

### Config Additions

```python
# Options mode config (separate from equity)
OPTIONS_BUDGET_INR = 15_000          # Start small
OPTIONS_MAX_LOTS = 1                 # Scale up with evidence
OPTIONS_MAX_LOSS_PER_DAY_PCT = 3.0   # Circuit breaker
OPTIONS_SL_PCT_OF_PREMIUM = 30       # SL = 30% of premium paid
OPTIONS_TARGET_PCT_OF_PREMIUM = 75   # Target = 75% gain on premium
OPTIONS_SQUARE_OFF_HOUR = 14         # Same as equity
OPTIONS_SQUARE_OFF_MINUTE = 0
OPTIONS_DRY_RUN = True               # Start in dry-run ALWAYS
OPTIONS_NAKED_SELL_ALLOWED = False    # HARD BLOCK — never naked
OPTIONS_INDEX = "NIFTY"              # Start with NIFTY only
OPTIONS_EXPIRY_PREFERENCE = "WEEKLY" # Thursday weekly expiry
```

---

## Execution Plan (Phased)

**Same discipline as equity: one phase at a time, binary verdict, no skipping.**

### Phase O-0: Conclude Intraday Equity (PREREQUISITE)

Before starting any options work, run the 3 final intraday equity backtests
(Phase 7 in TRADE_ROADMAP.md). This takes ~5-6 hours and gives a clean
conclusion on equity. Options mode starts ONLY after equity verdict is final.

| Step | Done? |
|---|---|
| Backtest A.3 (cross-sectional momentum) | |
| Backtest A.2 (gap-and-go volume) | |
| Backtest A.6 (prev-day high/low breakout) | |
| **Verdict:** equity conclusively dead or one idea passes | |

---

### Phase O-1: Education & Paper Trading (4-8 weeks, NO CODE)

**Goal:** Build intuition before writing a single line.

| Step | Action | Done? |
|---|---|---|
| O-1.1 | Read Zerodha Varsity Module 5 (Options Theory) — free | |
| O-1.2 | Paper trade 20+ option trades on Sensibull (free tier) | |
| O-1.3 | Track every paper trade: entry/exit premium, Greeks at entry, regime, result | |
| O-1.4 | Watch expiry-day premium decay in real-time (pick 3 Thursdays) | |
| O-1.5 | Use Zerodha brokerage calculator to verify costs per trade type | |
| O-1.6 | **Verdict:** paper PF ≥ 1.20 on 20+ trades before proceeding | |

**Exit criteria:** 20+ paper trades logged with positive PF. If paper trading
loses money, do NOT proceed to code.

---

### Phase O-2: Data Pipeline (1-2 weeks)

**Goal:** Get option chain data flowing before any strategy code.

| Step | Action | Done? |
|---|---|---|
| O-2.1 | Add `instruments("NFO")` to ZerodhaClient, cache NFO tokens | ✅ |
| O-2.2 | Build `get_option_chain(index, expiry)` → returns strikes, premiums, OI | ✅ |
| O-2.3 | Store option chain snapshots in `data/options.db` | ✅ |
| O-2.4 | Fetch NIFTY weekly option chain daily for 2+ weeks (build history) | |
| O-2.5 | Verify: can we get historical option premiums from Zerodha API? | |

**Exit criteria:** Option chain data flowing to SQLite. Historical data
available for backtesting.

---

### Phase O-3: Backtest Strategies (2-4 weeks)

**Goal:** Same rigor as equity — walk-forward, OOS, after costs.

| Step | Action | Done? |
|---|---|---|
| O-3.1 | Backtest Strategy 1 (directional buying on VOLATILE days) | ✅ PF 0.42 FAIL |
| O-3.2 | Backtest Strategy 2 (iron condor on RANGE expiry days) | |
| O-3.3 | Walk-forward: train on first half, test on second half | |
| O-3.4 | Calculate P&L net of ALL option costs (brokerage, STT, exchange, GST) | |
| O-3.5 | **Verdict:** any strategy OOS PF ≥ 1.15? | |

**Exit criteria:** At least one strategy passes OOS PF ≥ 1.15 after costs,
OR both fail and options mode is shelved.

---

### Phase O-4: Build Options Mode — Dry Run (2-3 weeks)

**Goal:** `python main.py --mode options --dry-run`

| Step | Action | Done? |
|---|---|---|
| O-4.1 | Create `modes/options/` module structure | ✅ |
| O-4.2 | Build option order engine (buy-only initially, DRY_RUN) | ✅ |
| O-4.3 | Build option scanner (strike selection, premium analysis) | ✅ |
| O-4.4 | Integrate regime classifier for strategy routing | ✅ |
| O-4.5 | Build performance tracker + reports (`reports/options/`) | ✅ |
| O-4.6 | Add dashboard panel for options P&L | ✅ (mode switcher on /dryrun) |
| O-4.7 | Dry-run with live option prices for 30+ trades | ⏳ BLOCKED — strategy PF < 1.0 |
| O-4.8 | **Verdict:** dry-run PF ≥ 1.15 on 30+ trades | ⏳ |

**Exit criteria:** Dry-run results match backtest expectations. 30+ trades
logged with positive PF.

---

### Phase O-5: Live — Minimum Capital (Ongoing)

**Goal:** `python main.py --mode options` (live, 1 lot)

| Step | Action | Done? |
|---|---|---|
| O-5.1 | Add NFO support to ZerodhaClient.place_order() | |
| O-5.2 | Add naked-sell hard block in order engine | |
| O-5.3 | Go live with 1 lot NIFTY, buy-only, Rs.5K-15K per trade | |
| O-5.4 | Run for 20+ live trades | |
| O-5.5 | **Promotion gate:** PF ≥ 1.15, win rate ≥ 45%, max DD ≤ 3% | |
| O-5.6 | Scale to Stage 2 (2 lots) only after gate passes | |

---

### Phase O-6: Add Defined-Risk Selling (After Stage 2)

**Goal:** Add iron condors and spreads (NOT naked selling).

| Step | Action | Done? |
|---|---|---|
| O-6.1 | Build multi-leg order support (atomic: both legs or neither) | |
| O-6.2 | Add iron condor strategy for RANGE expiry days | |
| O-6.3 | Hard-block: code refuses sell without protection leg | |
| O-6.4 | Dry-run 20+ iron condor trades | |
| O-6.5 | Live with minimum size if dry-run passes gate | |

---

## Promotion Gates

### Paper → Live Stage 1

| Metric | Required |
|---|---|
| Paper trades | ≥ 30 |
| Paper PF (after estimated costs) | ≥ 1.20 |
| Paper win rate | ≥ 45% (buying), ≥ 65% (selling) |
| Max single-trade loss | ≤ Rs.5,000 |

### Live Stage 1 → Stage 2

| Metric | Required |
|---|---|
| Live trades | ≥ 20 |
| Live PF (after actual costs) | ≥ 1.15 |
| Max daily loss | ≤ 3% of options capital |
| Win rate | ≥ 45% (buying) |
| Consecutive loss streak | ≤ 5 |

### Live Stage 2 → Stage 3

| Metric | Required |
|---|---|
| Live trades | ≥ 50 cumulative |
| Live PF | ≥ 1.20 |
| Sharpe (annualised) | > 0.5 |
| Profitable months | ≥ 3 of last 4 |
| Max drawdown | ≤ 15% of options capital |

---

## What We Reuse From Equity

| Asset | How It Helps |
|---|---|
| **Regime classifier** | Route RANGE→sell, VOLATILE→buy. Our #1 edge asset. |
| **NIFTY trend signal** | Direction for call/put selection. Already built. |
| **DRY_RUN infrastructure** | Same pattern: log orders, simulate with live prices. |
| **Report writer pattern** | Copy to `reports/options/`. |
| **Dashboard framework** | Add options panel alongside equity. |
| **ZerodhaClient** | Same login, same API — just NFO exchange + option symbols. |
| **Config validation** | Same `validate_ranges()` pattern for options config. |
| **Circuit breaker logic** | Same daily-loss-cap pattern. |

---

## Timeline (Rough, Not a Promise)

| Phase | When | Prerequisites |
|---|---|---|
| O-0 (conclude equity) | Next session | Existing backtest infra |
| O-1 (education + paper) | 4-8 weeks after O-0 | Sensibull account, Varsity reading |
| O-2 (data pipeline) | During O-1 | Zerodha API access |
| O-3 (backtesting) | After O-2 data collected | Historical option data |
| O-4 (dry-run mode) | After O-3 passes gate | Backtested strategy |
| O-5 (live minimum) | After O-4 passes gate | 30+ dry-run trades |
| O-6 (selling strategies) | After O-5 Stage 2 | Multi-leg order support |
