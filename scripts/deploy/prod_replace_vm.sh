#!/usr/bin/env bash
# Pantheon production fresh-boot control VM replacement controller (ENV-PROD-CONTROL-IMPL-001).
#
# Replaces the production control VM (PROD_VM_NAME) from a >= e2-standard-8 Debian 12 instance
# template while preserving the existing persistent data disk (PROD_DATA_DISK), the reserved
# external static IP, Caddy ACME TLS state, all durable control-plane state under PROD_DATA_MOUNT,
# and the incumbent boot disk (detached, --no-auto-delete, pre-snapshotted) as an immediate
# rollback target.
#
# Usage: prod_replace_vm.sh [release_id]
# Exit:  0 replaced and verified, 1 refused before VM deletion, 3 replacement failed (rolled back
#        to old_boot_disk), 6 completed over RTO budget (PROD_RTO_SECONDS).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
GCLOUD="${GCLOUD_BIN:-gcloud}"

refuse() {
  printf '{"status":"refused","error_code":"%s","message":"%s"}\n' "$1" "$2" >&2
  exit 1
}

RELEASE_ID="${1:-${RELEASE_ID:-}}"
for v in RELEASE_ID PROD_GCP_PROJECT_ID PROD_GCP_ZONE PROD_VM_NAME PROD_DATA_DISK \
         PROD_ROOT PROD_DATA_MOUNT PROD_BLUE_PORT PROD_GREEN_PORT PROD_BFF_HOST \
         PROD_FE_HOST PROD_IDP_ISSUER PROD_TENANT_ID PROD_RPO_SECONDS PROD_RTO_SECONDS \
         PROD_NETWORK PROD_PUBLIC_GCP_IDENTITY_API_KEY PROD_PUBLIC_GCP_IDENTITY_PROJECT_ID \
         PROD_PUBLIC_GCP_IDENTITY_AUTH_DOMAIN; do
  [[ -n "${!v:-}" ]] || refuse MISSING_INPUT "required input ${v} is missing"
