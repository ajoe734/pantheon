from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_dev_compose_wires_shared_jwt_verification_and_fail_closed_switches():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    runtime = compose["services"]["runtime-manager"]["environment"]
    governance = compose["services"]["governance"]["environment"]
    worker = compose["services"]["deployment-outbox-consumer"]["environment"]

    assert runtime["PANTHEON_RUNTIME_JWT_SECRET"] == "${PANTHEON_BFF_JWT_SECRET:-}"
    assert runtime["PANTHEON_CANARY_EXECUTION_ENABLED"] == "${PANTHEON_CANARY_EXECUTION_ENABLED:-false}"
    assert runtime["PANTHEON_LIVE_BROKER_ENABLED"] == "${PANTHEON_LIVE_BROKER_ENABLED:-false}"
    assert governance["PANTHEON_GOVERNANCE_JWT_SECRET"] == "${PANTHEON_BFF_JWT_SECRET:-}"
    assert worker["PANTHEON_ENVIRONMENT"] == "${PANTHEON_ENV:-dev}"


def test_split_topology_requires_explicit_execution_authorities_and_stage_flags():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.exec.yml").read_text(encoding="utf-8")
    )
    runtime = compose["services"]["runtime-manager"]["environment"]

    for key in (
        "PANTHEON_DEPLOYMENT_API_URL",
        "PANTHEON_GOVERNANCE_APPROVAL_API_URL",
        "PANTHEON_REGISTRY_API_URL",
        "PANTHEON_CAPITAL_API_URL",
        "PANTHEON_RUNTIME_JWT_SECRET",
    ):
        assert key in runtime
    assert runtime["PANTHEON_RUNTIME_AUTH_MODE"] == "${PANTHEON_RUNTIME_AUTH_MODE:-strict}"
    assert runtime["PANTHEON_CANARY_EXECUTION_ENABLED"] == "${PANTHEON_CANARY_EXECUTION_ENABLED:-false}"
    assert runtime["PANTHEON_LIVE_BROKER_ENABLED"] == "${PANTHEON_LIVE_BROKER_ENABLED:-false}"


def test_dev_deploy_script_explicitly_keeps_canary_disabled():
    script = (ROOT / "scripts" / "deploy_nonprod_vm.sh").read_text(
        encoding="utf-8"
    )
    assert script.count("PANTHEON_CANARY_EXECUTION_ENABLED=false") >= 2
