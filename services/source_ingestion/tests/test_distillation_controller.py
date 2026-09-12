"""Tests for the Strategy Distillation supervised loop controller.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from services.source_ingestion.connectors.base import SourceRecord, SourceRecordStatus
from services.source_ingestion.controller_state import ControllerState, ControllerStateStore
from services.source_ingestion.distillation_controller import (
    DistillationControllerConfig,
    run_controller_tick,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore
from services.source_ingestion.distillation_worker import (
    DistillationJobQueue,
    source_version_digest,
)


class DummyLoopWriter:
    def __init__(self) -> None:
        self.successes = []
        self.ticks = []

    async def record_success(self, **kwargs: Any) -> None:
        self.successes.append(kwargs)

    async def record_tick(self, **kwargs: Any) -> None:
        self.ticks.append(kwargs)


def _normalized_source(
    source_id: str = "src-tw-001",
    title: str = "LightGBM TW equity momentum factor paper",
    source_type: str = "paper",
    trust_score: float = 0.8,
    **metadata_overrides: Any,
) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        connector_id="conn-papers",
        source_type=source_type,
        title=title,
        content_ref=f"https://doi.org/10.1000/{source_id}",
        status="normalized",
        metadata={
            "trust_score": trust_score,
            "access_scope": ["research"],
            "license_scope": "internal",
            "keywords": ["momentum", "lightgbm", "equity"],
            "strategy_seed": {
                "hypothesis": "Dummy hypothesis",
                "asset_class": ["equity"],
                "market_scope": ["Taiwan"],
                "holding_period": "5 days",
                "required_data": ["OHLCV"],
                "backend_hint": "qlib",
                "feature_hints": ["momentum"],
                "label_hints": ["return"],
                "risk_notes": ["none"],
            },
            **metadata_overrides,
        },
    )


def test_distillation_controller_tick_success(tmp_path, monkeypatch) -> None:
    # 1. Setup paths
    evidence_path = tmp_path / "source_evidence.jsonl"
    job_queue_path = tmp_path / "job_queue.jsonl"
    seed_store_path = tmp_path / "seeds.jsonl"
    state_path = tmp_path / "controller_state.json"
    alive_path = tmp_path / "controller_alive"
    
    # Pre-populate source evidence JSONL with a normalized record
    record = _normalized_source("src-note-test-001")
    with open(evidence_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"record_type": "source_record", "payload": record.to_dict()}) + "\n")
        
    config = DistillationControllerConfig(
        database_url="postgresql://test:test@localhost:5432/test",
        registry_url="http://mock-registry:8087",
        interval_seconds=60,
        max_ticks=1,
        state_path=state_path,
        alive_path=alive_path,
        job_queue_path=job_queue_path,
        seed_store_path=seed_store_path,
        evidence_store_path=evidence_path,
        source_dirs=[tmp_path],
    )
    
    state = ControllerState(
        controller_id="test-controller",
        controller_name="test-distillation-controller",
        environment="test",
        tenant_id="test",
        deployment={"git_sha": "test-sha"},
    )
    state_store = ControllerStateStore(state_path)
    state_store.save(state)
    
    writer = DummyLoopWriter()

    # Mock registry HTTP requests. The store is stateful because the sync path
    # only acknowledges a job after a terminal readback of the write.
    registry_queries: list[str] = []
    registry_registrations: list[dict] = []
    registry_store: dict[str, dict] = {}

    def mock_get_registry_entry(url: str, registry_id: str) -> dict | None:
        registry_queries.append(registry_id)
        return registry_store.get(registry_id)

    def mock_register_strategy_spec(url: str, payload: dict) -> dict:
        registry_registrations.append(payload)
        metadata = dict(payload.get("metadata") or {})
        if isinstance(payload.get("strategy_spec"), dict):
            metadata.setdefault("strategy_spec", payload["strategy_spec"])
        entry = {
            **payload,
            "artifact_type": "strategy_spec",
            "artifact_state": "draft",
            "lineage": {
                "parent_registry_ids": None,
                **dict(payload.get("lineage") or {}),
                "source_strategy_spec_id": None,
            },
            "metadata": metadata,
        }
        entry.pop("strategy_spec", None)
        entry.pop("source_digest", None)
        registry_store[payload["registry_id"]] = {"entry": entry}
        return {"entry": entry}

    monkeypatch.setattr("services.source_ingestion.distillation_controller._get_registry_entry", mock_get_registry_entry)
    monkeypatch.setattr("services.source_ingestion.distillation_controller._register_strategy_spec_if_absent", mock_register_strategy_spec)

    # 2. Run tick
    res = run_controller_tick(config=config, state=state, store=state_store, writer=writer)

    # 3. Assertions
    assert res["status"] == "success"
    assert res["reconcile"]["created"] == 1
    assert res["actual"]["synced_count"] == 1

    digest = source_version_digest(record).removeprefix("sha256:")
    expected_registry_id = f"reg-strategy-spec-src-note-test-001-{digest[:12]}"
    # One pre-write probe plus one terminal readback of the same versioned id.
    assert registry_queries == [expected_registry_id, expected_registry_id]
    assert len(registry_registrations) == 1
    assert registry_registrations[0]["registry_id"] == expected_registry_id
    assert registry_registrations[0]["source_digest"] == source_version_digest(record)
    assert len(writer.successes) == 1
    assert writer.successes[0]["loop_id"] == "strategy_distillation"
    assert alive_path.exists()


def test_distillation_controller_tick_processes_pre_admitted_job_without_reenqueue(
    tmp_path, monkeypatch
) -> None:
    """L12-GAP-F02: a job admitted at commit time (the ingest-side event
    admission path) must be picked up and processed by the controller's
    tick without catch_up enqueueing a duplicate job for it."""
    evidence_path = tmp_path / "source_evidence.jsonl"
    job_queue_path = tmp_path / "job_queue.jsonl"
    seed_store_path = tmp_path / "seeds.jsonl"
    state_path = tmp_path / "controller_state.json"
    alive_path = tmp_path / "controller_alive"

    record = _normalized_source("src-note-test-003")
    with open(evidence_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"record_type": "source_record", "payload": record.to_dict()}) + "\n")

    # Simulate the source-ingest side: the normalized SourceRecord commit
    # already admitted its job to the queue, before this tick ever runs.
    pre_admitted = DistillationJobQueue(job_queue_path).enqueue_source_record(record)

    config = DistillationControllerConfig(
        database_url="postgresql://test:test@localhost:5432/test",
        registry_url="http://mock-registry:8087",
        interval_seconds=60,
        max_ticks=1,
        state_path=state_path,
        alive_path=alive_path,
        job_queue_path=job_queue_path,
        seed_store_path=seed_store_path,
        evidence_store_path=evidence_path,
        source_dirs=[tmp_path],
    )

    state = ControllerState(
        controller_id="test-controller",
        controller_name="test-distillation-controller",
        environment="test",
        tenant_id="test",
        deployment={"git_sha": "test-sha"},
    )
    state_store = ControllerStateStore(state_path)
    state_store.save(state)

    writer = DummyLoopWriter()

    registry_store: dict[str, dict] = {}

    def mock_get_registry_entry(url: str, registry_id: str) -> dict | None:
        return registry_store.get(registry_id)

    def mock_register_strategy_spec(url: str, payload: dict) -> dict:
        metadata = dict(payload.get("metadata") or {})
        if isinstance(payload.get("strategy_spec"), dict):
            metadata.setdefault("strategy_spec", payload["strategy_spec"])
        entry = {
            **payload,
            "artifact_type": "strategy_spec",
            "artifact_state": "draft",
            "lineage": {"parent_registry_ids": None, **dict(payload.get("lineage") or {}), "source_strategy_spec_id": None},
            "metadata": metadata,
        }
        entry.pop("strategy_spec", None)
        entry.pop("source_digest", None)
        registry_store[payload["registry_id"]] = {"entry": entry}
        return {"entry": entry}

    monkeypatch.setattr("services.source_ingestion.distillation_controller._get_registry_entry", mock_get_registry_entry)
    monkeypatch.setattr("services.source_ingestion.distillation_controller._register_strategy_spec_if_absent", mock_register_strategy_spec)

    res = run_controller_tick(config=config, state=state, store=state_store, writer=writer)

    assert res["status"] == "success"
    # catch_up's own enqueue step found the job already admitted: nothing new
    # to enqueue, but the pre-admitted job still gets fully processed.
    assert res["reconcile"]["enqueued"] == 0
    assert res["reconcile"]["created"] == 1
    assert res["actual"]["synced_count"] == 1

    processed_job = DistillationJobQueue(job_queue_path).get(record.source_id, source_version_digest(record))
    assert processed_job.job_id == pre_admitted.job_id
    assert processed_job.status == "done"


def test_distillation_controller_immutable_protection(tmp_path, monkeypatch) -> None:
    # Setup paths
    evidence_path = tmp_path / "source_evidence.jsonl"
    job_queue_path = tmp_path / "job_queue.jsonl"
    seed_store_path = tmp_path / "seeds.jsonl"
    state_path = tmp_path / "controller_state.json"
    alive_path = tmp_path / "controller_alive"
    
    record = _normalized_source("src-note-test-002")
    with open(evidence_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"record_type": "source_record", "payload": record.to_dict()}) + "\n")
        
    config = DistillationControllerConfig(
        database_url="postgresql://test:test@localhost:5432/test",
        registry_url="http://mock-registry:8087",
        interval_seconds=60,
        max_ticks=1,
        state_path=state_path,
        alive_path=alive_path,
        job_queue_path=job_queue_path,
        seed_store_path=seed_store_path,
        evidence_store_path=evidence_path,
        source_dirs=[tmp_path],
    )
    
    state = ControllerState(
        controller_id="test-controller",
        controller_name="test-distillation-controller",
        environment="test",
        tenant_id="test",
        deployment={"git_sha": "test-sha"},
    )
    state_store = ControllerStateStore(state_path)
    state_store.save(state)
    
    writer = DummyLoopWriter()
    
    # Mock registry: report that the entry already exists and is APPROVED (immutable)
    def mock_get_registry_entry(url: str, registry_id: str) -> dict | None:
        return {"entry": {"registry_id": registry_id, "artifact_state": "approved"}}
        
    def mock_register_strategy_spec(url: str, payload: dict) -> dict:
        pytest.fail("Should not write to approved/immutable registry entry!")
        
    monkeypatch.setattr("services.source_ingestion.distillation_controller._get_registry_entry", mock_get_registry_entry)
    monkeypatch.setattr("services.source_ingestion.distillation_controller._register_strategy_spec_if_absent", mock_register_strategy_spec)
    
    # Run tick
    res = run_controller_tick(config=config, state=state, store=state_store, writer=writer)
    
    # Check that it was skipped and not synced
    assert res["status"] == "success"
    assert res["actual"]["synced_count"] == 0
    assert res["actual"]["skipped_immutable_count"] == 1


def test_distillation_registry_http_client_authorization_and_rotation(tmp_path, monkeypatch) -> None:
    from services.source_ingestion.distillation_controller import (
        _get_registry_entry,
        _register_strategy_spec_if_absent,
    )
    from unittest.mock import MagicMock

    token_file = tmp_path / "distillation_token"
    token_file.write_text("token-v1\n", encoding="utf-8")
    token_file.chmod(0o600)

    monkeypatch.setenv("DISTILLATION_REGISTRY_SERVICE_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("DISTILLATION_REGISTRY_SERVICE_TOKEN", "stale-env-token")

    captured_requests = []

    def fake_urlopen(req, timeout=5):
        captured_requests.append(req)
        resp = MagicMock()
        resp.read.return_value = b'{"entry": {"registry_id": "test-id"}}'
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    # 1. GET request with token-v1
    res1 = _get_registry_entry("http://registry:8087", "test-id")
    assert res1 == {"entry": {"registry_id": "test-id"}}
    req1 = captured_requests[-1]
    assert req1.get_method() == "GET"
    assert req1.get_header("Authorization") == "Bearer token-v1"
    assert req1.full_url == "http://registry:8087/api/registry/strategy-specs/test-id"

    # 2. POST request with token-v1
    res2 = _register_strategy_spec_if_absent("http://registry:8087", {"registry_id": "test-id"})
    assert res2 == {"entry": {"registry_id": "test-id"}}
    req2 = captured_requests[-1]
    assert req2.get_method() == "POST"
    assert req2.get_header("Authorization") == "Bearer token-v1"
    assert req2.get_header("Content-type") == "application/json"
    assert req2.full_url == "http://registry:8087/api/registry/strategy-specs"

    # 3. Rotate token in file: next call sees rotated token immediately
    token_file.write_text("token-v2\n", encoding="utf-8")

    _get_registry_entry("http://registry:8087", "test-id")
    req3 = captured_requests[-1]
    assert req3.get_header("Authorization") == "Bearer token-v2"

    _register_strategy_spec_if_absent("http://registry:8087", {"registry_id": "test-id"})
    req4 = captured_requests[-1]
    assert req4.get_header("Authorization") == "Bearer token-v2"


def test_distillation_registry_http_client_missing_blank_and_file_error(tmp_path, monkeypatch) -> None:
    from services.source_ingestion.distillation_controller import (
        _get_registry_entry,
        _register_strategy_spec_if_absent,
    )
    from unittest.mock import MagicMock

    captured_requests = []

    def fake_urlopen(req, timeout=5):
        captured_requests.append(req)
        resp = MagicMock()
        resp.read.return_value = b'{"entry": {"registry_id": "test-id"}}'
        resp.__enter__.return_value = resp
        return resp

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    # 1. Missing / unconfigured: preserves unauthenticated test/local behavior
    monkeypatch.delenv("DISTILLATION_REGISTRY_SERVICE_TOKEN_FILE", raising=False)
    monkeypatch.delenv("DISTILLATION_REGISTRY_SERVICE_TOKEN", raising=False)

    _get_registry_entry("http://registry:8087", "test-id")
    assert captured_requests[-1].get_header("Authorization") is None

    _register_strategy_spec_if_absent("http://registry:8087", {"registry_id": "test-id"})
    assert captured_requests[-1].get_header("Authorization") is None

    # 2. Blank env token: no header
    monkeypatch.setenv("DISTILLATION_REGISTRY_SERVICE_TOKEN", "   ")
    _get_registry_entry("http://registry:8087", "test-id")
    assert captured_requests[-1].get_header("Authorization") is None

    # 3. File error: raises RuntimeError and does not log credentials
    bad_token_file = tmp_path / "bad_token"
    bad_token_file.write_text("secret-value", encoding="utf-8")
    bad_token_file.chmod(0o644)  # Unsafe permission

    monkeypatch.setenv("DISTILLATION_REGISTRY_SERVICE_TOKEN_FILE", str(bad_token_file))

    with pytest.raises(RuntimeError) as exc_info_get:
        _get_registry_entry("http://registry:8087", "test-id")
    assert "Configured service credential unavailable" in str(exc_info_get.value)
    assert "secret-value" not in str(exc_info_get.value)

    with pytest.raises(RuntimeError) as exc_info_post:
        _register_strategy_spec_if_absent("http://registry:8087", {"registry_id": "test-id"})
    assert "Configured service credential unavailable" in str(exc_info_post.value)
    assert "secret-value" not in str(exc_info_post.value)