done
[[ "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]] || refuse INVALID_RELEASE_ID "release_id has invalid characters"
[[ "$PROD_RPO_SECONDS" =~ ^[0-9]+$ ]] || refuse INVALID_RPO "PROD_RPO_SECONDS must be a positive integer"
[[ "$PROD_RTO_SECONDS" =~ ^-?[0-9]+$ ]] || refuse INVALID_RTO "PROD_RTO_SECONDS must be an integer"

ACTIVE_PROJECT="${CLOUDSDK_CORE_PROJECT:-$("$GCLOUD" config get-value project 2>/dev/null || true)}"
[[ "$ACTIVE_PROJECT" == "$PROD_GCP_PROJECT_ID" ]] \
  || refuse WRONG_PROJECT "active gcloud project '${ACTIVE_PROJECT}' does not equal PROD_GCP_PROJECT_ID"

REGION="${PROD_GCP_ZONE%-*}"
PROD_CADDY_CONTAINER="${PROD_CADDY_CONTAINER:-pantheon-caddy}"
PROD_CADDY_DEFAULT_IMAGE="${PROD_CADDY_DEFAULT_IMAGE:-caddy:2.8.4@sha256:63c1eabfc22aae3fb9b8fbcefd7be7be9ec4eb9a409cb02821922e4f161dc697}"
PROD_TARGET_MACHINE_TYPE="${PROD_TARGET_MACHINE_TYPE:-e2-standard-8}"
PROD_DEFAULT_TEMPLATE_NAME="${PROD_DEFAULT_TEMPLATE_NAME:-pantheon-prod-control-template-e2s8}"
PROD_SSH_WAIT_TRIES="${PROD_SSH_WAIT_TRIES:-30}"
PROD_SSH_WAIT_INTERVAL="${PROD_SSH_WAIT_INTERVAL:-5}"
REMOTE_TMP_BASE="${PROD_REMOTE_TMP_BASE:-/tmp}"
REMOTE_STAGE_DIR="${REMOTE_TMP_BASE}/pantheon-replace-vm-${RELEASE_ID}"
FSTAB_PATH="${PROD_FSTAB_PATH:-/etc/fstab}"

python3 -I "${SCRIPT_DIR}/prod_preflight.py" vm-size --machine-type "$PROD_TARGET_MACHINE_TYPE" >/dev/null 2>&1 \
  || refuse TARGET_VM_SIZE_BELOW_GATE "target machine type ${PROD_TARGET_MACHINE_TYPE} is below e2-standard-8"

WORK_DIR="$(umask 077; mktemp -d)" || refuse MKTEMP_FAILED "cannot create temporary working directory"
cleanup_workdir() { rm -rf -- "$WORK_DIR" 2>/dev/null || true; }
trap cleanup_workdir EXIT

# 1. Inspect incumbent control VM (instance id, machineType, boot disk, attached data disk, static IP, SA, network).
INCUMBENT_JSON="${WORK_DIR}/incumbent_vm.json"
"$GCLOUD" compute instances describe "$PROD_VM_NAME" \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --format=json > "$INCUMBENT_JSON" 2>/dev/null \
  || refuse VM_DESCRIBE_FAILED "cannot describe incumbent VM ${PROD_VM_NAME}"

INSPECT_VARS="$(python3 -I - "$INCUMBENT_JSON" "$PROD_DATA_DISK" "$PROD_NETWORK" "${PROD_SUBNETWORK:-}" "${PROD_RUNTIME_SERVICE_ACCOUNT:-}" <<'PY'
import json, shlex, sys
from pathlib import Path

inst = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
want_data_disk, default_net, override_subnet, override_sa = sys.argv[2:6]

def base(val: str) -> str:
    return str(val or "").strip().rstrip("/").rsplit("/", 1)[-1]

inst_id = str(inst.get("id") or "").strip()
mt = base(inst.get("machineType"))
if not inst_id or not mt:
    raise SystemExit("MISSING_VM_IDENTITY")

boot_disk = ""
boot_size_gb = "50"
data_device_name = ""
for d in inst.get("disks") or []:
    if not isinstance(d, dict):
        continue
    src = base(d.get("source"))
    if d.get("boot") is True:
        boot_disk = src
        boot_size_gb = str(d.get("diskSizeGb") or "50").strip() or "50"
    elif src == want_data_disk:
        data_device_name = str(d.get("deviceName") or want_data_disk).strip()

if not boot_disk:
    raise SystemExit("BOOT_DISK_NOT_FOUND")
if boot_disk == want_data_disk:
    raise SystemExit("BOOT_DISK_EQUALS_DATA_DISK")
if not data_device_name:
    raise SystemExit("DATA_DISK_NOT_ATTACHED")

nics = inst.get("networkInterfaces") or []
nic = nics[0] if nics and isinstance(nics[0], dict) else {}
net = base(nic.get("network")) or default_net
subnet = override_subnet.strip() or base(nic.get("subnetwork"))
ac = (nic.get("accessConfigs") or [{}])[0]
static_ip = str((ac if isinstance(ac, dict) else {}).get("natIP") or "").strip()
if not static_ip:
    raise SystemExit("STATIC_IP_MISSING")

sas = inst.get("serviceAccounts") or []
sa_obj = sas[0] if sas and isinstance(sas[0], dict) else {}
runtime_sa = override_sa.strip() or str(sa_obj.get("email") or "").strip()
if not runtime_sa:
    raise SystemExit("RUNTIME_SA_MISSING")
scopes_list = [str(s).strip() for s in (sa_obj.get("scopes") or []) if str(s).strip()]
scopes = ",".join(scopes_list) if scopes_list else "https://www.googleapis.com/auth/cloud-platform"

tags_list = [str(t).strip() for t in ((inst.get("tags") or {}).get("items") or []) if str(t).strip()]
tags = ",".join(tags_list) if tags_list else "pantheon-iap,pantheon-https"

out = {
    "OLD_INSTANCE_ID": inst_id,
    "OLD_MACHINE_TYPE": mt,
    "OLD_BOOT_DISK": boot_disk,
    "OLD_BOOT_SIZE_GB": boot_size_gb,
    "DATA_DEVICE_NAME": data_device_name,
    "STATIC_IP": static_ip,
    "VM_NETWORK": net,
    "VM_SUBNETWORK": subnet,
    "RUNTIME_SA": runtime_sa,
    "RUNTIME_SCOPES": scopes,
    "VM_TAGS": tags,
}
for k, v in out.items():
    print(f"{k}={shlex.quote(v)}")
PY
)" || refuse VM_INSPECT_FAILED "incumbent VM inspection failed (${INSPECT_VARS:-invalid JSON})"
eval "$INSPECT_VARS"

ADDR_JSON="${WORK_DIR}/addresses.json"
"$GCLOUD" compute addresses list \
  --project "$PROD_GCP_PROJECT_ID" --filter="address=${STATIC_IP}" --format=json > "$ADDR_JSON" 2>/dev/null \
  || refuse STATIC_IP_LOOKUP_FAILED "cannot query compute addresses for ${STATIC_IP}"

python3 -I - "$ADDR_JSON" "$STATIC_IP" <<'PY' \
  || refuse STATIC_IP_NOT_RESERVED "natIP ${STATIC_IP} is not a reserved EXTERNAL static address in ${PROD_GCP_PROJECT_ID}"
import json, sys
from pathlib import Path

addrs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8") or "[]")
want_ip = sys.argv[2]
ok = any(
    isinstance(a, dict)
    and str(a.get("address") or "").strip() == want_ip
    and str(a.get("addressType") or "EXTERNAL").upper() == "EXTERNAL"
    and str(a.get("status") or "IN_USE").upper() in ("IN_USE", "RESERVED")
    for a in addrs
)
if not ok:
    raise SystemExit(1)
PY

# 2. Take READY pre-replacement snapshots of BOTH PROD_DATA_DISK and OLD_BOOT_DISK (<= 62 chars lowercase).
SNAP_VARS="$(python3 -I - "${GITHUB_RUN_ID:-}" "${GITHUB_RUN_ATTEMPT:-}" <<'PY'
import datetime, re, shlex, sys

run_id, run_att = sys.argv[1].strip(), sys.argv[2].strip()
ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dt%H%M%Sz")
suffix = f"r{run_id}-a{run_att or '1'}-{ts}" if run_id else ts
suffix = re.sub(r"[^a-z0-9-]", "", suffix.lower()).strip("-")
data_snap = f"pantheon-prod-vmrep-data-{suffix}"[:62].rstrip("-")
boot_snap = f"pantheon-prod-vmrep-boot-{suffix}"[:62].rstrip("-")
pat = re.compile(r"^[a-z]([-a-z0-9]{0,60}[a-z0-9])?$")
assert pat.fullmatch(data_snap) and pat.fullmatch(boot_snap)
print(f"DATA_SNAPSHOT={shlex.quote(data_snap)}")
print(f"BOOT_SNAPSHOT={shlex.quote(boot_snap)}")
PY
)" || refuse SNAPSHOT_NAME_INVALID "cannot construct valid pre-replacement snapshot names"
eval "$SNAP_VARS"

"$GCLOUD" compute disks snapshot "$PROD_DATA_DISK" \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --snapshot-names "$DATA_SNAPSHOT" >/dev/null 2>&1 \
  || refuse DATA_SNAPSHOT_FAILED "pre-replacement snapshot of data disk ${PROD_DATA_DISK} failed"
DATA_SNAP_STATUS="$("$GCLOUD" compute snapshots describe "$DATA_SNAPSHOT" \
  --project "$PROD_GCP_PROJECT_ID" --format='value(status)' 2>/dev/null | tr -d '[:space:]')"
[[ "$DATA_SNAP_STATUS" == "READY" ]] \
  || refuse DATA_SNAPSHOT_NOT_READY "data disk snapshot ${DATA_SNAPSHOT} status is '${DATA_SNAP_STATUS}', expected READY"

"$GCLOUD" compute disks snapshot "$OLD_BOOT_DISK" \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --snapshot-names "$BOOT_SNAPSHOT" >/dev/null 2>&1 \
  || refuse BOOT_SNAPSHOT_FAILED "pre-replacement snapshot of boot disk ${OLD_BOOT_DISK} failed"
BOOT_SNAP_STATUS="$("$GCLOUD" compute snapshots describe "$BOOT_SNAPSHOT" \
  --project "$PROD_GCP_PROJECT_ID" --format='value(status)' 2>/dev/null | tr -d '[:space:]')"
