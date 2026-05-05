# Strategy Evolution

A plain-English log of how the trading **strategy** has changed over time. One row per shipped strategy idea, in the order it shipped. Skim this to see how the system grew from day 1 to today.

## What goes in this file

- Real strategy changes: a new entry rule, a new exit rule, a new indicator, a new risk gate, a new market-intel signal, a new execution behaviour.
- Tunings of an existing rule (e.g. *"lunch-lull threshold lowered from 6.0 to 5.7"*) — yes, that IS a strategy change. Include it.
- Categories: `Indicators`, `Risk`, `Execution`, `Market Intel`, `Infra`, `Performance`.

## What does NOT go here

- **Bug fixes.** Crash fixes, exception handling, data-correctness patches — these belong only in commit messages and `STRATEGY_ROADMAP.md` (where they keep their `Bug Fix` category). Skip them in this file even if they are filed as a Roadmap item.
- Pure refactors, doc-only edits, config validator hardening — anything with no behaviour change.

## Style rules (Copilot — follow these whenever you add a row)

1. **Plain English.** Anyone (non-coder, non-trader) should be able to read the row and understand what changed. No jargon, no acronyms beyond ones a casual investor knows (RSI, ADX, VWAP, SL, NSE).
2. **No code references.** No file paths, no function names, no internal variable names, no `_underscore_things`.
3. **One short sentence.** Aim for under ~25 words.
4. **Numbers belong in the row.** If the change has a threshold, percentage, or time, include it (e.g. *"between 11:30 and 12:15 IST"*, *"≥ 5.7"*, *"+0.5 score boost"*).
5. **Add a tiny example only if it makes the row clearer.** Format: trailing `(e.g. ...)` or *"so..."*. Skip the example if the row is already obvious.
6. **Keep the original work-item number from the Roadmap.** Never renumber rows.
7. **Skip Bug Fix rows.** If the matching Roadmap row's category is `Bug Fix`, do **not** add it here — it stays in the Roadmap only.

### Good rows
```
| 164 | Execution | Skip new entries between 11:30 and 12:15 IST unless the signal is very strong (|score| ≥ 5.7). Lunch hour is the choppiest window. |
| 217 | Risk | On NSE early-close days (e.g. Diwali Muhurat) the bot squares off before 13:30 instead of the usual 15:10. |
| 220 | Risk | If a whole sector turns sharply against us inside one scan, tighten stop-losses on every open position in that sector right away. |
```

### Bad rows (don't write like this)
```
| X | Risk | `_check_vix_spike()` reads `engine._vix_spike_active` after `set_vix_spike(True)` — see manager_v2.py L547 |
| X | Bug Fix | Fixed crash in `_update_exchange_sl` when broker times out |
| X | Risk | Stale-SL-M re-attribution + idempotent transactional cancel-replace inside _sector_cascade_protect |
```

