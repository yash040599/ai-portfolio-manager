# ================================================================
# main.py
# ================================================================
# Entry point. Run this file to start the portfolio manager.
#
# Usage:
#   python main.py --mode analyze                 ← long-term portfolio analysis (NoAI default)
#   python main.py --mode analyze --ai            ← analyse + AI qualitative overlay
#   python main.py --mode trade                   ← NoAI intraday trading (default)
#   python main.py --mode trade --noai            ← same as default (explicit NoAI)
#   python main.py --mode trade --ai              ← with AI selection (Gemini/GPT/Claude)
#   python main.py --mode trade --test            ← show NoAI strategy analysis (no cost)
#   python main.py --mode trade --ai --test       ← show AI strategy analysis (no cost)
#   python main.py --mode trade --dryrun          ← full NoAI run, no real orders placed
#   python main.py --mode trade --ai --dryrun     ← full AI run, no real orders
#   python main.py --mode trade --max 30000       ← limit today's budget to Rs.30,000
#   python main.py --mode trade --nifty 50|100|150|200  ← override scan universe
#   python main.py --mode swing                   ← NoAI swing scan (after market close)
#   python main.py --mode swing --ai               ← swing scan + AI qualitative overlay
#   python main.py --mode swing --actions           ← list pending swing actions
#   python main.py --mode swing --positions         ← list open swing book
#   python main.py --mode swing --confirm <ID> --qty N --price P  ← confirm a pending action
#   python main.py --mode swing --skip <ID>         ← skip a pending action
#   python main.py --mode swing --compare HDFCBANK,SBIN,ICICIBANK,KOTAKBANK
#                                                    ← side-by-side compare up to 4
#   python main.py --mode swing --compare-sector BANKING
#                                                    ← top 4 in a sector, auto-picked
#   python main.py --mode login                   ← test Zerodha login only
#   python main.py --mode dashboard               ← launch the web dashboard
#
# --test   shows the strategy analysis pipeline without AI calls or trades.
#          Useful for seeing how the bot analyses stocks, what scores
#          they get, and what the bot would do. No cost, no risk.
#
# --dryrun runs the FULL trading strategy (position monitoring, etc.)
#          but doesn't place real orders on Zerodha.
#
# Default mode is NoAI (pure technical signals, zero AI API calls).
# Use --ai to enable AI for stock selection and position reviews.
# AI provider is set by AI_PROVIDER in config.py (default: gemini).
#
# To change plans or budget:
#   Edit config.py — nothing else needs to change.
# ================================================================

import sys
from config              import Config
from core.logger         import Logger
from core.zerodha_client import ZerodhaClient
from modes.analyze.analyser  import PortfolioAnalyser
from modes.trade.manager   import PortfolioManager

VALID_MODES = {"analyze", "trade", "swing", "login", "dashboard"}


