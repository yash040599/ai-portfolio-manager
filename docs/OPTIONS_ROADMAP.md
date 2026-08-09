# Options Mode — Roadmap

> **Created:** 2026-06-06 | **Updated:** 2026-08-10
> **Status:** **SHELVED for the tested structures; ONE candidate left to test.**
> Both backtested strategies FAIL on real-data-validated premiums. NIFTY carries
> a genuine variance risk premium (+1.77 vol points median, positive 81% of the
> time) but it is too small for a symmetric defined-risk structure to capture
> once protection is bought. Next and probably final test: **calendar spread on
> recorded real premiums**.
> **Context:** Intraday equity Gap-and-Go v1.1 passes OOS PF 1.55.
> Options mode built as a separate engine (`--mode options`).
> See [OPTIONS_GUIDE.md](OPTIONS_GUIDE.md) for plain-English primer.

---

## Market structure — who actually makes money (SEBI, 2026-08-10 review)

Before any further work, the base rates. From SEBI's own studies:

| Finding | Source |
|---|---|
| **93%** of 1.13 crore individual F&O traders lost money FY22-FY24; aggregate loss **Rs.1.81 lakh crore** | [SEBI PR 22/2024](https://www.sebi.gov.in/media-and-notifications/press-releases/sep-2024/updated-sebi-study-reveals-93-of-individual-traders-incurred-losses-in-equity-fando-between-fy22-and-fy24-aggregate-losses-exceed-1-8-lakh-crores-over-three-years_86906.html) |
| Only **1%** of individuals earned profits above Rs.1 lakh after costs | ibid. |
| Individuals spent **Rs.26,000 each** on transaction costs in FY24 alone (Rs.50,000 crore over three years) | ibid. |
| FY24-25: **91%** lost, net **Rs.1.05 lakh crore**, up 41% YoY, average loss Rs.1.1 lakh | SEBI FY24-25 study |
| Prop desks made **Rs.33,000 cr** and FPIs **Rs.28,000 cr** gross in FY24, against individuals' **-Rs.61,000 cr** | SEBI PR 22/2024 |
| **96% of proprietary profits and 97% of FPI profits came from ALGORITHMIC trading** | ibid. |

**Reading:** "People make money in options" is true and almost entirely describes
colocated algorithmic desks, funded by retail losses plus retail transaction
costs. We will not compete with them on latency or execution. Any edge we claim
must come from something they are not competing for — realistically, patience and
holding period, not speed.

This does not by itself forbid trading options. It does set the prior: assume no
edge until a backtest on **real** premiums says otherwise, and treat any thin
result as noise.

---

## Candidate strategies — tested vs untested

### Tested and rejected

| Strategy | Result |
|---|---|
| Directional buying (regime + gap signal) | PF 0.42; 30-combo sweep max 0.53 |
| Short iron condor, symmetric | PF 0.46 OOS; 144-combo sweep max 1.02 |
| — across tenor (1/3/5/10/15/20 DTE) | best OOS 0.77 |
| — across size (1/3/10/30 lots) | plateaus 0.84 — costs are *not* the constraint |

### Untested, ranked by expected value

| # | Idea | Why it might work where the condor failed | Testable now? |
|---|---|---|---|
| **1** | **Calendar spread** — sell near weekly, buy far weekly, same strike | Fails differently. Net **long vega**, so a vol spike helps rather than kills; max loss capped at the debit. Monetises the *term structure* of theta (near-dated decays ~1/sqrt(T) faster) instead of betting the index stays in a corridor. | **Yes** — 15,314 same-strike near/far pairs recorded, 5,096 at the 7-day gap. Real premiums, no model. |
| 2 | IV-percentile conditional selling | Our condor sold **unconditionally**, every expiry. VRP is regime-dependent — fat when IV is high, can be negative when low. Selling only in the top IV quartile is standard professional practice and is a genuine omission in what we tested. | Partially — ~7 months of recorded IV; more needed |
| 3 | Delta-hedged short straddle on NIFTY futures | The mechanism prop desks actually use: harvest VRP directly instead of through a payoff shape that taxes it. | Needs futures data + daily rehedge model |
| 4 | Ratio / broken-wing structures | Asymmetric R:R; can be built for positive credit with no risk on one side. | Yes, same data |

**Next action:** build `backtest_options_condor.py`'s sibling for calendars and
run it on the recorded premiums. Coverage of weeklies is currently ~1-2 months,
enough for a directional read but **not** a promotion decision — keep backfilling
weekly and re-run on a fuller sample before any dry-run.

---

## Current Posture

| Area | Status |
|---|---|
| Stage | **Phase O-3 — two strategies tested, both FAIL. Real data pipeline now live.** |
| Code | `modes/options/` — manager, scanner, order engine, tracker, report writer |
| Backtest 1 | v1.0 directional buying: PF 0.42 FULL, 0.64 OOS (FAIL). 30-combo sweep max 0.53 |
| Backtest 2 | O-3.2 iron condor: PF 0.71 FULL, 0.46 OOS (FAIL). 144-combo sweep max 1.02 |
| Premium model | **Validated against real quotes 2026-08-08 — median 0.90x actual.** Verdicts stand |
| Data | ✅ `option_candles` in `data/options.db` — real premiums, backfilled from Kite |
| Dashboard | Dry-run page has Intraday/Options mode switcher |
| CLI | `python main.py --mode options` (dry-run default) |
| **Next step** | **Calendar spread backtest on recorded real premiums** |
| Capital | **Rs.0 — no live trading until strategy passes 1.15 gate** |

### O-2.5 answered (2026-08-08)

`record_option_chain.py --probe` settled the data question, and the answer was
better than assumed:

| Question | Answer |
|---|---|
| Historical option candles from Kite? | **Yes** — daily, 15-minute and 1-minute all return for listed contracts, back to listing date |
| Expired contracts? | **No** — absent from `instruments("NFO")`, no resolvable token, unreachable forever |
| Practical consequence | Backfill the **full life** of every listed contract before it expires. Weeklies list ~1 month ahead, so one run captures the whole premium path |
| NIFTY weekly expiry weekday | **Tuesday** (not Thursday) |
| NIFTY lot size (Kite-reported) | **65** |

So the pipeline is not limited to daily snapshots: `--backfill` pulls complete
OHLC history per contract. It remains strictly forward-only overall — anything
not captured before expiry is lost permanently.

### Premium model validated (2026-08-08)

`validate_premium_model.py` priced 2,006 real OTM observations with the same
model the backtests used:

| Moneyness | CE | PE |
|---|---|---|
| 0-1% OTM | 1.02x | 0.88x |
| 1-2% OTM | 0.99x | 0.92x |
| 2-3% OTM | 0.68x | 1.03x |
| 3%+ OTM | 0.42x | 1.24x |
| **Overall median** | **0.90x** | |

Near the money — where the condor's short strikes sit — the model is accurate
to within a few percent. It under-prices far-OTM calls, which is where condor
*protection* is bought, so the modelled net credit was if anything **too
generous**. The PF 0.46 verdict is therefore optimistic, not pessimistic.

Caveat: ~1 month of data across 2 expiries in a single volatility regime.
Indicative, not definitive.

### Why the next step is an edge, not another structure

Both failures now survive their own robustness checks:

- The condor sensitivity sweep stays under PF 1.0 even at an implausible 1.8x
  IV uplift, so the result is not a calibration artefact.
- Its model-free diagnostic shows the pricer is calibrated: 43.7% observed
  breach rate vs ~40% implied by 0.20-delta shorts.
- The premium model now checks out against real quotes at 0.90x median.
- Correcting the lot size (25 -> 65) and the expiry weekday (Thu -> Tue) moved
  OOS PF from 0.45 to 0.46 — i.e. the conclusion is insensitive to both.

The structures are not the problem. At 1 DTE the sellable corridor is +/-0.68%
while NIFTY's median move to settlement is 0.50% and its p80 is 1.12% — NIFTY
weekly options are priced roughly efficiently.

---

## Variance risk premium — measured on REAL premiums (2026-08-08)

`analyse_vrp.py` inverts Black-Scholes on the recorded premiums to get implied
vol, then compares it with the realised vol that actually followed. This is the
only structural reason option selling makes money anywhere, so it is the
decisive question for options mode.

| Metric | Value |
|---|---|
| Mean implied vol | 11.86% |
| Mean realised vol | 9.23% |
| Mean VRP | **+2.63 vol points** |
| Median VRP | **+1.77 vol points** |
| Observations with VRP > 0 | **34/42 = 81%** |

**There IS a positive variance risk premium on NIFTY.** Implied exceeds
subsequent realised four times out of five, by a median of 1.77 vol points —
in line with global equity-index norms.

Coverage caveat: 42 observations, almost all at 11-30 DTE. Only 3 fall in the
6-10 DTE bucket and none at 2-5 DTE, which is where the condor traded. The
number above is a longer-tenor premium and must not be assumed to hold at 1-2
DTE where gamma dominates theta.

### But the condor cannot capture it

Two follow-up tests, both on the corrected model:

| Test | Result |
|---|---|
| Tenor sweep (DTE 1/3/5/10/15/20, hold to expiry) | OOS PF 0.49 / 0.47 / 0.50 / **0.77** / 0.66 / 0.76 — improves at longer tenors, never reaches 1.15 |
| Size sweep (1 / 3 / 10 / 30 lots at DTE 10) | OOS PF 0.77 / 0.81 / 0.83 / **0.84** — plateaus |

The size test matters: ~95% of a condor's charges are the fixed Rs.20-per-order
brokerage across 8 orders, so scaling notional amortises them almost entirely.
PF still stalls at 0.84. **Transaction costs are not the binding constraint —
the structure is.**

The arithmetic: a 0.20-delta condor with 200-point wings collects ~Rs.36 credit
against ~Rs.164 max loss, i.e. 1:4.5. Break-even needs an ~82% win rate; the
observed rate is 70%. A 2.6-vol-point premium is simply too small to fund that
risk:reward once wings are bought.

### Verdict for options mode

The premium is real but **small**, and every defined-risk structure available to
us gives most of it away buying protection. Naked selling — the only structure
that keeps the whole premium — is permanently hard-blocked, correctly.

Options mode is therefore **SHELVED**, not merely paused, pending one of:

1. **A NIFTY-level directional or volatility-forecasting signal.** The VRP tells
   us *when* selling is favoured on average; a forecast would tell us *which*
   weeks, which is what turns a 2.6-point premium into a tradable edge.
2. **Delta-hedged short volatility using NIFTY futures.** This harvests the VRP
   directly rather than through a payoff structure that taxes it, but needs
   continuous hedging and futures margin.
3. **More recorded data at 2-10 DTE**, which would test whether short-tenor VRP
   is materially richer than the longer-tenor number measured here.

Keep `record_option_chain.py --backfill` running weekly regardless: the dataset
is forward-only, costs nothing to accumulate, and is the prerequisite for all
three paths above.

---

## Tooling added 2026-08-08

| Script | Purpose |
|---|---|
| `scripts/trade/option_pricing.py` | European Black-Scholes + volatility smile + the single NSE charge model shared by every options backtest |
| `scripts/trade/backtest_options_condor.py` | O-3.2 condor backtest — walk-forward, `--sweep`, `--sensitivity`, `--diagnose` |
| `scripts/trade/record_option_chain.py` | `--probe` (O-2.5), `--backfill` (full contract history), daily snapshot, `--summary` |
| `scripts/trade/validate_premium_model.py` | Scores the synthetic model against recorded real premiums |
| `scripts/trade/analyse_vrp.py` | Measures the NIFTY variance risk premium from recorded premiums |
| `ZerodhaClient.get_option_chain()` / `.list_option_expiries()` | O-2.2, previously marked done but never written |

---

## Backtest Results — O-3.2 Iron Condor (2026-08-08)

**Strategy:** Regime-gated short iron condor into weekly expiry, entered 1 day
before expiry at the 0.20-delta wings with 200-point protection, squared off at
the expiry close (never exercised — STT on ITM exercise is 0.125% of intrinsic).

| Window | Trades | Win Rate | PF | Sharpe | Net P&L |
|---|---|---|---|---|---|
| FULL | 89 | 55.1% | 0.70 | -1.06 | -Rs.55,055 |
| TRAIN | 45 | 64.4% | 1.02 | 0.15 | +Rs.1,414 |
| TEST (OOS) | 44 | 45.5% | **0.45** | -2.46 | -Rs.56,469 |

**Sweep:** 144 combinations (DTE 0-3 x delta 0.10-0.30 x wing 100-300 x SL
0/1.5/2.5). Best OOS PF **1.02** — nothing reaches the 1.15 gate.

**Sensitivity:** PF climbs with the IV assumption but plateaus below break-even
— 1.0x uplift -> 0.14, 1.35x -> 0.44, 1.8x -> 0.94. Skew is near-irrelevant.
The failure is therefore not a calibration artefact.

**Why it failed — the model-free explanation:**
At 1 DTE the 0.20-delta shorts sit only +/-0.68% from spot, but NIFTY's move
from entry open to expiry close has a median of 0.58% and an 80th percentile of
1.20%. The corridor you can sell is narrower than the distribution of moves, and
the credit does not compensate. Settlement landed outside the corridor 43.8% of
the time versus ~40% implied — i.e. **NIFTY weekly options are priced roughly
efficiently**, so there is no free theta to harvest without a real edge.

**Reproduce:**
```bash
python scripts/trade/backtest_options_condor.py --diagnose
python scripts/trade/backtest_options_condor.py --sensitivity
python scripts/trade/backtest_options_condor.py --sweep
```

---

## Corrections found while building O-3.2 (2026-08-08)

| Item | Was | Should be | Impact |
|---|---|---|---|
| `config.OPTIONS_NIFTY_LOT_SIZE` | 25 | **65** (Kite-reported 2026-08-08) | 2.6x position-sizing error in live/dry-run — **still needs fixing in config.py** |
| Expiry weekday assumption | Thursday | **Tuesday** (NSE moved index weeklies) | Mis-dated every simulated trade; `--expiry-switch` now handles the changeover |
| STT on option sale | 0.0625% | **0.1%** (Oct 2024) | Under-charged every v1.0 trade |
| GST base | brokerage only | brokerage + exchange + SEBI | Under-charged every v1.0 trade |
| O-2.2 `get_option_chain()` | marked ✅ | did not exist | Now implemented in `ZerodhaClient` |

Charge rates now live in one place (`scripts/trade/option_pricing.py`) so the
two backtests cannot drift apart. Re-running v1.0 with the corrected model left
its verdict unchanged at PF 0.42.

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
| O-2.2 | Build `get_option_chain(index, expiry)` → returns strikes, premiums, OI | ✅ real implementation 2026-08-08 (was wrongly marked done) |
| O-2.3 | Store option chain snapshots in `data/options.db` | ✅ `record_option_chain.py` |
| O-2.4 | Fetch NIFTY weekly option chain daily for 2+ weeks (build history) | ⏳ **START NOW — time-sensitive** |
| O-2.5 | Verify: can we get historical option premiums from Zerodha API? | ⏳ run `record_option_chain.py --probe` |

**Exit criteria:** Option chain data flowing to SQLite. Historical data
available for backtesting.

**How to run O-2.4/O-2.5:**
```bash
python scripts/trade/record_option_chain.py --probe     # answers O-2.5 once
python scripts/trade/record_option_chain.py             # daily snapshot
python scripts/trade/record_option_chain.py --summary   # accumulated history
```
Schedule the plain form once per trading day (ideally near the close). ~40
trading days of snapshots are needed before a measured-premium backtest means
anything.

---

### Phase O-3: Backtest Strategies (2-4 weeks)

**Goal:** Same rigor as equity — walk-forward, OOS, after costs.

| Step | Action | Done? |
|---|---|---|
| O-3.1 | Backtest Strategy 1 (directional buying on VOLATILE days) | ✅ PF 0.42 FAIL |
| O-3.2 | Backtest Strategy 2 (iron condor on RANGE expiry days) | ✅ PF 0.45 OOS FAIL |
| O-3.3 | Walk-forward: train on first half, test on second half | ✅ both strategies |
| O-3.4 | Calculate P&L net of ALL option costs (brokerage, STT, exchange, GST) | ✅ unified in `option_pricing.py` |
| O-3.5 | **Verdict:** any strategy OOS PF ≥ 1.15? | ❌ **No.** Best of 174 tested combos = 1.02 |
| O-3.6 | Re-run both on RECORDED premiums once O-2.4 has ~40 days | ⏳ blocked on data |

**Exit criteria:** At least one strategy passes OOS PF ≥ 1.15 after costs,
OR both fail and options mode is shelved.

**Status:** both fail on synthetic premiums. Options mode is **paused, not
shelved** — paused pending real data, because the synthetic premium is the
weakest link in both verdicts.

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

---

## New Strategy Candidates (from Phase 9 + research, 2026-06-12)

Moved here from TRADE_ROADMAP — these are options-mode strategies, not intraday equity.

| # | Strategy | Signal family | Why it might work on NSE | Data needed | Effort | Priority |
|---|---|---|---|---|---|---|
| **C.3** | **Expiry Day Short Straddle (Iron Condor)** | Theta decay | Sell OTM NIFTY strangles on weekly expiry day (Thursday). Theta crush is extreme — OTM options lose 80%+ value on expiry day. Our regime classifier identifies RANGE days (39% of days) which are perfect for premium selling. Well-documented edge in India — the variance risk premium is real. Iron condor caps max loss. | Options chain data (Zerodha API) | High | **HIGH** |
| **D.2** | **Expiry-Day Theta Selling (regime-gated)** | Theta decay + regime | Same concept as C.3 but explicitly gated by our morning regime classifier. On RANGE days (39%): sell OTM strangles/iron condors. On VOLATILE/TREND: skip (gamma risk too high). Complementary to Gap-and-Go which profits on VOLATILE days — this profits on the days Gap-and-Go sits out. Net effect: profitable across ALL regime types. | Options chain historical data (Sensibull/Zerodha) | High | **HIGH** |
| **C.7** | **Calendar Spread on NIFTY Futures** | Term structure | Buy far-month NIFTY future, sell near-month. Market-neutral. Profits from term structure mean-reversion. Zerodha Varsity Module 10 covers this. Defined risk, low margin. | NIFTY futures data (multiple expiries) | Medium | **LOW** |
| **D.8** | **Calendar Spread on NIFTY Options** | Theta differential | Sell this-week expiry option, buy next-week expiry at same strike. Profits from near-term theta decaying faster. Market-neutral-ish, defined risk (max loss = net debit). NIFTY weekly options have enough term structure anomaly. | Options chain with Greeks (multi-expiry) | Medium | **LOW** |
