#!/usr/bin/env bash
# Provision a dedicated Pantheon dev VM and move the current single-VM dev stack
# there. This intentionally migrates only the root `pantheon` compose project;
# the `pantheon-exec` stack stays out of dev so staging/live execution can be
# isolated on the staging VM pair.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-pantheon-493602}"
ZONE="${ZONE:-asia-east1-b}"
VM_NAME="${VM_NAME:-pantheon-dev-vm1}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-highmem-4}"
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-100GB}"
BOOT_DISK_TYPE="${BOOT_DISK_TYPE:-pd-balanced}"
IMAGE_PROJECT="${IMAGE_PROJECT:-ubuntu-os-cloud}"
IMAGE_FAMILY="${IMAGE_FAMILY:-ubuntu-2404-lts-amd64}"
NETWORK="${NETWORK:-default}"
PROVISIONING_MODEL="${PROVISIONING_MODEL:-STANDARD}"
SPOT_TERMINATION_ACTION="${SPOT_TERMINATION_ACTION:-STOP}"
REMOTE_USER="${REMOTE_USER:-edna}"
SOURCE_DIR="${SOURCE_DIR:-/home/lupin/code/pantheon}"
REMOTE_DIR="${REMOTE_DIR:-/home/${REMOTE_USER}/code/pantheon}"
BACKUP_ROOT="${BACKUP_ROOT:-/tmp/pantheon-dev-vm-migration}"
SKIP_CREATE="${SKIP_CREATE:-false}"
SKIP_VOLUME_COPY="${SKIP_VOLUME_COPY:-false}"
START_STACK="${START_STACK:-true}"
STOP_SOURCE_AFTER_HEALTH="${STOP_SOURCE_AFTER_HEALTH:-true}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/gcp_dev_vm_migrate.sh [options]

Options are supplied as environment variables:
  PROJECT_ID          GCP project. Default: pantheon-493602
  ZONE                GCP zone. Default: asia-east1-b
  VM_NAME             Dev VM name. Default: pantheon-dev-vm1
  MACHINE_TYPE        Dev VM machine type. Default: e2-highmem-4
  BOOT_DISK_SIZE      Boot disk size. Default: 100GB
  PROVISIONING_MODEL  STANDARD or SPOT. Default: STANDARD
  SPOT_TERMINATION_ACTION
                      STOP or DELETE when PROVISIONING_MODEL=SPOT. Default: STOP
  REMOTE_USER         Linux user on the VM. Default: edna
  SOURCE_DIR          Local Pantheon repo path. Default: /home/lupin/code/pantheon
  REMOTE_DIR          Remote Pantheon repo path. Default: /home/lupin/code/pantheon
  SKIP_CREATE=true    Do not create the VM; only migrate/start.
  SKIP_VOLUME_COPY=true
                      Do not copy Docker volumes; useful for a clean dev VM.
  START_STACK=false   Copy but do not run docker compose up.
  STOP_SOURCE_AFTER_HEALTH=false
                      Leave the source dev stack running after remote health passes.

Examples:
  bash scripts/gcp_dev_vm_migrate.sh
  VM_NAME=pantheon-dev-vm1 MACHINE_TYPE=e2-standard-4 bash scripts/gcp_dev_vm_migrate.sh
EOF
}

info() {
  echo "[dev-vm-migrate] $*"
}

error() {
  echo "[dev-vm-migrate] ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || error "$1 is required"
}

preflight() {
  require_cmd gcloud
  require_cmd docker
  require_cmd rsync
  [[ -d "${SOURCE_DIR}" ]] || error "SOURCE_DIR does not exist: ${SOURCE_DIR}"

  info "Checking gcloud auth and Compute permissions"
  gcloud auth print-access-token >/dev/null
  gcloud compute instances list \
    --project="${PROJECT_ID}" \
    --filter="name=${VM_NAME}" \
    --format="value(name)" >/dev/null
}

startup_script() {
  local path="$1"
  cat >"${path}" <<EOF
#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git jq rsync docker.io docker-compose-v2
systemctl enable --now docker

id "${REMOTE_USER}" >/dev/null 2>&1 || useradd -m -s /bin/bash "${REMOTE_USER}"
usermod -aG docker "${REMOTE_USER}" || true
mkdir -p "/home/${REMOTE_USER}/code"
chown -R "${REMOTE_USER}:${REMOTE_USER}" "/home/${REMOTE_USER}/code"
EOF
}

