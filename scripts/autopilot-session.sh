#!/usr/bin/env bash
# Start only on explicit launch. Reminders never launch a model.
set -euo pipefail
cd "$(dirname "$0")/.."
root=$(pwd)
cache="$HOME/.cache/sports-harness"
mkdir -p "$cache/reminders"

# The controller command: one kernel lock so a second launch (tmux or herdr) cannot
# create a duplicate controller; only the committed worker MCP server; compact at 500k.
controller_cmd() {
  local claude_bin
  claude_bin=$(mise which claude)
  test -x "$claude_bin"
  printf '%s' "flock -n '$cache/controller.lock' '$claude_bin' --dangerously-skip-permissions --autocompact 500k --mcp-config '$root/.mcp.json' --strict-mcp-config"
}

case "${1:-status}" in
  start)
    test "$(hostname)" = omarchy || { echo 'Start on Omarchy' >&2; exit 1; }
    if tmux has-session -t sports-autopilot 2>/dev/null; then
      exec tmux attach-session -t sports-autopilot
    fi
    exec tmux new-session -s sports-autopilot -c "$root" "$(controller_cmd)"
    ;;
  start-herdr)
    # Same controller command without tmux: herdr's server already keeps the pane alive
    # across detach. Run it in a fresh herdr tab or pane at its shell prompt, then
    # `/effort` high and `/autopilot`. Optional, from another pane:
    #   herdr agent rename <pane-id> controller
    # After a herdr *server* restart do not accept the auto-resumed pane (plain
    # `claude --resume`: no lock, no strict MCP config); exit it and run this again.
    test "$(hostname)" = omarchy || { echo 'Start on Omarchy' >&2; exit 1; }
    test "${HERDR_ENV:-}" = 1 || { echo 'Run this inside a herdr pane (run `herdr`, open a new tab)' >&2; exit 1; }
    if tmux has-session -t sports-autopilot 2>/dev/null; then
      echo 'A tmux sports-autopilot session exists; attach it with `start` or end it first' >&2; exit 1
    fi
    if ! flock -n "$cache/controller.lock" true; then
      echo "Controller lock $cache/controller.lock is held: a controller is already running" >&2; exit 1
    fi
    cd "$root"
    eval "exec $(controller_cmd)"
    ;;
  status)
    tmux list-sessions 2>/dev/null || true
    if command -v herdr >/dev/null 2>&1 && herdr status server >/dev/null 2>&1; then
      herdr agent list 2>/dev/null || true
    fi
    if flock -n "$cache/controller.lock" true 2>/dev/null; then
      echo 'controller lock: free'
    else
      echo 'controller lock: held (a controller is running)'
    fi
    find "$cache/reminders" -maxdepth 1 -type f -name '*.txt' -print
    ;;
  notify)
    message=${2:?message required}
    printf '%s\n' "$message"
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
    notify-send 'Sports autopilot' "$message"
    ;;
  reminder)
    delay=${2:?delay seconds required}; key=${3:?numeric key required}; message=${4:?message required}
    [[ "$delay" =~ ^[0-9]+$ && "$key" =~ ^[0-9]+$ ]] || exit 2
    systemd-run --user --unit="sports-reminder-$key" --on-active="${delay}s" \
      "$root/scripts/autopilot-session.sh" deliver "$key" "$message"
    ;;
  deliver)
    key=${2:?key required}; [[ "$key" =~ ^[0-9]+$ ]] || exit 2
    printf '%s %s\n' "$(date -u +%FT%TZ)" "${3:?message required}" > "$cache/reminders/$key.txt"
    "$0" notify "$3"
    ;;
  *) echo 'usage: autopilot-session.sh {start|start-herdr|status|notify MESSAGE|reminder SECONDS KEY MESSAGE}' >&2; exit 2;;
esac
