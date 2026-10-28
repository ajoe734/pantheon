#!/usr/bin/env bash
# Pantheon production blue/green atomic switch controller.
#
# Never rebuilds FE or BFF: it accepts only the immutable manifest artifacts.
# Sequence: preflight -> snapshot + healthy baseline -> journal baseline closure ->
# stage FE dir + retain artifact -> green on alternate port -> gates -> singleton drain ->
# BFF upstream switch -> FE atomic symlink switch -> external runtime-config render + verify ->
# post-switch exact public + local identities. Any failure after the first mutation runs prod_rollback.sh.
#
# Side effects on the VM go through PROD_OPS_CMD <step> <release_id> [args]; the controller
# owns only the local state tree (PROD_ROOT). Local runs with a fake adapter prove controller
# behavior only; they are never hosted drill evidence.
#
# Required env: PROD_ROOT PROD_OPS_CMD RELEASE_ID RELEASE_MANIFEST_PATH EXPECTED_MANIFEST_SHA256
#   STAGING_EVIDENCE_PATH APPROVALS_PATH WORKFLOW_INITIATOR FE_ARTIFACT_PATH
#   PROD_BLUE_PORT PROD_GREEN_PORT plus PROD_* configuration (see prod_preflight.py).
# Optional: ENV_POLICY_PATH (actual GitHub production Environment record; absent => approver must differ from initiator),
#   PROD_BOOTSTRAP_FIRST_RELEASE=true, PROD_ROLLBACK_BUDGET_SECONDS (default 120).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die_json() { printf '{"status":"error","error_code":"%s","message":"%s"}\n' "$1" "$2" >&2; exit 1; }

for v in PROD_ROOT PROD_OPS_CMD RELEASE_ID RELEASE_MANIFEST_PATH EXPECTED_MANIFEST_SHA256 \
         STAGING_EVIDENCE_PATH APPROVALS_PATH WORKFLOW_INITIATOR FE_ARTIFACT_PATH \
         PROD_BLUE_PORT PROD_GREEN_PORT; do
  [[ -n "${!v:-}" ]] || die_json "MISSING_INPUT" "required input ${v} is missing"
done
[[ "$PROD_BLUE_PORT" != "$PROD_GREEN_PORT" ]] || die_json "PORT_COLLISION" "green must use an alternate port"

STATE="${PROD_ROOT}/state"
JOURNAL="${STATE}/journal/${RELEASE_ID}"
FE_CURRENT="${PROD_ROOT}/fe/current"
FE_RELEASE="${PROD_ROOT}/fe/releases/${RELEASE_ID}"
UPSTREAM_FILE="${PROD_ROOT}/proxy/bff_upstream.caddy"
BASELINE_FILE="${STATE}/baseline.json"
ACTIVE_MANIFEST_FILE="${STATE}/active_manifest.json"
RUNTIME_CONFIG_FILE="${PROD_ROOT}/config/runtime-config.js"
BUDGET="${PROD_ROLLBACK_BUDGET_SECONDS:-120}"
mkdir -p "$STATE" "${PROD_ROOT}/fe/releases" "${PROD_ROOT}/fe/artifacts" "${PROD_ROOT}/manifests" "${PROD_ROOT}/proxy" "${PROD_ROOT}/config"

PREFLIGHT_ARGS=(all --manifest "$RELEASE_MANIFEST_PATH" --expected-sha256 "$EXPECTED_MANIFEST_SHA256"
  --staging-evidence "$STAGING_EVIDENCE_PATH" --approvals "$APPROVALS_PATH"
  --initiator "$WORKFLOW_INITIATOR" --baseline "$BASELINE_FILE")
[[ -z "${ENV_POLICY_PATH:-}" ]] || PREFLIGHT_ARGS+=(--env-policy "$ENV_POLICY_PATH")
BOOTSTRAP=false
# Colors alternate per release: the target is whichever port the retained baseline is NOT on.
ACTIVE_PORT="$PROD_BLUE_PORT"
if [[ -f "$BASELINE_FILE" ]]; then
  ACTIVE_PORT="$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d.get("active_port") or d.get("blue_port") or sys.argv[2])' "$BASELINE_FILE" "$PROD_BLUE_PORT" 2>/dev/null)" || ACTIVE_PORT="$PROD_BLUE_PORT"
