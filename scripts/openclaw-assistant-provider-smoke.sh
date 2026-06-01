#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${OPENCLAW_GATEWAY_ADAPTER_URL:-http://localhost:8104}"

curl_json() {
  local path="$1"
  curl -fsS "${BASE_URL}${path}" | jq .
}

echo "Listing assistant providers..."
curl_json "/api/openclaw-adapter/assistant/providers"

echo "Probing Codex readiness..."
curl_json "/api/openclaw-adapter/assistant/readiness/codex?auth_probe=true"

echo "Probing Claude readiness..."
curl_json "/api/openclaw-adapter/assistant/readiness/claude"
