#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${OPENCLAW_ADAPTER_URL:-http://localhost:8104}"
OPERATOR_ID="${SMOKE_OPERATOR_ID:-smoke-operator}"

echo "=== Probing Codex readiness ==="
curl -s "$BASE_URL/api/openclaw-adapter/assistant/readiness/codex" | jq .

echo ""
echo "=== Probing Claude readiness ==="
CLAUDE_READINESS=$(curl -s "$BASE_URL/api/openclaw-adapter/assistant/readiness/claude")
echo "$CLAUDE_READINESS" | jq .

CLAUDE_READY=$(echo "$CLAUDE_READINESS" | jq -r '.ready')

echo ""
echo "=== Claude provider invoke (tiny non-interactive probe) ==="
if [ "$CLAUDE_READY" = "true" ]; then
    curl -s -X POST "$BASE_URL/api/openclaw-adapter/assistant/claude/invoke" \
        -H "Content-Type: application/json" \
        -H "X-Operator-Id: $OPERATOR_ID" \
        -d '{"prompt": "Say: smoke-ok", "mode": "user"}' | jq .
else
    echo "Claude provider is not ready (expected in CI without auth mount)."
    echo "Invoke smoke skipped — readiness probe showed degraded state."

    # Still probe the invoke endpoint to confirm it returns degraded (not 5xx)
    INVOKE_RESULT=$(curl -s -X POST "$BASE_URL/api/openclaw-adapter/assistant/claude/invoke" \
        -H "Content-Type: application/json" \
        -H "X-Operator-Id: $OPERATOR_ID" \
        -d '{"prompt": "Say: smoke-ok", "mode": "user"}')
    echo "$INVOKE_RESULT" | jq .
    STATUS=$(echo "$INVOKE_RESULT" | jq -r '.status')
    if [ "$STATUS" != "degraded" ] && [ "$STATUS" != "ok" ]; then
        echo "ERROR: unexpected invoke status '$STATUS' — expected 'degraded' or 'ok'"
        exit 1
    fi
    echo "Degraded invoke returned expected status: $STATUS"
fi

echo ""
echo "=== Smoke complete ==="