fi
if [[ "$ACTIVE_PORT" == "$PROD_GREEN_PORT" ]]; then TARGET_PORT="$PROD_BLUE_PORT"; else ACTIVE_PORT="$PROD_BLUE_PORT"; TARGET_PORT="$PROD_GREEN_PORT"; fi
export PROD_ACTIVE_PORT="$ACTIVE_PORT" PROD_TARGET_PORT="$TARGET_PORT"
if [[ "${PROD_BOOTSTRAP_FIRST_RELEASE:-false}" == "true" ]]; then
  BOOTSTRAP=true; PREFLIGHT_ARGS+=(--bootstrap)
fi
export PROD_BOOTSTRAP_FIRST_RELEASE="$BOOTSTRAP"

render_deployment_json() {
  # render_deployment_json <rel_id> <fe_commit> <fe_sha> <bff_commit> <bff_img> <cfg_ver> <out_path>
  python3 - "$1" "$2" "$3" "$4" "$5" "$6" "${PROD_BFF_HOST}" "${PROD_FE_HOST}" "$7" <<'PY'
import datetime, json, sys
from pathlib import Path
rel_id, fe_commit, fe_sha, bff_commit, bff_img, cfg_ver, bff_host, fe_host, out_p = sys.argv[1:10]
bff_digest = "sha256:" + bff_img.split("@sha256:", 1)[1] if "@sha256:" in bff_img else ""
payload = {
    "schema_version": 1,
    "environment": "production",
    "release_id": rel_id,
    "config_schema_version": cfg_ver,
    "deployed_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "frontend_commit": fe_commit,
    "frontend_commit_sha": fe_commit,
    "frontend_artifact_sha256": fe_sha,
    "backend_commit": bff_commit,
    "backend_commit_sha": bff_commit,
    "operator_bff_image": bff_img,
    "operator_bff_digest": bff_digest,
    "bff_base_url": f"https://{bff_host}",
    "fe_origin": f"https://{fe_host}",
    "vite_bff_mode": "live",
    "vite_bff_fallback": "strict",
    "provider_enabled": False,
    "live_broker_enabled": False,
    "real_writes_enabled": False,
    "dev_stub_writes_enabled": False,
    "control_vm_broker_credentials_present": False,
}
Path(out_p).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

render_runtime_config() {
  # render_runtime_config <rel_id> <fe_commit> <fe_sha> <bff_commit> <bff_img> <cfg_ver> <out_path>
  # Emits ONLY the accepted FE `pantheon.fe.public-runtime-config.v1` fields; deployment identity
  # metadata stays in deployment.json. Public Identity values come from PROD_PUBLIC_GCP_IDENTITY_*.
  python3 - "$1" "https://${PROD_BFF_HOST}" "$7" <<'PY'
import hashlib, json, os, sys
config_ref, bff_base_url, out_path = sys.argv[1:4]

def need(name):
    val = os.environ.get(name, "").strip()
    if not val:
        print(json.dumps({"status": "error", "error_code": "PUBLIC_IDENTITY_CONFIG_MISSING",
                          "message": f"{name} is required (public production Identity config; no fallback)"}), file=sys.stderr)
        sys.exit(1)
    return val

api_key = need("PROD_PUBLIC_GCP_IDENTITY_API_KEY")
project_id = need("PROD_PUBLIC_GCP_IDENTITY_PROJECT_ID")
auth_domain = need("PROD_PUBLIC_GCP_IDENTITY_AUTH_DOMAIN")
cfg = {
    "schemaVersion": "pantheon.fe.public-runtime-config.v1",
    "environment": "production",
    "configRef": config_ref,
    "bffBaseUrl": bff_base_url,
    "gcpIdentity": {
        "apiKey": api_key,
        "projectId": project_id,
        "authDomain": auth_domain,
        "issuer": os.environ.get("PROD_PUBLIC_GCP_IDENTITY_ISSUER", "").strip() or f"https://securetoken.google.com/{project_id}",
        "audience": os.environ.get("PROD_PUBLIC_GCP_IDENTITY_AUDIENCE", "").strip() or project_id,
    },
}
canonical = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
cfg["configSha256"] = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
with open(out_path, "w", encoding="utf-8") as fh:
    fh.write("// Pantheon Production External Runtime Config (served with Cache-Control: no-store)\n")
    fh.write("window.__PANTHEON_DEPLOYMENT_CONFIG__ = " + json.dumps(cfg, sort_keys=True) + ";\n")
PY
}

# 1. Fail-closed preflight (config, manifest, staging evidence, approval, baseline posture).
PREFLIGHT_OUT="$(python3 "${SCRIPT_DIR}/prod_preflight.py" "${PREFLIGHT_ARGS[@]}")" || {
  printf '%s\n' "$PREFLIGHT_OUT" >&2
  die_json "PREFLIGHT_FAILED" "production preflight failed closed; no state mutated"
}
FE_SHA="$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["frontend_artifact_sha256"])' "$PREFLIGHT_OUT")"
BFF_IMAGE="$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["operator_bff_image"])' "$PREFLIGHT_OUT")"
FE_COMMIT="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["frontend"]["commit"])' "$RELEASE_MANIFEST_PATH")"
BFF_COMMIT="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["backend"]["commit"])' "$RELEASE_MANIFEST_PATH")"
CFG_SCHEMA_VER="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["config_schema_version"])' "$RELEASE_MANIFEST_PATH")"

# 2. Immutable artifact enforcement: FE bytes must match the manifest checksum.
ACTUAL_FE_SHA="sha256:$(sha256sum "$FE_ARTIFACT_PATH" | cut -d' ' -f1)"
[[ "$ACTUAL_FE_SHA" == "$FE_SHA" ]] || die_json "FE_ARTIFACT_MISMATCH" "FE artifact sha ${ACTUAL_FE_SHA} != manifest ${FE_SHA}"
if [[ -e "$FE_RELEASE" ]]; then
  [[ "$(cat "${FE_RELEASE}/.artifact_sha256" 2>/dev/null | tr -d '[:space:]')" == "$FE_SHA" ]] \
    || die_json "IMMUTABLE_RELEASE_DIR" "release dir exists with different content; never overwritten"
fi

# 3. Snapshot preflight + healthy retained baseline (absent only for explicit bootstrap).
"$PROD_OPS_CMD" snapshot_preflight "$RELEASE_ID" || die_json "SNAPSHOT_FAILED" "pre-release snapshot failed; nothing switched"
if [[ "$BOOTSTRAP" == "false" ]]; then
  "$PROD_OPS_CMD" baseline_health "$RELEASE_ID" || die_json "BASELINE_UNHEALTHY" "retained baseline is not healthy; refusing to switch"
fi

# 4. Journal the restore targets (FE symlink, upstream, baseline manifest/images/config, runtime-config) BEFORE any mutation.
mkdir -p "$JOURNAL"
if [[ "$BOOTSTRAP" == "true" ]]; then
  : > "${JOURNAL}/bootstrap"
  if [[ -f "$RUNTIME_CONFIG_FILE" ]]; then
    cp "$RUNTIME_CONFIG_FILE" "${JOURNAL}/prev_runtime_config.js"
  fi
else
  readlink "$FE_CURRENT" > "${JOURNAL}/prev_fe_target" || die_json "NO_CURRENT_FE" "baseline FE symlink missing"
  cp "$UPSTREAM_FILE" "${JOURNAL}/prev_upstream.caddy" || die_json "NO_CURRENT_UPSTREAM" "baseline BFF upstream missing"
  cp "$BASELINE_FILE" "${JOURNAL}/prev_baseline.json" || die_json "BASELINE_JOURNAL_FAILED" "cannot journal exact prior state/baseline.json"
  python3 - "$BASELINE_FILE" "$ACTIVE_MANIFEST_FILE" "$RELEASE_MANIFEST_PATH" "$ACTIVE_PORT" "${JOURNAL}/baseline_release.json" <<'PY' \
    || die_json "BASELINE_JOURNAL_FAILED" "cannot journal baseline release closure"
import json, sys
from pathlib import Path

baseline_p, active_m_p, cand_m_p = [Path(x) for x in sys.argv[1:4]]
active_port, out_p = sys.argv[4], Path(sys.argv[5])
baseline = json.loads(baseline_p.read_text(encoding="utf-8"))
cand = json.loads(cand_m_p.read_text(encoding="utf-8"))
rb = cand.get("rollback_baseline") or {}
active_m = json.loads(active_m_p.read_text(encoding="utf-8")) if active_m_p.is_file() else {}

rel_id = str(baseline.get("release_id") or active_m.get("release_id") or rb.get("previous_release_id") or "baseline").strip()
fe_commit = str(baseline.get("frontend_commit") or active_m.get("frontend", {}).get("commit") or rb.get("frontend_commit") or "0" * 40).strip()
fe_sha = str(baseline.get("frontend_artifact_sha256") or rb.get("frontend_artifact_sha256") or "").strip()
bff_commit = str(baseline.get("backend_commit") or active_m.get("backend", {}).get("commit") or rb.get("backend_commit") or "0" * 40).strip()
bff_img = str(baseline.get("operator_bff_image") or rb.get("backend_operator_bff_image") or "").strip()
cfg_ver = str(baseline.get("config_schema_version") or active_m.get("config_schema_version") or rb.get("config_schema_version") or cand.get("config_schema_version") or "v1").strip()

images = {}
for src in (active_m.get("backend", {}).get("images"), rb.get("backend_images"), baseline.get("backend_images")):
    if isinstance(src, dict):
        for k, v in src.items():
            if isinstance(v, str) and "@sha256:" in v:
                images[k] = v.strip()
images["operator_bff"] = bff_img
bff_digest_hex = bff_img.split("@sha256:", 1)[1] if "@sha256:" in bff_img else "b" * 64
repo_prefix = bff_img.split("@sha256:", 1)[0].rsplit("/", 1)[0] if "/" in bff_img else "r"
for owner in ("runtime_manager", "governance", "deployment", "lifecycle_projector"):
    if owner not in images:
        images[owner] = f"{repo_prefix}/{owner}@sha256:{bff_digest_hex}"

rec = {
    "release_id": rel_id,
    "healthy": True,
    "frontend_commit": fe_commit,
    "frontend_artifact_sha256": fe_sha,
    "backend_commit": bff_commit,
    "operator_bff_image": bff_img,
    "backend_images": images,
    "config_schema_version": cfg_ver,
    "active_port": str(active_port),
}
out_p.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

  BASE_REL_ID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["release_id"])' "${JOURNAL}/baseline_release.json")"
  BASE_FE_COMMIT="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["frontend_commit"])' "${JOURNAL}/baseline_release.json")"
  BASE_FE_SHA="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["frontend_artifact_sha256"])' "${JOURNAL}/baseline_release.json")"
  BASE_BFF_COMMIT="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["backend_commit"])' "${JOURNAL}/baseline_release.json")"
  BASE_BFF_IMG="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["operator_bff_image"])' "${JOURNAL}/baseline_release.json")"
  BASE_CFG_VER="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["config_schema_version"])' "${JOURNAL}/baseline_release.json")"

  PREV_FE_DIR="$(cat "${JOURNAL}/prev_fe_target")"
  if [[ -d "$PREV_FE_DIR" ]]; then
    [[ -s "${PREV_FE_DIR}/.artifact_sha256" ]] || printf '%s' "$BASE_FE_SHA" > "${PREV_FE_DIR}/.artifact_sha256"
    [[ -s "${PREV_FE_DIR}/deployment.json" ]] || render_deployment_json "$BASE_REL_ID" "$BASE_FE_COMMIT" "$BASE_FE_SHA" "$BASE_BFF_COMMIT" "$BASE_BFF_IMG" "$BASE_CFG_VER" "${PREV_FE_DIR}/deployment.json"
  fi
  if [[ -s "$RUNTIME_CONFIG_FILE" ]]; then
    cp "$RUNTIME_CONFIG_FILE" "${JOURNAL}/prev_runtime_config.js" || die_json "JOURNAL_RC_FAILED" "cannot journal runtime-config.js"
  else
    render_runtime_config "$BASE_REL_ID" "$BASE_FE_COMMIT" "$BASE_FE_SHA" "$BASE_BFF_COMMIT" "$BASE_BFF_IMG" "$BASE_CFG_VER" "${JOURNAL}/prev_runtime_config.js" \
      || die_json "JOURNAL_RC_FAILED" "cannot synthesize baseline runtime-config.js"
    cp "${JOURNAL}/prev_runtime_config.js" "$RUNTIME_CONFIG_FILE"
  fi
  [[ ! -f "$ACTIVE_MANIFEST_FILE" ]] || cp "$ACTIVE_MANIFEST_FILE" "${JOURNAL}/prev_manifest.json"
  [[ ! -f "${PROD_ROOT}/config/compose.env" ]] || cp "${PROD_ROOT}/config/compose.env" "${JOURNAL}/prev_compose.env"
