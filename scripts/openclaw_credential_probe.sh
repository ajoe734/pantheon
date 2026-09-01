#!/usr/bin/env bash
# OPGAP-GATE-HARDENING-20260901 — probe the credential path OpenClaw actually uses.
#
# Why this exists in this shape:
#
# The previous keepalive ran `docker exec <gateway> claude -p ...`, which
# inherits the container environment including CLAUDE_CODE_OAUTH_TOKEN. OpenClaw
# does NOT: it strips that variable before launching any managed Claude CLI run
# (`CLAUDE_CLI_CLEAR_ENV` in its own source, alongside CLAUDE_CONFIG_DIR), so the
# run it performs authenticates from the on-disk credential store instead.
#
# Those are two different credential paths. The on-disk credential expired on
# 2026-08-18 (empty accessToken/refreshToken), so every OpenClaw-managed run
# failed with "OAuth session expired and could not be refreshed" — while the
# keepalive kept logging OK every four hours, because the path it exercised was
# the one that still worked. It was green for two weeks over a dead dependency.
#
# A check that can pass while the thing it guards is dead is worse than no
# check: it manufactures confidence. So the authoritative assertion here runs
# the adapter's own readiness code — the exact thing the deploy gate consumes.
# The raw-CLI warm-up is kept because it does refresh the on-disk credential
# when a live refresh token is present, but it is explicitly non-authoritative
# and can never make this script succeed.
set -euo pipefail

CONTAINER="${OPENCLAW_GATEWAY_CONTAINER:-pantheon-openclaw-gateway-1}"
ADAPTER_CONTAINER="${OPENCLAW_ADAPTER_CONTAINER:-pantheon-openclaw-gateway-adapter-1}"


log() { echo "[openclaw-credential-probe] $*"; }

# Non-authoritative warm-up: refreshes the on-disk credential when it still has
# a usable refresh token. Never allowed to decide the outcome.
if docker exec "${CONTAINER}" claude -p "Reply exactly: OK" --model haiku </dev/null >/dev/null 2>&1; then
  log "raw-CLI warm-up succeeded (advisory only; this is NOT the path OpenClaw uses)"
else
  log "raw-CLI warm-up failed (advisory only)"
fi

# Authoritative: run the adapter's own readiness code, in the adapter's own
# container and environment. This is literally what the deploy gate consumes.
#
# A bare `openclaw agent` call is NOT equivalent and must not be substituted:
# it lets the gateway pick any model from its own fallback chain, so it answers
# via openai/gpt-5.6-sol even while the Claude credential is expired. The
# adapter instead pins each candidate model explicitly and applies its own
# per-candidate budget, which is why the two disagree. A probe that is easier to
# satisfy than the path it guards is the same defect this file exists to prevent.
log "probing the adapter's readiness path (the one the deploy gate consumes)"
if ! probe_output="$(docker exec "${ADAPTER_CONTAINER}" python3 -c '
import json, sys
sys.path.insert(0, "/workspace/services/openclaw-gateway-adapter")
from assistant_openclaw_provider import AssistantOpenClawProvider

info = AssistantOpenClawProvider().readiness(auth_probe=True)
print(json.dumps({
    "ready": info.get("ready"),
    "status": info.get("status"),
    "reason": info.get("reason"),
    "activeModel": info.get("active_model"),
    "primaryModel": info.get("primary_model"),
    "fallbackUsed": info.get("fallback_used", False),
    "answerProbe": info.get("answer_probe"),
}))
sys.exit(0 if info.get("ready") is True else 1)
' 2>&1)"; then
  log "FAILED: adapter readiness is not ready"
  log "${probe_output}"
  exit 1
fi

log "OK: adapter readiness path is healthy"
log "${probe_output}"
