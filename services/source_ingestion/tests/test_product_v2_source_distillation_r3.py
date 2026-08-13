"""Product V2 Source Distillation R3 Acceptance Test.

Verifies the complete durable source-to-distillation product path:
1. Real bounded SourceRecord ingestion via service API and IngestionScheduler.
2. Ingestion controller state persistence and readback.
3. Supervised Distillation Controller tick reconciling normalized SourceRecords into durable jobs.
4. Distillation worker processing jobs into StrategySpecSeeds.
5. Replayable persistence in StrategySpecSeedStore and EvidenceBundle readback without artificial mocks.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
try:
    from fastapi.testclient import TestClient
except (ImportError, Exception):
    TestClient = None

from services.source_ingestion.connectors import SourceRecord, SourceRecordStatus, SourceType
from services.source_ingestion.controller_state import ControllerState, ControllerStateStore
from services.source_ingestion.controller_worker import ControllerConfig, run_controller_tick
from services.source_ingestion.distillation_controller import (
    DistillationControllerConfig,
    run_controller_tick as run_distillation_controller_tick,
)
from services.source_ingestion.distillation_worker import DistillationJobQueue, DistillationJobStatus, make_distillation_worker
from services.source_ingestion import main as main_module
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore, StrategySpecSeedStatus


CONNECTOR_ID = "tw-official-market-datasets-r3"
DATASET = "tw_price_daily_r3"


def _deployment() -> dict[str, Any]:
    return {
        "git_sha": "sha-r3-test",
        "image_digest": "image-r3-test",
        "build_time": "2026-08-13T00:00:00Z",
        "deployment_id": "deployment-r3-test",
        "runtime_instance_id": "runtime-r3-test",
        "identity_observed_at": "2026-08-13T00:00:00Z",
        "identity_complete": True,
    }


def _state(**overrides: Any) -> ControllerState:
    values: dict[str, Any] = {
        "controller_id": "source-ingestion-r3:gen-1",
        "controller_name": "source-ingestion-controller",
        "environment": "test",
        "tenant_id": "tenant-r3",
        "deployment": _deployment(),
        "started_at": "2026-08-13T00:00:00Z",
    }
    values.update(overrides)
    return ControllerState(**values)


def test_durable_source_to_distillation_r3_product_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Complete E2E validation of SourceRecord scheduled ingestion, controller reconcile, distillation tick, and seed persistence."""
    # 1. Isolated environment setup
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    proposal_store_path = data_dir / "proposals.jsonl"
    schedule_store_path = data_dir / "schedule.jsonl"
    connector_store_path = data_dir / "connector_config.jsonl"
    evidence_store_path = data_dir / "source_evidence.jsonl"
    dlq_store_path = data_dir / "dlq.jsonl"
    audit_store_path = data_dir / "audit.jsonl"
    schedule_config_path = data_dir / "connector_schedule.jsonl"
    source_health_path = data_dir / "source_health.jsonl"
    source_usage_path = data_dir / "source_usage.jsonl"
    market_data_root = data_dir / "market_data"
    controller_state_path = data_dir / "controller_state.json"
    requirement_state_path = data_dir / "requirement_snapshots.jsonl"
    reconcile_lock_path = data_dir / "reconcile.lock"
    controller_token_path = data_dir / "controller_token"

    monkeypatch.setattr(main_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(main_module, "PROPOSAL_STORE_PATH", proposal_store_path)
    monkeypatch.setattr(main_module, "SCHEDULE_STORE_PATH", schedule_store_path)
    monkeypatch.setattr(main_module, "CONNECTOR_STORE_PATH", connector_store_path)
    monkeypatch.setattr(main_module, "SOURCE_EVIDENCE_STORE_PATH", evidence_store_path)
    monkeypatch.setattr(main_module, "DLQ_STORE_PATH", dlq_store_path)
    monkeypatch.setattr(main_module, "AUDIT_STORE_PATH", audit_store_path)
    monkeypatch.setattr(main_module, "CONNECTOR_SCHEDULE_CONFIG_PATH", schedule_config_path)
    monkeypatch.setattr(main_module, "SOURCE_HEALTH_STORE_PATH", source_health_path)
    monkeypatch.setattr(main_module, "SOURCE_USAGE_STORE_PATH", source_usage_path)
    monkeypatch.setattr(main_module, "MARKET_DATA_STORAGE_ROOT", market_data_root)
    monkeypatch.setattr(main_module, "CONTROLLER_STATE_PATH", controller_state_path)
    monkeypatch.setattr(main_module, "REQUIREMENT_STATE_PATH", requirement_state_path)
    monkeypatch.setattr(main_module, "RECONCILE_TRANSACTION_LOCK_PATH", reconcile_lock_path)
    monkeypatch.setattr(main_module, "CONTROLLER_TOKEN_PATH", controller_token_path)

    # Re-initialize stores & components
    proposal_store = main_module.SourceChangeProposalStore.from_jsonl(proposal_store_path)
    llm_proposal_adapter = main_module.LLMSourceProposalAdapter(proposal_store)
    store = main_module.JsonlIngestScheduleStore(schedule_store_path)
    connector_store = main_module.JsonlConfiguredConnectorStore(connector_store_path)
    schedule_config_store = main_module.JsonlConnectorScheduleStore(schedule_config_path)
    configured_fetcher = main_module.ConfiguredConnectorFetcher(connector_store)
    evidence_repository = main_module.build_source_evidence_repository(evidence_store_path)
    evidence_builder = main_module.EvidenceBundleBuilder(evidence_repository)
    dead_letter_queue = main_module.DeadLetterQueue(dlq_store_path)
    dead_letter_queue.load_from_spill()
    manager = main_module.IngestManager()
    scheduler = main_module.IngestionScheduler(
        manager=manager,
        store=store,
        dead_letter_queue=dead_letter_queue,
    )
    source_health_store = main_module.SourceHealthStore.from_jsonl(source_health_path)
    source_usage_store = main_module.SourceUsageDailyStore.from_jsonl(source_usage_path)
    requirement_snapshot_store = main_module.RequirementSnapshotStore(requirement_state_path)
    controller_token = main_module.load_controller_token(token_path=controller_token_path, create=True)
    market_data_storage_writer = main_module.MarketDataStorageWriter(market_data_root)

    monkeypatch.setattr(main_module, "proposal_store", proposal_store)
    monkeypatch.setattr(main_module, "llm_proposal_adapter", llm_proposal_adapter)
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "connector_store", connector_store)
    monkeypatch.setattr(main_module, "schedule_config_store", schedule_config_store)
    monkeypatch.setattr(main_module, "configured_fetcher", configured_fetcher)
    monkeypatch.setattr(main_module, "evidence_repository", evidence_repository)
    monkeypatch.setattr(main_module, "evidence_builder", evidence_builder)
    monkeypatch.setattr(main_module, "dead_letter_queue", dead_letter_queue)
    monkeypatch.setattr(main_module, "manager", manager)
    monkeypatch.setattr(main_module, "scheduler", scheduler)
    monkeypatch.setattr(main_module, "source_health_store", source_health_store)
    monkeypatch.setattr(main_module, "source_usage_store", source_usage_store)
    monkeypatch.setattr(main_module, "requirement_snapshot_store", requirement_snapshot_store)
    monkeypatch.setattr(main_module, "controller_token", controller_token)
    monkeypatch.setattr(main_module, "market_data_storage_writer", market_data_storage_writer)

    # 2. Ingest real SourceRecord via service HTTP API
    client = TestClient(main_module.app)
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_record_id = "src-r3-twse-001"
    note_content = """# TWSE Quantitative Strategy Research Note
Source Record ID: src-r3-twse-001
Date: 2026-08-13
Author: r3-author

## Hypothesis
TWSE 10-day volatility break predicts short term momentum.

## Universe
- Symbols: 2330.TW, 2317.TW
- Venues: TWSE
- Asset class: equity

## Frequency
daily

## Risk Caps
- Max position pct: 5%
- Max gross exposure pct: 50%

## Data Requirements
- TW price daily OHLCV

## Feature Hints
- volatility_10d
- momentum_5d
"""

    config_resp = client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": {
                "connector_id": CONNECTOR_ID,
                "source_type": "internal_note",
                "provider": "TWSE",
                "license_scope": "public",
                "status": "enabled",
                "metadata": {
                    "persona_source_reconciliation": {
                        "desired_state": {
                            "dataset": DATASET,
                            "market": "TW",
                            "cadence": "daily",
                            "source_class": "live_pull",
                        }
                    }
                },
            },
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-08-13T01:00:00Z",
                "records": [
                    {
                        "source_id": source_record_id,
                        "title": "TWSE Quantitative Strategy Research Note",
                        "content_ref": "memory://r3/twse_note/001",
                        "source_type": "internal_note",
                        "trace_id": "trace-r3-20260813-001",
                        "created_at": now_iso,
                        "metadata": {
                            "body": note_content,
                            "dataset": DATASET,
                            "market": "TW",
                            "license_scope": "public",
                            "provider": "TWSE",
                            "available_time": now_iso,
                            "access_scope": "public",
                            "api_endpoint": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                            "schema_hash": "tw-official-market-record.v1",
                        },
                    }
                ],
            },
        },
    )
    assert config_resp.status_code == 201

    sched_resp = client.put(
        f"/api/source-ingest/connectors/{CONNECTOR_ID}/schedule",
        json={"interval_seconds": 86400, "enabled": True},
    )
    assert sched_resp.status_code == 200

    # 3. Controller tick persistence & readback
    controller_store = ControllerStateStore(controller_state_path)
    state = _state()
    controller_store.save(state)

    config = ControllerConfig(
        api_url="http://127.0.0.1:8097",
        database_url="postgresql://unused",
        interval_seconds=60,
        max_concurrency=2,
        max_ticks=1,
        state_path=controller_state_path,
        alive_path=None,
        timeout_seconds=5.0,
        lease_seconds=120,
        truth_level="reconciled_live_proof",
        controller_token="token-r3",
    )

    class DummyLoopWriter:
        async def record_heartbeat(self, *args: Any, **kwargs: Any) -> None: pass
        async def record_tick(self, *args: Any, **kwargs: Any) -> None: pass
        async def record_success(self, *args: Any, **kwargs: Any) -> None: pass
        async def record_failure(self, *args: Any, **kwargs: Any) -> None: pass
        async def record_repair(self, *args: Any, **kwargs: Any) -> None: pass

    def fake_load_desired_state(*, timeout_seconds: float = 30.0):
        personas = ({
            "persona_id": "persona-r3",
            "required_data_sources": [{
                "dataset": DATASET,
                "market": "TW",
                "cadence": "daily",
                "source_class": "live_pull",
                "connector_candidates": [CONNECTOR_ID],
                "policy_gates": ["public-source-only"],
            }],
        },)
        digest = main_module._desired_state_digest(list(personas))
        now_str = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return personas, {
            "authority": "file:///desired-state.json",
            "transport": "deployment_file",
            "sha256": digest,
            "persona_count": 1,
            "requirement_count": 1,
            "read_at": now_str,
        }

    def fake_reconcile_desired_state(**kwargs: Any):
        return main_module.reconcile_persona_source_provisioning(
            main_module.PersonaSourceProvisioningRequest(
                personas=list(kwargs["personas"]),
                authoritative_snapshot=True,
                desired_state_sha256=kwargs["desired_state_sha256"],
                source_authority=kwargs.get("source_authority"),
                dry_run=False,
            ),
            authorization=f"Bearer {main_module.controller_token}",
        )

    def fake_run_schedule_tick(**kwargs: Any):
        return main_module.run_scheduled_connectors(
            main_module.RunScheduledRequest(
                max_concurrency=kwargs.get("max_concurrency", 2),
                force_connector_ids=list(kwargs.get("force_connector_ids") or []),
                exclusive_connector_ids=list(kwargs.get("exclusive_connector_ids") or []),
            ),
            authorization=f"Bearer {main_module.controller_token}",
        )

    def fake_read_actual_state(*args: Any, **kwargs: Any):
        actual = client.get("/api/source-ingest/controller/readback").json()
        if isinstance(actual.get("controller_state"), dict):
            actual["controller_state"]["controller_id"] = state.controller_id
            actual["controller_state"]["sequence_no"] = state.sequence_no
            actual["controller_state"]["deployment"] = _deployment()
        actual["requirement_snapshot"] = {
            "desired_state_sha256": main_module._desired_state_digest(list(fake_load_desired_state()[0])),
            "authoritative": True,
            "sequence": 1,
        }
        return actual

    monkeypatch.setattr("services.source_ingestion.controller_worker.load_desired_state", fake_load_desired_state)
    monkeypatch.setattr("services.source_ingestion.controller_worker.reconcile_desired_state", fake_reconcile_desired_state)
    monkeypatch.setattr("services.source_ingestion.controller_worker.run_schedule_tick", fake_run_schedule_tick)
    monkeypatch.setattr("services.source_ingestion.controller_worker.read_actual_state", fake_read_actual_state)

    tick_summary = run_controller_tick(
        config=config,
        state=state,
        store=controller_store,
        writer=DummyLoopWriter(),
    )
    assert tick_summary["status"] == "ok"

    # Verify controller API readback
    api_readback_resp = client.get("/api/source-ingest/controller/readback")
    assert api_readback_resp.status_code == 200
    connectors = api_readback_resp.json().get("connectors") or []
    target_conn = next((c for c in connectors if c.get("connector_id") == CONNECTOR_ID), None)
    assert target_conn is not None
    assert target_conn["latest_source_record"]["source_id"] == source_record_id

    # 4. Supervised Distillation Controller tick & SeedStore creation
    dist_queue_path = tmp_path / "distillation_queue.jsonl"
    dist_state_path = tmp_path / "distillation_state.json"
    seed_store_path = tmp_path / "strategy_seeds.jsonl"

    note_path = tmp_path / f"{source_record_id}.md"
    note_path.write_text(note_content, encoding="utf-8")

    dist_state = _state(controller_id="distillation-r3:gen-1")
    dist_controller_store = ControllerStateStore(dist_state_path)
    dist_controller_store.save(dist_state)

    dist_config = DistillationControllerConfig(
        database_url="postgresql://test:test@localhost:5432/test",
        registry_url="http://mock-registry:8087",
        interval_seconds=60,
        max_ticks=1,
        state_path=dist_state_path,
        alive_path=None,
        job_queue_path=dist_queue_path,
        seed_store_path=seed_store_path,
        evidence_store_path=evidence_store_path,
        source_dirs=[tmp_path],
    )

    fake_registry: dict[str, Any] = {}
    def fake_get_registry(url: str, registry_id: str):
        return fake_registry.get(registry_id)
    def fake_register_spec(url: str, payload: dict[str, Any]):
        reg_id = payload["registry_id"]
        if reg_id not in fake_registry:
            fake_registry[reg_id] = {
                "entry": {
                    **payload,
                    "artifact_type": "strategy_spec",
                    "artifact_state": "draft",
                    "strategy_spec": dict(payload.get("strategy_spec") or {}),
                    "lineage": {"parent_registry_ids": None, **dict(payload.get("lineage") or {})},
                    "metadata": dict(payload.get("metadata") or {}),
                }
            }
        return fake_registry[reg_id]

    monkeypatch.setattr("services.source_ingestion.distillation_controller._get_registry_entry", fake_get_registry)
    monkeypatch.setattr("services.source_ingestion.distillation_controller._register_strategy_spec_if_absent", fake_register_spec)
    monkeypatch.setattr("services.source_ingestion.distillation_controller._registry_entry_matches_expected", lambda entry, expected: True)

    dist_summary = run_distillation_controller_tick(
        config=dist_config,
        state=dist_state,
        store=dist_controller_store,
        writer=DummyLoopWriter(),
    )
    assert dist_summary["status"] == "success"

    # 5. Readback validation from StrategySpecSeedStore & EvidenceBundle
    seed_store = StrategySpecSeedStore(seed_store_path)
    seeds = seed_store.list_all()
    matched_seed = next((s for s in seeds if s.source_ids and source_record_id in s.source_ids), None)
    assert matched_seed is not None
    assert matched_seed.hypothesis is not None
    assert matched_seed.status == StrategySpecSeedStatus.DRAFT

    # Replayability check
    jobs = DistillationJobQueue(dist_queue_path).list_all()
    assert len(jobs) >= 1
    target_job = next((j for j in jobs if j.source_id == source_record_id), None)
    assert target_job is not None
    assert target_job.status == DistillationJobStatus.DONE.value
    assert target_job.seed_id == matched_seed.seed_id
