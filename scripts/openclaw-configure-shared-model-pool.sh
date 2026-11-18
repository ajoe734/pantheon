#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="${PANTHEON_DEV_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
COMPOSE_PROJECT="${PANTHEON_COMPOSE_PROJECT:-pantheon}"
COMPOSE_FILE="${PANTHEON_COMPOSE_FILE:-docker-compose.yml}"

# Keep model registration separate from credentials. Auth remains in the
# persistent OpenClaw agent store and is never embedded in repo configuration.
# No model is named here: the ordered model chain (first entry = primary) comes
# from OPENCLAW_MODEL_CHAIN, else the one shipped default in services/persona/runtime_profile.py, and is written by
# the chain step further down after it is validated against the gateway.
MODEL_POOL_BATCH='[
  {"path":"plugins.entries.codex.enabled","value":true},
  {"path":"plugins.entries.google.enabled","value":true},
  {"path":"gateway.http.endpoints.responses.enabled","value":true}
]'

# Exit status when the requested chain was refused and the previous chain kept.
# Everything else still completes; the deploy treats this status as advisory.
CHAIN_REFUSED_EXIT=3

# OpenClaw 2026.7.1 clears ambient Claude credentials before spawning its CLI.
# Bind only the explicitly provisioned product token, using an unresolved env
# reference on disk. Do not disable clearEnv or copy a native CLI login. The
# backend command is required by this pinned release's override schema.
CLAUDE_TOKEN_BATCH='[
  {"path":"agents.defaults.cliBackends[\"claude-cli\"].command","value":"claude"},
  {"path":"agents.defaults.cliBackends[\"claude-cli\"].env.CLAUDE_CODE_OAUTH_TOKEN","value":"${CLAUDE_CODE_OAUTH_TOKEN}"}
]'

# OpenClaw 2026.7.1 replaces (does not merge) args/resumeArgs from this
# override, so both lists repeat every registered claude-cli default and add
# --tools ToolSearch: ToolSearch is the only Claude Code built-in tool for every
# agent, while OpenClaw MCP tools stay. Applied with or without a token.
CLAUDE_TOOLSEARCH_BATCH='[
  {"path":"agents.defaults.cliBackends[\"claude-cli\"].command","value":"claude"},
  {"path":"agents.defaults.cliBackends[\"claude-cli\"].args","value":["-p","--output-format","stream-json","--include-partial-messages","--verbose","--setting-sources","user","--allowedTools","mcp__openclaw__*","--disallowedTools","ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor","--tools","ToolSearch"]},
  {"path":"agents.defaults.cliBackends[\"claude-cli\"].resumeArgs","value":["-p","--output-format","stream-json","--include-partial-messages","--verbose","--setting-sources","user","--allowedTools","mcp__openclaw__*","--disallowedTools","ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor","--tools","ToolSearch","--resume","{sessionId}"]}
]'

cd "$REPO_ROOT"

compose() {
  COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-openclaw}" \
    docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" "$@"
}

openclaw() {
  compose exec -T -u node openclaw-gateway node dist/index.js "$@"
}

parse_chain() {
  tr -s ', \t\r\n' '\n' \
    | jq -R -s -c 'split("\n") | map(select(length > 0)) | reduce .[] as $m ([]; if index($m) then . else . + [$m] end)'
}

default_chain="$(PYTHONPATH="$REPO_ROOT" python3 -m services.persona.runtime_profile default-chain | parse_chain)"
requested_raw="${OPENCLAW_MODEL_CHAIN:-}"
if [[ -n "${requested_raw//[[:space:],]/}" ]]; then
  requested_chain="$(printf '%s' "$requested_raw" | parse_chain)"
  chain_source=OPENCLAW_MODEL_CHAIN
else
  requested_chain="$default_chain"
  chain_source=default
fi

config_json_or_empty() {
  openclaw config get "$1" --json 2>/dev/null || printf '%s' '{}'
}
live_models="$(config_json_or_empty 'agents.defaults.models')"
live_providers="$(config_json_or_empty 'models.providers')"
live_cli_backends="$(config_json_or_empty 'agents.defaults.cliBackends')"
live_model="$(openclaw config get agents.defaults.model --json 2>/dev/null || printf '%s' 'null')"

