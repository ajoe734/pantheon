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
from services.source_ingestion.distillation_worker import DistillationJobQueue


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
    
    # Mock registry HTTP requests
    registry_queries = []
    registry_registrations = []
    
    def mock_get_registry_entry(url: str, registry_id: str) -> dict | None:
        registry_queries.append(registry_id)
        return None  # Pretend it is not yet registered
        
    def mock_register_strategy_spec(url: str, payload: dict) -> dict:
        registry_registrations.append(payload)
        return {"entry": {"registry_id": payload["registry_id"], "artifact_state": "draft"}}
        
    monkeypatch.setattr("services.source_ingestion.distillation_controller._get_registry_entry", mock_get_registry_entry)
    monkeypatch.setattr("services.source_ingestion.distillation_controller._register_strategy_spec", mock_register_strategy_spec)
    
    # 2. Run tick
    res = run_controller_tick(config=config, state=state, store=state_store, writer=writer)
    
    # 3. Assertions
    assert res["status"] == "success"
    assert res["reconcile"]["created"] == 1
    assert res["actual"]["synced_count"] == 1
    assert len(registry_queries) == 1
    assert registry_queries[0] == "reg-strategy-spec-src-note-test-001"
    assert len(registry_registrations) == 1
    assert registry_registrations[0]["registry_id"] == "reg-strategy-spec-src-note-test-001"
    assert len(writer.successes) == 1
    assert writer.successes[0]["loop_id"] == "strategy_distillation"
    assert alive_path.exists()


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
    monkeypatch.setattr("services.source_ingestion.distillation_controller._register_strategy_spec", mock_register_strategy_spec)
    
    # Run tick
    res = run_controller_tick(config=config, state=state, store=state_store, writer=writer)
    
    # Check that it was skipped and not synced
    assert res["status"] == "success"
    assert res["actual"]["synced_count"] == 0
    assert res["actual"]["skipped_immutable_count"] == 1