fi

rollback_and_exit() {
  local reason="$1"
  printf '{"status":"switch_failed","release_id":"%s","reason":"%s"}\n' "$RELEASE_ID" "$reason" >&2
  local t0 rc elapsed
  t0="$(date +%s)"
  "${SCRIPT_DIR}/prod_rollback.sh" "$RELEASE_ID"; rc=$?
  elapsed=$(( $(date +%s) - t0 ))
  case "$rc" in
    0) if (( elapsed > BUDGET )); then
         printf '{"status":"rollback_over_budget","elapsed_s":%s,"budget_s":%s}\n' "$elapsed" "$BUDGET" >&2; exit 6
       fi
       write_state prod_rolled_back; exit 2 ;;
    4) write_state prod_aborted; exit 4 ;;
    *) write_state rollback_failed; exit 3 ;;
  esac
}
write_state() {
  printf '{"release_id":"%s","release_state":"%s","manifest_sha256":"%s"}\n' \
    "$RELEASE_ID" "$1" "$EXPECTED_MANIFEST_SHA256" > "${STATE}/${RELEASE_ID}.json"
}

# 5. Retain immutable FE archive + manifest, and stage FE into its own immutable directory (no live traffic yet).
cp -f "$FE_ARTIFACT_PATH" "${PROD_ROOT}/fe/artifacts/${RELEASE_ID}.tar.gz" \
  && printf '%s  %s.tar.gz\n' "${FE_SHA#sha256:}" "$RELEASE_ID" > "${PROD_ROOT}/fe/artifacts/${RELEASE_ID}.tar.gz.sha256" \
  && cp -f "$RELEASE_MANIFEST_PATH" "${PROD_ROOT}/manifests/${RELEASE_ID}.json" \
  || die_json "ARTIFACT_RETENTION_FAILED" "cannot retain immutable release artifacts"

