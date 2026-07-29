#!/usr/bin/env bash
set -euo pipefail

task_id="L12-MANIFEST-RESTART-PROOF-20260729"
project="${L12_RESTART_PROOF_PROJECT:-l12-manifest-restart-proof-20260729}"
evidence_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
compose_file="$evidence_dir/fixture.compose.yml"
shared_worker="pantheon-policy-learning-shadow-eval-scheduler-1"
cleanup_pending=0

compose() {
  docker compose --project-name "$project" --file "$compose_file" "$@"
}

snapshot_container() {
  local container="$1"
  if ! docker inspect "$container" >/dev/null 2>&1; then
    printf 'null\n'
    return
  fi
  docker inspect "$container" --format '{{json .}}' |
    jq '{
      id: .Id,
      image_id: .Image,
      name: .Name,
      project: .Config.Labels["com.docker.compose.project"],
      service: .Config.Labels["com.docker.compose.service"],
      status: .State.Status,
      running: .State.Running,
      pid: .State.Pid,
      started_at: .State.StartedAt,
      finished_at: .State.FinishedAt,
      exit_code: .State.ExitCode,
      restart_count: .RestartCount,
      restart_policy: .HostConfig.RestartPolicy.Name,
      stop_timeout_seconds: .Config.StopTimeout,
      networks: (.NetworkSettings.Networks | keys)
    }'
}

cleanup() {
  if [[ "$cleanup_pending" == "1" ]]; then
    compose down --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ "$project" == "pantheon" ]]; then
  printf 'refusing shared Compose project name: %s\n' "$project" >&2
  exit 2
fi

mapfile -t existing_containers < <(
  docker ps --all --quiet --filter "label=com.docker.compose.project=$project"
)
if ((${#existing_containers[@]} > 0)); then
  printf 'refusing non-empty proof project: %s\n' "$project" >&2
  exit 2
fi

started_at="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
docker_server_version="$(docker version --format '{{.Server.Version}}')"
docker_server_name="$(docker info --format '{{.Name}}')"
image_id="$(docker image inspect pantheon-policy-learning-shadow-eval-scheduler:latest --format '{{.Id}}')"
rendered_worker="$(
  compose config --format json |
    jq '.services.worker | {
      image,
      command,
      restart,
      stop_grace_period,
      ports: (.ports // []),
      volumes: (.volumes // []),
      networks
    }'
)"
shared_before="$(snapshot_container "$shared_worker")"

cleanup_pending=1
compose up --detach
worker_id="$(compose ps --quiet worker)"
stub_id="$(compose ps --quiet api-stub)"

if [[ -z "$worker_id" || -z "$stub_id" ]]; then
  printf 'isolated fixture did not create both containers\n' >&2
  exit 3
fi

# Docker's restart policy is only armed after a container has remained up
# successfully. Twelve seconds exceeds the documented ten-second threshold.
sleep 12
worker_before="$(snapshot_container "$worker_id")"
stub_before="$(snapshot_container "$stub_id")"

jq --exit-status '
  .running == true
  and .restart_count == 0
  and .restart_policy == "unless-stopped"
  and .stop_timeout_seconds == 30
  and .project == "l12-manifest-restart-proof-20260729"
' <<<"$worker_before" >/dev/null

set +e
docker exec "$worker_id" sh -c 'kill -KILL 1'
crash_command_exit_code=$?
set -e

restart_observed=0
for _attempt in $(seq 1 60); do
  candidate="$(snapshot_container "$worker_id")"
  if jq --exit-status \
    '.running == true and .restart_count >= 1' <<<"$candidate" >/dev/null; then
    restart_observed=1
    worker_after="$candidate"
    break
  fi
  sleep 0.5
done

if [[ "$restart_observed" != "1" ]]; then
  printf 'RestartCount did not increment within 30 seconds\n' >&2
  exit 4
fi

for _attempt in $(seq 1 20); do
  startup_count="$(
    docker logs "$worker_id" 2>&1 |
      grep --count '"startup_recovery"' || true
  )"
  if ((startup_count >= 2)); then
    break
  fi
  sleep 0.5
done

worker_logs="$(docker logs --timestamps "$worker_id" 2>&1 | tail -n 20)"
shared_after="$(snapshot_container "$shared_worker")"

jq --exit-status --arg before_pid "$(jq -r '.pid' <<<"$worker_before")" '
  .running == true
  and .restart_count >= 1
  and (.pid | tostring) != $before_pid
' <<<"$worker_after" >/dev/null

shared_unchanged="$(
  jq --null-input \
    --argjson before "$shared_before" \
    --argjson after "$shared_after" \
    '$before == $after'
)"

compose down --remove-orphans
cleanup_pending=0

remaining_container_count="$(
  docker ps --all --quiet \
    --filter "label=com.docker.compose.project=$project" |
    wc -l |
    tr -d ' '
)"
remaining_network_count="$(
  docker network ls --quiet \
    --filter "label=com.docker.compose.project=$project" |
    wc -l |
    tr -d ' '
)"
finished_at="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"

