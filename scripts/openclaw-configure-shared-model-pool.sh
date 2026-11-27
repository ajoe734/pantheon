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
  {"path":"gateway.http.endpoints.responses.enabled","value":true},
  {"path":"agents.defaults.skipBootstrap","value":true}
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

# Standard input is detached for every compose call: docker compose exec -T
# still forwards stdin, so inside a `while read` loop it swallows the remaining
# lines (the second API route was never registered). Nothing here pipes into it.
compose() {
  COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-openclaw}" \
    docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" "$@" </dev/null
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

# OpenAI API route (per-token billing): one provider per key, each reading its
# key through an env SecretRef (container variables OPENCLAW_API_ROUTE_KEY_1/_2,
# never the ambient OpenAI/Codex key names). A key that is missing or empty drops its
# provider and its refs from the shipped default chain, so the gateway still
# starts and the other routes keep working. No key value is read or written here.
api_routes="$(PYTHONPATH="$REPO_ROOT" python3 -m services.persona.runtime_profile api-routes)"
present_routes='[]'
while IFS= read -r route; do
  route_provider="$(jq -r '.provider' <<<"$route")"
  route_env="$(jq -r '.env' <<<"$route")"
  if compose exec -T -u node openclaw-gateway node -e \
    'process.exit(process.env[process.argv[1]]?.trim() ? 0 : 1)' "$route_env"; then
    present_routes="$(jq -c --arg p "$route_provider" '. + [$p]' <<<"$present_routes")"
    route_models="$( (printf '%s\n' "$requested_chain" "$default_chain") | jq -s -c --arg p "$route_provider" \
      '[.[][] | select(startswith($p + "/")) | .[($p | length) + 1:]] | unique')"
    route_value="$(PYTHONPATH="$REPO_ROOT" python3 -c '
import json, sys
from services.persona.runtime_profile import api_route_provider_value
print(json.dumps(api_route_provider_value(sys.argv[1], json.loads(sys.argv[2])), separators=(",", ":")))
' "$route_env" "$route_models")"
    openclaw config set --batch-json "$(jq -n -c --arg p "models.providers.$route_provider" --argjson v "$route_value" '[{"path":$p,"value":$v}]')" >/dev/null
  else
    printf 'OpenAI API route %s: %s is empty; provider not registered.\n' "$route_provider" "$route_env" >&2
    openclaw config unset "models.providers.$route_provider" >/dev/null 2>&1 || true
  fi
done < <(jq -c '.[]' <<<"$api_routes")
default_chain="$(jq -c --argjson p "$present_routes" --argjson r "$api_routes" \
  '($r | map(select(.provider as $x | ($p | index($x)) | not) | .provider + "/")) as $absent
   | map(select(. as $ref | any($absent[]; . as $a | $ref | startswith($a)) | not))' <<<"$default_chain")"
if [[ -z "${requested_raw//[[:space:],]/}" ]]; then
  requested_chain="$default_chain"
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