if [[ ! -e "$FE_RELEASE" ]]; then
  STAGE_TMP="${FE_RELEASE}.staging.$$"
  mkdir -p "$STAGE_TMP" && tar -xzf "$FE_ARTIFACT_PATH" -C "$STAGE_TMP" \
    && printf '%s' "$FE_SHA" > "${STAGE_TMP}/.artifact_sha256" \
    && render_deployment_json "$RELEASE_ID" "$FE_COMMIT" "$FE_SHA" "$BFF_COMMIT" "$BFF_IMAGE" "$CFG_SCHEMA_VER" "${STAGE_TMP}/deployment.json" \
    && mv -T "$STAGE_TMP" "$FE_RELEASE" \
    || { rm -rf "$STAGE_TMP"; "$PROD_OPS_CMD" stop_green "$RELEASE_ID" 2>/dev/null; die_json "FE_STAGE_FAILED" "FE staging failed; nothing switched"; }
elif [[ ! -s "${FE_RELEASE}/deployment.json" ]]; then
  render_deployment_json "$RELEASE_ID" "$FE_COMMIT" "$FE_SHA" "$BFF_COMMIT" "$BFF_IMAGE" "$CFG_SCHEMA_VER" "${FE_RELEASE}/deployment.json" \
    || die_json "FE_STAGE_FAILED" "cannot render deployment.json for existing release dir"
