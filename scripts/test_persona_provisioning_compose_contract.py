from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL_DEFAULT = "${DATABASE_URL:-postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon}"
REGISTRY_API_URL_DEFAULT = "${PANTHEON_REGISTRY_API_URL:-http://registry:8087}"
PAPER_FLEET_URL_DEFAULT = (
    "${PANTHEON_PAPER_FLEET_RECONCILER_URL:-http://paper-fleet-reconciler:8011}"
)


class _ComposeLoader(yaml.SafeLoader):
    """Treat Compose's !override tag as its underlying YAML value."""


def _construct_override(loader: _ComposeLoader, node: yaml.Node) -> object:
    if isinstance(node, MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, ScalarNode):
        return loader.construct_scalar(node)
    raise TypeError(f"unsupported !override node: {type(node).__name__}")


_ComposeLoader.add_constructor("!override", _construct_override)


def _compose_service(compose_path: str) -> dict[str, object]:
    compose = yaml.load((ROOT / compose_path).read_text(encoding="utf-8"), Loader=_ComposeLoader)
    return compose["services"]["operator-bff"]


def _env_contract(env_path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (ROOT / env_path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


@pytest.mark.parametrize("compose_path", ["docker-compose.yml", "docker-compose.control.yml"])
def test_operator_bff_uses_durable_persona_provisioning_store(compose_path: str) -> None:
    service = _compose_service(compose_path)
    environment = service["environment"]

    assert environment["PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND"] == (
        "${PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND:-postgres}"
    )
    assert "memory" not in environment["PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND"].lower()
    assert environment["PANTHEON_PERSONA_PROVISIONING_STORE_DSN"] == DATABASE_URL_DEFAULT
    assert environment["PANTHEON_PERSONA_PROVISIONING_STORE_SCHEMA"] == (
        "${PANTHEON_PERSONA_PROVISIONING_STORE_SCHEMA:-bff}"
    )


@pytest.mark.parametrize("compose_path", ["docker-compose.yml", "docker-compose.control.yml"])
def test_operator_bff_uses_canonical_registry_owner_url(compose_path: str) -> None:
    service = _compose_service(compose_path)

    assert service["environment"]["PANTHEON_REGISTRY_API_URL"] == REGISTRY_API_URL_DEFAULT
    assert service["depends_on"]["registry"] == {"condition": "service_healthy"}


@pytest.mark.parametrize(
    ("compose_path", "expected"),
    [
        ("docker-compose.yml", PAPER_FLEET_URL_DEFAULT),
        ("docker-compose.control.yml", "${PANTHEON_PAPER_FLEET_RECONCILER_URL:-}"),
    ],
)
def test_operator_bff_reads_authoritative_paper_worker_sessions(
    compose_path: str,
    expected: str,
) -> None:
    service = _compose_service(compose_path)

    assert service["environment"]["PANTHEON_PAPER_FLEET_RECONCILER_URL"] == expected


@pytest.mark.parametrize("env_path", [".env.example", "env/prod-control.env.example"])
def test_environment_examples_document_durable_persona_contract(env_path: str) -> None:
    environment = _env_contract(env_path)

    assert environment["PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND"] == "postgres"
    assert environment["PANTHEON_PERSONA_PROVISIONING_STORE_SCHEMA"] == "bff"
    assert environment["PANTHEON_REGISTRY_API_URL"] == "http://registry:8087"
    assert "PANTHEON_PAPER_FLEET_RECONCILER_URL" in environment
