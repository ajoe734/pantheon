"""Static contract checks for the governance-api service family."""
from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _container_port(service: dict, port: str) -> bool:
    return any(str(mapping).endswith(f":{port}") for mapping in service.get("ports", []))


def test_governance_api_family_is_wired_in_root_compose() -> None:
    services = _compose()["services"]

    expected = {
        "governance": ("services/governance/Dockerfile", "8082", "/readyz"),
        "deployment": ("services/deployment/Dockerfile", "8095", "/readyz"),
        "capital": ("services/capital/Dockerfile", "8092", "/readyz"),
        "evolution": ("services/evolution/Dockerfile", "8093", "/readyz"),
    }

    for service_name, (dockerfile, port, health_path) in expected.items():
        service = services[service_name]
        assert service["build"]["dockerfile"] == dockerfile
        assert _container_port(service, port)
        healthcheck = " ".join(service["healthcheck"]["test"])
        assert health_path in healthcheck


def test_operator_bff_discovers_governance_api_family_by_explicit_env_vars() -> None:
    services = _compose()["services"]
    operator_bff = services["operator-bff"]
    env = operator_bff["environment"]

    assert env["PANTHEON_GOVERNANCE_APPROVAL_API_URL"] == "http://governance:8082"
    assert env["PANTHEON_DEPLOYMENT_API_URL"] == "http://deployment:8095"
    assert env["PANTHEON_CAPITAL_API_URL"] == "http://capital:8092"
    assert env["PANTHEON_EVOLUTION_API_URL"] == "http://evolution:8093"
    assert env["PANTHEON_RUNTIME_MANAGER_URL"] == "http://runtime-manager:8081"

    dependencies = set(operator_bff["depends_on"])
    assert {
        "governance",
        "deployment",
        "capital",
        "evolution",
        "runtime-manager",
    }.issubset(dependencies)


def test_deployment_service_uses_shared_governance_snapshot_volume() -> None:
    deployment = _compose()["services"]["deployment"]

    assert deployment["environment"]["DEPLOYMENT_DATA_DIR"] == "/data/governance"
    assert deployment["environment"]["PANTHEON_GOVERNANCE_DATA_DIR"] == "/data/governance"
    assert "governance-data:/data/governance" in deployment["volumes"]
    assert deployment["depends_on"]["governance"]["condition"] == "service_healthy"
