#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_CONF="${ROOT_DIR}/ops/nginx/pantheon-dashboard.conf"
TARGET_CONF="/etc/nginx/sites-available/pantheon-dashboard"
TARGET_LINK="/etc/nginx/sites-enabled/pantheon-dashboard"
DEFAULT_LINK="/etc/nginx/sites-enabled/default"

if [[ ! -f "${SOURCE_CONF}" ]]; then
  echo "Missing source config: ${SOURCE_CONF}" >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y nginx
sudo install -m 0644 "${SOURCE_CONF}" "${TARGET_CONF}"

if [[ -L "${DEFAULT_LINK}" || -f "${DEFAULT_LINK}" ]]; then
  sudo rm -f "${DEFAULT_LINK}"
fi

sudo ln -sf "${TARGET_CONF}" "${TARGET_LINK}"
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx

cat <<'EOF'
Dashboard reverse proxy is installed.

Expected public path:
  http://<host>/dashboard/

The dashboard app itself should stay bound to 127.0.0.1:4173.
If the URL still times out from outside the VM, the remaining blocker is GCP ingress/firewall, not nginx.
EOF