> **Source of truth:** `STRATEGY_ROADMAP.md` (Completed section). When you ship a strategy item, add a row here in the same commit, following the rules above.
> Last regenerated: 2026-05-05. Items: 157 strategy rows (#1 → #253). Bug-fix rows are intentionally excluded; see `STRATEGY_ROADMAP.md` for the full Bug Fix log.

## Timeline

| # | Category | What shipped |
|---|----------|--------------|
| 1 | Indicators | Need a recent volume spike to trust a candle pattern. |
| 2 | Indicators | Use Relative Volume (today vs 20-day average) as a base stock filter. |
| 3 | Indicators | A pattern matters more when it just formed; weight by age (latest 1.0×, older 0.7× / 0.4×). |
| 4 | Indicators | Use yesterday's High / Low / Close as today's support and resistance levels. |
| 5 | Risk | A trade going against the NIFTY trend needs a much stronger signal (score ≥ 3) to be allowed. |
| 6 | Indicators | Detect Opening Range Breakouts (use the 2nd 5-min candle). |
| 7 | Indicators | Confirm trend direction with the MACD histogram. |
| 8 | Risk | Hold at most 2 positions in any single sector at once. |
| 9 | Indicators | Use the pre-market gap (today's open vs yesterday's close) as an early signal. |
| 10 | Execution | Take partial profits — sell 1/3 at 1.5× risk, then trail the rest with a 50% step. |
| 11 | Execution | Re-scan for fresh opportunities every 30 minutes whenever a position slot is free. |
| 12 | Market Intel | Re-check the NIFTY regime (bullish / bearish / neutral) every 15 minutes. |
| 13 | Execution | Show a clear "minimum capital needed" hint at startup. |
| 14 | Risk | Exit a position that has gone nowhere for 45 minutes (NoAI mode). |
| 15 | Risk | Shrink position size after recent losses. |
| 16 | Risk | After a circuit-breaker trip, pause 30 min before resuming, with a max of 2 trips/day. |
| 17 | Indicators | Confirm the daily trend with hourly EMA alignment. |
| 18 | Indicators | Detect Bollinger Band squeezes (low-volatility set-ups). |
| 19 | Risk | Hard cap on circuit-breaker trips per day. |
| 20 | Risk | After 3 stop-losses in a row, pause new entries for 30 minutes (whipsaw guard). |
| 21 | Risk | Raise the score bar for new entries after losses (NoAI mode). |
| 22 | Risk | When the NIFTY regime flips against an open position, tighten its stop-loss. |
| 23 | Market Intel | Read India VIX to classify the current volatility regime. |
| 25 | Infra | Trade journal and end-of-day performance analytics. |
| 26 | Risk | Re-check the per-sector cap at the moment of entry, not just at scan time. |
| 27 | Risk | At 2:45 PM, exit any position still in a loss (don't carry losers into the close). |
| 28 | Indicators | Use ADX as a trend-strength filter. |
| 29 | Market Intel | Thursday F&O expiry day: wider stops (+0.3 ATR), one fewer slot, +0.5 score bump. |
| 30 | Indicators | Look back 3 days of candles so MACD and Bollinger have time to warm up. |
| 31 | Indicators | Skip indicators if today doesn't yet have enough candles for them. |
| 32 | Execution | Late-entry targets are smaller (1 PM: −20%, 2 PM: −25%) since less time is left. **REMOVED by #242** — see entry below. |
| 33 | Indicators | Use Fibonacci retracement levels in the trade direction. |
| 34 | Indicators | Use VWAP standard-deviation bands (±1σ, ±2σ) for stretch detection. |
| 35 | Execution | Reject trades whose bid-ask spread is wider than 0.3%. |
| 36 | Indicators | Reward fast-rising scores; penalise scores that are losing momentum. |
| 38 | Infra | Better slippage model in dry-run so back-tests look realistic. |
| 41 | Risk | If Thursday is an NSE holiday (Holi, Eid, etc.), apply expiry-day rules to the actual expiry day instead. |
| 42 | Market Intel | Read the 9:08 AM pre-open auction to detect today's gap before the bell. |
| 43 | Infra | Standalone script to verify our recorded trades against Zerodha. |
| 49 | Execution | Use pure ATR for SL and target (no longer mixed with %-based defaults). |
| 50 | Execution | Late-entry rules and time-decay exits are mutually exclusive (use whichever fits the entry time). |
| 51 | Indicators | Penalise stocks already extended in our direction (e.g. up >2% by 9:30 → −3 score). |
| 52 | Indicators | Cap at +3 if RSI is already extreme (≥ 75 for BUY, ≤ 25 for SELL). |
| 53 | Risk | Diversify long/short across slots; close-call scores can fill all slots one-direction. |
| 54 | Execution | Fewer trades, bigger per-trade size: max positions 5 → 3. |
| 55 | Execution | Use LIMIT orders for entry, fall back to MARKET if not filled. |
| 56 | Execution | Skip stocks priced under Rs.100 from the scan universe. |
| 58 | Execution | Position size scales with the user's chosen budget. |
| 59 | Execution | Default risk:reward target is 1.5:1 (configurable). |
| 60 | Execution | Use exchange SL-M orders so the broker enforces the stop even if the bot is offline. |
| 61 | Indicators | SuperTrend made configurable (period 7, multiplier 2.0 for intraday). |
| 64 | Risk | Stop opening fresh SHORT positions after 1 PM. |
| 65 | Risk | Reject any trade whose expected gross profit is under Rs.50. |
| 66 | Execution | Shorter post-open observation window: 15 min → 5 min. |
| 67 | Execution | Trail stop in smaller steps: 65% → 50% of gained profit. |
| 68 | Execution | Time-decay closes positions sooner: 40% → 25% of normal hold time. |
| 73 | Execution | Allow more fallback candidates (whole pre-filtered list, not just top N). |
| 74 | Execution | Sync any manually-placed Zerodha trades into the bot every 15 minutes. |
| 75 | Infra | New CLI flag `--max` to set today's risk budget. |
| 75 | Infra | New CLI flag `--nifty` to choose universe size (50 / 100 / 150 / 200). |
| 76 | Market Intel | Diversify long/short smartly using the per-side score gap. |
| 78 | Market Intel | Use the previous day's FII/DII flows as a directional bias at start of day. |
| 79 | Infra | Per-trade charges (brokerage, STT, GST, stamp) computed and stored in the tax ledger. |
| 80 | Infra | Manually-placed (EXTERNAL) positions get their own unique order id. |
| 81 | Infra | Importing the daily Zerodha sheet updates the charges field when P&L matches. |
| 82 | Execution | Stagnant-position exit reduced from 90 min to 45 min. |
| 90 | Execution | Default profit target lowered 1.5% → 1.2%. |
| 91 | Execution | Claude review-cycle stretched 20 → 30 minutes (AI mode only). |
| 92 | Execution | Risk:reward floor that adapts by time of day, with a mid-day retry pass. |
| 93 | Execution | At entry time, require Relative Volume ≥ 0.7× to confirm interest. |
| 94 | Indicators | Use Stochastic-RSI as a confluence signal (info only, not a hard gate). |
| 95 | Indicators | Boost stock score by ±0.5 if its sector is leading the move. |
| 96 | Execution | Claude's role narrowed to ranking / vetoing using StochRSI confluence (AI mode). |
| 97 | Execution | Feed the 15-min re-scan results back to Claude for the next decision (AI mode). |
| 103 | Execution | Scanner returns the full pre-filtered candidate pool, not a fixed top-N. |
| 104 | Execution | When budget shrinks mid-day, promote the next fallback candidate. |
| 107 | Execution | Position size scales with score conviction (simplified Kelly approach). |
| 110-111 | Infra | SQLite WAL mode; trades table has a unique-fill constraint to prevent duplicates. |
| 115 | Risk | Don't go SHORT a stock with RSI > 70; don't go LONG a stock with RSI < 30. |
| 116 | Risk | If the score has dropped since the last scan, skip the re-entry. |
| 117 | Execution | After 1 PM, repurpose unused SHORT slots for BUYs if a strong (score ≥ 4) BUY signal appears. |
| 118 | Execution | On expiry days, give positions an extra 15 min before the stagnant-exit timer fires. |
| 119 | Risk | Expiry-day score bump increased 0.5 → 1.0. |
| 120 | Infra | Show "next candle scan" and "next opportunity scan" timestamps in the live log. |
| 121 | Infra | Tick-rounding helper made a public utility; documented Kite's avg-volume gap. |
| 122 | Risk | Expiry-day post-open observation window: 15 min (vs 5 min normal). |
| 123 | Risk | Cap expiry-day trades at 5 to limit churn. |
| 124 | Risk | Cap total trades at 12 per day to prevent over-trading. |
| 125 | Risk | No BUY when price is below VWAP; no SELL when price is above VWAP. |
| 126 | Execution | Lunch lull (12:00-1:30): give positions an extra 15 min before stagnant-exit fires. |
| 127 | Execution | Minimum expected gross profit raised Rs.50 → Rs.75 (about 2× round-trip charges). |
| 128 | Risk | After a stagnant exit, don't re-enter the same stock in the same direction. |
| 129 | Risk | After charges, the trade must still offer at least 1:1 risk:reward. |
| 130 | Risk | RSI block made symmetric: also block BUY at RSI > 75 and SELL at RSI < 25. |
| 131 | Risk | Don't chase price stretched far from VWAP (BUY > +0.8% above, SELL < −0.8% below) unless score is very strong (≥ 6). |
| 132 | Risk | If the score has flipped sharply (delta ≥ 8) since last scan, wait one more cycle before entering. |
| 133 | Risk | Give a freshly-resumed or manually-opened position a 10-minute grace before time-decay or loser-exit can touch it. |
| 134 | Risk | Stop-loss must be at least 0.8% away (1.0% on expiry day); if too tight, widen the target to keep R:R intact. |
| 135 | Execution | Expiry-day post-open observation: 15 → 30 min, with a 15-min minimum if the bot starts late. |
| 136 | Execution | Skip the expiry-day position reduction when budget is under Rs.1 lakh (small-account flexibility). |
| 137 | Execution | The 5-min observation window is measured from market-open, not from when the bot started. |
| 138 | Risk | VWAP gates only switch on after 10:15 (VWAP needs ~1 hour of candles to be reliable). |
| 142 | Infra | At startup, sanity-check every numeric config value to catch typos before trading. |
| 145 | Execution | Position size scales with each stock's ATR so risk-per-trade is constant in rupee terms (a calm stock gets more shares than a volatile one). |
| 146 | Risk | Before entry, walk the top-5 order-book levels to estimate true fill price; reject if slippage is too high. |
| 147 | Risk | The Relative-Volume floor adapts to the time of day (e.g. lunch hour is naturally quieter, so the floor is lower). |
| 156 | Execution | Stagnant exit is now direction-aware: a slowly-bleeding position counts as "stagnant", a slowly-winning one does not. |
| 157 | Risk | New entries also need ADX trend strength and DI direction to agree (filters out chop). |
| 161 | Execution | After ANY exit on a stock, block the same stock+direction from being re-entered for 30 minutes. |
| 162 | Execution | Profit target must clear at least 2× round-trip charges, otherwise the trade isn't worth it. |
| 163 | Risk | Soft daily-loss stop at −1.5% (pauses new entries) on top of the hard −3% circuit breaker. The pause auto-releases if P&L recovers. |
| 164 | Execution | Skip new entries between 11:30 and 12:15 IST unless the signal is strong (\|score\| ≥ 5.7). Lunch hour is the choppiest window. |
| 165 | Risk | Risk gates auto-tighten for small accounts (under Rs.30k) — one losing trade hurts a Rs.20k account much more than a Rs.5L one. |
| 166 | Risk | Daily-loss safety gates also count unrealised (open-position) P&L, not only realised. |
| 168 | Risk | If today's P&L gives back ≥ 1.5% from its intraday peak, pause new entries (lock the day's progress). |
| 172 | Execution | Stagnant exit has two tiers: catch fast adverse moves quickly, and slow drifters at 45 min. |
| 173 | Risk | Reject entries that fight the day's opening gap (e.g. SELL on a strong gap-up open). |
| 174 | Risk | If the score on a held position flips hard the other way, exit immediately at market — don't wait for the SL to hit. |
| 177 | Infra | Post-trade rejection-audit script: every entry the bot SKIPPED is logged with the reason, so we can audit which gates are net-helpful. |
| 180 | Risk | Reject BUY when the stock is within 1% of its +20% upper circuit; same for SELL near the −20% lower circuit. |
| 188 | Execution | Same-direction signal-decay exit: if the score that justified the entry has decayed badly and the position is < 1R in profit, book and move on. |
| 190 | Risk | Reject entries whose latest candle pattern points the opposite way. |
| 191 | Risk | When the broker's SL-M fired before our software did, label the exit STOP_LOSS (not EXTERNAL_CLOSE). |
| 192 | Risk | Pause new entries between 9:30 and 10:30 if NIFTY ADX has been < 16 for 3 consecutive scans (chop guard). |
| 194 | Risk | On a strong gap day, raise the ADX threshold so we only chase high-conviction trends. |
| 195 | Risk | Don't average down: if the last exit on a stock was at a worse score, block re-entry. |
| 196 | Risk | After the post-open observation window, recheck the score on cached candidates and drop any whose edge has decayed. |
| 198 | Risk | Right after entry, if momentum stalls or flips, kill the position quickly (don't wait for the SL). |
| 199 | Risk | The post-observation recheck (#196) also requires the score direction to still match the trade direction. |
| 200 | Risk | At score-combine time, penalise candidates where the candle pattern contradicts the technical score. |
| 201 | Risk | Reject entries trading in the extreme tails of the VWAP statistical bands. |
| 202 | Risk | Tighten everything for late-day entries: higher R:R floor, higher score floor, lower position cap. |
| 206 | Infra | Skip the second R:R retry pass on days where no entry was rejected for R:R floor (saves a wasted pass). |
| 210 | Risk | Before retrying a failed order, check if the broker actually accepted the first one — prevents accidental double-fire. |
| 211 | Risk | If India VIX spikes ≥ 10% intraday, pause ALL new entries (every entry path), not just the periodic re-scan. |
| 212 | Risk | After the score floor passes, count BUY vs SELL candidates. If today's tape is heavily one-sided, penalise the minority side. |
| 217 | Risk | On NSE early-close days (e.g. Diwali Muhurat) the bot squares off before 13:30 instead of the usual 15:10. |
| 218 | Risk | Rank all sectors by average score; nudge each candidate's score up if its sector is top-ranked, down if bottom-ranked. |
| 219 | Risk | User can list known earnings dates in a config calendar; on those dates the listed stocks are skipped from the scan. |
| 220 | Risk | If a whole sector turns sharply against us inside one scan, tighten stop-losses on every open position in that sector right away. |
| 221 | Risk | Lunch-lull score-override lowered 6.0 → 5.7 — old level was rejecting too many borderline-but-profitable trades during 11:30–12:15 IST. |
| 224 | Risk | After 10 AM the bot was over-tightening: required R:R of 1.5 (which equals the default R:R, so tick rounding rejected almost everything) and capped concurrent trades at 2 regardless of budget (a Rs.5L account lost 5 of its 7 normal slots). Loosened the after-10 AM R:R bar to 1.3 and made the late-trade slot cap scale with budget (e.g. 4 slots on a Rs.5L account, still 2 on a Rs.20K account). |
| 225 | Risk | Simplified the after-10 AM rules. The R:R bar is now a single always-on floor of 1.3 (same value, same behaviour after 10 AM, plus protection against over-relaxation before 10 AM too). The separate after-10 AM slot cap was dropped — the budget-based slot count (e.g. 5 slots on a Rs.2L account) already does the right thing all day, and the late-entry score bump (each new trade after 10 AM still needs a higher score) is the real edge filter. Two fewer config knobs, same protection. |
| 233 | Risk | Retuned the post-entry momentum kill (#198) after the first live day showed it killing 4/4 morning entries on sub-spread micro-moves: extended the settlement grace from 60s to 180s, added a 0.40% adverse-move noise floor, and widened the kill window to 5 minutes. |
| 235 | Risk | Pinned R:R adaptive relaxation to the hard floor — "I haven't traded in an hour, lower the bar" is the same instinct that bankrupts retail traders, and the always-on 1.3 floor already rejected those trades anyway. Relaxation is now a no-op with the log labels preserved. |
| 236 | Risk | Bid-ask spread cap now scales with the budget regime. A small account with a 0.27% per-trade charge hurdle can't afford a 0.30% spread on top — TINY/SMALL accounts get a 0.20% effective cap; NORMAL/LARGE keep 0.30%. |
| 237 | Risk | Minimum-profit floor now scales with the budget regime: Rs.135 on TINY/SMALL (3× round-trip charges), Rs.200 NORMAL, Rs.400 LARGE. Old flat Rs.75 was less than 2× charges on small slots. |
| 238 | Risk | Charge-cushion multiple bumped from 2× to 3× round-trip charges so trades guarantee 2× cushion instead of 1× (industry retail-intraday rule of thumb). |
| 239 | Risk | Late-entry score bump raised from +0.5 to +1.0 — first live day showed +0.5 was too gentle to materially change which trades cleared the bar. |
| 240 | Risk | SMALL-regime daily trade cap tightened from 10 to 8 (matches TINY). Math: on a Rs.50K account a >55% win rate is needed to sustain 10+ trades/day at the 0.27% charge hurdle, which the live ledger has not yet demonstrated. |
| 241 | Risk | (SUPERSEDED BY #242.) Documented the post-1 PM rejection of default-ATR trades as intentional. Same-day analyst pass caught it as a self-defeating loop instead — see #242. |
| 242 | Risk | Removed the late-entry target compression at entry. The 20%/25% target cuts after 1 PM/2 PM were dropping default R:R below the always-on 1.3 hard floor, so every default afternoon trade was rejected by our own arithmetic. Pro intraday desks don't pre-shrink entry targets — drift on open positions is owned by stagnant-exit, momentum kill, open-position time-decay, and the 3:10 PM hard square-off. Targets are now honoured at entry across the day. |
| 243 | Infra | Collapsed the R:R floor system into a single uniform `RR_HARD_FLOOR = 1.3`. After #235 / #242, every code path was already returning 1.3 — the seven decorative knobs (morning/afternoon/late/relaxed floors, retry step, relax-after, hour selectors) and the dead relaxation/retry branches in the engine and the manager added zero behaviour, only readability cost. Same 1.3 floor everywhere; the give-up-after-5-empty-scans signal is the keeper. |
| 244 | Risk | The 30-minute "pause new entries after 3 losses in a row" guard now counts ANY losing exit (stop-loss, momentum kill, stagnant exit, signal decay, or late-day loser exit), not just hard stop-losses. End-of-day square-offs and operator closes still don't count. Today's session-1 lost 4 morning trades in a row and the old guard never fired because none were stop-losses; the broader counter would have paused the bot at exit #3. |
| 245 | Infra | A documented stability-window guardrail: every shipped strategy/risk/execution change has a soft 2-week freeze before further tuning of the same gate, so live ledger samples are not invalidated by daily re-tuning. Bug-fix commits (token `[bugfix-during-stability-window]`) are explicitly exempt. Process change only — does not affect any trade decision. |
| 246 | Risk | **Shipped 2026-04-28, then disabled 2026-05-05 after EV audit revealed the gate's predicate was empirically inverted on the live ledger.** Original design: after 10:00 IST the bot will no longer open trades unless the technical score is at least 7. Below that, the in-trade rescue exits cannot save the trade if the thesis breaks, so taking it would be admitting a position only the hard stop-loss can close. (Motivated by JIOFIN 2026-04-28 — entered at score 3.8, flipped to −5.5 within 15 min, ran to stop-loss for −Rs.183.) Morning entries (09:30-10:00) were unaffected because a fresh trend gives the position room to recover. **Disable rationale (phase-2 audit, 2026-05-05, 24 sessions / 157 bot-only positions):** the cohort the gate would now block (n=39 entries with |score|<7 post-10:00, all pre-ship) was net Rs.+618 at 53.8% WR, with EVERY score sub-bin net-positive (the strongest being |score|∈[5,6) at 70% WR / Rs.+323). The post-ship cohort that PASSED the gate (n=9 entries with |score|≥7) lost Rs.−451 at 33% WR. The gate's premise — "score≥7 post-10:00 is best" — is contradicted by every band of the blocked cohort. Same disable-with-trigger playbook as #253; re-enable trigger lives in roadmap #254 Awaiting-Data. The +1.0 late-entry score-bump (#239) stays active untouched. |
| 179 | Risk | Cap entries at 2 per rolling 60 seconds. The 04-22 → 05-05 audit found that bursts of 3+ entries inside 60 seconds ended with all of them losing together on 92% of occurrences — the burst itself is a regime signature (correlated tape pressure), not three independent setups. The cap is conservative (theoretical max ~120/hr, well above the 12-trade daily cap) so it only bites genuine bursts. |
| 251 | Risk | Auto-pause one trade direction (BUY or SELL) for the whole session when, over the trailing 7 trading days, that side has taken ≥ 10 trades, won ≤ 30% of them, AND the NIFTY is on the contra side. The bot keeps trading the other direction unimpeded. Origin: 2026-04-23 → 2026-05-05 BUY-side WR collapsed to 12.5% (5 of 40) while SELL held 42.9% — every individual day's gates fired correctly but the side-skew was unmistakable in aggregate. The bot used to be blind to its own multi-day directional drift; this gate is the catch. |
| 253 | Risk | Rolling-PF multi-day cold-streak gate. **Shipped enabled, then disabled same day after counterfactual replay revealed it is redundant with #251 and net-negative once #251 is active.** Original design: session-wide pause armed at startup when the trailing 3 trading days are net ≤ −Rs.300 AND profit-factor < 0.6 AND ≥ 5 trades. Audit over 17 evaluable sessions: #251 alone gains Rs.+503 vs baseline; #251+#253 gains Rs.+387 (incremental Rs.−116). Sources of negative incremental EV: false-pause on 04-10 (single big-loss day on 04-09 armed the gate, blocked a +Rs.488 winner); blocks the SELL side that has been profitable during the BUY collapse (05-05 SELL net was +Rs.28). Industry rationale (Kelly / fractional Kelly): when uncertain about edge, reduce stake, do not bet zero. Code retained for future re-enable; flag set to False. |
