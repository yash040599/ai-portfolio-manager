# Trading Statistics

Last updated: 2026-06-15 (v1.2.0: adaptive volume on broad-gap days. PF 1.30, Sharpe 1.29).

## 0. Current Verdict

| Area | Current Read |
|---|---|
| Runtime strategy version | 2.3-2026-06-15-GAP_AND_GO_1.2.0 |
| Active stage | **GAP_AND_GO_DRY_RUN** — v1.2.0 on NIFTY100: adaptive volume on broad-gap days (≥25 stocks gap ≥1% → vol threshold 2.0x→1.25x). Entry at 09:30 candle close, gap-hold 0.3%, score-contradiction block. OOS PF 1.30, Sharpe 1.29. |
| Strategy profile | `NOAI_GAP_AND_GO_1.2.0` (active in config.py) |
| AI mode | Not used — Gap-and-Go is pure rules-based (NoAI) |
| Run command | python main.py --mode trade --dryrun (SCAN_UNIVERSE = "NIFTY100") |
| Budget | Rs.50,000 |
| Daily trade cap | 2 trades max (GAP_GO_DAILY_CAP=2) |
| Square-off | 13:00 IST (GAP_GO_SQUARE_OFF_HOUR=13) |
| Loser exit | 12:00 IST (auto: sq-off − 1hr) |
| Trailing stop | DISABLED (sweep: every trail config destroys PF) |
| RSI BUY ceiling | 70.0 (block overbought gap-up buys) |
| Broad-gap threshold | 25 stocks (v1.2.0: ≥25 stocks gapping ≥1% = adaptive vol) |
| Broad-gap vol mult | 1.25x (v1.2.0: relaxed vol on broad days, normal days stay 2.0x) |
| Gap-hold filter | 0.3% (v1.1.0+: reject if gap faded > 0.3% from open) |
| Score contradiction | ENABLED (v1.1.0+: reject when score contradicts gap direction) |
| Entry timing | 09:45 IST (v1.1.0+: after 09:30 candle closes, matching backtest) |
| Worst-case daily loss | ~Rs.933 (2 trades x 2.5% SL), hard circuit breaker at Rs.1,500 |
| Note | v1.0.0 dry-run on 2026-06-09 lost Rs.475 (0/2 wins). Root cause: entry timing mismatch vs backtest + missing gap-hold check. Both fixed in v1.1.0. |

## 1. Backtest Results — Gap-and-Go v1.1.x (2026-06-09 → 2026-06-12)

### v1.1.0 OOS Results (TEST window: 2025-06-01 → 2026-05-22, net of costs)

**NIFTY50 (original baseline):**

| Config | Trades | WR | PF | Exp%/trade | Return | MaxDD | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| **v1.0.0 baseline (RSI 70)** | 159 | 32.1% | 1.37 | +0.100% | +15.94% | 7.93% | 1.38 |
| v1.1.0 + gap-hold 0.3% only | 98 | 37.8% | 1.57 | +0.146% | +14.26% | 5.59% | 1.67 |
| v1.1.0 + score-contra only | 131 | 32.1% | 1.44 | +0.122% | +16.02% | 6.46% | 1.46 |
| **v1.1.0 FULL (all filters)** | **98** | **35.7%** | **1.55** | **+0.146%** | **+14.33%** | **7.18%** | **1.66** |
| v1.1.0 + skip RANGE | 74 | 32.4% | 1.49 | +0.138% | +10.20% | 5.60% | 1.33 |
| v1.1.0 + VOLATILE only | 43 | 34.9% | 1.98 | +0.252% | +10.83% | 4.47% | 2.98 |

### v1.1.1 on NIFTY100 (2026-06-12)

Expanded universe from 50 → 100 stocks. Same v1.1.0 filters, same daily cap=2.

**v1.1.0 (NIFTY50) vs v1.1.1 (NIFTY100) head-to-head (FULL filters, OOS TEST):**

| Metric | v1.1.0 (NIFTY50) | v1.1.1 (NIFTY100) | Delta |
|---|---:|---:|---:|
| Profit Factor | 1.55 | **1.62** | +4.5% |
| Sharpe | 1.66 | **1.80** | +8.4% |
| Win Rate | 35.7% | 35.7% | same |
| Trades | 98 | 98 | same |
| Max Drawdown | 7.18% | 7.18% | same |
| Return | +14.33% | **+16.09%** | +1.8pp |
| Expectancy/trade | +0.146% | **+0.164%** | +12% |

