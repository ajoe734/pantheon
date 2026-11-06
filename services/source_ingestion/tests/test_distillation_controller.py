"""Tests for the Strategy Distillation supervised loop controller.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from services.source_ingestion.connectors.base import SourceRecord, SourceRecordStatus
from services.source_ingestion.controller_state import ControllerState, ControllerStateError, ControllerStateStore
from services.source_ingestion.distillation_controller import (
    DistillationControllerConfig,
    DistillationControllerError,
    refresh_runtime_identity,
    run_controller_tick,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore
from services.source_ingestion.distillation_worker import (
    DistillationJobQueue,
    DistillationJobStatus,
    MARKET_SOURCE_SKIP_REASON,
    source_version_digest,
)


class DummyLoopWriter:
    def __init__(self) -> None:
        self.successes = []
        self.ticks = []
        self.failures = []

    async def record_success(self, **kwargs: Any) -> None:
        self.successes.append(kwargs)

    async def record_tick(self, **kwargs: Any) -> None:
        self.ticks.append(kwargs)

    async def record_failure(self, **kwargs: Any) -> None:
        self.failures.append(kwargs)


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
    record = _normalized_source("src-note-test-001", tenant_id="test")
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

    # Published fields must project as admissible controller truth.
    import importlib
    from datetime import datetime, timedelta, timezone

    import jsonschema

    published = writer.successes[0]
    now = datetime.now(timezone.utc)
    lease_seconds = published["lease_duration_seconds"]
    assert lease_seconds >= config.interval_seconds + 30
    row = {
        "loop_id": published["loop_id"],
        "tenant_id": state.tenant_id,
        "environment": state.environment,
        "controller_id": state.controller_id,
        "controller_name": state.controller_name,
        "deployment_sha": "test-sha",
        "desired_state_query": None,
        "actual_state_query": None,
        "desired_state": published["desired_state"],
        "downstream_actual_state": published["downstream_actual_state"],
        "last_heartbeat_at": now,
        "last_tick_at": None,
        "last_success_at": now,
        "last_failure_at": None,
        "last_failure_reason": None,
        "last_repair_at": None,
        "last_repair_reason": None,
        "backlog": published["backlog"],
        "lag": None,
        "dlq_count": None,
        "evidence_refs": published["evidence_refs"],
        "truth_level": published["truth_level"],
        "lease_token": "token",
        "lease_expires_at": now + timedelta(seconds=lease_seconds),
        "payload": published["payload"],
    }
    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "schemas" / "loop-controller-record.schema.json").read_text()
    )
    jsonschema.Draft7Validator(schema).validate(json.loads(json.dumps(row, default=lambda v: v.isoformat())))
    projector = importlib.import_module("services.loop-control.projector")
    # Steady state: the next tick is one interval away; the record must stay healthy.
    later = now + timedelta(seconds=config.interval_seconds)
    projected = projector.project_controller_record_to_bff(row, now=later)
    assert projected["desired_state_presence"]["authoritative"] is True
    assert projected["downstream_actual_state"]["authoritative"] is True
    assert projected["evidence_refs"]
    assert projected["controller_health"]["status"] == "healthy"


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

    record = _normalized_source("src-note-test-003", tenant_id="test")
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
    
    record = _normalized_source("src-note-test-002", tenant_id="test")
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


def test_restart_refreshes_identity_but_preserves_same_scope_progress(monkeypatch):
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-dev")
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("GIT_SHA", "new-sha")
    monkeypatch.setenv("IMAGE_DIGEST", "sha256:new-image")
    monkeypatch.setenv("BUILD_TIME", "new-build")
    monkeypatch.setenv("PANTHEON_CONTROLLER_NAME", "new-controller-name")
    state = ControllerState(
        controller_id="old-process", controller_name="old-name", environment="dev",
        tenant_id="tenant-dev", deployment={"git_sha": "old-sha"}, started_at="old-start",
    )
    state.record_success(desired_state={"cursor": 4}, reconcile={}, schedule={}, actual_readback={"done": 3})
    before = state.to_dict()
    assert refresh_runtime_identity(state) is state
    after = state.to_dict()
    assert after["deployment"] == {"git_sha": "new-sha", "image_digest": "sha256:new-image", "build_time": "new-build"}
    assert after["controller_id"] != before["controller_id"]
    assert after["controller_name"] == "new-controller-name"
    assert after["started_at"] != "old-start"
    for key in before.keys() - {"controller_id", "controller_name", "deployment", "started_at", "heartbeat_at"}:
        assert after[key] == before[key], key


@pytest.mark.parametrize("tenant,environment", [("other", "dev"), ("tenant-dev", "prod"), ("other", "prod")])
def test_restart_rejects_foreign_checkpoint_without_mutating_it(monkeypatch, tenant, environment):
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-dev")
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    state = ControllerState(controller_id="old", controller_name="distill", environment=environment, tenant_id=tenant, deployment={"git_sha": "old"})
    before = state.to_dict()
    with pytest.raises(ControllerStateError, match="tenant/environment"):
        refresh_runtime_identity(state)
    assert state.to_dict() == before


@pytest.mark.parametrize("writer_unavailable", [False, True])
def test_failed_tick_publishes_failure_not_reconciled_proof(tmp_path, monkeypatch, writer_unavailable):
    config = DistillationControllerConfig(
        database_url="unused", registry_url="http://unused.invalid", interval_seconds=60, max_ticks=1,
        state_path=tmp_path / "state.json", alive_path=tmp_path / "alive",
        job_queue_path=tmp_path / "queue.sqlite", seed_store_path=tmp_path / "seeds.jsonl",
        evidence_store_path=tmp_path / "evidence.jsonl", source_dirs=[],
    )
    state = ControllerState(controller_id="process", controller_name="distill", environment="dev", tenant_id="tenant-dev", deployment={"git_sha": "current"})
    state.record_success(desired_state={}, reconcile={}, schedule={}, actual_readback={})
    last_success = state.last_success_at
    store = ControllerStateStore(config.state_path)
    writer = DummyLoopWriter()
    monkeypatch.setenv("PANTHEON_LOOP_ID", "L2")

    def fail_read(*args, **kwargs):
        raise ValueError("source repository failed")

    monkeypatch.setattr("services.source_ingestion.distillation_controller.read_source_records_for_tenant", fail_read)
    if writer_unavailable:
        async def fail_write(**kwargs):
            raise OSError("shared writer unavailable")
        monkeypatch.setattr(writer, "record_failure", fail_write)
    with pytest.raises(DistillationControllerError, match="source repository failed") as failure:
        run_controller_tick(config=config, state=state, store=store, writer=writer)
    assert failure.value.stage == "desired_read"
    persisted = store.load()
    assert persisted.last_success_at == last_success
    assert persisted.last_failure_at is not None
    assert persisted.last_failure_stage == "desired_read"
    assert persisted.consecutive_failures == 1
    assert not writer.successes and not writer.ticks
    assert config.alive_path.exists()  # liveness is deliberately not business success
    assert not config.job_queue_path.exists()  # no recovery or replay
    if not writer_unavailable:
        assert len(writer.failures) == 1
        report = writer.failures[0]
        assert report["loop_id"] == "L2"
        assert report["truth_level"] == "scheduled_tick"
        assert report["reason"].startswith("desired_read:")
        assert report["payload"]["error_stage"] == "desired_read"


def test_controller_redrives_transient_dead_letters_after_registry_recovers(tmp_path, monkeypatch) -> None:
    evidence_path = tmp_path / "source_evidence.jsonl"
    job_queue_path = tmp_path / "job_queue.jsonl"
    state_path = tmp_path / "controller_state.json"
    record = _normalized_source("src-note-test-003", tenant_id="test")
    evidence_path.write_text(
        json.dumps({"record_type": "source_record", "payload": record.to_dict()}) + "\n",
        encoding="utf-8",
    )
    config = DistillationControllerConfig(
        database_url="postgresql://test:test@localhost:5432/test",
        registry_url="http://mock-registry:8087",
        interval_seconds=60,
        max_ticks=1,
        state_path=state_path,
        alive_path=tmp_path / "alive",
        job_queue_path=job_queue_path,
        seed_store_path=tmp_path / "seeds.jsonl",
        evidence_store_path=evidence_path,
        source_dirs=[tmp_path],
        max_attempts=1,
    )
    state = ControllerState(
        controller_id="test-controller",
        controller_name="test-distillation-controller",
        environment="test",
        tenant_id="test",
        deployment={"git_sha": "test-sha"},
    )
    store = ControllerStateStore(state_path)
    store.save(state)
    registry = {"up": False, "entries": {}}

    def get_entry(url, registry_id):
        if not registry["up"]:
            raise RuntimeError("Failed to query registry: timed out")
        return registry["entries"].get(registry_id)

    def register(url, payload):
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
        registry["entries"][payload["registry_id"]] = {"entry": entry}
        return {"entry": entry}

    mod = "services.source_ingestion.distillation_controller"
    monkeypatch.setattr(f"{mod}._get_registry_entry", get_entry)
    monkeypatch.setattr(f"{mod}._register_strategy_spec_if_absent", register)

    with pytest.raises(Exception, match="degraded"):
        run_controller_tick(config=config, state=state, store=store, writer=DummyLoopWriter())

    # Registry outage persists: still degraded, nothing redriven.
    with pytest.raises(Exception, match="degraded"):
        run_controller_tick(config=config, state=state, store=store, writer=DummyLoopWriter())

    registry["up"] = True
    res = run_controller_tick(config=config, state=state, store=store, writer=DummyLoopWriter())
    assert res["status"] == "success"
    assert res["actual"]["outbox"]["dead_letter"] == 0
    assert res["actual"]["synced_count"] == 1


def _legacy_raw_refs_digest(source: SourceRecord) -> str:
    import hashlib
    from services.source_ingestion.distillation_worker import _canonical_json

    metadata = dict(source.metadata)
    for volatile_key in ("ingest_run_id", "source_ingest_run_id", "trace_id"):
        metadata.pop(volatile_key, None)
    identity = _canonical_json(
        {
            "source_id": source.source_id,
            "connector_id": source.connector_id,
            "source_type": source.source_type.value,
            "title": source.title,
            "content_ref": source.content_ref,
            "metadata": metadata,
        }
    )
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def test_controller_processes_pending_and_dlq_jobs_persisted_under_previous_raw_refs_projection(
    tmp_path, monkeypatch
) -> None:
    import sqlite3
    from services.source_ingestion.distillation_worker import _canonical_json, _stable_job_id

    evidence_path = tmp_path / "source_evidence.jsonl"
    job_queue_path = tmp_path / "job_queue.sqlite3"
    seed_store_path = tmp_path / "seeds.jsonl"
    state_path = tmp_path / "controller_state.json"

    pending_initial = _normalized_source(
        "tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19",
        tenant_id="test",
        ingest_run_id="ingest-ac0cc7b00496",
        source_ingest_run_id="ingest-ac0cc7b00496",
        raw_storage_refs=[
            {"dataset": "TaiwanStockPrice", "raw_blob_path": "raw/run-1/2330.json", "sha256": "11" * 32}
        ],
    )
    dlq_initial = _normalized_source(
        "tw-official:tw_price_daily:TWSE:2317:aa11bb22cc33dd44",
        tenant_id="test",
        ingest_run_id="ingest-ac0cc7b00496",
        source_ingest_run_id="ingest-ac0cc7b00496",
        raw_storage_refs=[
            {"dataset": "TaiwanStockPrice", "raw_blob_path": "raw/run-1/2317.json", "sha256": "22" * 32}
        ],
    )

    queue = DistillationJobQueue(job_queue_path, default_max_attempts=1)
    pending_legacy_digest = _legacy_raw_refs_digest(pending_initial)
    pending_legacy_job_id = _stable_job_id(pending_initial.source_id, pending_legacy_digest)
    dlq_legacy_digest = _legacy_raw_refs_digest(dlq_initial)
    dlq_legacy_job_id = _stable_job_id(dlq_initial.source_id, dlq_legacy_digest)

    with sqlite3.connect(job_queue_path) as conn:
        for record, digest, job_id, status in (
            (pending_initial, pending_legacy_digest, pending_legacy_job_id, "pending"),
            (dlq_initial, dlq_legacy_digest, dlq_legacy_job_id, "dead_letter"),
        ):
            event_id = f"source-normalized:{record.source_id}:{digest}"
            idempotency_key = f"distill:{record.source_id}:{digest}"
            payload_json = _canonical_json(record.to_dict())
            conn.execute(
                """
                INSERT INTO distillation_source_versions (
                    source_id, source_digest, event_version, payload_json, committed_at
                ) VALUES (?, ?, 'source_record.normalized.v1', ?, 1792481535.0)
                """,
                (record.source_id, digest, payload_json),
            )
            conn.execute(
                """
                INSERT INTO distillation_outbox (
                    job_id, event_id, source_id, source_digest, event_version,
                    idempotency_key, status, enqueued_at, available_at, attempts, max_attempts, updated_at
                ) VALUES (?, ?, ?, ?, 'source_record.normalized.v1', ?, ?, 1792481535.0, 1792481535.0, ?, 1, 1792481535.0)
                """,
                (
                    job_id,
                    event_id,
                    record.source_id,
                    digest,
                    idempotency_key,
                    status,
                    1 if status == "dead_letter" else 0,
                ),
            )
            if status == "dead_letter":
                conn.execute(
                    """
                    INSERT INTO distillation_inbox (
                        event_id, job_id, payload_digest, status, attempts, last_error, updated_at
                    ) VALUES (?, ?, ?, 'dead_letter', 1, 'registry_sync_failed: RuntimeError: Failed to query registry: timed out', 1792481535.0)
                    """,
                    (event_id, job_id, digest),
                )
                conn.execute(
                    """
                    INSERT INTO distillation_dead_letters (
                        job_id, event_id, source_id, source_digest, payload_json, attempts, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, 'registry_sync_failed: RuntimeError: Failed to query registry: timed out', 1792481535.0)
                    """,
                    (job_id, event_id, record.source_id, digest, payload_json),
                )
        conn.commit()

    # Desired state has re-ingested snapshots with new batch raw_storage_refs
    pending_reingested = _normalized_source(
        pending_initial.source_id,
        tenant_id="test",
        ingest_run_id="ingest-84555a4c38a4",
        source_ingest_run_id="ingest-84555a4c38a4",
        raw_storage_refs=[
            {"dataset": "TaiwanStockPrice", "raw_blob_path": "raw/run-2/2330.json", "sha256": "aa" * 32}
        ],
    )
    dlq_reingested = _normalized_source(
        dlq_initial.source_id,
        tenant_id="test",
        ingest_run_id="ingest-84555a4c38a4",
        source_ingest_run_id="ingest-84555a4c38a4",
        raw_storage_refs=[
            {"dataset": "TaiwanStockPrice", "raw_blob_path": "raw/run-2/2317.json", "sha256": "bb" * 32}
        ],
    )
    evidence_path.write_text(
        "\n".join(
            json.dumps({"record_type": "source_record", "payload": r.to_dict()})
            for r in (pending_reingested, dlq_reingested)
        )
        + "\n",
        encoding="utf-8",
    )

    config = DistillationControllerConfig(
        database_url="postgresql://test:test@localhost:5432/test",
        registry_url="http://mock-registry:8087",
        interval_seconds=60,
        max_ticks=1,
        state_path=state_path,
        alive_path=tmp_path / "alive",
        job_queue_path=job_queue_path,
        seed_store_path=seed_store_path,
        evidence_store_path=evidence_path,
        source_dirs=[tmp_path],
        max_attempts=1,
    )
    state = ControllerState(
        controller_id="test-controller",
        controller_name="test-distillation-controller",
        environment="test",
        tenant_id="test",
        deployment={"git_sha": "test-sha"},
    )
    store = ControllerStateStore(state_path)
    store.save(state)
    registry_entries: dict[str, dict] = {}

    def get_entry(url, registry_id):
        return registry_entries.get(registry_id)

    def register(url, payload):
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
        registry_entries[payload["registry_id"]] = {"entry": entry}
        return {"entry": entry}

    mod = "services.source_ingestion.distillation_controller"
    monkeypatch.setattr(f"{mod}._get_registry_entry", get_entry)
    monkeypatch.setattr(f"{mod}._register_strategy_spec_if_absent", register)

    res = run_controller_tick(
        config=config, state=state, store=store, writer=DummyLoopWriter(), queue=queue
    )
    assert res["status"] == "success"
    assert res["reconcile"]["enqueued"] == 0
    assert res["reconcile"]["processed"] == 2
    assert res["actual"]["synced_count"] == 2
    assert res["actual"]["outbox"]["dead_letter"] == 0
    assert queue.count() == 2
    assert queue.version_count(pending_initial.source_id) == 1
    assert queue.version_count(dlq_initial.source_id) == 1

    expected_pending_reg_id = (
        f"reg-strategy-spec-{pending_initial.source_id}-"
        f"{pending_legacy_digest.removeprefix('sha256:')[:12]}"
    )
    expected_dlq_reg_id = (
        f"reg-strategy-spec-{dlq_initial.source_id}-"
        f"{dlq_legacy_digest.removeprefix('sha256:')[:12]}"
    )
    assert set(registry_entries) == {expected_pending_reg_id, expected_dlq_reg_id}
    pending_job = queue.get(pending_initial.source_id, pending_legacy_digest)
    dlq_job = queue.get(dlq_initial.source_id, dlq_legacy_digest)
    assert pending_job.job_id == pending_legacy_job_id
    assert pending_job.source_digest == pending_legacy_digest
    assert pending_job.registry_id == expected_pending_reg_id
    assert dlq_job.job_id == dlq_legacy_job_id
    assert dlq_job.source_digest == dlq_legacy_digest
    assert dlq_job.registry_id == expected_dlq_reg_id


def test_build_registry_payload_accepts_previous_raw_refs_digest_and_rejects_tampered_digest(
    tmp_path
) -> None:
    from services.source_ingestion.distillation_controller import (
        _build_registry_payload,
        _versioned_registry_id,
    )
    from services.source_ingestion.distillation_worker import (
        DistillationJob,
        DistillationJobStatus,
        RegistrySyncRequest,
        _stable_job_id,
        _synthesize_evidence_bundle,
        _synthesize_evidence_item,
    )
    from services.source_ingestion.seed_materializer import (
        MaterializationMode,
        SeedMaterializationService,
    )

    source = _normalized_source(
        "tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19",
        tenant_id="test",
        raw_storage_refs=[
            {"dataset": "TaiwanStockPrice", "raw_blob_path": "raw/run-1/2330.json", "sha256": "11" * 32}
        ],
    )
    legacy_digest = _legacy_raw_refs_digest(source)
    assert legacy_digest != source_version_digest(source)

    item = _synthesize_evidence_item(source, legacy_digest)
    bundle = _synthesize_evidence_bundle(source, item, legacy_digest)
    seed_store = StrategySpecSeedStore(tmp_path / "seeds.jsonl")
    seed = SeedMaterializationService(store=seed_store, created_by="test").materialize(
        bundle,
        source_records=[source],
        evidence_items=[item],
        mode=MaterializationMode.CREATE_IF_ABSENT,
        requested_by="test",
        idempotency_key=f"distill:{source.source_id}:{legacy_digest}",
    ).seed
    job = DistillationJob(
        job_id=_stable_job_id(source.source_id, legacy_digest),
        source_id=source.source_id,
        idempotency_key=f"distill:{source.source_id}:{legacy_digest}",
        status=DistillationJobStatus.LEASED,
        enqueued_at="2026-10-20T07:32:05Z",
        source_digest=legacy_digest,
    )
    registry_id = _versioned_registry_id(source, job)
    payload = _build_registry_payload(
        request=RegistrySyncRequest(
            source=source,
            seed=seed,
            job=job,
            evidence_item=item,
            evidence_bundle=bundle,
            version_key=legacy_digest,
        ),
        registry_id=registry_id,
    )
    assert payload["registry_id"] == registry_id
    assert payload["source_digest"] == legacy_digest
    assert payload["metadata"]["distillation"]["source_digest"] == legacy_digest

    tampered_digest = "sha256:" + ("0" * 64)
    tampered_item = _synthesize_evidence_item(source, tampered_digest)
    tampered_bundle = _synthesize_evidence_bundle(source, tampered_item, tampered_digest)
    tampered_job = DistillationJob(
        job_id=_stable_job_id(source.source_id, tampered_digest),
        source_id=source.source_id,
        idempotency_key=f"distill:{source.source_id}:{tampered_digest}",
        status=DistillationJobStatus.LEASED,
        enqueued_at="2026-10-20T07:32:05Z",
        source_digest=tampered_digest,
    )
    with pytest.raises(RuntimeError, match="Registry sync source digest mismatch"):
        _build_registry_payload(
            request=RegistrySyncRequest(
                source=source,
                seed=seed,
                job=tampered_job,
                evidence_item=tampered_item,
                evidence_bundle=tampered_bundle,
                version_key=tampered_digest,
            ),
            registry_id=_versioned_registry_id(source, tampered_job),
        )


def test_controller_tick_with_only_market_sources_skips_registry_and_records_healthy_success(
    tmp_path, monkeypatch
) -> None:
    import importlib
    import jsonschema
    from services.source_ingestion.distillation_controller import (
        RegistrySyncRequest,
        _make_registry_sync,
    )
    from services.source_ingestion.distillation_worker import (
        DistillationJob,
        _stable_job_id,
        _synthesize_evidence_bundle,
        _synthesize_evidence_item,
    )
    from services.source_ingestion.seed_materializer import (
        MaterializationMode,
        SeedMaterializationService,
    )

    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    evidence_path = tmp_path / "evidence.jsonl"
    market_sources = [
        _normalized_source(
            "tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19",
            title="TWSE 2330 daily price bar",
            source_type="market",
            tenant_id="test",
            symbol="2330",
        ),
        _normalized_source(
            "tw-official:tw_price_daily:TWSE:2317:99aabbccddeeff00",
            title="TWSE 2317 daily price bar",
            source_type="market",
            tenant_id="test",
            symbol="2317",
        ),
    ]
    with evidence_path.open("w", encoding="utf-8") as fh:
        for src in market_sources:
            fh.write(json.dumps({"record_type": "source_record", "payload": src.to_dict()}) + "\n")

    state_path = tmp_path / "state.json"
    config = DistillationControllerConfig(
        database_url="postgresql://fake",
        registry_url="http://fake-registry",
        interval_seconds=60,
        max_ticks=1,
        state_path=state_path,
        alive_path=tmp_path / "alive",
        job_queue_path=tmp_path / "jobs.jsonl",
        seed_store_path=tmp_path / "seeds.jsonl",
        evidence_store_path=evidence_path,
        source_dirs=[source_dir],
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

    registry_reads: list[str] = []
    registry_writes: list[dict[str, Any]] = []

    def forbidden_get_entry(url: str, registry_id: str) -> dict | None:
        registry_reads.append(registry_id)
        pytest.fail(f"Registry read must not occur for market source: {registry_id}")

    def forbidden_register(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        registry_writes.append(payload)
        pytest.fail(f"Registry write must not occur for market source: {payload.get('registry_id')}")

    mod = "services.source_ingestion.distillation_controller"
    monkeypatch.setattr(f"{mod}._get_registry_entry", forbidden_get_entry)
    monkeypatch.setattr(f"{mod}._register_strategy_spec_if_absent", forbidden_register)

    # Also verify direct _make_registry_sync guard makes zero HTTP calls for market sources.
    direct_sync = _make_registry_sync(config)
    m0 = market_sources[0]
    m0_digest = source_version_digest(m0)
    m0_item = _synthesize_evidence_item(m0, m0_digest)
    m0_bundle = _synthesize_evidence_bundle(m0, m0_item, m0_digest)
    temp_seeds = StrategySpecSeedStore(tmp_path / "direct_seeds.jsonl")
    m0_seed = SeedMaterializationService(store=temp_seeds, created_by="test").materialize(
        m0_bundle,
        source_records=[m0],
        evidence_items=[m0_item],
        mode=MaterializationMode.CREATE_IF_ABSENT,
        requested_by="test",
        idempotency_key=f"distill:{m0.source_id}:{m0_digest}",
    ).seed
    direct_res = direct_sync(
        RegistrySyncRequest(
            source=m0,
            seed=m0_seed,
            job=DistillationJob(
                job_id=_stable_job_id(m0.source_id, m0_digest),
                source_id=m0.source_id,
                idempotency_key=f"distill:{m0.source_id}:{m0_digest}",
                status=DistillationJobStatus.LEASED,
                enqueued_at="2026-11-06T00:00:00Z",
                source_digest=m0_digest,
            ),
            evidence_item=m0_item,
            evidence_bundle=m0_bundle,
            version_key=m0_digest,
        )
    )
    assert direct_res.status == "skipped"
    assert direct_res.reason == MARKET_SOURCE_SKIP_REASON
    assert direct_res.registry_id == ""

    res = run_controller_tick(config=config, state=state, store=state_store, writer=writer)
    assert res["status"] == "success"
    assert registry_reads == []
    assert registry_writes == []
    assert res["reconcile"]["enqueued"] == 2
    assert res["reconcile"]["processed"] == 2
    assert res["reconcile"]["created"] == 0
    assert res["reconcile"]["skipped"] == 2
    assert res["reconcile"]["failed"] == 0
    assert res["reconcile"]["registry_synced"] == 0
    assert res["actual"]["synced_count"] == 0
    assert res["actual"]["sync_failures_count"] == 0
    assert res["actual"]["pending_dead_letter_count"] == 0
    assert res["actual"]["terminal_drafts"] == []

    # Seeds are still materialized locally for market sources.
    seed_store = StrategySpecSeedStore(config.seed_store_path)
    assert len(seed_store.list_all()) == 2

    queue = DistillationJobQueue(config.job_queue_path)
    for src in market_sources:
        job = queue.get(src.source_id, source_version_digest(src))
        assert job is not None
        assert job.status == DistillationJobStatus.SKIPPED
        assert job.skip_reason == MARKET_SOURCE_SKIP_REASON
        assert job.registry_id is None
        assert job.attempts == 1
    assert queue.list_dead_letters() == []

    # LoopControllerWriter received healthy desired & downstream status and 0 terminal drafts in summary.
    assert len(writer.successes) == 1
    assert len(writer.failures) == 0
    recorded = writer.successes[0]
    assert recorded["desired_state"]["present"] is True
    assert recorded["desired_state"]["source"] == "source-ingest.normalized_source_records"
    assert recorded["desired_state"]["summary"] == "2 eligible normalized source records"
    assert recorded["downstream_actual_state"]["status"] == "ready"
    assert recorded["downstream_actual_state"]["source"] == "registry.distillation_job_queue"
    assert recorded["downstream_actual_state"]["summary"] == "0 terminal drafts; 0 dead letters"

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    lease_seconds = recorded["lease_duration_seconds"]
    row = {
        "loop_id": recorded["loop_id"],
        "tenant_id": state.tenant_id,
        "environment": state.environment,
        "controller_id": state.controller_id,
        "controller_name": state.controller_name,
        "deployment_sha": "test-sha",
        "desired_state_query": None,
        "actual_state_query": None,
        "desired_state": recorded["desired_state"],
        "downstream_actual_state": recorded["downstream_actual_state"],
        "last_heartbeat_at": now,
        "last_tick_at": None,
        "last_success_at": now,
        "last_failure_at": None,
        "last_failure_reason": None,
        "last_repair_at": None,
        "last_repair_reason": None,
        "backlog": recorded["backlog"],
        "lag": None,
        "dlq_count": None,
        "evidence_refs": recorded["evidence_refs"],
        "truth_level": recorded["truth_level"],
        "lease_token": "token",
        "lease_expires_at": now + timedelta(seconds=lease_seconds),
        "payload": recorded["payload"],
    }
    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "schemas" / "loop-controller-record.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.Draft7Validator(schema).validate(
        json.loads(json.dumps(row, default=lambda v: v.isoformat()))
    )
    projector = importlib.import_module("services.loop-control.projector")
    projected = projector.project_controller_record_to_bff(
        row,
        now=now + timedelta(seconds=config.interval_seconds),
    )
    assert projected["desired_state_presence"]["authoritative"] is True
    assert projected["downstream_actual_state"]["authoritative"] is True
    assert projected["controller_health"]["status"] == "healthy"


def test_controller_tick_internal_note_syncs_while_market_skips_and_approved_row_is_untouched(
    tmp_path, monkeypatch
) -> None:
    import copy

    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    approved_registry_id = (
        "reg-strategy-spec-tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19-da7cb8297afb"
    )
    approved_entry = {
        "registry_id": approved_registry_id,
        "artifact_kind": "StrategySpec",
        "artifact_state": "approved",
        "status": "ACTIVE",
        "strategy_id": "strat-tw-2330-approved",
        "version": "1.0.0",
        "checksum": "chk-approved-2330",
        "payload": {
            "registry_id": approved_registry_id,
            "strategy_id": "strat-tw-2330-approved",
            "version": "1.0.0",
            "checksum": "chk-approved-2330",
            "status": "ACTIVE",
        },
    }

    historical_market = _normalized_source(
        "tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19",
        title="TWSE 2330 historical bar",
        source_type="market",
        tenant_id="test",
        ingest_run_id="ingest-run-old",
        raw_storage_refs=[
            {"dataset": "TaiwanStockPrice", "raw_blob_path": "raw/old/2330.json", "sha256": "11" * 32}
        ],
    )
    new_market = _normalized_source(
        "tw-official:tw_price_daily:TWSE:2454:1122334455667788",
        title="TWSE 2454 daily bar",
        source_type="market",
        tenant_id="test",
        ingest_run_id="ingest-run-new",
    )
    note_source = _normalized_source(
        "internal-note:desk:semi-cycle-20261106",
        title="Semiconductor inventory inflection note",
        source_type="internal_note",
        tenant_id="test",
    )

    queue_path = tmp_path / "jobs.jsonl"
    queue = DistillationJobQueue(queue_path)
    hist_digest = source_version_digest(historical_market)
    enqueued_hist = queue.enqueue_source_record(historical_market, requested_by="legacy", enqueued_at=1000.0)
    claimed_hist = queue.claim_due(worker_id="legacy", lease_seconds=30.0, now=1000.0)
    assert len(claimed_hist) == 1
    queue.mark_done(
        claimed_hist[0].job_id,
        seed_id="seed-legacy-2330",
        registry_id=approved_registry_id,
        strategy_id="strat-tw-2330-approved",
        version="1.0.0",
        checksum="chk-approved-2330",
        processed_at=1001.0,
        claim_token=claimed_hist[0].lease_token,
    )

    # Replay the historical market source with a newer ingest_run_id alongside a new market bar and an internal_note.
    replayed_historical_market = _normalized_source(
        "tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19",
        title="TWSE 2330 historical bar",
        source_type="market",
        tenant_id="test",
        ingest_run_id="ingest-run-replayed",
        raw_storage_refs=[
            {"dataset": "TaiwanStockPrice", "raw_blob_path": "raw/new/2330.json", "sha256": "22" * 32}
        ],
    )
    evidence_path = tmp_path / "evidence.jsonl"
    with evidence_path.open("w", encoding="utf-8") as fh:
        for src in (replayed_historical_market, new_market, note_source):
            fh.write(json.dumps({"record_type": "source_record", "payload": src.to_dict()}) + "\n")

    registry_entries: dict[str, dict[str, Any]] = {
        approved_registry_id: copy.deepcopy(approved_entry),
    }
    registry_reads: list[str] = []
    registry_writes: list[str] = []

    def get_entry(url: str, registry_id: str) -> dict[str, Any] | None:
        registry_reads.append(registry_id)
        entry = registry_entries.get(registry_id)
        return {"entry": copy.deepcopy(entry)} if entry is not None else None

    def register(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        reg_id = payload["registry_id"]
        registry_writes.append(reg_id)
        metadata = dict(payload.get("metadata") or {})
        if isinstance(payload.get("strategy_spec"), dict):
            metadata.setdefault("strategy_spec", payload["strategy_spec"])
        entry = {
            **copy.deepcopy(payload),
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
        registry_entries[reg_id] = entry
        return {"entry": copy.deepcopy(entry)}

    mod = "services.source_ingestion.distillation_controller"
    monkeypatch.setattr(f"{mod}._get_registry_entry", get_entry)
    monkeypatch.setattr(f"{mod}._register_strategy_spec_if_absent", register)

    state_path = tmp_path / "state.json"
    config = DistillationControllerConfig(
        database_url="postgresql://fake",
        registry_url="http://fake-registry",
        interval_seconds=60,
        max_ticks=1,
        state_path=state_path,
        alive_path=tmp_path / "alive",
        job_queue_path=queue_path,
        seed_store_path=tmp_path / "seeds.jsonl",
        evidence_store_path=evidence_path,
        source_dirs=[source_dir],
    )
    state = ControllerState(
        controller_id="test-controller",
        controller_name="test-distillation-controller",
        environment="test",
        tenant_id="test",
        deployment={"git_sha": "test-sha"},
    )
    store = ControllerStateStore(state_path)
    store.save(state)
    writer = DummyLoopWriter()

    res = run_controller_tick(config=config, state=state, store=store, writer=writer, queue=queue)
    assert res["status"] == "success"
    assert res["reconcile"]["enqueued"] == 2
    assert res["reconcile"]["processed"] == 2
    assert res["reconcile"]["created"] == 1
    assert res["reconcile"]["skipped"] == 1
    assert res["reconcile"]["failed"] == 0
    assert res["reconcile"]["registry_synced"] == 1

    note_digest = source_version_digest(note_source)
    expected_note_reg_id = (
        f"reg-strategy-spec-{note_source.source_id}-{note_digest.removeprefix('sha256:')[:12]}"
    )
    assert registry_writes == [expected_note_reg_id]
    assert set(registry_reads) == {expected_note_reg_id}

    # Approved row in Registry is completely untouched.
    assert registry_entries[approved_registry_id] == approved_entry

    # Historical market job stayed terminal (DONE) and did not fail; new market job is SKIPPED.
    hist_job = queue.get(historical_market.source_id, hist_digest)
    assert hist_job is not None
    assert hist_job.status == DistillationJobStatus.DONE
    assert hist_job.registry_id == approved_registry_id

    new_market_job = queue.get(new_market.source_id, source_version_digest(new_market))
    assert new_market_job is not None
    assert new_market_job.status == DistillationJobStatus.SKIPPED
    assert new_market_job.skip_reason == MARKET_SOURCE_SKIP_REASON
    assert new_market_job.registry_id is None



