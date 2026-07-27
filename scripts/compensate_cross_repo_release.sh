#!/usr/bin/env bash
# Restore the exact hosted FE/BFF baseline after a rejected cross-repo release.
#
# The frontend deploy workflow owns its atomic symlink switch and rollback.
# This helper proves that it returned to the pre-release frontend commit, then
# restores the prior BFF commit under a fresh shared environment lease. It
# never edits the runtime Compose manifest or deploy_nonprod_vm.sh.

set -euo pipefail

PINNED_LEASE_CONTROLLER_SHA="9e564718da8c39199a4c311f1a667b74226e3428"
LEASE_CLI_SHA256="52276793f99162fc7ca307a1370addd8d99478208ebf7beb67eab23b97b83048"
LEASE_WRAPPER_SHA256="6c82021b93621f16776d5d67a9e20cb9d690f7ebfa257ebf8c329f7d158fb2c2"

die() {
  echo "[cross-repo-release-compensation] ERROR: $*" >&2
  exit 75
}

required() {
  local name="$1"
  [[ -n "${!name:-}" ]] || die "${name} is required"
}

for name in \
  PANTHEON_ENVIRONMENT_LEASE_TOKEN \
  PANTHEON_LEASE_CONTROLLER_ROOT \
  PANTHEON_RELEASE_REPO_ROOT \
  PANTHEON_ROLLBACK_BACKEND_SHA \
  PANTHEON_ROLLBACK_FRONTEND_SHA \
  PANTHEON_FAILED_BACKEND_SHA \
  PANTHEON_FAILED_FRONTEND_SHA \
  PANTHEON_RELEASE_CANDIDATE_ID \
  PANTHEON_RELEASE_CONTROLLER_LOG \
  PANTHEON_ROLLBACK_EVIDENCE_OUT \
  DEV_BFF_URL \
  DEV_FE_URL \
  REMOTE_USER \
  DEV_VM \
  DEV_ZONE \
  GCP_DEPLOY_PROJECT_ID \
  RUNNER_TEMP \
  GITHUB_REPOSITORY \
  GITHUB_RUN_ID \
  GITHUB_RUN_ATTEMPT \
  GITHUB_SERVER_URL; do
  required "${name}"
done

sha_pattern='^[0-9a-f]{40}$'
digest_pattern='^[0-9a-f]{64}$'
[[ "${PANTHEON_ROLLBACK_BACKEND_SHA}" =~ ${sha_pattern} ]] \
  || die "rollback backend SHA is invalid"
[[ "${PANTHEON_ROLLBACK_FRONTEND_SHA}" =~ ${sha_pattern} ]] \
  || die "rollback frontend SHA is invalid"
[[ "${PANTHEON_FAILED_BACKEND_SHA}" =~ ${sha_pattern} ]] \
  || die "failed backend SHA is invalid"
[[ "${PANTHEON_FAILED_FRONTEND_SHA}" =~ ${sha_pattern} ]] \
  || die "failed frontend SHA is invalid"
[[ "${PANTHEON_RELEASE_CANDIDATE_ID}" =~ ${digest_pattern} ]] \
  || die "release candidate ID is invalid"
[[ -f "${PANTHEON_RELEASE_CONTROLLER_LOG}" && ! -L "${PANTHEON_RELEASE_CONTROLLER_LOG}" ]] \
  || die "release controller log is missing or unsafe"

lease_controller="$(realpath "${PANTHEON_LEASE_CONTROLLER_ROOT}")"
release_root="$(realpath "${PANTHEON_RELEASE_REPO_ROOT}")"
lease_cli="${lease_controller}/scripts/dev_environment_lease.py"
lease_wrapper="${lease_controller}/scripts/run_with_dev_environment_lease.sh"
deploy_script="${release_root}/scripts/deploy_nonprod_vm.sh"
[[ "$(git -C "${lease_controller}" rev-parse HEAD)" == "${PINNED_LEASE_CONTROLLER_SHA}" ]] \
  || die "lease controller is not the pinned trust root"
[[ -f "${lease_cli}" && ! -L "${lease_cli}" ]] || die "lease CLI is unsafe"
[[ -f "${lease_wrapper}" && ! -L "${lease_wrapper}" ]] || die "lease wrapper is unsafe"
[[ -f "${deploy_script}" && ! -L "${deploy_script}" ]] || die "deploy script is unsafe"
printf '%s  %s\n' \
  "${LEASE_CLI_SHA256}" "${lease_cli}" \
  "${LEASE_WRAPPER_SHA256}" "${lease_wrapper}" \
  | sha256sum --check --strict