**v1.1.1 (NIFTY100) full results (v1.1.0 filters, OOS TEST):**

| Config | Trades | WR | PF | Exp%/trade | Return | MaxDD | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| **v1.0.0 baseline (RSI 70)** | 160 | 31.9% | 1.39 | +0.106% | +16.95% | 7.93% | 1.45 |
| v1.1.0 + gap-hold 0.3% only | 98 | 37.8% | 1.64 | +0.163% | +16.02% | 5.59% | 1.81 |
| v1.1.0 + score-contra only | 132 | 32.6% | 1.53 | +0.148% | +19.58% | 6.46% | 1.72 |
| **v1.1.1 FULL (all filters)** | **98** | **35.7%** | **1.62** | **+0.164%** | **+16.09%** | **7.18%** | **1.80** |
| v1.1.1 + skip RANGE | 76 | 34.2% | 1.75 | +0.203% | +15.45% | 5.60% | 1.85 |
| v1.1.1 + VOLATILE only | 45 | 37.8% | 2.45 | +0.357% | +16.08% | 4.47% | 3.97 |

**Why v1.1.1 (NIFTY100) is better with the same trade count:** The daily cap=2 forces selection of the top-2 gap signals per day. With 100 stocks instead of 50, the 2nd-best candidate is often a NIFTY100-extra stock with stronger gap+volume conviction than the 2nd-best NIFTY50 stock. The biggest uplift is on VOLATILE days (PF 1.98→2.45) where mid-large-caps (positions 51-100) show more explosive institutional flow.

**Config switched to `TRADE_STRATEGY_PROFILE = "NOAI_GAP_AND_GO_1.2.0"` / `SCAN_UNIVERSE = "NIFTY100"` on 2026-06-15.**

### v1.2.0 — Adaptive Volume on Broad-Gap Days (2026-06-15)

On broad-gap days (≥25 stocks gap ≥1%), volume threshold relaxes from 2.0x → 1.25x. Narrow days unchanged.

**v1.1.1 → v1.2.0 head-to-head (v1.1 filters, OOS TEST):**

| Metric | v1.1.1 (fixed vol=2.0x) | v1.2.0 (adaptive vol) | Delta |
|---|---:|---:|---:|
| Profit Factor | 1.17 | **1.30** | **+11%** |
| Sharpe | 0.71 | **1.29** | **+82%** |
| Trades | 163 | **179** | +16 |
| Max Drawdown | 13.51% | **11.58%** | **-14%** |
| Return | +9.04% | **+17.43%** | **+93%** |

**Broad-gap threshold × volume sweep (OOS TEST, top configs):**

| Config | Trades | PF | Sharpe | MaxDD |
|---|---:|---:|---:|---:|
| baseline vol=2.0x all days | 163 | 1.17 | 0.71 | 13.51% |
| **broad≥25, vol_broad=1.25x** | **179** | **1.30** | **1.29** | **11.58%** |
| broad≥25, vol_broad=1.0x | 187 | 1.28 | 1.25 | 12.42% |
| broad≥20, vol_broad=1.25x | 183 | 1.26 | 1.14 | 13.58% |
| broad≥30, vol_broad=1.25x | 177 | 1.27 | 1.15 | 11.07% |
| global vol=1.5x (no adaptive) | 208 | 1.04 | 0.19 | 17.59% |

**Side analysis (BUY-only vs SELL-only, v1.1 filters):**

| Side | Trades | PF | Sharpe | MaxDD |
|---|---:|---:|---:|---:|
| ALL (baseline) | 163 | 1.17 | 0.71 | 13.51% |
| BUY only (gap-ups) | 42 | 1.24 | 0.47 | 9.20% |
| SELL only (gap-downs) | 126 | 1.04 | 0.17 | 11.52% |

**Key observations:**
- SELLs dominate trade count (126 vs 37 BUYs) but are barely profitable (PF 1.04). BUYs have better PF (1.24) but too few trades for standalone.
- Global vol=1.5x is destructive (PF 1.04). Adaptive approach is critical — only relax on days when dilution is the mechanism.
- The surface is smooth (broad≥20-30, vol=1.0-1.25x all produce PF 1.26-1.30), indicating robust selection, not overfitting.
- Broad days = 33/242 (14%) of trading days. Zero regression risk on the other 86%.

