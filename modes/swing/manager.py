# ================================================================
# modes/swing/manager.py
# ================================================================
# Orchestrator for `python main.py --mode swing`.
#
# Pipeline:
#   1. Validate config + login to Zerodha
#   2. Check market close gate
#   3. Load open swing positions
#   4. Review open positions (daily action)
#   5. Scan for new candidates
#   6. Optional AI overlay
#   7. Persist run + candidates + actions
#   8. Write report
#
# SWING_ROADMAP S1-S2.
# ================================================================

from __future__ import annotations

import datetime

from config import Config, now_ist
from core.claude_client import ClaudeClient
from core.logger import Logger
from core.zerodha_client import ZerodhaClient
from modes.swing.scanner import SwingScanner
from modes.swing.ath_scanner import DipBuyScanner
from modes.swing.review import review_position
from modes.swing.persistence import (
    init_db, save_run, open_positions, realised_pnl_summary,
    latest_run_for_date_and_mode, pending_actions,
)
from modes.swing.report import save_report
from modes.swing.types import (
    SwingRunResult, SwingAction, SwingCandidate, SwingPosition,
    ACTION_ENTRY, STATUS_PENDING, DIP_SETUP_TYPES,
)


# Market close time (IST). Swing scan must not run before this.
MARKET_CLOSE_HOUR   = 15
MARKET_CLOSE_MINUTE = 30

# Default swing capital — should come from config later
DEFAULT_SWING_CAPITAL = 100_000.0


