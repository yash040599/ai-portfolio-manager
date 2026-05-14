# Swing Trading — Operator Guide

This is the **user-facing walkthrough** for the swing-trading mode
(Phase 4). For the design spec (setup definitions, scoring rules, risk
math) see [SWING_STRATEGY.md](SWING_STRATEGY.md). For the change log
(every shipped item with rationale) see [SWING_ROADMAP.md](SWING_ROADMAP.md).

> **Report-only by permanent design.** The bot never places broker
> orders. It scans, scores, recommends, and persists your manual
> entries / exits to its own ledger. You always trade on Zerodha Kite
> yourself. Items that previously tracked execution automation
> (CNC order wrappers, GTT/OCO, reconciliation, ledger isolation)
> are in the [SWING_ROADMAP Removed section](SWING_ROADMAP.md).

---

## Contents

1. [What it does](#1-what-it-does)
2. [Dashboard surface (`/swing`)](#2-dashboard-surface--swing)
3. [CLI command reference](#3-cli-command-reference)
4. [The 5 setup detectors + scoring](#4-the-5-setup-detectors--scoring)
5. [The 52-week dip-buy strategy + backtest evidence](#5-the-52-week-dip-buy-strategy--backtest-evidence)
6. [AI overlay — costs, sticky cache, prompt](#6-ai-overlay--costs-sticky-cache-prompt)
7. [Compare up to 4 stocks](#7-compare-up-to-4-stocks)
8. [The Add+ flow (manual entry confirmation)](#8-the-add-flow-manual-entry-confirmation)
9. [HTTP API reference](#9-http-api-reference)
10. [Persistence + reports + read-only inspection](#10-persistence--reports--read-only-inspection)
11. [Tuning knobs (Config)](#11-tuning-knobs-config)
12. [Common questions / failure modes](#12-common-questions--failure-modes)

---

## 1. What it does

Daily-candle scanner for **multi-day delivery (CNC) trades** held 2
trading days to 8 weeks against the NIFTY 50 / 100 / 150 / 200
universes (configurable). Each scan:

1. Fetches the last ~10 years of daily candles for every symbol in
   the universe (via Zerodha, chunked through the 2000-day API cap,
   cached in `data/candle_cache.db`).
2. Runs the **technical scanner** — 4 setup detectors plus
   cross-setup modifiers. Anything scoring ≥2.0 becomes an
   ACCEPTED candidate.
3. Runs the **dip-buy scanner** — flags any name ≥18% below its
   rolling 52-week high as a `52W_DIP` candidate.
4. Unifies the priority ranking (technical first, dip-buy after,
   each ordered by score descending).
5. Optionally runs the **Claude AI overlay** on the top 15
   candidates (~Rs.45 max per scan on Pro plan).
6. Persists the run to `data/swing.db` and writes a text report
   to `reports/swing/<YYYY>/<MM>/swing_report_<DD>.txt`.

You **read** the result on the dashboard or the CLI, **trade manually**
on Kite, then click **Add+** (or run `--mode swing --confirm <ID>
--qty N --price P`) to log the position into the swing book.

---

## 2. Dashboard surface (`/swing`)

Open `http://localhost:8765/swing` (or whatever port the dashboard
launched on). Top to bottom:

### Realised Swing P&L card
Gross / charges / net P&L summary aggregated from every CLOSED
position in `swing_positions`. Updated whenever Mark-Exit-Done is
clicked.

### Daily Scan card
- **Run Scan** button — kicks off the full universe scan; spinner +
  status banner during; auto-refreshes when done.
- **Use Claude AI overlay** checkbox — adds the AI overlay to the
  top 15 candidates (cost-confirm dialog before the run).
- **Swing Capital** input — defaults to Zerodha available margin (or
  Rs.1,00,000 if the broker is unreachable, with a precise inline
  reason).
- **AI cost preview** banner — shows worst-case cost up-front so the
  click never feels open-ended.
- Auto-scan: when the dashboard server is running after 3:30 PM IST,
  it auto-submits one NoAI scan per day on page-open if today's run
  is missing.

### Analyse a Single Stock card
Type any NSE ticker → "Analyse" → result card renders below with
the same metrics the recommendation table shows for full-scan
candidates. Includes:
- Status (ACCEPTED / REJECTED with full reason + per-setup score
  breakdown when nothing qualifies)
- Setup type, composite score, sector
- Current price, 52w-rolling high, % below 52w high
- RSI, RS vs NIFTY, volume ratio
- Suggested entry / stop / target / qty / R:R
- Optional AI overlay (~Rs.3) via the in-card checkbox
- **Add+** button to log the position into the swing book
- Link to the full detail page

### Compare Stocks (up to 4) card
Side-by-side scoring matrix. Two seed paths:
1. Free-text input — `HDFCBANK, SBIN, ICICIBANK, KOTAKBANK`
2. Sector dropdown — picks the top 4 by `SECTOR_MAP` order
   (BANKING → HDFCBANK, ICICIBANK, KOTAKBANK, AXISBANK).

The result table highlights the winning value per row in green and
shows a **"X wins / Y of Z metrics"** tally per stock so the user
can see WHY one stock is rated better than another. Stock symbols
in the column headers link to their detail page.

### Entry Recommendations table
Up to ~80 ACCEPTED candidates from the latest non-snapshot,
non-search-box full-scan run. Columns:

| Column | Notes |
|---|---|
| # | Priority rank (technical first, dip-buy after) |
| Symbol | Click → detail page |
| Setup | BREAKOUT / PULLBACK / TREND_CONT / SUPPORT_REV / 52W_DIP |
| % Below 52w High | Computed from the rolling 252-day max-close |
| Live Price | Polled every 5s from Zerodha quotes |
| Entry / Stop / Target / Qty / R:R | NoAI risk plan |
| Reason | First reason from the score breakdown (rest in tooltip) |
| Action | **Add+** button — single-click adds to swing book |

### Open Swing Book table
Every OPEN position (a position you confirmed via Add+). Columns:
Symbol / Qty / Entry / Live Price / P&L / Stop / Target / R / Action /
**Mark Exit Done**. Live price + P&L tick every 5s. Mark-Exit-Done
prompts for exit qty + price; closes the position with auto-computed
charges (STT + GST + exchange + SEBI + stamp duty for delivery).

### Top-right error toast
Any external-API failure (Zerodha auth, Claude rate-limit, network)
surfaces as a top-right toast within 5 seconds. Auth-shaped Zerodha
errors automatically rename `data/access_token.json` → `.invalid` and
flip the auth pill so you know to re-login.

### Per-stock detail page (`/swing/<SYMBOL>`)
Reachable from any symbol link. Shows:
- Recommendation summary (price, entry/stop/target, R:R, qty, score)
- **Stock Health Check** — 9 plain-English checks (long-term trend,
  medium-term trend, short-term trend, all aligned, RSI sweet spot,
  volume vs avg, weekly trend, RS vs NIFTY, R:R)
- "Why this stock?" reasoning bullets
- Dip-buy signal (when applicable) with full reasoning
- **Analyse with AI (~Rs.3)** button — populates the AI Analysis
  panel for this single stock. Sticky for 7 days.
- AI Analysis panel — VERDICT first, then THESIS / NEWS /
  FUNDAMENTAL / PEERS / RISKS / CORP-ACTION / WHY-FAIL.
- "Analysed N days ago" freshness badge above the AI text.

---

## 3. CLI command reference

Every dashboard action has a CLI sibling. Both surfaces call the same
persistence helpers so they can never disagree.

| Command | What it does |
|---|---|
| `python main.py --mode swing` | Run today's NoAI scan. Prints accepted candidates + open book. Refuses to scan before market close (uses yesterday's completed daily candle when run pre-close). |
| `python main.py --mode swing --ai` | Same scan + Claude AI overlay capped at top `SWING_AI_MAX_CANDIDATES` (default 15) by `priority_rank`. Pre-AI snapshot written first so a Ctrl+C still leaves a usable report. |
| `python main.py --mode swing --nifty 100` | Override scan universe (`50` / `100` / `150` / `200`). |
| `python main.py --mode swing --actions` | List all PENDING swing actions. Prints action_id, symbol, qty, suggested entry/stop. |
| `python main.py --mode swing --positions` | List all OPEN swing positions. Prints position_id, symbol, managed_qty, entry, stop, entry date. |
| `python main.py --mode swing --confirm <ID> --qty N --price P [--stop X]` | Confirm a PENDING ENTRY action — same flow as the dashboard's **Add+** button. Mandatory: `--qty`, `--price`. Optional: `--stop` (overrides `action.suggested_stop`). |
| `python main.py --mode swing --skip <ID> [--reason "..."]` | Skip a PENDING action. Idempotent — re-skipping returns success. (Dashboard no longer offers Skip; CLI keeps it for batch scripting.) |
| `python main.py --mode swing --compare HDFCBANK,SBIN,ICICIBANK,KOTAKBANK` | Compare up to 4 NSE symbols side-by-side. Prints metrics-x-stocks matrix marking the winning value per row. |
| `python main.py --mode swing --compare-sector BANKING` | Auto-pick the top 4 in a sector (`SECTOR_MAP` order) and run the comparison. Sector aliases: `BANK`/`BANKING`, `IT`/`TECH`, `PHARMA`/`HEALTH`, `AUTO`, `ENERGY`/`OIL`, `METALS`, `FMCG`/`CONSUMER`, `INFRA`/`POWER`, `TELECOM`, `CAPGOODS`/`DEFENCE`, `FINANCE`/`NBFC`. |
| `python main.py --mode swing --backtest` | Run the X/Y dip-buy parameter sweep on cached candle history. Writes `reports/backtest/ath_backtest.{txt,json}`. Pure-offline; never touches broker. |

**Single-stock analyse from the CLI** (the search-box equivalent):
```powershell
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from core.zerodha_client import ZerodhaClient; from core.logger import Logger; from config import Config; from modes.swing.scanner import SwingScanner; z = ZerodhaClient(Config, Logger('S')); z.login(interactive=False); s = SwingScanner(Config, z, Logger('S')); c, _ = s.scan_one('SBIN'); print(c.status, c.setup_type, c.score, c.dip_from_ath_pct)"
```

---

## 4. The 5 setup detectors + scoring

| Setup | Triggers when |
|---|---|
| **BREAKOUT** | Close > 20-day high or 50-day high; volume confirms (≥1.5×); above SMAs; RS positive. **+1.0 NR7 bonus** when today's H-L is the smallest of the trailing 7 bars AND volume is expanding (Mark Minervini's VCP variant). |
| **PULLBACK_UPTREND** | Trend-up gate (close > SMA-200 AND SMA-50 > SMA-200); pullback to EMA-20 (within 3%) or SMA-50 (within 2%); RSI 40-60. |
| **TREND_CONTINUATION** | SMAs stacked (EMA-20 > SMA-50 > SMA-200); not extended (close ≤5% above EMA-20); volume OK; weekly trend up. |
| **SUPPORT_REVERSAL** | **Hard gate: weekly trend must be turning up** (10-week SMA rising). Within 3% of SMA-200, within 10% of 52w low, RSI recovering 25-40. The hard gate prevents "catching a falling knife". |
| **52W_DIP** | Close ≥`SWING_DIP_PCT` (default 18%) below the rolling 252-day max-close. Score = the dip% itself, so deeper dips rank higher. |

### Cross-setup modifiers
| Modifier | Applies to | Effect |
|---|---|---|
| **52w-high proximity** | All 4 technical setups | +0.5 to +2.0 bonus for continuation setups (BREAKOUT, TREND_CONTINUATION) when within 5% / 3% / 1.5% / at-or-above the 52w high. **Same magnitude as a penalty** for mean-reversion setups (PULLBACK, SUPPORT_REVERSAL) — a "pullback" near the 52w high is by definition extended, not a pullback. |
| **NR7 + volume** | BREAKOUT only | +1.0 when both NR7 AND vol_ratio ≥1.2 (volume contraction → expansion). |
| **Sector rotation** | All ACCEPTED candidates | +0.5 when the candidate's sector is in today's top 3 by mean RS. Computed manager-level from the full candidate pool (sectors with <2 candidates excluded). |
| **NONE filter** | All technical setups | If the best base+modifier score is <2.0, the candidate is REJECTED with an explicit per-setup score breakdown so you can see what was close. |

### Worst-case score envelope
- BREAKOUT: ~9.0 max in synthetic test, ~8.0–8.5 in live data
- TREND_CONTINUATION: ~7.0 max, ~6.0–6.5 live
- PULLBACK_UPTREND: ~7.5 max, ~6.0 live (capped by 52w penalty)
- SUPPORT_REVERSAL: ~5.5 max, ~5.0 live
- 52W_DIP: unbounded by % dip; typically 18–30% in market corrections

The score numbers are arbitrary units — what matters is the
**ordering** within a setup family and across the unified table.

---

## 5. The 52-week dip-buy strategy + backtest evidence

The dip-buy mechanic (calibrated against the standalone
[market-research](https://github.com/yash040599/market-research)
repo's 10-year, 121-combo X/Y backtest):

1. Track the **rolling 252-day max-close** (≈ 52 weeks) for each
   stock — `Config.SWING_DIP_LOOKBACK_DAYS = 252` by default.
2. Buy `SWING_DIP_BUY_AMOUNT` (default Rs.10,000) when the close
   is ≥`SWING_DIP_PCT` (default 18%) below that high.
3. Sell on first close ≥`SWING_DIP_TARGET_PCT` (default 12%) above
   the buy price.
4. Re-arm immediately after a sell.

**Backtest results** (every cell of X∈[10,20] × Y∈[10,20] was profitable):

| | XIRR | Trade count |
|---|---|---|
| (X=20, Y=10) | 29.5% | 328 |
| **(X=18, Y=12) — current default** | 25.6% | 264 |
| (X=10, Y=10) — worst | 20.0% | 487 |

NIFTY-50 reference: 13–14% CAGR over the same 10-year window.

Default (18, 12) was chosen over (20, 10) because dip frequency at
X=18 is roughly 2× higher than at X=20 (more shots over a multi-year
horizon) and Y=12 retains comfortable headroom over real-world
charges + execution noise on a Rs.10,000 ticket.

The standalone backtest used the all-time-high reference (`max(closes)`
over 10y); the live scanner uses the 252-day rolling reference because
(a) the 52w high resets every year so the trigger is responsive to the
current regime, (b) it's the canonical large-cap-investor anchor (and
the standard breakout-watch level for trend followers). The post-COVID
5-year slice of the data shows the 52w-high variant tracks within
~150 bps XIRR of the ATH variant in the (X∈[16,20], Y∈[10,13]) sweet
spot.

To re-tune for your risk tolerance, edit the four knobs in `config.py`:
`SWING_DIP_PCT`, `SWING_DIP_TARGET_PCT`, `SWING_DIP_BUY_AMOUNT`,
`SWING_DIP_LOOKBACK_DAYS`.

---

## 6. AI overlay — costs, sticky cache, prompt

### Costs
Per Claude call: `Config.CLAUDE_COST_PER_CALL` (default Rs.3 on Pro
plan). Three AI surfaces:

| Surface | Cost per click |
|---|---|
| Single stock on detail page **Analyse with AI** button | 1× — `~Rs.3` |
| Single stock on `/swing` search box (AI checkbox) | 1× — `~Rs.3` |
| Full scan with `--ai` / dashboard AI checkbox | Capped at `SWING_AI_MAX_CANDIDATES = 15` × `~Rs.3` = **~Rs.45 max** |

The cap protects against runaway cost on a wide NIFTY 100 scan after
a market correction (~50 ACCEPTED candidates would cost ~Rs.150
without the cap). Cap selects top-N by unified `priority_rank` so
the budget always lands on the strongest signals.

### Sticky cache (carry-forward)
Every AI response is persisted to `swing_candidates.ai_overlay_json`
and survives across runs. When you re-scan a symbol the next day:

- **`SwingManager.run()`** copies any cached overlay (≤7 days old)
  onto the freshly-saved candidate so the dashboard recommendation
  table shows the existing analysis.
- **`candidate_by_symbol()`** does the same lookup at read time —
  belt-and-braces, so any code path reading a candidate gets the
  AI overlay if any cached one exists.
- **Search-box `analyse_one`** carries forward when AI is NOT
  requested (avoids re-charging for an analysis you already paid for).

The "Analysed N days ago" badge above the AI text on the detail
page tells you how stale the cached analysis is. Click "Analyse
with AI" to force a refresh.

Error-only payloads (`{"error":"..."}`) are filtered out of the
carry-forward so a transient Claude failure doesn't shadow a good
older response.

### Prompt structure
The prompt asks for **8 mandatory sections** with **VERDICT first**
(so truncation can never hide the conclusion):

1. **VERDICT** — BUY / WATCH / SKIP + one-sentence justification.
2. **THESIS** — 2-3 bullets with concrete catalysts.
3. **RECENT NEWS / CATALYSTS** (last 60 days) — earnings, M&A,
   regulatory, promoter pledge, block deals. "None known" when
   nothing concrete.
4. **FUNDAMENTAL CONTEXT** — P/E vs sector, ROE/ROCE band, debt
   sense, promoter holding stability + pledge status. "Unknown"
   over fabrication.
5. **PEER COMPARISON** — 1-2 listed sector peers, one line each.
6. **RISKS** — 2-3 specific to this name.
7. **CORPORATE-ACTION SANITY CHECK** — split / bonus / demerger
   in last 24 months. Critical for dip-buy candidates because a
   1:5 split mechanically drops the price 80% but isn't a real dip.
8. **WHY IT MIGHT FAIL** — cleanest invalidation path.

Hard rules: no fabricated numbers, no different entry/stop/target
suggestions (those are NoAI-owned), 400-600 words for 60-second
readability.

---

## 7. Compare up to 4 stocks

Side-by-side metric matrix. Cap of 4 by design (matches typical
1280-wide laptop screen and avoids accidental fan-out on typo).

**20 metric rows** (top to bottom): **today's overall rank** (the
bot's single cross-family ranking — lower wins; this is THE answer
to "which one does the bot pick first?"), status, setup, composite
score (per-setup scale), sector, current price, 52w high (rolling),
% below 52w high (setup-aware winner), above SMA-200, above SMA-50,
above EMA-20, weekly trend up, RSI(14), RS vs NIFTY (60d), volume
ratio, suggested entry, stop loss, target, suggested qty, R:R.

Each row carries a **direction** (`high` / `low` / `true` / `rsi` /
`dip_aware` / `neutral`) and the renderer picks a winner per row:
- `high`: max wins (composite score, RS, R:R, volume ratio).
- `low`: min wins (today's overall rank — #1 beats #5; suggested
  entry / target if you want a cheap entry).
- `dip_aware`: setup-family-aware (% Below 52w high). For dip-buy
  candidates higher % wins (deeper dip = better entry); for
  momentum setups lower % wins (closer to high = stronger). Mixed
  comparisons get NO winner highlight — neither interpretation is
  honest.
- `true`: True wins (above SMA-200, weekly trend up, etc.).
- `rsi`: closest to 50 wins (oversold AND overbought are bad).
- `neutral`: no winner highlight (display-only).

> **Why two scoring rows?** "Today's overall rank" is the unified
> bot ranking — directly comparable across setup families.
> "Composite score" is the within-family signal strength (0-10ish
> for technical setups; 18-30+ % for dip-buy). A composite of 25.9%
> on a 52W_DIP candidate is NOT "better" than 7.5 on a BREAKOUT —
> they're different scales. The rank row resolves the comparison.

The "winner overall" headline (`HDFC wins 7 of 20 metrics`) sums up
which name dominates the matrix.

**Sector aliases** for `--compare-sector`:
| Input | Resolves to |
|---|---|
| `BANK` / `BANKS` / `BANKING` | `BANKING` |
| `IT` / `TECH` | `IT` |
| `PHARMA` / `HEALTH` / `HEALTHCARE` | `PHARMA` |
| `AUTO` / `AUTOMOBILE` | `AUTO` |
| `ENERGY` / `OIL` / `OILGAS` | `ENERGY` |
| `METALS` / `METAL` | `METALS` |
| `FMCG` / `CONSUMER` | `FMCG` |
| `INFRA` / `POWER` | `INFRA` |
| `TELECOM` / `TELECOMM` | `TELECOM` |
| `CAPGOODS` / `CAPITAL` / `ENGINEERING` / `DEFENCE` | `CAPGOODS` |
| `FIN` / `NBFC` / `FINANCE` | `FINANCE` |

Top-4 picked from `SECTOR_MAP` insertion order which puts higher-cap
names first.

---

## 8. The Add+ flow (manual entry confirmation)

After you place the order on Zerodha Kite manually:

1. Click the **Add+** button on the recommendation row (or in the
   search-box result card).
2. Browser prompts for **Executed quantity** (positive integer).
3. Browser prompts for **Executed price** (positive Rupees).
4. Browser prompts for **Stop-loss price** (optional — blank uses the
   bot's suggested stop).
5. POST to `/api/swing/actions/<id>/confirm` with hard server-side
   validation: rejects NaN / 0 / negative / qty > suggested_qty.
6. Server inserts a row into `swing_positions` (status=OPEN) with
   YOUR actual fill numbers + the bot's suggested target.
7. Page reloads; the position shows up in the Open Swing Book.

The same flow works from the CLI:
```powershell
python main.py --mode swing --confirm 42 --qty 10 --price 974.60 --stop 877.14
```

### Mark Exit Done
Same flow for closing a position:
1. Click **Mark Exit Done** on the open-book row.
2. Browser prompts for exit qty + exit price.
3. Server validates qty ≤ `pos.managed_qty`, price > 0.
4. Computes gross P&L, delivery charges (STT + exchange + GST +
   SEBI + stamp duty), net P&L.
5. Marks the position CLOSED (or reduces `managed_qty` for partials).

### Hardening
- **Re-entrancy guard** — double-clicking Add+ won't create
  duplicate positions; the second click finds the existing
  `linked_action_id` and returns the same position.
- **Skip is idempotent** — re-skipping an already-skipped action
  returns success rather than an error.
- **Input validation** — both client-side (`alert()` before any
  request) and server-side (HTTP 400 with descriptive error). NaN
  / 0 / negative qty/price never reach the SQL writer.

---

## 9. HTTP API reference

Same data, machine-readable. All endpoints return JSON.

| Endpoint | Purpose |
|---|---|
| `GET /api/swing/data` | Latest run summary + entry actions + positions + P&L. |
| `GET /api/swing/run_status` | In-flight scan status (for the dashboard polling banner). |
| `POST /api/swing/run?mode=NOAI&capital=X` | Kick off a fresh scan. Single-flight. |
| `POST /api/swing/actions/<id>/confirm` | Add+ payload `{qty, price, stop}`. |
| `POST /api/swing/actions/<id>/skip` | Skip payload `{reason}`. |
| `POST /api/swing/positions/<id>/exit` | Mark Exit Done payload `{qty, price}`. |
| `POST /api/swing/analyse_one?symbol=X&ai=1&capital=N` | Search-box single-stock analyse. |
| `POST /api/swing/ai_analyse/<SYMBOL>` | Per-stock detail-page AI button (~Rs.3). |
| `GET /api/swing/compare?symbols=A,B,C,D` | Compare matrix. |
| `GET /api/swing/compare?sector=BANKING` | Sector-auto-populated compare. |
| `GET /api/swing/sectors` | List of known `SECTOR_MAP` keys. |
| `GET /api/live_prices?symbols=A,B,C` | Live Zerodha quotes (rate-limited 5s). |
| `GET /api/errors?since=<id>` | New external-API errors since the JS poller's last seen id. |

---

## 10. Persistence + reports + read-only inspection

### `data/swing.db` schema
- `swing_runs` — one row per scan (or per pre-AI snapshot, marked
  `is_snapshot=1`). Filtered out of `latest_run()` so the dashboard
  picks the post-AI row when AI mode ran.
- `swing_candidates` — every scored stock per run (ACCEPTED +
  REJECTED). Keyed by `(run_id, symbol)`. Carries the snapshot JSON
  + the live `ai_overlay_json` column.
- `swing_actions` — entry recommendations (PENDING/CONFIRMED/SKIPPED)
  + exit actions written by Mark-Exit-Done.
- `swing_positions` — confirmed positions (OPEN/CLOSED) with
  realised P&L.
- `swing_events` — append-only audit trail (ENTRY / EXIT / STOP_MOVE).

### Reports
Every scan writes:
```
reports/swing/<YYYY>/<MM>/swing_report_<DD>.txt    # human-readable
reports/swing/<YYYY>/<MM>/swing_data_<DD>.json     # machine
```

Plain text, grep-able for any external tooling.

### Read-only inspection (no separate flag needed)
```powershell
# Last full-scan run summary (skips SEARCH_BOX + snapshot rows)
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from modes.swing.persistence import latest_run; r = latest_run(); print(r)"

# All pending actions across runs
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from modes.swing.persistence import pending_actions; [print(a.action_id, a.symbol, a.action_type, a.suggested_price) for a in pending_actions()]"

# All open positions with realised P&L
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from modes.swing.persistence import open_positions, realised_pnl_summary; [print(p.position_id, p.symbol, p.managed_qty, p.entry_price) for p in open_positions()]; print(realised_pnl_summary())"

# AI overlay for a specific symbol (with timestamp)
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); from modes.swing.persistence import latest_ai_overlay_for_symbol; r = latest_ai_overlay_for_symbol('SBIN'); print(r[1] if r else None); print(r[0][:500] if r else 'no overlay')"
```

---

## 11. Tuning knobs (Config)

In `config.py`:

| Knob | Default | What it does |
|---|---|---|
| `SCAN_UNIVERSE` | `"NIFTY100"` | `NIFTY50` / `NIFTY100` / `NIFTY150` / `NIFTY200` / `CUSTOM`. Override per-run with `--nifty 200`. |
| `SWING_DIP_PCT` | `18.0` | % below 52w high required to qualify as a `52W_DIP` candidate. |
| `SWING_DIP_TARGET_PCT` | `12.0` | Take-profit % above buy for dip-buy positions. |
| `SWING_DIP_BUY_AMOUNT` | `10_000.0` | Fixed Rs. ticket size per dip-buy. |
| `SWING_DIP_LOOKBACK_DAYS` | `252` | Rolling-window length for "52w high". 252 ≈ 1 year of trading bars. |
| `SWING_AI_MAX_CANDIDATES` | `15` | Cap on AI overlay calls per scan. |
| `CLAUDE_COST_PER_CALL` | `3.0` | Estimated Rs. per Claude call (used for the cost preview banner only — actual cost is metered by Anthropic). |
| `EARNINGS_BLACKOUT_ENABLED` | `True` | Master switch for the earnings filter. |
| `EARNINGS_BLACKOUT_SYMBOLS_2026` | `{}` | Operator-maintained `{"YYYY-MM-DD": ["SYM1", "SYM2"]}` map. Symbols with results in T+0..2 are skipped. |
| `RISK_PER_TRADE_PCT` | `0.5` | % of swing capital risked per technical-setup trade. |
| `MAX_POSITION_PCT` | `40` | Max single-position value cap. |

The four `SWING_DIP_*` knobs are user-tunable; everything else is set
based on the calibration evidence in
[market-research/results/xirr_matrix.csv](https://github.com/yash040599/market-research/blob/main/results/xirr_matrix.csv).
The [copilot/swing-review.md](../copilot/swing-review.md) skill has a
mandatory backtest sanity-check step that fires whenever any swing
knob moves outside the X∈{16-20}, Y∈{10-13} sweet spot.

---

## 12. Common questions / failure modes

**Q: I searched a stock and got `REJECTED — No qualifying setup`. Did the search fail?**
A: No, the search ran fine. The stock just doesn't qualify TODAY against any of the 5 setup detectors AND is short of the 52w-dip threshold. The result card now shows the full context (current price, 52w high, % below, RSI, RS vs NIFTY) plus the per-setup score breakdown so you can see HOW close it was. Click "Open detail page" to drill in for the full health check.

**Q: AI overlay disappeared after I re-searched the stock without AI.**
A: Fixed in S48. The carry-forward now runs at every read site so the cached overlay (≤7 days old) re-attaches automatically. If it's still empty after a refresh, check `latest_ai_overlay_for_symbol(SYMBOL)` from the CLI — if it returns None the cached overlay has expired or never existed.

**Q: Live prices not updating?**
A: The dashboard polls `/api/live_prices` every 5s. If the prices are frozen, check the top-right toast — auth failures invalidate the token and surface as a "Re-login" toast. Click the auth pill and re-login.

**Q: Swing capital shows Rs.1,00,000 instead of my Zerodha balance?**
A: A precise inline reason appears next to the input box (yellow text): "Zerodha not logged in", "token expired", or "funds fetch failed (\<exception\>)". Re-login from the auth pill if needed.

**Q: Why is BHEL a 52w-high stock but the dip column shows 0%?**
A: That's correct — BHEL is at fresh 52w high so the dip is 0% by definition. The detail page renders this as "at fresh 52w high (Rs.X)" via the existing `if dip_pct <= 0` branch.

**Q: Two different runs of the same scan show different priority ranks.**
A: The scan re-runs every time and ranks afresh. Rankings shift based on intraday price moves, fresh news (the manager rebanks after both technical + dip scanners), and the S28 sector-rotation bonus (which depends on today's full RS distribution, not yesterday's). This is expected behaviour.

**Q: I ran AI mode and it consumed credits without producing a report.**
A: Fixed in S21 + S43. Pre-AI snapshot is written BEFORE the AI loop starts, so a Ctrl+C still leaves a usable scan. The AI loop is also wrapped in try/except KeyboardInterrupt. The AI cost is bounded by `Config.SWING_AI_MAX_CANDIDATES` (default 15 = ~Rs.45 max).

**Q: How do I review yesterday's swing recommendations against today's actual moves?**
A: Use the [copilot/swing-review.md](../copilot/swing-review.md) skill — it's a 10-step daily review recipe (sister of `trade-daily-review.md`) that walks the live ledger, checks AI cost reconciliation, surfaces stale Awaiting-Data items, and validates against the backtest sweet-spot when any knob has moved.

---

## See also

- [SWING_STRATEGY.md](SWING_STRATEGY.md) — design spec (setup definitions, scoring math, risk model)
- [SWING_ROADMAP.md](SWING_ROADMAP.md) — chronological change log (S1-S48 to date)
- [../copilot/swing-review.md](../copilot/swing-review.md) — daily-review skill
- [market-research](https://github.com/yash040599/market-research) — standalone X/Y backtest for the dip-buy strategy
