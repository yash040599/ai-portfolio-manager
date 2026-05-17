# Strategy Evolution

A plain-English, newest-first log of how the trading strategy changed over time. Read from top to bottom to see where the system is now, then how it got here.

## Current Direction

As of 2026-05-15, the trading program is in a Chan Research Reset. The planning reset alone was not a live strategy change, but Stage 0 runtime visibility and pause enforcement have now shipped. Future rows should only appear when a staged strategy, risk, execution, market-intel, or evidence change actually ships.

## What Goes Here

- Real strategy changes: a new entry rule, exit rule, indicator, risk gate, market-intel signal, execution behavior, or evidence gate.
- Tunings of an existing rule, such as changing a threshold, time window, or score bump.
- Categories: `Indicators`, `Risk`, `Execution`, `Market Intel`, `Infra`, and `Performance`.

## What Does Not Go Here

- Bug fixes, crashes, exception handling, and data-correctness patches.
- Pure refactors, docs-only edits, config-validator hardening, or any change with no trading or evidence behavior.
- Generic research ideas that have not shipped.

## Style Rules

1. Write in plain English. A non-coder and non-trader should understand the row.
2. Do not use file paths, function names, or internal variable names.
3. Keep each row to one short sentence unless the shipped strategy needs evidence context.
4. Include the threshold, percentage, or time when it explains the behavior.
5. Keep the original work-item number. Do not renumber old rows.
6. Skip Bug Fix rows.

## Reading Note

The table is intentionally upside down from the old version: latest changes are on top, earliest changes are at the bottom. The roadmap no longer keeps the completed archive; use git history for low-level bug-fix archaeology and this file for strategy history.

Last reorganised: 2026-05-15. Rows preserved from the prior history: 165 strategy rows; staged reset rows appear above that preserved history. Bug-fix rows remain intentionally excluded.

## Timeline

