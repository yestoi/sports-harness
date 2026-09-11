## Preflight (once per session; journal the result as a `preflight` entry)

1. `make preflight` (one call: clock, git, Mac sleep, test DB, pytest processes, secrets modes, paper posture, tunnel,
   NAS containers and `/healthz`, disk and memory, deployed stamp versus `main`, game window, tick ages). Save the output
   verbatim to `docs/superpowers/autopilot/evidence/<date>-preflight-<HHMM>.txt`; the paper-posture line goes in the
   journal verbatim and anything but `paper posture intact` is a gate. Fix local plumbing inline (tunnel:
   `ssh -N -L 8180:127.0.0.1:8180 trey@192.168.12.228` with `run_in_background`; sleep: `caffeinate -dims &`;
   `chmod 600 secrets/*`). Anything else is a gate.
2. Tools: one `ToolSearch` for `ScheduleWakeup`, `PushNotification`, `ListAgents`, `CronCreate`, `CronList`, `CronDelete`,
   `Monitor`, `SendMessage` plus the Chrome set (`tabs_context_mcp`, `tabs_create_mcp`, `navigate`, `computer`, `read_page`,
   `get_page_text`, `tabs_close_mcp`); `tabs_context_mcp` must answer. Journal which resolved (R23). Fallbacks: `CronList`
   then `CronCreate` for wakeups; `osascript -e 'display notification "<text>" with title "autopilot"'` for notifications.
   The two test notifications (R3) run on the first session of a calendar day only; `state.md` records the day they ran.
3. Git: `main` with an empty `git status --porcelain`, or a `phaseN-`/`fix-` branch whose ledger explains the state (Unit:
   phase 2a). A dirty tree on `main` is inspected, never discarded: state-file edits are committed, the rest is a gate.
   `git worktree list` shows the implementer worktrees still alive; reconcile each against a ledger and live workers per [recovery.md](recovery.md). Preserve unexplained worktrees;
   remove only completed, accounted-for work under the phase cleanup procedure.
