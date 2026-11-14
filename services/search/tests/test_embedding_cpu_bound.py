"""Tests for bounded CPU usage and compose CPU resource controls in search embedding."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from typing import Any
import unittest
from unittest import mock

import yaml

from services.knowledge.evidence import (
    EvidenceBundle,
    EvidenceItem,
    InMemoryEvidenceRepository,
    KnowledgeObject,
)
from services.search.index_pipeline import (
    IncrementalIndexPipeline,
    JsonlIndexPipelineStore,
    PIPELINE_SCHEMA_VERSION,
)
from services.search.local_embeddings import EMBEDDING_THREADS, LocalEmbeddingEngine
from services.search.scheduler_worker import run_tick
from services.source_ingestion.connectors.base import SourceRecord


ROOT = Path(__file__).resolve().parents[3]


class _FakeTextEmbedding:
    """Spy replacement for fastembed.TextEmbedding recording constructor and embed kwargs."""

    instances: list["_FakeTextEmbedding"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = dict(kwargs)
        self.threads = kwargs.get("threads")
        self.embed_calls: list[dict[str, Any]] = []
        _FakeTextEmbedding.instances.append(self)

    def embed(self, texts: Any, **kwargs: Any):
        text_list = list(texts)
        self.embed_calls.append({"texts": text_list, "kwargs": dict(kwargs)})
        for idx, _ in enumerate(text_list):
            vec = [0.0] * 1024
            vec[idx % 1024] = 1.0
            yield vec


def test_local_embedding_engine_passes_fixed_thread_count_at_most_two() -> None:
    """Document and query embedding must construct TextEmbedding with threads <= 2."""
    assert isinstance(EMBEDDING_THREADS, int)
    assert 1 <= EMBEDDING_THREADS <= 2
    assert EMBEDDING_THREADS == 2

    _FakeTextEmbedding.instances.clear()
    fake_fastembed = ModuleType("fastembed")
    fake_fastembed.TextEmbedding = _FakeTextEmbedding  # type: ignore[attr-defined]

    with mock.patch.dict(sys.modules, {"fastembed": fake_fastembed}):
        engine = LocalEmbeddingEngine(cache_dir="/opt/search/models")
        assert engine.threads == EMBEDDING_THREADS
        assert 1 <= engine.threads <= 2

        with (
            mock.patch.object(engine, "verify_integrity", return_value=True),
            mock.patch.object(engine, "_find_snapshot_dir", return_value=Path("/opt/search/models")),
        ):
            query_vec = engine.embed_query("2330 台積電 收盤價")
            assert len(query_vec) == 1024
            assert len(_FakeTextEmbedding.instances) == 1

            constructed = _FakeTextEmbedding.instances[0]
            assert constructed.init_kwargs["threads"] == 2
            assert 1 <= constructed.init_kwargs["threads"] <= 2
            assert constructed.init_kwargs["model_name"] == "intfloat/multilingual-e5-large"
            assert constructed.init_kwargs["local_files_only"] is True

            doc_vecs = engine.embed_documents(
                [
                    "TWSE daily close official reference record for 2330",
                    "TDCC shareholding distribution official reference record for 2317",
                ],
                batch_size=64,
            )
            assert len(doc_vecs) == 2
            assert all(len(vec) == 1024 for vec in doc_vecs)
            # Reuses the same bounded TextEmbedding instance for both queries and documents.
            assert len(_FakeTextEmbedding.instances) == 1
            assert len(constructed.embed_calls) == 2
            assert constructed.embed_calls[0]["texts"] == ["query: 2330 台積電 收盤價"]
            assert "parallel" not in constructed.embed_calls[0]["kwargs"]
            assert constructed.embed_calls[1]["kwargs"] == {"batch_size": 64}
            assert "parallel" not in constructed.embed_calls[1]["kwargs"]


def test_real_fastembed_session_uses_bounded_threads_when_packaged_model_available() -> None:
    """When running inside the search image with packaged model, verify ONNX session threads <= 2."""
    cache_dir = Path("/opt/search/models")
    if not (cache_dir / "model.onnx").is_file():
        raise unittest.SkipTest("Packaged ONNX model not mounted at /opt/search/models in this environment")

    engine = LocalEmbeddingEngine(cache_dir=cache_dir)
    embedder = engine._ensure_loaded()
    assert embedder.threads == 2
    assert embedder.model.threads == 2
    session_options = embedder.model.model.get_session_options()
    assert 1 <= session_options.intra_op_num_threads <= 2
    assert session_options.intra_op_num_threads == 2


def test_search_service_compose_defines_cpu_limit_and_below_default_cpu_weight() -> None:
    """search-svc in docker-compose.yml must set cpus <= 2 and cpu_shares < 1024 without new env vars."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    search_svc = compose["services"]["search-svc"]

    assert "cpus" in search_svc
    cpus = float(search_svc["cpus"])
    assert 0.0 < cpus <= 2.0
    assert cpus == 2.0

    assert "cpu_shares" in search_svc
    cpu_shares = int(search_svc["cpu_shares"])
    assert 0 < cpu_shares < 1024
    assert cpu_shares == 256

    expected_env_keys = {
        "PORT",
        "PANTHEON_SEARCH_JWT_SECRET",
        "PANTHEON_SEARCH_JWT_ISSUER",
        "PANTHEON_SEARCH_JWT_AUDIENCE",
        "SEARCH_DATA_DIR",
        "SEARCH_INDEX_STORE_PATH",
        "SEARCH_MATERIALIZE_STORE_PATH",
        "SEARCH_PIPELINE_STORE_PATH",
        "SEARCH_EVIDENCE_STORE_PATH",
        "SEARCH_FRESHNESS_SLA_SECONDS",
        "SEARCH_PIPELINE_RETENTION_RUNS",
        "SEARCH_DURABLE_INDEX_ONLY",
        "PANTHEON_SEARCH_BACKEND",
        "PANTHEON_SEARCH_POSTGRES_DSN",
        "SEARCH_EMBEDDING_CACHE_DIR",
        "HF_HUB_OFFLINE",
        "DATABASE_URL",
        "PANTHEON_SOURCE_SEARCH_POSTURE",
        "SEARCH_INDEX_STORE_BACKEND",
        "SEARCH_INDEX_STORE_DSN",
        "SEARCH_EVIDENCE_BACKEND",
        "SEARCH_EVIDENCE_DSN",
    }
    assert set(search_svc["environment"].keys()) == expected_env_keys


