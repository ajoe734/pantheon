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