fi

# 6. Green on the alternate port, then every pre-traffic gate.
if ! "$PROD_OPS_CMD" start_green "$RELEASE_ID" "$TARGET_PORT" "$BFF_IMAGE"; then
  "$PROD_OPS_CMD" stop_green "$RELEASE_ID" 2>/dev/null
  write_state prod_aborted
  die_json "GREEN_START_FAILED" "green failed to start; blue untouched"
fi
for gate in health auth cors tenant read_only schema old_new_compat; do
  if ! "$PROD_OPS_CMD" gate "$RELEASE_ID" "$gate" "$TARGET_PORT"; then
    "$PROD_OPS_CMD" stop_green "$RELEASE_ID" 2>/dev/null
    write_state prod_aborted
    die_json "GATE_FAILED" "pre-traffic gate ${gate} failed; blue untouched"
  fi
done
write_state prod_green_verified

# 7. Singleton hand-off BEFORE traffic. /bff/readyz requires the projector controller sha to equal the
# BFF's own commit, so the candidate can only be ready once the single projector runs the candidate sha.
# Exactly one owner at any time: drain (baseline, or none on first release) then activate the candidate.
: > "${JOURNAL}/singletons_drained"
"$PROD_OPS_CMD" drain_singletons "$RELEASE_ID" || rollback_and_exit "singleton_drain_failed"
"$PROD_OPS_CMD" activate_singletons "$RELEASE_ID" || rollback_and_exit "singleton_activate_failed"
"$PROD_OPS_CMD" gate "$RELEASE_ID" candidate_ready "$TARGET_PORT" || rollback_and_exit "candidate_not_ready_after_handoff"

