#!/usr/bin/env bash
set -euo pipefail
container_name=paper-risk-change-pg-20261123-codex
docker run --rm -d --name "$container_name" -e POSTGRES_PASSWORD=fixture-password -e POSTGRES_DB=pantheon -p 127.0.0.1::5432 postgres:16
trap 'docker rm -f "$container_name" >/dev/null' EXIT
pg_port=$(docker inspect --format '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}' "$container_name")
for attempt in $(seq 1 30); do
    if docker exec "$container_name" pg_isready -U postgres -d pantheon >/dev/null 2>&1; then break; fi
    if [[ $attempt == 30 ]]; then exit 1; fi
    sleep 1
done
export CAPITAL_TEST_DSN="postgresql://postgres:fixture-password@127.0.0.1:$pg_port/pantheon"
timeout 180 .venv-pantheon/bin/python3 -m pytest -q services/capital acceptance/test_paper_risk_policy_binding.py acceptance/test_persona_risk_profile_change.py