lease_token="${PANTHEON_ENVIRONMENT_LEASE_TOKEN}"
unset PANTHEON_ENVIRONMENT_LEASE_TOKEN
export -n lease_token 2>/dev/null || true

lease_dir="$(mktemp -d "${RUNNER_TEMP%/}/pantheon-release-rollback-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-XXXXXX")"
chmod 0700 "${lease_dir}"
state_file="${lease_dir}/state.json"
acquisition_file="${lease_dir}/acquisition.json"
pid_file="${lease_dir}/heartbeat.pid"
identity_file="${lease_dir}/heartbeat-identity.json"
failure_file="${lease_dir}/heartbeat-failure.json"
shutdown_file="${lease_dir}/heartbeat-stop.json"
heartbeat_log="${lease_dir}/heartbeat.log"
heartbeat_pid=""
rollback_succeeded=false

heartbeat_stopped() {
  local state
  [[ -n "${heartbeat_pid}" ]] || return 0
  state="$(ps -o stat= -p "${heartbeat_pid}" 2>/dev/null | tr -d '[:space:]')"
  case "${state}" in
    ""|Z*|X*|x*) return 0 ;;
  esac
  return 1
}

stop_heartbeat() {
  heartbeat_stopped && return 0
  python3 "${lease_cli}" verify-heartbeat-identity \
    --identity-file "${identity_file}" \
    --pid "${heartbeat_pid}" \
    --expected-cli "${lease_cli}" \
    --state-file "${state_file}" >/dev/null || return 1
  kill -TERM "${heartbeat_pid}" 2>/dev/null || {
    heartbeat_stopped && return 0
    return 1
  }
  kill -CONT "${heartbeat_pid}" 2>/dev/null || true
  for _ in $(seq 1 40); do
    heartbeat_stopped && return 0
    sleep 0.25
  done
  return 1
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ -n "${heartbeat_pid}" ]]; then
    if ! stop_heartbeat; then
      echo "[cross-repo-release-compensation] heartbeat identity could not be stopped safely" >&2
      status=75
    fi
  fi
  if [[ "${rollback_succeeded}" == "true" ]]; then
    [[ ! -e "${failure_file}" ]] || status=75
    [[ -s "${shutdown_file}" ]] || status=75
    if [[ "${status}" -eq 0 ]]; then
      PANTHEON_ENVIRONMENT_LEASE_TOKEN="${lease_token}" \
        python3 "${lease_cli}" verify \
          --repository ajoe734/execute-plans \
          --branch environment-coordination \
          --path .pantheon/environment-leases/pantheon-dev-environment.json \
          --resource pantheon-dev-environment \
          --state-file "${state_file}" \
          --max-heartbeat-age-seconds 120 >/dev/null || status=75
    fi
    if [[ "${status}" -eq 0 ]]; then
      PANTHEON_ENVIRONMENT_LEASE_TOKEN="${lease_token}" \
        python3 "${lease_cli}" release \
          --repository ajoe734/execute-plans \
          --branch environment-coordination \
          --path .pantheon/environment-leases/pantheon-dev-environment.json \
          --resource pantheon-dev-environment \
          --state-file "${state_file}" >/dev/null || status=75
    fi
  else
    echo "[cross-repo-release-compensation] rollback incomplete; lease remains quarantined until TTL" >&2
  fi
  lease_token=""
  exit "${status}"
}
trap cleanup EXIT INT TERM

PANTHEON_ENVIRONMENT_LEASE_TOKEN="${lease_token}" \
  python3 "${lease_cli}" acquire \
    --repository ajoe734/execute-plans \
    --branch environment-coordination \
    --path .pantheon/environment-leases/pantheon-dev-environment.json \
    --resource pantheon-dev-environment \
    --mode deployment \
    --owner "pantheon:${GITHUB_REPOSITORY}:${GITHUB_RUN_ID}:${GITHUB_RUN_ATTEMPT}:rollback" \
    --ttl-seconds 300 \
    --wait-seconds 7200 \
    --poll-seconds 5 \
    --state-file "${state_file}" \
    --expected-backend-sha "${PANTHEON_ROLLBACK_BACKEND_SHA}" \
    --run-url "${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}" \
    --json-out "${acquisition_file}"

exec {token_fd}<<<"${lease_token}"
env -i \
  PATH="${PATH}" HOME="${HOME}" LANG=C.UTF-8 \
  PYTHONNOUSERSITE=1 PYTHONSAFEPATH=1 PYTHONSTARTUP= PYTHONINSPECT= \
  python3 "${lease_cli}" heartbeat-loop \
    --repository ajoe734/execute-plans \
    --branch environment-coordination \
    --path .pantheon/environment-leases/pantheon-dev-environment.json \
    --resource pantheon-dev-environment \
    --state-file "${state_file}" \
    --ttl-seconds 300 \
    --interval-seconds 60 \
    --failure-json-out "${failure_file}" \
    --shutdown-json-out "${shutdown_file}" \
    --identity-json-out "${identity_file}" \
    --token-stdin \
    <&"${token_fd}" >"${heartbeat_log}" 2>&1 &