# Retire OpenClaw stock first-run files (OPENCLAW-AGENT-WORKSPACE-FIRST-RUN-RETIRE-20261127):
# 1. Move stock BOOTSTRAP.md (matched by stock SHA-256 or stock heading, never by
#    file name alone) from every existing workspace (workspace-* and workspaces/*)
#    under the gateway state dir into <stateDir>/retired-stock-bootstrap/<workspace>/
#    with a receipt line per file (workspace, file, sha256, time, archive_path).
# 2. For the two deny-all service agents (workspace-structured-extraction and
#    workspace-memory-index), also retire stock IDENTITY.md, USER.md, SOUL.md,
#    HEARTBEAT.md (and stock AGENTS.md) the same retained way, then install the
#    Pantheon-authored non-interactive AGENTS.md text.
# Non-stock or operator-authored files, Pantheon-rendered SOUL.md and MEMORY.md
# files are never touched, and a second run moves nothing.
structured_agents_b64="$(base64 < "$REPO_ROOT/integrations/openclaw/service_agents/structured-extraction/AGENTS.md" | tr -d '\r\n')"
memory_agents_b64="$(base64 < "$REPO_ROOT/integrations/openclaw/service_agents/memory-index/AGENTS.md" | tr -d '\r\n')"
compose exec -T -u node openclaw-gateway node -e '
  // RETIRE_STOCK_FIRST_RUN
  const crypto = require("crypto");
  const fs = require("fs");
  const os = require("os");
  const path = require("path");

  const structuredAgentsMd = Buffer.from(process.argv[1] || "", "base64").toString("utf8");
  const memoryAgentsMd = Buffer.from(process.argv[2] || "", "base64").toString("utf8");
  if (!structuredAgentsMd.trim() || !memoryAgentsMd.trim()) {
    throw new Error("Pantheon service-agent AGENTS.md payload is empty");
  }

  const stateDir = (process.env.OPENCLAW_STATE_DIR || "").trim() || path.join(os.homedir(), ".openclaw");
  const archiveRoot = path.join(stateDir, "retired-stock-bootstrap");
  const receiptsPath = path.join(archiveRoot, "receipts.jsonl");

  const STOCK_SPECS = {
    "BOOTSTRAP.md": {
      hashes: new Set([
        "204b441f369bb8ae932deefc13835f639a1eae2eb5e6a4facebfe784893b09de",
        "1c85f2aad8c4ace090e714a0ec2dec3c928e54c8d2d20d58175f0ae3963d99b3",
      ]),
      headings: new Set(["# BOOTSTRAP.md - Hello, World"]),
    },
    "IDENTITY.md": {
      hashes: new Set([
        "2f6da67d69bd362f7aaa13c1214dcbba9f0f7cb51ff42ec5313e24d6bf6f9e55",
        "1c447d4ce2d33b4836d3c95c2bc70cc783ea3ccd450e61e2db7e04d5465e9820",
        "8e5a3a0b5caddf451c3c8886f0ef71f127b51305bacacfdc42e6cceb6654176c",
        "83d3590b0325fab4c8d0b3ca781be20ce363e3873ebc03f535eef4129cc96907",
      ]),
      headings: new Set(["# IDENTITY.md - Who Am I?", "# IDENTITY.md - Agent Identity"]),
    },
    "USER.md": {
      hashes: new Set([
        "e418ca9a680553b3ad8f54aecb0d403330810bb77826ab5251cc1dc13368fe16",
        "599bd4d663c852bca679a341d53605c1a48b7cd7601bd7d102ee5407828dbacb",
        "c27d72b87209508f93cc2b553df4047520d4ae44221242b8a496186c6f0b4956",
        "f75b3acf635a7e114dd4b92bd2136137646a929789037ded995f75a9e096eec4",
      ]),
      headings: new Set(["# USER.md - About Your Human", "# USER.md - User Profile"]),
    },
    "SOUL.md": {
      hashes: new Set([
        "29c66a31acce424ed6cf9b1c9e77dcdc5ae79d5fe2a035765c302a34b9025e0e",
        "79c61aaee618c787c164c5d767053a83c6e218191b3b6ebb66fd07320b071ce8",
        "07fd53cd761eeb4040d0d7560114f95e6278a8cb9a6eef816a0d39c3e837a83b",
        "f0511b1e69f3a5b110e277ba60e74ddeba6b83896b8a23b1195f545a89f4959d",
      ]),
      headings: new Set(["# SOUL.md - Who You Are", "# SOUL.md - The Soul of C-3PO"]),
    },
    "HEARTBEAT.md": {
      hashes: new Set([
        "ecce558615751a35aa173731e892ff3993f44bb4f5a1219c0a02994790c85528",
        "3aa50f4d5fe97e4e7bb3bec1512de79ce79d0e6c27dfca167e6ff0b6a3a7b2ba",
        "1605f546995e0bdcb11f9bf905173b14aca25cfad664fe2c7644d18c2b4142e2",
      ]),
      headings: new Set([
        "<!-- Heartbeat template; comments-only content prevents scheduled heartbeat API calls. -->",
        "# Keep this file empty (or with only comments) to skip heartbeat API calls.",
        "# HEARTBEAT.md template",
      ]),
    },
    "AGENTS.md": {
      hashes: new Set([
        "c9a664b73200ae5d6b0da0908de3256cdb4dda8ba6fe99f5e6c5115c3983604c",
        "7d340e13e845b8bf7c69c60f5dbcc7b5b0e03b1401496d2a091af7223499bbfc",
        "23aaee216b39932727528347391db5ab73ee99fe83997b7909f1e410d814fbd4",
        "6cf2ca11dbeae314356f797920814ef654e64f995d599619e6e9bf07cec3b500",
      ]),
      headings: new Set(["# AGENTS.md - Your Workspace", "# AGENTS.md - OpenClaw Workspace"]),
    },
  };

  function stripOptionalFrontmatter(text) {
    if (!text.startsWith("---\n") && !text.startsWith("---\r\n")) return text;
    const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n/.exec(text);
    return match ? text.slice(match[0].length) : text;
  }

  function firstNonEmptyLine(text) {
    const body = stripOptionalFrontmatter(text);
    for (const line of body.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (trimmed) return trimmed;
    }
    return "";
  }

  function isStockTemplate(fileName, content, sha256) {
    const spec = STOCK_SPECS[fileName];
    if (!spec) return false;
    if (fileName === "SOUL.md" && content.includes("# SOUL.md — ")) return false;
    if (spec.hashes.has(sha256)) return true;
    return spec.headings.has(firstNonEmptyLine(content));
  }

  function retireIfStock(workspaceRel, wsDir, fileName) {
    const filePath = path.join(wsDir, fileName);
    let stat;
    try {
      stat = fs.lstatSync(filePath);
    } catch (err) {
      if (err && err.code === "ENOENT") return false;
      throw err;
    }
    if (!stat.isFile()) return false;
    const raw = fs.readFileSync(filePath);
    const content = raw.toString("utf8");
    const sha256 = crypto.createHash("sha256").update(raw).digest("hex");
    if (!isStockTemplate(fileName, content, sha256)) return false;

    const destPath = path.join(archiveRoot, workspaceRel, fileName);
    fs.mkdirSync(path.dirname(destPath), { recursive: true });
    fs.renameSync(filePath, destPath);
    const receipt = {
      workspace: workspaceRel,
      file: fileName,
      sha256,
      time: new Date().toISOString(),
      archive_path: path.relative(stateDir, destPath).split(path.sep).join("/"),
    };
    fs.appendFileSync(receiptsPath, JSON.stringify(receipt) + "\n", "utf8");
    console.log("RETIRED_STOCK_WORKSPACE_FILE " + JSON.stringify(receipt));
    return true;
  }

  fs.mkdirSync(stateDir, { recursive: true });
  const workspaces = [];
  for (const entry of fs.readdirSync(stateDir, { withFileTypes: true })) {
    if (entry.isDirectory() && !entry.isSymbolicLink() && entry.name.startsWith("workspace-")) {
      workspaces.push(entry.name);
    }
  }
  const nestedRoot = path.join(stateDir, "workspaces");
  if (fs.existsSync(nestedRoot) && fs.lstatSync(nestedRoot).isDirectory()) {
    for (const entry of fs.readdirSync(nestedRoot, { withFileTypes: true })) {
      if (entry.isDirectory() && !entry.isSymbolicLink()) {
        workspaces.push("workspaces/" + entry.name);
      }
    }
  }
  workspaces.sort();

  for (const workspaceRel of workspaces) {
    retireIfStock(workspaceRel, path.join(stateDir, workspaceRel), "BOOTSTRAP.md");
  }

  const SERVICE_AGENTS = [
    ["workspace-structured-extraction", structuredAgentsMd],
    ["workspace-memory-index", memoryAgentsMd],
  ];
  for (const [workspaceRel, desiredAgentsMd] of SERVICE_AGENTS) {
    const wsDir = path.join(stateDir, workspaceRel);
    fs.mkdirSync(wsDir, { recursive: true });
    for (const fileName of ["BOOTSTRAP.md", "IDENTITY.md", "USER.md", "SOUL.md", "HEARTBEAT.md", "AGENTS.md"]) {
      retireIfStock(workspaceRel, wsDir, fileName);
    }
    const agentsPath = path.join(wsDir, "AGENTS.md");
    let current = null;
    try {
      current = fs.readFileSync(agentsPath, "utf8");
    } catch (err) {
      if (!err || err.code !== "ENOENT") throw err;
    }
    if (current === null || (current !== desiredAgentsMd && current.startsWith("# AGENTS.md - Pantheon Service Agent"))) {
      fs.writeFileSync(agentsPath, desiredAgentsMd, "utf8");
    }
  }
' "$structured_agents_b64" "$memory_agents_b64"

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
skip_bootstrap="$(openclaw config get agents.defaults.skipBootstrap --json)"
jq -e --argjson chain "$effective_chain" '. == $chain[0]' <<<"$primary" >/dev/null
jq -e --argjson chain "$effective_chain" '. == $chain[1:]' <<<"$fallbacks" >/dev/null
jq -e '. == true' <<<"$responses_enabled" >/dev/null
jq -e '. == true' <<<"$skip_bootstrap" >/dev/null
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
