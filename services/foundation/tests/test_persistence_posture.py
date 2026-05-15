from __future__ import annotations

from pathlib import Path

import pytest

from services.foundation.persistence_posture import (
    validate_persistence_posture,
    require_persistence_posture,
)
from services.source_search_posture import validate_source_search_posture


BASE_PROD_ENV = {
    "PANTHEON_PERSISTENCE_POSTURE": "production",
    "DATABASE_URL": "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
    "PANTHEON_S3_ENDPOINT": "http://minio:9000",
    "PANTHEON_ARTIFACT_BUCKET": "pantheon-artifacts",
    "PANTHEON_S3_ACCESS_KEY": "pantheon",
    "PANTHEON_S3_SECRET_KEY": "pantheonminio",
}


def test_dev_mode_allows_json_fallback_and_marks_it_dev_only() -> None:
    check = validate_persistence_posture(
        "governance",
        env={"PANTHEON_PERSISTENCE_POSTURE": "dev", "GOVERNANCE_STORE_BACKEND": "json"},
    )

    assert check.status == "ok"
    assert check.enforced is False
    assert check.backends["GOVERNANCE_STORE_BACKEND"] == "json"
    assert check.to_dict()["dev_fallback_allowed"] is True


def test_staging_live_env_requires_postgres_and_object_store() -> None:
    check = validate_persistence_posture(
        "governance",
        env={
            "PANTHEON_ENV": "staging-live",
            "DATABASE_URL": "sqlite:///tmp/governance.db",
            "GOVERNANCE_STORE_BACKEND": "json",
            "GOVERNANCE_AUDIT_BACKEND": "jsonl",
        },
    )

    errors = "; ".join(check.errors)
    assert check.status == "error"
    assert check.enforced is True
    assert "DATABASE_URL must be a Postgres DSN" in errors
    assert "GOVERNANCE_STORE_BACKEND must be postgres" in errors
    assert "GOVERNANCE_AUDIT_BACKEND must be postgres" in errors
    assert "PANTHEON_S3_ENDPOINT is required" in errors


def test_prod_posture_accepts_postgres_backends_and_object_store() -> None:
    check = validate_persistence_posture(
        "research-worker-gateway",
        env={
            **BASE_PROD_ENV,
            "RESEARCH_WORKER_GATEWAY_EVENT_STORE_BACKEND": "postgres",
        },
    )

    assert check.status == "ok"
    assert check.enforced is True
    assert check.object_store_configured is True
    assert check.database_backend == "postgres"


def test_require_persistence_posture_fails_fast() -> None:
    with pytest.raises(RuntimeError, match="TRAINING_SESSION_EVENT_STORE_BACKEND must be postgres"):
        require_persistence_posture(
            "training-session",
            env={
                **BASE_PROD_ENV,
                "TRAINING_SESSION_EVENT_STORE_BACKEND": "jsonl",
            },
        )


def test_unknown_service_rejected() -> None:
    with pytest.raises(ValueError, match="unknown persistence posture service"):
        validate_persistence_posture("unknown", env={})


def test_prod_control_env_example_satisfies_platform_posture() -> None:
    env_path = Path(__file__).resolve().parents[3] / "env" / "prod-control.env.example"
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            env[key] = value

    for service in (
        "consultation",
        "training-session",
        "policy-learning",
        "research-orchestrator",
        "research-worker-gateway",
        "governance",
        "capital",
        "incidents",
        "postmortems",
        "promotion",
        "memory",
        "reconciliation-drift",
    ):
        assert validate_persistence_posture(service, env=env).status == "ok"
    assert validate_source_search_posture("source-ingest", env=env).status == "ok"
    assert validate_source_search_posture("search", env=env).status == "ok"


# ---------------------------------------------------------------------------
# Wave 4: hard enforcement — staging/prod compose defaults reject json/jsonl
# ---------------------------------------------------------------------------

STAGING_COMPOSE_BASE_ENV = {
    "DATABASE_URL": "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
    "PANTHEON_S3_ENDPOINT": "http://minio:9000",
    "PANTHEON_ARTIFACT_BUCKET": "pantheon-artifacts",
    "PANTHEON_S3_ACCESS_KEY": "pantheon",
    "PANTHEON_S3_SECRET_KEY": "pantheonminio",
}


