"""Tests for the incremental search indexing pipeline.

Covers:
- Full rebuild on first run (no prior snapshot)
- Incremental update (only new/modified objects processed)
- Stale source handling (object removed from repository)
- Freshness SLA tracking (fresh vs stale)
- Schema versioning in pipeline snapshots
- Retention pruning in JsonlIndexPipelineStore
- HTTP endpoints: /api/search/index/refresh, /freshness, /pipeline-runs
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest import mock

import pytest

from services.knowledge.evidence import (
    EvidenceBundle,
    EvidenceItem,
    InMemoryEvidenceRepository,
    KnowledgeObject,
)
from services.source_ingestion.connectors.base import SourceRecord

from services.search.index_pipeline import (
    IndexPipelineSnapshot,
    IncrementalIndexPipeline,
    JsonlIndexPipelineStore,
    PIPELINE_SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_repo_with_objects(object_ids: list[str], created_at: str | None = None) -> InMemoryEvidenceRepository:
    repo = InMemoryEvidenceRepository()
    at = created_at or "2026-04-30T01:00:00Z"
    for oid in object_ids:
        src = SourceRecord(
            source_id=f"src-{oid}",
            connector_id="test-connector",
            source_type="paper",
            title=f"Source {oid}",
            content_ref=f"ref://{oid}",
            trace_id=f"trace-{oid}",
        )
        item = EvidenceItem(
            evidence_item_id=f"evi-{oid}",
            source_id=src.source_id,
            item_type="text_chunk",
            content_ref=f"ref://{oid}#evi",
            citation_label=f"cite-{oid}",
            body=f"Body for {oid}",
        )
        bundle = EvidenceBundle(
            evidence_bundle_id=f"evb-{oid}",
            source_ids=[src.source_id],
            evidence_item_ids=[item.evidence_item_id],
            summary=f"Bundle {oid}",
            citation_refs=[item.citation_label],
            confidence=1.0,
            license_scope="internal",
            access_scope=["operator"],
            created_by="test",
        )
        ko = KnowledgeObject(
            knowledge_object_id=oid,
            source_id=src.source_id,
            evidence_item_id=item.evidence_item_id,
            evidence_bundle_id=bundle.evidence_bundle_id,
            title=f"Object {oid}",
            text=f"Text for {oid}",
            source_type="paper",
            license_scope="internal",
            access_scope=["operator"],
            environment_scope=["paper"],
            keywords=["test"],
            metadata={"created_at": at},
        )
        repo.add_source_record(src)
        repo.add_evidence_item(item)
        repo.add_bundle(bundle)
        repo.add_knowledge_object(ko)
    return repo


def _make_pipeline_store(tmp_path: Path) -> JsonlIndexPipelineStore:
    return JsonlIndexPipelineStore(tmp_path / "pipeline.jsonl", max_retention=10)


def _make_pipeline(repo: InMemoryEvidenceRepository, store: JsonlIndexPipelineStore, sla: int = 3600) -> IncrementalIndexPipeline:
    return IncrementalIndexPipeline(repo, store, freshness_sla_seconds=sla)


# ---------------------------------------------------------------------------
# JsonlIndexPipelineStore
# ---------------------------------------------------------------------------

def test_pipeline_store_empty_on_init(tmp_path):
    store = _make_pipeline_store(tmp_path)
    assert store.get_latest() is None
    assert store.list_runs() == []


def test_pipeline_store_record_and_reload(tmp_path):
    store = _make_pipeline_store(tmp_path)
    snap = IndexPipelineSnapshot(
        pipeline_run_id="run-001",
        schema_version=PIPELINE_SCHEMA_VERSION,
        triggered_by="manual",
        trigger_ref=None,
        indexed_count=5,
        incremental_count=3,
        removed_count=0,
        indexed_watermarks={"paper": "2026-04-30T01:00:00Z"},
        indexed_at="2026-04-30T02:00:00Z",
        is_full_rebuild=True,
        freshness_sla_seconds=3600,
        indexed_object_ids=["ko-a", "ko-b"],
    )
    returned = store.record_run(snap)
    assert returned.pipeline_run_id == "run-001"
    assert store.get_latest() is not None
    assert store.get_latest().pipeline_run_id == "run-001"

    # Reload from disk
    store2 = JsonlIndexPipelineStore(tmp_path / "pipeline.jsonl")
    assert store2.get_latest() is not None
    latest = store2.get_latest()
    assert latest.pipeline_run_id == "run-001"
    assert latest.schema_version == PIPELINE_SCHEMA_VERSION
    assert latest.indexed_count == 5
    assert latest.incremental_count == 3
    assert latest.indexed_object_ids == ["ko-a", "ko-b"]


def test_pipeline_store_latest_is_last_written(tmp_path):
    store = _make_pipeline_store(tmp_path)
    for i in range(3):
        store.record_run(IndexPipelineSnapshot(
            pipeline_run_id=f"run-{i:03d}",
            schema_version=PIPELINE_SCHEMA_VERSION,
            triggered_by="test",
            trigger_ref=None,
            indexed_count=i,
            incremental_count=0,
            removed_count=0,
            indexed_watermarks={},
            indexed_at=f"2026-04-30T0{i}:00:00Z",
            is_full_rebuild=False,
            freshness_sla_seconds=3600,
        ))
    assert store.get_latest().pipeline_run_id == "run-002"


def test_pipeline_store_retention_prune(tmp_path):
    store = JsonlIndexPipelineStore(tmp_path / "pipeline.jsonl", max_retention=50)
    for i in range(10):
        store.record_run(IndexPipelineSnapshot(
            pipeline_run_id=f"run-{i:03d}",
            schema_version=PIPELINE_SCHEMA_VERSION,
            triggered_by="test",
            trigger_ref=None,
            indexed_count=i,
            incremental_count=0,
            removed_count=0,
            indexed_watermarks={},
            indexed_at=f"2026-04-30T{i:02d}:00:00Z",
            is_full_rebuild=False,
            freshness_sla_seconds=3600,
        ))

    pruned = store.prune(max_keep=5)
    assert pruned == 5
    assert len(store.list_runs()) == 5
    assert store.get_latest().pipeline_run_id == "run-009"
    assert store.list_runs()[0].pipeline_run_id == "run-005"

    # After prune, disk file also has only 5 entries
    reloaded = JsonlIndexPipelineStore(tmp_path / "pipeline.jsonl", max_retention=5)
    assert len(reloaded.list_runs()) == 5


def test_pipeline_store_auto_retention_prunes_to_limit(tmp_path):
    store = JsonlIndexPipelineStore(tmp_path / "pipeline.jsonl", max_retention=5)
    for i in range(10):
        store.record_run(IndexPipelineSnapshot(
            pipeline_run_id=f"run-{i:03d}",
            schema_version=PIPELINE_SCHEMA_VERSION,
            triggered_by="test",
            trigger_ref=None,
            indexed_count=i,
            incremental_count=0,
            removed_count=0,
            indexed_watermarks={},
            indexed_at=f"2026-04-30T{i:02d}:00:00Z",
            is_full_rebuild=False,
            freshness_sla_seconds=3600,
        ))

    assert len(store.list_runs(limit=100)) == 5
    assert store.list_runs()[0].pipeline_run_id == "run-005"
    assert store.get_latest().pipeline_run_id == "run-009"

    reloaded = JsonlIndexPipelineStore(tmp_path / "pipeline.jsonl", max_retention=5)
    assert [run.pipeline_run_id for run in reloaded.list_runs(limit=100)] == [
        "run-005",
        "run-006",
        "run-007",
        "run-008",
        "run-009",
    ]


def test_pipeline_store_list_runs_limit(tmp_path):
    store = _make_pipeline_store(tmp_path)
    for i in range(8):
        store.record_run(IndexPipelineSnapshot(
            pipeline_run_id=f"run-{i}",
            schema_version=PIPELINE_SCHEMA_VERSION,
            triggered_by="test",
            trigger_ref=None,
            indexed_count=i,
            incremental_count=0,
            removed_count=0,
            indexed_watermarks={},
            indexed_at="2026-04-30T00:00:00Z",
            is_full_rebuild=False,
            freshness_sla_seconds=3600,
        ))
    assert len(store.list_runs(limit=3)) == 3
    assert len(store.list_runs(limit=100)) == 8


# ---------------------------------------------------------------------------
# IncrementalIndexPipeline — full rebuild
# ---------------------------------------------------------------------------

def test_pipeline_first_run_is_full_rebuild(tmp_path):
    repo = _make_repo_with_objects(["ko-a", "ko-b", "ko-c"])
    store = _make_pipeline_store(tmp_path)
    pipeline = _make_pipeline(repo, store)

    snap = pipeline.run(triggered_by="manual")

    assert snap.is_full_rebuild is True
    assert snap.indexed_count == 3
    assert snap.incremental_count == 3
    assert snap.removed_count == 0
    assert snap.triggered_by == "manual"
    assert snap.schema_version == PIPELINE_SCHEMA_VERSION
    assert set(snap.indexed_object_ids) == {"ko-a", "ko-b", "ko-c"}


def test_pipeline_force_full_always_full_rebuild(tmp_path):
    repo = _make_repo_with_objects(["ko-a"])
    store = _make_pipeline_store(tmp_path)
    pipeline = _make_pipeline(repo, store)

    pipeline.run()  # First run
    snap = pipeline.run(force_full=True, triggered_by="operator")

    assert snap.is_full_rebuild is True
    assert snap.incremental_count == 1


# ---------------------------------------------------------------------------
# IncrementalIndexPipeline — incremental update
# ---------------------------------------------------------------------------

def test_pipeline_incremental_only_new_objects(tmp_path):
    old_time = "2026-04-29T10:00:00Z"
    new_time = "2026-04-30T10:00:00Z"

    # First run with 2 old objects
    repo = _make_repo_with_objects(["ko-old-1", "ko-old-2"], created_at=old_time)
    store = _make_pipeline_store(tmp_path)
    pipeline = _make_pipeline(repo, store)
    first_snap = pipeline.run()

    assert first_snap.is_full_rebuild is True
    assert first_snap.indexed_count == 2

    # Add 1 new object to repo, keep existing ones
    src = SourceRecord(
        source_id="src-new",
        connector_id="test-connector",
        source_type="paper",
        title="New source",
        content_ref="ref://new",
    )
    item = EvidenceItem(
        evidence_item_id="evi-new",
        source_id="src-new",
        item_type="text_chunk",
        content_ref="ref://new#evi",
        citation_label="cite-new",
        body="New body",
    )
    bundle = EvidenceBundle(
        evidence_bundle_id="evb-new",
        source_ids=["src-new"],
        evidence_item_ids=["evi-new"],
        summary="New bundle",
        citation_refs=["cite-new"],
        confidence=1.0,
        license_scope="internal",
        access_scope=["operator"],
        created_by="test",
    )
    ko_new = KnowledgeObject(
        knowledge_object_id="ko-new",
        source_id="src-new",
        evidence_item_id="evi-new",
        evidence_bundle_id="evb-new",
        title="New object",
        text="New text",
        source_type="paper",
        license_scope="internal",
        access_scope=["operator"],
        environment_scope=["paper"],
        keywords=["new"],
        metadata={"created_at": new_time},
    )
    repo.add_source_record(src)
    repo.add_evidence_item(item)
    repo.add_bundle(bundle)
    repo.add_knowledge_object(ko_new)

    second_snap = pipeline.run(triggered_by="ingest_completion", trigger_ref="run-xyz")

    assert second_snap.is_full_rebuild is False
    assert second_snap.indexed_count == 3
    assert "ko-new" in second_snap.indexed_object_ids
    # The new object must be counted as incremental; old objects below the watermark must not be.
    assert second_snap.incremental_count == 1
    assert second_snap.triggered_by == "ingest_completion"
    assert second_snap.trigger_ref == "run-xyz"


# ---------------------------------------------------------------------------
# IncrementalIndexPipeline — stale source (removal) handling
# ---------------------------------------------------------------------------

def test_pipeline_tracks_removed_objects(tmp_path):
    repo = _make_repo_with_objects(["ko-a", "ko-b", "ko-c"])
    store = _make_pipeline_store(tmp_path)
    pipeline = _make_pipeline(repo, store)
    first = pipeline.run()
    assert first.indexed_count == 3

    # Simulate object removal: create fresh repo with only 2 objects
    repo2 = _make_repo_with_objects(["ko-a", "ko-b"])
    pipeline2 = IncrementalIndexPipeline(repo2, store, freshness_sla_seconds=3600)
    second = pipeline2.run()

    assert second.removed_count == 1
    assert second.indexed_count == 2
    assert "ko-c" not in second.indexed_object_ids


def test_pipeline_removed_count_is_zero_when_no_removals(tmp_path):
    repo = _make_repo_with_objects(["ko-x"])
    store = _make_pipeline_store(tmp_path)
    pipeline = _make_pipeline(repo, store)
    pipeline.run()
    second = pipeline.run()
    assert second.removed_count == 0


# ---------------------------------------------------------------------------
# IncrementalIndexPipeline — freshness SLA
# ---------------------------------------------------------------------------

def test_freshness_status_never_indexed(tmp_path):
    repo = _make_repo_with_objects([])
    store = _make_pipeline_store(tmp_path)
    pipeline = _make_pipeline(repo, store, sla=60)
    status = pipeline.freshness_status()

    assert status["status"] == "never_indexed"
    assert status["is_fresh"] is False
    assert status["last_indexed_at"] is None


def test_freshness_status_fresh_immediately_after_run(tmp_path):
    repo = _make_repo_with_objects(["ko-z"])
    store = _make_pipeline_store(tmp_path)
    pipeline = _make_pipeline(repo, store, sla=3600)
    pipeline.run()
    status = pipeline.freshness_status()

    assert status["status"] == "fresh"
    assert status["is_fresh"] is True
    assert status["staleness_seconds"] is not None
    assert status["staleness_seconds"] < 5


def test_freshness_status_stale_when_past_sla(tmp_path):
    repo = _make_repo_with_objects(["ko-z"])
    store = _make_pipeline_store(tmp_path)
    pipeline = _make_pipeline(repo, store, sla=1)
    pipeline.run()

    # Patch indexed_at to be in the past
    latest = store.get_latest()
    old_snap = IndexPipelineSnapshot(
        pipeline_run_id=latest.pipeline_run_id,
        schema_version=latest.schema_version,
        triggered_by=latest.triggered_by,
        trigger_ref=latest.trigger_ref,
        indexed_count=latest.indexed_count,
        incremental_count=latest.incremental_count,
        removed_count=latest.removed_count,
        indexed_watermarks=dict(latest.indexed_watermarks),
        indexed_at="2020-01-01T00:00:00Z",  # very old
        is_full_rebuild=latest.is_full_rebuild,
        freshness_sla_seconds=1,
        indexed_object_ids=list(latest.indexed_object_ids),
    )
    store._runs[-1] = old_snap

    status = pipeline.freshness_status()
    assert status["status"] == "stale"
    assert status["is_fresh"] is False
    assert status["staleness_seconds"] > 1


# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------

def test_pipeline_snapshot_schema_version(tmp_path):
    repo = _make_repo_with_objects(["ko-ver"])
    store = _make_pipeline_store(tmp_path)
    pipeline = _make_pipeline(repo, store)
    snap = pipeline.run()
    assert snap.schema_version == PIPELINE_SCHEMA_VERSION

    # Round-trip to dict and back
    data = snap.to_dict()
    assert data["schema_version"] == PIPELINE_SCHEMA_VERSION
    restored = IndexPipelineSnapshot.from_dict(data)
    assert restored.schema_version == PIPELINE_SCHEMA_VERSION
    assert restored.pipeline_run_id == snap.pipeline_run_id


# ---------------------------------------------------------------------------
# HTTP endpoints via create_app
# ---------------------------------------------------------------------------

def _make_test_app(tmp_path: Path):
    from fastapi.testclient import TestClient
    from services.search.main import create_app

    repo = _make_repo_with_objects(["ko-http-1", "ko-http-2"], created_at="2026-04-30T00:00:00Z")
    # Persist repo to JSONL evidence file
    from services.knowledge.evidence import JsonlEvidenceRepository
    evidence_path = tmp_path / "evidence.jsonl"
    disk_repo = JsonlEvidenceRepository(evidence_path)
    for src in repo.list_source_records():
        disk_repo.add_source_record(src)
    for item in repo.list_evidence_items():
        disk_repo.add_evidence_item(item)
    for bundle in repo.list_bundles():
        disk_repo.add_bundle(bundle)
    for ko in repo.list_knowledge_objects():
        disk_repo.add_knowledge_object(ko)

    app = create_app(
        index_store_path=tmp_path / "search-index.jsonl",
        evidence_store_path=evidence_path,
        materialize_store_path=tmp_path / "materialize.jsonl",
        pipeline_store_path=tmp_path / "pipeline.jsonl",
        freshness_sla_seconds=3600,
        pipeline_retention_runs=20,
    )
    return TestClient(app)


def test_http_refresh_endpoint(tmp_path):
    client = _make_test_app(tmp_path)
    resp = client.post("/api/search/index/refresh", json={"triggered_by": "test"})
    assert resp.status_code == 200
    body = resp.json()
    assert "pipeline_snapshot" in body
    snap = body["pipeline_snapshot"]
    assert snap["schema_version"] == PIPELINE_SCHEMA_VERSION
    assert snap["indexed_count"] == 2
    assert snap["is_full_rebuild"] is True
    assert snap["triggered_by"] == "test"


def test_http_refresh_with_no_body(tmp_path):
    client = _make_test_app(tmp_path)
    resp = client.post("/api/search/index/refresh")
    assert resp.status_code == 200
    assert resp.json()["pipeline_snapshot"]["triggered_by"] == "manual"


def test_http_freshness_never_indexed(tmp_path):
    from fastapi.testclient import TestClient
    from services.search.main import create_app
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_text("")
    app = create_app(
        index_store_path=tmp_path / "search-index.jsonl",
        evidence_store_path=evidence_path,
        materialize_store_path=tmp_path / "materialize.jsonl",
        pipeline_store_path=tmp_path / "pipeline.jsonl",
    )
    client = TestClient(app)
    resp = client.get("/api/search/index/freshness")
    assert resp.status_code == 200
    assert resp.json()["status"] == "never_indexed"


def test_http_freshness_after_refresh(tmp_path):
    client = _make_test_app(tmp_path)
    client.post("/api/search/index/refresh")
    resp = client.get("/api/search/index/freshness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "fresh"
    assert data["is_fresh"] is True


def test_http_pipeline_runs_empty(tmp_path):
    from fastapi.testclient import TestClient
    from services.search.main import create_app
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_text("")
    app = create_app(
        index_store_path=tmp_path / "search-index.jsonl",
        evidence_store_path=evidence_path,
        materialize_store_path=tmp_path / "materialize.jsonl",
        pipeline_store_path=tmp_path / "pipeline.jsonl",
    )
    client = TestClient(app)
    resp = client.get("/api/search/index/pipeline-runs")
    assert resp.status_code == 200
    assert resp.json()["runs"] == []


def test_http_pipeline_runs_after_refresh(tmp_path):
    client = _make_test_app(tmp_path)
    client.post("/api/search/index/refresh", json={"triggered_by": "test_run"})
    resp = client.get("/api/search/index/pipeline-runs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["triggered_by"] == "test_run"


def test_http_health_reports_pipeline_retention_and_sla(tmp_path):
    client = _make_test_app(tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pipeline_retention_runs"] == 20
    assert body["freshness_sla_seconds"] == 3600


def test_http_refresh_incremental_second_run(tmp_path):
    client = _make_test_app(tmp_path)
    first = client.post("/api/search/index/refresh").json()["pipeline_snapshot"]
    second = client.post("/api/search/index/refresh").json()["pipeline_snapshot"]
    assert first["is_full_rebuild"] is True
    assert second["is_full_rebuild"] is False
    assert second["indexed_count"] == first["indexed_count"]


# ---------------------------------------------------------------------------
# Source-ingest notification
# ---------------------------------------------------------------------------

def test_notify_search_index_refresh_fires_on_completed_run():
    """_notify_search_index_refresh is called when SEARCH_INGEST_NOTIFY_URL is set and run completes."""
    with mock.patch.dict("os.environ", {"SEARCH_INGEST_NOTIFY_URL": "http://search-svc:8098"}):
        import importlib
        import services.source_ingestion.main as ingest_main
        importlib.reload(ingest_main)
        # Reload to pick up env var
        assert ingest_main.SEARCH_INGEST_NOTIFY_URL == "http://search-svc:8098"
        fake_response = mock.Mock()
        fake_response.__enter__ = mock.Mock(return_value=fake_response)
        fake_response.__exit__ = mock.Mock(return_value=None)
        fake_response.read.return_value = json.dumps(
            {
                "truth": {
                    "index_refreshed": True,
                    "pipeline_run_id": "pipe-run-abc",
                    "freshness_status": "fresh",
                    "freshness_within_sla": True,
                    "materialized": True,
                    "materialized_matches_completion": True,
                }
            }
        ).encode("utf-8")
        fake_response.getcode.return_value = 200
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = fake_response
            summary = ingest_main._notify_search_index_refresh(
                "run-abc",
                connector_id="conn-test",
                source_type="paper",
                trace_id="trace-run-abc",
                normalized_count=2,
                evidence_refs={
                    "source_ids": ["src-a"],
                    "evidence_bundle_id": "evbundle-a",
                    "knowledge_object_ids": ["ko-a"],
                },
            )
        mock_open.assert_called_once()
        req = mock_open.call_args.args[0]
        assert req.full_url == "http://search-svc:8098/api/search/index/source-completions"
        assert req.get_method() == "POST"
        assert json.loads(req.data.decode("utf-8")) == {
            "ingest_run_id": "run-abc",
            "connector_id": "conn-test",
            "source_type": "paper",
            "trace_id": "trace-run-abc",
            "normalized_count": 2,
            "source_ids": ["src-a"],
            "evidence_bundle_id": "evbundle-a",
            "knowledge_object_ids": ["ko-a"],
            "materialize": True,
        }
        assert summary["status"] == "refreshed"
        assert summary["search_service"]["pipeline_run_id"] == "pipe-run-abc"
        assert summary["search_service"]["materialized_matches_completion"] is True
        ingest_main.SEARCH_INGEST_NOTIFY_URL = ""


def test_notify_search_index_refresh_noop_when_url_empty():
    """No HTTP call is made when SEARCH_INGEST_NOTIFY_URL is not set."""
    import services.source_ingestion.main as ingest_main
    with mock.patch("urllib.request.urlopen") as mock_open:
        with mock.patch.dict("os.environ", {"SEARCH_INGEST_NOTIFY_URL": ""}):
            ingest_main.SEARCH_INGEST_NOTIFY_URL = ""
            summary = ingest_main._notify_search_index_refresh("run-abc")
        mock_open.assert_not_called()
        assert summary["status"] == "not_configured"
        assert summary["configured"] is False


def test_notify_search_index_refresh_ignores_network_errors():
    """Network errors during notification do not propagate."""
    import services.source_ingestion.main as ingest_main
    with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        ingest_main.SEARCH_INGEST_NOTIFY_URL = "http://search-svc:8098"
        summary = ingest_main._notify_search_index_refresh("run-abc")  # must not raise
        assert summary["status"] == "notify_failed"
        assert "connection refused" in summary["error"]
        ingest_main.SEARCH_INGEST_NOTIFY_URL = ""
