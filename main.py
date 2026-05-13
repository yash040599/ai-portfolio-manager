# ================================================================
# main.py
# ================================================================
# Entry point. Run this file to start the portfolio manager.
#
# Usage:
#   python main.py --mode analyze                 ← long-term portfolio analysis (NoAI default)
#   python main.py --mode analyze --ai            ← analyse + Claude qualitative overlay
#   python main.py --mode trade                   ← NoAI intraday trading (default)
#   python main.py --mode trade --noai            ← same as default (explicit NoAI)
#   python main.py --mode trade --ai              ← with Claude AI selection
#   python main.py --mode trade --test            ← show NoAI strategy analysis (no cost)
#   python main.py --mode trade --ai --test       ← show AI strategy analysis (no cost)
#   python main.py --mode trade --dryrun          ← full NoAI run, no real orders placed
#   python main.py --mode trade --ai --dryrun     ← full AI run, no real orders
#   python main.py --mode trade --max 30000       ← limit today's budget to Rs.30,000
#   python main.py --mode trade --nifty 50|100|150|200  ← override scan universe
#   python main.py --mode swing                   ← NoAI swing scan (after market close)
#   python main.py --mode swing --ai               ← swing scan + Claude qualitative overlay
#   python main.py --mode swing --actions           ← list pending swing actions
#   python main.py --mode swing --positions         ← list open swing book
#   python main.py --mode swing --confirm <ID> --qty N --price P  ← confirm a pending action
#   python main.py --mode swing --skip <ID>         ← skip a pending action
#   python main.py --mode login                   ← test Zerodha login only
#   python main.py --mode dashboard               ← launch the web dashboard
#
# --test   shows the strategy analysis pipeline without Claude or trades.
#          Useful for seeing how the bot analyses stocks, what scores
#          they get, and what the bot would do. No cost, no risk.
#
# --dryrun runs the FULL trading strategy (position monitoring, etc.)
#          but doesn't place real orders on Zerodha.
#
# Default mode is NoAI (pure technical signals, zero Claude API calls).
# Use --ai to enable Claude for stock selection and position reviews.
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
        print("  analyze --ai                  — analyse + Claude qualitative overlay")
        print()
        print("  trade                         — NoAI intraday trading (default)")
        print("  trade --dryrun                — full strategy, no real orders")
        print("  trade --test                  — show NoAI strategy analysis (no cost)")
        print("  trade --ai                    — with Claude AI selection")
        print("  trade --ai --dryrun           — AI run, no real orders")
        print("  trade --ai --test             — show AI strategy analysis (no cost)")
        print("  trade --noai                  — same as default (explicit NoAI)")
        print("  trade --max 30000             — limit today's budget to Rs.30,000")
        print("  trade --nifty 50|100|150|200  — override scan universe")
        print()
        print("  swing                         — NoAI swing scan (after market close)")
        print("  swing --ai                    — swing scan + Claude overlay")
        print("  swing --actions               — list pending swing actions")
        print("  swing --positions             — list open swing book")
        print("  swing --confirm <ID> --qty N --price P  — confirm action")
        print("  swing --skip <ID>             — skip a pending action")
        print()
        print("  login                         — test Zerodha login only")
        print("  dashboard                     — launch the web dashboard")
        sys.exit(1)

    if mode == "analyze":
        # Default flow is NoAI (zero Claude cost). --ai opts in to the
        # Claude qualitative overlay on top of the same NoAI base.
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
        # zero Claude API calls). Use --ai for Claude selection + reviews.
        runner = PortfolioManager(Config)
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
