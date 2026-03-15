# ================================================================
# main.py
# ================================================================
# Entry point. Run this file to start the portfolio manager.
#
# Usage:
#   python main.py            ← runs Phase 1 (analysis only)
#   python main.py --phase 2  ← runs Phase 2 (auto manager, coming soon)
#
# To change plans or budget:
#   Edit config.py — nothing else needs to change.
# ================================================================

import sys
from config              import Config
from portfolio.analyser  import PortfolioAnalyser
from portfolio.manager   import PortfolioManager


def main():
    # Parse optional --phase argument
    phase = 1
    if "--phase" in sys.argv:
        try:
            phase = int(sys.argv[sys.argv.index("--phase") + 1])
        except (IndexError, ValueError):
            print("Usage: python main.py --phase [1|2]")
            sys.exit(1)

    if phase == 1:
        # Phase 1: Read-only portfolio analysis
        runner = PortfolioAnalyser(Config)
        runner.run()

    elif phase == 2:
        # Phase 2: Auto buy/sell manager (coming soon)
        runner = PortfolioManager(Config)
        runner.run()

    else:
        print(f"Unknown phase: {phase}. Use --phase 1 or --phase 2.")
        sys.exit(1)


if __name__ == "__main__":
    main()