| # | Category | What shipped |
|---|----------|--------------|
| T1.6 | Infra | Daily trade reports now generate Chan evidence snapshots, and dry-run report files are separated from live report files so research artifacts cannot merge into dashboard/tax actuals. |
| T1.5 | Infra | Dry-run evidence now writes to a separate analysis database with simulated regulatory charges, keeping actual dashboard and tax P&L limited to live ledger rows. |
| T1.4 | Infra | Live-vs-replay reports now compare replay output with live candidate telemetry, logical trades, and after-cost tax-ledger outcomes under the same config hash, while explicitly flagging missing telemetry instead of treating it as parity evidence. |
| T1.3 | Infra | Replay now reports raw, gross, and net after-cost results using synthetic sizing, adverse slippage/spread fills, Zerodha charge math, and square-off-aware exits. |
| T1.2 | Infra | Replay output now writes a config-hash-stamped candidate ledger showing which historical candidates entered or were rejected, including no-trade runs. |
| T1.1 | Infra | Replay can now score historical candles through the scanner-style NoAI candle path with an injected session clock, while the old simplified score remains available for comparison. |
| T1 | Infra | Replay now has a private versioned data repo, a local SQLite/CSV seed dataset, and a backtest reader that uses that dataset before falling back to the old candle cache. |
| T0 | Infra | The trade runtime now labels runs as Stage 0 Chan Research Reset, warns when candidate telemetry is unhealthy, and blocks live order placement while the reset pause is active. |
| 258 | Risk | Live NoAI stopped sizing bigger trades by score because recent results showed higher scores were losing more; equal sizing is now the default. |
| 255 | Risk | If live quote or order-book data is still missing after retries, the bot now refuses the entry instead of assuming the spread is safe. |
| 253 | Risk | A rolling profit-factor cold-streak pause was tested, then disabled the same day because it blocked good SELL trades and added negative value. |
| 251c | Risk | A paused BUY/SELL side can now be probed again when market breadth strongly supports that side, even if NIFTY itself is flat. |
| 251b | Risk | A paused BUY/SELL side can now be probed again when NIFTY moves more than 1% in that side's favor for two scans. |
| 251a | Risk | When one side is paused, the surviving side gets a session cap if its own evidence sample is too thin. |
| 251 | Risk | The bot can pause BUY or SELL for a full day when that side has been losing badly over the last 7 trading days and NIFTY is against it. |
| 179a | Risk | The 60-second burst cap now scales by account size: small accounts stay stricter, larger accounts can take one or two more simultaneous signals. |
| 179 | Risk | Cap entries at 2 per rolling 60 seconds because recent bursts of 3 or more trades were usually losing together. |
| 246 | Risk | The post-10:00 high-score-only entry gate was shipped, then disabled after review showed the lower-score trades it blocked were actually profitable. |
| 245 | Infra | Strategy and risk changes now have a soft 2-week stability window before retuning, so samples are not invalidated by daily changes. |
| 244 | Risk | The loss-streak pause now counts any losing exit, not just hard stop-loss exits. |
| 243 | Infra | The risk:reward floor was simplified to one always-on 1.3 floor instead of several decorative time-based knobs. |
| 242 | Risk | Late-entry target compression was removed because it made afternoon trades fail the bot's own minimum risk:reward rule. |
| 241 | Risk | Superseded by #242: the earlier post-1 PM rejection rule was reclassified as self-defeating, not intentional protection. |
| 240 | Risk | Small-account daily trade cap tightened from 10 to 8 because charges require a high win rate that live results had not shown. |
| 239 | Risk | Late-entry score bump raised from +0.5 to +1.0 so afternoon trades need a clearer edge. |
| 238 | Risk | Charge cushion raised from 2x to 3x round-trip charges so tiny expected profits do not pass the gate. |
| 237 | Risk | Minimum expected profit now scales by account size: Rs.135 for tiny/small, Rs.200 normal, Rs.400 large. |
| 236 | Risk | Bid-ask spread cap is stricter for tiny/small accounts, because charges already take a larger bite there. |
| 235 | Risk | The bot no longer lowers the risk:reward bar just because it has not traded for a while. |
| 233 | Risk | Momentum-kill exits were given more settlement time and a small noise floor after live trades showed they were firing too quickly. |
| 225 | Risk | After-10 AM entry rules were simplified: one 1.3 risk:reward floor stays on all day, and budget-based slot limits handle trade count. |
| 224 | Risk | After-10 AM rules were loosened from an overly strict setup that rejected nearly every rounded target and capped larger budgets too hard. |
| 221 | Risk | Lunch-lull score-override lowered 6.0 → 5.7 — old level was rejecting too many borderline-but-profitable trades during 11:30–12:15 IST. |
| 220 | Risk | If a whole sector turns sharply against us inside one scan, tighten stop-losses on every open position in that sector right away. |
| 219 | Risk | User can list known earnings dates in a config calendar; on those dates the listed stocks are skipped from the scan. |
| 218 | Risk | Rank all sectors by average score; nudge each candidate's score up if its sector is top-ranked, down if bottom-ranked. |
| 217 | Risk | On NSE early-close days (e.g. Diwali Muhurat) the bot squares off before 13:30 instead of the usual 15:10. |
| 212 | Risk | After the score floor passes, count BUY vs SELL candidates. If today's tape is heavily one-sided, penalise the minority side. |
| 211 | Risk | If India VIX spikes ≥ 10% intraday, pause ALL new entries (every entry path), not just the periodic re-scan. |
| 210 | Risk | Before retrying a failed order, check if the broker actually accepted the first one — prevents accidental double-fire. |
| 206 | Infra | Skip the second R:R retry pass on days where no entry was rejected for R:R floor (saves a wasted pass). |
| 202 | Risk | Tighten everything for late-day entries: higher R:R floor, higher score floor, lower position cap. |
| 201 | Risk | Reject entries trading in the extreme tails of the VWAP statistical bands. |
| 200 | Risk | At score-combine time, penalise candidates where the candle pattern contradicts the technical score. |
| 199 | Risk | The post-observation recheck (#196) also requires the score direction to still match the trade direction. |
| 198 | Risk | Right after entry, if momentum stalls or flips, kill the position quickly (don't wait for the SL). |
| 196 | Risk | After the post-open observation window, recheck the score on cached candidates and drop any whose edge has decayed. |
| 195 | Risk | Don't average down: if the last exit on a stock was at a worse score, block re-entry. |
| 194 | Risk | On a strong gap day, raise the ADX threshold so we only chase high-conviction trends. |
| 192 | Risk | Pause new entries between 9:30 and 10:30 if NIFTY ADX has been < 16 for 3 consecutive scans (chop guard). |
| 191 | Risk | When the broker's SL-M fired before our software did, label the exit STOP_LOSS (not EXTERNAL_CLOSE). |
| 190 | Risk | Reject entries whose latest candle pattern points the opposite way. |
| 188 | Execution | Same-direction signal-decay exit: if the score that justified the entry has decayed badly and the position is < 1R in profit, book and move on. |
| 180 | Risk | Reject BUY when the stock is within 1% of its +20% upper circuit; same for SELL near the −20% lower circuit. |
| 177 | Infra | Post-trade rejection-audit script: every entry the bot SKIPPED is logged with the reason, so we can audit which gates are net-helpful. |
| 174 | Risk | If the score on a held position flips hard the other way, exit immediately at market — don't wait for the SL to hit. |
| 173 | Risk | Reject entries that fight the day's opening gap (e.g. SELL on a strong gap-up open). |
| 172 | Execution | Stagnant exit has two tiers: catch fast adverse moves quickly, and slow drifters at 45 min. |
| 168 | Risk | If today's P&L gives back ≥ 1.5% from its intraday peak, pause new entries (lock the day's progress). |
| 166 | Risk | Daily-loss safety gates also count unrealised (open-position) P&L, not only realised. |
| 165 | Risk | Risk gates auto-tighten for small accounts (under Rs.30k) — one losing trade hurts a Rs.20k account much more than a Rs.5L one. |
| 164 | Execution | Skip new entries between 11:30 and 12:15 IST unless the signal is strong (\|score\| ≥ 5.7). Lunch hour is the choppiest window. |
| 163 | Risk | Soft daily-loss stop at −1.5% (pauses new entries) on top of the hard −3% circuit breaker. The pause auto-releases if P&L recovers. |
| 162 | Execution | Profit target must clear at least 2× round-trip charges, otherwise the trade isn't worth it. |
| 161 | Execution | After ANY exit on a stock, block the same stock+direction from being re-entered for 30 minutes. |
| 157 | Risk | New entries also need ADX trend strength and DI direction to agree (filters out chop). |
| 156 | Execution | Stagnant exit is now direction-aware: a slowly-bleeding position counts as "stagnant", a slowly-winning one does not. |
| 147 | Risk | The Relative-Volume floor adapts to the time of day (e.g. lunch hour is naturally quieter, so the floor is lower). |
| 146 | Risk | Before entry, walk the top-5 order-book levels to estimate true fill price; reject if slippage is too high. |
| 145 | Execution | Position size scales with each stock's ATR so risk-per-trade is constant in rupee terms (a calm stock gets more shares than a volatile one). |
| 142 | Infra | At startup, sanity-check every numeric config value to catch typos before trading. |
| 138 | Risk | VWAP gates only switch on after 10:15 (VWAP needs ~1 hour of candles to be reliable). |
| 137 | Execution | The 5-min observation window is measured from market-open, not from when the bot started. |
| 136 | Execution | Skip the expiry-day position reduction when budget is under Rs.1 lakh (small-account flexibility). |
| 135 | Execution | Expiry-day post-open observation: 15 → 30 min, with a 15-min minimum if the bot starts late. |
| 134 | Risk | Stop-loss must be at least 0.8% away (1.0% on expiry day); if too tight, widen the target to keep R:R intact. |
| 133 | Risk | Give a freshly-resumed or manually-opened position a 10-minute grace before time-decay or loser-exit can touch it. |
| 132 | Risk | If the score has flipped sharply (delta ≥ 8) since last scan, wait one more cycle before entering. |
| 131 | Risk | Don't chase price stretched far from VWAP (BUY > +0.8% above, SELL < −0.8% below) unless score is very strong (≥ 6). |
| 130 | Risk | RSI block made symmetric: also block BUY at RSI > 75 and SELL at RSI < 25. |
| 129 | Risk | After charges, the trade must still offer at least 1:1 risk:reward. |
| 128 | Risk | After a stagnant exit, don't re-enter the same stock in the same direction. |
| 127 | Execution | Minimum expected gross profit raised Rs.50 → Rs.75 (about 2× round-trip charges). |
| 126 | Execution | Lunch lull (12:00-1:30): give positions an extra 15 min before stagnant-exit fires. |
| 125 | Risk | No BUY when price is below VWAP; no SELL when price is above VWAP. |
| 124 | Risk | Cap total trades at 12 per day to prevent over-trading. |
| 123 | Risk | Cap expiry-day trades at 5 to limit churn. |
| 122 | Risk | Expiry-day post-open observation window: 15 min (vs 5 min normal). |
| 121 | Infra | Tick-rounding helper made a public utility; documented Kite's avg-volume gap. |
| 120 | Infra | Show "next candle scan" and "next opportunity scan" timestamps in the live log. |
| 119 | Risk | Expiry-day score bump increased 0.5 → 1.0. |
| 118 | Execution | On expiry days, give positions an extra 15 min before the stagnant-exit timer fires. |
| 117 | Execution | After 1 PM, repurpose unused SHORT slots for BUYs if a strong (score ≥ 4) BUY signal appears. |
| 116 | Risk | If the score has dropped since the last scan, skip the re-entry. |
| 115 | Risk | Don't go SHORT a stock with RSI > 70; don't go LONG a stock with RSI < 30. |
| 110-111 | Infra | SQLite WAL mode; trades table has a unique-fill constraint to prevent duplicates. |
| 107 | Execution | Position size scales with score conviction (simplified Kelly approach). |
| 104 | Execution | When budget shrinks mid-day, promote the next fallback candidate. |
| 103 | Execution | Scanner returns the full pre-filtered candidate pool, not a fixed top-N. |
| 97 | Execution | Feed the 15-min re-scan results back to Claude for the next decision (AI mode). |
| 96 | Execution | Claude's role narrowed to ranking / vetoing using StochRSI confluence (AI mode). |
| 95 | Indicators | Boost stock score by ±0.5 if its sector is leading the move. |
| 94 | Indicators | Use Stochastic-RSI as a confluence signal (info only, not a hard gate). |
| 93 | Execution | At entry time, require Relative Volume ≥ 0.7× to confirm interest. |
| 92 | Execution | Risk:reward floor that adapts by time of day, with a mid-day retry pass. |
| 91 | Execution | Claude review-cycle stretched 20 → 30 minutes (AI mode only). |
| 90 | Execution | Default profit target lowered 1.5% → 1.2%. |
| 82 | Execution | Stagnant-position exit reduced from 90 min to 45 min. |
| 81 | Infra | Importing the daily Zerodha sheet updates the charges field when P&L matches. |
| 80 | Infra | Manually-placed (EXTERNAL) positions get their own unique order id. |
| 79 | Infra | Per-trade charges (brokerage, STT, GST, stamp) computed and stored in the tax ledger. |
| 78 | Market Intel | Use the previous day's FII/DII flows as a directional bias at start of day. |
| 76 | Market Intel | Diversify long/short smartly using the per-side score gap. |
| 75 | Infra | New CLI flag `--nifty` to choose universe size (50 / 100 / 150 / 200). |
| 75 | Infra | New CLI flag `--max` to set today's risk budget. |
| 74 | Execution | Sync any manually-placed Zerodha trades into the bot every 15 minutes. |
| 73 | Execution | Allow more fallback candidates (whole pre-filtered list, not just top N). |
| 68 | Execution | Time-decay closes positions sooner: 40% → 25% of normal hold time. |
| 67 | Execution | Trail stop in smaller steps: 65% → 50% of gained profit. |
| 66 | Execution | Shorter post-open observation window: 15 min → 5 min. |
| 65 | Risk | Reject any trade whose expected gross profit is under Rs.50. |
| 64 | Risk | Stop opening fresh SHORT positions after 1 PM. |
| 61 | Indicators | SuperTrend made configurable (period 7, multiplier 2.0 for intraday). |
| 60 | Execution | Use exchange SL-M orders so the broker enforces the stop even if the bot is offline. |
| 59 | Execution | Default risk:reward target is 1.5:1 (configurable). |
| 58 | Execution | Position size scales with the user's chosen budget. |
| 56 | Execution | Skip stocks priced under Rs.100 from the scan universe. |
| 55 | Execution | Use LIMIT orders for entry, fall back to MARKET if not filled. |
| 54 | Execution | Fewer trades, bigger per-trade size: max positions 5 → 3. |
| 53 | Risk | Diversify long/short across slots; close-call scores can fill all slots one-direction. |
| 52 | Indicators | Cap at +3 if RSI is already extreme (≥ 75 for BUY, ≤ 25 for SELL). |
| 51 | Indicators | Penalise stocks already extended in our direction (e.g. up >2% by 9:30 → −3 score). |
| 50 | Execution | Late-entry rules and time-decay exits are mutually exclusive (use whichever fits the entry time). |
| 49 | Execution | Use pure ATR for SL and target (no longer mixed with %-based defaults). |
| 43 | Infra | Standalone script to verify our recorded trades against Zerodha. |
| 42 | Market Intel | Read the 9:08 AM pre-open auction to detect today's gap before the bell. |
| 41 | Risk | If Thursday is an NSE holiday (Holi, Eid, etc.), apply expiry-day rules to the actual expiry day instead. |
| 38 | Infra | Better slippage model in dry-run so back-tests look realistic. |
| 36 | Indicators | Reward fast-rising scores; penalise scores that are losing momentum. |
| 35 | Execution | Reject trades whose bid-ask spread is wider than 0.3%. |
| 34 | Indicators | Use VWAP standard-deviation bands (±1σ, ±2σ) for stretch detection. |
| 33 | Indicators | Use Fibonacci retracement levels in the trade direction. |
| 32 | Execution | Late-entry targets are smaller (1 PM: −20%, 2 PM: −25%) since less time is left. **REMOVED by #242** — see entry below. |
| 31 | Indicators | Skip indicators if today doesn't yet have enough candles for them. |
| 30 | Indicators | Look back 3 days of candles so MACD and Bollinger have time to warm up. |
| 29 | Market Intel | Thursday F&O expiry day: wider stops (+0.3 ATR), one fewer slot, +0.5 score bump. |
| 28 | Indicators | Use ADX as a trend-strength filter. |
| 27 | Risk | At 2:45 PM, exit any position still in a loss (don't carry losers into the close). |
| 26 | Risk | Re-check the per-sector cap at the moment of entry, not just at scan time. |
| 25 | Infra | Trade journal and end-of-day performance analytics. |
| 23 | Market Intel | Read India VIX to classify the current volatility regime. |
| 22 | Risk | When the NIFTY regime flips against an open position, tighten its stop-loss. |
| 21 | Risk | Raise the score bar for new entries after losses (NoAI mode). |
| 20 | Risk | After 3 stop-losses in a row, pause new entries for 30 minutes (whipsaw guard). |
| 19 | Risk | Hard cap on circuit-breaker trips per day. |
| 18 | Indicators | Detect Bollinger Band squeezes (low-volatility set-ups). |
| 17 | Indicators | Confirm the daily trend with hourly EMA alignment. |
| 16 | Risk | After a circuit-breaker trip, pause 30 min before resuming, with a max of 2 trips/day. |
| 15 | Risk | Shrink position size after recent losses. |
| 14 | Risk | Exit a position that has gone nowhere for 45 minutes (NoAI mode). |
| 13 | Execution | Show a clear "minimum capital needed" hint at startup. |
| 12 | Market Intel | Re-check the NIFTY regime (bullish / bearish / neutral) every 15 minutes. |
| 11 | Execution | Re-scan for fresh opportunities every 30 minutes whenever a position slot is free. |
| 10 | Execution | Take partial profits — sell 1/3 at 1.5× risk, then trail the rest with a 50% step. |
| 9 | Indicators | Use the pre-market gap (today's open vs yesterday's close) as an early signal. |
| 8 | Risk | Hold at most 2 positions in any single sector at once. |
| 7 | Indicators | Confirm trend direction with the MACD histogram. |
| 6 | Indicators | Detect Opening Range Breakouts (use the 2nd 5-min candle). |
| 5 | Risk | A trade going against the NIFTY trend needs a much stronger signal (score ≥ 3) to be allowed. |
| 4 | Indicators | Use yesterday's High / Low / Close as today's support and resistance levels. |
| 3 | Indicators | A pattern matters more when it just formed; weight by age (latest 1.0×, older 0.7× / 0.4×). |
| 2 | Indicators | Use Relative Volume (today vs 20-day average) as a base stock filter. |
| 1 | Indicators | Need a recent volume spike to trust a candle pattern. |
