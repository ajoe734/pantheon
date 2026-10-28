#!/usr/bin/env bash
# Pantheon production VM-side operations adapter (ENV-PROD-CONTROL-IMPL-001).
#
# Invoked by prod_atomic_switch.sh / prod_rollback.sh as: prod_ops.sh <step> <release_id> [args]
# Runs ON the production control VM. Every step performs a real readback and exits non-zero
# on any missing input or failed check; there is no unconditional success path and no
# synthetic pass JSON. It never rebuilds images (compose --no-build, pull_policy:never),
# pre-acquires only digest-pinned (@sha256:...) images, and never formats, mounts over, or
# recreates the production data disk.
#
# Steps: migrate snapshot_preflight prepull_images baseline_health start_green stop_green
#        gate <name> <port> drain_singletons reload_proxy activate_singletons
#        resume_baseline_singletons verify_runtime_config post_identity
#
# Required env: PROD_ROOT PROD_DATA_MOUNT PROD_BLUE_PORT PROD_GREEN_PORT PROD_BFF_HOST
#   PROD_FE_HOST RELEASE_MANIFEST_PATH PROD_COMPOSE_FILE PROD_COMPOSE_ENV_FILE
#   PROD_ACTIVE_PORT / PROD_TARGET_PORT (exported by the controller).
# Test seams (default to real binaries): DOCKER_BIN CURL_BIN MOUNTPOINT_BIN SUDO_BIN PROD_CADDY_CONTAINER.
# Gates that need an authenticated identity read PROD_GATE_TOKEN_FILE (read-only synthetic
# identity issued by the production IdP); absent => the gate fails, it is never skipped.

set -uo pipefail

STEP="${1:-}"; RELEASE_ID="${2:-}"
[[ -n "$STEP" && -n "$RELEASE_ID" ]] || { echo '{"status":"error","error_code":"USAGE"}' >&2; exit 64; }
shift 2

DOCKER="${DOCKER_BIN:-docker}"
CURL="${CURL_BIN:-curl}"
MOUNTPOINT="${MOUNTPOINT_BIN:-mountpoint}"
SUDO="${SUDO_BIN:-sudo}"
CADDY_CONTAINER="${PROD_CADDY_CONTAINER:-pantheon-caddy}"
SNAPSHOT_MAX_AGE="${PROD_SNAPSHOT_MAX_AGE_SECONDS:-3600}"

fail() { printf '{"status":"error","step":"%s","error_code":"%s","message":"%s"}\n' "$STEP" "$1" "$2" >&2; exit 1; }
need() { local v; for v in "$@"; do [[ -n "${!v:-}" ]] || fail "MISSING_INPUT" "required env ${v} is missing"; done; }

run_priv() {
  if [[ "${PROD_USE_SUDO:-false}" == "true" || "${FAKE_OSLOGIN_NONROOT:-false}" == "true" ]]; then
    "$SUDO" "$@"
  else
    "$@"
  fi
}

need PROD_ROOT PROD_DATA_MOUNT PROD_BLUE_PORT PROD_GREEN_PORT PROD_BFF_HOST PROD_FE_HOST \
     RELEASE_MANIFEST_PATH PROD_COMPOSE_FILE PROD_COMPOSE_ENV_FILE

# Product owner packaging contract (single source: staging_sanitize.PRODUCT_OWNER_IMAGE_KEYS, asserted equal by tests).
# Core-profile product owners are stateless-process owners with native Postgres stores; the Agora request worker is a
# singleton-profile worker (exactly one instance, drained/activated with the lifecycle projector, never per color).
PRODUCT_OWNER_KEYS="registry telemetry incidents postmortems evolution lineage_read reconciliation_drift_svc persona consultation_svc source_ingest training_session_svc policy_learning_svc research_orchestrator_svc memory capital openclaw_gateway_adapter"
# Singleton workers (drained/activated together with the lifecycle projector; exactly one each, never per color).
# The Paper fleet reconciler/signal producer reuse the existing training-session image and the Redis signal-store.
PRODUCT_WORKER_KEY="agora_interaction_worker paper_fleet_reconciler paper_signal_producer"
ABSENT_BASELINE_IMAGE="baseline-absent.invalid/product-owner@sha256:$(printf '0%.0s' {1..64})"
svc_of() { echo "${1//_/-}"; }
PRODUCT_CORE_SERVICES=""
for _k in $PRODUCT_OWNER_KEYS; do PRODUCT_CORE_SERVICES+="$(svc_of "$_k") "; done
PRODUCT_WORKER_SERVICE=""
for _k in $PRODUCT_WORKER_KEY; do PRODUCT_WORKER_SERVICE+="$(svc_of "$_k") "; done
# Both profiles are always active: the worker depends on core product owners and compose rejects inactive-profile deps.
CORE_PROFILES=(--profile core --profile singleton)

# Digest-pinned product owner images currently exported by release_env/baseline_env (none for an absent baseline).
owner_images() {
  local k v
  [[ "${PROD_BASELINE_PRODUCT_OWNERS:-present}" == "absent" ]] && return 0
  for k in $PRODUCT_OWNER_KEYS $PRODUCT_WORKER_KEY; do
    v="PANTHEON_${k^^}_IMAGE"; echo "${!v}"
  done
}

color_for_port() { if [[ "$1" == "$PROD_BLUE_PORT" ]]; then echo blue; elif [[ "$1" == "$PROD_GREEN_PORT" ]]; then echo green; else return 1; fi; }
compose() { run_priv "$DOCKER" compose -p pantheon-production -f "$PROD_COMPOSE_FILE" --env-file "$PROD_COMPOSE_ENV_FILE" "$@"; }
manifest_field() { python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));
for k in sys.argv[2].split("."): d=d[k]
print(d)' "$RELEASE_MANIFEST_PATH" "$1" 2>/dev/null; }
http_code() { "$CURL" -sS -o /dev/null -m 10 -w '%{http_code}' "$@"; }
FE_ORIGIN="https://${PROD_FE_HOST}"
BFF_ORIGIN="https://${PROD_BFF_HOST}"

