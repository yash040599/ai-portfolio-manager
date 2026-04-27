#!/usr/bin/env bash
# ============================================================
# start_trade_vm.sh — one-command daily VM bring-up
# ============================================================
# Replaces the manual sequence:
#   cd ai-portfolio-manager
#   source venv/bin/activate
#   git pull
#   python scripts/backup_data.py --ssh --all-remote   (answer y)
#   python main.py --mode trade --noai --max 50000
#
# Usage (on the VM):
#   ./scripts/start_trade_vm.sh                # defaults: --noai --max 50000
#   ./scripts/start_trade_vm.sh --ai           # forward any extra flag to main.py
#   ./scripts/start_trade_vm.sh --max 30000    # override budget
#
# Any args passed to this script are forwarded verbatim to `main.py --mode trade`,
# replacing the defaults. Pass nothing for the standard run.
# ============================================================

set -euo pipefail

# Resolve project root from this script's location, so it works no matter
# where the user invokes it from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Activate venv (VM convention: ./venv/, dev convention: ./.venv/ — try both).
if   [[ -f venv/bin/activate  ]]; then source venv/bin/activate
elif [[ -f .venv/bin/activate ]]; then source .venv/bin/activate
else
  echo "ERROR: no venv found (looked in ./venv and ./.venv)" >&2
  exit 1
fi

echo "==> git pull"
git pull --ff-only

echo "==> backup_data.py --ssh --all-remote (auto-confirm)"
# Auto-answer 'y' to the destructive-overwrite prompt. Equivalent to typing y.
yes y | python scripts/backup_data.py --ssh --all-remote

echo "==> main.py --mode trade ${*:---noai --max 50000}"
# Default flags if the caller passed none; otherwise forward verbatim.
if (( $# == 0 )); then
  exec python main.py --mode trade --noai --max 50000
else
  exec python main.py --mode trade "$@"
fi
