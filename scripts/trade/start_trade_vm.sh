#!/usr/bin/env bash
# ============================================================
# start_trade_vm.sh — one-command daily VM bring-up
# ============================================================
# Runs the bot inside a DETACHED tmux session so SSH disconnects do
# NOT kill the bot. (2026-05-12: fixed root cause of the mid-session
# crash — previous version did `exec python` in the SSH foreground,
# so SIGHUP from a dropped SSH connection killed the bot, leaving
# open positions protected only by their exchange-side SL-M.)
#
# Usage (on the VM — from anywhere, including the home directory):
#
#   ./ai-portfolio-manager/scripts/trade/start_trade_vm.sh                  # default: --noai --max 50000
#   ./ai-portfolio-manager/scripts/trade/start_trade_vm.sh --max 30000      # custom args
#   ./ai-portfolio-manager/scripts/trade/start_trade_vm.sh attach           # re-attach to live bot
#                                                                     #   (Ctrl-B then D to detach
#                                                                     #    without killing)
#   ./ai-portfolio-manager/scripts/trade/start_trade_vm.sh status           # is the bot running?
#   ./ai-portfolio-manager/scripts/trade/start_trade_vm.sh logs             # tail today's log file
#   ./ai-portfolio-manager/scripts/trade/start_trade_vm.sh stop             # graceful Ctrl-C then kill
#
# Hard requirement: tmux must be installed. If not:
#     sudo apt install -y tmux       # Debian / Ubuntu
#     sudo dnf install -y tmux       # Fedora / RHEL
# ============================================================

set -euo pipefail

SESSION_NAME="trader"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
SELF_NAME="$(basename "$0")"

# ── Subcommand dispatch ─────────────────────────────────────
case "${1:-start}" in
  attach)
    if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "ERROR: bot session '$SESSION_NAME' is not running."
      echo "Start with: $SELF_NAME"
      exit 1
    fi
    exec tmux attach -t "$SESSION_NAME"
    ;;
  status)
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "✓ Bot session '$SESSION_NAME' is RUNNING."
      echo "  Attach: $SELF_NAME attach   (Ctrl-B then D to detach safely)"
      echo "  --- last 20 lines ---"
      tmux capture-pane -t "$SESSION_NAME" -p | tail -n 20
    else
      echo "✗ Bot session '$SESSION_NAME' is NOT running."
      exit 1
    fi
    exit 0
    ;;
  logs)
    DAILY_LOG="$PROJECT_ROOT/logs/trader_$(date +%Y-%m-%d).log"
    if [[ ! -f "$DAILY_LOG" ]]; then
      echo "No log file for today: $DAILY_LOG"
      exit 1
    fi
    exec tail -F "$DAILY_LOG"
    ;;
  stop)
    if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "No bot session running."
      exit 0
    fi
    echo "Sending Ctrl-C to bot, then waiting 8s for graceful shutdown..."
    tmux send-keys -t "$SESSION_NAME" C-c
    sleep 8
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
    fi
    echo "Bot stopped."
    exit 0
    ;;
  start)
    shift || true
    ;;
  --help|-h)
    sed -n '2,/^# ===/p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    # Unknown first arg → treat as a passthrough flag for main.py
    # (backward compatibility with the old `--max 30000` invocation).
    ;;
esac

# ── Hard requirement: tmux ───────────────────────────────────
if ! command -v tmux >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: tmux is not installed.

tmux keeps the bot ALIVE when your SSH connection drops. Without it,
a momentary network blip will SIGHUP the bot mid-session — leaving any
open MIS positions protected only by their exchange-side SL-M (no
software-side decay/reversal/target/trailing-stop monitoring).

Install:
  sudo apt install -y tmux       # Debian / Ubuntu
  sudo dnf install -y tmux       # Fedora / RHEL

Then re-run: ./scripts/trade/start_trade_vm.sh
EOF
  exit 1
fi

# Refuse to start a second instance.
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "ERROR: bot session '$SESSION_NAME' is already running."
  echo "  Attach: $SELF_NAME attach"
  echo "  Status: $SELF_NAME status"
  echo "  Stop:   $SELF_NAME stop"
  exit 1
fi

cd "$PROJECT_ROOT"

# Pick venv (VM: ./venv/, dev: ./.venv/).
if   [[ -f venv/bin/activate  ]]; then VENV_DIR=venv
elif [[ -f .venv/bin/activate ]]; then VENV_DIR=.venv
else
  echo "ERROR: no venv found (looked in ./venv and ./.venv)" >&2
  exit 1
fi

mkdir -p logs
DAILY_LOG="logs/trader_$(date +%Y-%m-%d).log"

# Build the args we forward to main.py. Default if caller passed nothing.
if (( $# == 0 )); then
  set -- --noai --max 50000
fi
MAIN_ARGS="$*"

# Write the runner sequence to a temp script file. We do NOT inline it
# into `tmux new-session "bash -c \"...\""` because INNER_CMD itself
# contains double quotes (echo "==> [...]") and the nested-quote
# escaping is fragile across bash/tmux versions. A temp script file
# is bulletproof — variable expansion happens once at heredoc time,
# all quoting is local, and we delete the file when bash finishes
# loading it (the in-memory copy keeps running).
RUNNER_SCRIPT="$PROJECT_ROOT/.trader_runner_$$.sh"
cat > "$RUNNER_SCRIPT" <<RUNNER_EOF
#!/usr/bin/env bash
set -u
cd '$PROJECT_ROOT'
source $VENV_DIR/bin/activate
echo "==> [\$(date +%H:%M:%S)] git pull"
if ! git pull --ff-only; then
  echo "!! git pull FAILED — bot will NOT start. Resolve and re-run."
  echo "Press Enter to close this pane."
  read
  exit 1
fi
echo "==> [\$(date +%H:%M:%S)] backup_data.py --ssh --all-remote"
if ! yes y | python scripts/shared/backup_data.py --ssh --all-remote; then
  echo "!! data sync FAILED — bot will NOT start. Resolve and re-run."
  echo "Press Enter to close this pane."
  read
  exit 1
fi
echo "==> [\$(date +%H:%M:%S)] main.py --mode trade $MAIN_ARGS"
set -o pipefail
python main.py --mode trade $MAIN_ARGS 2>&1 | tee -a '$DAILY_LOG'
EC=\${PIPESTATUS[0]}
echo
echo "==> [\$(date +%H:%M:%S)] bot exited with code \$EC"
echo "Press Enter to close this pane (the tmux session will end)."
read
RUNNER_EOF
chmod +x "$RUNNER_SCRIPT"

# Launch detached. The bot now lives inside tmux, parented by the tmux
# server, NOT by this SSH session. SSH can drop without killing it.
# bash loads the runner into memory before we delete the temp file.
tmux new-session -d -s "$SESSION_NAME" "bash '$RUNNER_SCRIPT'; rm -f '$RUNNER_SCRIPT'"

cat <<EOF
✓ Bot started in tmux session '$SESSION_NAME'.

  Live attach :  $SELF_NAME attach    (Ctrl-B then D = detach WITHOUT killing)
  Status      :  $SELF_NAME status
  Tail log    :  $SELF_NAME logs
  Stop        :  $SELF_NAME stop

  Log file    :  $PROJECT_ROOT/$DAILY_LOG
  Args        :  --mode trade $MAIN_ARGS

The bot will SURVIVE SSH disconnects. You can close this terminal safely.
EOF