[[ "$BOOT_SNAP_STATUS" == "READY" ]] \
  || refuse BOOT_SNAPSHOT_NOT_READY "boot disk snapshot ${BOOT_SNAPSHOT} status is '${BOOT_SNAP_STATUS}', expected READY"

# 3. Assemble bundle and migrate/verify incumbent boot-disk state + Caddy/ACME onto PROD_DATA_MOUNT over IAP.
BUNDLE_DIR="${WORK_DIR}/bundle"
mkdir -p "$BUNDLE_DIR"
cp "${SCRIPT_DIR}/prod_atomic_switch.sh" "${SCRIPT_DIR}/prod_rollback.sh" "${SCRIPT_DIR}/prod_ops.sh" \
   "${SCRIPT_DIR}/prod_preflight.py" "${SCRIPT_DIR}/staging_sanitize.py" "${SCRIPT_DIR}/release_manifest.py" \
   "$BUNDLE_DIR/" || refuse BUNDLE_PREP_FAILED "cannot copy deploy scripts into bundle"
cp "${REPO_ROOT}/deploy/production/docker-compose.prod-control.yml" \
   "${REPO_ROOT}/deploy/production/Caddyfile" \
   "${REPO_ROOT}/deploy/production/bootstrap.sh" \
   "$BUNDLE_DIR/" || refuse BUNDLE_PREP_FAILED "cannot copy production compose/Caddy/bootstrap into bundle"

