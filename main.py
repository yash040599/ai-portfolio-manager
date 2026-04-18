# ================================================================
# main.py
# ================================================================
# Entry point. Run this file to start the portfolio manager.
#
# Usage:
#   python main.py --mode analyze                 ← portfolio analysis (read-only)
#   python main.py --mode trade                   ← V2 NoAI intraday trading (default)
#   python main.py --mode trade --ai              ← V2 with Claude AI selection
#   python main.py --mode trade --noai            ← same as default (explicit NoAI)
#   python main.py --mode trade --test            ← test NoAI strategy pipeline (no cost)
#   python main.py --mode trade --ai --test       ← test V2+Claude strategy pipeline
#   python main.py --mode trade --dryrun          ← full NoAI run, no real orders placed
#   python main.py --mode trade --ai --dryrun     ← full V2+Claude run, no real orders
#   python main.py --mode trade --v1              ← V1 legacy trading (retired)
#   python main.py --mode trade --v1 --dryrun     ← V1 dry run
#   python main.py --mode trade --nifty 150       ← scan NIFTY100 + next 50 mid caps
#   python main.py --mode trade --nifty 100       ← scan Nifty 100 (override config)
#
# --test   shows the strategy analysis pipeline without Claude or trades.
#          Useful for seeing how the bot analyses stocks, what scores
#          they get, and what the bot would do. No cost, no risk.
#
# --dryrun runs the FULL trading strategy (position monitoring, etc.)
#          but doesn't place real orders on Zerodha.
#
# Default mode is NoAI (pure technical signals, zero Claude calls).
# Use --ai to enable Claude for stock selection and position reviews.
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

    if use_v1 and use_v2:
        print("\n  Error: --v1 and --v2 are mutually exclusive.")
        sys.exit(1)

    if use_ai and use_noai:
        print("\n  Error: --ai and --noai are mutually exclusive.")
        sys.exit(1)

    if mode not in VALID_MODES:
        print("Usage: python main.py --mode [analyze|trade|login] [flags]")
        print()
        print("  analyze                    — read-only portfolio analysis")
        print()
        print("  trade                      — V2 NoAI intraday trading (default)")
        print("  trade --dryrun             — full strategy, no real orders")
        print("  trade --test               — show NoAI strategy analysis (no cost)")
        print("  trade --ai                 — V2 with Claude AI selection")
        print("  trade --ai --dryrun        — V2+Claude dry run")
        print("  trade --ai --test          — show V2+Claude strategy analysis (no cost)")
        print("  trade --noai               — same as default (explicit NoAI)")
        print("  trade --max 30000          — limit today's budget to Rs.30,000")
        print("  trade --nifty 50|100|150|200  — override scan universe (each tier adds 50 more)")
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

        # Set max budget override from --max flag
        if max_budget is not None:
            Config.MAX_BUDGET_INR = max_budget
            print(f"\n  Budget cap set to Rs.{max_budget:,} (via --max)\n")

        # Set scan universe override from --nifty flag
        if nifty_universe is not None:
            Config.SCAN_UNIVERSE = nifty_universe
            print(f"  Scan universe set to {nifty_universe} (via --nifty)\n")

        if use_v1:
            # V1 DEPRECATED — frozen as of 2026-04-08, no new features.
            # Still functional but not actively maintained or tested.
            if use_noai or use_ai or use_test:
                print("\n  Error: --noai, --ai, and --test are V2 features.")
                print("  V1 has no pre-filter strategy to test or run without AI.")
                print()
                print("  Usage:")
                print("    python main.py --mode trade --v1           ← V1 live trading")
                print("    python main.py --mode trade --v1 --dryrun  ← V1 dry run")
                print()
                print("  For V2 features, drop the --v1 flag:")
                print("    python main.py --mode trade --test         ← V2 strategy test")
                print("    python main.py --mode trade --ai           ← V2 with Claude")
                sys.exit(1)
            runner = PortfolioManager(Config)
            runner.run()
        else:
            # V2 is the default (--v2 is optional, same behavior)
            # Default mode is NoAI (pure technical signals).
            # Use --ai to enable Claude for selection & reviews.
            from portfolio.manager_v2 import PortfolioManagerV2
            runner = PortfolioManagerV2(Config)
            if use_ai:
                # Claude-enabled mode
                if use_test:
                    runner.run_test(noai=False)
                else:
                    runner.run()
            else:
                # NoAI mode (default, also triggered by explicit --noai)
                if use_test:
                    runner.run_test(noai=True)
                else:
                    runner.run_noai()

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
