from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from services.knowledge.evidence import EvidenceBundleBuilder, EvidenceItem, JsonlEvidenceRepository
from services.search.main import create_app
from services.source_ingestion.connectors import SourceRecord
from services.source_search_posture import validate_source_search_posture


def _write_source_evidence(path: Path, source_id: str = "src-refresh-note") -> None:
    repository = JsonlEvidenceRepository(path)
    builder = EvidenceBundleBuilder(repository)
    source = SourceRecord(
        source_id=source_id,
        connector_id="conn-refresh",
        source_type="internal_note",
        title="Refresh note",
        content_ref=f"memory://refresh/{source_id}",
        metadata={"access_scope": ["operator", "research"]},
        trace_id="trace-refresh",
    )
    item = EvidenceItem(
        evidence_item_id=f"evi-{source_id}",
        source_id=source.source_id,
        item_type="text_chunk",
        content_ref=f"memory://refresh/{source_id}#chunk",
        citation_label=f"{source_id}#1",
        body="Search refresh evidence for bounded source search.",
        access_scope=["operator", "research"],
        trace_refs=[source.trace_id],
    )
    bundle = builder.build_bundle(
        source_records=[source],
        evidence_items=[item],
        summary="Refresh bundle.",
        created_by="test",
        evidence_bundle_id=f"evbundle-{source_id}",
    )
    builder.build_knowledge_object(
        knowledge_object_id=f"ko-{source_id}",
        source_record=source,
        evidence_item=item,
        evidence_bundle=bundle,
        title="Refresh note",
        text=item.body,
        keywords=["bounded", "refresh"],
    )


def test_search_index_refresh_records_pipeline_snapshot(tmp_path: Path) -> None:
    evidence_path = tmp_path / "source_evidence.jsonl"
    pipeline_path = tmp_path / "pipeline.jsonl"
    _write_source_evidence(evidence_path)

    client = TestClient(
        create_app(
            tmp_path / "search-index.jsonl",
            evidence_path,
            tmp_path / "search-materialize.jsonl",
            pipeline_path,
            freshness_sla_seconds=60,
        )
    )

    response = client.post(
        "/api/search/index/refresh",
        json={"triggered_by": "bounded_refresh_test", "trigger_ref": "ingest-run-1"},
    )

    assert response.status_code == 200, response.text
    snapshot = response.json()["pipeline_snapshot"]
    assert snapshot["triggered_by"] == "bounded_refresh_test"
    assert snapshot["trigger_ref"] == "ingest-run-1"
    assert snapshot["indexed_count"] == 1
    assert snapshot["incremental_count"] == 1
    assert pipeline_path.exists()

    runs = client.get("/api/search/index/pipeline-runs?limit=5")
    assert runs.status_code == 200, runs.text
    assert runs.json()["total"] == 1
    assert runs.json()["retention_runs"] == 500
    assert runs.json()["runs"][0]["pipeline_run_id"] == snapshot["pipeline_run_id"]


def test_source_completion_refresh_materializes_and_replays_truth(tmp_path: Path) -> None:
    evidence_path = tmp_path / "source_evidence.jsonl"
    materialize_path = tmp_path / "search-materialize.jsonl"
    pipeline_path = tmp_path / "pipeline.jsonl"
    _write_source_evidence(evidence_path)

    client = TestClient(
        create_app(
            tmp_path / "search-index.jsonl",
            evidence_path,
            materialize_path,
            pipeline_path,
            freshness_sla_seconds=60,
        )
    )

    response = client.post(
        "/api/search/index/source-completions",
        json={
            "ingest_run_id": "ingest-src005-1",
            "connector_id": "conn-refresh",
            "source_type": "internal_note",
            "trace_id": "trace-src005",
            "normalized_count": 1,
            "source_ids": ["src-refresh-note"],
            "evidence_bundle_id": "evbundle-src-refresh-note",
            "knowledge_object_ids": ["ko-src-refresh-note"],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "source_search_completion_refresh.v1"
    assert payload["pipeline_snapshot"]["triggered_by"] == "ingest_completion"
    assert payload["pipeline_snapshot"]["trigger_ref"] == "ingest-src005-1"
    assert payload["freshness"]["status"] == "fresh"
    assert payload["materialized_index"]["trigger_ref"] == "ingest-src005-1"
    assert payload["materialized_index"]["source_completion"]["source_ids"] == ["src-refresh-note"]
    assert payload["truth"]["index_refreshed"] is True
    assert payload["truth"]["materialized_matches_completion"] is True
    assert materialize_path.exists()

    replay = client.get("/api/search/index/source-completions/ingest-src005-1")
    assert replay.status_code == 200, replay.text
    assert replay.json()["truth"]["pipeline_run_id"] == payload["pipeline_snapshot"]["pipeline_run_id"]
    assert replay.json()["truth"]["materialized_matches_completion"] is True


def test_search_refresh_freshness_starts_closed_then_opens_after_refresh(tmp_path: Path) -> None:
    evidence_path = tmp_path / "source_evidence.jsonl"
    _write_source_evidence(evidence_path)
    client = TestClient(
        create_app(
            tmp_path / "search-index.jsonl",
            evidence_path,
            tmp_path / "search-materialize.jsonl",
            tmp_path / "pipeline.jsonl",
            freshness_sla_seconds=60,
        )
    )

    before = client.get("/api/search/index/freshness")
    assert before.status_code == 200
    assert before.json()["status"] == "never_indexed"
    assert before.json()["is_fresh"] is False
    assert before.json()["within_sla"] is False
    assert before.json()["last_run_at"] is None

    refresh = client.post("/api/search/index/refresh", json={"force_full": True})
    assert refresh.status_code == 200, refresh.text

    after = client.get("/api/search/index/freshness")
    assert after.status_code == 200
    assert after.json()["status"] == "fresh"
    assert after.json()["is_fresh"] is True
    assert after.json()["within_sla"] is True
    assert after.json()["last_run_at"] == after.json()["last_indexed_at"]
    assert after.json()["seconds_since_last_run"] == after.json()["staleness_seconds"]


def test_search_production_posture_fails_closed_without_durable_backend() -> None:
    check = validate_source_search_posture(
        "search",
        env={
            "PANTHEON_SOURCE_SEARCH_POSTURE": "production",
            "DATABASE_URL": "postgresql://pantheon:pantheon@postgres:5432/pantheon",
            "SEARCH_INDEX_STORE_BACKEND": "jsonl",
            "SEARCH_EVIDENCE_BACKEND": "postgres",
            "SEARCH_DURABLE_INDEX_ONLY": "false",
            "PANTHEON_S3_ENDPOINT": "http://minio:9000",
            "PANTHEON_ARTIFACT_BUCKET": "pantheon-artifacts",
            "PANTHEON_S3_ACCESS_KEY": "pantheon",
            "PANTHEON_S3_SECRET_KEY": "pantheonminio",
        },
    )

    assert check.status == "error"
    assert "SEARCH_INDEX_STORE_BACKEND must be postgres" in "; ".join(check.errors)
    assert "SEARCH_DURABLE_INDEX_ONLY must be true" in "; ".join(check.errors)