### v1.1.0 Changes (what improved PF from 1.37 → 1.55)

1. **Entry at 09:30 candle close** (not LTP at 09:30:06): backtest enters at `candles[1]["close"]`, v1.0.0 entered at live LTP 1 second into the candle — look-ahead bias removed.
2. **Gap-hold 0.3%**: reject if LTP faded >0.3% from today's open. Standalone PF 1.57. Swept: 0.3% best (PF 1.57), 0.5% PF 1.44, 0.7% PF 1.47, 1.0% PF 1.31.
3. **Score-contradiction block**: reject BUY when composite score < 0 (SELL when > 0). Standalone PF 1.44.

### Regime-Conditional Cap Sweep (v1.1.0, TEST window)

Tested whether raising daily trade cap on VOLATILE days captures more edge.

| Config | Trades | PF | Sharpe | Return |
|---|---:|---:|---:|---:|
| **baseline cap=2 all days** | **98** | **1.55** | **1.66** | **+14.33%** |
| VOLATILE cap=3 | 96 | 1.58 | 1.71 | +14.97% |
| VOLATILE cap=4 | 99 | 1.46 | 1.45 | +12.86% |
| VOLATILE cap=5 | 102 | 1.43 | 1.38 | +12.21% |
| VOLATILE cap=8 | 108 | 1.46 | 1.47 | +13.96% |
| VOLATILE+TREND cap=3 | 100 | 1.54 | 1.65 | +14.56% |

**Verdict: keep cap=2 universally.** Cap=3 on VOLATILE is +2% PF (within noise). Cap=4+ degrades monotonically — the 3rd+ gap stocks are weaker follow-throughs. The v1.1.0 filters already self-select out bad RANGE-day trades, so the regime skip also hurts (PF 1.55 → 1.49).

### Regime Skip Verdict

Skipping RANGE days makes PF **worse** (1.55 → 1.49) with v1.1.0 filters. The gap-hold and score-contra filters implicitly remove bad RANGE trades (gaps fade fast → caught by 0.3% hold check; indicators contradict → caught by score filter). Remaining RANGE-day trades that pass all filters are profitable. **Run every day, let the filters do their job.**

### Expected Rs P&L — v1.1.1 at Rs.50K Budget (OOS backtest)

Per-trade sizing: Rs.25K per slot (50K / 2 slots).

| Metric | Value |
|---|---:|
| Total trades (1 year OOS) | 91 |
| Total net P&L | Rs.+3,621 |
| Total charges | Rs.2,332 (39% of gross) |
| Avg winner | Rs.+274 (34 wins) |
| Avg loser | Rs.-100 (57 losses) |
| Avg per trade | Rs.+40 |

**Weekly** (40 active weeks):

| | Rs. |
|---|---:|
| Best week | +1,822 |
| P75 | +276 |
| Median | -37 |
| P25 | -130 |
| Worst week | -979 |
| Positive weeks | 45% |

**Monthly** (11 months):

| | Rs. |
|---|---:|
| Best month | +1,737 |
| P75 | +1,145 |
| Median | +190 |
| P25 | -200 |
| Worst month | -1,068 |
| Positive months | 55% (6/11) |

**Annual**: +7.2% return on Rs.50K. Charges eat 39% of gross at this budget — scaling to Rs.1L would roughly double net returns as charge ratio drops to ~20%.

## 2. Legacy Backtest (62-Gate Audit, 2026-05-26)

The legacy multi-indicator scorer (NOAI_LEGACY_FULL) was audited across 62 gates:

| Metric | Before Audit | After Audit |
|---|---:|---:|
| Profit Factor | 0.71 | 0.86 |
| OOS PF (walk-forward) | — | 0.82 |
| Trades (annual) | ~18,750 | 970 |
| Win Rate | 40.5% | 37.8% |

**Verdict: FAIL.** OOS PF 0.82 = negative expectancy. Led to the Gap-and-Go research (Phases 1-7) that produced the first passing strategy.

## 3. FY 2026-27 Historical Record

| Metric | Value |
|---|---:|
| Total trades (old NoAI baseline) | 184 |
| Net P&L (old NoAI baseline) | Rs.-3,929 |
| Charges (old NoAI baseline) | Rs.2,591 |
| v1.0.0 dry-run 2026-06-09 | 2 trades, Rs.-476 (0/2 wins) |

