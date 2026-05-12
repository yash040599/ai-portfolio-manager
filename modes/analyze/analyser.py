# ================================================================
# modes/analyze/analyser.py
# ================================================================
# Orchestrator for `python main.py --mode analyze`.
#
# Pipeline (ANALYZE_ROADMAP P1-P7, 2026-05-12):
#
#   1. Validate config + login to Zerodha
#   2. Fetch holdings
#   3. NoAI enrichment   (modes/analyze/enrich_noai.py)
#   4. Compute metrics   (modes/analyze/metrics.py — P6)
#   5. Run gap analysis  (modes/analyze/gaps.py — P7)
#   6. AI overlay        (modes/analyze/enrich_ai.py — only if --ai)
#   7. Persist snapshot  (modes/analyze/persistence.py — P2)
#   8. Render report     (modes/analyze/report.py — P5)
#
# Long-term lens throughout (no intraday). NoAI is the default; --ai
# adds Claude qualitative overlay on top of the same NoAI base.
# ================================================================

from __future__ import annotations

import datetime

from config              import Config, now_ist
from core.claude_client  import ClaudeClient
from core.logger         import Logger
from core.zerodha_client import ZerodhaClient
from modes.analyze.enrich_noai  import enrich_holdings
from modes.analyze.enrich_ai    import overlay_ai
from modes.analyze.gaps         import analyse_gaps
from modes.analyze.metrics      import compute_metrics
from modes.analyze.persistence  import (
    runs_between as _runs_between,
    save_snapshot,
)
from modes.analyze.report       import save_report
from modes.analyze.types        import PortfolioSnapshot


