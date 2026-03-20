# ================================================================
# main.py
# ================================================================
# Entry point. Run this file to start the portfolio manager.
#
# Usage:
#   python main.py --mode analyze  ← portfolio analysis (read-only)
#   python main.py --mode trade    ← intraday trading bot
#
# To change plans or budget:
#   Edit config.py — nothing else needs to change.
# ================================================================

import sys
from config              import Config
from portfolio.analyser  import PortfolioAnalyser
from portfolio.manager   import PortfolioManager

VALID_MODES = {"analyze", "trade"}


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

    if mode not in VALID_MODES:
        print("Usage: python main.py --mode [analyze|trade]")
        print("  analyze  — read-only portfolio analysis")
        print("  trade    — intraday trading bot")
        sys.exit(1)

    if mode == "analyze":
        runner = PortfolioAnalyser(Config)
        runner.run()

    elif mode == "trade":
        runner = PortfolioManager(Config)
        runner.run()


if __name__ == "__main__":
    main()