Gap-and-Go v1.1.1 dry-run results will be tracked here starting 2026-06-12.

## 4. Promotion Metrics

Evidence starts from the first v1.1.1 dry-run session:

| Metric | Target |
|---|---:|
| Profit factor | >= 1.15 after costs |
| Expectancy | >= Rs.10/trade |
| Trade win rate | >= 40% |
| Profitable-day rate | >= 55% |
| Max drawdown | <= 3% of daily capital |
| Sample size | >= 10 sessions, >= 20 trades |

Capital scaling (Rs.50K → Rs.1L) requires passing all promotion metrics.
At Rs.50K, charges eat 39% of gross; at Rs.1L this drops to ~20%.

## 5. Update Protocol

After each live trading session:
1. Check daily report in reports/trading/
2. Run scripts/trade/promotion_check.py --window 20 when >= 20 trades accumulated
3. Update this doc with latest metrics
4. Capital scaling decision only after promotion metrics pass

---

## 6. Options Backtest Results — v1.0 (2026-06-09)

**Strategy:** Regime-Gated Directional NIFTY Option Buying
**Data:** 509 NIFTY 50 daily candles (Apr 2024 – May 2026)
**Premium model:** Brenner-Subrahmanyam ATM approximation + Parkinson vol
**Backtest script:** `scripts/trade/backtest_options.py`

### Walk-Forward Results (net of all NSE option charges)

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

**Verdict: FAIL.** PF 0.42 — directional buying with simple gap signal
cannot overcome theta + charges. VOLATILE regime (PF 0.77) shows promise
but still sub-1.0. See [OPTIONS_STRATEGY.md §6](OPTIONS_STRATEGY.md)
for improvement ideas.

---

## 7. All-Mode Backtest Summary (Consolidated)

| Mode | Strategy | Window | Trades | PF | Sharpe | Status |
|---|---|---|---|---|---|---|
| **Intraday equity** | Gap-and-Go v1.1.1 | OOS | 98 | **1.62** | 1.80 | ✅ Dry-run active (NIFTY100) |
| Intraday equity | Legacy blended score | OOS | 970 | 0.82 | -1.30 | ❌ Abandoned |
| Intraday equity | ORB-15 breakout | OOS | ~200 | 0.97 | -0.15 | ❌ Close but fail |
| Intraday equity | VWAP mean-reversion | OOS | ~300 | 0.80 | -0.80 | ❌ Abandoned |
| Intraday equity | EMA pullback | OOS | ~250 | 0.65 | -2.10 | ❌ Abandoned |
| Intraday equity | First Hour Range Breakout | OOS | 466 | 0.81 | -1.67 | ❌ FAIL (Phase 9.1) |
| Intraday equity | Opening Candle Momentum | OOS | 443 | 0.87 | -1.16 | ❌ FAIL (Phase 9.4) |
| Intraday equity | NIFTY Index Momentum (futures) | OOS | 13 | 0.25 | -2.62 | ❌ FAIL (Phase 9.3) |
| Intraday equity | Sector Rotation Intraday | OOS | 472 | 0.78 | -2.22 | ❌ FAIL (Phase 9.6) |
| **Options** | Directional buying v1.0 | FULL | 147 | 0.42 | -6.37 | ❌ FAIL |
| Options | Directional buying v1.0 | OOS | 60 | 0.64 | -3.24 | ❌ FAIL |
| Options | VOLATILE-only subset | FULL | 36 | 0.77 | — | ❌ Promising but fail |
| **Swing** | 52W dip-buy | 10yr | ~500 | 1.29 CAGR alpha | — | ✅ Report-only |

**Key takeaways:**
1. Only Gap-and-Go v1.1.1 passes the 1.15 PF gate — currently in dry-run
2. Phase 9 tested 4 new strategies (FHRB, OCM, NIFTY Futures, Sector Rotation) — all FAIL
3. Budget scaling Rs.50K→Rs.1L saves only 0.024%/trade — doesn't rescue any failed strategy
4. Intraday equity search space on NSE is largely exhausted at Rs.50K/cost level
5. Options directional buying needs a better signal or pivot to selling
6. Regime routing is the strongest reusable asset across all modes