ensure_vm() {
  if [[ "${SKIP_CREATE}" == "true" ]]; then
    info "Skipping VM creation because SKIP_CREATE=true"
    return
  fi

  if gcloud compute instances describe "${VM_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" >/dev/null 2>&1; then
    info "VM already exists: ${VM_NAME}"
    return
  fi

  local startup
  local create_args
  startup="$(mktemp)"
  startup_script "${startup}"
  info "Creating dev VM: ${VM_NAME}"
  create_args=(
    compute instances create "${VM_NAME}"
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --machine-type="${MACHINE_TYPE}" \
    --image-project="${IMAGE_PROJECT}" \
    --image-family="${IMAGE_FAMILY}" \
    --boot-disk-size="${BOOT_DISK_SIZE}" \
    --boot-disk-type="${BOOT_DISK_TYPE}" \
    --network="${NETWORK}" \
    --scopes="cloud-platform" \
    --tags="pantheon-dev" \
    --labels="app=pantheon,env=dev,role=dev-backend" \
    --metadata-from-file=startup-script="${startup}"
  )
  if [[ "${PROVISIONING_MODEL}" == "SPOT" ]]; then
    create_args+=(
      --provisioning-model=SPOT
      --instance-termination-action="${SPOT_TERMINATION_ACTION}"
      --maintenance-policy=TERMINATE
      --no-restart-on-failure
    )
  fi
  gcloud "${create_args[@]}"
  rm -f "${startup}"
}

wait_for_ssh() {
  info "Waiting for SSH and startup packages on ${VM_NAME}"
  for _ in $(seq 1 60); do
    if gcloud compute ssh "${REMOTE_USER}@${VM_NAME}" \
      --project="${PROJECT_ID}" \
      --zone="${ZONE}" \
      --command="docker --version >/dev/null && docker compose version >/dev/null" \
      --quiet >/dev/null 2>&1; then
      return
    fi
    sleep 10
  done
  error "Timed out waiting for ${VM_NAME} SSH/startup readiness"
}

ssh_cmd() {
  gcloud compute ssh "${REMOTE_USER}@${VM_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --quiet \
    --command="$1"
}

copy_repo() {
  info "Copying repo working tree to ${VM_NAME}:${REMOTE_DIR}"
  ssh_cmd "mkdir -p '${REMOTE_DIR}'"
  gcloud compute ssh "${REMOTE_USER}@${VM_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --quiet \
    --command="mkdir -p '${REMOTE_DIR}'" >/dev/null

  gcloud compute scp \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --recurse \
    --quiet \
    "${SOURCE_DIR}/." \
    "${REMOTE_USER}@${VM_NAME}:${REMOTE_DIR}"
}

backup_volumes() {
  mkdir -p "${BACKUP_ROOT}"
  mapfile -t volumes < <(docker volume ls --format '{{.Name}}' | grep '^pantheon_' | sort || true)
  if [[ "${#volumes[@]}" -eq 0 ]]; then
    info "No pantheon_* Docker volumes found to copy"
    return
  fi

  info "Backing up root dev volumes: ${volumes[*]}"
  for volume in "${volumes[@]}"; do
    docker run --rm \
      -v "${volume}:/from:ro" \
      -v "${BACKUP_ROOT}:/backup" \
      alpine:3.20 \
      sh -c "cd /from && tar czf /backup/${volume}.tgz ."
  done
}

copy_volume_backups() {
  if [[ "${SKIP_VOLUME_COPY}" == "true" ]]; then
    info "Skipping Docker volume copy because SKIP_VOLUME_COPY=true"
    return
  fi

  backup_volumes
  if ! compgen -G "${BACKUP_ROOT}/pantheon_*.tgz" >/dev/null; then
    info "No volume backup archives produced"
    return
  fi

  info "Copying Docker volume backups to ${VM_NAME}"
  ssh_cmd "mkdir -p '${BACKUP_ROOT}'"
  gcloud compute scp \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --recurse \
    --quiet \
    "${BACKUP_ROOT}/." \
    "${REMOTE_USER}@${VM_NAME}:${BACKUP_ROOT}"

  info "Restoring Docker volumes on ${VM_NAME}"
  ssh_cmd "for archive in '${BACKUP_ROOT}'/pantheon_*.tgz; do volume=\$(basename \"\$archive\" .tgz); docker volume create \"\$volume\" >/dev/null; docker run --rm -v \"\$volume:/to\" -v '${BACKUP_ROOT}:/backup:ro' alpine:3.20 sh -c \"cd /to && tar xzf /backup/\$(basename \"\$archive\")\"; done"
}

start_dev_stack() {
  if [[ "${START_STACK}" != "true" ]]; then
    info "Skipping stack start because START_STACK=false"
    return
  fi

  info "Starting root dev compose stack on ${VM_NAME}"
  ssh_cmd "cd '${REMOTE_DIR}' && docker compose -p pantheon -f docker-compose.yml up -d --build"
  info "Checking operator BFF health"
  ssh_cmd "curl -fsS http://127.0.0.1:18001/health >/dev/null"
}

stop_source_stack() {
  if [[ "${STOP_SOURCE_AFTER_HEALTH}" != "true" ]]; then
    info "Leaving source dev stack running because STOP_SOURCE_AFTER_HEALTH=false"
    return
  fi

  info "Stopping source root dev stack on this VM"
  docker compose -p pantheon -f "${SOURCE_DIR}/docker-compose.yml" down
}

main() {
  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
  fi

  preflight
  ensure_vm
  wait_for_ssh
  copy_repo
  copy_volume_backups
  start_dev_stack
  stop_source_stack
  info "Done. Dev BFF should be on ${VM_NAME}:18001 inside the VM."
}

main "$@"