class PortfolioAnalyser:
    """Read-only long-term portfolio analyser.

    Every run produces a `PortfolioSnapshot` that lands in:
      data/portfolio_analyses.db                                (P2)
      reports/portfolio/<YYYY>/<MM>/portfolio_report_DD.txt     (P5)
      reports/portfolio/<YYYY>/<MM>/portfolio_data_DD.json      (P5)
    """

    def __init__(self, config: type[Config], use_ai: bool = False):
        self.cfg = config
        self.use_ai = bool(use_ai)
        self.log     = Logger("PortfolioAnalyser")
        self.zerodha = ZerodhaClient(config, Logger("ZerodhaClient"))
        self.claude: ClaudeClient | None = (
            ClaudeClient(config, Logger("ClaudeClient")) if self.use_ai else None
        )

    # ── Run ─────────────────────────────────────────────────────

    def run(self) -> PortfolioSnapshot | None:
        self._print_banner()

        # 1. Validate config (Claude only required when --ai)
        missing = self.cfg.validate(require_claude=self.use_ai)
        if missing:
            self.log.section("CONFIGURATION ERROR")
            for k in missing:
                self.log.error(f"Missing in .env file: {k}=your_value_here")
            return None

        for warning in self.cfg.mismatch_warnings():
            self.log.warning(f"Plan mismatch: {warning}")

        # 2. Login + account snapshot
        self.log.section("ZERODHA LOGIN")
        self.zerodha.login()
        self.zerodha.print_account_snapshot()

        # 3. Holdings
        self.log.section("FETCHING HOLDINGS")
        holdings = self.zerodha.get_holdings()
        if not holdings:
            self.log.warning("No holdings found in your demat account.")
            return None
        self.log.success(f"Found {len(holdings)} stock(s) in your demat account")

        # 4. NoAI enrichment (always runs)
        self.log.section("NOAI ENRICHMENT")
        records = enrich_holdings(holdings, zerodha=self.zerodha,
                                  log=self.log, cfg=self.cfg)
        if not records:
            self.log.warning("Enrichment produced no records — abort.")
            return None

        # 5. Portfolio metrics (P6 + P8 risk/return + cash drag)
        self.log.section("PORTFOLIO METRICS")
        cash_balance: float | None = None
        try:
            cash_balance = self.zerodha.get_available_funds()
            self.log.info(f"Cash balance available: Rs.{cash_balance:,.2f}")
        except Exception as e:
            self.log.warning(f"Cash-balance fetch failed: {e} — cash drag will be skipped")
        # Prior runs power max-DD + XIRR. Pull a generous window so the
        # CAGR has a real time anchor; metrics.py filters to >= 30d.
        prior_runs: list[dict] = []
        try:
            today = now_ist().date()
            d_from = (today - datetime.timedelta(days=400)).isoformat()
            d_to   = today.isoformat()
            prior_runs = _runs_between(d_from, d_to)
        except Exception as e:
            self.log.debug(f"Prior-run lookup failed: {e}")
        metrics = compute_metrics(
            records,
            cash_balance=cash_balance,
            prior_runs=prior_runs,
        )
        hhi_v  = metrics.hhi_concentration.value
        top5_v = metrics.top_5_concentration_pct.value
        pe_v   = metrics.weighted_pe.value
        hhi_s  = f"{hhi_v:.0f}"  if hhi_v  is not None else "n/a"
        top5_s = f"{top5_v:.1f}%" if top5_v is not None else "n/a"
        pe_s   = f"{pe_v:.1f}"   if pe_v   is not None else "n/a"
        self.log.success(f"HHI {hhi_s}, top-5 {top5_s}, weighted P/E {pe_s}")
        if metrics.sharpe_ratio and metrics.sharpe_ratio.value is not None:
            self.log.info(
                f"Sharpe {metrics.sharpe_ratio.value:.2f}, "
                f"vol {metrics.volatility_30d_pct.value:.1f}% (annualised)"
            )

        # 6. Gap analysis (P7)
        self.log.section("GAP ANALYSIS")
        gaps = analyse_gaps(records, metrics)
        if gaps.flags:
            self.log.info(
                f"{len(gaps.flags)} gap(s) flagged "
                f"({sum(1 for f in gaps.flags if f.severity == 'RISK')} RISK, "
                f"{sum(1 for f in gaps.flags if f.severity == 'WARN')} WARN)"
            )
        else:
            self.log.success("No structural gaps detected against benchmark")

        # 7. AI overlay (only when --ai)
        mode = "AI" if self.use_ai else "NOAI"
        if self.use_ai and self.claude is not None:
            self.log.section("AI OVERLAY (Claude)")
            overlay_ai(records, claude=self.claude, log=self.log, cfg=self.cfg)

        # 8. Persist + render
        snapshot = PortfolioSnapshot(
            timestamp = now_ist(),
            mode      = mode,
            holdings  = records,
            metrics   = metrics,
            gaps      = gaps,
            notes     = "",
        )
        self.log.section("PERSIST + REPORT")
        try:
            run_id = save_snapshot(snapshot)
            self.log.success(f"Snapshot persisted as run #{run_id}")
        except Exception as e:
            self.log.warning(f"Persist failed (continuing with file output): {e}")
        try:
            txt_path, json_path = save_report(snapshot, self.log)
        except Exception as e:
            self.log.error(f"Report write failed: {e}")
            txt_path = json_path = None

        self._print_summary(snapshot, txt_path, json_path)
        return snapshot

    # ── Single-stock targeted re-analysis ───────────────────────

    def analyse_single_stock(self, symbol: str) -> PortfolioSnapshot | None:
        """Re-run enrichment for ONE symbol and merge into the latest
        snapshot.

        Two cases:
          (a) `symbol` is in the user's demat holdings — refresh that
              one row (price, technicals, AI overlay if enabled);
              portfolio metrics + gaps stay as-is from the prior run.
          (b) `symbol` is NOT in holdings — treat as a "wishlist"
              entry. Synthesise a holding with qty=0 + avg=0, run
              enrichment, append to the snapshot. Portfolio metrics
              still come from the prior run (the wishlist row has
              zero weight so it would not move HHI / sector mix
              even if we did recompute).

        Returns the updated PortfolioSnapshot, or None when no prior
        full-portfolio run exists (the user must run "Analyse all"
        once before single-stock re-analyses make sense).
        """
        from modes.analyze.persistence import latest_snapshot, save_snapshot

        symbol = (symbol or "").strip().upper()
        if not symbol:
            self.log.error("analyse_single_stock: empty symbol")
            return None

        # 1. Validate config (Claude only required when --ai)
        missing = self.cfg.validate(require_claude=self.use_ai)
        if missing:
            self.log.section("CONFIGURATION ERROR")
            for k in missing:
                self.log.error(f"Missing in .env file: {k}=your_value_here")
            return None

        # 2. Login
        self.log.section(f"ZERODHA LOGIN (single-stock {symbol})")
        self.zerodha.login()

        # 3. Find existing holding row OR build a wishlist row.
        prior = latest_snapshot()
        if prior is None:
            self.log.error(
                "No prior full-portfolio run on file. Run 'Analyse all' "
                "once before re-analysing a single stock."
            )
            return None

        zerodha_holdings = []
        try:
            zerodha_holdings = self.zerodha.get_holdings()
        except Exception as e:
            self.log.warning(f"Holdings fetch failed: {e}")

        target = next(
            (h for h in zerodha_holdings if h.get("symbol") == symbol),
            None,
        )
        if target is None:
            # Wishlist entry. Treat as a zero-quantity holding so the
            # enrich pipeline still pulls quote + 1y candles + sector
            # + tier + dividends + P/E.
            self.log.info(
                f"{symbol} not in your demat — running as wishlist entry"
            )
            target = {
                "symbol": symbol,
                "exchange": "NSE",
                "quantity": 0,
                "avg_buy_price": 0.0,
                "current_price": 0.0,
                "invested_value": 0.0,
                "current_value": 0.0,
                "pnl": 0.0,
                "pnl_percent": 0.0,
            }

        # 4. NoAI enrichment for just this one stock.
        self.log.section(f"NOAI ENRICHMENT ({symbol})")
        records = enrich_holdings([target], zerodha=self.zerodha,
                                  log=self.log, cfg=self.cfg)
        if not records:
            self.log.error(f"Enrichment produced no record for {symbol}")
            return None
        new_record = records[0]

        # 5. Optional AI overlay.
        if self.use_ai and self.claude is not None:
            self.log.section(f"AI OVERLAY (Claude) for {symbol}")
            overlay_ai([new_record], claude=self.claude,
                       log=self.log, cfg=self.cfg)

        # 6. Merge into prior snapshot.
        merged_holdings: list = []
        replaced = False
        for h in prior.holdings:
            if h.symbol == symbol:
                merged_holdings.append(new_record)
                replaced = True
            else:
                merged_holdings.append(h)
        if not replaced:
            merged_holdings.append(new_record)

        # Preserve prior metrics + gaps (single-stock refresh doesn't
        # invalidate them — wishlist row has zero weight; held-row
        # refresh moves at most the symbol's own value but the diff
        # is small enough that next full run will reconcile).
        snapshot = PortfolioSnapshot(
            timestamp = now_ist(),
            mode      = "AI" if self.use_ai else "NOAI",
            holdings  = merged_holdings,
            metrics   = prior.metrics,
            gaps      = prior.gaps,
            notes     = (prior.notes or "") + f" [single-stock refresh: {symbol}]",
        )

        # 7. Persist (same-day upsert handles dupe-prevention).
        try:
            run_id = save_snapshot(snapshot)
            self.log.success(
                f"Single-stock refresh persisted as run #{run_id}"
            )
        except Exception as e:
            self.log.warning(f"Persist failed: {e}")

        return snapshot

    # ── Display ─────────────────────────────────────────────────

    def _print_banner(self) -> None:
        plan = self.cfg.claude()
        zrd  = self.cfg.zerodha()
        mode_label = "AI" if self.use_ai else "NOAI"
        print(f"\n{'='*64}")
        print("  AI PORTFOLIO MANAGER — ANALYSE MODE")
        print(f"{'='*64}")
        print(f"  Run mode       : {mode_label}  "
              f"({'Claude overlay enabled' if self.use_ai else 'deterministic only — no Claude calls'})")
        if self.use_ai:
            print(f"  Claude plan    : {self.cfg.CLAUDE_PLAN.upper()}  "
                  f"(model {plan['model']})")
        print(f"  Zerodha plan   : {self.cfg.ZERODHA_PLAN.upper()}  "
              f"(price source {zrd['price_source']})")
        print(f"{'='*64}\n")

    def _print_summary(self, snap: PortfolioSnapshot,
                       txt_path: str | None,
                       json_path: str | None) -> None:
        m = snap.metrics
        invested = (m.total_invested.value or 0) if m.total_invested else 0
        current  = (m.total_current_value.value or 0) if m.total_current_value else 0
        pnl      = (m.total_pnl.value or 0) if m.total_pnl else 0
        pnl_pct  = (m.total_pnl_pct.value or 0) if m.total_pnl_pct else 0
        most_stale = snap.most_stale_at()
        print(f"\n{'='*64}")
        self.log.success("Analyse run complete")
        self.log.success(
            f"Holdings : {len(snap.holdings)}  ·  "
            f"Invested Rs.{invested:,.0f}  ·  Current Rs.{current:,.0f}  ·  "
            f"P&L Rs.{pnl:+,.0f} ({pnl_pct:+.2f}%)"
        )
        self.log.info(
            f"Most stale field across all enrichment: "
            f"{most_stale.strftime('%Y-%m-%d %H:%M IST')}"
        )
        if txt_path:
            print(f"\n  Report : {txt_path}")
        if json_path:
            print(f"  Data   : {json_path}")
        print("\n  Re-run with --ai for Claude qualitative overlay" if not self.use_ai
              else "\n  AI overlay applied")
        print(f"{'='*64}\n")
