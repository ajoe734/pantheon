#!/usr/bin/env bash
# Pull developer home-state from an old VM into this VM.
#
# This complements repo-only handoff scripts by migrating the state that makes a
# remote development machine feel like the old one: VS Code extensions and
# history, AI tool records, shell history, Git/SSH config, and workspaces.

set -euo pipefail

TRANSPORT="${PANTHEON_OLD_VM_TRANSPORT:-gcloud}"
GCP_PROJECT="${PANTHEON_OLD_VM_PROJECT:-pantheon-493602}"
GCP_ZONE="${PANTHEON_OLD_VM_ZONE:-asia-east1-b}"
GCP_INSTANCE="${PANTHEON_OLD_VM_INSTANCE:-pantheon-taiwan}"
GCP_USER="${PANTHEON_OLD_VM_USER:-edna}"
GCP_ACCOUNT="${PANTHEON_OLD_VM_GCP_ACCOUNT:-edna@cctech-support.com}"
GCP_TUNNEL_THROUGH_IAP="${PANTHEON_OLD_VM_TUNNEL_THROUGH_IAP:-0}"
SOURCE_HOST="${PANTHEON_OLD_VM_HOST:-}"
SOURCE_HOST_SET=0
SOURCE_HOME="${PANTHEON_OLD_VM_HOME:-/home/lupin}"
TARGET_HOME="${PANTHEON_NEW_VM_HOME:-${HOME}}"
SSH_BIN="${PANTHEON_OLD_VM_SSH_BIN:-ssh}"
SSH_PORT="${PANTHEON_OLD_VM_SSH_PORT:-}"
SSH_IDENTITY_FILE="${PANTHEON_OLD_VM_IDENTITY_FILE:-}"
CONNECT_TIMEOUT="${PANTHEON_OLD_VM_CONNECT_TIMEOUT:-10}"
DRY_RUN=0
INCLUDE_CACHE=0
INCLUDE_VSCODE_SERVER_BINARIES=0
INCLUDE_AUTHORIZED_KEYS=0
INCLUDE_DOCKER_VOLUMES=0
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="${PANTHEON_DEV_STATE_BACKUP_ROOT:-${TARGET_HOME}/.migration-backups/old-vm-dev-state-${TIMESTAMP}}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/pull_old_vm_dev_state.sh [options]

Options:
  --transport ssh|gcloud   How to reach the old VM. Default: gcloud
  --source-instance NAME   Old GCP VM instance. Default: pantheon-taiwan
  --project PROJECT        Old GCP project. Default: pantheon-493602
  --zone ZONE              Old GCP zone. Default: asia-east1-b
  --user USER              Old VM Linux user. Default: edna
  --gcp-account ACCOUNT    GCP account to use. Default: edna@cctech-support.com
  --tunnel-through-iap     Use IAP for gcloud compute ssh
  --source-host HOST       Old VM SSH target, or user@instance with --transport gcloud
  --source-home PATH        Old VM home path. Default: /home/lupin
  --target-home PATH        New VM home path. Default: $HOME
  --identity-file PATH      SSH identity file for the old VM
  --ssh-port PORT           SSH port for the old VM
  --dry-run, -n             Show what would be copied
  --include-cache           Also copy selected dev caches
  --include-vscode-server   Also copy VS Code server binaries, not just data/extensions
  --include-authorized-keys Also copy old ~/.ssh/authorized_keys
  --include-docker-volumes  Stream all Docker volumes from old VM
  --help, -h                Show this help

Environment equivalents:
  PANTHEON_OLD_VM_TRANSPORT
  PANTHEON_OLD_VM_INSTANCE
  PANTHEON_OLD_VM_PROJECT
  PANTHEON_OLD_VM_ZONE
  PANTHEON_OLD_VM_USER
  PANTHEON_OLD_VM_GCP_ACCOUNT
  PANTHEON_OLD_VM_TUNNEL_THROUGH_IAP
  PANTHEON_OLD_VM_HOST
  PANTHEON_OLD_VM_HOME
  PANTHEON_NEW_VM_HOME
  PANTHEON_OLD_VM_IDENTITY_FILE
  PANTHEON_OLD_VM_SSH_PORT
  PANTHEON_DEV_STATE_BACKUP_ROOT

