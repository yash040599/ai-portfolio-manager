# ================================================================
# main.py
# ================================================================
# Entry point. Run this file to start the portfolio manager.
#
# Usage:
#   python main.py --mode analyze                 ← portfolio analysis (read-only)
#   python main.py --mode trade                   ← V2 intraday trading (default)
#   python main.py --mode trade --v2              ← same as above (explicit V2)
#   python main.py --mode trade --noai            ← V2 fully automated, zero Claude calls
#   python main.py --mode trade --v2 --noai       ← same as above (explicit)
#   python main.py --mode trade --test            ← test V2 strategy pipeline (no cost)
#   python main.py --mode trade --noai --test     ← test NoAI strategy pipeline
#   python main.py --mode trade --dryrun          ← full V2 run, no real orders placed
#   python main.py --mode trade --v1              ← V1 legacy trading (retired)
#   python main.py --mode trade --v1 --dryrun     ← V1 dry run
#
# --test   shows the strategy analysis pipeline without Claude or trades.
#          Useful for seeing how the bot analyses stocks, what scores
#          they get, and what the bot would do. No cost, no risk.
#
# --dryrun runs the FULL trading strategy (Claude calls, position
#          monitoring, etc.) but doesn't place real orders on Zerodha.
#
# To change plans or budget:
#   Edit config.py — nothing else needs to change.
# ================================================================

import sys
from config              import Config
from core.logger         import Logger
from core.zerodha_client import ZerodhaClient
from portfolio.analyser  import PortfolioAnalyser
from portfolio.manager   import PortfolioManager

VALID_MODES = {"analyze", "trade", "login"}


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
    use_v1     = "--v1"     in sys.argv
    use_v2     = "--v2"     in sys.argv
    use_test   = "--test"   in sys.argv
    use_noai   = "--noai"   in sys.argv
    use_dryrun = "--dryrun" in sys.argv

    if use_v1 and use_v2:
        print("\n  Error: --v1 and --v2 are mutually exclusive.")
        sys.exit(1)

    if mode not in VALID_MODES:
        print("Usage: python main.py --mode [analyze|trade|login] [flags]")
        print()
        print("  analyze                    — read-only portfolio analysis")
        print()
        print("  trade                      — V2 intraday trading (default)")
        print("  trade --dryrun             — full strategy, no real orders")
        print("  trade --test               — show V2 strategy analysis (no cost)")
        print("  trade --noai               — V2 fully automated, zero Claude calls")
        print("  trade --noai --dryrun      — NoAI dry run")
        print("  trade --noai --test        — show NoAI strategy analysis (no cost)")
        print()
        print("  trade --v1                 — V1 legacy trading (retired)")
        print("  trade --v1 --dryrun        — V1 dry run")
        print()
        print("  login                      — test Zerodha login only")
        sys.exit(1)

    if mode == "analyze":
        runner = PortfolioAnalyser(Config)
        runner.run()

    elif mode == "trade":
        # Set DRY_RUN from CLI flag
        if use_dryrun:
            Config.DRY_RUN = True

        if use_v1:
            # V1 legacy mode (retired — kept for comparison)
            if use_noai or use_test:
                print("\n  Error: --noai and --test are V2 features.")
                print("  V1 has no pre-filter strategy to test or run without AI.")
                print()
                print("  Usage:")
                print("    python main.py --mode trade --v1           ← V1 live trading")
                print("    python main.py --mode trade --v1 --dryrun  ← V1 dry run")
                print()
                print("  For V2 features, drop the --v1 flag:")
                print("    python main.py --mode trade --test         ← V2 strategy test")
                print("    python main.py --mode trade --noai         ← V2 no-AI mode")
                sys.exit(1)
            runner = PortfolioManager(Config)
            runner.run()
        else:
            # V2 is the default (--v2 is optional, same behavior)
            from portfolio.manager_v2 import PortfolioManagerV2
            runner = PortfolioManagerV2(Config)
            if use_noai:
                if use_test:
                    runner.run_test(noai=True)
                else:
                    runner.run_noai()
            elif use_test:
                runner.run_test()
            else:
                runner.run()

    elif mode == "login":
        missing = Config.validate()
        if missing:
            for key in missing:
                print(f"Missing in .env: {key}")
            sys.exit(1)
        client = ZerodhaClient(Config, Logger("ZerodhaLogin"))
        client.login()
        client.print_account_snapshot()


if __name__ == "__main__":
    main()