heartbeat_pid=$!
exec {token_fd}<&-
printf '%s\n' "${heartbeat_pid}" > "${pid_file}"

for _ in $(seq 1 100); do
  [[ -s "${identity_file}" ]] && break
  kill -0 "${heartbeat_pid}" 2>/dev/null || {
    sed -n '1,200p' "${heartbeat_log}" >&2 || true
    die "lease heartbeat exited before becoming identity-bound"
  }
  sleep 0.1
done
[[ -s "${identity_file}" ]] || die "lease heartbeat identity was not recorded"

export TARGET_ENV=dev
export PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE="${state_file}"
export PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_PID_FILE="${pid_file}"
export PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_IDENTITY_FILE="${identity_file}"
export PANTHEON_DEV_ENVIRONMENT_LEASE_FAILURE_FILE="${failure_file}"
export PANTHEON_DEV_ENVIRONMENT_LEASE_HEARTBEAT_LOG="${heartbeat_log}"
export PANTHEON_DEV_ENVIRONMENT_LEASE_MAX_HEARTBEAT_AGE_SECONDS=120
export PANTHEON_DEV_ENVIRONMENT_LEASE_VERIFY_INTERVAL_SECONDS=30

PANTHEON_ENVIRONMENT_LEASE_TOKEN="${lease_token}" \
  "${lease_wrapper}" \
    bash "${deploy_script}" \
      --environment dev \
      --component bff \
      --sha "${PANTHEON_ROLLBACK_BACKEND_SHA}" \
      --project-id "${GCP_DEPLOY_PROJECT_ID}"

curl --fail-with-body --silent --show-error \
  --retry 5 --retry-all-errors --connect-timeout 10 --max-time 30 \
  "${DEV_BFF_URL%/}/health" >/dev/null
bff_version="${lease_dir}/bff-version-after-rollback.json"
fe_deployment="${lease_dir}/frontend-deployment-after-rollback.json"
curl --fail-with-body --silent --show-error \
  --retry 5 --retry-all-errors --connect-timeout 10 --max-time 30 \
  "${DEV_BFF_URL%/}/bff/version" > "${bff_version}"
curl --fail-with-body --silent --show-error \
  --retry 5 --retry-all-errors --connect-timeout 10 --max-time 30 \
  "${DEV_FE_URL%/}/deployment.json" > "${fe_deployment}"

python3 - \
  "${bff_version}" \
  "${fe_deployment}" \
  "${PANTHEON_ROLLBACK_EVIDENCE_OUT}" \
  "${PANTHEON_RELEASE_CONTROLLER_LOG}" <<'PY'
import hashlib
import json
import os
import pathlib
import sys

bff = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
frontend = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
actual_bff = bff.get("source_commit_sha") or ""
actual_frontend = frontend.get("frontendSha") or frontend.get("commit") or ""
if actual_bff != os.environ["PANTHEON_ROLLBACK_BACKEND_SHA"]:
    raise SystemExit(
        f"hosted BFF rollback mismatch: {actual_bff} != "
        f"{os.environ['PANTHEON_ROLLBACK_BACKEND_SHA']}"
    )
if actual_frontend != os.environ["PANTHEON_ROLLBACK_FRONTEND_SHA"]:
    raise SystemExit(
        f"hosted frontend rollback mismatch: {actual_frontend} != "
        f"{os.environ['PANTHEON_ROLLBACK_FRONTEND_SHA']}"
    )
controller_log = pathlib.Path(sys.argv[4]).read_bytes()
evidence = {
    "schema_version": "pantheon.cross-repo-release-compensation.v1",
    "release_candidate_id": os.environ["PANTHEON_RELEASE_CANDIDATE_ID"],
    "outcome": "compensated",
    "rejected_pair": {
        "backend_sha": os.environ["PANTHEON_FAILED_BACKEND_SHA"],
        "frontend_sha": os.environ["PANTHEON_FAILED_FRONTEND_SHA"],
    },
    "restored_pair": {
        "backend_sha": actual_bff,
        "frontend_sha": actual_frontend,
    },
    "controller_failure_log_sha256": hashlib.sha256(controller_log).hexdigest(),
    "workflow": {
        "repository": os.environ["GITHUB_REPOSITORY"],
        "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
    },
}
output = pathlib.Path(sys.argv[3])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(
    json.dumps(evidence, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

rollback_succeeded=true