python3 -I - "$RELEASE_ID" "$PROD_DATA_DISK" "$DATA_SNAPSHOT" "$OLD_BOOT_DISK" "$BOOT_SNAPSHOT" > "${BUNDLE_DIR}/pre_replace_snapshot.json" <<'PY'
import datetime, json, sys
rel_id, data_disk, data_snap, boot_disk, boot_snap = sys.argv[1:6]
print(json.dumps({
    "release_id": rel_id,
    "disk": data_disk,
    "snapshot": data_snap,
    "boot_disk": boot_disk,
    "boot_snapshot": boot_snap,
    "status": "READY",
    "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, sort_keys=True))
PY

{
  if [[ -n "${PROD_REMOTE_PATH_PREFIX:-}" ]]; then
    printf 'export PATH=%q:"$PATH"\n' "${PROD_REMOTE_PATH_PREFIX}"
  fi
  for v in RELEASE_ID PROD_ROOT PROD_DATA_MOUNT PROD_BLUE_PORT PROD_GREEN_PORT \
           PROD_GCP_PROJECT_ID PROD_GCP_ZONE PROD_WIF_PROVIDER PROD_SERVICE_ACCOUNT \
           PROD_RUNTIME_SERVICE_ACCOUNT PROD_INSTANCE_TEMPLATE PROD_IAM_POLICY_REF \
           PROD_SECRETS_REF PROD_DATA_DISK PROD_NETWORK PROD_SUBNETWORK \
           PROD_BFF_HOST PROD_FE_HOST PROD_IDP_ISSUER PROD_TENANT_ID \
           PROD_RPO_SECONDS PROD_RTO_SECONDS PROD_RETENTION_DAYS \
           PROD_ONCALL_OWNER PROD_ROLLBACK_OWNER STAGING_GCP_PROJECT_ID \
           DEV_GCP_PROJECT_ID PROD_PUBLIC_GCP_IDENTITY_API_KEY \
           PROD_PUBLIC_GCP_IDENTITY_PROJECT_ID PROD_PUBLIC_GCP_IDENTITY_AUTH_DOMAIN \
           PROD_PUBLIC_GCP_IDENTITY_ISSUER PROD_PUBLIC_GCP_IDENTITY_AUDIENCE \
           VITE_BFF_REAL_WRITES WORKFLOW_INITIATOR PROD_CADDY_CONTAINER \
           PROD_CADDY_DEFAULT_IMAGE FSTAB_PATH DOCKER_BIN CURL_BIN MOUNTPOINT_BIN \
           SUDO_BIN GCLOUD_BIN FAKE_STATE BIN_LOG FAKE_READY_PORTS \
           FAKE_OSLOGIN_NONROOT PROD_BASELINE_READY_TRIES PROD_BASELINE_READY_INTERVAL \
           PROD_WORKER_CRED_TRIES PROD_WORKER_CRED_INTERVAL PROD_WORKER_HEALTH_WAIT_SECONDS \
           PROD_SECRETS_ENV_FILE PROD_POSTGRES_PASSWORD_FILE; do
    if [[ -n "${!v+x}" ]]; then
      printf 'export %s=%q\n' "$v" "${!v}"
    fi
  done
  printf 'export PROD_BOOTSTRAP_FIRST_RELEASE=false\n'
} > "${BUNDLE_DIR}/remote.env"

upload_bundle_to_vm() {
  "$GCLOUD" compute ssh "$PROD_VM_NAME" --tunnel-through-iap --quiet \
    --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" \
    --command "rm -rf ${REMOTE_STAGE_DIR} && mkdir -p ${REMOTE_STAGE_DIR} && chmod 0700 ${REMOTE_STAGE_DIR}" \
    && "$GCLOUD" compute scp --tunnel-through-iap --recurse --quiet \
      --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" \
      "$BUNDLE_DIR" "${PROD_VM_NAME}:${REMOTE_STAGE_DIR}"
}

upload_bundle_to_vm || refuse IAP_UPLOAD_FAILED "cannot upload replacement bundle to incumbent VM ${PROD_VM_NAME}"

PRE_MIGRATE_OUT="$("$GCLOUD" compute ssh "$PROD_VM_NAME" --tunnel-through-iap --quiet \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --command "set -euo pipefail
  B=${REMOTE_STAGE_DIR}/bundle
  source \"\$B/remote.env\"
  sudo install -d \"\$PROD_ROOT/bin\" \"\$PROD_DATA_MOUNT/state/snapshots\" \"\$PROD_DATA_MOUNT/config\" \"\$PROD_DATA_MOUNT/proxy/caddy-data\" \"\$PROD_DATA_MOUNT/proxy/caddy-config\"
  sudo install -m 0755 \"\$B/bootstrap.sh\" \"\$B/prod_atomic_switch.sh\" \"\$B/prod_rollback.sh\" \"\$B/prod_ops.sh\" \"\$B/prod_preflight.py\" \"\$B/staging_sanitize.py\" \"\$B/release_manifest.py\" \"\$PROD_ROOT/bin/\"
  sudo install -m 0644 \"\$B/docker-compose.prod-control.yml\" \"\$B/Caddyfile\" \"\$PROD_ROOT/bin/\"
  sudo -H -E env PROD_USE_SUDO=true PROD_REQUIRE_FULL_SECRET_KEYS=true PROD_BOOTSTRAP_SKIP_STATEFUL=true PROD_PREPULL_IMAGES=false \
    PROD_SECRETS_ENV_FILE=\"\${PROD_SECRETS_ENV_FILE:-\$PROD_DATA_MOUNT/secrets/prod.secrets.env}\" \
    PROD_POSTGRES_PASSWORD_FILE=\"\${PROD_POSTGRES_PASSWORD_FILE:-\$PROD_DATA_MOUNT/secrets/postgres_password}\" \
    PROD_COMPOSE_ENV_FILE=\"\$PROD_DATA_MOUNT/config/compose.env\" \
    \"\$PROD_ROOT/bin/bootstrap.sh\" >/dev/null
  sudo python3 -I \"\$PROD_ROOT/bin/prod_preflight.py\" boot-disk --prod-root \"\$PROD_ROOT\" --data-mount \"\$PROD_DATA_MOUNT\" >/dev/null
  sudo install -m 0644 \"\$B/pre_replace_snapshot.json\" \"\$PROD_DATA_MOUNT/state/snapshots/vm-replace-\$RELEASE_ID.json\"
  sudo python3 -I - \"\$PROD_DATA_MOUNT/state/active_manifest.json\" \"\$PROD_DATA_MOUNT/state/baseline.json\" \"\$RELEASE_ID\" <<'PY'
import json, sys
from pathlib import Path
am = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
bl = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
want = sys.argv[3]
if am.get('release_id') != want or bl.get('release_id') != want or bl.get('healthy') is not True:
    raise SystemExit(f'active release mismatch: active_manifest={am.get(\"release_id\")} baseline={bl.get(\"release_id\")} want={want}')
PY
  if ! sudo test -L \"\$PROD_DATA_MOUNT/fe/current\" && sudo test -d \"\$PROD_DATA_MOUNT/fe/releases/\$RELEASE_ID\"; then
    sudo ln -sfn \"\$PROD_ROOT/fe/releases/\$RELEASE_ID\" \"\$PROD_DATA_MOUNT/fe/current\"
  fi
  sudo test -s \"\$PROD_ROOT/fe/current/deployment.json\"
  sudo test -s \"\$PROD_ROOT/fe/current/.artifact_sha256\"
  data_uuid=\"\$(sudo findmnt -n -o UUID --target \"\$PROD_DATA_MOUNT\" 2>/dev/null | tr -d '[:space:]' || true)\"
  if [[ -z \"\$data_uuid\" ]]; then
    src_dev=\"\$(sudo findmnt -n -o SOURCE --target \"\$PROD_DATA_MOUNT\" 2>/dev/null | tr -d '[:space:]' || true)\"
    [[ -z \"\$src_dev\" ]] || data_uuid=\"\$(sudo blkid -s UUID -o value \"\$src_dev\" 2>/dev/null | tr -d '[:space:]' || true)\"
  fi
  [[ -n \"\$data_uuid\" ]] || { echo 'DATA_UUID_MISSING' >&2; exit 1; }
  printf '%s\n' \"\$data_uuid\" | sudo tee \"\$PROD_DATA_MOUNT/state/data_disk_uuid\" >/dev/null
  DOCKER=\"\${DOCKER_BIN:-docker}\"
  caddy_img=\"\$(sudo \"\$DOCKER\" inspect -f '{{.Config.Image}}' \"\$PROD_CADDY_CONTAINER\" 2>/dev/null | tr -d '[:space:]' || true)\"
  if [[ -n \"\$caddy_img\" && \"\$caddy_img\" != *@sha256:* ]]; then
    caddy_digest=\"\$(sudo \"\$DOCKER\" image inspect -f '{{index .RepoDigests 0}}' \"\$caddy_img\" 2>/dev/null | tr -d '[:space:]' || true)\"
    [[ \"\$caddy_digest\" == *@sha256:* ]] && caddy_img=\"\$caddy_digest\"
  fi
  if [[ -z \"\$caddy_img\" || \"\$caddy_img\" != *@sha256:* ]]; then
    caddy_img=\"\$PROD_CADDY_DEFAULT_IMAGE\"
  fi
  printf '%s\n' \"\$caddy_img\" | sudo tee \"\$PROD_DATA_MOUNT/proxy/caddy-image.txt\" >/dev/null
  sudo \"\$DOCKER\" cp \"\${PROD_CADDY_CONTAINER}:/data/.\" \"\$PROD_DATA_MOUNT/proxy/caddy-data/\" >/dev/null 2>&1 || true
  sudo \"\$DOCKER\" cp \"\${PROD_CADDY_CONTAINER}:/config/.\" \"\$PROD_DATA_MOUNT/proxy/caddy-config/\" >/dev/null 2>&1 || true
  sudo sync
  printf 'DATA_UUID=%s\nCADDY_IMAGE=%s\n' \"\$data_uuid\" \"\$caddy_img\"
" 2>&1)" || refuse PRE_REPLACE_MIGRATE_FAILED "incumbent VM pre-replacement migration or state verification failed: $(tr '\n"' '  ' <<<"$PRE_MIGRATE_OUT")"

DATA_UUID="$(sed -n 's/^DATA_UUID=//p' <<<"$PRE_MIGRATE_OUT" | tail -n1 | tr -d '[:space:]')"
CADDY_IMAGE="$(sed -n 's/^CADDY_IMAGE=//p' <<<"$PRE_MIGRATE_OUT" | tail -n1 | tr -d '[:space:]')"
[[ -n "$DATA_UUID" ]] || refuse DATA_UUID_MISSING "failed to capture filesystem UUID for ${PROD_DATA_MOUNT}"
[[ "$CADDY_IMAGE" == *@sha256:* ]] || refuse CADDY_IMAGE_UNPINNED "captured Caddy image '${CADDY_IMAGE}' is not digest-pinned"

# 4. Ensure >= e2-standard-8 Debian 12 instance template (create pantheon-prod-control-template-e2s8 if absent or < 8 vCPUs).
TPL_JSON="${WORK_DIR}/instance_template.json"
TARGET_TEMPLATE="${PROD_INSTANCE_TEMPLATE:-}"
TEMPLATE_READY=false
if [[ -n "$TARGET_TEMPLATE" ]]; then
  if "$GCLOUD" compute instance-templates describe "$TARGET_TEMPLATE" \
      --project "$PROD_GCP_PROJECT_ID" --format=json > "$TPL_JSON" 2>/dev/null; then
    if python3 -I "${SCRIPT_DIR}/prod_preflight.py" vm-size --instance-template "$TPL_JSON" >/dev/null 2>&1; then
      TEMPLATE_READY=true
    fi
  fi
fi

if [[ "$TEMPLATE_READY" != "true" ]]; then
  TARGET_TEMPLATE="$PROD_DEFAULT_TEMPLATE_NAME"
  if "$GCLOUD" compute instance-templates describe "$TARGET_TEMPLATE" \
      --project "$PROD_GCP_PROJECT_ID" --format=json > "$TPL_JSON" 2>/dev/null \
     && python3 -I "${SCRIPT_DIR}/prod_preflight.py" vm-size --instance-template "$TPL_JSON" >/dev/null 2>&1; then
    TEMPLATE_READY=true
  else
    SUBNET_ARGS=()
    if [[ -n "$VM_SUBNETWORK" ]]; then
      SUBNET_ARGS=(--subnet "$VM_SUBNETWORK" --region "$REGION")
    fi
    "$GCLOUD" compute instance-templates create "$TARGET_TEMPLATE" \
      --project "$PROD_GCP_PROJECT_ID" \
      --machine-type "$PROD_TARGET_MACHINE_TYPE" \
      --network "$VM_NETWORK" \
      "${SUBNET_ARGS[@]}" \
      --no-address \
      --maintenance-policy=MIGRATE \
      --provisioning-model=STANDARD \
      --service-account "$RUNTIME_SA" \
      --scopes "$RUNTIME_SCOPES" \
      --image-family=debian-12 \
      --image-project=debian-cloud \
      --boot-disk-size="${OLD_BOOT_SIZE_GB}GB" \
      --boot-disk-type=pd-balanced \
      --boot-disk-auto-delete \
      --shielded-vtpm \
      --shielded-integrity-monitoring \
      --tags "$VM_TAGS" \
      --metadata "enable-oslogin=TRUE,block-project-ssh-keys=TRUE,serial-port-enable=FALSE" \
      --labels "environment=production,managed_by=pantheon,role=prod-control" >/dev/null 2>&1 \
      || refuse TEMPLATE_CREATE_FAILED "cannot create >= e2-standard-8 instance template ${TARGET_TEMPLATE}"
    "$GCLOUD" compute instance-templates describe "$TARGET_TEMPLATE" \
      --project "$PROD_GCP_PROJECT_ID" --format=json > "$TPL_JSON" 2>/dev/null \
      || refuse TEMPLATE_DESCRIBE_FAILED "cannot describe created instance template ${TARGET_TEMPLATE}"
  fi
fi

python3 -I "${SCRIPT_DIR}/prod_preflight.py" vm-size --instance-template "$TPL_JSON" >/dev/null 2>&1 \
  || refuse TEMPLATE_SIZE_BELOW_GATE "instance template ${TARGET_TEMPLATE} is below the e2-standard-8 (8 vCPU) minimum gate"

# 5. Start RTO timer, protect BOTH old boot disk and PROD_DATA_DISK (--no-auto-delete), stop incumbent VM containers,
#    delete incumbent VM with --keep-disks=all, and arm automatic rollback to OLD_BOOT_DISK on any failure.
RTO_START_EPOCH="$(date +%s)"

"$GCLOUD" compute instances set-disk-auto-delete "$PROD_VM_NAME" \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" \
  --disk "$OLD_BOOT_DISK" --no-auto-delete >/dev/null 2>&1 \
  || refuse AUTO_DELETE_PROTECT_FAILED "cannot set --no-auto-delete on incumbent boot disk ${OLD_BOOT_DISK}"

"$GCLOUD" compute instances set-disk-auto-delete "$PROD_VM_NAME" \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" \
  --disk "$PROD_DATA_DISK" --no-auto-delete >/dev/null 2>&1 \
  || refuse AUTO_DELETE_PROTECT_FAILED "cannot set --no-auto-delete on production data disk ${PROD_DATA_DISK}"

"$GCLOUD" compute ssh "$PROD_VM_NAME" --tunnel-through-iap --quiet \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --command "set -uo pipefail
  B=${REMOTE_STAGE_DIR}/bundle
  [[ -f \"\$B/remote.env\" ]] && source \"\$B/remote.env\"
  DOCKER=\"\${DOCKER_BIN:-docker}\"
  sudo \"\$DOCKER\" rm -f \"\$PROD_CADDY_CONTAINER\" >/dev/null 2>&1 || true
  cids=\"\$(sudo \"\$DOCKER\" ps -q 2>/dev/null || true)\"
  [[ -z \"\$cids\" ]] || sudo \"\$DOCKER\" stop \$cids >/dev/null 2>&1 || true
  sudo sync
  sudo umount \"\$PROD_DATA_MOUNT\" >/dev/null 2>&1 || true
" >/dev/null 2>&1 || true

wait_for_ssh() {
  local i
  for i in $(seq 1 "$PROD_SSH_WAIT_TRIES"); do
    if "$GCLOUD" compute ssh "$PROD_VM_NAME" --tunnel-through-iap --quiet \
        --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --command "true" >/dev/null 2>&1; then
      return 0
    fi
    (( i < PROD_SSH_WAIT_TRIES )) && sleep "$PROD_SSH_WAIT_INTERVAL"
  done
  return 1
}

rollback_to_old_boot_disk() {
  local failed_stage="$1" reason="$2" rb_ok=false
  if "$GCLOUD" compute instances describe "$PROD_VM_NAME" \
      --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --format='value(name)' >/dev/null 2>&1; then
    "$GCLOUD" compute instances set-disk-auto-delete "$PROD_VM_NAME" \
      --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" \
      --disk "$PROD_DATA_DISK" --no-auto-delete >/dev/null 2>&1 || true
    "$GCLOUD" compute instances delete "$PROD_VM_NAME" \
      --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" \
      --keep-disks=all --quiet >/dev/null 2>&1 || true
  fi
  local subnet_args=()
  [[ -n "$VM_SUBNETWORK" ]] && subnet_args=(--subnet "$VM_SUBNETWORK")
  if "$GCLOUD" compute instances create "$PROD_VM_NAME" \
      --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" \
      --machine-type "$OLD_MACHINE_TYPE" \
      --network "$VM_NETWORK" "${subnet_args[@]}" \
      --address "$STATIC_IP" \
      --service-account "$RUNTIME_SA" --scopes "$RUNTIME_SCOPES" \
      --tags "$VM_TAGS" \
      --metadata "enable-oslogin=TRUE,block-project-ssh-keys=TRUE,serial-port-enable=FALSE" \
      --disk "name=${OLD_BOOT_DISK},boot=yes,mode=rw,auto-delete=no" \
      --disk "name=${PROD_DATA_DISK},device-name=${DATA_DEVICE_NAME},mode=rw,boot=no,auto-delete=no" >/dev/null 2>&1; then
    if wait_for_ssh && upload_bundle_to_vm; then
      if "$GCLOUD" compute ssh "$PROD_VM_NAME" --tunnel-through-iap --quiet \
          --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --command "set -euo pipefail
          B=${REMOTE_STAGE_DIR}/bundle
          source \"\$B/remote.env\"
          DOCKER=\"\${DOCKER_BIN:-docker}\"
          MOUNTPOINT=\"\${MOUNTPOINT_BIN:-mountpoint}\"
          sudo mkdir -p \"\$PROD_DATA_MOUNT\"
          if ! sudo \"\$MOUNTPOINT\" -q \"\$PROD_DATA_MOUNT\"; then
            sudo mount -U \"${DATA_UUID}\" \"\$PROD_DATA_MOUNT\" 2>/dev/null || sudo mount \"\$PROD_DATA_MOUNT\"
          fi
          sudo \"\$MOUNTPOINT\" -q \"\$PROD_DATA_MOUNT\"
          sudo systemctl disable --now caddy.service >/dev/null 2>&1 || true
          sudo \"\$DOCKER\" rm -f \"\$PROD_CADDY_CONTAINER\" >/dev/null 2>&1 || true
          sudo \"\$DOCKER\" run -d --name \"\$PROD_CADDY_CONTAINER\" --restart unless-stopped --network host \
            -e \"PROD_BFF_HOST=\$PROD_BFF_HOST\" -e \"PROD_FE_HOST=\$PROD_FE_HOST\" -e \"PROD_ROOT=\$PROD_ROOT\" \
            -v \"\$PROD_DATA_MOUNT:\$PROD_DATA_MOUNT\" -v \"\$PROD_ROOT:\$PROD_ROOT\" \
            -v \"\$PROD_DATA_MOUNT/proxy/Caddyfile:/etc/caddy/Caddyfile:ro\" \
            -v \"\$PROD_DATA_MOUNT/proxy/caddy-data:/data\" \
            -v \"\$PROD_DATA_MOUNT/proxy/caddy-config:/config\" \
            \"${CADDY_IMAGE}\" >/dev/null
          sudo -H -E env PROD_USE_SUDO=true \
            PROD_COMPOSE_FILE=\"\$PROD_ROOT/bin/docker-compose.prod-control.yml\" \
            PROD_COMPOSE_ENV_FILE=\"\$PROD_DATA_MOUNT/config/compose.env\" \
            RELEASE_MANIFEST_PATH=\"\$PROD_DATA_MOUNT/state/active_manifest.json\" \
            \"\$PROD_ROOT/bin/prod_ops.sh\" restore_active_release \"\$RELEASE_ID\" >/dev/null
        " >/dev/null 2>&1; then
        rb_ok=true
      fi
    fi
  fi
  python3 -I - "$failed_stage" "$reason" "$rb_ok" "$OLD_BOOT_DISK" "$PROD_DATA_DISK" "$STATIC_IP" <<'PY'
import json, sys
stage, reason, rb_ok, old_boot, data_disk, ip = sys.argv[1:7]
status = "replace_vm_rolled_back" if rb_ok == "true" else "replace_vm_rollback_failed"
print(json.dumps({
    "status": status,
    "failed_stage": stage,
    "message": reason,
    "rolled_back_to_old_boot_disk": rb_ok == "true",
    "old_boot_disk": old_boot,
    "data_disk": data_disk,
    "static_ip": ip,
}, sort_keys=True))
PY
  exit 3
}

"$GCLOUD" compute instances delete "$PROD_VM_NAME" \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" \
  --keep-disks=all --quiet >/dev/null 2>&1 \
  || rollback_to_old_boot_disk "delete_incumbent_vm" "failed to delete incumbent VM with --keep-disks=all"

for retained_disk in "$OLD_BOOT_DISK" "$PROD_DATA_DISK"; do
  "$GCLOUD" compute disks describe "$retained_disk" \
    --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --format='value(name)' >/dev/null 2>&1 \
    || rollback_to_old_boot_disk "verify_kept_disks" "retained disk ${retained_disk} missing after --keep-disks=all delete"
done

# 6. Create new PROD_VM_NAME from >= e2-standard-8 template with brand-new boot disk + existing PROD_DATA_DISK + STATIC_IP.
"$GCLOUD" compute instances create "$PROD_VM_NAME" \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" \
  --source-instance-template "$TARGET_TEMPLATE" \
  --machine-type "$PROD_TARGET_MACHINE_TYPE" \
  --address "$STATIC_IP" \
  --disk "name=${PROD_DATA_DISK},device-name=${DATA_DEVICE_NAME},mode=rw,boot=no,auto-delete=no" >/dev/null 2>&1 \
  || rollback_to_old_boot_disk "create_new_vm" "failed to create replacement VM from template ${TARGET_TEMPLATE}"

"$GCLOUD" compute instances set-disk-auto-delete "$PROD_VM_NAME" \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" \
  --disk "$PROD_DATA_DISK" --no-auto-delete >/dev/null 2>&1 \
  || rollback_to_old_boot_disk "protect_data_disk_new_vm" "cannot set --no-auto-delete on ${PROD_DATA_DISK} on replacement VM"

NEW_VM_JSON="${WORK_DIR}/new_vm.json"
"$GCLOUD" compute instances describe "$PROD_VM_NAME" \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --format=json > "$NEW_VM_JSON" 2>/dev/null \
  || rollback_to_old_boot_disk "describe_new_vm" "cannot describe replacement VM ${PROD_VM_NAME}"

NEW_VM_VARS="$(python3 -I - "$NEW_VM_JSON" "$OLD_INSTANCE_ID" "$OLD_BOOT_DISK" "$PROD_DATA_DISK" "$STATIC_IP" <<'PY'
import json, shlex, sys
from pathlib import Path

inst = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
old_id, old_boot, data_disk, want_ip = sys.argv[2:6]

def base(val: str) -> str:
    return str(val or "").strip().rstrip("/").rsplit("/", 1)[-1]

new_id = str(inst.get("id") or "").strip()
new_mt = base(inst.get("machineType"))
new_boot = ""
data_attached = False
for d in inst.get("disks") or []:
    if not isinstance(d, dict):
        continue
    src = base(d.get("source"))
    if d.get("boot") is True:
        new_boot = src
    elif src == data_disk:
        data_attached = True

nics = inst.get("networkInterfaces") or []
nic = nics[0] if nics and isinstance(nics[0], dict) else {}
ac = (nic.get("accessConfigs") or [{}])[0]
got_ip = str((ac if isinstance(ac, dict) else {}).get("natIP") or "").strip()

if not new_id or new_id == old_id:
    raise SystemExit("INSTANCE_ID_NOT_REPLACED")
if not new_boot or new_boot in (old_boot, data_disk):
    raise SystemExit("BOOT_DISK_NOT_FRESH")
if not data_attached:
    raise SystemExit("DATA_DISK_NOT_ATTACHED_ON_NEW_VM")
if got_ip != want_ip:
    raise SystemExit(f"STATIC_IP_MISMATCH:{got_ip}!={want_ip}")

print(f"NEW_INSTANCE_ID={shlex.quote(new_id)}")
print(f"NEW_MACHINE_TYPE={shlex.quote(new_mt)}")
print(f"NEW_BOOT_DISK={shlex.quote(new_boot)}")
PY
)" || rollback_to_old_boot_disk "verify_new_vm_identity" "replacement VM identity check failed (${NEW_VM_VARS:-invalid})"
eval "$NEW_VM_VARS"

NEW_VCPUS="$("$GCLOUD" compute machine-types describe "$NEW_MACHINE_TYPE" \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --format='value(guestCpus)' 2>/dev/null | tr -d '[:space:]')"
python3 -I "${SCRIPT_DIR}/prod_preflight.py" vm-size --machine-type "$NEW_MACHINE_TYPE" --vcpus "$NEW_VCPUS" >/dev/null 2>&1 \
  || rollback_to_old_boot_disk "verify_new_vm_size" "replacement VM size (${NEW_MACHINE_TYPE}, vcpus=${NEW_VCPUS}) failed >= e2-standard-8 gate"

# 7. Wait for IAP SSH, mount UUID=<DATA_UUID> at PROD_DATA_MOUNT in /etc/fstab (NEVER mkfs), run bootstrap.sh,
#    disable host systemd caddy.service, recreate pantheon-caddy with preserved ACME state, and restore active release.
wait_for_ssh || rollback_to_old_boot_disk "wait_for_ssh" "IAP SSH did not become ready on replacement VM"
upload_bundle_to_vm || rollback_to_old_boot_disk "upload_bundle_new_vm" "cannot upload bundle to replacement VM over IAP"

RESTORE_OUT="$("$GCLOUD" compute ssh "$PROD_VM_NAME" --tunnel-through-iap --quiet \
  --project "$PROD_GCP_PROJECT_ID" --zone "$PROD_GCP_ZONE" --command "set -euo pipefail
  B=${REMOTE_STAGE_DIR}/bundle
  source \"\$B/remote.env\"
  DOCKER=\"\${DOCKER_BIN:-docker}\"
  MOUNTPOINT=\"\${MOUNTPOINT_BIN:-mountpoint}\"
  FSTAB=\"\${FSTAB_PATH:-/etc/fstab}\"
  sudo mkdir -p \"\$PROD_DATA_MOUNT\" \"\$(dirname \"\$FSTAB\")\"
  if ! sudo grep -q \"UUID=${DATA_UUID}[[:space:]]\" \"\$FSTAB\" 2>/dev/null; then
    printf 'UUID=%s %s ext4 defaults,nofail 0 2\n' \"${DATA_UUID}\" \"\$PROD_DATA_MOUNT\" | sudo tee -a \"\$FSTAB\" >/dev/null
  fi
  if ! sudo \"\$MOUNTPOINT\" -q \"\$PROD_DATA_MOUNT\"; then
    sudo mount -U \"${DATA_UUID}\" \"\$PROD_DATA_MOUNT\" 2>/dev/null || sudo mount \"\$PROD_DATA_MOUNT\"
  fi
  sudo \"\$MOUNTPOINT\" -q \"\$PROD_DATA_MOUNT\"
  sudo test -s \"\$PROD_DATA_MOUNT/state/active_manifest.json\"
  sudo test -s \"\$PROD_DATA_MOUNT/state/baseline.json\"
  sudo install -d \"\$PROD_ROOT/bin\"
  sudo install -m 0755 \"\$B/bootstrap.sh\" \"\$B/prod_atomic_switch.sh\" \"\$B/prod_rollback.sh\" \"\$B/prod_ops.sh\" \"\$B/prod_preflight.py\" \"\$B/staging_sanitize.py\" \"\$B/release_manifest.py\" \"\$PROD_ROOT/bin/\"
  sudo install -m 0644 \"\$B/docker-compose.prod-control.yml\" \"\$B/Caddyfile\" \"\$PROD_ROOT/bin/\"
  sudo -H -E env PROD_USE_SUDO=true PROD_ALLOW_APT_INSTALL=true PROD_REGISTRY_AUTH=true PROD_REQUIRE_FULL_SECRET_KEYS=true \
    PROD_MACHINE_TYPE=\"${NEW_MACHINE_TYPE}\" \
    PROD_SECRETS_ENV_FILE=\"\$PROD_DATA_MOUNT/secrets/prod.secrets.env\" \
    PROD_POSTGRES_PASSWORD_FILE=\"\$PROD_DATA_MOUNT/secrets/postgres_password\" \
    PROD_COMPOSE_ENV_FILE=\"\$PROD_DATA_MOUNT/config/compose.env\" \
    RELEASE_MANIFEST_PATH=\"\$PROD_DATA_MOUNT/state/active_manifest.json\" \
    \"\$PROD_ROOT/bin/bootstrap.sh\" >/dev/null
  # Bare-Debian apt-get install of caddy starts host systemd caddy.service on port 80; disable it before starting pantheon-caddy.
  sudo systemctl disable --now caddy.service >/dev/null 2>&1 || true
  sudo systemctl stop caddy.service >/dev/null 2>&1 || true
  sudo python3 -I \"\$PROD_ROOT/bin/prod_preflight.py\" boot-disk --prod-root \"\$PROD_ROOT\" --data-mount \"\$PROD_DATA_MOUNT\" >/dev/null
  caddy_img=\"\$(sudo cat \"\$PROD_DATA_MOUNT/proxy/caddy-image.txt\" 2>/dev/null | tr -d '[:space:]' || true)\"
  [[ \"\$caddy_img\" == *@sha256:* ]] || caddy_img=\"${CADDY_IMAGE}\"
  sudo \"\$DOCKER\" image inspect \"\$caddy_img\" >/dev/null 2>&1 || sudo \"\$DOCKER\" pull \"\$caddy_img\" >/dev/null 2>&1
  sudo mkdir -p \"\$PROD_DATA_MOUNT/proxy/caddy-data\" \"\$PROD_DATA_MOUNT/proxy/caddy-config\"
  sudo \"\$DOCKER\" rm -f \"\$PROD_CADDY_CONTAINER\" >/dev/null 2>&1 || true
  sudo \"\$DOCKER\" run -d --name \"\$PROD_CADDY_CONTAINER\" --restart unless-stopped --network host \
    -e \"PROD_BFF_HOST=\$PROD_BFF_HOST\" -e \"PROD_FE_HOST=\$PROD_FE_HOST\" -e \"PROD_ROOT=\$PROD_ROOT\" \
    -v \"\$PROD_DATA_MOUNT:\$PROD_DATA_MOUNT\" -v \"\$PROD_ROOT:\$PROD_ROOT\" \
    -v \"\$PROD_DATA_MOUNT/proxy/Caddyfile:/etc/caddy/Caddyfile:ro\" \
    -v \"\$PROD_DATA_MOUNT/proxy/caddy-data:/data\" \
    -v \"\$PROD_DATA_MOUNT/proxy/caddy-config:/config\" \
    \"\$caddy_img\" >/dev/null
  sudo -H -E env PROD_USE_SUDO=true \
    PROD_COMPOSE_FILE=\"\$PROD_ROOT/bin/docker-compose.prod-control.yml\" \
    PROD_COMPOSE_ENV_FILE=\"\$PROD_DATA_MOUNT/config/compose.env\" \
    RELEASE_MANIFEST_PATH=\"\$PROD_DATA_MOUNT/state/active_manifest.json\" \
    \"\$PROD_ROOT/bin/prod_ops.sh\" restore_active_release \"\$RELEASE_ID\"
" 2>&1)" || rollback_to_old_boot_disk "restore_active_release" "fresh-boot bootstrap or restore_active_release failed: $(tr '\n"' '  ' <<<"$RESTORE_OUT")"

RESTORED_IDENTITY_JSON="$(printf '%s\n' "$RESTORE_OUT" | tail -n1)"
SNAP_SCHED_JSON="$(GCLOUD_BIN="$GCLOUD" bash "${SCRIPT_DIR}/prod_ops.sh" snapshot_schedule check "$RELEASE_ID" 2>&1)" \
  || rollback_to_old_boot_disk "snapshot_schedule_check" "data disk snapshot schedule check failed after VM replacement"

RTO_SECONDS="${PROD_REPLACE_RTO_ELAPSED_SECONDS:-$(( $(date +%s) - RTO_START_EPOCH ))}"
EXIT_CODE=0
RESULT_STATUS="prod_vm_replaced"
if (( RTO_SECONDS > PROD_RTO_SECONDS )); then
  RESULT_STATUS="replace_vm_over_budget"
  EXIT_CODE=6
fi

python3 -I - "$RESULT_STATUS" "$RELEASE_ID" "$PROD_VM_NAME" "$OLD_INSTANCE_ID" "$NEW_INSTANCE_ID" \
  "$OLD_MACHINE_TYPE" "$NEW_MACHINE_TYPE" "$NEW_VCPUS" "$TARGET_TEMPLATE" \
  "$OLD_BOOT_DISK" "$NEW_BOOT_DISK" "$PROD_DATA_DISK" "$DATA_UUID" "$STATIC_IP" \
  "$DATA_SNAPSHOT" "$BOOT_SNAPSHOT" "$CADDY_IMAGE" "$RTO_SECONDS" "$PROD_RTO_SECONDS" \
  "$RESTORED_IDENTITY_JSON" "$SNAP_SCHED_JSON" "$EXIT_CODE" <<'PY'
import json, sys

(status, rel_id, vm_name, old_id, new_id, old_mt, new_mt, vcpus, tpl,
 old_boot, new_boot, data_disk, data_uuid, static_ip, data_snap, boot_snap,
 caddy_img, rto_s, rto_budget_s, restored_raw, sched_raw, code) = sys.argv[1:23]

print(json.dumps({
    "status": status,
    "release_id": rel_id,
    "vm_name": vm_name,
    "old_instance_id": old_id,
    "new_instance_id": new_id,
    "old_machine_type": old_mt,
    "machine_type": new_mt,
    "vcpus": int(vcpus),
    "instance_template": tpl,
    "old_boot_disk": old_boot,
    "old_boot_disk_preserved": True,
    "new_boot_disk": new_boot,
    "data_disk": data_disk,
    "data_disk_uuid": data_uuid,
    "static_ip": static_ip,
    "data_snapshot": data_snap,
    "boot_snapshot": boot_snap,
    "boot_disk_decoupled": True,
    "caddy_restored": True,
    "caddy_image": caddy_img,
    "rto_seconds": int(rto_s),
    "rto_budget_seconds": int(rto_budget_s),
    "restored_identity": json.loads(restored_raw),
    "snapshot_schedule": json.loads(sched_raw),
    "exit_code": int(code),
}, sort_keys=True))
PY
exit "$EXIT_CODE"
