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
from modes.swing.review import review_position
from modes.swing.persistence import (
    init_db, save_run, open_positions, realised_pnl_summary,
    latest_run_for_date_and_mode, pending_actions,
)
from modes.swing.report import save_report
from modes.swing.types import (
    SwingRunResult, SwingAction, SwingCandidate, SwingPosition,
    ACTION_ENTRY, STATUS_PENDING,
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

        # 8. AI overlay (only if explicitly requested)
        if self.use_ai and self.claude:
            self.log.section("AI OVERLAY")
            from modes.swing.ai_overlay import overlay_ai_on_candidates
            candidates = overlay_ai_on_candidates(
                candidates, self.claude, self.log)

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
