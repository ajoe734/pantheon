"""Focused acceptance for the current twelve-loop Compose owner wiring.

This suite is intentionally structural: component tasks own runtime behavior,
while this task owns the single root ``docker-compose.yml`` composition.  The
tests verify that default rendering activates the canonical controllers, uses
credentials accepted by the existing authority contracts, and points health
monitoring at real service routes instead of guessed paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from services.runtime_auth_inbound import validate_request_auth


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]


def _default(interpolation: str) -> str:
    """Return the default from one required ``${NAME:-default}`` expression."""

    assert interpolation.startswith("${") and interpolation.endswith("}")
    _name, separator, value = interpolation[2:-1].partition(":-")
    assert separator == ":-"
    return value


def _env(service: str) -> dict[str, str]:
    environment = SERVICES[service].get("environment") or {}
    assert isinstance(environment, dict)
    return environment


def test_source_controller_is_the_single_default_durable_owner() -> None:
    owner = SERVICES["source-ingest-scheduler"]

    assert "profiles" not in owner
    assert owner["restart"] == "unless-stopped"
    assert owner["stop_grace_period"] == "30s"
    assert owner["command"] == [
        "python",
        "-m",
        "services.source_ingestion.controller_worker",
    ]
    assert (
        owner["environment"]["SOURCE_INGEST_CONTROLLER_MAX_TICKS"]
        == "${SOURCE_INGEST_CONTROLLER_MAX_TICKS:-0}"
    )

    matching_commands = [
        name
        for name, service in SERVICES.items()
        if service.get("command") == owner["command"]
    ]
    assert matching_commands == ["source-ingest-scheduler"]

    # A bounded run remains an explicit override of this same service:
    # docker compose run --rm -e SOURCE_INGEST_CONTROLLER_MAX_TICKS=1 ...
    assert "SOURCE_INGEST_CONTROLLER_MAX_TICKS" in owner["environment"]


def test_teaching_worker_uses_verified_jwt_and_agora_uses_one_owner_url() -> None:
    teaching_api = _env("training-session-svc")
    teaching_worker = _env("training-session-preview-worker")
    teaching_token = _default(teaching_worker["TRAINING_SESSION_WORKER_TOKEN"])
    teaching_context = validate_request_auth(
        authorization=f"Bearer {teaching_token}",
        required_roles=("training-service",),
        env={
            "PANTHEON_RUNTIME_AUTH_MODE": "strict",
            "PANTHEON_RUNTIME_JWT_SECRET": _default(
                teaching_api["TRAINING_SESSION_JWT_SECRET"]
            ),
        },
    )
    assert teaching_context.actor_id == "training-session-preview-worker"
    assert teaching_context.claims["service"] == "training-session-preview-worker"
    assert teaching_context.claims["allowed_tenants"] == ["*"]
    assert teaching_worker["TRAINING_SESSION_WORKER_SERVICE_ID"] == (
        "training-session-preview-worker"
    )

    policy_api = _env("policy-learning-svc")
    policy_worker = _env("policy-learning-shadow-eval-scheduler")
    assert policy_worker["POLICY_LEARNING_SERVICE_TOKEN"] == policy_api[
        "POLICY_LEARNING_SERVICE_TOKEN"
    ]
    assert _default(policy_worker["POLICY_LEARNING_SERVICE_TOKEN"]) == (
        "pantheon-local-policy-learning-service"
    )
    assert policy_worker["AGORA_BFF_URL"] == "http://operator-bff:8001"
    assert policy_worker["RESEARCH_SERVICE_URL"] == (
        "http://research-orchestrator-svc:8101"
    )


def test_consultation_provider_and_functional_health_are_explicit() -> None:
    consultation = SERVICES["consultation-svc"]
    consultation_env = _env("consultation-svc")
    adapter_env = _env("openclaw-gateway-adapter")

    assert consultation_env["CONSULTATION_PROVIDER_URL"] == (
        "http://openclaw-gateway-adapter:8104"
        "/api/openclaw-adapter/consultation/contributions"
    )
    assert consultation_env["CONSULTATION_PROVIDER_TOKEN"] == adapter_env[
        "CONSULTATION_PROVIDER_TOKEN"
    ]
    assert consultation_env["CONSULTATION_PROVIDER_SERVICE_ACTOR"] == (
        adapter_env["CONSULTATION_PROVIDER_ALLOWED_SERVICE_ACTOR"]
    )
    assert consultation_env["CONSULTATION_PROVIDER_SERVICE_ACTOR"] == (
        "consultation-workflow-executor"
    )
    assert consultation["depends_on"]["openclaw-gateway-adapter"]["condition"] == (
        "service_healthy"
    )

    healthcheck = " ".join(consultation["healthcheck"]["test"])
    assert "CONSULTATION_WORKFLOW_EXECUTOR_HEALTH_FILE" in healthcheck
    assert "functional_health" in healthcheck
    assert "blocked_count" in healthcheck
    assert "dead_letter_count" in healthcheck
    assert consultation_env["CONSULTATION_HANDOFF_SINK_URL"] == (
        "${CONSULTATION_HANDOFF_SINK_URL:-}"
    )


def test_deployment_capital_and_evolution_use_component_contracts() -> None:
    deployment = SERVICES["deployment-outbox-consumer"]
    assert _env("deployment-outbox-consumer")[
        "PANTHEON_DEPLOYMENT_SERVICE_TOKEN"
    ] == "${PANTHEON_DEPLOYMENT_SERVICE_TOKEN:-deployment-outbox-consumer:deployment_consumer}"
    assert deployment["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "services.deployment.outbox_consumer_worker",
        "healthcheck",
    ]

    assert _env("paper-signal-producer")["PAPER_SIGNAL_STRATEGY"] == (
        "${PAPER_SIGNAL_STRATEGY:-artifact}"
    )
    postmortems = _env("postmortems")
    evolution = _env("evolution")
    assert postmortems["EVOLUTION_AUTH_TOKEN"] == evolution["EVOLUTION_AUTH_TOKEN"]
    assert postmortems["EVOLUTION_DEFAULT_TENANT_ID"] == (
        "${EVOLUTION_DEFAULT_TENANT_ID:-pantheon-default}"
    )


def test_bff_health_registry_uses_real_typed_paths_and_telemetry_identity() -> None:
    bff = _env("operator-bff")
    telemetry = _env("telemetry")
    targets = json.loads(bff["PANTHEON_BFF_HEALTH_TARGETS_JSON"])

    assert len(targets) == len({item["name"] for item in targets})
    assert all(item["health_path"].startswith("/") for item in targets)
    assert all(item["component_kind"] for item in targets)
    target_map = {item["name"]: item for item in targets}
    assert target_map["source-ingest"]["health_path"] == "/readyz"
    assert target_map["training-session"]["health_path"] == "/readyz"
    assert target_map["consultation"]["health_path"] == "/readyz"
    assert target_map["deployment"]["health_path"] == "/readyz"
    assert target_map["policy-learning"]["health_path"] == "/readyz"
    assert target_map["evolution"]["health_path"] == "/readyz"
    assert target_map["openclaw-gateway-adapter"]["health_path"] == "/readyz"

    producer = bff["PANTHEON_BFF_HEALTH_PRODUCER"]
    assert telemetry["PANTHEON_TELEMETRY_INFRA_PRODUCERS"] == (
        "${PANTHEON_TELEMETRY_INFRA_PRODUCERS:-control-plane-bff}"
    )
    health_token = _default(bff["PANTHEON_BFF_HEALTH_TELEMETRY_JWT"])
    health_context = validate_request_auth(
        authorization=f"Bearer {health_token}",
        required_roles=("service",),
        env={
            "PANTHEON_RUNTIME_AUTH_MODE": "strict",
            "PANTHEON_RUNTIME_JWT_SECRET": _default(
                telemetry["PANTHEON_TELEMETRY_JWT_SECRET"]
            ),
        },
    )
    assert health_context.claims["allowed_tenants"] == [
        _default(bff["PANTHEON_BFF_HEALTH_TENANT_ID"])
    ]
    assert health_context.claims["allowed_producers"] == [producer]


def test_default_owner_services_are_not_hidden_behind_profiles() -> None:
    canonical_default_owners = {
        "source-ingest-scheduler",
        "strategy-distillation-worker",
        "alpha-replication-worker",
        "training-session-preview-worker",
        "policy-learning-shadow-eval-scheduler",
        "consultation-svc",
        "deployment-outbox-consumer",
        "paper-signal-producer",
        "reconciliation-drift-scheduler",
        "evolution-dispatch-worker",
        "evolution-daily-sweep-scheduler",
        "evolution-threshold-sweep-producer",
        "loop-run-projector-scheduler",
    }

    assert canonical_default_owners <= SERVICES.keys()
    assert {
        name for name in canonical_default_owners if "profiles" in SERVICES[name]
    } == set()
