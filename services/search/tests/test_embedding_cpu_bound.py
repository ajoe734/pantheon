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
    assert session_options.inter_op_num_threads == 2


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


def _benchmark_documents() -> list[KnowledgeObject]:
    """Deterministic synthetic official-reference shapes, never asserted as market truth."""
    objects = []
    for index in range(132):
        venue, symbol, company = (
            ("TWSE", "2330", "台積電"),
            ("TPEx", "3105", "穩懋"),
            ("TDCC", "2317", "鴻海"),
        )[index % 3]
        rows = []
        while True:
            row = {
                "Date": f"115{index % 12 + 1:02d}{len(rows) % 28 + 1:02d}",
                "Code": symbol, "Name": company,
                "TradeVolume": str(30000000 + index * 100 + len(rows)),
                "TradeValue": "28500000000", "OpeningPrice": "950.00",
                "HighestPrice": "960.00", "LowestPrice": "945.00",
                "ClosingPrice": str(955 + index % 10), "Change": "+5.00",
                "Transaction": "18000",
            }
            if venue == "TDCC":
                row = {"資料日期": row["Date"], "證券代號": symbol,
                       "持股分級": len(rows) % 17 + 1, "人數": 18000 + index,
                       "股數": 30000000 + len(rows), "占集保庫存數比例%": "12.34"}
            rows.append(row)
            body = json.dumps({"venue": venue, "records": rows}, ensure_ascii=False)
            if len(body.encode("utf-8")) >= 13000:
                break
        objects.append(KnowledgeObject(
            knowledge_object_id=f"cpu-burst-{index:03d}", source_id=f"official-{venue}-{index}",
            evidence_item_id=f"item-{index}", evidence_bundle_id=f"bundle-{index}",
            title=f"{venue} {symbol} {company} 官方參考資料 {index}", text=body,
            source_type="official_reference", license_scope="official_reference",
            access_scope=["operator"], environment_scope=["paper"],
            keywords=[venue, symbol, company],
            metadata={"created_at": "2026-11-14T00:00:00Z"},
        ))
    return objects


