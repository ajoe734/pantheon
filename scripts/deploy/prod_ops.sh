#!/usr/bin/env bash
# Pantheon production VM-side operations adapter (ENV-PROD-CONTROL-IMPL-001).
#
# Invoked by prod_atomic_switch.sh / prod_rollback.sh as: prod_ops.sh <step> <release_id> [args]
# Runs ON the production control VM. Every step performs a real readback and exits non-zero
# on any missing input or failed check; there is no unconditional success path and no
# synthetic pass JSON. It never rebuilds or pulls images (compose --no-build, pull_policy:never)
# and never formats, mounts over, or recreates the production data disk.
#
# Steps: snapshot_preflight baseline_health start_green stop_green gate <name> <port>
#        drain_singletons reload_proxy activate_singletons resume_baseline_singletons
#        verify_runtime_config post_identity
#
# Required env: PROD_ROOT PROD_DATA_MOUNT PROD_BLUE_PORT PROD_GREEN_PORT PROD_BFF_HOST
#   PROD_FE_HOST RELEASE_MANIFEST_PATH PROD_COMPOSE_FILE PROD_COMPOSE_ENV_FILE
#   PROD_ACTIVE_PORT / PROD_TARGET_PORT (exported by the controller).
# Test seams (default to real binaries): DOCKER_BIN CURL_BIN PROD_CADDY_CONTAINER.
# Gates that need an authenticated identity read PROD_GATE_TOKEN_FILE (read-only synthetic
# identity issued by the production IdP); absent => the gate fails, it is never skipped.

set -uo pipefail

STEP="${1:-}"; RELEASE_ID="${2:-}"
[[ -n "$STEP" && -n "$RELEASE_ID" ]] || { echo '{"status":"error","error_code":"USAGE"}' >&2; exit 64; }
shift 2

DOCKER="${DOCKER_BIN:-docker}"
CURL="${CURL_BIN:-curl}"
CADDY_CONTAINER="${PROD_CADDY_CONTAINER:-pantheon-caddy}"
SNAPSHOT_MAX_AGE="${PROD_SNAPSHOT_MAX_AGE_SECONDS:-3600}"

fail() { printf '{"status":"error","step":"%s","error_code":"%s","message":"%s"}\n' "$STEP" "$1" "$2" >&2; exit 1; }
need() { local v; for v in "$@"; do [[ -n "${!v:-}" ]] || fail "MISSING_INPUT" "required env ${v} is missing"; done; }

need PROD_ROOT PROD_DATA_MOUNT PROD_BLUE_PORT PROD_GREEN_PORT PROD_BFF_HOST PROD_FE_HOST \
     RELEASE_MANIFEST_PATH PROD_COMPOSE_FILE PROD_COMPOSE_ENV_FILE

color_for_port() { if [[ "$1" == "$PROD_BLUE_PORT" ]]; then echo blue; elif [[ "$1" == "$PROD_GREEN_PORT" ]]; then echo green; else return 1; fi; }
compose() { "$DOCKER" compose -p pantheon-production -f "$PROD_COMPOSE_FILE" --env-file "$PROD_COMPOSE_ENV_FILE" "$@"; }
manifest_field() { python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));
for k in sys.argv[2].split("."): d=d[k]
print(d)' "$RELEASE_MANIFEST_PATH" "$1"; }
http_code() { "$CURL" -sS -o /dev/null -m 10 -w '%{http_code}' "$@"; }
FE_ORIGIN="https://${PROD_FE_HOST}"

release_env() {
  # Image refs come only from the manifest (immutable digests); exported for compose.
  export PANTHEON_RELEASE_ID="$RELEASE_ID"
  PANTHEON_OPERATOR_BFF_IMAGE="$(manifest_field backend.images.operator_bff)" || fail MANIFEST_READ "cannot read operator_bff image"
  PANTHEON_RUNTIME_MANAGER_IMAGE="$(manifest_field backend.images.runtime_manager)" \
    && PANTHEON_GOVERNANCE_IMAGE="$(manifest_field backend.images.governance)" \
    && PANTHEON_DEPLOYMENT_IMAGE="$(manifest_field backend.images.deployment)" \
    && PANTHEON_LIFECYCLE_PROJECTOR_IMAGE="$(manifest_field backend.images.lifecycle_projector)" \
    || fail OWNER_IMAGES "manifest backend.images lacks the core owner closure (runtime_manager, governance, deployment, lifecycle_projector)"
  export PANTHEON_OPERATOR_BFF_IMAGE PANTHEON_RUNTIME_MANAGER_IMAGE PANTHEON_GOVERNANCE_IMAGE \
    PANTHEON_DEPLOYMENT_IMAGE PANTHEON_LIFECYCLE_PROJECTOR_IMAGE PROD_BFF_CORS_ORIGINS="$FE_ORIGIN" PROD_BLUE_PORT PROD_GREEN_PORT
}

