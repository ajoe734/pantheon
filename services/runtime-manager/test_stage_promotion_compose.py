from __future__ import annotations

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_dev_compose_wires_shared_jwt_verification_and_fail_closed_switches():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    runtime = compose["services"]["runtime-manager"]["environment"]
    governance = compose["services"]["governance"]["environment"]
    bff = compose["services"]["operator-bff"]["environment"]
    worker = compose["services"]["deployment-outbox-consumer"]["environment"]

    assert runtime["PANTHEON_RUNTIME_JWT_SECRET"] == "${PANTHEON_RUNTIME_JWT_SECRET:-${PANTHEON_BFF_JWT_SECRET:-${PANTHEON_DEV_BFF_JWT_SECRET:-}}}"
    assert runtime["PANTHEON_CANARY_EXECUTION_ENABLED"] == "${PANTHEON_CANARY_EXECUTION_ENABLED:-false}"
    assert runtime["PANTHEON_LIVE_BROKER_ENABLED"] == "${PANTHEON_LIVE_BROKER_ENABLED:-false}"
    assert governance["PANTHEON_GOVERNANCE_JWT_SECRET"] == "${PANTHEON_GOVERNANCE_JWT_SECRET:-${PANTHEON_DEV_BFF_JWT_SECRET:-}}"
    assert bff["PANTHEON_GOVERNANCE_JWT_SECRET"] == "${PANTHEON_GOVERNANCE_JWT_SECRET:-${PANTHEON_DEV_BFF_JWT_SECRET:-}}"
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
    # The helper centralizes dev runtime environment exports and must keep canary disabled
    helper_match = re.search(r"with_dev_bff_runtime_env\(\)\s*\{([\s\S]*?)\n\}", script)
    assert helper_match is not None, "with_dev_bff_runtime_env definition not found"
    assert "PANTHEON_CANARY_EXECUTION_ENABLED=false" in helper_match.group(1)

    # Every dev rollout path (root and bff) must execute through with_dev_bff_runtime_env
    remote_case_match = re.search(
        r'case "\$\{PANTHEON_DEPLOY_COMPONENT\}" in([\s\S]*?)\nesac', script
    )
    assert remote_case_match is not None, "PANTHEON_DEPLOY_COMPONENT case block not found"
    remote_case = remote_case_match.group(1)

    for component in ("root", "bff"):
        comp_match = re.search(
            rf"^\s*{component}\)([\s\S]*?);;", remote_case, re.MULTILINE
        )
        assert comp_match is not None, f"Rollout path '{component}' not found in deploy script"
        assert "with_dev_bff_runtime_env" in comp_match.group(1), (
            f"Rollout path '{component}' must execute through with_dev_bff_runtime_env"
        )


def test_compose_jwt_secrets_fail_closed_without_literal_defaults():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose.get("services", {})
    jwt_secret_entries = []

    for service_name, service_config in services.items():
        environment = service_config.get("environment") or {}
        if isinstance(environment, dict):
            for var_name, var_value in environment.items():
                if "JWT_SECRET" in var_name:
                    jwt_secret_entries.append((service_name, var_name, var_value))

    assert len(jwt_secret_entries) > 0, "No JWT_SECRET entries found in docker-compose.yml"

    for service_name, var_name, var_value in jwt_secret_entries:
        assert isinstance(var_value, str), f"{service_name}.{var_name} must be a string"
        assert var_value.startswith("${") and var_value.endswith("}"), (
            f"{service_name}.{var_name} ({var_value}) must be an interpolation expression"
        )
        assert not re.search(r":-[^\$\}]", var_value), (
            f"{service_name}.{var_name} ({var_value}) contains a literal default"
        )
        assert var_value.rstrip("}").endswith(":-"), (
            f"{service_name}.{var_name} ({var_value}) must end with ':-' before closing braces (fail closed, empty default)"
        )
        assert "pantheon-local-" not in var_value, (
            f"{service_name}.{var_name} ({var_value}) must not contain published default 'pantheon-local-'"
        )
