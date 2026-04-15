#!/usr/bin/env bash
set -euo pipefail

HOST="${PANTHEON_REMOTE_HOST:-pantheon-gcp}"
REMOTE_PATH="${PANTHEON_REMOTE_PATH:-/home/edna/code/pantheon}"
SUPERVISOR_SESSION="${PANTHEON_REMOTE_SUPERVISOR_SESSION:-pantheon-supervisor}"
ACTION="${1:-status}"

remote_exec() {
  ssh "${HOST}" \
    REMOTE_PATH="${REMOTE_PATH}" \
    SUPERVISOR_SESSION="${SUPERVISOR_SESSION}" \
    'bash -s' -- "$ACTION" <<'EOF'
set -euo pipefail

ACTION="${1:-status}"

find_repo_writer_pids() {
  python3 - "$REMOTE_PATH" <<'PY'
import re
import subprocess
import sys

repo = sys.argv[1]
patterns = [
    r"supervisor\.py",
    r"watch_events\.py",
    r"approval_queue\.py",
    r"dashboard_server\.py",
    r"launch-docs-site\.sh",
    r"\bcodex\b.*\bexec\b",
    r"\bclaude\b.*\s-p\b",
    r"\bgemini\b",
    r"\bcopilot\b.*--autopilot",
    r"\bqwen\b.*\s-p\b",
]

lines = subprocess.check_output(["ps", "-eo", "pid=,args="], text=True).splitlines()
for raw in lines:
    line = raw.strip()
    if not line:
        continue
    pid, *rest = line.split(maxsplit=1)
    args = rest[0] if rest else ""
    if repo not in args:
        continue
    if any(re.search(pattern, args) for pattern in patterns):
        print(pid)
PY
}

print_repo_writer_status() {
  printf '%s\n' '---REMOTE-WRITERS---'
  python3 - "$REMOTE_PATH" <<'PY'
import re
import subprocess
import sys

repo = sys.argv[1]
patterns = [
    r"supervisor\.py",
    r"watch_events\.py",
    r"approval_queue\.py",
    r"dashboard_server\.py",
    r"launch-docs-site\.sh",
    r"\bcodex\b.*\bexec\b",
    r"\bclaude\b.*\s-p\b",
    r"\bgemini\b",
    r"\bcopilot\b.*--autopilot",
    r"\bqwen\b.*\s-p\b",
]

lines = subprocess.check_output(["ps", "-eo", "pid=,args="], text=True).splitlines()
matches = []
for raw in lines:
    line = raw.strip()
    if not line:
        continue
    pid, *rest = line.split(maxsplit=1)
    args = rest[0] if rest else ""
    if repo not in args:
        continue
    if any(re.search(pattern, args) for pattern in patterns):
        matches.append((pid, args))

if not matches:
    print("(none)")
else:
    for pid, args in matches:
        print(f"{pid}\t{args}")
PY
  printf '\n%s\n' '---TMUX---'
  if tmux has-session -t "${SUPERVISOR_SESSION}" 2>/dev/null; then
    tmux list-sessions | rg "^${SUPERVISOR_SESSION}:" || true
  else
    echo "(none)"
  fi
}

stop_repo_writers() {
  if tmux has-session -t "${SUPERVISOR_SESSION}" 2>/dev/null; then
    tmux kill-session -t "${SUPERVISOR_SESSION}"
  fi

  mapfile -t pids < <(find_repo_writer_pids)
  if [[ "${#pids[@]}" -gt 0 ]]; then
    kill "${pids[@]}" 2>/dev/null || true
    sleep 2
    mapfile -t still_running < <(find_repo_writer_pids)
    if [[ "${#still_running[@]}" -gt 0 ]]; then
      kill -9 "${still_running[@]}" 2>/dev/null || true
    fi
  fi

  find "${REMOTE_PATH}/.orchestrator" -maxdepth 1 \( -name '*.pid' -o -name '*.lock' \) -delete 2>/dev/null || true
  print_repo_writer_status
}

start_supervisor() {
  mkdir -p "${REMOTE_PATH}/.orchestrator/logs"
  if tmux has-session -t "${SUPERVISOR_SESSION}" 2>/dev/null; then
    echo "Supervisor session already running: ${SUPERVISOR_SESSION}"
    exit 0
  fi
  tmux new-session -d -s "${SUPERVISOR_SESSION}" \
    "cd '${REMOTE_PATH}' && mkdir -p .orchestrator/logs && bash scripts/run-supervisor.sh --verbose 2>&1 | tee -a .orchestrator/logs/remote-supervisor-console.log"
  sleep 2
  print_repo_writer_status
}

tail_supervisor() {
  if tmux has-session -t "${SUPERVISOR_SESSION}" 2>/dev/null; then
    tmux capture-pane -pt "${SUPERVISOR_SESSION}" -S -120
  else
    tail -n 120 "${REMOTE_PATH}/.orchestrator/logs/remote-supervisor-console.log"
  fi
}

case "${ACTION}" in
  status)
    print_repo_writer_status
    ;;
  stop|stop-writers)
    stop_repo_writers
    ;;
  start|start-supervisor)
    start_supervisor
    ;;
  restart)
    stop_repo_writers
    start_supervisor
    ;;
  logs|tail)
    tail_supervisor
    ;;
  *)
    echo "Usage: $0 [status|stop|stop-writers|start|start-supervisor|restart|logs|tail]" >&2
    exit 1
    ;;
esac
EOF
}

remote_exec
