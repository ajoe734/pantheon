#!/usr/bin/env bash
# Bootstrap the supervisor runtime layout on a fresh machine.
#
# Supervisor Authority V2 never launches from the human checkout: it launches
# from an immutable clean command root, against a live config whose task-state
# event log lives outside every Git root. Historically that layout was created
# by hand on one machine and existed nowhere else, so losing the machine lost
# the ability to start the control plane at all. This script rebuilds the
# layout from the repository alone.
#
# It is idempotent: existing keys, worktrees, and runtime files are reused
# rather than regenerated, so it is safe to re-run after a failed attempt.
#
# Usage:
#   scripts/bootstrap-orchestrator-runtime.sh [--dry-run]
#
# Environment overrides:
#   PANTHEON_DEPLOY_ROOT   deployment layout parent (default ~/pantheon-ci-deploy)
#   PANTHEON_STATUS_ROOT   control-plane state owner (default: this checkout)
#   BOOTSTRAP_ORCHESTRATOR_STOP_AFTER_KEYPAIR   test-only seam: exit 0 right
#     after the dev-bridge keypair phase, before touching git worktrees or the
#     supervisor promote/watchdog/health chain. Never set this on a real host.
set -euo pipefail

DRY_RUN=0
for argument in "$@"; do
  case "$argument" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown argument: $argument" >&2; exit 2 ;;
  esac
done

STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}"
DEPLOY_ROOT="${PANTHEON_DEPLOY_ROOT:-$HOME/pantheon-ci-deploy}"
RUNTIME_DIR="$DEPLOY_ROOT/runtime"
COMMAND_RUNTIME_PARENT="$DEPLOY_ROOT/command-runtimes"
LIVE_CONFIG="$RUNTIME_DIR/live-supervisor-mainroot-config.json"
AUTHORITY_ENV_FILE="$RUNTIME_DIR/supervisor-authority-public.env"
SIGNER_ENV_FILE="$RUNTIME_DIR/dev-bridge-signing-private.env"
KEY_ID="assistant-bridge-dev"

# promote/prune resolve the deployment layout from this variable, so a rebuilt
# host owns the layout under its own home instead of the original operator path.
export PANTHEON_DEPLOY_ROOT="$DEPLOY_ROOT"

log() { echo "[bootstrap $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  would run: $*"
  else
    "$@"
  fi
}

cd "$STATUS_ROOT"
if [[ ! -f ai-status.json || ! -d .orchestrator ]]; then
  log "FATAL: status root is not a Pantheon checkout: $STATUS_ROOT"
  exit 1
fi
if [[ -n "$(git -C "$STATUS_ROOT" status --porcelain 2>/dev/null || true)" ]]; then
  log "FATAL: status root has uncommitted changes; commit or clean it before sealing a runtime"
  exit 1
fi

# ---------------------------------------------------------------------------
# 0. Host preflight
#
# Promotion seals the command runtime read-only before it probes the worker
# sandbox, so a missing sandbox is discovered late and leaves a sealed tree
# behind. Check the host requirements first and fail with the exact remedy.
# ---------------------------------------------------------------------------
if ! command -v bwrap >/dev/null 2>&1; then
  log "FATAL: bubblewrap is required for the worker command runtime"
  log "  sudo apt-get install -y bubblewrap"
  exit 1
fi
if ! bwrap --ro-bind / / --dev /dev --unshare-user --unshare-pid true >/dev/null 2>&1; then
  log "FATAL: bwrap cannot create a user namespace on this host"
  log "  Ubuntu 24.04+ restricts unprivileged user namespaces by default."
  log "  Grant the capability to bwrap alone, keeping the system-wide restriction:"
  log "    sudo tee /etc/apparmor.d/bwrap >/dev/null <<'PROFILE'"
  log "    abi <abi/4.0>,"
  log "    include <tunables/global>"
  log "    profile bwrap /usr/bin/bwrap flags=(unconfined) {"
  log "      userns,"
  log "      include if exists <local/bwrap>"
  log "    }"
  log "    PROFILE"
  log "    sudo apparmor_parser -r /etc/apparmor.d/bwrap"
  exit 1
fi

# ---------------------------------------------------------------------------
# 1. Deployment layout
# ---------------------------------------------------------------------------
log "deployment root: $DEPLOY_ROOT"
run mkdir -p "$RUNTIME_DIR" "$COMMAND_RUNTIME_PARENT"
run chmod 700 "$DEPLOY_ROOT" "$RUNTIME_DIR" "$COMMAND_RUNTIME_PARENT"

# ---------------------------------------------------------------------------
# 2. Dev bridge signing keys
#
# The keypair is a purely local trust boundary: local tooling signs dev task
# packets and the local dev-bridge inbox verifies them. Product BFF processes
# receive neither the private key nor the signer module, so a fresh machine may
# mint a fresh keypair. The private half is written to its own file and is
# never placed in the supervisor's authority environment.
# ---------------------------------------------------------------------------
if [[ -f "$AUTHORITY_ENV_FILE" && -f "$SIGNER_ENV_FILE" ]]; then
  log "keypair already present, keeping existing key: $AUTHORITY_ENV_FILE"