jq --null-input \
  --arg record_type "pantheon.manifest-isolated-restart-proof.v1" \
  --arg task_id "$task_id" \
  --arg project "$project" \
  --arg started_at "$started_at" \
  --arg finished_at "$finished_at" \
  --arg docker_server_version "$docker_server_version" \
  --arg docker_server_name "$docker_server_name" \
  --arg image_id "$image_id" \
  --argjson rendered_worker "$rendered_worker" \
  --argjson worker_before "$worker_before" \
  --argjson worker_after "$worker_after" \
  --argjson stub_before "$stub_before" \
  --argjson shared_before "$shared_before" \
  --argjson shared_after "$shared_after" \
  --argjson shared_unchanged "$shared_unchanged" \
  --argjson crash_command_exit_code "$crash_command_exit_code" \
  --argjson remaining_container_count "$remaining_container_count" \
  --argjson remaining_network_count "$remaining_network_count" \
  --arg worker_logs "$worker_logs" \
  '{
    record_type: $record_type,
    task_id: $task_id,
    result: (
      if (
        $worker_before.restart_count == 0
        and $worker_after.restart_count >= 1
        and $worker_before.id == $worker_after.id
        and $worker_before.pid != $worker_after.pid
        and $worker_after.running == true
        and $shared_unchanged
        and $remaining_container_count == 0
        and $remaining_network_count == 0
      ) then "pass" else "fail" end
    ),
    scope: {
      compose_project: $project,
      isolated_from_shared_project: ($project != "pantheon"),
      external_ports: $rendered_worker.ports,
      shared_volumes: $rendered_worker.volumes,
      internal_network_only: true
    },
    runtime: {
      started_at: $started_at,
      finished_at: $finished_at,
      docker_server_version: $docker_server_version,
      docker_server_name: $docker_server_name,
      image_id: $image_id,
      rendered_worker: $rendered_worker
    },
    unsolicited_pid1_crash: {
      command: "docker exec <isolated-worker-id> sh -c '\''kill -KILL 1'\''",
      command_exit_code: $crash_command_exit_code,
      operator_stop_api_used: false
    },
    observed: {
      worker_before: $worker_before,
      worker_after: $worker_after,
      same_container_id: ($worker_before.id == $worker_after.id),
      pid_changed: ($worker_before.pid != $worker_after.pid),
      restart_count_delta: ($worker_after.restart_count - $worker_before.restart_count),
      worker_logs: $worker_logs,
      stub_before: $stub_before
    },
    shared_stack_guard: {
      target: "pantheon-policy-learning-shadow-eval-scheduler-1",
      before: $shared_before,
      after: $shared_after,
      byte_equivalent_snapshot: $shared_unchanged
    },
    cleanup: {
      isolated_project_removed: (
        $remaining_container_count == 0 and $remaining_network_count == 0
      ),
      remaining_container_count: $remaining_container_count,
      remaining_network_count: $remaining_network_count
    }
  }'
