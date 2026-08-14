from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
DEPLOY = ROOT / "scripts" / "deploy_nonprod_vm.sh"
ENSURE_WORKER = ROOT / "scripts" / "ensure_devloop_paper_runtime_worker.sh"


def test_default_root_stack_uses_binding_scoped_paper_fleet() -> None:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]

    assert "profiles" not in services["paper-fleet-reconciler"]
    assert "profiles" not in services["paper-signal-producer"]
    producer = services["paper-signal-producer"]
    assert producer["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "services.execution.lean_runtime.paper_signal_producer",
        "healthcheck",
    ]
    assert producer["environment"]["PANTHEON_LIVE_BROKER_ENABLED"] == "false"
    assert producer["environment"]["PANTHEON_CANARY_EXECUTION_ENABLED"] == "false"
    assert services["pantheon-paper-runtime"]["profiles"] == [
        "static-paper-runtime"
    ]

    telemetry_token = services["telemetry"]["environment"][
        "PANTHEON_TELEMETRY_SERVICE_TOKEN"
    ]
    reconciler_token = services["paper-fleet-reconciler"]["environment"][
        "PANTHEON_TELEMETRY_SERVICE_TOKEN"
    ]
    assert telemetry_token == reconciler_token
    assert telemetry_token == (
        "${PANTHEON_TELEMETRY_SERVICE_TOKEN:-telemetry-service-internal}"
    )
    tenant = services["paper-fleet-reconciler"]["environment"][
        "PANTHEON_TENANT_ID"
    ]
    assert services["telemetry"]["environment"][
        "PANTHEON_TELEMETRY_SERVICE_TENANTS"
    ] == "${PANTHEON_TELEMETRY_SERVICE_TENANTS:-" + tenant + "}"


def test_dev_deploy_retires_static_worker_and_verifies_fleet() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")
    root_case = deploy[deploy.index("  root)\n") : deploy.index("\n  bff)\n")]
    default_profiles = next(
        line
        for line in deploy.splitlines()
        if line.strip().startswith(
            'PANTHEON_DEV_COMPOSE_PROFILES="${PANTHEON_DEV_COMPOSE_PROFILES:-'
        )
    )

    assert "static-paper-runtime" not in default_profiles
    assert "retire_legacy_static_paper_runtime()" in deploy
    assert "verify_dev_paper_fleet()" in deploy
    assert "http://127.0.0.1:18011/readyz" in deploy
    assert 'worker.get("heartbeat_status") == "active"' in deploy
    compose_up = root_case.index(
        "docker compose -p pantheon -f docker-compose.yml up -d --build"
    )
    assert compose_up < root_case.index("retire_legacy_static_paper_runtime")
    assert compose_up < root_case.index("verify_dev_paper_fleet")


def test_worker_ensure_helper_targets_fleet_reconciler() -> None:
    helper = ENSURE_WORKER.read_text(encoding="utf-8")

    assert "pantheon-paper-fleet-reconciler-1" in helper
    assert "paper-fleet-reconciler" in helper
    assert "pantheon-pantheon-paper-runtime-1" not in helper
    assert 'COMPOSE_SERVICE="${PANTHEON_PAPER_FLEET_RECONCILER_SERVICE:-' in helper
