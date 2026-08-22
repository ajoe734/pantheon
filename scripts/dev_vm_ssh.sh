#!/usr/bin/env bash
# Canonical direct-SSH transport for the Pantheon dev VM.
#
# The dev VM already exposes SSH on its fixed public address.  CI supplies one
# dedicated private key and a pinned known_hosts file; this helper deliberately
# avoids gcloud compute ssh/scp so routine deploys never need to mutate project
# or instance SSH metadata.

set -euo pipefail

error() {
  echo "[dev-vm-ssh] ERROR: $*" >&2
  exit 2
}

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/dev_vm_ssh.sh prepare <credential-directory>
  scripts/dev_vm_ssh.sh exec <remote-command>
  scripts/dev_vm_ssh.sh copy-from <remote-path> <local-path>

Required environment for prepare:
  DEV_DEPLOY_SSH_PRIVATE_KEY
  DEV_DEPLOY_SSH_KNOWN_HOSTS

Required environment for exec/copy-from:
  DEV_DEPLOY_SSH_KEY_FILE
  DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE

Optional environment:
  DEV_DEPLOY_SSH_HOST       Default: 35.201.204.12
  DEV_DEPLOY_SSH_USER       Default: REMOTE_USER or lupin
  DEV_DEPLOY_SSH_PORT       Default: 22
  DEV_DEPLOY_SSH_TIMEOUT    Default: 12
EOF
  exit 2
}

require_regular_file() {
  local label="$1"
  local path="$2"
  [[ "$path" == /* ]] || error "$label must be an absolute path"
  [[ -f "$path" && ! -L "$path" ]] || error "$label must be a regular non-symlink file: $path"
}

load_transport_credentials() {
  default_credential_dir=""
  if [[ -n "${RUNNER_TEMP:-}" && -n "${GITHUB_RUN_ID:-}" && -n "${GITHUB_RUN_ATTEMPT:-}" ]]; then
    default_credential_dir="${RUNNER_TEMP}/pantheon-dev-ssh-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
  fi
  key_file="${DEV_DEPLOY_SSH_KEY_FILE:-${default_credential_dir:+${default_credential_dir}/deploy_key}}"
  known_hosts_file="${DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE:-${default_credential_dir:+${default_credential_dir}/known_hosts}}"
  [[ -n "$key_file" ]] || error "DEV_DEPLOY_SSH_KEY_FILE is required"
  [[ -n "$known_hosts_file" ]] || error "DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE is required"
  require_regular_file "DEV_DEPLOY_SSH_KEY_FILE" "$key_file"
  require_regular_file "DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE" "$known_hosts_file"
  key_mode="$(stat -c '%a' "$key_file")"
  [[ "$key_mode" =~ ^[0-7]*00$ ]] \
    || error "DEV_DEPLOY_SSH_KEY_FILE must not be group/world accessible (mode=$key_mode)"
  [[ -s "$key_file" ]] || error "DEV_DEPLOY_SSH_KEY_FILE is empty"
  [[ -s "$known_hosts_file" ]] || error "DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE is empty"
}

command_name="${1:-}"
[[ -n "$command_name" ]] || usage
shift

host="${DEV_DEPLOY_SSH_HOST:-35.201.204.12}"
remote_user="${DEV_DEPLOY_SSH_USER:-${REMOTE_USER:-lupin}}"
port="${DEV_DEPLOY_SSH_PORT:-22}"
timeout_seconds="${DEV_DEPLOY_SSH_TIMEOUT:-12}"

[[ "$host" =~ ^[A-Za-z0-9._:-]+$ ]] || error "DEV_DEPLOY_SSH_HOST contains unsupported characters"
[[ "$remote_user" =~ ^[A-Za-z0-9._-]+$ ]] || error "DEV_DEPLOY_SSH_USER contains unsupported characters"
[[ "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 ]] \
  || error "DEV_DEPLOY_SSH_PORT must be between 1 and 65535"
[[ "$timeout_seconds" =~ ^[0-9]+$ && "$timeout_seconds" -ge 1 ]] \
  || error "DEV_DEPLOY_SSH_TIMEOUT must be a positive integer"

case "$command_name" in
  prepare)
    [[ "$#" -eq 1 && "$1" == /* ]] || usage
    credential_dir="$1"
    private_key="${DEV_DEPLOY_SSH_PRIVATE_KEY:-}"
    known_hosts="${DEV_DEPLOY_SSH_KNOWN_HOSTS:-}"
    [[ -n "$private_key" ]] || error "DEV_DEPLOY_SSH_PRIVATE_KEY is required"
    [[ -n "$known_hosts" ]] || error "DEV_DEPLOY_SSH_KNOWN_HOSTS is required"
    [[ ! -L "$credential_dir" ]] || error "credential directory cannot be a symlink: $credential_dir"
    install -d -m 0700 "$credential_dir"
    key_file="$credential_dir/deploy_key"
    known_hosts_file="$credential_dir/known_hosts"
    (umask 077; printf '%s\n' "$private_key" > "$key_file")
    (umask 077; printf '%s\n' "$known_hosts" > "$known_hosts_file")
    chmod 0600 "$key_file" "$known_hosts_file"
    ssh-keygen -y -f "$key_file" >/dev/null 2>&1 \
      || error "DEV_DEPLOY_SSH_PRIVATE_KEY is not a usable unencrypted private key"
    ssh-keygen -F "$host" -f "$known_hosts_file" >/dev/null 2>&1 \
      || error "DEV_DEPLOY_SSH_KNOWN_HOSTS has no pinned entry for $host"
    printf 'DEV_DEPLOY_SSH_KEY_FILE=%s\n' "$key_file"
    printf 'DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE=%s\n' "$known_hosts_file"
    ;;
  exec)
    [[ "$#" -eq 1 && -n "$1" ]] || usage
    load_transport_credentials
    ssh_options=(
      -F /dev/null
      -i "$key_file"
      -p "$port"
      -T
      -o BatchMode=yes
      -o IdentitiesOnly=yes
      -o StrictHostKeyChecking=yes
      -o "UserKnownHostsFile=$known_hosts_file"
      -o "ConnectTimeout=$timeout_seconds"
      -o ConnectionAttempts=3
      -o ServerAliveInterval=30
      -o ServerAliveCountMax=4
      -o LogLevel=ERROR
    )
    exec ssh "${ssh_options[@]}" "${remote_user}@${host}" "$1"
    ;;
  copy-from)
    [[ "$#" -eq 2 && -n "$1" && -n "$2" ]] || usage
    load_transport_credentials
    remote_path="$1"
    local_path="$2"
    [[ "$remote_path" != *$'\n'* && "$local_path" != *$'\n'* ]] \
      || error "copy paths cannot contain newlines"
    scp_options=(
      -F /dev/null
      -i "$key_file"
      -P "$port"
      -o BatchMode=yes
      -o IdentitiesOnly=yes
      -o StrictHostKeyChecking=yes
      -o "UserKnownHostsFile=$known_hosts_file"
      -o "ConnectTimeout=$timeout_seconds"
      -o ConnectionAttempts=3
      -o LogLevel=ERROR
    )
    exec scp "${scp_options[@]}" "${remote_user}@${host}:${remote_path}" "$local_path"
    ;;
  *)
    usage
    ;;
esac