def _benchmark_worker(mode: str, output: Path) -> None:
    import hashlib
    import importlib.metadata
    import importlib.util
    import os
    import resource
    import time

    import numpy as np

    from services.search.index_adapter import KeywordIndexAdapter

    engine_class = LocalEmbeddingEngine
    if mode == "before":
        spec = importlib.util.spec_from_file_location("baseline_embeddings", output / "baseline.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        engine_class = module.LocalEmbeddingEngine
    engine = engine_class(manifest_path=ROOT / "services/search/model-manifest.json",
                          cache_dir="/opt/search/models")
    assert engine.verify_integrity(), "Benchmark requires the manifest-pinned real model"
    session = engine._ensure_loaded().model.model
    options = session.get_session_options()
    objects = _benchmark_documents()
    documents = KeywordIndexAdapter(InMemoryEvidenceRepository()).documents_for(objects)
    texts = [doc.search_text for doc in documents]
    queries = ["2330 台積電 收盤價", "3105 穩懋 成交量", "2317 鴻海 股權分散",
               "TWSE daily closing price", "台股官方參考資料 長期價格與成交筆數", "集保持股分級比例"]
    engine.embed_query("model warmup")
    (output / f"ready-{mode}").touch()
    deadline = time.monotonic() + 120
    while not (output / f"go-{mode}").exists():
        if time.monotonic() > deadline:
            raise TimeoutError("Benchmark driver did not start the burst")
        time.sleep(0.02)
    cpu_start = resource.getrusage(resource.RUSAGE_SELF)
    wall_start = time.perf_counter()
    vectors = engine.embed_documents(texts)  # production default batch_size=64
    wall_seconds = time.perf_counter() - wall_start
    cpu_end = resource.getrusage(resource.RUSAGE_SELF)
    (output / f"burst-done-{mode}").touch()
    query_vectors = [engine.embed_query(query) for query in queries]
    np.savez(output / f"vectors-{mode}.npz", documents=vectors, queries=query_vectors)
    usage = Path("/sys/fs/cgroup/cpu.stat").read_text()
    result = {
        "mode": mode, "count": len(vectors), "batch_size": 64,
        "wall_seconds": wall_seconds,
        "cpu_user_seconds": cpu_end.ru_utime - cpu_start.ru_utime,
        "cpu_system_seconds": cpu_end.ru_stime - cpu_start.ru_stime,
        "objects_per_hour": len(vectors) * 3600 / wall_seconds,
        "intra_op_threads": options.intra_op_num_threads,
        "inter_op_threads": options.inter_op_num_threads,
        "affinity": sorted(os.sched_getaffinity(0)),
        "cpu_max": Path("/sys/fs/cgroup/cpu.max").read_text().strip(),
        "cpu_weight": Path("/sys/fs/cgroup/cpu.weight").read_text().strip(),
        "cpu_stat_at_end": usage,
        "knowledge_text_bytes": [len(obj.text.encode("utf-8")) for obj in objects],
        "embedding_text_bytes": [len(text.encode("utf-8")) for text in texts],
        "corpus_sha256": hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode()).hexdigest(),
        "versions": {name: importlib.metadata.version(name)
                     for name in ["fastembed", "onnxruntime", "numpy"]},
    }
    (output / f"worker-{mode}.json").write_text(json.dumps(result, indent=2))
    if mode == "after":
        with np.load(output / "vectors-before.npz") as before:
            comparison = {}
            for kind, after in [("documents", vectors), ("queries", query_vectors)]:
                old, new = before[kind], np.asarray(after)
                similarities = np.sum(old * new, axis=1) / (
                    np.linalg.norm(old, axis=1) * np.linalg.norm(new, axis=1))
                comparison[kind] = {"count": len(old), "min_cosine": float(similarities.min()),
                                    "cosines": similarities.tolist()}
                assert similarities.min() >= 0.9999, comparison[kind]
            # Compare exact top-10 document IDs for all six representative queries.
            old_ranks = np.argsort(-(before["queries"] @ before["documents"].T), axis=1)[:, :10]
            new_ranks = np.argsort(-(np.asarray(query_vectors) @ np.asarray(vectors).T), axis=1)[:, :10]
            comparison["top_10_document_ids_identical"] = bool(np.array_equal(old_ranks, new_ranks))
            comparison["before_top_10"] = old_ranks.tolist()
            comparison["after_top_10"] = new_ranks.tolist()
        (output / "comparison.json").write_text(json.dumps(comparison, indent=2))
    print(json.dumps({key: result[key] for key in ["mode", "count", "wall_seconds", "objects_per_hour"]}), flush=True)


def _benchmark_neighbour() -> None:
    """A local read-service proxy with fixed JSON/CPU work, not a product endpoint."""
    import hashlib
    import signal
    from http.server import BaseHTTPRequestHandler, HTTPServer

    # Python is PID 1 in this container; explicitly handle Docker's stop signal.
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(0))
    payload = json.dumps({"records": [{"id": i, "state": "available", "symbol": "2330"}
                                      for i in range(256)]}).encode()

    class ReadHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            for _ in range(200):
                hashlib.sha256(payload).digest()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    HTTPServer(("0.0.0.0", 8000), ReadHandler).serve_forever()