release_env() {
  # Candidate image refs come only from the candidate manifest (immutable @sha256:... digests).
  local vars
  vars="$(python3 - "$RELEASE_MANIFEST_PATH" "$RELEASE_ID" "$FE_ORIGIN" "$PRODUCT_OWNER_KEYS $PRODUCT_WORKER_KEY" <<'PY'
import json, re, shlex, sys

DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
m = json.load(open(sys.argv[1], encoding="utf-8"))
rel_id, fe_origin = sys.argv[2], sys.argv[3]
imgs = m.get("backend", {}).get("images", {})
product_keys = sys.argv[4].split()
required = ("operator_bff", "runtime_manager", "governance", "deployment", "lifecycle_projector", *product_keys)
for k in required:
    val = str(imgs.get(k) or "").strip()
    if not DIGEST_RE.match(val):
        raise SystemExit(1)
bff_img = imgs["operator_bff"]
bff_digest = "sha256:" + bff_img.split("@sha256:", 1)[1]
out = {
    "PANTHEON_RELEASE_ID": rel_id,
    "PANTHEON_BACKEND_COMMIT": str(m.get("backend", {}).get("commit") or ""),
    "PANTHEON_FRONTEND_COMMIT": str(m.get("frontend", {}).get("commit") or ""),
    "PANTHEON_CONFIG_SCHEMA_VERSION": str(m.get("config_schema_version") or "v1"),
    "PANTHEON_OPERATOR_BFF_IMAGE": bff_img,
    "PANTHEON_OPERATOR_BFF_DIGEST": bff_digest,
    "PANTHEON_RUNTIME_MANAGER_IMAGE": imgs["runtime_manager"],
    "PANTHEON_GOVERNANCE_IMAGE": imgs["governance"],
    "PANTHEON_DEPLOYMENT_IMAGE": imgs["deployment"],
    "PANTHEON_LIFECYCLE_PROJECTOR_IMAGE": imgs["lifecycle_projector"],
    "PROD_BFF_CORS_ORIGINS": fe_origin,
    "PROD_BASELINE_PRODUCT_OWNERS": "present",
}
for k in product_keys:
    out[f"PANTHEON_{k.upper()}_IMAGE"] = imgs[k]
for k, v in out.items():
    print(f"export {k}={shlex.quote(v)}")
PY
  )" || fail OWNER_IMAGES "manifest backend.images lacks the digest-pinned owner closure (core owners and every product owner incl. agora_interaction_worker)"
  eval "$vars"
  export PROD_BLUE_PORT PROD_GREEN_PORT
}

baseline_env() {
  # Retained baseline image refs and config come ONLY from the journaled baseline_release.json,
  # state/baseline.json, or manifest.rollback_baseline — NEVER from the candidate release_env.
  local journal_rec="${PROD_ROOT}/state/journal/${RELEASE_ID}/baseline_release.json"
  local state_base="${PROD_ROOT}/state/baseline.json"
  local vars
  vars="$(python3 - "$journal_rec" "$state_base" "$RELEASE_MANIFEST_PATH" "$FE_ORIGIN" "$PRODUCT_OWNER_KEYS $PRODUCT_WORKER_KEY" "$ABSENT_BASELINE_IMAGE" <<'PY'
import json, re, shlex, sys
from pathlib import Path

DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
journal_p, state_p, manifest_p, fe_origin = [Path(x) for x in sys.argv[1:4]] + [sys.argv[4]]
product_keys = sys.argv[5].split()
absent_image = sys.argv[6]

rec = {}
if journal_p.is_file():
    rec = json.loads(journal_p.read_text(encoding="utf-8"))
elif state_p.is_file():
    rec = json.loads(state_p.read_text(encoding="utf-8"))

manifest = json.loads(manifest_p.read_text(encoding="utf-8")) if manifest_p.is_file() else {}
rb = manifest.get("rollback_baseline") or {}

if not rec and not rb:
    raise SystemExit("no retained baseline record or rollback_baseline")

rel_id = str(rec.get("release_id") or rb.get("previous_release_id") or "").strip()
fe_commit = str(rec.get("frontend_commit") or rb.get("frontend_commit") or "").strip()
fe_sha = str(rec.get("frontend_artifact_sha256") or rb.get("frontend_artifact_sha256") or "").strip()
bff_commit = str(rec.get("backend_commit") or rb.get("backend_commit") or "").strip()
bff_img = str(rec.get("operator_bff_image") or rb.get("backend_operator_bff_image") or "").strip()
cfg_ver = str(rec.get("config_schema_version") or rb.get("config_schema_version") or manifest.get("config_schema_version") or "v1").strip()

if not rel_id or not DIGEST_RE.match(bff_img):
    raise SystemExit("invalid baseline release_id or operator_bff_image")

b_imgs = {}
for src in (rb.get("backend_images"), rec.get("backend_images")):
    if isinstance(src, dict):
        for k, v in src.items():
            if isinstance(v, str) and DIGEST_RE.match(v.strip()):
                b_imgs[k] = v.strip()
b_imgs["operator_bff"] = bff_img
bff_digest_hex = bff_img.split("@sha256:", 1)[1]
repo_prefix = bff_img.split("@sha256:", 1)[0].rsplit("/", 1)[0] if "/" in bff_img else "r"
for owner in ("runtime_manager", "governance", "deployment", "lifecycle_projector"):
    if owner not in b_imgs:
        b_imgs[owner] = f"{repo_prefix}/{owner}@sha256:{bff_digest_hex}"
    if not DIGEST_RE.match(b_imgs[owner]):
        raise SystemExit(f"unpinned baseline owner {owner}")

# Product owners are restored to the EXACT retained baseline digests; they are never derived from the candidate or
# invented. A baseline that predates the product owners must carry none of them (rollback then removes them); a
# partial set is an unrestorable baseline and fails closed.
have = [k for k in product_keys if k in b_imgs]
if have and len(have) != len(product_keys):
    raise SystemExit("partial baseline product owner closure: missing " + ",".join(k for k in product_keys if k not in b_imgs))

out = {
    "PANTHEON_RELEASE_ID": rel_id,
    "BASELINE_RELEASE_ID": rel_id,
    "BASELINE_FE_COMMIT": fe_commit,
    "BASELINE_FE_SHA": fe_sha,
    "PANTHEON_BACKEND_COMMIT": bff_commit,
    "BASELINE_BFF_COMMIT": bff_commit,
    "PANTHEON_CONFIG_SCHEMA_VERSION": cfg_ver,
    "PANTHEON_OPERATOR_BFF_IMAGE": b_imgs["operator_bff"],
    "PANTHEON_OPERATOR_BFF_DIGEST": "sha256:" + bff_digest_hex,
    "PANTHEON_RUNTIME_MANAGER_IMAGE": b_imgs["runtime_manager"],
    "PANTHEON_GOVERNANCE_IMAGE": b_imgs["governance"],
    "PANTHEON_DEPLOYMENT_IMAGE": b_imgs["deployment"],
    "PANTHEON_LIFECYCLE_PROJECTOR_IMAGE": b_imgs["lifecycle_projector"],
    "PROD_BFF_CORS_ORIGINS": fe_origin,
    "PROD_BASELINE_PRODUCT_OWNERS": "present" if have else "absent",
}
for k in product_keys:
    # Sentinel only satisfies compose interpolation when the baseline has no product owners; it is never started.
    out[f"PANTHEON_{k.upper()}_IMAGE"] = b_imgs[k] if have else absent_image
for k, v in out.items():
    print(f"export {k}={shlex.quote(v)}")
PY
  )" || fail BASELINE_ENV_MISSING "retained baseline manifest/config closure missing or invalid for rollback"
  eval "$vars"
  export PROD_BLUE_PORT PROD_GREEN_PORT
}

