#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/.orchestrator/logs"
LOG_FILE="${LOG_DIR}/cloudflared-dashboard.log"
URL_FILE="${LOG_DIR}/cloudflared-dashboard.url"
LOCAL_URL="${PANTHEON_DASHBOARD_TUNNEL_TARGET:-http://127.0.0.1:80}"
PUBLIC_PATH="${PANTHEON_DASHBOARD_TUNNEL_PUBLIC_PATH:-/dashboard/}"
RESTART_DELAY="${PANTHEON_DASHBOARD_TUNNEL_RESTART_DELAY_SECONDS:-2}"

mkdir -p "${LOG_DIR}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed." >&2
  exit 1
fi

while true; do
  rm -f "${URL_FILE}"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] starting quick tunnel to ${LOCAL_URL}" | tee -a "${LOG_FILE}"
  cloudflared tunnel --no-autoupdate --url "${LOCAL_URL}" 2>&1 | tee -a "${LOG_FILE}" >(
    python3 -c '
import re
import sys
from pathlib import Path

url_path = Path(sys.argv[1])
public_path = "/" + sys.argv[2].strip("/") + "/"

for line in sys.stdin:
    match = re.search(r"https://[-a-z0-9]+\.trycloudflare\.com", line)
    if match:
        url_path.write_text(match.group(0).rstrip("/") + public_path, encoding="utf-8")
' "${URL_FILE}" "${PUBLIC_PATH}"
  )
  status=${PIPESTATUS[0]}
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] quick tunnel exited with status ${status}; restarting in ${RESTART_DELAY}s" | tee -a "${LOG_FILE}"
  sleep "${RESTART_DELAY}"
done
