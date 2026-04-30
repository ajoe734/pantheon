from __future__ import annotations

import pytest

from services.source_search_posture import validate_source_search_posture


BASE_PROD_ENV = {
    "PANTHEON_SOURCE_SEARCH_POSTURE": "production",
    "DATABASE_URL": "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
    "PANTHEON_S3_ENDPOINT": "http://minio:9000",
    "PANTHEON_ARTIFACT_BUCKET": "pantheon-artifacts",
    "PANTHEON_S3_ACCESS_KEY": "pantheon",
    "PANTHEON_S3_SECRET_KEY": "pantheonminio",
}


def test_source_search_posture_keeps_dev_jsonl_rollback_available() -> None:
    check = validate_source_search_posture(
        "source-ingest",
        env={"PANTHEON_SOURCE_SEARCH_POSTURE": "dev", "SOURCE_INGEST_EVIDENCE_BACKEND": "jsonl"},
    )

    assert check.status == "ok"
    assert check.enforced is False
    assert check.backends["SOURCE_INGEST_EVIDENCE_BACKEND"] == "jsonl"


def test_source_ingest_production_posture_requires_postgres_and_object_store() -> None:
    check = validate_source_search_posture(
        "source-ingest",
        env={
            "PANTHEON_SOURCE_SEARCH_POSTURE": "production",
            "DATABASE_URL": "sqlite:///tmp/source.db",
            "SOURCE_INGEST_EVIDENCE_BACKEND": "jsonl",
        },
    )

    assert check.status == "error"
    assert "DATABASE_URL must be a Postgres DSN" in "; ".join(check.errors)
    assert "SOURCE_INGEST_EVIDENCE_BACKEND must be postgres" in "; ".join(check.errors)
    assert "PANTHEON_S3_ENDPOINT is required" in "; ".join(check.errors)


def test_search_production_posture_requires_durable_postgres_only_index() -> None:
    check = validate_source_search_posture(
        "search",
        env={
            **BASE_PROD_ENV,
            "SEARCH_INDEX_STORE_BACKEND": "postgres",
            "SEARCH_EVIDENCE_BACKEND": "postgres",
            "SEARCH_DURABLE_INDEX_ONLY": "true",
        },
    )

    assert check.status == "ok"
    assert check.enforced is True
    assert check.object_store_configured is True


def test_search_production_posture_rejects_request_document_mode() -> None:
    check = validate_source_search_posture(
        "search",
        env={
            **BASE_PROD_ENV,
            "SEARCH_INDEX_STORE_BACKEND": "postgres",
            "SEARCH_EVIDENCE_BACKEND": "postgres",
            "SEARCH_DURABLE_INDEX_ONLY": "false",
        },
    )

    assert check.status == "error"
    assert "SEARCH_DURABLE_INDEX_ONLY must be true" in "; ".join(check.errors)


def test_source_search_posture_rejects_unknown_service() -> None:
    with pytest.raises(ValueError, match="unknown source/search posture service"):
        validate_source_search_posture("unknown", env={})
