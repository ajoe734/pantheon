import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.issue_dev_paper_principals import CONSUMER_FILES, issue_environment


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
DEPLOY = ROOT / "scripts" / "deploy_nonprod_vm.sh"
ENSURE_WORKER = ROOT / "scripts" / "ensure_devloop_paper_runtime_worker.sh"


def test_default_root_stack_uses_binding_scoped_paper_fleet() -> None:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]

    # Default root deployment membership is the "root" profile.
    assert "root" in services["paper-fleet-reconciler"]["profiles"]
    assert "root" in services["paper-signal-producer"]["profiles"]
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
    # Layer C stays off by default; the cutover task flips it, not this stack.
    assert producer["environment"]["PANTHEON_PAPER_EXPRESSION_LAYER"] == (
        "${PANTHEON_PAPER_EXPRESSION_LAYER:-off}"
    )
    assert "pantheon-paper-runtime" not in services

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


def test_dev_deploy_has_no_static_worker_and_verifies_fleet() -> None:
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
    assert "retire_legacy_static_paper_runtime" not in deploy
    assert "static-paper-runtime" not in deploy
    assert "verify_dev_paper_fleet()" in deploy
    assert "http://127.0.0.1:18011/readyz" in deploy
    assert 'worker.get("heartbeat_status") == "active"' in deploy
    compose_up = root_case.index("run_dev_candidate_compose up -d \\\n")
    assert compose_up < root_case.index("verify_dev_paper_fleet")


def test_worker_ensure_helper_targets_fleet_reconciler() -> None:
    helper = ENSURE_WORKER.read_text(encoding="utf-8")

    assert "pantheon-paper-fleet-reconciler-1" in helper
    assert "paper-fleet-reconciler" in helper
    assert "pantheon-pantheon-paper-runtime-1" not in helper
    assert 'COMPOSE_SERVICE="${PANTHEON_PAPER_FLEET_RECONCILER_SERVICE:-' in helper


PRINCIPAL_ROOT = "/run/pantheon-principals"
_INTERPOLATION = re.compile(r"^\$\{([A-Z0-9_]+):-(.*)\}$")


def _issued_environment() -> dict[str, str]:
    return issue_environment(
        {
            "PANTHEON_ENV": "dev",
            "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
            "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
            "PANTHEON_DEV_BFF_JWT_SECRET": "contract-test-signing-key-" * 2,
            "PANTHEON_DEV_BFF_JWT_ISSUER": "contract-issuer",
            "PANTHEON_DEV_BFF_JWT_AUDIENCE": "contract-audience",
        },
        now=2_000_000_000,
    )


def _deploy_time_value(raw: object, issued: dict[str, str]) -> str:
    """The value the deploy shell gives the variable: the issuer exports it when compose defaults empty."""
    text = str(raw)
    match = _INTERPOLATION.match(text)
    if match is None:
        return text
    name, default = match.groups()
    return default or issued.get(name, "")


def principal_mount_gaps(compose: dict) -> list[str]:
    """Services whose *_TOKEN_FILE/*_JWT_FILE resolves under /run/pantheon-principals without a matching mount."""
    services = compose["services"]
    issued = _issued_environment()
    issuer_volumes = {
        mount.split(":")[0]: mount.split(":")[1].rsplit("/", 1)[-1]
        for mount in services["dev-paper-principal-issuer"]["volumes"]
        if ":/issued/" in mount
    }
    gaps: list[str] = []
    for name, service in services.items():
        environment = service.get("environment") or {}
        if isinstance(environment, list):
            environment = dict(item.split("=", 1) for item in environment if "=" in item)
        for variable, raw in environment.items():
            if not variable.endswith(("_TOKEN_FILE", "_JWT_FILE")):
                continue
            value = _deploy_time_value(raw, issued)
            if not value.startswith(PRINCIPAL_ROOT + "/"):
                continue
            mounts = [
                mount.split(":") for mount in service.get("volumes") or []
                if isinstance(mount, str) and mount.split(":")[1:2] == [PRINCIPAL_ROOT]
            ]
            if not mounts:
                gaps.append(f"{name}: {variable} has no mount at {PRINCIPAL_ROOT}")
                continue
            consumer = issuer_volumes.get(mounts[0][0])
            file_name = value[len(PRINCIPAL_ROOT) + 1:]
            if consumer is None or file_name not in CONSUMER_FILES.get(consumer, ()):
                gaps.append(f"{name}: {variable} -> {file_name} is not issued into volume {mounts[0][0]}")
    return gaps


def test_every_principal_file_variable_has_a_mount_that_carries_it() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert principal_mount_gaps(compose) == []


def test_producer_and_reconciler_capital_file_agrees_with_mount_and_issuer() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    issuer_mounts = services["dev-paper-principal-issuer"]["volumes"]
    issued = _issued_environment()
    for service_name in ("paper-signal-producer", "paper-fleet-reconciler"):
        service = services[service_name]
        value = _deploy_time_value(service["environment"]["DEPLOYMENT_CAPITAL_SERVICE_TOKEN_FILE"], issued)
        assert value == PRINCIPAL_ROOT + "/DEPLOYMENT_CAPITAL_SERVICE_TOKEN"
        mounts = [m for m in service["volumes"] if m.split(":")[1:2] == [PRINCIPAL_ROOT]]
        assert mounts == ["dev-paper-capital-reader-tokens:/run/pantheon-principals:ro"]
        assert "dev-paper-capital-reader-tokens:/issued/paper-capital-reader" in issuer_mounts
        assert CONSUMER_FILES["paper-capital-reader"] == ("DEPLOYMENT_CAPITAL_SERVICE_TOKEN",)
    assert "dev-paper-capital-reader-tokens" in compose["volumes"]
    # Never the deployment volume: it also carries governance and registry principals.
    for service_name in ("paper-signal-producer", "paper-fleet-reconciler"):
        assert not any(m.startswith("dev-paper-deployment-tokens") for m in services[service_name]["volumes"])


@pytest.mark.parametrize("service_name", ["paper-signal-producer", "paper-fleet-reconciler"])
def test_contract_flags_a_missing_or_wrong_mount(service_name: str) -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    mount = "dev-paper-capital-reader-tokens:/run/pantheon-principals:ro"
    compose["services"][service_name]["volumes"].remove(mount)
    assert principal_mount_gaps(compose) == [
        f"{service_name}: DEPLOYMENT_CAPITAL_SERVICE_TOKEN_FILE has no mount at {PRINCIPAL_ROOT}"
    ]
    compose["services"][service_name]["volumes"].append("dev-paper-runtime-tokens:/run/pantheon-principals:ro")
    assert principal_mount_gaps(compose) == [
        f"{service_name}: DEPLOYMENT_CAPITAL_SERVICE_TOKEN_FILE -> DEPLOYMENT_CAPITAL_SERVICE_TOKEN "
        "is not issued into volume dev-paper-runtime-tokens"
    ]


def test_contract_flags_exactly_the_two_services_on_the_b7153eefd_compose() -> None:
    result = subprocess.run(
        ["git", "show", "b7153eefd:docker-compose.yml"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip("commit b7153eefd is not available in this checkout")
    gaps = principal_mount_gaps(yaml.safe_load(result.stdout))
    assert sorted(gap.split(":")[0] for gap in gaps) == ["paper-fleet-reconciler", "paper-signal-producer"]
