#!/usr/bin/env bash
set -euo pipefail

echo "Probing Codex readiness..."
curl -s http://localhost:8104/api/openclaw-adapter/assistant/readiness/codex | jq .

echo "Probing Claude readiness..."
curl -s http://localhost:8104/api/openclaw-adapter/assistant/readiness/claude | jq .
