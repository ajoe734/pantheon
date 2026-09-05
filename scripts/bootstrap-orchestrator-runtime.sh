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
#     after the dev-bridge keypair phase (which itself runs after the command
#     root worktree and supervisor venv are materialized, since keypair
#     generation needs that venv's cryptography), before the supervisor
#     promote/watchdog/health chain. Never set this on a real host.
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
# 2. Immutable command root
#
# The promoted supervisor must launch from an exact, clean tree. A detached
# worktree at the current commit gives that without duplicating Git objects.
# This must exist before the supervisor Python environment below, which
# installs from this exact command root's own .orchestrator/requirements.txt.
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
# 3. Supervisor Python environment
#
# The supervisor must never launch from the ambient /usr/bin/python3: that
# interpreter has no reason to carry pydantic/cryptography, and losing them
# silently drops packet intake while the heartbeat stays healthy (see
# docs/operations/supervisor-python-runtime.md). This venv is deploy-root
# owned rather than checkout-scoped so it survives command-runtime pruning
# and re-promotion.
#
# It is versioned per exact command SHA rather than kept at one fixed path:
# a fixed path would mean this install (or a failed reinstall) mutates the
# same directory a currently running incumbent supervisor already launched
# from, and a partial/failed install could break that live process before
# any preflight ever runs. A per-SHA directory means an install for a new
# candidate can never touch the directory backing a different, already
# promoted SHA -- the incumbent (and every prior verified environment,
# usable for rollback) is untouched by construction, not by ordering.
#
# This must run before the keypair phase below: keypair generation needs
# ``cryptography``, and on a completely fresh host the ambient interpreter
# has no reason to carry it -- the deploy-root-owned venv must exist and be
# proven first so the keypair step never depends on ambient packages.
#
# Re-running this script (idempotent by design) must not unconditionally
# pip-install into an *existing* per-SHA venv: the exact directory named by
# COMMAND_SHA can already be the one a currently running incumbent
# supervisor launched from, so a re-run reaching this phase (for example
# after an earlier phase failed and the operator re-invokes the script) must
# not mutate a healthy incumbent in place. An existing environment is
# therefore validated read-only first; only a missing or failing environment
# is (re)provisioned, and it is provisioned into an isolated, never-before-
# published directory and preflighted there, then published into the
# per-SHA path with a create-only (no-clobber) rename -- so the per-SHA path
# itself is never opened for writing once it is healthy.
# ---------------------------------------------------------------------------
SUPERVISOR_PYTHON_PARENT="$RUNTIME_DIR/supervisor-python"
SUPERVISOR_PYTHON_DIR="$SUPERVISOR_PYTHON_PARENT/$COMMAND_SHA"
SUPERVISOR_PYTHON="$SUPERVISOR_PYTHON_DIR/bin/python3"
SUPERVISOR_REQUIREMENTS="$COMMAND_ROOT/.orchestrator/requirements.txt"

supervisor_python_verified=0
if [[ -x "$SUPERVISOR_PYTHON" ]]; then
  log "validating existing supervisor Python environment read-only: $SUPERVISOR_PYTHON_DIR"
  if python3 -B "$COMMAND_ROOT/scripts/provision_live_supervisor_config.py" \
    --command-root "$COMMAND_ROOT" \
    --python "$SUPERVISOR_PYTHON" \
    --requirements "$SUPERVISOR_REQUIREMENTS" \
    --validate-python-dependencies-only >/dev/null 2>&1; then
    supervisor_python_verified=1
  else
    log "existing supervisor Python environment failed read-only validation; provisioning a fresh one instead of mutating it in place: $SUPERVISOR_PYTHON_DIR"
  fi
fi

if [[ "$supervisor_python_verified" -eq 1 ]]; then
  log "reusing already-verified supervisor Python environment: $SUPERVISOR_PYTHON_DIR"
elif [[ $DRY_RUN -eq 1 ]]; then
  echo "  would run: python3 -m venv $SUPERVISOR_PYTHON_DIR (via isolated staging + atomic publish)"
  echo "  would run: $SUPERVISOR_PYTHON -m pip install --quiet --disable-pip-version-check -r $SUPERVISOR_REQUIREMENTS"