def main():
    # Parse --mode argument
    mode = None
    if "--mode" in sys.argv:
        try:
            mode = sys.argv[sys.argv.index("--mode") + 1].lower()
        except (IndexError, ValueError):
            pass

    # Backward compatibility: support old --phase 1/2 syntax
    if mode is None and "--phase" in sys.argv:
        try:
            phase = int(sys.argv[sys.argv.index("--phase") + 1])
            mode = "analyze" if phase == 1 else "trade" if phase == 2 else None
        except (IndexError, ValueError):
            pass

    # Parse CLI flags
    use_test   = "--test"   in sys.argv
    use_noai   = "--noai"   in sys.argv
    use_ai     = "--ai"     in sys.argv
    use_dryrun = "--dryrun" in sys.argv

    # Parse --max budget override (e.g. --max 30000 or --max 30_000)
    max_budget = None
    if "--max" in sys.argv:
        try:
            raw = sys.argv[sys.argv.index("--max") + 1]
            max_budget = int(raw.replace("_", "").replace(",", ""))
            if max_budget <= 0:
                print(f"\n  Error: --max must be a positive amount, got {raw}")
                sys.exit(1)
        except (IndexError, ValueError):
            print("\n  Error: --max requires a numeric amount (e.g. --max 30000)")
            sys.exit(1)

    # Parse --nifty universe override (e.g. --nifty 50 / 100 / 150 / 200)
    nifty_universe = None
    if "--nifty" in sys.argv:
        try:
            raw = sys.argv[sys.argv.index("--nifty") + 1].strip().lower()
        except (IndexError, ValueError):
            print("\n  Error: --nifty requires a value (50, 100, 150, or 200)")
            sys.exit(1)
        mapping = {
            "50":  "NIFTY50",
            "100": "NIFTY100",
            "150": "NIFTY150",
            "200": "NIFTY200",
        }
        if raw not in mapping:
            print(f"\n  Error: invalid --nifty value '{raw}'.")
            print("  We only support 50, 100, 150 or 200 as of now.")
            print("  Usage: --nifty 50 | --nifty 100 | --nifty 150 | --nifty 200")
            sys.exit(1)
        nifty_universe = mapping[raw]

    if "--v1" in sys.argv or "--v2" in sys.argv:
        flag = "--v1" if "--v1" in sys.argv else "--v2"
        print(f"\n  Note: {flag} is no longer recognised — there is only one")
        print("  trading strategy now. Use --noai (default) or --ai instead.")
        sys.exit(1)

    if use_ai and use_noai:
        print("\n  Error: --ai and --noai are mutually exclusive.")
        sys.exit(1)

    if mode not in VALID_MODES:
        print("Usage: python main.py --mode [analyze|trade|swing|login|dashboard] [flags]")
        print()
        print("  analyze                       — long-term portfolio analysis (NoAI, default)")
        print("  analyze --ai                  — analyse + AI qualitative overlay")
        print()
        print("  trade                         — NoAI intraday trading (default)")
        print("  trade --dryrun                — full strategy, no real orders")
        print("  trade --test                  — show NoAI strategy analysis (no cost)")
        print("  trade --ai                    — with AI selection")
        print("  trade --ai --dryrun           — AI run, no real orders")
        print("  trade --ai --test             — show AI strategy analysis (no cost)")
        print("  trade --noai                  — same as default (explicit NoAI)")
        print("  trade --max 30000             — limit today's budget to Rs.30,000")
        print("  trade --nifty 50|100|150|200  — override scan universe")
        print()
        print("  swing                         — NoAI swing scan (after market close)")
        print("  swing --ai                    — swing scan + AI overlay")
        print("  swing --actions               — list pending swing actions")
        print("  swing --positions             — list open swing book")
        print("  swing --confirm <ID> --qty N --price P  — confirm action")
        print("  swing --skip <ID>             — skip a pending action")
        print()
        print("  login                         — test Zerodha login only")
        print("  dashboard                     — launch the web dashboard")
        sys.exit(1)

    if mode == "analyze":
        # Default flow is NoAI (zero AI cost). --ai opts in to the
        # AI qualitative overlay on top of the same NoAI base.
        runner = PortfolioAnalyser(Config, use_ai=use_ai)
        runner.run()

    elif mode == "trade":
        # Set DRY_RUN from CLI flag
        if use_dryrun:
            Config.DRY_RUN = True

        # Set max budget override from --max flag
        if max_budget is not None:
            Config.MAX_BUDGET_INR = max_budget
            print(f"\n  Budget cap set to Rs.{max_budget:,} (via --max)\n")

        # Set scan universe override from --nifty flag
        if nifty_universe is not None:
            Config.SCAN_UNIVERSE = nifty_universe
            print(f"  Scan universe set to {nifty_universe} (via --nifty)\n")

        # Single trading entry point. Default mode is NoAI (pure rules,
        # zero AI API calls). Use --ai for AI selection + reviews.
        #
        # Gap-and-Go strategy is pure rules-based — AI adds no value
        # (the alpha is gap+volume, not score ranking). Auto-downgrade
        # to noai when Gap-and-Go is active.
        runner = PortfolioManager(Config)
        profile = getattr(Config, "TRADE_STRATEGY_PROFILE", "NOAI_LEGACY_FULL")
        if use_ai and (profile == "NOAI_GAP_AND_GO" or profile.startswith("NOAI_GAP_AND_GO_")):
            print(
                "\n  Gap-and-Go strategy is pure rules-based (gap + volume signal)."
                "\n  AI selection adds no value here."
                "\n"
                "\n  Options:"
                "\n    [1] Continue with Gap-and-Go (NoAI) — recommended, backtest PF 1.37"
                "\n    [2] Switch to AI + legacy score-based strategy (backtest PF 0.86)"
                "\n    [3] Exit"
                "\n"
            )
            try:
                choice = input("  Your choice [1]: ").strip()
            except (EOFError, KeyboardInterrupt):
                choice = "1"
            if choice == "2":
                print("  Switching to AI + NOAI_LEGACY_FULL.\n")
                Config.TRADE_STRATEGY_PROFILE = "NOAI_LEGACY_FULL"
            elif choice == "3":
                print("  Exiting.")
                sys.exit(0)
            else:
                print("  Continuing with Gap-and-Go (NoAI).\n")
                use_ai = False
        if use_ai:
            if use_test:
                runner.run_test(noai=False)
            else:
                runner.run()
        else:
            if use_test:
                runner.run_test(noai=True)
            else:
                runner.run_noai()

    elif mode == "swing":
        from modes.swing.manager import SwingManager
        from modes.swing.persistence import confirm_action, skip_action, init_db

        # Set scan universe override from --nifty flag
        if nifty_universe is not None:
            Config.SCAN_UNIVERSE = nifty_universe

        # Sub-commands
        if "--actions" in sys.argv:
            init_db()
            SwingManager(Config).list_actions()

        elif "--positions" in sys.argv:
            init_db()
            SwingManager(Config).list_positions()

        elif "--confirm" in sys.argv:
            init_db()
            try:
                aid = int(sys.argv[sys.argv.index("--confirm") + 1])
            except (IndexError, ValueError):
                print("  Error: --confirm requires an action ID")
                sys.exit(1)
            qty = 0
            price = 0.0
            stop = 0.0
            if "--qty" in sys.argv:
                try:
                    qty = int(sys.argv[sys.argv.index("--qty") + 1])
                except (IndexError, ValueError):
                    pass
            if "--price" in sys.argv:
                try:
                    price = float(sys.argv[sys.argv.index("--price") + 1])
                except (IndexError, ValueError):
                    pass
            if "--stop" in sys.argv:
                try:
                    stop = float(sys.argv[sys.argv.index("--stop") + 1])
                except (IndexError, ValueError):
                    pass
            result = confirm_action(
                action_id=aid, executed_qty=qty, executed_price=price,
                source="CLI", confirmed_stop=stop,
            )
            if result:
                print(f"  Confirmed action #{aid} -> position {result.symbol} "
                      f"(qty={result.managed_qty}, status={result.status})")
            else:
                print(f"  Failed to confirm action #{aid} (not found or not pending)")

        elif "--skip" in sys.argv:
            init_db()
            try:
                aid = int(sys.argv[sys.argv.index("--skip") + 1])
            except (IndexError, ValueError):
                print("  Error: --skip requires an action ID")
                sys.exit(1)
            reason = ""
            if "--reason" in sys.argv:
                try:
                    reason = sys.argv[sys.argv.index("--reason") + 1]
                except (IndexError, ValueError):
                    pass
            ok = skip_action(aid, reason)
            print(f"  {'Skipped' if ok else 'Failed to skip'} action #{aid}")

        elif "--compare" in sys.argv or "--compare-sector" in sys.argv:
            # Compare up to 4 stocks side-by-side (S45).
            #   --compare HDFCBANK,SBIN,ICICIBANK,KOTAKBANK
            #   --compare-sector BANKING
            init_db()
            missing = Config.validate()
            if missing:
                for key in missing:
                    print(f"Missing in .env: {key}")
                sys.exit(1)
            from modes.swing.compare import (
                MAX_COMPARE_STOCKS, normalise_sector, top_n_in_sector,
                compare_symbols, render_text_table, list_known_sectors,
            )
            from modes.swing.scanner import SwingScanner
            from core.zerodha_client import ZerodhaClient

            chosen_sector = ""
            symbols: list[str] = []
            if "--compare-sector" in sys.argv:
                try:
                    sec_raw = sys.argv[sys.argv.index("--compare-sector") + 1]
                except (IndexError, ValueError):
                    print("  Error: --compare-sector requires a sector name. "
                          f"Known: {', '.join(list_known_sectors())}")
                    sys.exit(1)
                chosen_sector = normalise_sector(sec_raw)
                symbols = top_n_in_sector(
                    chosen_sector, n=MAX_COMPARE_STOCKS)
                if not symbols:
                    print(f"  Error: no symbols found in sector "
                          f"{chosen_sector!r}. "
                          f"Known: {', '.join(list_known_sectors())}")
                    sys.exit(1)
            else:
                try:
                    raw = sys.argv[sys.argv.index("--compare") + 1]
                except (IndexError, ValueError):
                    print("  Error: --compare requires a comma-separated "
                          "list of NSE tickers (max 4).")
                    sys.exit(1)
                symbols = [s.strip().upper()
                           for s in raw.split(",") if s.strip()]
                if not symbols:
                    print("  Error: no symbols parsed from --compare.")
                    sys.exit(1)
                if len(symbols) > MAX_COMPARE_STOCKS:
                    print(f"  Note: truncating to first {MAX_COMPARE_STOCKS} "
                          f"symbols (got {len(symbols)}).")
                    symbols = symbols[:MAX_COMPARE_STOCKS]

            zerodha = ZerodhaClient(Config, Logger("ZerodhaClient"))
            zerodha.login()
            scanner = SwingScanner(Config, zerodha, Logger("SwingCompare"))

            def _scan_one(sym: str):
                return scanner.scan_one(sym, swing_capital=100_000.0)

            result = compare_symbols(
                symbols, scan_one=_scan_one, sector=chosen_sector)
            print(render_text_table(result))

        elif "--backtest" in sys.argv:
            # ATH dip-buy backtest: runs X/Y matrix simulation
            from modes.swing.ath_backtest import ATHBacktester, format_backtest_report
            missing = Config.validate()
            if missing:
                for key in missing:
                    print(f"Missing in .env: {key}")
                sys.exit(1)
            from core.zerodha_client import ZerodhaClient
            zerodha = ZerodhaClient(Config, Logger("ZerodhaClient"))
            zerodha.login()
            bt = ATHBacktester(zerodha, Logger("ATHBacktest"))
            matrix = bt.run_matrix()
            report = format_backtest_report(matrix)
            print(report)
            # Save report
            import os, json
            os.makedirs("reports/backtest", exist_ok=True)
            with open("reports/backtest/ath_backtest.txt", "w") as f:
                f.write(report)
            with open("reports/backtest/ath_backtest.json", "w") as f:
                json.dump(matrix.to_dict(), f, indent=2, default=str)
            print(f"  Saved: reports/backtest/ath_backtest.txt + .json")

        else:
            # Default: run the swing scan
            runner = SwingManager(Config, use_ai=use_ai)
            runner.run(trigger_source="CLI")

    elif mode == "login":
        missing = Config.validate()
        if missing:
            for key in missing:
                print(f"Missing in .env: {key}")
            sys.exit(1)
        from core.zerodha_client import ZerodhaClient
        client = ZerodhaClient(Config, Logger("ZerodhaLogin"))
        client.login()
        client.print_account_snapshot()

    elif mode == "dashboard":
        # Read-only profitability dashboard. All flags after `--mode
        # dashboard` are forwarded to Dashboard.cli (its own argparse).
        from modes.dashboard.cli import main as dashboard_main
        idx = sys.argv.index("--mode")
        forwarded = [a for a in sys.argv[idx + 2:] if a]
        sys.exit(dashboard_main(forwarded))


if __name__ == "__main__":
    main()