Examples:
  bash scripts/pull_old_vm_dev_state.sh --dry-run
  bash scripts/pull_old_vm_dev_state.sh \
    --source-instance pantheon-taiwan \
    --project pantheon-493602 \
    --zone asia-east1-b
  bash scripts/pull_old_vm_dev_state.sh \
    --transport ssh \
    --source-host edna@OLD_IP \
    --identity-file ~/.ssh/pantheon_gcp_vm_ed25519
EOF
}

info() {
  echo "[old-vm-dev-state] $*"
}

warn() {
  echo "[old-vm-dev-state] WARNING: $*" >&2
}

error() {
  echo "[old-vm-dev-state] ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || error "$1 is required"
}

quote_sh() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

path_key() {
  printf "%s" "$1" | sed 's#[^A-Za-z0-9._-]#_#g'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --transport)
      TRANSPORT="${2:-}"
      shift 2
      ;;
    --source-instance)
      GCP_INSTANCE="${2:-}"
      shift 2
      ;;
    --project)
      GCP_PROJECT="${2:-}"
      shift 2
      ;;
    --zone)
      GCP_ZONE="${2:-}"
      shift 2
      ;;
    --user)
      GCP_USER="${2:-}"
      shift 2
      ;;
    --gcp-account)
      GCP_ACCOUNT="${2:-}"
      shift 2
      ;;
    --tunnel-through-iap)
      GCP_TUNNEL_THROUGH_IAP=1
      shift
      ;;
    --source-host)
      SOURCE_HOST="${2:-}"
      SOURCE_HOST_SET=1
      shift 2
      ;;
    --source-home)
      SOURCE_HOME="${2:-}"
      shift 2
      ;;
    --target-home)
      TARGET_HOME="${2:-}"
      shift 2
      ;;
    --identity-file)
      SSH_IDENTITY_FILE="${2:-}"
      shift 2
      ;;
    --ssh-port)
      SSH_PORT="${2:-}"
      shift 2
      ;;
    --dry-run|-n)
      DRY_RUN=1
      shift
      ;;
    --include-cache)
      INCLUDE_CACHE=1
      shift
      ;;
    --include-vscode-server)
      INCLUDE_VSCODE_SERVER_BINARIES=1
      shift
      ;;
    --include-authorized-keys)
      INCLUDE_AUTHORIZED_KEYS=1
      shift
      ;;
    --include-docker-volumes)
      INCLUDE_DOCKER_VOLUMES=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      error "Unknown option: $1"
      ;;
  esac
done

case "${TRANSPORT}" in
  ssh|gcloud)
    ;;
  *)
    error "Unsupported transport: ${TRANSPORT}. Use 'ssh' or 'gcloud'."
    ;;
esac

if [[ "${TRANSPORT}" == "gcloud" ]]; then
  if [[ "${SOURCE_HOST_SET}" -eq 1 ]]; then
    if [[ "${SOURCE_HOST}" == *@* ]]; then
      GCP_USER="${SOURCE_HOST%@*}"
      GCP_INSTANCE="${SOURCE_HOST#*@}"
    else
      GCP_INSTANCE="${SOURCE_HOST}"
    fi
  fi
  SOURCE_HOST="${GCP_USER}@${GCP_INSTANCE}"
fi

if [[ "${TRANSPORT}" == "ssh" ]]; then
  [[ -n "${SOURCE_HOST}" ]] || error "--source-host is required with --transport ssh"
