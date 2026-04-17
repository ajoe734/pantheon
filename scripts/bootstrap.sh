#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${PANTHEON_COMPOSE_FILE:-$ROOT_DIR/docker-compose.yml}"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

INFRA_SERVICES=(postgres minio nats)

if [[ $# -gt 0 ]]; then
  SERVICES=("$@")
else
  SERVICES=("${INFRA_SERVICES[@]}")
fi

echo "==> Starting services: ${SERVICES[*]}"
docker compose -f "$COMPOSE_FILE" up -d "${SERVICES[@]}"

echo "==> Waiting for services to become healthy..."
for svc in "${SERVICES[@]}"; do
  echo -n "    $svc "
  until [[ "$(docker compose -f "$COMPOSE_FILE" ps --format json "$svc" 2>/dev/null \
              | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Health','') if isinstance(d,dict) else next((x.get('Health','') for x in d if x.get('Service')=='$svc'),''))" 2>/dev/null)" == "healthy" ]]; do
    echo -n "."
    sleep 2
  done
  echo " healthy"
done

# Bootstrap MinIO bucket if minio was started
if printf '%s\n' "${SERVICES[@]}" | grep -q '^minio$'; then
  echo "==> Creating MinIO bucket via minio-init..."
  docker compose -f "$COMPOSE_FILE" run --rm minio-init
fi

echo "==> Done."
docker compose -f "$COMPOSE_FILE" ps "${SERVICES[@]}"