class SwingManager:
    """Orchestrates the daily swing scan + position review cycle."""

    def __init__(self, config: type[Config], use_ai: bool = False):
        self.cfg = config
        self.use_ai = bool(use_ai)
        self.log = Logger("SwingManager")
        self.zerodha = ZerodhaClient(config, Logger("ZerodhaClient"))
        self.claude: ClaudeClient | None = (
            ClaudeClient(config, Logger("ClaudeClient")) if self.use_ai else None
        )

    # ── Main entry point ────────────────────────────────────────

    def run(self, trigger_source: str = "CLI",
             force: bool = False,
             swing_capital: float | None = None,
             ) -> SwingRunResult | None:
        """Run the full swing pipeline. Returns the result or None on failure.

        `force=True` skips the "already ran today" confirmation prompt.
        `swing_capital` overrides Config.SWING_CAPITAL if provided.
        Auto-triggers always skip silently; manual triggers ask.
        """
        self._print_banner()

        ts_start = now_ist()
        trade_date = ts_start.strftime("%Y-%m-%d")

        result = SwingRunResult(
            started_at=ts_start.isoformat(),
            mode="AI" if self.use_ai else "NOAI",
            universe=getattr(self.cfg, "SCAN_UNIVERSE", "NIFTY100"),
            run_for_date=trade_date,
            trigger_source=trigger_source,
        )

        # 1. Check for existing run (idempotency)
        mode = "AI" if self.use_ai else "NOAI"
        init_db()
        existing = latest_run_for_date_and_mode(trade_date, mode)
        if existing:
            # Auto-triggers: silently skip — no user present to ask.
            if trigger_source in ("DASHBOARD_AUTO", "PAGE_OPEN_AUTO"):
                self.log.info(f"Swing {mode} run already exists for {trade_date}. "
                              "Skipping automatic rerun.")
                return None

            # Manual triggers: ask unless force=True.
            if not force:
                finished = existing.get('finished_at', '')[:16]
                print(f"\n  A {mode} swing scan already ran today "
                      f"({trade_date}, finished {finished}).")
                if trigger_source == "CLI":
                    answer = input("  Rerun analysis? [y/N]: ").strip().lower()
                    if answer not in ("y", "yes"):
                        print("  Skipped.\n")
                        return None
                else:
                    # Dashboard button without force flag — should not happen
                    # if the UI sends force=true, but guard anyway.
                    self.log.info("Existing run found; dashboard should "
                                  "confirm via force flag.")
                    return None

        # 2. Market close gate — warn but don't block
        market_closed = self._is_market_closed(ts_start)
        if not market_closed:
            # Market still open: use yesterday's completed candle as the
            # latest data point. Today's candle is incomplete.
            yesterday = (ts_start - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            result.run_for_date = yesterday
            trade_date = yesterday
            result.notes = (result.notes or "") + (
                f"Ran before market close — using {yesterday} as the "
                "latest completed daily candle. Today's partial candle is excluded."
            )
            self.log.info(f"Market still open. Analysis will use data through "
                          f"{yesterday} (today's incomplete candle excluded).")
            print(f"\n  Note: Market is still open. Using completed data "
                  f"through {yesterday}.")
            print(f"  Today's partial candle is excluded.\n")

        # 3. Validate config + login
        missing = self.cfg.validate(require_claude=self.use_ai)
        if missing:
            self.log.section("CONFIGURATION ERROR")
            for k in missing:
                self.log.error(f"Missing in .env: {k}")
            return None

        self.log.section("ZERODHA LOGIN")
        self.zerodha.login()

        # 4. Load open positions
        positions = open_positions()
        self.log.info(f"Open swing positions: {len(positions)}")

        # 5. Fetch NIFTY candles (for RS calculation)
        scanner = SwingScanner(self.cfg, self.zerodha, self.log)
        nifty_candles = scanner._fetch_daily_candles("NIFTY 50", "NSE")

        # 6. Review open positions
        self.log.section("POSITION REVIEW")
        review_actions: list[SwingAction] = []
        for pos in positions:
            try:
                candles = scanner._fetch_daily_candles(pos.symbol, pos.exchange)
                action = review_position(pos, candles, nifty_candles)
                review_actions.append(action)
                pos.daily_action = action.action_type
                self.log.info(f"  {pos.symbol}: {action.action_type} — {action.notes}")
            except Exception as exc:
                self.log.warning(f"  {pos.symbol} review failed: {exc}")

        # 7. Scan for new candidates
        self.log.section("SWING SCAN")
        existing_pos_dicts = [
            {
                "symbol": p.symbol,
                "risk_rupees": (p.entry_price - p.stop_price) * p.managed_qty,
                "position_value": p.entry_price * p.managed_qty,
                "sector": "",  # TODO: look up from SECTOR_MAP
            }
            for p in positions
        ]

        _capital = swing_capital or getattr(
            self.cfg, "SWING_CAPITAL", DEFAULT_SWING_CAPITAL)

        # When market is still open, tell scanner to use only candles
        # up to yesterday so today's incomplete candle is excluded.
        candle_to_date = None if market_closed else (
            ts_start - datetime.timedelta(days=1)).date()

        candidates, entry_actions = scanner.scan(
            swing_capital=_capital,
            existing_positions=existing_pos_dicts,
            nifty_candles=nifty_candles,
            candle_to_date=candle_to_date,
        )

        # 7b. Dip-buy scan (52-week-high reference; legacy ATH name)
        self.log.section("DIP-BUY SCAN")
        open_symbols = {p.symbol for p in positions}
        # Also exclude symbols already accepted by the technical scan
        open_symbols |= {c.symbol for c in candidates if c.status == "ACCEPTED"}

        ath_scanner = DipBuyScanner(self.cfg, self.zerodha, self.log)
        ath_candidates, ath_actions = ath_scanner.scan(
            existing_symbols=open_symbols,
            candle_to_date=candle_to_date,
        )
        candidates.extend(ath_candidates)
        entry_actions.extend(ath_actions)

        # 7b1. Sector-rotation bonus (S28).
        # Compute today's per-sector mean relative-strength from the
        # full candidate pool (accepted + rejected — both carry RS
        # via compute_swing_indicators). Add +0.5 to each accepted
        # candidate sitting in the top-3 sectors so a strong setup
        # in a leading sector outranks an equally-strong setup in a
        # lagging sector. Manager-level so we have access to the
        # whole universe's RS map without per-symbol fundamentals.
        from modes.swing.signals import (
            compute_sector_rs, top_n_sectors_by_rs,
            SECTOR_LEADER_BONUS, SECTOR_LEADER_TOP_N,
        )
        sector_rs = compute_sector_rs(candidates)
        leader_sectors = top_n_sectors_by_rs(sector_rs)
        if leader_sectors:
            self.log.info(
                f"Sector leaders today (top {SECTOR_LEADER_TOP_N} by mean RS): "
                + ", ".join(
                    f"{s} ({sector_rs[s]:+.1f}%)" for s in leader_sectors
                )
            )
            for c in candidates:
                if (c.status == "ACCEPTED"
                        and (c.sector or "") in leader_sectors):
                    c.score = round(float(c.score) + SECTOR_LEADER_BONUS, 2)
                    c.reasons = list(c.reasons or []) + [
                        f"Sector leader: {c.sector} "
                        f"(mean RS {sector_rs[c.sector]:+.1f}%)"
                    ]

        # 7c. Unify priority_rank across both scanners.
        # Each scanner ranks within its own pool; for the dashboard's
        # single combined entry table we want one global ranking so
        # the AI overlay budget (capped) lands on the best signal
        # mix. Convention: technical first (rank 1..N), dip-buy after
        # (rank N+1..M). DIP_SETUP_TYPES covers both the legacy
        # "ATH_DIP" string and the current "52W_DIP" string.
        # Sort uses the *post-bonus* score from §7b1 so leading-sector
        # candidates float to the top of their setup family.
        accepted = [c for c in candidates if c.status == "ACCEPTED"]
        accepted.sort(key=lambda c: (
            0 if c.setup_type not in DIP_SETUP_TYPES else 1,
            -float(c.score),
        ))
        for unified_rank, c in enumerate(accepted, 1):
            c.priority_rank = unified_rank
        # Mirror the unified rank onto entry_actions so the
        # dashboard table can sort by it directly.
        rank_by_symbol = {c.symbol: c.priority_rank for c in accepted}
        for a in entry_actions:
            new_rank = rank_by_symbol.get(a.symbol)
            if new_rank is not None:
                a.priority_rank = new_rank

        # 8. AI overlay (only if explicitly requested)
        # Persist BEFORE the AI overlay so a Ctrl+C / network failure
        # mid-overlay still leaves a saved scan + report on disk
        # (origin: 2026-05-14 user reported "ran AI mode and it ran
        # no stop; stopped it; got no report"). Cost-cap is enforced
        # inside `overlay_ai_on_candidates` (Config.SWING_AI_MAX_CANDIDATES).
        if self.use_ai and self.claude:
            # Pre-AI snapshot so a partial AI run is recoverable.
            try:
                pre_ai_actions = review_actions + entry_actions
                pre_ai_result = SwingRunResult(
                    started_at=ts_start.isoformat(),
                    mode="AI",
                    universe=result.universe,
                    run_for_date=trade_date,
                    trigger_source=trigger_source,
                    candidates=list(candidates),
                    actions=list(pre_ai_actions),
                    positions=list(positions),
                    finished_at=now_ist().isoformat(),
                    notes=(result.notes or "") + " | pre-AI snapshot",
                )
                save_run(pre_ai_result, is_snapshot=True)
                self.log.info("Pre-AI snapshot persisted (cost-safe checkpoint)")
            except Exception as exc:
                self.log.warning(f"Pre-AI snapshot save failed: {exc}")

            self.log.section("AI OVERLAY")
            from modes.swing.ai_overlay import overlay_ai_on_candidates
            try:
                candidates = overlay_ai_on_candidates(
                    candidates, self.claude, self.log)
            except KeyboardInterrupt:
                self.log.warning(
                    "AI overlay interrupted by user — keeping pre-AI "
                    "snapshot and continuing to write the report.")
                # fall through; downstream save_run + save_report run

        # 8b. AI overlay carry-forward (sticky across runs).
        # If a candidate didn't get an AI overlay this run (either AI
        # mode wasn't requested, OR the candidate fell outside the
        # cost-cap of `overlay_ai_on_candidates`), copy a recent
        # cached overlay from any prior run into the freshly-built
        # candidate so the user doesn't lose qualitative context they
        # already paid Claude for. Freshness gate is 7 days by default.
        # Origin: 2026-05-14 user request — "as SBIN is also in
        # NIFTY 100 the AI response must also reflect in our tool
        # recommended list where SBIN must also be there".
        try:
            from modes.swing.persistence import latest_ai_overlay_for_symbol
            carried = 0
            for c in candidates:
                if c.status != "ACCEPTED":
                    continue
                if c.ai_overlay_json:
                    continue
                cached = latest_ai_overlay_for_symbol(c.symbol)
                if cached:
                    c.ai_overlay_json = cached[0]
                    carried += 1
            if carried:
                self.log.info(
                    f"AI overlay carry-forward: {carried} candidate(s) "
                    f"inherited a cached overlay (<= 7 days old)"
                )
        except Exception as exc:
            self.log.warning(f"AI overlay carry-forward failed: {exc}")

        # 9. Assemble result
        all_actions = review_actions + entry_actions
        result.candidates = candidates
        result.actions = all_actions
        result.positions = positions
        result.finished_at = now_ist().isoformat()

        # 10. Persist
        self.log.section("PERSIST")
        run_id = save_run(result)
        result.run_id = run_id
        self.log.info(f"Saved swing run #{run_id}")

        # 11. Report
        self.log.section("REPORT")
        txt_path, json_path = save_report(result)
        self.log.info(f"Report: {txt_path}")

        # 12. Print summary
        self._print_summary(result)

        return result

    # ── CLI sub-commands ────────────────────────────────────────

    def list_actions(self) -> list[SwingAction]:
        """List all pending actions."""
        init_db()
        actions = pending_actions()
        if not actions:
            print("\n  No pending swing actions.\n")
            return actions

        print(f"\n  Pending swing actions ({len(actions)}):")
        print(f"  {'ID':>5s}  {'Type':15s}  {'Symbol':12s}  "
              f"{'Qty':>5s}  {'Price':>10s}  {'Stop':>10s}")
        for a in actions:
            print(f"  {a.action_id:>5d}  {a.action_type:15s}  {a.symbol:12s}  "
                  f"{a.suggested_qty:>5d}  Rs.{a.suggested_price:>9,.2f}  "
                  f"Rs.{a.suggested_stop:>9,.2f}")
        print()
        return actions

    def list_positions(self) -> list[SwingPosition]:
        """List all open positions."""
        init_db()
        positions = open_positions()
        if not positions:
            print("\n  No open swing positions.\n")
            return positions

        print(f"\n  Open swing positions ({len(positions)}):")
        print(f"  {'ID':>5s}  {'Symbol':12s}  {'Qty':>5s}  "
              f"{'Entry':>10s}  {'Stop':>10s}  {'Date':10s}")
        for p in positions:
            print(f"  {p.position_id:>5d}  {p.symbol:12s}  {p.managed_qty:>5d}  "
                  f"Rs.{p.entry_price:>9,.2f}  Rs.{p.stop_price:>9,.2f}  "
                  f"{p.entry_date}")
        print()
        return positions

    # ── Helpers ─────────────────────────────────────────────────

    def _is_market_closed(self, ts: datetime.datetime) -> bool:
        """True if market has closed for the day."""
        if ts.hour > MARKET_CLOSE_HOUR:
            return True
        if ts.hour == MARKET_CLOSE_HOUR and ts.minute >= MARKET_CLOSE_MINUTE:
            return True
        return False

    def _print_banner(self) -> None:
        mode_label = "AI" if self.use_ai else "NoAI"
        print(f"""
================================================================
  Swing Trading Scanner ({mode_label})
================================================================
""")

    def _print_summary(self, result: SwingRunResult) -> None:
        accepted = [c for c in result.candidates if c.status == "ACCEPTED"]
        rejected = [c for c in result.candidates if c.status == "REJECTED"]
        open_pos = [p for p in result.positions if p.status == "OPEN"]

        pnl = realised_pnl_summary()

        print(f"""
================================================================
  Swing scan complete — {result.run_for_date}
  Mode: {result.mode}  |  Universe: {result.universe}
  Candidates: {len(result.candidates)} seen, {len(accepted)} accepted, {len(rejected)} rejected
  Open positions: {len(open_pos)}
  Actions: {len(result.actions)}
  Realised P&L: Rs.{pnl['net_pnl']:+,.2f} (gross Rs.{pnl['gross_pnl']:+,.2f}, charges Rs.{pnl['charges']:,.2f})
================================================================
""")