fi
[[ -n "${GCP_USER}" ]] || error "--user is required with --transport gcloud"
[[ -n "${GCP_INSTANCE}" ]] || error "--source-instance is required with --transport gcloud"
[[ -n "${GCP_PROJECT}" ]] || error "--project is required with --transport gcloud"
[[ -n "${GCP_ZONE}" ]] || error "--zone is required with --transport gcloud"
[[ -n "${SOURCE_HOME}" ]] || error "--source-home is required"
[[ -n "${TARGET_HOME}" ]] || error "--target-home is required"
[[ -d "${TARGET_HOME}" ]] || error "Target home does not exist: ${TARGET_HOME}"

SSH_ARGS=(
  -o "BatchMode=yes"
  -o "StrictHostKeyChecking=accept-new"
  -o "ConnectTimeout=${CONNECT_TIMEOUT}"
)
if [[ -n "${SSH_PORT}" ]]; then
  SSH_ARGS+=(-p "${SSH_PORT}")
fi
if [[ -n "${SSH_IDENTITY_FILE}" ]]; then
  SSH_ARGS+=(-i "${SSH_IDENTITY_FILE}")
fi

GCLOUD_SSH_ARGS=(
  compute ssh "${GCP_USER}@${GCP_INSTANCE}"
  "--project=${GCP_PROJECT}"
  "--zone=${GCP_ZONE}"
  --quiet
  --ssh-flag=-T
)
if [[ -n "${GCP_ACCOUNT}" ]]; then
  GCLOUD_SSH_ARGS+=("--account=${GCP_ACCOUNT}")
fi
if [[ "${GCP_TUNNEL_THROUGH_IAP}" == "1" || "${GCP_TUNNEL_THROUGH_IAP}" == "true" ]]; then
  GCLOUD_SSH_ARGS+=(--tunnel-through-iap)
fi

RSYNC_RSH=""
RSYNC_REMOTE_HOST="${SOURCE_HOST}"
RSYNC_BASE_ARGS=(
  -az
  --human-readable
  --partial
  --protect-args
  --backup
  --info=stats2,progress2
)
if [[ "${DRY_RUN}" -eq 1 ]]; then
  RSYNC_BASE_ARGS+=(--dry-run --itemize-changes)
fi

remote_sh() {
  if [[ "${TRANSPORT}" == "gcloud" ]]; then
    gcloud "${GCLOUD_SSH_ARGS[@]}" --command="$1"
  else
    "${SSH_BIN}" "${SSH_ARGS[@]}" "${SOURCE_HOST}" "$@"
  fi
}

rsync_source_host() {
  printf "%s" "${RSYNC_REMOTE_HOST}"
}

configure_rsync_transport() {
  if [[ "${TRANSPORT}" == "ssh" ]]; then
    RSYNC_RSH="$(printf "%q " "${SSH_BIN}" "${SSH_ARGS[@]}")"
    RSYNC_REMOTE_HOST="${SOURCE_HOST}"
    return
  fi

  local dry_run_command
  local last_index
  local part
  local -a dry_run_parts
  local -a rsync_shell_parts=()

  dry_run_command="$(gcloud "${GCLOUD_SSH_ARGS[@]}" --dry-run)"
  read -r -a dry_run_parts <<<"${dry_run_command}"
  if [[ "${#dry_run_parts[@]}" -lt 2 ]]; then
    error "Could not resolve gcloud SSH command for rsync: ${dry_run_command}"
  fi

  last_index="$((${#dry_run_parts[@]} - 1))"
  RSYNC_REMOTE_HOST="${dry_run_parts[$last_index]}"
  unset "dry_run_parts[$last_index]"

  for part in "${dry_run_parts[@]}"; do
    # gcloud emits -t even for dry-run; rsync needs a non-interactive SSH stream.
    if [[ "${part}" == "-t" ]]; then
      continue
    fi
    rsync_shell_parts+=("${part}")
  done

  RSYNC_RSH="$(printf "%q " "${rsync_shell_parts[@]}")"
}

