#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="${PANTHEON_DASHBOARD_TUNNEL_SESSION:-pantheon-dashboard-tunnel}"
LOG_DIR="${ROOT_DIR}/.orchestrator/logs"
LOG_FILE="${LOG_DIR}/cloudflared-dashboard.log"
URL_FILE="${LOG_DIR}/cloudflared-dashboard.url"
LOCAL_URL="${PANTHEON_DASHBOARD_TUNNEL_TARGET:-http://127.0.0.1:80}"
PUBLIC_PATH="${PANTHEON_DASHBOARD_TUNNEL_PUBLIC_PATH:-/dashboard/}"

mkdir -p "${LOG_DIR}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed." >&2
  echo "Install it first, for example:" >&2
  echo "  curl -fsSL -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb" >&2
  echo "  sudo dpkg -i /tmp/cloudflared.deb" >&2
  exit 1
fi

tmux kill-session -t "${SESSION}" 2>/dev/null || true
: > "${LOG_FILE}"
rm -f "${URL_FILE}"

tmux new-session -d -s "${SESSION}" \
  "cd '${ROOT_DIR}' && cloudflared tunnel --no-autoupdate --url '${LOCAL_URL}' 2>&1 | tee -a '${LOG_FILE}'"

for _ in $(seq 1 30); do
  python3 - "${LOG_FILE}" "${URL_FILE}" "${PUBLIC_PATH}" <<'PY'
import re
import sys
from pathlib import Path

log_path = Path(sys.argv[1])
url_path = Path(sys.argv[2])
public_path = sys.argv[3]

if not log_path.exists():
    raise SystemExit(1)

text = log_path.read_text(encoding="utf-8", errors="ignore")
matches = re.findall(r"https://[-a-z0-9]+\.trycloudflare\.com", text)
if not matches:
    raise SystemExit(1)

base = matches[-1].rstrip("/")
path = "/" + public_path.strip("/") + "/"
url_path.write_text(base + path, encoding="utf-8")
PY
  if [[ -s "${URL_FILE}" ]]; then
    cat "${URL_FILE}"
    exit 0
  fi
  sleep 1
done

echo "Tunnel started, but no trycloudflare URL was captured yet." >&2
echo "Check: tmux capture-pane -pt ${SESSION}" >&2
exit 1