# Bind schema/compat gates to live staging evidence for this exact manifest (sets GATE_REASON on failure).
staging_migration_gate() {
  GATE_REASON="$(python3 - "$RELEASE_MANIFEST_PATH" "${STAGING_EVIDENCE_PATH:-}" <<'PY' 2>&1
import hashlib, json, re, sys
mp, ep = sys.argv[1:3]
try:
    m = json.load(open(mp)); e = json.load(open(ep))
except Exception as exc:
    print(f"cannot read manifest/staging evidence: {exc}"); sys.exit(1)
sha = re.compile(r"sha256:[0-9a-f]{64}|[0-9a-f]{64}")
def bad(msg): print(msg); sys.exit(1)
g = (e.get("gates") or {}).get("migration_and_compatibility")
if e.get("status") != "staging_verified" or e.get("dry_run") is not False or e.get("observed_from_live_vm") is not True:
    bad("staging evidence is not a live staging_verified observation")
if e.get("release_id") != m.get("release_id"):
    bad("staging evidence release_id != manifest")
if (e.get("manifest") or {}).get("operator_bff_image") != m["backend"]["images"]["operator_bff"]:
    bad("staging evidence BFF image != manifest")
if not isinstance(g, dict) or g.get("migration_verified") is not True or g.get("old_app_new_schema_compatible") is not True:
    bad("staging evidence lacks migration_verified/old_app_new_schema_compatible")
want = m.get("migration_set_sha256")
if not isinstance(want, str) or not sha.fullmatch(want) or g.get("migration_set_sha256") != want:
    bad("staging migration_set_sha256 != manifest migration_set_sha256")
if not isinstance(m.get("compatibility_manifest_sha256"), str) or not sha.fullmatch(m["compatibility_manifest_sha256"]):
    bad("manifest compatibility_manifest_sha256 missing/invalid")
PY
)" || return 1
}
# Production consumption of the Stage-owned migration contract (prod_preflight.py migration). The product has
# no schema_migrations table: the qualified versioned SQL (migration-spec.json + migrations/*.sql, PROD_MIGRATION_DIR)
# is hashed with the Stage recipe and must equal the manifest migration_set_sha256, and the live information_schema
# catalog (same query Stage uses) must contain every relation that SQL creates. Never applies migrations.
MIGRATION_HELPER="${PROD_PREFLIGHT_PY:-$(dirname "${BASH_SOURCE[0]}")/prod_preflight.py}"
migration_dir() { printf '%s' "${PROD_MIGRATION_DIR:-${PROD_ROOT}/migration/${RELEASE_ID}}"; }
live_catalog() {
  compose exec -T postgres sh -c 'psql -X -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    <<<"$(python3 -I "$MIGRATION_HELPER" migration catalog-sql "$RELEASE_MANIFEST_PATH" "$(migration_dir)")" 2>/dev/null
}
production_schema_gate() {
  local catalog
  GATE_REASON="$(python3 -I "$MIGRATION_HELPER" migration verify "$RELEASE_MANIFEST_PATH" "$(migration_dir)" 2>&1)" || return 1
  catalog="$(live_catalog)" || { GATE_REASON="production live catalog unreadable (database unreachable)"; return 1; }
  GATE_REASON="$(printf '%s' "$catalog" | python3 -I "$MIGRATION_HELPER" migration catalog "$RELEASE_MANIFEST_PATH" "$(migration_dir)" 2>&1)" || return 1
}
container_of() { compose ps -q "$1" 2>/dev/null | head -n1; }
require_ready() { # require_ready <port>
  [[ "$(http_code "http://127.0.0.1:$1/bff/readyz")" == "200" ]]
}
# Process liveness only. BFF /bff/readyz also requires the singleton lifecycle projector controller to
# report the SAME deployment sha as the BFF (BFF_COMMIT), so a candidate cannot be readyz-true before
# the singleton hand-off. Pre-handoff checks use liveness; post-handoff checks use readiness.
require_live() { # require_live <port>
  [[ "$(http_code "http://127.0.0.1:$1/livez")" == "200" ]]
}
wait_until() { # wait_until <tries> <interval> <command...>
  local tries="$1" interval="$2" _; shift 2
  for _ in $(seq 1 "$tries"); do "$@" && return 0; sleep "$interval"; done
  return 1
}
singleton_count() { compose ps -q lifecycle-projector 2>/dev/null | grep -c . || true; }
worker_count() { compose ps -q $PRODUCT_WORKER_SERVICE 2>/dev/null | grep -c . || true; }
# Every singleton worker (Agora request worker, Paper fleet reconciler, Paper signal producer) is exactly one.
workers_each_single() {
  local svc
  for svc in $PRODUCT_WORKER_SERVICE; do
    [[ "$(compose ps -q "$svc" 2>/dev/null | grep -c . || true)" == "1" ]] || { OWNER_REASON="singleton worker ${svc} count is not exactly 1"; return 1; }
  done
}
# Every product owner must run the exact exported digest (candidate or retained baseline); the Agora request worker
# must be a singleton (0 only when the baseline has none). Sets OWNER_REASON on failure.
verify_product_owner_images() {
  local k svc var want c got
  OWNER_REASON=""
  if [[ "${PROD_BASELINE_PRODUCT_OWNERS:-present}" == "absent" ]]; then
    for k in $PRODUCT_OWNER_KEYS $PRODUCT_WORKER_KEY; do
      svc="$(svc_of "$k")"
      [[ -z "$(container_of "$svc")" ]] || { OWNER_REASON="product owner ${svc} still present but absent from retained baseline"; return 1; }
    done
    return 0
  fi
  for k in $PRODUCT_OWNER_KEYS $PRODUCT_WORKER_KEY; do
    svc="$(svc_of "$k")"; var="PANTHEON_${k^^}_IMAGE"; want="${!var}"
    c="$(container_of "$svc")"
    [[ -n "$c" && "$(printf '%s\n' "$c" | grep -c .)" == "1" ]] || { OWNER_REASON="product owner ${svc} is not running exactly once"; return 1; }
    got="$(run_priv "$DOCKER" inspect -f '{{.Config.Image}}' "$c" 2>/dev/null | tr -d '[:space:]')"
    [[ "$got" == "$want" ]] || { OWNER_REASON="product owner ${svc} runs '${got}' != expected '${want}'"; return 1; }
  done
  workers_each_single || return 1
}
# Remove product owner containers by compose project labels (works without a resolvable owner image).
remove_product_owners() {
  local k svc ids
  for k in $PRODUCT_OWNER_KEYS $PRODUCT_WORKER_KEY; do
    svc="$(svc_of "$k")"
    ids="$(run_priv "$DOCKER" ps -aq --filter "label=com.docker.compose.project=pantheon-production" --filter "label=com.docker.compose.service=${svc}")" || return 1
    [[ -z "$ids" ]] || run_priv "$DOCKER" rm -f $ids >/dev/null || return 1
  done
}

verify_runtime_config_internal() {
  local expected_rel="${1:-}"
  local cfg="${PROD_ROOT}/config/runtime-config.js"
  [[ -s "$cfg" ]] || fail RUNTIME_CONFIG_MISSING "${cfg} missing"
  if [[ -L "${PROD_ROOT}/fe/current" || -d "${PROD_ROOT}/fe/current" ]]; then
    [[ "$(readlink -f "$cfg")" != "$(readlink -f "${PROD_ROOT}/fe/current")"/* ]] \
      || fail RUNTIME_CONFIG_SEALED "runtime-config.js must live outside the sealed FE release"
  fi
  mkdir -p "${PROD_ROOT}/state"
  local hdrs
  hdrs="$("$CURL" -sS -m 10 -D - -o "${PROD_ROOT}/state/runtime-config.readback" "${PROD_FE_BASE_URL:-$FE_ORIGIN}/runtime-config.js")" \
    || fail RUNTIME_CONFIG_FETCH "cannot fetch same-origin /runtime-config.js"
  grep -qi '^cache-control:.*no-store' <<<"$hdrs" || fail RUNTIME_CONFIG_CACHEABLE "runtime-config.js is not served no-store"
  cmp -s "$cfg" "${PROD_ROOT}/state/runtime-config.readback" || fail RUNTIME_CONFIG_DRIFT "served runtime-config.js differs from the rendered file"
  grep -q "https://${PROD_BFF_HOST}" "$cfg" || fail RUNTIME_CONFIG_BFF "runtime-config.js does not point at the production BFF"
  python3 - "$cfg" "$expected_rel" "https://${PROD_BFF_HOST}" <<'PY' || fail RUNTIME_CONFIG_CONTRACT "runtime-config.js violates the accepted public runtime config contract or expected configRef ${expected_rel}"
import hashlib, json, re, sys
text = open(sys.argv[1], encoding="utf-8").read()
want_ref, want_bff = sys.argv[2], sys.argv[3]
m = re.search(r"window\.__PANTHEON_DEPLOYMENT_CONFIG__\s*=\s*(\{.*\})\s*;", text, re.S)
if not m:
    raise SystemExit("no __PANTHEON_DEPLOYMENT_CONFIG__ object")
cfg = json.loads(m.group(1))
if cfg.get("schemaVersion") != "pantheon.fe.public-runtime-config.v1":
    raise SystemExit("unsupported schemaVersion")
if cfg.get("environment") != "production":
    raise SystemExit("environment is not production")
if cfg.get("bffBaseUrl") != want_bff:
    raise SystemExit("bffBaseUrl mismatch")
ref = cfg.get("configRef")
if not isinstance(ref, str) or not ref:
    raise SystemExit("configRef missing")
if want_ref and ref != want_ref:
    raise SystemExit(f"configRef {ref} != expected {want_ref}")
ident = cfg.get("gcpIdentity")
if not isinstance(ident, dict) or not all(isinstance(ident.get(k), str) and ident[k] for k in ("apiKey", "projectId", "authDomain", "issuer", "audience")):
    raise SystemExit("gcpIdentity incomplete")
sha = cfg.get("configSha256")
if sha is not None:
    body = {k: v for k, v in cfg.items() if k != "configSha256"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
    if sha != "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest():
        raise SystemExit("configSha256 is not truthful")
PY
}

verify_public_fe_bff_pair() {
  # verify_public_fe_bff_pair <port> <expected_fe_sha> <expected_bff_image> [expected_rel_id] [expected_fe_commit] [expected_bff_commit] [expected_singleton_image]
  local port="$1" want_fe="$2" want_bff="$3" want_rel="${4:-}" want_fe_c="${5:-}" want_bff_c="${6:-}" want_simg="${7:-}"
  local color c img fe sc simg=""
  color="$(color_for_port "$port")" || fail BAD_PORT "port ${port} is neither blue nor green"
  c="$(container_of "operator-bff-${color}")"; [[ -n "$c" ]] || fail IDENTITY "active container not found on ${color}"
  img="$(run_priv "$DOCKER" inspect -f '{{.Config.Image}}' "$c" 2>/dev/null | tr -d '[:space:]')" || fail IDENTITY "inspect failed"
  [[ -n "$img" ]] || fail IDENTITY "empty container image"
  fe="$(cat "${PROD_ROOT}/fe/current/.artifact_sha256" 2>/dev/null | tr -d '[:space:]')" || fail IDENTITY "FE identity file missing"
  [[ -n "$fe" ]] || fail IDENTITY "FE identity file empty"

  if [[ -n "$want_simg" || "$(singleton_count)" -gt 0 ]]; then
    [[ "$(singleton_count)" == "1" ]] || fail IDENTITY "singleton owner count is $(singleton_count), expected 1"
    sc="$(container_of lifecycle-projector)"; [[ -n "$sc" ]] || fail IDENTITY "singleton container not found"
    simg="$(run_priv "$DOCKER" inspect -f '{{.Config.Image}}' "$sc" 2>/dev/null | tr -d '[:space:]')" || fail IDENTITY "singleton inspect failed"
  fi

  mkdir -p "${PROD_ROOT}/state"
  local fe_dep_file="${PROD_ROOT}/state/fe-deployment.readback"
  local fe_hdrs bff_ver
  fe_hdrs="$("$CURL" -fsS -m 10 -D - -o "$fe_dep_file" "${PROD_FE_BASE_URL:-$FE_ORIGIN}/deployment.json" 2>/dev/null)" \
    || fail IDENTITY "cannot fetch public FE /deployment.json"
  grep -qi '^cache-control:.*no-store' <<<"$fe_hdrs" || fail IDENTITY "public FE /deployment.json is not served no-store"

  [[ "$(http_code "${PROD_BFF_BASE_URL:-$BFF_ORIGIN}/bff/readyz")" == "200" ]] \
    || fail IDENTITY "public BFF /bff/readyz did not return 200"
  bff_ver="$("$CURL" -fsS -m 10 "${PROD_BFF_BASE_URL:-$BFF_ORIGIN}/bff/version" 2>/dev/null)" \
    || fail IDENTITY "cannot fetch public BFF /bff/version"

  python3 - "$fe" "$img" "$simg" "$fe_dep_file" "$bff_ver" "$want_fe" "$want_bff" "$want_rel" "$want_fe_c" "$want_bff_c" "$want_simg" <<'PY' \
    || fail IDENTITY "public hosted FE/BFF identity mismatch"
import json, sys
from pathlib import Path

fe_local, img_local, simg_local, fe_dep_path, bff_ver_raw = sys.argv[1:6]
want_fe, want_bff, want_rel, want_fe_c, want_bff_c, want_simg = sys.argv[6:12]

fe_dep = json.loads(Path(fe_dep_path).read_text(encoding="utf-8"))
bff_ver = json.loads(bff_ver_raw)

pub_fe_sha = str(fe_dep.get("frontend_artifact_sha256") or "").strip()
pub_fe_commit = str(fe_dep.get("frontend_commit") or fe_dep.get("frontend_commit_sha") or "").strip()
pub_bff_commit_from_fe = str(fe_dep.get("backend_commit") or fe_dep.get("backend_commit_sha") or "").strip()
pub_bff_img_from_fe = str(fe_dep.get("operator_bff_image") or "").strip()
pub_rel_id = str(fe_dep.get("release_id") or "").strip()

pub_bff_commit = str(bff_ver.get("source_commit_sha") or bff_ver.get("backend_commit") or bff_ver.get("commit") or "").strip()
pub_bff_digest = str(bff_ver.get("image_digest") or bff_ver.get("operator_bff_digest") or bff_ver.get("operator_bff_image") or "").strip()
pub_bff_rel = str(bff_ver.get("release_id") or "").strip()

if pub_fe_sha != fe_local:
    raise SystemExit(f"public FE artifact sha {pub_fe_sha} != local {fe_local}")
if pub_bff_img_from_fe and pub_bff_img_from_fe != img_local:
    raise SystemExit(f"public FE operator_bff_image {pub_bff_img_from_fe} != local {img_local}")

local_digest = "sha256:" + img_local.split("@sha256:", 1)[1] if "@sha256:" in img_local else img_local
if pub_bff_digest not in (local_digest, img_local):
    raise SystemExit(f"public BFF digest {pub_bff_digest} != local {local_digest}")

if not pub_fe_commit or not pub_bff_commit:
    raise SystemExit("public FE or BFF commit missing")
if pub_bff_commit_from_fe and pub_bff_commit_from_fe != pub_bff_commit:
    raise SystemExit(f"FE backend_commit {pub_bff_commit_from_fe} != BFF commit {pub_bff_commit}")
if pub_rel_id and pub_bff_rel and pub_rel_id != pub_bff_rel:
    raise SystemExit(f"FE release_id {pub_rel_id} != BFF release_id {pub_bff_rel}")

if want_fe and fe_local != want_fe:
    raise SystemExit(f"FE sha {fe_local} != expected {want_fe}")
if want_bff and img_local != want_bff:
    raise SystemExit(f"BFF image {img_local} != expected {want_bff}")
if want_rel and pub_rel_id and pub_rel_id != want_rel:
    raise SystemExit(f"release_id {pub_rel_id} != expected {want_rel}")
if want_fe_c and pub_fe_commit != want_fe_c:
    raise SystemExit(f"FE commit {pub_fe_commit} != expected {want_fe_c}")
if want_bff_c and pub_bff_commit != want_bff_c:
    raise SystemExit(f"BFF commit {pub_bff_commit} != expected {want_bff_c}")
if want_simg and simg_local != want_simg:
    raise SystemExit(f"singleton image {simg_local} != expected {want_simg}")

out = {
    "release_id": pub_rel_id or want_rel,
    "frontend_commit": pub_fe_commit,
    "frontend_artifact_sha256": fe_local,
    "backend_commit": pub_bff_commit,
    "operator_bff_image": img_local,
    "operator_bff_digest": local_digest,
    "singleton_image": simg_local,
    "public_fe_deployment_verified": True,
    "public_bff_version_verified": True,
    "observed_from_live_vm": True,
}
print(json.dumps(out, sort_keys=True))
PY
}

case "$STEP" in
  snapshot_preflight)
    run_priv "$MOUNTPOINT" -q "$PROD_DATA_MOUNT" || fail DATA_DISK_NOT_MOUNTED "${PROD_DATA_MOUNT} is not a mounted data disk"
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

  prepull_images)
    release_env
    for img in "$PANTHEON_OPERATOR_BFF_IMAGE" "$PANTHEON_RUNTIME_MANAGER_IMAGE" \
               "$PANTHEON_GOVERNANCE_IMAGE" "$PANTHEON_DEPLOYMENT_IMAGE" \
               "$PANTHEON_LIFECYCLE_PROJECTOR_IMAGE" $(owner_images); do
      run_priv "$DOCKER" pull "$img" >/dev/null 2>&1 || fail IMAGE_PULL_FAILED "failed to pull digest-pinned image ${img}"
      run_priv "$DOCKER" image inspect "$img" >/dev/null 2>&1 || fail IMAGE_INSPECT_FAILED "pulled image ${img} failed inspect"
    done
    if [[ "${PROD_BOOTSTRAP_FIRST_RELEASE:-false}" != "true" ]] && [[ -f "${PROD_ROOT}/state/baseline.json" || -f "${PROD_ROOT}/state/journal/${RELEASE_ID}/baseline_release.json" ]]; then
      baseline_env
      for img in "$PANTHEON_OPERATOR_BFF_IMAGE" "$PANTHEON_RUNTIME_MANAGER_IMAGE" \
                 "$PANTHEON_GOVERNANCE_IMAGE" "$PANTHEON_DEPLOYMENT_IMAGE" \
                 "$PANTHEON_LIFECYCLE_PROJECTOR_IMAGE" $(owner_images); do
        run_priv "$DOCKER" pull "$img" >/dev/null 2>&1 || fail IMAGE_PULL_FAILED "failed to pull baseline image ${img}"
        run_priv "$DOCKER" image inspect "$img" >/dev/null 2>&1 || fail IMAGE_INSPECT_FAILED "baseline image ${img} failed inspect"
      done
    fi
    printf '{"status":"images_prepulled","release_id":"%s"}\n' "$RELEASE_ID"
    ;;

  baseline_health)
    wait_until "${PROD_BASELINE_READY_TRIES:-30}" "${PROD_BASELINE_READY_INTERVAL:-2}" require_ready "$PROD_ACTIVE_PORT" || fail BASELINE_UNHEALTHY "baseline on port ${PROD_ACTIVE_PORT:-?} not ready"
    if [[ -f "${PROD_ROOT}/state/journal/${RELEASE_ID}/baseline_release.json" ]]; then
      baseline_env
      verify_runtime_config_internal "$BASELINE_RELEASE_ID"
      verify_public_fe_bff_pair "$PROD_ACTIVE_PORT" "$BASELINE_FE_SHA" "$PANTHEON_OPERATOR_BFF_IMAGE" \
        "$BASELINE_RELEASE_ID" "$BASELINE_FE_COMMIT" "$BASELINE_BFF_COMMIT" "$PANTHEON_LIFECYCLE_PROJECTOR_IMAGE" >/dev/null
    fi
    ;;

  start_green)
    port="${1:-}"; image="${2:-}"; color="$(color_for_port "$port")" || fail BAD_PORT "port ${port} is neither blue nor green"
    [[ "$port" != "${PROD_ACTIVE_PORT:-}" ]] || fail PORT_IN_USE "target port equals the active baseline port"
    release_env
    [[ "$PANTHEON_OPERATOR_BFF_IMAGE" == "$image" ]] || fail IMAGE_MISMATCH "controller image differs from manifest"
    # Acquisition only: owners are started if absent but an already-running owner (baseline) is never recreated here.
    compose "${CORE_PROFILES[@]}" up -d --no-build --no-recreate runtime-manager governance deployment $PRODUCT_CORE_SERVICES || fail OWNER_CLOSURE "core/product owner closure not running"
    compose --profile "$color" up -d --no-build "operator-bff-${color}" || fail GREEN_START "compose failed to start ${color}"
    wait_until "${PROD_GREEN_READY_TRIES:-30}" "${PROD_GREEN_READY_INTERVAL:-2}" require_live "$port" && exit 0
    fail GREEN_NOT_READY "${color} did not become live on ${port}"
    ;;

  stop_green)
    release_env
    color="$(color_for_port "${PROD_TARGET_PORT:-}")" || fail BAD_PORT "no target port"
    compose --profile "$color" rm -fsv "operator-bff-${color}" || fail STOP_GREEN "could not stop ${color}"
    ;;

  gate)
    name="${1:-}"; port="${2:-}"
    release_env
    case "$name" in
      health) require_live "$port" || fail GATE_HEALTH "livez not 200 on ${port}" ;;
      candidate_ready)
        # Post singleton hand-off: candidate readiness now includes the candidate projector controller sha.
        wait_until "${PROD_CANDIDATE_READY_TRIES:-30}" "${PROD_CANDIDATE_READY_INTERVAL:-2}" require_ready "$port" \
          || fail GATE_CANDIDATE_READY "candidate readyz not 200 on ${port} after singleton hand-off" ;;
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
        envs="$(run_priv "$DOCKER" inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c")" || fail GATE_READ_ONLY "inspect failed"
        for want in PANTHEON_LIVE_BROKER_ENABLED=false PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED=false PANTHEON_BFF_AUTH_STUB=false PANTHEON_BFF_AUTH_MODE=strict; do
          grep -qx "$want" <<<"$envs" || fail GATE_READ_ONLY "runtime readback lacks ${want}"
        done
        code="$(http_code -X POST "http://127.0.0.1:${port}/bff/capabilities")"
        [[ "$code" =~ ^(401|403|404|405)$ ]] || fail GATE_READ_ONLY "unauthenticated write returned ${code}" ;;
      schema)
        # Image labels are not a contract: the accepted BFF image carries no migration/compat labels. Identity is the
        # exact manifest + digest-pinned image bound to genuine live staging evidence for this same manifest.
        staging_migration_gate || fail GATE_SCHEMA "${GATE_REASON}"
        production_schema_gate || fail GATE_SCHEMA "${GATE_REASON}" ;;
      old_new_compat)
        staging_migration_gate || fail GATE_COMPAT "${GATE_REASON}"
        c="$(container_of "operator-bff-$(color_for_port "$port")")"; [[ -n "$c" ]] || fail GATE_COMPAT "candidate container not found"
        cimg="$(run_priv "$DOCKER" inspect -f '{{.Config.Image}}' "$c" 2>/dev/null | tr -d '[:space:]')"
        [[ "$cimg" == "$(manifest_field backend.images.operator_bff)" ]] || fail GATE_COMPAT "running candidate image '${cimg}' != manifest operator_bff image"
        if [[ "${PROD_BOOTSTRAP_FIRST_RELEASE:-false}" == "true" ]]; then
          # Explicit first release has no prior baseline application; validate candidate and database without inventing old app.
          [[ ! -f "${PROD_ROOT}/state/baseline.json" ]] || fail GATE_COMPAT "bootstrap declared while retained baseline exists"
          production_schema_gate || fail GATE_COMPAT "${GATE_REASON}"
          require_live "$port" || fail GATE_COMPAT "bootstrap candidate not live on ${port}"
        else
          # Two-release path: the old baseline is fully ready (it still owns the singleton) and the new candidate is
          # live against the same database; candidate readiness is proven after the singleton hand-off.
          # The live DB must already be at the exact staging-verified migration set (old app still ready on it).
          production_schema_gate || fail GATE_COMPAT "${GATE_REASON}"
          require_ready "$PROD_ACTIVE_PORT" && require_live "$port" || fail GATE_COMPAT "old is not ready or new is not live"
        fi ;;
      *) fail UNKNOWN_GATE "unknown gate '${name}'" ;;
    esac
    ;;

  migrate)
    # Out-of-band forward migration with the Stage-qualified versioned SQL (expand only, autocommit per file like
    # Stage's psql -f). Same verified bytes, same set identity; idempotent SQL. Never touches the data disk itself.
    release_env
    mig_out="$(python3 -I "$MIGRATION_HELPER" migration verify "$RELEASE_MANIFEST_PATH" "$(migration_dir)" 2>&1)" || fail MIGRATION_SPEC "${mig_out}"
    # Base schema first: the product's own idempotent substrate initializer (scripts/db_migrate.sh --print-sql, shipped in
    # the same immutable material dir) creates public.telemetry_events (+ingest cursor) and loop_controller_records, which
    # the Stage-qualified expand SQL does not. Same psql transport as scripts/bootstrap.sh. Its sha256 is journaled.
    substrate="$(migration_dir)/db_migrate.sh"
    [[ -s "$substrate" ]] || fail MIGRATION_SUBSTRATE "base-schema initializer ${substrate} missing from the migration material"
    substrate_sql="$(bash "$substrate" --print-sql)" && [[ -n "$substrate_sql" ]] || fail MIGRATION_SUBSTRATE "db_migrate.sh --print-sql produced no SQL"
    compose exec -T postgres sh -c 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -' \
      <<<"$substrate_sql" >/dev/null || fail MIGRATION_SUBSTRATE_FAILED "base-schema initialization failed"
    mkdir -p "${PROD_ROOT}/state"
    printf '%s\n' "$(sha256sum "$substrate" | cut -d' ' -f1)" > "${PROD_ROOT}/state/substrate-${RELEASE_ID}.sha256"
    while IFS= read -r mig_name; do
      compose exec -T postgres sh -c 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -' \
        < "$(migration_dir)/migrations/${mig_name}" >/dev/null || fail MIGRATION_SQL_FAILED "${mig_name} failed"
    done < <(python3 -I "$MIGRATION_HELPER" migration names "$RELEASE_MANIFEST_PATH" "$(migration_dir)")
    production_schema_gate || fail GATE_SCHEMA "${GATE_REASON}"
    ;;

  drain_singletons)
    release_env
    # Exactly one singleton owner: stop it before the upstream switch.
    compose "${CORE_PROFILES[@]}" rm -fsv lifecycle-projector $PRODUCT_WORKER_SERVICE || fail DRAIN "could not stop singleton owner"
    [[ "$(singleton_count)" == "0" && "$(worker_count)" == "0" ]] || fail DRAIN "singleton owner still present after drain"
    ;;

  activate_singletons)
    release_env
    compose "${CORE_PROFILES[@]}" up -d --no-build runtime-manager governance deployment $PRODUCT_CORE_SERVICES || fail ACTIVATE "could not update core/product owner closure"
    compose "${CORE_PROFILES[@]}" up -d --no-build --no-recreate lifecycle-projector $PRODUCT_WORKER_SERVICE || fail ACTIVATE "could not start singleton owners"
    [[ "$(singleton_count)" == "1" ]] || fail ACTIVATE "singleton owner count is $(singleton_count), expected exactly 1"
    verify_product_owner_images || fail ACTIVATE "${OWNER_REASON}"
    sc="$(container_of lifecycle-projector)"; [[ -n "$sc" ]] || fail ACTIVATE "singleton container not found"
    simg="$(run_priv "$DOCKER" inspect -f '{{.Config.Image}}' "$sc" 2>/dev/null | tr -d '[:space:]')" || fail ACTIVATE "singleton inspect failed"
    [[ "$simg" == "$PANTHEON_LIFECYCLE_PROJECTOR_IMAGE" ]] || fail ACTIVATE "singleton image '${simg}' != candidate '${PANTHEON_LIFECYCLE_PROJECTOR_IMAGE}'"
    ;;

  resume_baseline_singletons)
    baseline_env
    # A candidate projector may already own the singleton; remove it first so exactly one (baseline) remains.
    compose "${CORE_PROFILES[@]}" rm -fsv lifecycle-projector $PRODUCT_WORKER_SERVICE || fail ACTIVATE "could not stop candidate singleton owners"
    if [[ "$PROD_BASELINE_PRODUCT_OWNERS" == "absent" ]]; then
      # The retained baseline never had product owners: remove the candidate's (by compose labels; no sentinel image is started).
      remove_product_owners || fail ACTIVATE "could not remove candidate product owners absent from the baseline"
      compose "${CORE_PROFILES[@]}" up -d --no-build runtime-manager governance deployment || fail ACTIVATE "could not restore baseline core owner closure"
      compose "${CORE_PROFILES[@]}" up -d --no-build --no-recreate lifecycle-projector || fail ACTIVATE "could not start baseline singleton owner"
    else
      compose "${CORE_PROFILES[@]}" up -d --no-build runtime-manager governance deployment $PRODUCT_CORE_SERVICES || fail ACTIVATE "could not restore baseline core/product owner closure"
      compose "${CORE_PROFILES[@]}" up -d --no-build --no-recreate lifecycle-projector $PRODUCT_WORKER_SERVICE || fail ACTIVATE "could not start baseline singleton owners"
    fi
    [[ "$(singleton_count)" == "1" ]] || fail ACTIVATE "singleton owner count is $(singleton_count), expected exactly 1"
    verify_product_owner_images || fail ACTIVATE "${OWNER_REASON}"
    sc="$(container_of lifecycle-projector)"; [[ -n "$sc" ]] || fail ACTIVATE "baseline singleton container not found"
    simg="$(run_priv "$DOCKER" inspect -f '{{.Config.Image}}' "$sc" 2>/dev/null | tr -d '[:space:]')" || fail ACTIVATE "baseline singleton inspect failed"
    [[ "$simg" == "$PANTHEON_LIFECYCLE_PROJECTOR_IMAGE" ]] || fail ACTIVATE "restored singleton image '${simg}' != baseline '${PANTHEON_LIFECYCLE_PROJECTOR_IMAGE}'"
    ;;

  reload_proxy)
    run_priv "$DOCKER" exec "$CADDY_CONTAINER" caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 || fail PROXY_VALIDATE "caddy config invalid; not reloaded"
    run_priv "$DOCKER" exec "$CADDY_CONTAINER" caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 || fail PROXY_RELOAD "caddy reload failed"
    ;;

  verify_runtime_config)
    verify_runtime_config_internal "$RELEASE_ID"
    ;;

  post_identity)
    verify_public_fe_bff_pair "${PROD_TARGET_PORT:-}" "" "" "" "" "" ""
    ;;

  *) fail UNKNOWN_STEP "unknown step '${STEP}'" ;;
esac
