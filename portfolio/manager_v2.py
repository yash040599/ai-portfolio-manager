# ================================================================
# portfolio/manager_v2.py
# ================================================================
# V2 intraday trading bot using candle-pattern-based pre-filtering.
#
# Extends PortfolioManager (V1) — inherits ALL infrastructure:
#   - Zerodha login, budget management, signal handling
#   - Position entry, exit, square-off, report generation
#   - Wait timers, holiday detection, status display
#   - ATR-based SL/targets, trailing stop, circuit breaker
#   - Crash recovery, order API failure protection
#
# V2 ADDITIONS (everything else is inherited from V1):
#   1. StockScannerV2 replaces V1 scanner (math pre-filter → Claude)
#   2. Claude review includes fresh 5-min candle pattern context
#   3. Dynamic poll interval — halved when near SL or target (0.5%)
#   4. Periodic candle re-scan every V2_CANDLE_RESCAN_MINUTES (free,
#      no Claude cost) — logs strong signals on open positions
#
# DESIGN: inheritance means V2 automatically gets any future V1
# improvements (new risk rules, better prompts, etc.) for free.
#
# Default mode is NoAI (pure technical signals, zero Claude calls).
# Use --ai for Claude-assisted selection and reviews.
#
# Run with: python main.py --mode trade          (NoAI, default)
#           python main.py --mode trade --ai     (Claude-assisted)
# ================================================================

import time
import datetime

from config                           import Config, now_ist
from core.logger                      import Logger
from core.zerodha_client              import ZerodhaClient
from core.claude_client               import ClaudeClient
from services.stock_scanner_v2        import StockScannerV2
from services                         import candle_patterns
from services.order_engine            import OrderEngine
from services.report_writer           import ReportWriter
from services.performance_tracker     import PerformanceTracker
from portfolio.manager                import PortfolioManager


