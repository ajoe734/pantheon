#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="${PANTHEON_DEV_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
COMPOSE_PROJECT="${PANTHEON_COMPOSE_PROJECT:-pantheon}"
COMPOSE_FILE="${PANTHEON_COMPOSE_FILE:-docker-compose.yml}"

# Keep model registration separate from credentials. Auth remains in the
# persistent OpenClaw agent store and is never embedded in repo configuration.
MODEL_POOL_BATCH='[
  {"path":"plugins.entries.codex.enabled","value":true},
  {"path":"plugins.entries.google.enabled","value":true},
  {"path":"gateway.http.endpoints.responses.enabled","value":true},
  {"path":"agents.defaults.model.primary","value":"anthropic/claude-opus-4-8"},
  {"path":"agents.defaults.model.fallbacks","value":["openai/gpt-5.6-sol","openai/gpt-5.5"]},
  {"path":"agents.defaults.models[\"openai/gpt-5.6-sol\"]","value":{"alias":"codex-sol","agentRuntime":{"id":"codex"}}},
  {"path":"agents.defaults.models[\"openai/gpt-5.5\"]","value":{"alias":"codex-5.5","agentRuntime":{"id":"codex"}}},
  {"path":"agents.defaults.models[\"anthropic/claude-opus-4-8\"]","value":{"alias":"opus","agentRuntime":{"id":"claude-cli"}}},
  {"path":"agents.defaults.models[\"anthropic/claude-sonnet-4-6\"]","value":{"alias":"sonnet","agentRuntime":{"id":"claude-cli"}}},
  {"path":"agents.defaults.models[\"google/gemini-3.1-pro-preview\"]","value":{"alias":"gemini","agentRuntime":{"id":"google-gemini-cli"}}}
]'

# OpenClaw 2026.7.1 clears ambient Claude credentials before spawning its CLI.
# Bind only the explicitly provisioned product token, using an unresolved env
# reference on disk. Do not disable clearEnv or copy a native CLI login. The
# backend command is required by this pinned release's override schema.
CLAUDE_TOKEN_BATCH='[
  {"path":"agents.defaults.cliBackends[\"claude-cli\"].command","value":"claude"},
  {"path":"agents.defaults.cliBackends[\"claude-cli\"].env.CLAUDE_CODE_OAUTH_TOKEN","value":"${CLAUDE_CODE_OAUTH_TOKEN}"}
]'

cd "$REPO_ROOT"

compose() {
  COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-openclaw}" \
    docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" "$@"
}

openclaw() {
  compose exec -T -u node openclaw-gateway node dist/index.js "$@"
}

openclaw config set --batch-json "$MODEL_POOL_BATCH"
if compose exec -T -u node openclaw-gateway node -e \
  'process.exit(process.env.CLAUDE_CODE_OAUTH_TOKEN?.trim() ? 0 : 1)'; then
  # The argv contains only the literal reference, never the credential value.
  openclaw config set --batch-json "$CLAUDE_TOKEN_BATCH" >/dev/null
fi
openclaw config validate

# OpenClaw reports that a restart is required after config mutation. Apply it
# here so deployment never advertises routes that only exist on disk.
compose restart openclaw-gateway
ready=false
for _ in $(seq 1 30); do
  if compose exec -T openclaw-gateway \
    curl -fsS --max-time 5 http://127.0.0.1:18789/readyz >/dev/null; then
    ready=true
    break
  fi
  sleep 2
done
if [[ "$ready" != true ]]; then
  printf 'OpenClaw gateway did not become ready after model-pool restart.\n' >&2
  exit 1
fi

configured="$(openclaw config get agents.defaults.models --json)"
primary="$(openclaw config get agents.defaults.model.primary --json)"
fallbacks="$(openclaw config get agents.defaults.model.fallbacks --json)"
responses_enabled="$(openclaw config get gateway.http.endpoints.responses.enabled --json)"
jq -e '. == "anthropic/claude-opus-4-8"' <<<"$primary" >/dev/null
jq -e '. == ["openai/gpt-5.6-sol", "openai/gpt-5.5"]' <<<"$fallbacks" >/dev/null
jq -e '. == true' <<<"$responses_enabled" >/dev/null
for model_ref in \
  openai/gpt-5.6-sol \
  openai/gpt-5.5 \
  anthropic/claude-opus-4-8 \
  anthropic/claude-sonnet-4-6 \
  google/gemini-3.1-pro-preview; do
  jq -e --arg model_ref "$model_ref" 'has($model_ref)' <<<"$configured" >/dev/null
done

openclaw --version
printf 'Configured OpenClaw shared model pool and enabled the OpenResponses HTTP endpoint.\n'
