#!/usr/bin/env bash
set -euo pipefail

HOST="${PANTHEON_REMOTE_HOST:-pantheon-gcp}"
REMOTE_PATH="${PANTHEON_REMOTE_PATH:-/home/lupin/pantheon}"
ACTION="${1:-status}"

remote_exec() {
  ssh "${HOST}" \
    REMOTE_PATH="${REMOTE_PATH}" \
    'bash -s' -- "$ACTION" <<'EOF'
set -euo pipefail

ACTION="${1:-status}"

print_repo_writer_status() {
  printf '%s\n' '---REMOTE-WRITERS---'
  python3 - "$REMOTE_PATH" <<'PY'
import re
import subprocess
import sys

repo = sys.argv[1]
patterns = [
    r"supervisor\.py",
    r"approval_queue\.py",
    r"dashboard_server\.py",
    r"launch-docs-site\.sh",
    r"\bcodex\b.*\bexec\b",
    r"\bclaude\b.*\s-p\b",
    r"\bgemini\b",
    r"\bcopilot\b.*--autopilot",
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
}

tail_supervisor() {
  tail -n 120 "${REMOTE_PATH}/.orchestrator/logs/supervisor-watchdog-cron.log" 2>/dev/null || true
  find "${REMOTE_PATH}/.orchestrator/logs" -maxdepth 1 -type f \
    -name 'supervisor-watchdog-restart-*.log' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr | head -n 1 | cut -d' ' -f2- | xargs -r tail -n 120
}

case "${ACTION}" in
  status)
    print_repo_writer_status
    ;;
  logs|tail)
    tail_supervisor
    ;;
  *)
    echo "Usage: $0 [status|logs|tail]" >&2
    echo "Supervisor lifecycle is owned by immutable runtime promotion and the watchdog." >&2
    exit 1
    ;;
esac
EOF
}

remote_exec