remote_exists() {
  local rel="$1"
  local abs="${SOURCE_HOME%/}/${rel}"
  remote_sh "test -e $(quote_sh "${abs}")"
}

remote_is_dir() {
  local rel="$1"
  local abs="${SOURCE_HOME%/}/${rel}"
  remote_sh "test -d $(quote_sh "${abs}")"
}

copy_path() {
  local label="$1"
  local source_rel="$2"
  local target_rel="$3"
  shift 3
  local extra_args=("$@")
  local source_abs="${SOURCE_HOME%/}/${source_rel}"
  local target_abs="${TARGET_HOME%/}/${target_rel}"
  local backup_dir="${BACKUP_ROOT}/$(path_key "${target_rel}")"
  local source_arg
  local target_arg
  local rsync_args=("${RSYNC_BASE_ARGS[@]}" --backup-dir="${backup_dir}" "${extra_args[@]}")

  if ! remote_exists "${source_rel}"; then
    info "Skipping missing ${label}: ${source_abs}"
    return
  fi

  mkdir -p "${backup_dir}"
  if remote_is_dir "${source_rel}"; then
    mkdir -p "${target_abs}"
    source_arg="$(rsync_source_host):${source_abs%/}/"
    target_arg="${target_abs%/}/"
  else
    mkdir -p "$(dirname "${target_abs}")"
    source_arg="$(rsync_source_host):${source_abs}"
    target_arg="${target_abs}"
  fi

  info "Copying ${label}: ${source_abs} -> ${target_abs}"
  rsync "${rsync_args[@]}" -e "${RSYNC_RSH}" "${source_arg}" "${target_arg}"
}

copy_many() {
  local label="$1"
  shift
  local rel
  for rel in "$@"; do
    copy_path "${label} ${rel}" "${rel}" "${rel}"
  done
}

fix_permissions() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    return
  fi

  if [[ -d "${TARGET_HOME}/.ssh" ]]; then
    chmod 700 "${TARGET_HOME}/.ssh"
    find "${TARGET_HOME}/.ssh" -type f -name "authorized_keys" -exec chmod 600 {} +
    find "${TARGET_HOME}/.ssh" -type f -name "config" -exec chmod 600 {} +
    find "${TARGET_HOME}/.ssh" -type f -name "known_hosts*" -exec chmod 644 {} +
    find "${TARGET_HOME}/.ssh" -type f \( -name "id_*" -o -name "*.pem" -o -name "*_ed25519" -o -name "*_rsa" \) -exec chmod 600 {} +
  fi

  if [[ -d "${TARGET_HOME}/.gnupg" ]]; then
    chmod 700 "${TARGET_HOME}/.gnupg"
    find "${TARGET_HOME}/.gnupg" -type f -exec chmod 600 {} +
  fi
}

copy_docker_volumes() {
  local volume
  local volumes

  require_cmd docker
  info "Listing Docker volumes on ${SOURCE_HOST}"
  volumes="$(remote_sh "docker volume ls --format '{{.Name}}' | sort || true")"
  if [[ -z "${volumes}" ]]; then
    info "No Docker volumes found on old VM"
    return
  fi

  while IFS= read -r volume; do
    [[ -n "${volume}" ]] || continue
    if [[ "${DRY_RUN}" -eq 1 ]]; then
      info "Would stream Docker volume ${volume}"
      continue
    fi

    info "Streaming Docker volume ${volume}"
    docker volume create "${volume}" >/dev/null
    docker run --rm -v "${volume}:/to" alpine:3.20 \
      sh -c 'find /to -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'
    remote_sh "docker run --rm -v $(quote_sh "${volume}"):/from:ro alpine:3.20 sh -c 'cd /from && tar czf - .'" |
      docker run --rm -i -v "${volume}:/to" alpine:3.20 sh -c "cd /to && tar xzf -"
  done <<<"${volumes}"
}