def test_pipeline_checkpoint_and_scheduler_contract_unchanged() -> None:
    """Incremental pipeline checkpointing and scheduler refresh/materialize contract remain unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        repo = InMemoryEvidenceRepository()
        src = SourceRecord(
            source_id="src-cpu-bound-1",
            connector_id="tw_official_twse",
            source_type="filing",
            title="TWSE 2330 台積電 daily close official reference",
            content_ref="twse://tw_price_daily/2330/2026-11-14",
            trace_id="trace-cpu-bound-1",
        )
        item = EvidenceItem(
            evidence_item_id="evi-cpu-bound-1",
            source_id=src.source_id,
            item_type="text_chunk",
            content_ref="twse://tw_price_daily/2330/2026-11-14#evi",
            citation_label="twse:2330:2026-11-14",
            body="2330 台積電 收盤價 1055 元 成交股數 32,450,000",
        )
        bundle = EvidenceBundle(
            evidence_bundle_id="evb-cpu-bound-1",
            source_ids=[src.source_id],
            evidence_item_ids=[item.evidence_item_id],
            summary="Official reference bundle for 2330",
            citation_refs=[item.citation_label],
            confidence=1.0,
            license_scope="internal",
            access_scope=["operator"],
            created_by="tw_official_twse",
        )
        ko = KnowledgeObject(
            knowledge_object_id="ko-cpu-bound-1",
            source_id=src.source_id,
            evidence_item_id=item.evidence_item_id,
            evidence_bundle_id=bundle.evidence_bundle_id,
            title="TWSE 2330 台積電 daily close official reference",
            text="2330 台積電 收盤價 1055 元 成交股數 32,450,000",
            source_type="official_reference",
            license_scope="internal",
            access_scope=["operator"],
            environment_scope=["paper"],
            keywords=["2330", "tw_price_daily"],
            metadata={"created_at": "2026-11-14T08:30:00Z"},
        )
        repo.add_source_record(src)
        repo.add_evidence_item(item)
        repo.add_bundle(bundle)
        repo.add_knowledge_object(ko)

        indexed_records: list[Any] = []

        class _FakeRetrievalBackend:
            def index_documents(self, records: list[Any]) -> int:
                indexed_records.extend(records)
                return len(records)

            def indexed_document_states(self, tenant_id: str, doc_ids: list[str]) -> dict[str, bool]:
                return {r.id: r.is_active for r in indexed_records if r.id in doc_ids}

        pipeline_store = JsonlIndexPipelineStore(tmp_path / "pipeline.jsonl", max_retention=10)
        pipeline = IncrementalIndexPipeline(
            repo,
            pipeline_store,
            freshness_sla_seconds=3600,
            retrieval_backend=_FakeRetrievalBackend(),
        )
        snap = pipeline.run(triggered_by="source_ingest_complete", trigger_ref="ingest-run-cpu-bound-1")
        assert snap.schema_version == PIPELINE_SCHEMA_VERSION
        assert snap.triggered_by == "source_ingest_complete"
        assert snap.trigger_ref == "ingest-run-cpu-bound-1"
        assert snap.indexed_count == 1
        assert snap.incremental_count == 1
        assert snap.indexed_object_ids == ["ko-cpu-bound-1"]
        assert "filing" in snap.indexed_watermarks
        assert len(indexed_records) == 1
        assert indexed_records[0].id == "ko-cpu-bound-1"
        assert pipeline_store.get_latest() is not None
        assert pipeline_store.get_latest().pipeline_run_id == snap.pipeline_run_id

        calls: list[tuple[str, dict[str, Any]]] = []

        class _FakeHttpResponse:
            def __init__(self, payload: dict[str, Any]) -> None:
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request, timeout: float = 30.0):
            body = json.loads((request.data or b"{}").decode("utf-8"))
            calls.append((request.full_url, body))
            return _FakeHttpResponse({"status": "ok"})

        with mock.patch("services.search.scheduler_worker.urllib.request.urlopen", fake_urlopen):
            outcome = run_tick(
                api_url="http://search-svc:8098",
                force_full=False,
                materialize=True,
                trigger_ref="schedule-cpu-bound-001",
            )
        assert outcome["refresh"] == {"status": "ok"}
        assert outcome["materialize"] == {"status": "ok"}
        assert [c[0] for c in calls] == [
            "http://search-svc:8098/api/search/index/refresh",
            "http://search-svc:8098/api/search/index/materialize",
        ]


class TestEmbeddingCpuBound(unittest.TestCase):
    """unittest wrapper so tests also run in images without pytest installed."""

    def test_local_embedding_engine_passes_fixed_thread_count_at_most_two(self) -> None:
        test_local_embedding_engine_passes_fixed_thread_count_at_most_two()

    def test_real_fastembed_session_uses_bounded_threads_when_packaged_model_available(self) -> None:
        test_real_fastembed_session_uses_bounded_threads_when_packaged_model_available()

    def test_search_service_compose_defines_cpu_limit_and_below_default_cpu_weight(self) -> None:
        test_search_service_compose_defines_cpu_limit_and_below_default_cpu_weight()

    def test_pipeline_checkpoint_and_scheduler_contract_unchanged(self) -> None:
        test_pipeline_checkpoint_and_scheduler_contract_unchanged()


if __name__ == "__main__":
    unittest.main()
