#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${OPENCLAW_GATEWAY_ADAPTER_URL:-${OPENCLAW_ADAPTER_URL:-http://localhost:8104}}"
OPERATOR_ID="${SMOKE_OPERATOR_ID:-smoke-operator}"

curl_json() {
  local path="$1"
  curl -fsS "${BASE_URL}${path}" | jq .
}

post_json_with_status() {
  local path="$1"
  local body="$2"
  local response
  local curl_status

  set +e
  response=$(curl -sS -w "\n%{http_code}" -X POST "${BASE_URL}${path}" \
    -H "Content-Type: application/json" \
    -H "X-Operator-Id: ${OPERATOR_ID}" \
    -d "$body")
  curl_status=$?
  set -e
  if [ "$curl_status" -ne 0 ]; then
    echo "ERROR: curl failed for ${path}"
    exit "$curl_status"
  fi

  POST_JSON_STATUS=$(printf '%s\n' "$response" | tail -n1)
  POST_JSON_BODY=$(printf '%s\n' "$response" | sed '$d')
  printf '%s\n' "$POST_JSON_BODY" | jq .
}

echo "=== Listing assistant providers ==="
curl_json "/api/openclaw-adapter/assistant/providers"

echo ""
echo "=== Adapter liveness no-op ==="
curl_json "/livez"

echo ""
echo "=== Probing Codex readiness ==="
CODEX_READINESS=$(curl -fsS "${BASE_URL}/api/openclaw-adapter/assistant/readiness/codex?auth_probe=true")
echo "$CODEX_READINESS" | jq .
CODEX_READY=$(echo "$CODEX_READINESS" | jq -r '.ready')
CODEX_MOUNT_MODE=$(echo "$CODEX_READINESS" | jq -r '.mount_mode // "unknown"')

if [ "$CODEX_READY" = "true" ] && [ "$CODEX_MOUNT_MODE" = "ro" ]; then
  echo "WARNING: Codex is ready but mount_mode is 'ro'. Automatic credential refresh will fail."
fi

echo ""
echo "=== Codex provider invoke (tiny non-interactive probe) ==="
CODEX_INVOKE_PAYLOAD='{"prompt": "Reply with exactly: smoke-ok", "mode": "user"}'
post_json_with_status "/api/openclaw-adapter/assistant/providers/codex/invoke" "$CODEX_INVOKE_PAYLOAD"
CODEX_STATUS=$(echo "$POST_JSON_BODY" | jq -r '.status')
if [ "$CODEX_READY" = "true" ]; then
  if [ "$POST_JSON_STATUS" != "200" ] || [ "$CODEX_STATUS" != "ok" ]; then
    echo "ERROR: Codex readiness was ready but invoke returned HTTP ${POST_JSON_STATUS} status '${CODEX_STATUS}'"
    exit 1
  fi
  echo "Codex invoke returned expected status: $CODEX_STATUS"
else
  if [ "$CODEX_STATUS" = "ok" ]; then
    echo "Codex invoke succeeded despite degraded readiness."
  elif [ "$CODEX_STATUS" = "provider_error" ]; then
    CODEX_ERROR_CODE=$(echo "$POST_JSON_BODY" | jq -r '.error_code // ""')
    case "$CODEX_ERROR_CODE" in
      CODEX_*)
        echo "Degraded Codex invoke returned expected provider error: $CODEX_ERROR_CODE"
        ;;
      *)
        echo "ERROR: unexpected Codex provider error '$CODEX_ERROR_CODE'"
        exit 1
        ;;
    esac
  else
    echo "ERROR: unexpected Codex invoke status '$CODEX_STATUS'; expected 'ok' or 'provider_error'"
    exit 1
  fi
fi

echo ""
echo "=== Probing Claude readiness ==="
CLAUDE_READINESS=$(curl -fsS "${BASE_URL}/api/openclaw-adapter/assistant/readiness/claude?auth_probe=true")
echo "$CLAUDE_READINESS" | jq .
CLAUDE_READY=$(echo "$CLAUDE_READINESS" | jq -r '.ready')
CLAUDE_MOUNT_MODE=$(echo "$CLAUDE_READINESS" | jq -r '.mount_mode // "unknown"')

if [ "$CLAUDE_READY" = "true" ] && [ "$CLAUDE_MOUNT_MODE" = "ro" ]; then
  echo "WARNING: Claude is ready but mount_mode is 'ro'. Automatic credential refresh will fail."
fi

echo ""
echo "=== Claude provider invoke (tiny non-interactive probe) ==="
CLAUDE_INVOKE_PAYLOAD='{"prompt": "Say: smoke-ok", "mode": "user"}'
if [ "$CLAUDE_READY" = "true" ]; then
  post_json_with_status "/api/openclaw-adapter/assistant/claude/invoke" "$CLAUDE_INVOKE_PAYLOAD"
  CLAUDE_STATUS=$(echo "$POST_JSON_BODY" | jq -r '.status')
  if [ "$POST_JSON_STATUS" != "200" ] || [ "$CLAUDE_STATUS" != "ok" ]; then
    echo "ERROR: Claude readiness was ready but invoke returned HTTP ${POST_JSON_STATUS} status '${CLAUDE_STATUS}'"
    exit 1
  fi
  echo "Claude invoke returned expected status: $CLAUDE_STATUS"
else
  echo "Claude provider is not ready (expected in CI without auth mount)."
  echo "Performing probe anyway to verify degraded behavior..."

  post_json_with_status "/api/openclaw-adapter/assistant/claude/invoke" "$CLAUDE_INVOKE_PAYLOAD"
  CLAUDE_STATUS=$(echo "$POST_JSON_BODY" | jq -r '.status')

  if [ "$CLAUDE_STATUS" = "degraded" ]; then
    CLAUDE_REASON=$(echo "$POST_JSON_BODY" | jq -r '.degraded_reason // ""')
    echo "Claude invoke returned expected degraded status: $CLAUDE_STATUS (reason: $CLAUDE_REASON)"
  elif [ "$CLAUDE_STATUS" = "ok" ]; then
    echo "Claude invoke succeeded despite degraded readiness."
  else
    echo "ERROR: unexpected Claude invoke status '${CLAUDE_STATUS}'; expected 'ok' or 'degraded'"
    exit 1
  fi
fi

echo ""
echo "=== Smoke complete ==="
