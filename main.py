# ================================================================
# main.py
# ================================================================
# Entry point. Run this file to start the portfolio manager.
#
# Usage:
#   python main.py --mode analyze     ← portfolio analysis (read-only)
#   python main.py --mode trade       ← intraday trading bot (V1)
#   python main.py --mode trade --v2  ← intraday trading bot (V2 candle strategy)
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

    # Check for --v2 and --test flags
    use_v2   = "--v2"   in sys.argv
    use_test = "--test" in sys.argv

    if mode not in VALID_MODES:
        print("Usage: python main.py --mode [analyze|trade|login] [--v2] [--test]")
        print("  analyze           — read-only portfolio analysis")
        print("  trade             — intraday trading bot (V1)")
        print("  trade --v2        — intraday trading bot (V2 candle strategy)")
        print("  trade --v2 --test — test V2 candle pipeline (no Claude calls, no trades)")
        print("  login             — test Zerodha login only")
        sys.exit(1)

    if mode == "analyze":
        runner = PortfolioAnalyser(Config)
        runner.run()

    elif mode == "trade":
        if use_v2:
            from portfolio.manager_v2 import PortfolioManagerV2
            runner = PortfolioManagerV2(Config)
            if use_test:
                runner.run_test()
            else:
                runner.run()
        else:
            if use_test:
                print("--test is only supported with --v2 (candle strategy)")
                sys.exit(1)
            runner = PortfolioManager(Config)
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