# Refuse (named error, previous chain kept) a chain that is empty, malformed,
# names a CLI-runtime model (persona tool calls lack host identity there) or
# names a ref the gateway has not registered. A ref is registered when it is in
# agents.defaults.models, in a models.providers.<p>.models catalog, or is part
# of the repo-shipped default chain.
chain_errors="$(jq -n -c \
  --argjson chain "$requested_chain" --argjson models "$live_models" \
  --argjson providers "$live_providers" --argjson cli "$live_cli_backends" \
  --argjson default "$default_chain" '
  def prov: split("/")[0];
  def mid: (split("/")[1:] | join("/"));
  if ($chain | length) == 0 then [{"error":"chain_empty"}]
  else [ $chain[] as $r
    | if ($r | test("^[^/]+/.+$") | not) then {"ref":$r,"error":"chain_model_ref_malformed"}
      elif ((($models[$r].agentRuntime.id? // "openclaw") != "openclaw") or (($cli | type) == "object" and ($cli | has($r | prov)))) then {"ref":$r,"error":"chain_model_cli_runtime"}
      elif (($models | type) == "object" and ($models | has($r)))
        or ((($providers[$r | prov].models? // []) | map(.id? // empty) | index($r | mid)) != null)
        or ($default | index($r)) != null then empty
      else {"ref":$r,"error":"chain_model_not_registered"} end ]
  end')"
chain_refused=false
if [[ "$(jq 'length' <<<"$chain_errors")" -ne 0 ]]; then
  chain_refused=true
  printf 'OPENCLAW_MODEL_CHAIN_REFUSED source=%s errors=%s\n' "$chain_source" "$chain_errors" >&2
  effective_chain="$(jq -c 'if type == "object" then ([.primary] + (.fallbacks // [])) elif type == "string" then [.] else [] end | map(select(. != null and . != ""))' <<<"$live_model")"
  if [[ "$(jq 'length' <<<"$effective_chain")" -eq 0 ]]; then
    # Never leave the gateway without a chain.
    effective_chain="$default_chain"
    printf 'No previous chain on the gateway; applying the shipped default chain.\n' >&2
  else
    printf 'Keeping the previous gateway chain: %s\n' "$effective_chain" >&2
  fi
else
  effective_chain="$requested_chain"
fi
# Write only when applying a chain (accepted, or no previous chain to keep).
write_chain=true
if [[ "$chain_refused" == true && "$effective_chain" != "$default_chain" ]]; then
  write_chain=false
fi
chain_batch="$(jq -n -c --argjson c "$effective_chain" --argjson models "$live_models" '
  [{"path":"agents.defaults.model.primary","value":$c[0]},
   {"path":"agents.defaults.model.fallbacks","value":$c[1:]}]
  + [ $c[] as $r
      | if (($models | type) == "object" and ($models | has($r)))
        then {"path":("agents.defaults.models[\"" + $r + "\"].agentRuntime"),"value":{"id":"openclaw"}}
        else {"path":("agents.defaults.models[\"" + $r + "\"]"),"value":{"agentRuntime":{"id":"openclaw"}}} end ]')"

openclaw config set --batch-json "$MODEL_POOL_BATCH"
if [[ "$write_chain" == true ]]; then
  openclaw config set --batch-json "$chain_batch" >/dev/null
fi
openclaw config set --batch-json "$CLAUDE_TOOLSEARCH_BATCH" >/dev/null
# Persist the non-secret Anthropic workspace header as a literal value (never
# an unresolved env-var reference) so the shared openclaw-data volume remains
# valid if rolled back to an incumbent compose definition that does not export
# ANTHROPIC_WORKSPACE_ID. When the env var is absent, preserve any existing
# persisted literal header untouched.
compose exec -T -u node openclaw-gateway node -e '
  const { execFileSync } = require("child_process");
  const ws = process.env.ANTHROPIC_WORKSPACE_ID?.trim();
  if (!ws) process.exit(0);
  const env = { ...process.env };
  delete env.ANTHROPIC_WORKSPACE_ID;
  execFileSync(
    process.execPath,
    [
      "dist/index.js",
      "config",
      "set",
      "--batch-json",
      JSON.stringify([
        {
          path: "models.providers.anthropic.headers.anthropic-workspace-id",
          value: ws,
        },
      ]),
    ],
    { env, stdio: ["ignore", "ignore", "inherit"] },
  );
'
if compose exec -T -u node openclaw-gateway node -e \
  'process.exit(process.env.CLAUDE_CODE_OAUTH_TOKEN?.trim() ? 0 : 1)'; then
  # The argv contains only the literal reference, never the credential value.
  openclaw config set --batch-json "$CLAUDE_TOKEN_BATCH" >/dev/null
fi
# Dedicated deny-all agents: structured-extraction (adapter STRUCTURED_AGENT_ID)
# and memory-index (adapter memory LLM bridge, Hindsight background model calls).
# Upsert so main and other agents stay unchanged. Fail closed: a read/shape
# error must never replace the whole registry.
# Both agents carry a copy of the one chain (primary + fallbacks) so they follow
# it whether or not an agent without its own fallbacks inherits the defaults.
agents_cfg="$(openclaw config get agents --json)"
agents_list="$(jq -ce --argjson chain "$effective_chain" \
  '(if has("list") then .list else [] end) | if type == "array" then . else error("agents.list is not an array") end
  | (if any(.[]; .id == "structured-extraction") then . else . + [{"id":"structured-extraction"}] end)
  | (if any(.[]; .id == "memory-index") then . else . + [{"id":"memory-index"}] end)
  | map(if .id == "structured-extraction" then
      .tools = ((.tools // {}) + {"deny":["*"]})
      | .model = {"primary": $chain[0], "fallbacks": $chain[1:]}
    else . end)
  | map(if .id == "memory-index" then
      .tools = ((.tools // {}) + {"deny":["*"]})
      | .model = {"primary": $chain[0], "fallbacks": $chain[1:]}
    else . end)
  | map(if (.tools.deny? // null) == ["*"] then .tools.exec = {"security":"deny","ask":"always"} else . end)' <<<"$agents_cfg")"
openclaw config set agents.list "$agents_list" --strict-json --replace >/dev/null
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
jq -e --argjson chain "$effective_chain" '. == $chain[0]' <<<"$primary" >/dev/null
jq -e --argjson chain "$effective_chain" '. == $chain[1:]' <<<"$fallbacks" >/dev/null
jq -e '. == true' <<<"$responses_enabled" >/dev/null
openclaw config get agents.list --json \
  | jq -e 'map(select(.id == "structured-extraction")) | length == 1 and .[0].tools.deny == ["*"] and .[0].tools.exec.security == "deny"' >/dev/null
openclaw config get agents.list --json \
  | jq -e 'map(select(.id == "memory-index")) | length == 1 and .[0].tools.deny == ["*"] and .[0].tools.exec.security == "deny" and .[0].tools.exec.ask == "always"' >/dev/null
# memory-index and structured-extraction carry exactly the effective chain, and
# every chain ref is registered with the gateway-native runtime.
openclaw config get agents.list --json \
  | jq -e --argjson chain "$effective_chain" '
      map(select(.id == "memory-index" or .id == "structured-extraction"))
      | length == 2 and all(.[]; .model == {"primary": $chain[0], "fallbacks": $chain[1:]})' >/dev/null
if [[ "$write_chain" == true ]]; then
  jq -e --argjson chain "$effective_chain" \
    '. as $m | $chain | all(.[]; . as $r | ($m | has($r)) and $m[$r].agentRuntime.id == "openclaw")' \
    <<<"$configured" >/dev/null
fi

openclaw --version
printf 'Configured OpenClaw model chain (%s): %s\n' "$chain_source" "$effective_chain"
printf 'Configured OpenClaw shared model pool and enabled the OpenResponses HTTP endpoint.\n'
if [[ "$chain_refused" == true ]]; then
  printf 'OPENCLAW_MODEL_CHAIN_REFUSED advisory: previous chain kept.\n' >&2
  exit "$CHAIN_REFUSED_EXIT"
fi