class PortfolioManagerV2(PortfolioManager):
    """
    V2 intraday trading bot with candle-pattern and technical-indicator
    pre-filtering. Extends V1 — same lifecycle, smarter stock selection.
    """

    def __init__(self, config: type[Config]):
        # Initialize parent (sets up all shared infra)
        super().__init__(config)

        # Replace the V1 scanner with V2 (candle-aware)
        self.scanner = StockScannerV2(
            config,
            self.claude,
            self.zerodha,
            Logger("StockScannerV2"),
        )

        # V2-specific state
        self._fast_poll = False        # True when near SL/target
        self._last_candle_scan = 0.0   # timestamp of last candle re-scan
        self._noai = False             # True when running in --noai mode
        self._last_nifty_check = 0.0   # timestamp of last NIFTY regime re-check
        self._last_opportunity_scan = 0.0  # timestamp of last periodic opportunity scan

    # ================================================================
    # OVERRIDE: BANNER
    # ================================================================

    def _print_banner(self):
        """Shows V2 configuration."""
        plan = self.cfg.claude()
        zrd  = self.cfg.zerodha()
        print(f"\n{'='*58}")
        if self._noai:
            print("  AI PORTFOLIO MANAGER — V2 NO-AI MODE")
        else:
            print("  AI PORTFOLIO MANAGER — V2 CANDLE STRATEGY")
        print(f"{'='*58}")
        if not self._noai:
            print(f"  Claude plan    : {self.cfg.CLAUDE_PLAN.upper()}")
            print(f"  → {plan['note']}")
            print()
        print(f"  Zerodha plan   : {self.cfg.ZERODHA_PLAN.upper()}")
        print(f"  → {zrd['note']}")
        print()
        if not self._noai:
            print(f"  Claude model   : {plan['model']}")
        else:
            print(f"  Claude model   : NONE (pure technical signals)")
        print(f"  Price source   : {zrd['price_source'].upper()}")
        print()
        print(f"  \033[96m★ V2 Strategy\033[0m : Candle patterns + Technical indicators")
        print(f"    Pre-filter  : EMA(9/21), RSI(14), VWAP, SuperTrend(7,2.0)")
        print(f"    Patterns    : Hammer, Engulfing, Morning/Evening Star, etc.")
        print(f"    Dynamic poll: faster near SL/target zones")
        if self._noai:
            print(f"    AI calls    : ZERO — fully rule-based trading")
        print(f"{'='*58}\n")

    # ================================================================
    # TEST MODE — run V2 candle pipeline end-to-end, no Claude
    # ================================================================

    def run_test(self, noai: bool = False):
        """
        Runs the full V2 strategy analysis pipeline and shows what the
        bot does at each step — without any Claude API calls or orders.

        Purpose: educate the user on how the strategy works, verify the
        pipeline is functioning correctly, and show what trades the bot
        would consider today.

        When noai=True, also shows the NoAI auto-selection logic
        (which trades would be auto-entered without Claude).

        Steps:
          1. Validate config + log into Zerodha
          2. Fetch live quotes for the stock universe
          3. Run V2 pre-filter (candle patterns + technical indicators)
          4. Print detailed results for every analysed stock
          5. Show sector diversification filter results
          6. Show what would be sent to Claude (V2) or auto-selected (NoAI)
        """
        from services.stock_scanner_v2 import MAX_CANDIDATES, SECTOR_MAP, MAX_PER_SECTOR

        mode_label = "NoAI" if noai else "V2"
        print(f"\n{'='*58}")
        print(f"  {mode_label} STRATEGY ANALYSIS — TEST MODE")
        print(f"  Shows how the bot analyses and selects trades.")
        print(f"  No Claude calls. No trades. No cost.")
        print(f"{'='*58}\n")

        print("  STRATEGY PIPELINE:")
        print("  ┌─────────────────────────────────────────────────┐")
        print("  │ 1. Fetch candle data (15-min + daily)           │")
        print("  │ 2. Run 14 candlestick pattern detectors         │")
        print("  │ 3. Compute 14 technical indicators              │")
        print("  │    EMA, RSI, VWAP, SuperTrend, MACD, ORB, Gap,  │")
        print("  │    Daily EMA, Prev-Day S&R, Hourly EMA, BB, ADX │")
        print("  │ 4. Score each stock (~-25 to +25)               │")
        print("  │ 5. Filter by min score threshold                │")
        print("  │ 6. Apply Nifty trend hard filter                │")
        print("  │ 7. Apply sector diversification (max 2/sector)  │")
        if noai:
            print("  │ 8. Auto-select top candidates → BUY/SELL       │")
            print("  │    (score > 0 = BUY, score < 0 = SELL)         │")
        else:
            print("  │ 8. Send top 15 to Claude for final selection    │")
        print("  └─────────────────────────────────────────────────┘")
        print()

        # ── Step 1: Validate config ───────────────────────────────
        missing = self.cfg.validate(require_claude=False)
        if missing:
            for key in missing:
                self.log.error(f"Missing in .env file: {key}")
            return

        # ── Step 2: Login to Zerodha ──────────────────────────────
        self.log.section("ZERODHA LOGIN")
        try:
            self.zerodha.login()
        except Exception as e:
            self.log.error(f"Zerodha login failed: {e}")
            return

        self.zerodha.print_account_snapshot()

        # ── Step 3: Fetch live quotes ─────────────────────────────
        universe = self.scanner.get_universe()
        self.log.section(f"STEP 1: SCANNING {len(universe)} STOCKS ({self.cfg.SCAN_UNIVERSE})")

        stocks = [{"symbol": s, "exchange": "NSE"} for s in universe]
        quotes = self.zerodha.get_quotes_safe(stocks)
        if quotes is None:
            self.log.error("Could not fetch market data. Aborting.")
            return

        # ── Step 4: Run V2 pre-filter (math only) ────────────────
        self.log.section("STEP 2: TECHNICAL ANALYSIS (free — no API cost)")
        self.log.info(f"Candle interval : {self.cfg.V2_CANDLE_INTERVAL}")
        self.log.info(f"Min score       : {self.cfg.V2_MIN_SCORE}")
        self.log.info(f"Max candidates  : {MAX_CANDIDATES}")
        self.log.info(f"Sector limit    : {MAX_PER_SECTOR} per sector")
        self.log.info("")

        scored = []
        skipped = 0
        for i, symbol in enumerate(universe):
            if (i + 1) % 20 == 0 or (i + 1) == len(universe):
                self.log.info(f"  Analysed {i + 1}/{len(universe)} stocks...")

            result = self.scanner._analyse_stock(symbol)
            if result:
                scored.append(result)
            else:
                skipped += 1

        self.log.info(f"\nAnalysed {len(scored)} stocks | Skipped {skipped} (insufficient candle data)")

        # ── Step 5: Show ALL results (not just filtered) ──────────
        scored.sort(key=lambda x: abs(x["combined_score"]), reverse=True)

        self.log.section("STEP 3: SCORING RESULTS (all stocks ranked)")
        print(f"{'Symbol':<14} {'Score':>6}  {'Signal':<12} {'RSI':>5}  "
              f"{'EMA(9/21)':<16} {'SuperTrend':<12} {'VWAP':>10}  "
              f"{'Price':>10}  {'Patterns'}")
        print("-" * 120)

        for r in scored:
            tech = r["technical"]
            ps = r["pattern_summary"]
            rsi_val = tech["rsi"]["rsi"]
            ema_sig = tech["ema_cross"]["signal"]
            st_trend = tech["supertrend"]["trend"]
            patterns = ", ".join(ps["patterns"][:4]) if ps["patterns"] else "-"

            score = r["combined_score"]
            passed = abs(score) >= self.cfg.V2_MIN_SCORE

            marker = "*" if passed else " "
            print(
                f"{marker} {r['symbol']:<12} {score:>+6.1f}  {tech['signal']:<12} "
                f"{rsi_val:>5.0f}  {ema_sig:<16} {st_trend:<12} "
                f"Rs.{r['vwap']:>9.2f}  Rs.{r['current_price']:>9.2f}  "
                f"[{patterns}]"
            )

        # ── Step 6: Show filtered candidates ──────────────────────
        filtered = [s for s in scored if abs(s["combined_score"]) >= self.cfg.V2_MIN_SCORE]

        # Apply sector diversification (same as real scan)
        sector_diversified = []
        sector_counts: dict[str, int] = {}
        dropped_sector = 0
        for s in filtered:
            sector = SECTOR_MAP.get(s["symbol"], "OTHER")
            count = sector_counts.get(sector, 0)
            if count >= MAX_PER_SECTOR:
                dropped_sector += 1
                continue
            sector_counts[sector] = count + 1
            sector_diversified.append(s)

        top = sector_diversified[:MAX_CANDIDATES]

        self.log.section(f"STEP 4: FILTERING — {len(filtered)} passed score, {dropped_sector} dropped by sector limit, top {len(top)} shown")

        if dropped_sector:
            self.log.info(f"  Sector caps applied (max {MAX_PER_SECTOR}/sector):")
            for sec, cnt in sorted(sector_counts.items()):
                if cnt >= MAX_PER_SECTOR:
                    self.log.info(f"    {sec}: {cnt} (capped)")

        if not top:
            self.log.warning("No stocks passed the pre-filter threshold today.")
            return

        for r in top:
            tech = r["technical"]
            ps = r["pattern_summary"]
            ema = tech["ema_cross"]
            st = tech["supertrend"]
            rsi_data = tech["rsi"]
            sr = tech.get("prev_day_sr", {})
            sector = SECTOR_MAP.get(r["symbol"], "OTHER")

            print(f"\n  {'-'*50}")
            print(f"  {r['symbol']}  --  Score: {r['combined_score']:+.1f}  ({tech['signal']})  [Sector: {sector}]")
            print(f"  {'-'*50}")
            print(f"  Price    : Rs.{r['current_price']:.2f}")
            print(f"  VWAP     : Rs.{r['vwap']:.2f}  ({'above' if r['current_price'] > r['vwap'] else 'below'} VWAP)")
            print(f"  RSI(14)  : {rsi_data['rsi']:.1f}  ({rsi_data['signal']}, strength: {rsi_data['strength']})")
            print(f"  EMA(9/21): {ema['signal']}  (spread: {ema['spread_pct']:+.2f}%)")
            print(f"  SuperTrnd: {st['trend']}  (signal: {st['signal']})")
            if r.get("rvol", 0) > 0:
                print(f"  RVol     : {r['rvol']:.1f}x  ({'HIGH' if r['rvol'] > 2 else 'LOW' if r['rvol'] < 0.3 else 'normal'})")
            if sr.get("signal", "NONE") != "NONE":
                pivot_str = f"  Pivot: Rs.{sr['pivot']:.2f}" if sr.get("pivot") else ""
                print(f"  PrevDayS&R: {sr['signal']}  (score: {sr['score']:+.1f}){pivot_str}")

            macd_data = tech.get("macd", {})
            if macd_data.get("signal", "NONE") != "NONE":
                print(f"  MACD     : {macd_data['signal']} / {macd_data['momentum']}  (hist: {macd_data['histogram']:.4f})")

            orb_data = tech.get("orb", {})
            if orb_data.get("signal", "NONE") not in ("NONE", "INSIDE_RANGE"):
                print(f"  ORB      : {orb_data['signal']}  (range: Rs.{orb_data['or_low']:.2f} - Rs.{orb_data['or_high']:.2f})")
            elif orb_data.get("or_high", 0) > 0:
                print(f"  ORB      : INSIDE_RANGE  (Rs.{orb_data['or_low']:.2f} - Rs.{orb_data['or_high']:.2f})")

            gap_data = tech.get("gap", {})
            if gap_data.get("signal", "NO_GAP") != "NO_GAP":
                print(f"  Gap      : {gap_data['signal']}  ({gap_data['gap_pct']:+.1f}%)")

            if ps["patterns"]:
                print(f"  Patterns : {', '.join(ps['patterns'])}")
                print(f"  Pat.score: {ps['score']:+.1f}  ({ps['net_signal']})")
                if ps["strongest"]:
                    s = ps["strongest"]
                    print(f"  Strongest: {s['pattern']}  ({s['signal']}, strength: {s['strength']})")
            else:
                print(f"  Patterns : none detected")

            print(f"  Candles  : {r['candle_count']} (15-min candles used)")

        # ── Step 6b: Nifty hard filter simulation ─────────────────
        would_drop_bear = sum(1 for s in filtered if s["combined_score"] > 0 and abs(s["combined_score"]) < 3)
        would_drop_bull = sum(1 for s in filtered if s["combined_score"] < 0 and abs(s["combined_score"]) < 3)
        if would_drop_bear or would_drop_bull:
            print(f"\n  Nifty hard filter impact (if applied):")
            if would_drop_bear:
                print(f"    BEARISH market → would drop {would_drop_bear} weak BUY signals (score < 3)")
            if would_drop_bull:
                print(f"    BULLISH market → would drop {would_drop_bull} weak SELL signals (score < 3)")

        # ── Step 7: Show next step (Claude vs Auto-select) ────────
        if noai:
            self.log.section("STEP 5: NoAI AUTO-SELECTION (what would be traded)")
            max_trades = self.cfg.MAX_POSITIONS
            auto_picks = top[:max_trades]
            print(f"  Max positions: {max_trades}")
            print(f"  Auto-selected: {len(auto_picks)} trades\n")

            for i, r in enumerate(auto_picks, 1):
                cs = r["combined_score"]
                if cs > 0:
                    side = "BUY"
                elif cs < 0:
                    side = "SELL"
                else:
                    # Roadmap #169: zero score has no direction — skip from preview.
                    continue
                sl_pct = self.cfg.DEFAULT_STOP_LOSS_PCT / 100
                tgt_pct = self.cfg.DEFAULT_TARGET_PCT / 100
                price = r["current_price"]
                if side == "BUY":
                    sl = round(price * (1 - sl_pct), 2)
                    target = round(price * (1 + tgt_pct), 2)
                else:
                    sl = round(price * (1 + sl_pct), 2)
                    target = round(price * (1 - tgt_pct), 2)

                tech = r["technical"]
                ps = r["pattern_summary"]
                rationale_parts = [f"Score {r['combined_score']:+.1f}"]
                rationale_parts.append(f"RSI {tech['rsi']['rsi']:.0f}")
                rationale_parts.append(f"EMA {tech['ema_cross']['signal']}")
                rationale_parts.append(f"ST {tech['supertrend']['trend']}")
                macd_data = tech.get("macd", {})
                if macd_data.get("signal", "NONE") != "NONE":
                    rationale_parts.append(f"MACD {macd_data['signal']}")
                if ps["patterns"]:
                    rationale_parts.append(f"Patterns: {', '.join(ps['patterns'][:2])}")

                print(f"  Trade {i}: {side} {r['symbol']} @ Rs.{price:.2f}")
                print(f"           SL: Rs.{sl:.2f}  Target: Rs.{target:.2f}")
                print(f"           {' | '.join(rationale_parts)}")
                print()

            print(f"  ℹ️  In live NoAI mode, ATR-based SL/target would override")
            print(f"      the defaults shown above with volatility-adapted levels.")
        else:
            snapshot = self.scanner._build_enriched_snapshot(top, quotes)
            if snapshot:
                self.log.section("STEP 5: ENRICHED SNAPSHOT (would be sent to Claude)")
                print(snapshot)
                print(f"\n  ℹ️  In live V2 mode, Claude would analyse this data and")
                print(f"      pick the best {self.cfg.MAX_POSITIONS} trades with specific entry/SL/target.")

        # ── Summary ───────────────────────────────────────────────
        self.log.section("TEST SUMMARY")
        bulls = sum(1 for s in top if s["combined_score"] > 0)
        bears = sum(1 for s in top if s["combined_score"] < 0)
        print(f"  Mode           : {mode_label} Strategy Test")
        print(f"  Universe       : {len(universe)} stocks ({self.cfg.SCAN_UNIVERSE})")
        print(f"  Analysed       : {len(scored)}")
        print(f"  Skipped        : {skipped} (not enough candle data)")
        print(f"  Passed filter  : {len(filtered)} (|score| >= {self.cfg.V2_MIN_SCORE})")
        print(f"  Sector dropped : {dropped_sector}")
        print(f"  Top candidates : {len(top)} (max {MAX_CANDIDATES})")
        print(f"  Bullish setups : {bulls}")
        print(f"  Bearish setups : {bears}")
        print(f"\n  Claude calls   : 0  (test mode — no API cost)")
        print(f"  Orders placed  : 0  (test mode — no trades)")
        if noai:
            print(f"\n  To run NoAI live : python main.py --mode trade --noai")
            print(f"  To dry-run first : python main.py --mode trade --noai --dryrun")
        else:
            print(f"\n  To run V2 live   : python main.py --mode trade")
            print(f"  To dry-run first : python main.py --mode trade --dryrun")
        print()

    # ================================================================
    # OVERRIDE: PRE-MARKET SCAN (routes to noai when flag is set)
    # ================================================================

    def _run_pre_market_scan(self, session_context: str = ""):
        """Routes scan to noai or Claude path based on mode."""
        if self._noai:
            self._run_noai_scan(session_context)
        else:
            super()._run_pre_market_scan(session_context)

    def _run_claude_review(self, quotes: dict):
        """Skip Claude review in noai mode (called by V1 run() on resume)."""
        if self._noai:
            self.log.info("NoAI mode: skipping Claude review (rule-based only)")
            return
        super()._run_claude_review(quotes)

    # ================================================================
    # NO-AI MODE — fully automated, zero Claude calls
    # ================================================================

    def run_noai(self):
        """
        Runs the full trading day using only technical signals —
        no Claude API calls at all. Trade selection, monitoring,
        and re-scans are all rule-based.

        Uses the same lifecycle as run() (login, wait for market,
        observation period, monitoring, square-off, report) but
        replaces every Claude call with math-based logic.
        """
        self._noai = True
        self.run()

    def _run_noai_scan(self, session_context: str = ""):
        """
        Pre-market scan without Claude — uses scan_noai() which
        selects trades purely from technical scores.
        """
        now = now_ist()
        market_open = now.replace(
            hour=self.cfg.MARKET_OPEN_HOUR,
            minute=self.cfg.MARKET_OPEN_MINUTE,
            second=0, microsecond=0,
        )

        if now < market_open:
            self.log.section("PRE-MARKET SCAN (NoAI)")
        else:
            self.log.section("MARKET SCAN (NoAI — joined late)")

        self.log.info(f"Universe: {self.cfg.SCAN_UNIVERSE}")
        self.log.info(f"Budget: Rs.{self._budget:,.2f}")
        self.log.info(f"Mode: {'DRY RUN' if self.cfg.DRY_RUN else 'LIVE TRADING'}")
        self.log.info("Selection: pure technical signals (no Claude calls)")

        universe = self.scanner.get_universe()
        self.log.info(f"Scanning {len(universe)} stocks...")

        stocks = [{"symbol": s, "exchange": "NSE"} for s in universe]
        quotes = self.zerodha.get_quotes_safe(stocks)
        if quotes is None:
            self.log.error("Could not fetch market data. Aborting scan.")
            self._scan_failed = True
            return

        nifty_context = self._build_nifty_context()

        # Determine available slots
        open_count = len(self.engine.open_positions())
        max_trades = self.cfg.MAX_POSITIONS - open_count

        # Count open direction for direction-aware filtering
        open_buys = sum(1 for p in self.engine.open_positions() if p["side"] == "BUY")
        open_sells = sum(1 for p in self.engine.open_positions() if p["side"] == "SELL")

        self._trade_plans = self.scanner.scan_noai(
            quotes, nifty_context,
            max_trades=max_trades,
            session_context=session_context,
            day_pnl=self.engine.day_pnl(),
            open_buys=open_buys,
            open_sells=open_sells,
        )

        if self._trade_plans:
            # Show primary picks with full details, fallbacks just listed
            max_t = self.cfg.MAX_POSITIONS
            primary_plans = self._trade_plans[:max_t]
            fallback_plans = self._trade_plans[max_t:]
            self.log.section("TRADE PLAN (NoAI)")
            for i, t in enumerate(primary_plans, 1):
                self.log.info(
                    f"  Pick {i}: {t['side']} {t['qty']}x {t['symbol']} "
                    f"@ Rs.{t['entry_price']:.2f} | "
                    f"SL: Rs.{t['stop_loss']:.2f} | "
                    f"Target: Rs.{t['target_price']:.2f}"
                )
                self.log.info(f"         {t.get('rationale', '')}")
            if fallback_plans:
                fb_syms = ", ".join(t['symbol'] for t in fallback_plans[:6])
                extra = f" +{len(fallback_plans)-6}" if len(fallback_plans) > 6 else ""
                self.log.info(
                    f"  Fallback ({len(fallback_plans)}): {fb_syms}{extra}"
                )

    # ================================================================
    # OVERRIDE: MONITOR LOOP (V2 — with dynamic polling + candle re-scan)
    # ================================================================

    def _run_monitor_loop(self):
        """
        V2 monitor loop with:
        - Dynamic poll interval (faster when near SL/target)
        - Periodic candle re-scan (every 15 min) to detect new setups
        - Enhanced Claude review with position candle context
        """
        if self._noai:
            self.log.section("V2 MONITORING — NoAI (rule-based + candle re-scan)")
        else:
            self.log.section("V2 MONITORING — Candle-aware price tracking")

        base_poll = self.cfg.PRICE_POLL_SECONDS
        fast_poll = max(5, base_poll // 2)  # halve interval, min 5s
        review_interval = self.cfg.POSITION_REVIEW_MINUTES * 60
        candle_rescan_interval = self.cfg.V2_CANDLE_RESCAN_MINUTES * 60

        if self._noai:
            self.log.info(
                f"Base poll: {base_poll}s | Fast poll: {fast_poll}s | "
                f"Claude review: DISABLED (noai) | "
                f"Candle rescan: every {self.cfg.V2_CANDLE_RESCAN_MINUTES}min"
            )
        else:
            self.log.info(
                f"Base poll: {base_poll}s | Fast poll: {fast_poll}s | "
                f"Claude review: every {self.cfg.POSITION_REVIEW_MINUTES}min | "
                f"Candle rescan: every {self.cfg.V2_CANDLE_RESCAN_MINUTES}min"
            )

        last_review_time = time.time()
        self._last_candle_scan = time.time()
        self._last_nifty_check = time.time()
        self._last_opportunity_scan = time.time()
        self._last_external_sync = time.time()

        # Prime VIX-spike state once at loop start so opportunity-scan
        # entries inside the loop honour the pause from the very first
        # iteration (#211 — closes the window between observe-and-enter
        # and the first NIFTY recheck).
        try:
            self.engine.set_vix_spike(self._check_vix_spike())
        except Exception as e:
            self.log.debug(f"VIX-spike prime at loop start failed: {e}")

        while not self._shutdown_requested:
            now = now_ist()

            # ── Square-off check ──────────────────────────────────
            if self._is_square_off_time(now):
                self._clear_status_line()
                self.log.info("Square-off time reached")
                break

            # ── All positions closed? ─────────────────────────
            if not self.engine.open_positions():
                if self.engine.is_order_api_broken():
                    self._clear_status_line()
                    self.log.error("All positions closed, order API broken — stopping")
                    break

                sq_off = now.replace(
                    hour=self.cfg.SQUARE_OFF_HOUR,
                    minute=self.cfg.SQUARE_OFF_MINUTE,
                    second=0, microsecond=0,
                )
                mins_remaining = (sq_off - now).total_seconds() / 60

                if mins_remaining >= self.cfg.MIN_MINUTES_FOR_ENTRY:
                    if self.engine.is_rr_giveup():
                        self._clear_status_line()
                        self.log.warning(
                            "All positions closed — R:R giveup active, "
                            "no viable setups today. Stopping."
                        )
                        break
                    if self.engine.is_sl_paused():
                        self._clear_status_line()
                        self.log.info(
                            f"All positions closed but SL pause active — "
                            f"waiting for pause to expire before re-scanning"
                        )
                        time.sleep(base_poll)
                        continue
                    if self._check_vix_spike():
                        self.engine.set_vix_spike(True)
                        self._clear_status_line()
                        self.log.info(
                            f"All positions closed but VIX (Volatility Index) spike active — "
                            f"waiting for VIX to settle before re-scanning"
                        )
                        time.sleep(base_poll)
                        continue
                    self.engine.set_vix_spike(False)
                    self._clear_status_line()
                    self.log.info(
                        f"All positions closed with {mins_remaining:.0f} min left — "
                        f"V2 re-scanning with candle analysis..."
                    )
                    # Sync with Zerodha before re-scan (detect manual trades, refresh budget)
                    self.engine.sync_external_positions()
                    self.engine.refresh_budget()
                    closed_trades = self.engine.closed_positions()
                    traded_symbols = list({p["symbol"] for p in closed_trades})
                    day_pnl = self.engine.day_pnl()
                    session_ctx = (
                        f"\nSESSION CONTEXT (V2 mid-day re-scan):\n"
                        f"  Market condition: {self._market_condition}.\n"
                        f"  Day P&L so far: Rs.{day_pnl:,.2f} from {len(closed_trades)} closed trades.\n"
                        f"  Already traded today: {', '.join(traded_symbols) if traded_symbols else 'none'}.\n"
                        f"  DO NOT pick any stock already traded today unless opposite direction.\n"
                        f"  {'If P&L is negative, only pick high-conviction candle setups with tight stops.' if day_pnl < 0 else f'All capital is free — deploy at least {self.cfg.MIN_BUDGET_UTILISATION_PCT:.0f}% on high-conviction setups.'}\n"
                    )
                    self._trade_plans = []
                    self._run_pre_market_scan(session_context=session_ctx)
                    if self._trade_plans:
                        self._enter_positions()
                        last_review_time = time.time()
                        self._last_candle_scan = time.time()
                        self._last_nifty_check = time.time()
                        self._last_opportunity_scan = time.time()
                        continue
                    else:
                        self.log.info("V2 re-scan: no new trades — done for the day")
                        break
                else:
                    self._clear_status_line()
                    self.log.info(
                        f"All positions closed — {mins_remaining:.0f} min left, "
                        f"not enough for new trades "
                        f"(change MIN_MINUTES_FOR_ENTRY in config.py to allow later entries)"
                    )
                    break

            # ── Periodic external position sync (detect manual trades) ─
            # Run FIRST before quote fetch so manual positions are included
            # in quotes, SL/target checks, and slot counting.
            if not self.cfg.DRY_RUN and time.time() - self._last_external_sync >= candle_rescan_interval:
                new_ext = self.engine.sync_external_positions()
                if new_ext > 0:
                    self._clear_status_line()
                    self.log.info(f"Detected {new_ext} manual trade(s) — now managed by bot")
                self._last_external_sync = time.time()

            # ── Fetch live quotes ─────────────────────────────────
            open_symbols = [
                {"symbol": p["symbol"], "exchange": p["exchange"]}
                for p in self.engine.open_positions()
            ]
            try:
                quotes = self.zerodha.get_quotes(open_symbols)
            except Exception as e:
                self.log.warning(f"Quote fetch failed: {e} — retrying")
                time.sleep(base_poll)
                continue

            # Cache quotes on the engine so MTM-aware CB / soft-stop /
            # peak-drawdown (Roadmap #166) can include open-position
            # MTM without every call site threading quotes through.
            self.engine.set_latest_quotes(quotes)

            # ── SL/target check (free, rule-based) ────────────────
            closed = self.engine.check_stops_and_targets(quotes)
            if closed > 0:
                self._clear_status_line()
                self.log.info(f"{closed} position(s) auto-closed")
                # ── Partial re-scan: fill empty slots with new trades ─
                # Sync with Zerodha to detect manual trades before counting slots
                self.engine.sync_external_positions()
                self.engine.refresh_budget()
                open_count = len(self.engine.open_positions())
                rescan_cooldown = 120  # min 2 min between partial re-scans
                time_since_rescan = time.time() - self._last_partial_rescan
                if (
                    open_count > 0
                    and open_count < self.cfg.MAX_POSITIONS
                    and not self.engine.is_order_api_broken()
                    and not self._circuit_broken
                    and not self.engine.is_sl_paused()
                    and not self.engine.is_rr_giveup()
                    and time_since_rescan >= rescan_cooldown
                ):
                    sq_now = now_ist()
                    sq_off = sq_now.replace(
                        hour=self.cfg.SQUARE_OFF_HOUR,
                        minute=self.cfg.SQUARE_OFF_MINUTE,
                        second=0, microsecond=0,
                    )
                    mins_left = (sq_off - sq_now).total_seconds() / 60
                    slots = self.cfg.MAX_POSITIONS - open_count
                    if mins_left >= self.cfg.MIN_MINUTES_FOR_ENTRY:
                        self.log.info(
                            f"{slots} slot(s) free, {mins_left:.0f} min left — "
                            f"V2 scanning for replacement trades..."
                        )
                        closed_trades = self.engine.closed_positions()
                        traded_symbols = list({p["symbol"] for p in closed_trades})
                        day_pnl = self.engine.day_pnl()
                        session_ctx = (
                            f"\nSESSION CONTEXT (V2 partial re-scan — {slots} slot(s) available):\n"
                            f"  Market condition: {self._market_condition}.\n"
                            f"  Day P&L so far: Rs.{day_pnl:,.2f} from {len(closed_trades)} closed trades.\n"
                            f"  Already traded today: {', '.join(traded_symbols) if traded_symbols else 'none'}.\n"
                            f"  Currently holding: {', '.join(p['symbol'] for p in self.engine.open_positions())}.\n"
                            f"  You have {slots} slot(s) available. Pick at most {slots} new trade(s).\n"
                            f"  DO NOT pick any stock already traded or currently held.\n"
                            f"  If P&L is negative, only pick high-conviction candle setups with tight stops.\n"
                        )
                        self._trade_plans = []
                        self._run_pre_market_scan(session_context=session_ctx)
                        if self._trade_plans:
                            self._enter_positions()
                            last_review_time = time.time()
                            self._last_candle_scan = time.time()
                        else:
                            self.log.info("V2 partial re-scan: no replacement trades found")
                        self._last_partial_rescan = time.time()
                        self._last_opportunity_scan = time.time()  # sync timers

            # ── Order API broken check ────────────────────────────
            if self.engine.is_order_api_broken():
                self._clear_status_line()
                self.log.error("Order API broken — shutting down")
                if self.engine.open_positions():
                    self._square_off()
                break

            # ── Circuit breaker ───────────────────────────────────
            if self.engine.check_circuit_breaker():
                self._circuit_broken = True
                self._square_off()
                cooldown = self.cfg.CIRCUIT_BREAKER_COOLDOWN_MINUTES
                if cooldown > 0 and not self.engine.circuit_breaker_trips_exhausted():
                    sq_off = now.replace(
                        hour=self.cfg.SQUARE_OFF_HOUR,
                        minute=self.cfg.SQUARE_OFF_MINUTE,
                        second=0, microsecond=0,
                    )
                    mins_left = (sq_off - now).total_seconds() / 60
                    if mins_left > cooldown + self.cfg.MIN_MINUTES_FOR_ENTRY:
                        self.log.info(
                            f"Circuit breaker cooldown: waiting {cooldown} min "
                            f"before resuming with reduced budget..."
                        )
                        # Polling sleep — check shutdown every 10s
                        for _ in range(cooldown * 6):
                            if self._shutdown_requested:
                                break
                            time.sleep(10)
                        if self._shutdown_requested:
                            break
                        self._circuit_broken = False
                        self.engine.reset_circuit_breaker_baseline()
                        self.engine.refresh_budget()
                        self.log.info(
                            f"Circuit breaker cooldown complete — resuming with "
                            f"loss-adjusted budget Rs.{self.engine.loss_adjusted_budget():,.2f}"
                        )
                        self._last_opportunity_scan = time.time()
                        continue
                self.log.warning(
                    "Circuit breaker: stopping for the day "
                    "(change MAX_CIRCUIT_BREAKER_TRIPS or CIRCUIT_BREAKER_COOLDOWN_MINUTES in config.py to adjust)"
                )
                break

            # ── Late-day loser exit ───────────────────────────────
            if self.engine.open_positions():
                loser_closed = self.engine.check_loser_exit(quotes)
                if loser_closed > 0:
                    self._clear_status_line()
                    self.log.info(f"{loser_closed} losing position(s) auto-exited (late-day loser cleanup)")

            # ── Dynamic poll rate ─────────────────────────────────
            if self.engine.open_positions():
                near_trigger = self.scanner.should_increase_poll_rate(
                    self.engine.open_positions(), quotes,
                )
                if near_trigger and not self._fast_poll:
                    self._fast_poll = True
                    self._clear_status_line()
                    self.log.info("⚡ Position near SL/target — increasing poll rate")
                elif not near_trigger and self._fast_poll:
                    self._fast_poll = False

            # ── Periodic candle re-scan (free, no Claude cost) ──
            candle_elapsed = time.time() - self._last_candle_scan
            if candle_elapsed >= candle_rescan_interval and self.engine.open_positions():
                self._clear_status_line()
                self.log.info("V2 candle re-scan: refreshing technical data for open positions")
                for pos in self.engine.open_positions():
                    fresh = self.scanner._analyse_stock(pos["symbol"], pos.get("exchange", "NSE"))
                    if not fresh:
                        continue

                    score = fresh["combined_score"]
                    ps = fresh["pattern_summary"]
                    patterns = ", ".join(ps["patterns"][:3]) if ps["patterns"] else "none"

                    if abs(score) >= 5:
                        self.log.info(
                            f"  {pos['symbol']}: score {score:+.1f}  "
                            f"tech: {fresh['technical']['signal']}  "
                            f"patterns: [{patterns}]"
                        )

                    # Hard exit on strong signal reversal (#174). Runs
                    # BEFORE _auto_protect_on_contrary_signal — if the
                    # reversal is severe enough to trigger an exit, no
                    # point tightening SL on a position we're closing.
                    if self._signal_reversal_exit(pos, fresh, quotes):
                        continue

                    # Same-direction thesis decay (#188). Catches
                    # trades where the entry signal hasn't flipped to
                    # the opposite side (which #174 already covers)
                    # but has decayed to a small fraction of its entry
                    # strength. Runs AFTER reversal so a decisive flip
                    # still goes through the reversal log line.
                    if self._signal_decay_exit(pos, fresh, quotes):
                        continue

                    # Auto-tighten SL on strong contrary signal
                    self._auto_protect_on_contrary_signal(pos, fresh, quotes)

                # Sector-cascade protect (#149) — runs once per
                # candle re-scan, AFTER per-position checks so it
                # only tightens what's still open. Defensive only;
                # never opens a new trade.
                try:
                    self._sector_cascade_protect(quotes)
                except (AttributeError, KeyError) as e:
                    # Expected defensive-programming failures
                    # (missing scanner attr, missing position field):
                    # silent debug.
                    self.log.debug(f"Sector cascade protect skipped: {e}")
                except Exception as e:
                    # Unexpected failure — surface at WARNING so we
                    # notice in the daily log review instead of
                    # losing a defensive exit silently.
                    self.log.warning(f"Sector cascade protect failed: {e}")

                self._last_candle_scan = time.time()
                next_candle = now_ist() + datetime.timedelta(seconds=candle_rescan_interval)
                self.log.info(
                    f"Next candle re-scan: {next_candle.strftime('%H:%M:%S')} "
                    f"({self.cfg.V2_CANDLE_RESCAN_MINUTES}min)"
                )

            # ── Periodic NIFTY regime re-check (free) ─────────────
            nifty_recheck_interval = self.cfg.NIFTY_RECHECK_MINUTES * 60
            if nifty_recheck_interval > 0:
                nifty_elapsed = time.time() - self._last_nifty_check
                if nifty_elapsed >= nifty_recheck_interval:
                    old_condition = self._market_condition
                    self._build_nifty_context()  # updates self._market_condition + VIX
                    if self._market_condition and self._market_condition != old_condition:
                        self._clear_status_line()
                        self.log.info(
                            f"📊 Market regime shifted: {old_condition} → {self._market_condition}"
                        )
                        # Tighten SLs on positions contradicted by new regime
                        self._regime_shift_protect(quotes)
                    # VIX spike detection (Roadmap #211)
                    spike = self._check_vix_spike()
                    self.engine.set_vix_spike(spike)
                    if spike:
                        self._clear_status_line()
                        self.log.warning(
                            f"⚠ VIX (Volatility Index) SPIKE detected: {self._india_vix:.1f} "
                            f"(opened at {self._india_vix_open:.1f}) — "
                            f"pausing new entries"
                        )
                    self._last_nifty_check = time.time()

            # ── Periodic opportunity scan (fill free slots) ───────
            opp_rescan_interval = self.cfg.OPPORTUNITY_RESCAN_MINUTES * 60
            if opp_rescan_interval > 0:
                open_count = len(self.engine.open_positions())
                opp_elapsed = time.time() - self._last_opportunity_scan
                if (
                    open_count > 0
                    and open_count < self.cfg.MAX_POSITIONS
                    and opp_elapsed >= opp_rescan_interval
                    and not self.engine.is_order_api_broken()
                    and not self._circuit_broken
                    and not self.engine.is_sl_paused()
                    and not self._check_vix_spike()
                    and not self.engine.is_rr_giveup()
                ):
                    sq_now = now_ist()
                    sq_off = sq_now.replace(
                        hour=self.cfg.SQUARE_OFF_HOUR,
                        minute=self.cfg.SQUARE_OFF_MINUTE,
                        second=0, microsecond=0,
                    )
                    mins_left = (sq_off - sq_now).total_seconds() / 60
                    slots = self.cfg.MAX_POSITIONS - open_count
                    if mins_left >= self.cfg.MIN_MINUTES_FOR_ENTRY:
                        self._clear_status_line()
                        self.log.info(
                            f"⏰ Periodic opportunity scan: {slots} slot(s) free, "
                            f"{mins_left:.0f} min left — scanning for new trades..."
                        )
                        self.engine.sync_external_positions()
                        self.engine.refresh_budget()
                        closed_trades = self.engine.closed_positions()
                        traded_symbols = list({p["symbol"] for p in closed_trades})
                        day_pnl = self.engine.day_pnl()
                        session_ctx = (
                            f"\nSESSION CONTEXT (periodic opportunity scan — {slots} slot(s) available):\n"
                            f"  Market condition: {self._market_condition}.\n"
                            f"  Day P&L so far: Rs.{day_pnl:,.2f} from {len(closed_trades)} closed trades.\n"
                            f"  Already traded today: {', '.join(traded_symbols) if traded_symbols else 'none'}.\n"
                            f"  Currently holding: {', '.join(p['symbol'] for p in self.engine.open_positions())}.\n"
                            f"  You have {slots} slot(s) available. Pick at most {slots} new trade(s).\n"
                            f"  DO NOT pick any stock already traded or currently held.\n"
                            f"  {'If day P&L is negative, only pick high-conviction setups with tight stops.' if day_pnl < 0 else 'Actively look for good setups to fill slots — size positions to use capital effectively.'}\n"
                        )
                        self._trade_plans = []
                        self._run_pre_market_scan(session_context=session_ctx)
                        if self._trade_plans:
                            self._enter_positions()
                            last_review_time = time.time()
                            self._last_candle_scan = time.time()
                        else:
                            self.log.info("Periodic opportunity scan: no new trades found")
                    self._last_opportunity_scan = time.time()
                    next_opp = now_ist() + datetime.timedelta(seconds=opp_rescan_interval)
                    self.log.info(
                        f"Next opportunity scan: {next_opp.strftime('%H:%M:%S')} "
                        f"({self.cfg.OPPORTUNITY_RESCAN_MINUTES}min)"
                    )

            # ── Claude review (with candle context) ───────────────
            elapsed = time.time() - last_review_time
            if elapsed >= review_interval and self.engine.open_positions():
                if self._noai:
                    # NoAI: check for stagnant positions instead of Claude review
                    stagnant_closed = self.engine.check_stagnant_positions(quotes)
                    if stagnant_closed > 0:
                        self._clear_status_line()
                        self.log.info(f"{stagnant_closed} stagnant position(s) exited (NoAI)")
                else:
                    self._clear_status_line()
                    self._run_claude_review_v2(quotes)
                last_review_time = time.time()

            # ── Print status ──────────────────────────────────────
            self._print_status(quotes)

            # ── Sleep (dynamic) ───────────────────────────────────
            poll = fast_poll if self._fast_poll else base_poll
            time.sleep(poll)

    # ================================================================
    # V2 CLAUDE REVIEW (with candle context)
    # ================================================================

    def _run_claude_review_v2(self, quotes: dict):
        """
        Enhanced Claude review that includes real-time candle pattern
        analysis for each open position.
        """
        self.log.section("CLAUDE V2 REVIEW — with candle analysis")
        self.engine.claude_calls += 1

        nifty_context = self._build_nifty_context()

        actions = self.scanner.review_positions_v2(
            open_positions   = self.engine.open_positions(),
            quotes           = quotes,
            day_pnl          = self.engine.day_pnl(),
            budget_remaining = self.engine.budget_remaining(),
            nifty_context    = nifty_context,
            closed_positions = self.engine.closed_positions(),
        )

        if actions:
            self.engine.apply_review_actions(actions, quotes)

        # Re-fetch quotes for table display
        all_open = self.engine.open_positions()
        if all_open:
            open_symbols = [
                {"symbol": p["symbol"], "exchange": p["exchange"]}
                for p in all_open
            ]
            try:
                quotes = self.zerodha.get_quotes(open_symbols)
            except Exception:
                pass

        self.engine.print_position_status(quotes)

    # ================================================================
    # AUTO-PROTECT: TIGHTEN SL ON CONTRARY CANDLE SIGNALS
    # ================================================================

    def _compute_protective_sl(
        self,
        side: str,
        entry: float,
        current_price: float,
        old_sl: float,
    ):
        """
        Compute a tightened protective SL for candle-protect and
        regime-shift, with a safety cushion.

        Rules:
          - In profit → lock 50% of current profit (candidate).
          - Break-even / loss → candidate is the entry price.
          - Candidate is then clamped so the new SL sits at least
            CANDLE_PROTECT_MIN_CUSHION_PCT away from the live price
            (for BUY: SL ≤ price − cushion; for SELL: SL ≥ price + cushion).
          - Returns None if the result would not be tighter than old_sl
            (never loosen the stop).

        BUG FIX (2026-04-17): Previously when a contrary signal arrived
        on a break-even / loss position, the new SL was set to exact
        entry. If current price was already against entry, the tightened
        SL fired immediately on the next tick — triggering the
        INDIGO SL-M double-book bug. The cushion keeps the SL at arm's
        length from the live price so noise doesn't hit it.
        """
        cushion_pct = max(
            0.5 * self.cfg.DEFAULT_STOP_LOSS_PCT,
            self.cfg.CANDLE_PROTECT_MIN_CUSHION_PCT,
        ) / 100
        cushion = round(current_price * cushion_pct, 2)

        if side == "BUY":
            profit = current_price - entry
            if profit > 0:
                candidate = round(entry + profit * 0.5, 2)
            else:
                candidate = round(entry, 2)
            # SL must stay at least `cushion` below live price
            max_allowed = round(current_price - cushion, 2)
            new_sl = min(candidate, max_allowed)
            # Only apply if strictly tighter (higher) than existing SL
            if new_sl <= old_sl:
                return None
            return new_sl
        else:  # SELL
            profit = entry - current_price
            if profit > 0:
                candidate = round(entry - profit * 0.5, 2)
            else:
                candidate = round(entry, 2)
            # SL must stay at least `cushion` above live price
            min_allowed = round(current_price + cushion, 2)
            new_sl = max(candidate, min_allowed)
            # Only apply if strictly tighter (lower) than existing SL
            if new_sl >= old_sl:
                return None
            return new_sl

    # ================================================================
    # SIGNAL-REVERSAL EXIT (#174)
    # ================================================================

    # Reversal pattern sets — single source of truth in
    # services/candle_patterns.
    _BEARISH_REVERSAL_PATTERNS = candle_patterns.BEARISH_REVERSAL_PATTERNS
    _BULLISH_REVERSAL_PATTERNS = candle_patterns.BULLISH_REVERSAL_PATTERNS

    def _signal_reversal_exit(
        self,
        pos: dict,
        analysis: dict,
        quotes: dict,
    ) -> bool:
        """
        Hard-exit a held position when the free candle re-scan detects
        a STRONG opposite signal. Returns True if the position was exited
        (callers should `continue` past further per-position protection
        steps in that case).

        Why this exists: the static SL-M only catches price-side moves.
        A momentum reversal — large opposite combined_score + a confirming
        bearish/bullish reversal candle — is signal-side information that
        typically arrives BEFORE the price stop. Acting on it cuts losses
        earlier than the fixed SL would.

        Triggers (BUY position, mirrored for SELL):
          - combined_score <= -SIGNAL_REVERSAL_SCORE
          - AND (if SIGNAL_REVERSAL_REQUIRE_PATTERN) at least one
            confirming bearish reversal pattern is present.

        Skipped when:
          - Feature disabled via SIGNAL_REVERSAL_EXIT_ENABLED.
          - Position is in profit ≥ 1× initial risk — a profitable
            position is the trailing-stop's job; don't dump a winner
            on a single 15-min candle reversal.
          - Live price is missing/zero (no way to compute exit P&L).
        """
        if not getattr(self.cfg, "SIGNAL_REVERSAL_EXIT_ENABLED", False):
            return False

        score  = analysis.get("combined_score", 0)
        side   = pos["side"]
        symbol = pos["symbol"]

        threshold = self.cfg.SIGNAL_REVERSAL_SCORE
        is_buy_reversal  = (side == "BUY"  and score <= -threshold)
        is_sell_reversal = (side == "SELL" and score >=  threshold)
        if not (is_buy_reversal or is_sell_reversal):
            return False

        # Confirming-pattern requirement: avoid acting on a single noisy
        # score swing without a candlestick reversal to back it up.
        patterns = analysis.get("pattern_summary", {}).get("patterns", []) or []
        pattern_set = {p.upper() for p in patterns}
        if self.cfg.SIGNAL_REVERSAL_REQUIRE_PATTERN:
            confirming = (
                self._BEARISH_REVERSAL_PATTERNS if side == "BUY"
                else self._BULLISH_REVERSAL_PATTERNS
            )
            if not (pattern_set & confirming):
                return False

        # Live price needed to evaluate profit-skip + log accurate P&L.
        key = f"{pos.get('exchange', 'NSE')}:{symbol}"
        current_price = quotes.get(key, {}).get("last_price", 0)
        if current_price <= 0:
            return False

        entry = pos["entry_price"]
        qty   = pos["qty"]
        if side == "BUY":
            pnl = (current_price - entry) * qty
        else:
            pnl = (entry - current_price) * qty

        # Profitable winners are the trailing-stop's responsibility —
        # one bad candle shouldn't dump a position that's already paid
        # ≥1R. Use the position's recorded initial risk when available.
        # Defensive fallback: if initial_sl/stop_loss were lost (e.g. a
        # restart that didn't fully rehydrate the position), still skip
        # any position that's currently in profit. Without this guard
        # `initial_risk` collapses to 0 and ANY profitable position with
        # a reversal pattern would be exited — defeating the purpose
        # of the 1R skip.
        initial_sl = pos.get("initial_sl") or pos.get("stop_loss") or entry
        initial_risk = abs(entry - initial_sl) * qty
        if initial_risk > 0:
            if pnl >= initial_risk:
                return False
        elif pnl > 0:
            # No risk reference available — be conservative and skip
            # any in-profit position rather than risk dumping a winner.
            return False

        confirming_match = sorted(pattern_set & (
            self._BEARISH_REVERSAL_PATTERNS if side == "BUY"
            else self._BULLISH_REVERSAL_PATTERNS
        ))
        pattern_tag = ", ".join(confirming_match) if confirming_match else "none"
        self.log.warning(
            f"⚠ SIGNAL REVERSAL {symbol} {side}: score {score:+.1f} "
            f"(threshold ±{threshold:.1f}), patterns [{pattern_tag}] — "
            f"exiting at Rs.{current_price:.2f}, P&L Rs.{pnl:+,.2f}"
        )
        self.engine.exit_position(pos, current_price, "SIGNAL_REVERSAL")
        return True

    def _signal_decay_exit(
        self,
        pos: dict,
        analysis: dict,
        quotes: dict,
    ) -> bool:
        """
        Same-direction thesis decay exit (#188). Companion to
        `_signal_reversal_exit`: catches positions whose entry signal
        hasn't flipped to the opposite side (which #174 covers) but
        has decayed to a small fraction of its entry strength, AND
        the trade isn't yet in profit.

        Without this gate, weak-but-not-flipped trades sit in the
        slow-positive corridor for hours and only exit on LOSER_EXIT
        (BHARTIARTL 2026-04-21: 5h 3min held flat for entry score
        +10.1 → +3.6 at the 10:31 re-scan; nothing else fired).

        Triggers (BUY position, mirrored for SELL):
          - feature enabled
          - abs(entry_score) >= SIGNAL_DECAY_MIN_ENTRY_SCORE — only
            act on trades that started with real conviction
          - fresh_score has the SAME sign as entry_score (true flips
            are #174's job, NOT this one)
          - abs(fresh_score) < abs(entry_score) * SIGNAL_DECAY_FRACTION
          - elapsed >= SIGNAL_DECAY_MIN_HOLD_MINUTES (don't act on
            one bad re-score immediately after entry)
          - pnl < initial_risk * SIGNAL_DECAY_WINNER_SKIP_R_MULTIPLE
            (book-and-go below 1R: sub-1R profit has no trailing-stop
             cushion — the stop is at-or-below entry so any pullback
             gives it all back. Winners ≥1R keep running on the
             trailing stop. Fallback on missing initial_sl: use the
             conservative `pnl > 0` skip so we never dump a legacy /
             restart-rehydrated profitable position.)

        Returns True if the position was exited (caller should
        `continue` past further per-position protection in that
        case).
        """
        if not getattr(self.cfg, "SIGNAL_DECAY_EXIT_ENABLED", False):
            return False

        entry_score = pos.get("_entry_score")
        if entry_score is None:
            return False  # legacy / restart-rehydrated position

        try:
            entry_score = float(entry_score)
        except (TypeError, ValueError):
            return False

        if abs(entry_score) < self.cfg.SIGNAL_DECAY_MIN_ENTRY_SCORE:
            return False

        fresh_score = analysis.get("combined_score")
        if fresh_score is None:
            return False
        try:
            fresh_score = float(fresh_score)
        except (TypeError, ValueError):
            return False

        # Same-sign requirement (opposite flips are #174's domain).
        if (entry_score > 0) != (fresh_score > 0):
            return False
        # Edge case: fresh_score == 0 also disqualifies same-sign,
        # caught above (0 > 0 is False on both sides).

        # Decay magnitude check.
        if abs(fresh_score) >= abs(entry_score) * self.cfg.SIGNAL_DECAY_FRACTION:
            return False

        # Hold-time guard: avoid acting on the very first re-scan
        # after entry. parse entry_time HH:MM:SS against today.
        side   = pos["side"]
        symbol = pos["symbol"]
        entry_time_str = pos.get("entry_time", "")
        if not entry_time_str:
            return False
        now = now_ist()
        try:
            entry_dt = datetime.datetime.strptime(
                f"{now.strftime('%Y-%m-%d')} {entry_time_str}",
                "%Y-%m-%d %H:%M:%S",
            )
        except (ValueError, TypeError):
            return False
        # `now_ist()` returns naive IST; `entry_dt` is naive too.
        elapsed_min = (now - entry_dt).total_seconds() / 60
        if elapsed_min < self.cfg.SIGNAL_DECAY_MIN_HOLD_MINUTES:
            return False

        # Live price needed to compute pnl + log accurate exit.
        key = f"{pos.get('exchange', 'NSE')}:{symbol}"
        current_price = quotes.get(key, {}).get("last_price", 0)
        if current_price <= 0:
            return False

        entry_price = pos["entry_price"]
        qty         = pos["qty"]
        pnl = (current_price - entry_price) * qty if side == "BUY" \
            else (entry_price - current_price) * qty

        # Winner skip. Book-and-go below 1R (configurable via
        # SIGNAL_DECAY_WINNER_SKIP_R_MULTIPLE): sub-1R profit has no
        # trailing-stop cushion, so any pullback on a decayed signal
        # just bleeds it back to flat. Above 1R the trailing stop is
        # already above entry and can protect the winner — let it run.
        # Fallback when initial_sl is missing (legacy / rehydrated
        # positions): conservative `pnl > 0` skip so we never dump a
        # profitable trade without a known risk reference.
        initial_sl   = pos.get("initial_sl") or pos.get("stop_loss")
        initial_risk = (
            abs(entry_price - initial_sl) * qty
            if initial_sl and initial_sl > 0
            else 0.0
        )
        if initial_risk > 0:
            winner_floor = initial_risk * self.cfg.SIGNAL_DECAY_WINNER_SKIP_R_MULTIPLE
            if pnl >= winner_floor and winner_floor > 0:
                return False
        else:
            # Fallback: no usable initial_sl → conservative `pnl > 0` skip
            # so we never dump a profitable legacy / rehydrated position
            # without a known risk reference.
            if pnl > 0:
                return False

        decay_pct = (1 - abs(fresh_score) / abs(entry_score)) * 100
        r_multiple_str = (
            f"{pnl / initial_risk:+.2f}R" if initial_risk > 0 else "n/a"
        )
        self.log.warning(
            f"⚠ SIGNAL DECAY {symbol} {side}: entry score {entry_score:+.1f} "
            f"→ {fresh_score:+.1f} ({decay_pct:.0f}% decay) after {elapsed_min:.0f} min, "
            f"P&L Rs.{pnl:+,.2f} ({r_multiple_str}) — exiting at Rs.{current_price:.2f}"
        )
        # Stamp the fresh re-score so #195 average-down prevention can
        # block a same-magnitude re-entry of this symbol+side.
        pos["_exit_score"] = fresh_score
        self.engine.exit_position(pos, current_price, "SIGNAL_DECAY")
        return True

    def _auto_protect_on_contrary_signal(
        self,
        pos: dict,
        analysis: dict,
        quotes: dict,
    ):
        """
        When the free candle re-scan detects a strong signal AGAINST
        an open position, auto-tighten the stop-loss to breakeven or
        lock in partial profit. This acts immediately — no need to
        wait for the next Claude review.

        Triggers:
          - BUY position + score <= -4  (strong bearish signal)
          - SELL position + score >= +4 (strong bullish signal)

        Action:
          - If in profit: move SL to lock 50% of current profit
          - If at breakeven or loss: move SL to entry price (breakeven)
          - SL only moves in the protective direction (never loosened)
        """
        score  = analysis["combined_score"]
        side   = pos["side"]
        symbol = pos["symbol"]
        entry  = pos["entry_price"]
        old_sl = pos["stop_loss"]

        # Check if signal is contrary to position direction
        contrary = (side == "BUY" and score <= -4) or \
                   (side == "SELL" and score >= 4)

        if not contrary:
            return

        # Get current price from quotes
        key = f"{pos.get('exchange', 'NSE')}:{symbol}"
        q = quotes.get(key, {})
        current_price = q.get("last_price", 0)
        if current_price <= 0:
            return

        # Calculate new protective SL
        new_sl = self._compute_protective_sl(side, entry, current_price, old_sl)
        if new_sl is None:
            return

        # Apply the tighter SL
        pos["stop_loss"] = new_sl
        # BUG FIX (Apr 17 2026): Also move the exchange SL-M trigger so the
        # broker-side order matches the software SL. Without this, the
        # software SL fires first (because it is tighter), but the exchange
        # SL-M is still pending — the exit_position() STOP_LOSS path then
        # had a bug where it assumed the exchange order had already filled,
        # causing the position to stay live on Zerodha and get re-adopted
        # with double-booked P&L.
        self.engine._update_exchange_sl(pos, new_sl)
        patterns = ", ".join(analysis["pattern_summary"]["patterns"][:3])
        self.log.warning(
            f"⚠ CANDLE PROTECT {symbol}: contrary signal (score {score:+.1f}, "
            f"[{patterns}]) → SL tightened Rs.{old_sl:.2f} → Rs.{new_sl:.2f}"
        )

    # ================================================================
    # REGIME-SHIFT PROTECTION
    # ================================================================

    def _regime_shift_protect(self, quotes: dict):
        """
        When the Nifty regime flips against open positions, tighten SLs:
          - In profit → lock 50% of profit
          - Near breakeven/loss → move SL to entry (breakeven)
        Only affects positions whose side contradicts the new regime.
        """
        regime = (self._market_condition or "").upper()
        if "BEARISH" not in regime and "BULLISH" not in regime:
            return  # NEUTRAL — no action

        for pos in self.engine.open_positions():
            side = pos["side"]
            # Check if regime contradicts position
            if regime.startswith("BEARISH") and side == "BUY":
                pass  # bearish market hurts longs
            elif regime.startswith("BULLISH") and side == "SELL":
                pass  # bullish market hurts shorts
            else:
                continue  # position aligns with regime

            symbol = pos["symbol"]
            entry  = pos["entry_price"]
            old_sl = pos["stop_loss"]
            key    = f"{pos.get('exchange', 'NSE')}:{symbol}"
            q      = quotes.get(key, {})
            current_price = q.get("last_price", 0)
            if current_price <= 0:
                continue

            if side == "BUY":
                new_sl = self._compute_protective_sl(side, entry, current_price, old_sl)
                if new_sl is None:
                    continue
            else:
                new_sl = self._compute_protective_sl(side, entry, current_price, old_sl)
                if new_sl is None:
                    continue

            pos["stop_loss"] = new_sl
            # BUG FIX (Apr 17 2026): Keep exchange SL-M in sync with software SL.
            self.engine._update_exchange_sl(pos, new_sl)
            self.log.warning(
                f"⚠ REGIME PROTECT {symbol} {side}: market turned {regime} "
                f"→ SL tightened Rs.{old_sl:.2f} → Rs.{new_sl:.2f}"
            )

    # ================================================================
    # SECTOR-CASCADE PROTECTION (#149)
    # ================================================================

    def _sector_cascade_protect(self, quotes: dict):
        """Tighten SLs on open positions inside a fast-collapsing
        sector. Defensive only — never opens a new trade.

        Runs once per candle re-scan, just after per-position
        decay / contrary checks. Reads the scanner's two-tick
        per-sector AVERAGE score snapshot
        (`scanner.last_sector_momentum` vs
        `scanner._prev_sector_momentum`) and triggers when:

          1. ``prev - last >= SECTOR_CASCADE_DROP_THRESHOLD`` (BUYs)
             — sector flipped against us by ≥ 2.0 in one window.
          2. ``last <= -SECTOR_CASCADE_OPPOSITE_FLOOR`` (BUYs) —
             new average is solidly negative, not just "less
             positive".
          3. We have ≥ ``SECTOR_CASCADE_MIN_OPEN`` open positions
             in that sector.

        Mirror conditions for SELLs (sector turned bullish).

        Action: software SL → max(current, breakeven-with-buffer);
        exchange SL-M is replaced via `_update_exchange_sl()`.
        """
        if not getattr(self.cfg, "SECTOR_CASCADE_EXIT_ENABLED", True):
            return
        scanner = getattr(self, "scanner", None)
        if scanner is None:
            return
        last = getattr(scanner, "last_sector_momentum", {}) or {}
        prev = getattr(scanner, "_prev_sector_momentum", {}) or {}
        if not last or not prev:
            return  # need two snapshots to compute a delta

        from services.stock_scanner_v2 import SECTOR_MAP

        drop_thr = float(self.cfg.SECTOR_CASCADE_DROP_THRESHOLD)
        opp_floor = float(self.cfg.SECTOR_CASCADE_OPPOSITE_FLOOR)
        min_open = int(self.cfg.SECTOR_CASCADE_MIN_OPEN)

        # Group open positions by sector.
        by_sector: dict[str, list[dict]] = {}
        for p in self.engine.open_positions():
            sec = SECTOR_MAP.get(p["symbol"], "OTHER")
            by_sector.setdefault(sec, []).append(p)

        for sector, positions in by_sector.items():
            if len(positions) < min_open:
                continue
            last_avg = last.get(sector)
            prev_avg = prev.get(sector)
            if last_avg is None or prev_avg is None:
                continue
            delta = prev_avg - last_avg  # positive when sector dropped
            cascade_down = delta >= drop_thr and last_avg <= -opp_floor
            cascade_up   = (-delta) >= drop_thr and last_avg >= opp_floor
            if not (cascade_down or cascade_up):
                continue

            tightened = 0
            for pos in positions:
                side = pos["side"]
                # Only tighten positions on the side that the cascade
                # is now hostile to.
                if cascade_down and side != "BUY":
                    continue
                if cascade_up and side != "SELL":
                    continue
                symbol = pos["symbol"]
                entry  = pos["entry_price"]
                old_sl = pos["stop_loss"]
                key    = f"{pos.get('exchange', 'NSE')}:{symbol}"
                q      = quotes.get(key, {})
                current_price = q.get("last_price", 0)
                if current_price <= 0:
                    continue
                new_sl = self._compute_protective_sl(side, entry, current_price, old_sl)
                if new_sl is None:
                    continue
                # Update software SL FIRST (always succeeds, in-memory).
                # If broker-side replace fails, software SL is the
                # tighter fallback and _reconcile_orphan_sl_m() on the
                # next sync repairs the broker mismatch. Wrapping the
                # broker call ensures one position's failure cannot
                # leave subsequent positions un-tightened.
                pos["stop_loss"] = new_sl
                try:
                    self.engine._update_exchange_sl(pos, new_sl)
                except Exception as e:
                    self.log.warning(
                        f"SECTOR CASCADE {symbol}: broker SL replace failed "
                        f"({type(e).__name__}: {e}); software SL still tightened "
                        f"to Rs.{new_sl:.2f}, reconcile will retry next sync"
                    )
                tightened += 1
                self.log.warning(
                    f"⚠ SECTOR CASCADE {symbol} {side}: {sector} avg score "
                    f"{prev_avg:+.1f} → {last_avg:+.1f} (Δ {delta:+.1f}) "
                    f"→ SL tightened Rs.{old_sl:.2f} → Rs.{new_sl:.2f}"
                )
            if tightened:
                self.log.warning(
                    f"⚠ SECTOR CASCADE: {sector} cascade — "
                    f"tightened {tightened} position(s)"
                )