elif [[ -f "$AUTHORITY_ENV_FILE" || -f "$SIGNER_ENV_FILE" ]]; then
  log "FATAL: mismatched dev-bridge keypair state; a prior bootstrap was interrupted mid-write"
  [[ -f "$AUTHORITY_ENV_FILE" ]] && log "  present: $AUTHORITY_ENV_FILE"
  [[ -f "$SIGNER_ENV_FILE" ]] && log "  present: $SIGNER_ENV_FILE"
  log "  the two files are minted together and cannot be reconciled from a single half"
  log "  repair: remove both files below, then re-run this script to mint a fresh pair"
  log "    rm -f '$AUTHORITY_ENV_FILE' '$SIGNER_ENV_FILE'"
  exit 1
elif [[ $DRY_RUN -eq 1 ]]; then
  echo "  would generate Ed25519 keypair for key id $KEY_ID"
  echo "  would write $AUTHORITY_ENV_FILE (mode 600)"
  echo "  would write $SIGNER_ENV_FILE (mode 600)"
else
  log "generating Ed25519 keypair for key id $KEY_ID"
  python3 - "$AUTHORITY_ENV_FILE" "$SIGNER_ENV_FILE" "$KEY_ID" <<'PYTHON'
import base64
import json
import os
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

authority_path, signer_path, key_id = sys.argv[1:4]

private_key = Ed25519PrivateKey.generate()
private_raw = private_key.private_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PrivateFormat.Raw,
    encryption_algorithm=serialization.NoEncryption(),
)
public_raw = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
public_map = json.dumps(
    {key_id: base64.urlsafe_b64encode(public_raw).decode().rstrip("=")},
    sort_keys=True,
    separators=(",", ":"),
)


def write_600(path: str, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


# Only the public verifier map may cross into the supervisor environment.
write_600(authority_path, f"BRIDGE_SIGNING_PUBLIC_KEYS_JSON='{public_map}'\n")
write_600(
    signer_path,
    "# Local dev-bridge signing identity. Never source this into the supervisor.\n"
    f"BRIDGE_SIGNING_KEY_ID='{key_id}'\n"
    f"BRIDGE_SIGNING_PRIVATE_KEY='{private_raw.hex()}'\n"
    f"BRIDGE_SIGNING_PUBLIC_KEYS_JSON='{public_map}'\n",
)
PYTHON
  log "wrote $AUTHORITY_ENV_FILE and $SIGNER_ENV_FILE"
fi

if [[ "${BOOTSTRAP_ORCHESTRATOR_STOP_AFTER_KEYPAIR:-0}" -eq 1 ]]; then
  log "stopping after keypair phase (BOOTSTRAP_ORCHESTRATOR_STOP_AFTER_KEYPAIR=1)"
  exit 0
fi

# ---------------------------------------------------------------------------
# 3. Immutable command root
#
# The promoted supervisor must launch from an exact, clean tree. A detached
# worktree at the current commit gives that without duplicating Git objects.
# ---------------------------------------------------------------------------
COMMAND_SHA="$(git -C "$STATUS_ROOT" rev-parse HEAD)"
COMMAND_ROOT="$COMMAND_RUNTIME_PARENT/$COMMAND_SHA"
if [[ -d "$COMMAND_ROOT" ]]; then
  log "command root already materialized: $COMMAND_ROOT"
else
  log "materializing command root at $COMMAND_SHA"
  run git -C "$STATUS_ROOT" worktree add --detach "$COMMAND_ROOT" "$COMMAND_SHA"
fi

# ---------------------------------------------------------------------------
# 4. Promote, persist, verify
# ---------------------------------------------------------------------------
log "promoting supervisor runtime"
run python3 "$STATUS_ROOT/scripts/promote_supervisor_runtime.py" \
  --repo "$COMMAND_ROOT" \
  --status-root "$STATUS_ROOT" \
  --live-config "$LIVE_CONFIG" \
  --authority-env-file "$AUTHORITY_ENV_FILE" \
  --promote \
  --json

log "installing supervisor watchdog"
run python3 "$STATUS_ROOT/scripts/supervisor_watchdog_install.py" \
  --repo "$COMMAND_ROOT" \
  --config "$LIVE_CONFIG" \
  --authority-env-file "$AUTHORITY_ENV_FILE" \
  --method auto \
  --start-now

# A user systemd unit is stopped at logout unless lingering is enabled, which
# would silently end the control plane when the operator disconnects.
if [[ $DRY_RUN -eq 0 ]] && command -v loginctl >/dev/null 2>&1; then
  if [[ "$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || echo no)" != "yes" ]]; then
    log "NOTE: systemd lingering is off; run 'sudo loginctl enable-linger $USER' to survive logout"
  fi
fi

# The identity dimension compares the running process against the command
# runtime it should have launched from, so it must be pointed at the command
# root and the live config. Defaulting to the human checkout silently reports
# every identity check as failed.
log "verifying runtime health"
run python3 "$STATUS_ROOT/scripts/supervisor_runtime_health.py" \
  --repo "$COMMAND_ROOT" \
  --config-path "$LIVE_CONFIG" \
  --require-watchdog \
  --json
