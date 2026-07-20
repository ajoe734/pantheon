#!/usr/bin/env bash
# Ensure the authoritative dev paper fleet reconciler is running.
#
# The reconciler owns one binding-scoped paper worker per active RuntimeBinding.
# The old static pantheon-paper-runtime service is compatibility-only and must
# not be silently recreated by this helper.
set -u

CONTAINER="${PANTHEON_PAPER_FLEET_RECONCILER_CONTAINER:-${PANTHEON_PAPER_RUNTIME_CONTAINER:-pantheon-paper-fleet-reconciler-1}}"
COMPOSE_DIR="${PANTHEON_COMPOSE_DIR:-/home/lupin/pantheon}"
COMPOSE_SERVICE="${PANTHEON_PAPER_FLEET_RECONCILER_SERVICE:-${PANTHEON_PAPER_RUNTIME_SERVICE:-paper-fleet-reconciler}}"

ts() {
  date -u +%FT%TZ
}

status="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || true)"
if [ -z "$status" ]; then
  echo "$(ts) missing $CONTAINER; attempting compose recreate for $COMPOSE_SERVICE"
  if (cd "$COMPOSE_DIR" && docker compose up -d "$COMPOSE_SERVICE"); then
    echo "$(ts) recreated $COMPOSE_SERVICE as $CONTAINER"
    exit 0
  fi
  echo "$(ts) ERROR failed to recreate $COMPOSE_SERVICE from $COMPOSE_DIR" >&2
  exit 1
fi

if [ "$status" != "running" ]; then
  if docker start "$CONTAINER"; then
    echo "$(ts) restarted $CONTAINER (previous_status=$status)"
    exit 0
  fi
  echo "$(ts) ERROR failed to start $CONTAINER (previous_status=$status)" >&2
  exit 1
fi

health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null || echo unknown)"
if [ "$health" = "unhealthy" ]; then
  echo "$(ts) ERROR $CONTAINER is running but unhealthy" >&2
  exit 1
fi
