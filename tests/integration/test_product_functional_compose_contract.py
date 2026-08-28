"""Product-functional closure Compose topology contract.

This suite protects the narrow integration boundary between the repaired
component owners.  It deliberately inspects the rendered root topology rather
than re-testing component behavior: component tests remain each owner's
responsibility.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _environment(service: dict) -> dict:
    environment = service.get("environment") or {}
    assert isinstance(environment, dict)
    return environment


def _healthy_dependency(service: dict, dependency: str) -> None:
    assert service["depends_on"][dependency]["condition"] == "service_healthy"


def test_source_default_is_zero_egress_and_snapshot_state_is_durable() -> None:
    compose = _compose()
    services = compose["services"]
    source = services["source-ingest"]
    scheduler = services["source-ingest-scheduler"]

    source_env = _environment(source)
    scheduler_env = _environment(scheduler)
    assert source_env["SOURCE_INGEST_LATEST_MARKET_SNAPSHOT_PATH"] == (
        "/data/source-ingest/latest_market_snapshots.jsonl"
    )
    assert "source-ingest-data:/data/source-ingest" in source["volumes"]
    assert "/readyz" in " ".join(source["healthcheck"]["test"])

    # Reconciliation is the default service behavior. A pull requires the
    # explicit bounded deployment profile and exact allowlists.
    assert "profiles" not in scheduler
    assert scheduler_env["SOURCE_INGEST_CONTROLLER_MODE"] == (
        "${SOURCE_INGEST_CONTROLLER_MODE:-reconcile_only}"
    )
    assert scheduler_env["SOURCE_INGEST_CONTROLLER_MAX_TICKS"] == (
        "${SOURCE_INGEST_CONTROLLER_MAX_TICKS:-0}"
    )
    assert scheduler["restart"] == "${SOURCE_INGEST_CONTROLLER_RESTART_POLICY:-unless-stopped}"


def test_agora_management_and_provider_use_exact_healthy_owners() -> None:
    services = _compose()["services"]
    bff = services["operator-bff"]
    bff_env = _environment(bff)

    expected_owner_urls = {
        "PANTHEON_REGISTRY_API_URL": "http://registry:8087",
        "PANTHEON_CONSULTATION_API_URL": "http://consultation-svc:8096",
        "PANTHEON_SOURCE_INGEST_API_URL": "http://source-ingest:8097",
        "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL": "http://research-orchestrator-svc:8101",
        "PANTHEON_RESEARCH_WORKER_GATEWAY_API_URL": "http://research-worker-gateway-svc:8103",
        "PANTHEON_PAPER_FLEET_RECONCILER_URL": "${PANTHEON_PAPER_FLEET_RECONCILER_URL:-http://paper-fleet-reconciler:8011}",
        "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": "http://openclaw-gateway-adapter:8104",
    }
    for name, expected in expected_owner_urls.items():
        assert bff_env[name] == expected
    assert "bff-data:/data/bff" in bff["volumes"]

    for dependency in (
        "registry",
        "consultation-svc",
        "source-ingest",
        "research-orchestrator-svc",
        "research-worker-gateway-svc",
        "paper-fleet-reconciler",
        "openclaw-gateway-adapter",
    ):
        _healthy_dependency(bff, dependency)

    health_targets = {
        target["name"]: target["health_path"]
        for target in json.loads(bff_env["PANTHEON_BFF_HEALTH_TARGETS_JSON"])
    }
    assert health_targets["source-ingest"] == "/readyz"
    assert health_targets["research-orchestrator"] == "/readyz"
    assert health_targets["research-worker-gateway"] == "/readyz"
    assert health_targets["paper-fleet-reconciler"] == "/readyz"
    assert health_targets["openclaw-gateway-adapter"] == "/readyz"

    consultation = services["consultation-svc"]
    consultation_env = _environment(consultation)
    assert consultation_env["CONSULTATION_PROVIDER_URL"] == (
        "http://openclaw-gateway-adapter:8104/"
        "api/openclaw-adapter/consultation/contributions"
    )
    assert "consultation-data:/data/consultation" in consultation["volumes"]
    _healthy_dependency(consultation, "openclaw-gateway-adapter")
    _healthy_dependency(consultation, "governance")

    adapter = services["openclaw-gateway-adapter"]
    assert "/livez" in " ".join(adapter["healthcheck"]["test"])
    assert "openclaw-adapter-data:/root/.openclaw" in adapter["volumes"]


def test_only_dynamic_executable_paper_fleet_is_default_and_is_source_bound() -> None:
    services = _compose()["services"]
    fleet = services["paper-fleet-reconciler"]
    producer = services["paper-signal-producer"]
    static_runtime = services["pantheon-paper-runtime"]

    fleet_env = _environment(fleet)
    producer_env = _environment(producer)
    assert "profiles" not in fleet
    assert static_runtime["profiles"] == ["static-paper-runtime"]
    assert fleet_env["PANTHEON_SOURCE_INGEST_URL"] == "http://source-ingest:8097"
    assert fleet_env["PANTHEON_PERFORMANCE_STATE_ROOT"] == "/data/runtime/paper-performance"
    assert "runtime-data:/data/runtime" in fleet["volumes"]
    _healthy_dependency(fleet, "runtime-manager")
    _healthy_dependency(fleet, "source-ingest")

    # The producer is also a current-artifact consumer, so it must never fall
    # back to an implicit localhost snapshot endpoint before Source is ready.
    assert producer_env["PANTHEON_SOURCE_INGEST_URL"] == "http://source-ingest:8097"
    assert producer_env["PAPER_SIGNAL_STRATEGY"] == "${PAPER_SIGNAL_STRATEGY:-artifact}"
    _healthy_dependency(producer, "runtime-manager")
    _healthy_dependency(producer, "signal-store")
    _healthy_dependency(producer, "source-ingest")

    for service in (fleet, producer, static_runtime, services["runtime-manager"]):
        environment = _environment(service)
        assert environment.get("PANTHEON_LIVE_BROKER_ENABLED", "false") == "false" or environment.get(
            "PANTHEON_LIVE_BROKER_ENABLED"
        ) == "${PANTHEON_LIVE_BROKER_ENABLED:-false}"
        assert environment.get("PANTHEON_CANARY_EXECUTION_ENABLED", "false") == "false" or environment.get(
            "PANTHEON_CANARY_EXECUTION_ENABLED"
        ) == "${PANTHEON_CANARY_EXECUTION_ENABLED:-false}"


def test_legacy_profiles_remain_explicit_compatibility_or_operator_paths() -> None:
    services = _compose()["services"]

    # No legacy profile is silently made part of the root topology.  The static
    # runtime remains an explicit compatibility/test path while the dynamic
    # reconciler is the sole default paper owner.
    assert services["pantheon-paper-runtime"]["profiles"] == ["static-paper-runtime"]
    assert services["source-ingest-agora-projector"]["profiles"] == [
        "source-ingest-scheduler"
    ]
    assert services["openclaw-gateway"]["profiles"] == ["openclaw"]


def test_dev_login_ttl_contract_supports_bounded_proof_window() -> None:
    services = _compose()["services"]
    bff = services["operator-bff"]
    bff_env = _environment(bff)
    assert bff_env["PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS"] == (
        "${PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS:-1800}"
    )
def test_postgres_container_shared_memory_floor_is_at_least_256m() -> None:
    services = _compose()["services"]
    postgres = services.get("postgres")
    assert postgres is not None, "postgres service must exist in docker-compose.yml"
    assert "shm_size" in postgres, (
        "postgres service must declare shm_size (container default 64MB fails VACUUM with ENOSPC)"
    )
    shm_size_str = str(postgres["shm_size"]).strip().lower()
    assert shm_size_str in ("256m", "256mb", "512m", "512mb", "1g", "1gb"), (
        f"postgres shm_size must be at least 256m, got: {postgres['shm_size']}"
    )