else
  mkdir -p "$SUPERVISOR_PYTHON_PARENT"
  candidate_python_dir="$(mktemp -d "$SUPERVISOR_PYTHON_PARENT/.supervisor-python-provision-$COMMAND_SHA.XXXXXX")"
  log "creating supervisor Python environment in isolation: $candidate_python_dir"
  if ! python3 -m venv "$candidate_python_dir"; then
    log "FATAL: failed to create supervisor Python environment: $candidate_python_dir"
    rm -rf -- "$candidate_python_dir"
    exit 1
  fi
  log "installing supervisor Python dependencies from $SUPERVISOR_REQUIREMENTS"
  if ! "$candidate_python_dir/bin/python3" -m pip install --quiet --disable-pip-version-check \
    -r "$SUPERVISOR_REQUIREMENTS"; then
    log "FATAL: failed to install supervisor Python dependencies from $SUPERVISOR_REQUIREMENTS"
    rm -rf -- "$candidate_python_dir"
    exit 1
  fi
  log "preflighting isolated supervisor Python dependencies for $candidate_python_dir/bin/python3"
  if ! python3 -B "$COMMAND_ROOT/scripts/provision_live_supervisor_config.py" \
    --command-root "$COMMAND_ROOT" \
    --python "$candidate_python_dir/bin/python3" \
    --requirements "$SUPERVISOR_REQUIREMENTS" \
    --validate-python-dependencies-only >/dev/null; then
    log "FATAL: python dependency preflight failed for command root=$COMMAND_ROOT"
    rm -rf -- "$candidate_python_dir"
    exit 1
  fi
  if [[ -e "$SUPERVISOR_PYTHON_DIR" ]]; then
    log "FATAL: refusing to replace an existing supervisor Python environment that failed read-only validation: $SUPERVISOR_PYTHON_DIR"
    rm -rf -- "$candidate_python_dir"
    exit 1
  fi
  if ! python3 - "$candidate_python_dir" "$SUPERVISOR_PYTHON_DIR" "$SUPERVISOR_PYTHON_PARENT" <<'PY'
import ctypes
import errno
import os
import sys
from pathlib import Path

source, destination, parent = map(Path, sys.argv[1:])
libc = ctypes.CDLL(None, use_errno=True)
renameat2 = libc.renameat2
renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
renameat2.restype = ctypes.c_int
if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
    error = ctypes.get_errno()
    if error != errno.EEXIST:
        raise OSError(error, os.strerror(error), destination)
fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
  then
    log "FATAL: failed to publish supervisor Python environment atomically: $SUPERVISOR_PYTHON_DIR"
    rm -rf -- "$candidate_python_dir"
    exit 1
  fi
  rm -rf -- "$candidate_python_dir"
  log "published verified supervisor Python environment: $SUPERVISOR_PYTHON_DIR"
fi

# ---------------------------------------------------------------------------
# 4. Dev bridge signing keys
#
# The keypair is a purely local trust boundary: local tooling signs dev task
# packets and the local dev-bridge inbox verifies them. Product BFF processes
# receive neither the private key nor the signer module, so a fresh machine may
# mint a fresh keypair. The private half is written to its own file and is
# never placed in the supervisor's authority environment.
#
# Key generation runs under $SUPERVISOR_PYTHON (the venv proven above), never
# the ambient python3: a fresh host has no reason to carry cryptography on
# its system interpreter, and this step must not depend on it.
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
  "$SUPERVISOR_PYTHON" - "$AUTHORITY_ENV_FILE" "$SIGNER_ENV_FILE" "$KEY_ID" <<'PYTHON'
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
# 5. Promote, persist, verify
# ---------------------------------------------------------------------------
log "promoting supervisor runtime"
run python3 "$STATUS_ROOT/scripts/promote_supervisor_runtime.py" \
  --repo "$COMMAND_ROOT" \
  --status-root "$STATUS_ROOT" \
  --live-config "$LIVE_CONFIG" \
  --python "$SUPERVISOR_PYTHON" \
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
