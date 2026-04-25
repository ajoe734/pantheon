#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONT_REPO="${1:-/home/edna/code/front-ai-trading-system}"
PROFILE_DIR="${LOVABLE_PROFILE_DIR:-$HOME/.cache/pantheon-lovable-trigger/profile}"
STORAGE_STATE="${LOVABLE_STORAGE_STATE:-}"
COOLDOWN_MS="${LOVABLE_COOLDOWN_MS:-45000}"
POINTER_FLAG=()

if [[ "${LOVABLE_POINTER_PROMPT:-0}" == "1" ]]; then
  POINTER_FLAG=(--pointer-prompt)
fi

cd "$SCRIPT_DIR"

if [[ -n "$STORAGE_STATE" ]]; then
  node send_prompt.mjs batch \
    --repo "$FRONT_REPO" \
    --storage-state "$STORAGE_STATE" \
    --cooldown-ms "$COOLDOWN_MS" \
    "${POINTER_FLAG[@]}" \
    --prompt-file "$FRONT_REPO/docs/lovable/2026-04-24-route-live-activation-prompt.md" \
    --prompt-file "$FRONT_REPO/docs/lovable/2026-04-24-reopened-evolution-consultation-realignment-prompt.md" \
    --prompt-file "$FRONT_REPO/docs/lovable/2026-04-24-pkt001-pkt003-followup-prompt.md"
else
  node send_prompt.mjs batch \
    --repo "$FRONT_REPO" \
    --profile-dir "$PROFILE_DIR" \
    --cooldown-ms "$COOLDOWN_MS" \
    "${POINTER_FLAG[@]}" \
    --prompt-file "$FRONT_REPO/docs/lovable/2026-04-24-route-live-activation-prompt.md" \
    --prompt-file "$FRONT_REPO/docs/lovable/2026-04-24-reopened-evolution-consultation-realignment-prompt.md" \
    --prompt-file "$FRONT_REPO/docs/lovable/2026-04-24-pkt001-pkt003-followup-prompt.md"
fi