def test_wave4_control_compose_governance_rejects_json_in_production_default() -> None:
    """docker-compose.control.yml defaults PANTHEON_PERSISTENCE_POSTURE to production;
    governance must reject json backends without explicit dev override."""
    check = validate_persistence_posture(
        "governance",
        env={
            **STAGING_COMPOSE_BASE_ENV,
            "PANTHEON_PERSISTENCE_POSTURE": "production",
            "GOVERNANCE_STORE_BACKEND": "json",
            "GOVERNANCE_AUDIT_BACKEND": "jsonl",
        },
    )
    assert check.enforced is True
    assert check.status == "error"
    assert "GOVERNANCE_STORE_BACKEND must be postgres" in "; ".join(check.errors)
    assert "GOVERNANCE_AUDIT_BACKEND must be postgres" in "; ".join(check.errors)


def test_wave4_control_compose_governance_accepts_postgres_in_production_default() -> None:
    check = validate_persistence_posture(
        "governance",
        env={
            **STAGING_COMPOSE_BASE_ENV,
            "PANTHEON_PERSISTENCE_POSTURE": "production",
            "GOVERNANCE_STORE_BACKEND": "postgres",
            "GOVERNANCE_AUDIT_BACKEND": "postgres",
        },
    )
    assert check.enforced is True
    assert check.status == "ok"


def test_wave4_control_compose_capital_rejects_json_in_production_default() -> None:
    check = validate_persistence_posture(
        "capital",
        env={
            **STAGING_COMPOSE_BASE_ENV,
            "PANTHEON_PERSISTENCE_POSTURE": "production",
            "CAPITAL_STORE_BACKEND": "json",
            "CAPITAL_AUDIT_BACKEND": "jsonl",
        },
    )
    assert check.enforced is True
    assert check.status == "error"
    assert "CAPITAL_STORE_BACKEND must be postgres" in "; ".join(check.errors)


def test_wave4_dev_fallback_still_allowed_with_explicit_dev_posture() -> None:
    """Dev single-VM can still use json/jsonl by setting PANTHEON_PERSISTENCE_POSTURE=dev."""
    for service, backend_key, backend_val in [
        ("governance", "GOVERNANCE_STORE_BACKEND", "json"),
        ("capital", "CAPITAL_STORE_BACKEND", "json"),
        ("memory", "PANTHEON_MEMORY_STORE_BACKEND", "json"),
        ("incidents", "INCIDENT_STORE_BACKEND", "json"),
        ("promotion", "PROMOTION_STORE_BACKEND", "json"),
        ("reconciliation-drift", "RECONCILIATION_DRIFT_STORE_BACKEND", "json"),
    ]:
        check = validate_persistence_posture(
            service,
            env={"PANTHEON_PERSISTENCE_POSTURE": "dev", backend_key: backend_val},
        )
        assert check.status == "ok", f"{service} should allow json fallback in dev mode"
        assert check.enforced is False


def test_wave4_staging_full_compose_consultation_rejects_jsonl_in_production_default() -> None:
    check = validate_persistence_posture(
        "consultation",
        env={
            **STAGING_COMPOSE_BASE_ENV,
            "PANTHEON_PERSISTENCE_POSTURE": "production",
            "CONSULTATION_STORE_BACKEND": "jsonl",
        },
    )
    assert check.enforced is True
    assert check.status == "error"
    assert "CONSULTATION_STORE_BACKEND must be postgres" in "; ".join(check.errors)


def test_wave4_staging_full_compose_training_session_rejects_jsonl_in_production_default() -> None:
    check = validate_persistence_posture(
        "training-session",
        env={
            **STAGING_COMPOSE_BASE_ENV,
            "PANTHEON_PERSISTENCE_POSTURE": "production",
            "TRAINING_SESSION_EVENT_STORE_BACKEND": "jsonl",
        },
    )
    assert check.enforced is True
    assert check.status == "error"
