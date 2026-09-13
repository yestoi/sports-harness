#!/usr/bin/env bash
# Start only on explicit launch. Reminders never launch a model.
set -euo pipefail
cd "$(dirname "$0")/.."
root=$(pwd)
cache="$HOME/.cache/sports-harness"
mkdir -p "$cache/reminders"
case "${1:-status}" in
  start)
    test "$(hostname)" = omarchy || { echo 'Start on Omarchy' >&2; exit 1; }
    if tmux has-session -t sports-autopilot 2>/dev/null; then
      exec tmux attach-session -t sports-autopilot
    fi
    claude_bin=$(mise which claude)
    test -x "$claude_bin"
    exec tmux new-session -s sports-autopilot -c "$root" \
      "flock -n '$cache/controller.lock' '$claude_bin' --dangerously-skip-permissions --autocompact 500k --mcp-config '$root/.mcp.json' --strict-mcp-config"
    ;;
  status)
    tmux list-sessions 2>/dev/null || true
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
  *) echo 'usage: autopilot-session.sh {start|status|notify MESSAGE|reminder SECONDS KEY MESSAGE}' >&2; exit 2;;
esac
