#!/usr/bin/env bash
# audit_deploy_drift.sh — detect drift between deployed dev containers and origin/dev.
#
# WHY: dev images carry no git-SHA / OCI revision label (verified 2026-06-14), so
# there is no reliable way to tell which commit a running service was built from.
# Drift happened silently this session (the lean-runtime worker image was 8 days
# stale; telemetry/operator-bff were stale) and was only caught by hand. This script
# is the interim detector: for each running pantheon service it compares the image
# build date against commits touching that service's repo path since the build, and
# flags expected-but-missing services (e.g. the paper-fleet reconciler).
#
# Usage:  bash scripts/audit_deploy_drift.sh
# Env:    PANTHEON_DEV_SSH_KEY (default ~/.ssh/google_compute_engine)
#         PANTHEON_DEV_HOST    (default lupin@35.201.239.38)
set -uo pipefail
SSH_KEY="${PANTHEON_DEV_SSH_KEY:-$HOME/.ssh/google_compute_engine}"
DEV_HOST="${PANTHEON_DEV_HOST:-lupin@35.201.239.38}"
ssh_vm() { ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=12 "$DEV_HOST" "$@" 2>/dev/null; }

# container -> repo path(s) used to detect drift (space-separated paths).
mapping=(
  "pantheon-operator-bff-1:services/control-plane/bff"
  "pantheon-telemetry-1:services/telemetry"
  "pantheon-runtime-manager-1:services/execution/runtime-manager services/runtime-manager"
  "pantheon-optimizer-svc-1:services/optimizer-svc"
  "pantheon-evolution-1:services/evolution"
  "pantheon-governance-1:services/governance"
  "pantheon-consultation-svc-1:services/consultation"
  "pantheon-broker-1:services/broker"
  "pantheon-reconciliation-drift-svc-1:services/reconciliation-drift"
)
# representative paper worker (image: pantheon-lean-runtime:*)
worker="$(ssh_vm 'docker ps --format "{{.Names}}" | grep "^pantheon-paper-runtime-" | head -1')"
[ -n "$worker" ] && mapping+=("$worker:services/execution/lean_runtime")


# --- precise mode: use the baked org.opencontainers.image.revision label ---
if [ "${1:-}" = "--precise" ]; then
  git fetch origin dev --quiet 2>/dev/null
  printf "%-38s %-14s %-10s %s\n" "SERVICE" "IMG_REV" "STATUS" "PATHS_AHEAD"
  for entry in "${mapping[@]}"; do
    c="${entry%%:*}"; paths="${entry#*:}"
    img="$(ssh_vm "docker inspect $c --format '{{.Config.Image}}'")"
    rev="$(ssh_vm "docker image inspect '$img' --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}'")"
    if [ -z "$rev" ] || [ "$rev" = "unknown" ] || [ "$rev" = "<no value>" ]; then
      printf "%-38s %-14s %-10s %s\n" "${c#pantheon-}" "-" "NO-LABEL" "rebuild to stamp SHA"
      continue
    fi
    ahead="$(git log "${rev}..origin/dev" --oneline -- $paths 2>/dev/null | wc -l | tr -d ' ')"
    if ! git cat-file -e "$rev" 2>/dev/null; then status="UNKNOWN-SHA"; elif [ "${ahead:-0}" -gt 0 ]; then status="DRIFT"; else status="ok"; fi
    printf "%-38s %-14s %-10s %s\n" "${c#pantheon-}" "${rev:0:12}" "$status" "$ahead"
  done
  exit 0
fi

git fetch origin dev --quiet 2>/dev/null
printf "%-38s %-12s %-10s %s\n" "SERVICE" "IMG_BUILT" "STATUS" "COMMITS_SINCE"
drift=0
for entry in "${mapping[@]}"; do
  c="${entry%%:*}"; paths="${entry#*:}"
  img="$(ssh_vm "docker inspect $c --format '{{.Config.Image}}'")"
  built_full="$(ssh_vm "docker image inspect '$img' --format '{{.Created}}'")"
  built="${built_full%%T*}"
  [ -z "$built_full" ] && { printf "%-38s %-12s %-10s\n" "${c#pantheon-}" "?" "NO-IMAGE"; continue; }
  n="$(git log origin/dev --since="$built_full" --oneline -- $paths 2>/dev/null | wc -l | tr -d ' ')"
  if [ "${n:-0}" -gt 0 ]; then status="DRIFT"; drift=$((drift+1)); else status="ok"; fi
  printf "%-38s %-12s %-10s %s\n" "${c#pantheon-}" "$built" "$status" "$n"
done

# expected-but-absent durability services
echo
if ! ssh_vm 'docker ps --format "{{.Names}}"' | grep -q "paper-fleet-reconciler"; then
  echo "WARN: paper-fleet-reconciler NOT running (workers unmanaged; gated behind compose profile 'paper-fleet')"
fi
echo
echo "drift_count=$drift"
echo "NOTE: images carry no git-SHA label; date-granularity heuristic only."
echo "FOLLOW-UP: bake org.opencontainers.image.revision (git SHA) into images for exact drift detection."
[ "$drift" -gt 0 ] && exit 1 || exit 0