container_of() { compose ps -q "$1" 2>/dev/null | head -n1; }
require_ready() { # require_ready <port>
  [[ "$(http_code "http://127.0.0.1:$1/bff/readyz")" == "200" ]]
}
singleton_count() { compose ps -q lifecycle-projector 2>/dev/null | grep -c . ; }

case "$STEP" in
  snapshot_preflight)
    mountpoint -q "$PROD_DATA_MOUNT" || fail DATA_DISK_NOT_MOUNTED "${PROD_DATA_MOUNT} is not a mounted data disk"
    snap="${PROD_ROOT}/state/snapshots/${RELEASE_ID}.json"
    [[ -f "$snap" ]] || fail SNAPSHOT_MISSING "no pre-release snapshot record ${snap}"
    python3 - "$snap" "$RELEASE_ID" "${PROD_DATA_DISK:-}" "$SNAPSHOT_MAX_AGE" <<'PY' || fail SNAPSHOT_INVALID "snapshot record invalid, stale or for another disk/release"
import json, sys, time, datetime
rec = json.load(open(sys.argv[1]))
assert rec.get("release_id") == sys.argv[2]
assert rec.get("status") == "READY"
assert rec.get("disk") == sys.argv[3] and sys.argv[3]
created = datetime.datetime.fromisoformat(rec["created_at"].replace("Z", "+00:00")).timestamp()
assert 0 <= time.time() - created <= int(sys.argv[4])
PY
    ;;

  baseline_health)
    require_ready "$PROD_ACTIVE_PORT" || fail BASELINE_UNHEALTHY "baseline on port ${PROD_ACTIVE_PORT:-?} not ready"
    ;;

  start_green)
    port="${1:-}"; image="${2:-}"; color="$(color_for_port "$port")" || fail BAD_PORT "port ${port} is neither blue nor green"
    [[ "$port" != "${PROD_ACTIVE_PORT:-}" ]] || fail PORT_IN_USE "target port equals the active baseline port"
    release_env
    [[ "$PANTHEON_OPERATOR_BFF_IMAGE" == "$image" ]] || fail IMAGE_MISMATCH "controller image differs from manifest"
    compose --profile core up -d --no-build --no-recreate runtime-manager governance deployment || fail OWNER_CLOSURE "core owner closure not running"
    compose --profile "$color" up -d --no-build "operator-bff-${color}" || fail GREEN_START "compose failed to start ${color}"
    for _ in $(seq 1 "${PROD_GREEN_READY_TRIES:-30}"); do require_ready "$port" && exit 0; sleep "${PROD_GREEN_READY_INTERVAL:-2}"; done
    fail GREEN_NOT_READY "${color} did not become ready on ${port}"
    ;;

  stop_green)
    color="$(color_for_port "${PROD_TARGET_PORT:-}")" || fail BAD_PORT "no target port"
    compose --profile "$color" rm -fsv "operator-bff-${color}" || fail STOP_GREEN "could not stop ${color}"
    ;;

  gate)
    name="${1:-}"; port="${2:-}"
    case "$name" in
      health) require_ready "$port" || fail GATE_HEALTH "readyz not 200 on ${port}" ;;
      auth)
        code="$(http_code "http://127.0.0.1:${port}/bff/capabilities")"
        [[ "$code" == "401" || "$code" == "403" ]] || fail GATE_AUTH "unauthenticated request returned ${code}, expected 401/403" ;;
      cors)
        allowed="$("$CURL" -sS -m 10 -D - -o /dev/null -H "Origin: ${FE_ORIGIN}" -X OPTIONS -H "Access-Control-Request-Method: GET" "http://127.0.0.1:${port}/bff/capabilities" | tr -d '\r' | awk -F': ' 'tolower($1)=="access-control-allow-origin"{print $2}')"
        denied="$("$CURL" -sS -m 10 -D - -o /dev/null -H "Origin: https://evil.invalid" -X OPTIONS -H "Access-Control-Request-Method: GET" "http://127.0.0.1:${port}/bff/capabilities" | tr -d '\r' | awk -F': ' 'tolower($1)=="access-control-allow-origin"{print $2}')"
        [[ "$allowed" == "$FE_ORIGIN" && -z "$denied" ]] || fail GATE_CORS "allow-origin='${allowed}' foreign='${denied}'" ;;
      tenant)
        need PROD_GATE_TOKEN_FILE; [[ -s "$PROD_GATE_TOKEN_FILE" ]] || fail GATE_TENANT "gate token file missing/empty"
        code="$(http_code -H "Authorization: Bearer $(cat "$PROD_GATE_TOKEN_FILE")" -H "X-Tenant-Id: __foreign_tenant__" "http://127.0.0.1:${port}/bff/capabilities")"
        [[ "$code" == "401" || "$code" == "403" || "$code" == "404" ]] || fail GATE_TENANT "cross-tenant request returned ${code}" ;;
      read_only)
        c="$(container_of "operator-bff-$(color_for_port "$port")")"; [[ -n "$c" ]] || fail GATE_READ_ONLY "green container not found"
        envs="$("$DOCKER" inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c")" || fail GATE_READ_ONLY "inspect failed"
        for want in PANTHEON_LIVE_BROKER_ENABLED=false PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED=false PANTHEON_BFF_AUTH_STUB=false PANTHEON_BFF_AUTH_MODE=strict; do
          grep -qx "$want" <<<"$envs" || fail GATE_READ_ONLY "runtime readback lacks ${want}"
        done
        code="$(http_code -X POST "http://127.0.0.1:${port}/bff/capabilities")"
        [[ "$code" =~ ^(401|403|404|405)$ ]] || fail GATE_READ_ONLY "unauthenticated write returned ${code}" ;;
      schema)
        want="$(manifest_field migration_set_sha256)" || fail GATE_SCHEMA "manifest has no migration set sha"
        img="$(manifest_field backend.images.operator_bff)"
        got="$("$DOCKER" image inspect -f '{{index .Config.Labels "org.pantheon.migration_set_sha256"}}' "$img" 2>/dev/null)"
        [[ -n "$got" && "$got" == "$want" ]] || fail GATE_SCHEMA "image migration label '${got}' != manifest '${want}'"
        compose exec -T postgres pg_isready >/dev/null || fail GATE_SCHEMA "postgres not ready" ;;
      old_new_compat)
        want="$(manifest_field compatibility_manifest_sha256)" || fail GATE_COMPAT "manifest has no compatibility sha"
        img="$(manifest_field backend.images.operator_bff)"
        got="$("$DOCKER" image inspect -f '{{index .Config.Labels "org.pantheon.compatibility_manifest_sha256"}}' "$img" 2>/dev/null)"
        [[ -n "$got" && "$got" == "$want" ]] || fail GATE_COMPAT "image compatibility label '${got}' != manifest '${want}'"
        # Old (active) and new (green) must serve the same database concurrently.
        require_ready "$PROD_ACTIVE_PORT" && require_ready "$port" || fail GATE_COMPAT "old and new are not both ready" ;;
      *) fail UNKNOWN_GATE "unknown gate '${name}'" ;;
    esac
    ;;

  drain_singletons)
    # Exactly one singleton owner: stop it before the upstream switch.
    compose --profile singleton rm -fsv lifecycle-projector || fail DRAIN "could not stop singleton owner"
    [[ "$(singleton_count)" == "0" ]] || fail DRAIN "singleton owner still present after drain"
    ;;

  activate_singletons|resume_baseline_singletons)
    release_env
    compose --profile singleton up -d --no-build --no-recreate lifecycle-projector || fail ACTIVATE "could not start singleton owner"
    [[ "$(singleton_count)" == "1" ]] || fail ACTIVATE "singleton owner count is $(singleton_count), expected exactly 1"
    ;;

  reload_proxy)
    "$DOCKER" exec "$CADDY_CONTAINER" caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 || fail PROXY_VALIDATE "caddy config invalid; not reloaded"
    "$DOCKER" exec "$CADDY_CONTAINER" caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 || fail PROXY_RELOAD "caddy reload failed"
    ;;

  verify_runtime_config)
    cfg="${PROD_ROOT}/config/runtime-config.js"
    [[ -s "$cfg" ]] || fail RUNTIME_CONFIG_MISSING "${cfg} missing"
    [[ "$(readlink -f "$cfg")" != "$(readlink -f "${PROD_ROOT}/fe/current")"/* ]] || fail RUNTIME_CONFIG_SEALED "runtime-config.js must live outside the sealed FE release"
    hdrs="$("$CURL" -sS -m 10 -D - -o "${PROD_ROOT}/state/runtime-config.readback" "${PROD_FE_BASE_URL:-$FE_ORIGIN}/runtime-config.js")" || fail RUNTIME_CONFIG_FETCH "cannot fetch same-origin /runtime-config.js"
    grep -qi '^cache-control:.*no-store' <<<"$hdrs" || fail RUNTIME_CONFIG_CACHEABLE "runtime-config.js is not served no-store"
    cmp -s "$cfg" "${PROD_ROOT}/state/runtime-config.readback" || fail RUNTIME_CONFIG_DRIFT "served runtime-config.js differs from the rendered file"
    grep -q "https://${PROD_BFF_HOST}" "$cfg" || fail RUNTIME_CONFIG_BFF "runtime-config.js does not point at the production BFF"
    ;;

  post_identity)
    color="$(color_for_port "${PROD_TARGET_PORT:-}")" || fail BAD_PORT "no target port"
    c="$(container_of "operator-bff-${color}")"; [[ -n "$c" ]] || fail IDENTITY "active container not found"
    img="$("$DOCKER" inspect -f '{{.Config.Image}}' "$c")" || fail IDENTITY "inspect failed"
    fe="$(cat "${PROD_ROOT}/fe/current/.artifact_sha256" 2>/dev/null)" || fail IDENTITY "FE identity file missing"
    python3 -c 'import json,sys;print(json.dumps({"frontend_artifact_sha256":sys.argv[1],"operator_bff_image":sys.argv[2],"observed_from_live_vm":True}))' "$fe" "$img"
    ;;

  *) fail UNKNOWN_STEP "unknown step '${STEP}'" ;;
esac