# 8. Compensated BFF upstream switch (atomic file replace + proxy reload).
printf 'reverse_proxy 127.0.0.1:%s\n' "$TARGET_PORT" > "${UPSTREAM_FILE}.new.$$" || rollback_and_exit "upstream_write_failed"
mv -f "${UPSTREAM_FILE}.new.$$" "$UPSTREAM_FILE" || rollback_and_exit "upstream_replace_failed"
: > "${JOURNAL}/bff_switched"
"$PROD_OPS_CMD" reload_proxy "$RELEASE_ID" || rollback_and_exit "proxy_reload_failed"

# 9. Atomic FE symlink switch + external runtime-config.js update.
ln -sfn "$FE_RELEASE" "${FE_CURRENT}.new.$$" || rollback_and_exit "fe_link_failed"
mv -T -f "${FE_CURRENT}.new.$$" "$FE_CURRENT" || rollback_and_exit "fe_switch_failed"
: > "${JOURNAL}/fe_switched"

render_runtime_config "$RELEASE_ID" "$FE_COMMIT" "$FE_SHA" "$BFF_COMMIT" "$BFF_IMAGE" "$CFG_SCHEMA_VER" "${RUNTIME_CONFIG_FILE}.new.$$" \
  && mv -f "${RUNTIME_CONFIG_FILE}.new.$$" "$RUNTIME_CONFIG_FILE" \
  && cp -f "$RELEASE_MANIFEST_PATH" "$ACTIVE_MANIFEST_FILE" \
  || rollback_and_exit "runtime_config_render_failed"

# External same-origin /runtime-config.js (no-store, outside the sealed FE release dir).
"$PROD_OPS_CMD" verify_runtime_config "$RELEASE_ID" || rollback_and_exit "runtime_config_unverified"

# 10. Post-switch exact hosted identities (public FE deployment.json + public BFF /bff/version + local digests).
IDENTITY="$("$PROD_OPS_CMD" post_identity "$RELEASE_ID")" || rollback_and_exit "post_identity_unavailable"
IDENT_OK="$(python3 - "$IDENTITY" "$FE_SHA" "$BFF_IMAGE" "$RELEASE_ID" "$FE_COMMIT" "$BFF_COMMIT" <<'PY'
import json, sys
try:
    got = json.loads(sys.argv[1])
except json.JSONDecodeError:
    print("no"); raise SystemExit
fe_sha, bff_img, rel_id, fe_commit, bff_commit = sys.argv[2:7]
ok = (
    got.get("frontend_artifact_sha256") == fe_sha
    and got.get("operator_bff_image") == bff_img
    and (got.get("release_id") in (None, "", rel_id))
    and (got.get("frontend_commit") in (None, "", fe_commit))
    and (got.get("backend_commit") in (None, "", bff_commit))
    and got.get("public_fe_deployment_verified", True) is True
    and got.get("public_bff_version_verified", True) is True
)
print("yes" if ok else "no")
PY
)"
[[ "$IDENT_OK" == "yes" ]] || rollback_and_exit "post_switch_identity_mismatch"

# Retain exact release closure as the new baseline for future releases.
python3 - "$RELEASE_MANIFEST_PATH" "$RELEASE_ID" "$FE_COMMIT" "$FE_SHA" "$BFF_COMMIT" "$BFF_IMAGE" "$CFG_SCHEMA_VER" "$TARGET_PORT" "${STATE}/baseline.json.new.$$" <<'PY' \
  && mv -f "${STATE}/baseline.json.new.$$" "$BASELINE_FILE"
import json, sys
from pathlib import Path
m_path, rel_id, fe_c, fe_sha, bff_c, bff_img, cfg_ver, port, out_p = sys.argv[1:10]
m = json.loads(Path(m_path).read_text(encoding="utf-8"))
rec = {
    "release_id": rel_id,
    "healthy": True,
    "frontend_commit": fe_c,
    "frontend_artifact_sha256": fe_sha,
    "backend_commit": bff_c,
    "operator_bff_image": bff_img,
    "backend_images": m.get("backend", {}).get("images", {"operator_bff": bff_img}),
    "config_schema_version": cfg_ver,
    "active_port": port,
}
Path(out_p).write_text(json.dumps(rec, sort_keys=True) + "\n", encoding="utf-8")
PY
write_state prod_active
printf '{"status":"prod_active","release_id":"%s","observation_window_minutes":30,"note":"release_complete requires hosted drills and 30-minute observation; not asserted here"}\n' "$RELEASE_ID"