def _benchmark_driver(output: Path) -> None:
    """Run foreground Docker children with terminal collection and a 40-minute bound each.

    python -m services.search.tests.test_embedding_cpu_bound --benchmark-driver /tmp/search-cpu-run
    Requires Docker, the local pantheon-search-r2-test:local image, and Python test dependencies.
    Services share CPUs 0-7; the measuring client uses CPUs 8-11 on this 12-CPU host.
    """
    import http.client
    import os
    import subprocess
    import time

    output.mkdir(parents=True, exist_ok=False)
    os.sched_setaffinity(0, {8, 9, 10, 11})
    baseline = subprocess.check_output(["git", "show", "18fbecadc^:services/search/local_embeddings.py"])
    (output / "baseline.py").write_bytes(baseline)
    image = "pantheon-search-r2-test:local"
    prefix = f"search-cpu-{os.getpid()}"
    common = ["--cpuset-cpus", "0-7", "--memory", "20g", "-v", f"{ROOT}:/workspace:ro",
              "-v", f"{output}:/results", "-w", "/workspace", "--entrypoint", "python", image,
              "-u", "-m", "services.search.tests.test_embedding_cpu_bound"]
    server_name = prefix + "-read"
    children = []

    def probe(port, duration=None, done=None):
        samples, errors = [], []
        start = time.monotonic()
        while (time.monotonic() - start < duration) if duration else not done.exists():
            tick = time.perf_counter()
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("GET", "/read")
                response = connection.getresponse()
                assert response.status == 200 and len(response.read()) > 10000
                samples.append((time.perf_counter() - tick) * 1000)
            except Exception as exc:
                errors.append(str(exc))
            finally:
                connection.close()
            if time.monotonic() - start > 2400:
                raise TimeoutError("Embedding burst exceeded 40 minutes")
            time.sleep(max(0, 0.02 - (time.perf_counter() - tick)))
        ordered = sorted(samples)
        assert ordered and not errors, errors
        return {"count": len(samples), "errors": errors, "p50_ms": ordered[int((len(ordered)-1)*0.5)],
                "p95_ms": ordered[int((len(ordered)-1)*0.95)], "samples_ms": samples}

    try:
        server_log = (output / "neighbour.log").open("w")
        server = subprocess.Popen(["docker", "run", "--rm", "--name", server_name,
            "--cpus", "8", "--cpu-shares", "1024", "-p", "127.0.0.1::8000",
            *common, "--benchmark-neighbour"], stdout=server_log, stderr=subprocess.STDOUT)
        children.append((server_name, server))
        port = None
        for _ in range(60):
            info = subprocess.run(["docker", "port", server_name, "8000"], capture_output=True, text=True)
            if info.returncode == 0 and info.stdout.strip():
                port = int(info.stdout.strip().rsplit(":", 1)[1])
                try:
                    probe(port, duration=0.1)
                    break
                except AssertionError:
                    pass
            time.sleep(1)
        assert port, "Read-service proxy did not start"
        results = {}
        for mode, cpus, shares in [("before", "8", "1024"), ("after", "2", "256")]:
            name = prefix + "-" + mode
            log = (output / f"{mode}.log").open("w")
            worker = subprocess.Popen(["docker", "run", "--rm", "--name", name, "--network", "none",
                "--cpus", cpus, "--cpu-shares", shares, *common,
                "--benchmark-worker", mode, "/results"], stdout=log, stderr=subprocess.STDOUT)
            children.append((name, worker))
            deadline = time.monotonic() + 180
            while not (output / f"ready-{mode}").exists():
                assert worker.poll() is None, (output / f"{mode}.log").read_text()
                if time.monotonic() > deadline:
                    raise TimeoutError("Embedding model did not become ready")
                time.sleep(0.25)
            probe(port, duration=2)  # warm the HTTP path before the idle sample
            idle = probe(port, duration=20)
            (output / f"go-{mode}").touch()
            active = probe(port, done=output / f"burst-done-{mode}")
            returncode = worker.wait(timeout=180)
            assert returncode == 0, (output / f"{mode}.log").read_text()
            log.close()
            results[mode] = {"idle": idle, "during_burst": active,
                             "p95_to_idle_ratio": active["p95_ms"] / idle["p95_ms"],
                             "worker_exit_status": returncode}
            (output / "http.json").write_text(json.dumps(results, indent=2))
            print(json.dumps({"mode": mode, "p95_to_idle_ratio": results[mode]["p95_to_idle_ratio"]}), flush=True)
        assert results["after"]["p95_to_idle_ratio"] <= 1.5, results["after"]["p95_to_idle_ratio"]
        after = json.loads((output / "worker-after.json").read_text())
        assert after["objects_per_hour"] >= 300, after["objects_per_hour"]
    finally:
        for name, child in reversed(children):
            if child.poll() is None:
                subprocess.run(["docker", "stop", "--time", "5", name], capture_output=True, timeout=30)
            code = child.wait(timeout=30)
            print(json.dumps({"container": name, "terminal_exit_status": code}), flush=True)
            if name == server_name:
                assert code == 0, "Read-service proxy did not shut down cleanly"
        server_log.close()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--benchmark-driver"]:
        _benchmark_driver(Path(sys.argv[2]).resolve())
    elif sys.argv[1:2] == ["--benchmark-worker"]:
        _benchmark_worker(sys.argv[2], Path(sys.argv[3]))
    elif sys.argv[1:2] == ["--benchmark-neighbour"]:
        _benchmark_neighbour()
    else:
        unittest.main()