main() {
  require_cmd rsync
  if [[ "${TRANSPORT}" == "gcloud" ]]; then
    require_cmd gcloud
  else
    require_cmd "${SSH_BIN}"
  fi
  configure_rsync_transport

  if [[ "${TRANSPORT}" == "gcloud" ]]; then
    info "Source: gcloud ${GCP_PROJECT}/${GCP_ZONE}/${GCP_USER}@${GCP_INSTANCE}:${SOURCE_HOME}"
  else
    info "Source: ${SOURCE_HOST}:${SOURCE_HOME}"
  fi
  info "Target: ${TARGET_HOME}"
  info "Backup root for overwritten target files: ${BACKUP_ROOT}"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    info "Dry run only; no files will be changed"
  fi

  info "Checking old VM SSH access"
  remote_sh "test -d $(quote_sh "${SOURCE_HOME}")" || error "Cannot access ${SOURCE_HOST}:${SOURCE_HOME}"

  copy_path "workspace code and repo state" "code" "code"

  copy_path "VS Code extensions" ".vscode-server/extensions" ".vscode-server/extensions"
  copy_path "VS Code server data, settings, history, logs, profiles, and cached VSIXs" ".vscode-server/data" ".vscode-server/data"

  if [[ "${INCLUDE_VSCODE_SERVER_BINARIES}" -eq 1 ]]; then
    copy_path "VS Code server binaries" ".vscode-server/bin" ".vscode-server/bin"
    copy_path "VS Code CLI servers" ".vscode-server/cli/servers" ".vscode-server/cli/servers"
  fi

  copy_many "AI/development record" \
    ".codex" \
    ".claude" \
    ".config/Claude" \
    ".continue" \
    ".aider.conf.yml" \
    ".aider.model.settings.yml" \
    ".aider.input.history" \
    ".aider.chat.history.md"

  copy_many "shell/editor history and profile" \
    ".bash_history" \
    ".zsh_history" \
    ".python_history" \
    ".psql_history" \
    ".sqlite_history" \
    ".mysql_history" \
    ".rediscli_history" \
    ".lesshst" \
    ".viminfo" \
    ".profile" \
    ".bashrc" \
    ".bash_aliases" \
    ".inputrc" \
    ".tmux.conf" \
    ".selected_editor"

  copy_many "developer config" \
    ".gitconfig" \
    ".gitignore_global" \
    ".git-credentials" \
    ".config/git" \
    ".config/gh" \
    ".docker" \
    ".npmrc" \
    ".yarnrc" \
    ".pypirc" \
    ".pip" \
    ".config/pip" \
    ".poetry" \
    ".config/pypoetry" \
    ".cargo/config.toml"

  if [[ "${INCLUDE_CACHE}" -eq 1 ]]; then
    copy_many "selected dev cache" \
      ".cache/pip" \
      ".cache/pypoetry" \
      ".cache/yarn" \
      ".cache/ms-playwright" \
      ".npm" \
      ".local/share/pnpm"
  fi

  if [[ "${INCLUDE_DOCKER_VOLUMES}" -eq 1 ]]; then
    copy_docker_volumes
  fi

  ssh_excludes=(
    --exclude="*.sock"
    --exclude="control*"
    --exclude="*.tmp"
    --exclude="google_compute_engine"
    --exclude="google_compute_engine.pub"
    --exclude="google_compute_known_hosts"
  )
  if [[ "${INCLUDE_AUTHORIZED_KEYS}" -ne 1 ]]; then
    ssh_excludes+=(--exclude="authorized_keys")
  fi
  copy_path "SSH config and development keys" ".ssh" ".ssh" "${ssh_excludes[@]}"

  # Copy gcloud state last because it can change the active credentials used by
  # this same script when --transport gcloud is selected.
  copy_path "gcloud config and auth state" ".config/gcloud" ".config/gcloud"

  fix_permissions
  info "Done. Backups for overwritten target files are under ${BACKUP_ROOT}"
}

main "$@"
