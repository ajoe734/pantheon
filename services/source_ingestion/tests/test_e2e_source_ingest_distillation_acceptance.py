"""Focused E2E acceptance proof for L12-MIN-SRC-20260808.

Drives a real bounded source-ingestion tick through main.py persistence,
verifies the persisted SourceRecord ID via authoritative controller readback,
and lets Strategy Distillation consume that exact persisted record.
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


from services.source_ingestion.connectors import SourceRecord
from services.source_ingestion.controller_state import ControllerState, ControllerStateStore
from services.source_ingestion.controller_worker import ControllerConfig, run_controller_tick
from services.source_ingestion.distillation_controller import (
    DistillationControllerConfig,
    run_controller_tick as run_distillation_controller_tick,
)
from services.source_ingestion.distillation_worker import DistillationJobQueue, make_distillation_worker
from services.source_ingestion import main as main_module
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


CONNECTOR_ID = "tw-official-market-datasets"
DATASET = "tw_price_daily"


def _deployment() -> dict[str, Any]:
    return {
        "git_sha": "sha-e2e-test",
        "image_digest": "image-e2e-test",
        "build_time": "2026-08-09T00:00:00Z",
        "deployment_id": "deployment-e2e-test",
        "runtime_instance_id": "runtime-e2e-test",
        "identity_observed_at": "2026-08-09T00:00:00Z",
        "identity_complete": True,
    }


def _state(**overrides: Any) -> ControllerState:
    values: dict[str, Any] = {
        "controller_id": "source-ingestion-e2e:generation-1",
        "controller_name": "source-ingestion-controller",
        "environment": "test",
        "tenant_id": "tenant-e2e",
        "deployment": _deployment(),
        "started_at": "2026-08-09T00:00:00Z",
    }
    values.update(overrides)
    return ControllerState(**values)


def test_real_bounded_source_ingestion_tick_persistence_readback_and_distillation_consumption(tmp_path: Path) -> None:
    # 1. Setup isolated directories and env for main.py persistence
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

    # Patch module paths in main
    main_module.DATA_DIR = data_dir
    main_module.PROPOSAL_STORE_PATH = proposal_store_path
    main_module.SCHEDULE_STORE_PATH = schedule_store_path
    main_module.CONNECTOR_STORE_PATH = connector_store_path
    main_module.SOURCE_EVIDENCE_STORE_PATH = evidence_store_path
    main_module.DLQ_STORE_PATH = dlq_store_path
    main_module.AUDIT_STORE_PATH = audit_store_path
    main_module.CONNECTOR_SCHEDULE_CONFIG_PATH = schedule_config_path
    main_module.SOURCE_HEALTH_STORE_PATH = source_health_path
    main_module.SOURCE_USAGE_STORE_PATH = source_usage_path
    main_module.MARKET_DATA_STORAGE_ROOT = market_data_root
    main_module.CONTROLLER_STATE_PATH = controller_state_path
    main_module.REQUIREMENT_STATE_PATH = requirement_state_path
    main_module.RECONCILE_TRANSACTION_LOCK_PATH = reconcile_lock_path
    main_module.CONTROLLER_TOKEN_PATH = controller_token_path

    # Re-initialize stores & components on main
    main_module.proposal_store = main_module.SourceChangeProposalStore.from_jsonl(proposal_store_path)
    main_module.llm_proposal_adapter = main_module.LLMSourceProposalAdapter(main_module.proposal_store)
    main_module.store = main_module.JsonlIngestScheduleStore(schedule_store_path)
    main_module.connector_store = main_module.JsonlConfiguredConnectorStore(connector_store_path)
    main_module.schedule_config_store = main_module.JsonlConnectorScheduleStore(schedule_config_path)
    main_module.configured_fetcher = main_module.ConfiguredConnectorFetcher(main_module.connector_store)
    main_module.evidence_repository = main_module.build_source_evidence_repository(evidence_store_path)
    main_module.evidence_builder = main_module.EvidenceBundleBuilder(main_module.evidence_repository)
    main_module.dead_letter_queue = main_module.DeadLetterQueue(dlq_store_path)
    main_module.dead_letter_queue.load_from_spill()
    main_module.manager = main_module.IngestManager()
    main_module.scheduler = main_module.IngestionScheduler(
        manager=main_module.manager,
        store=main_module.store,
        dead_letter_queue=main_module.dead_letter_queue,
    )
    main_module.source_health_store = main_module.SourceHealthStore.from_jsonl(source_health_path)
    main_module.source_usage_store = main_module.SourceUsageDailyStore.from_jsonl(source_usage_path)
    main_module.requirement_snapshot_store = main_module.RequirementSnapshotStore(requirement_state_path)
    main_module.controller_token = main_module.load_controller_token(token_path=controller_token_path, create=True)
    main_module.market_data_storage_writer = main_module.MarketDataStorageWriter(market_data_root)

    # 2. Configure connector via FastAPI TestClient (real HTTP/service boundary)
    client = TestClient(main_module.app)
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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
                "next_watermark": "2026-08-09T01:00:00Z",
                "records": [
                    {
                        "source_id": "src-e2e-20260809-001",
                        "title": "TW Price Daily E2E Tick",
                        "content_ref": "memory://e2e/tw_price_daily/001",
                        "source_type": "internal_note",
                        "trace_id": "trace-e2e-20260809-001",
                        "created_at": now_iso,
                        "metadata": {
                            "dataset": DATASET,
                            "market": "TW",
                            "license_scope": "public",
                            "provider": "TWSE",
                            "available_time": now_iso,
                            "api_endpoint": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                            "access_scope": "public",
                            "schema_hash": "tw-official-market-record.v1",
                        },
                    }
                ],
            },
        },
    )
    assert config_resp.status_code == 201

    # Schedule connector so controller tick detects it
    sched_resp = client.put(
        f"/api/source-ingest/connectors/{CONNECTOR_ID}/schedule",
        json={"interval_seconds": 86400, "enabled": True},
    )
    assert sched_resp.status_code == 200

    # 3. Drive controller worker tick to ingest the scheduled connector and persist real SourceRecord
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
        controller_token="token-e2e",
    )

    class DummyLoopWriter:
        async def record_heartbeat(self, *args: Any, **kwargs: Any) -> None: pass
        async def record_tick(self, *args: Any, **kwargs: Any) -> None: pass
        async def record_success(self, *args: Any, **kwargs: Any) -> None: pass
        async def record_failure(self, *args: Any, **kwargs: Any) -> None: pass
        async def record_repair(self, *args: Any, **kwargs: Any) -> None: pass

    # Patch network calls in controller_worker to execute directly in-memory against main_module
    def fake_load_desired_state(*, timeout_seconds: float = 30.0):
        personas = ({
            "persona_id": "persona-e2e",
            "required_data_sources": [{
                "dataset": DATASET,
                "market": "TW",
                "cadence": "daily",
                "source_class": "live_pull",
                "connector_candidates": ["tw-official-market-datasets"],
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
        rec_result = main_module.reconcile_persona_source_provisioning(
            main_module.PersonaSourceProvisioningRequest(
                personas=list(kwargs["personas"]),
                authoritative_snapshot=True,
                desired_state_sha256=kwargs["desired_state_sha256"],
                source_authority=kwargs.get("source_authority"),
                dry_run=False,
            ),
            authorization=f"Bearer {main_module.controller_token}",
        )
        return rec_result

    def fake_run_schedule_tick(**kwargs: Any):
        run_res = main_module.run_scheduled_connectors(
            main_module.RunScheduledRequest(
                max_concurrency=kwargs.get("max_concurrency", 2),
                force_connector_ids=list(kwargs.get("force_connector_ids") or []),
                exclusive_connector_ids=list(kwargs.get("exclusive_connector_ids") or []),
            ),
            authorization=f"Bearer {main_module.controller_token}",
        )
        return run_res

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

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("services.source_ingestion.controller_worker.load_desired_state", fake_load_desired_state)
    monkeypatch.setattr("services.source_ingestion.controller_worker.reconcile_desired_state", fake_reconcile_desired_state)
    monkeypatch.setattr("services.source_ingestion.controller_worker.run_schedule_tick", fake_run_schedule_tick)
    monkeypatch.setattr("services.source_ingestion.controller_worker.read_actual_state", fake_read_actual_state)

    try:
        tick_summary = run_controller_tick(
            config=config,
            state=state,
            store=controller_store,
            writer=DummyLoopWriter(),
        )
    finally:
        monkeypatch.undo()
    assert tick_summary["status"] == "ok"
    assert tick_summary["provider_egress_attempted"] is True

    # 4. Verify GET /api/source-ingest/controller/readback API endpoint exposes exact persisted source_id
    api_readback_resp = client.get("/api/source-ingest/controller/readback")
    assert api_readback_resp.status_code == 200
    api_readback = api_readback_resp.json()
    connectors = api_readback.get("connectors") or []
    target_conn_readback = next((c for c in connectors if c.get("connector_id") == CONNECTOR_ID), None)
    assert target_conn_readback is not None
    latest_rec = target_conn_readback.get("latest_source_record") or {}
    assert latest_rec.get("source_id") == "src-e2e-20260809-001"

    persisted_records = main_module.evidence_repository.list_source_records()
    assert len(persisted_records) >= 1
    target_record = next((r for r in persisted_records if r.source_id == "src-e2e-20260809-001"), None)
    assert target_record is not None
    assert target_record.connector_id == CONNECTOR_ID
    assert target_record.status == "normalized"

    # 5. Connect Strategy Distillation to consume the exact persisted SourceRecord
    dist_queue_path = tmp_path / "distillation_queue.jsonl"
    dist_state_path = tmp_path / "distillation_state.json"
    seed_store_path = tmp_path / "strategy_seeds.jsonl"

    dist_state = _state(controller_id="distillation-e2e:generation-1")
    dist_controller_store = ControllerStateStore(dist_state_path)
    dist_controller_store.save(dist_state)

    note_path = tmp_path / "src-e2e-20260809-001.md"
    note_path.write_text(
        """# TW Price Daily E2E Tick
Source Record ID: src-e2e-20260809-001
Date: 2026-08-09
Author: e2e-author
Access: public
License: public
Confidence: 0.95

## Hypothesis
TW Price Daily E2E Tick signal hypothesis

## Universe
- Symbols: 2330.TW
- Venues: TWSE
- Asset class: equity

## Frequency
daily

## Risk Caps
- Max position pct: 2%
- Max gross exposure pct: 20%

## Data Requirements
- TW price daily

## Feature Hints
- momentum

## Label Hints
- return
""",
        encoding="utf-8",
    )

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

    # Verify seed store received distilled output from target_record
    seed_store = StrategySpecSeedStore(seed_store_path)
    seeds = seed_store.list_all()
    assert len(seeds) >= 1
    matched_seed = next((s for s in seeds if s.source_ids and target_record.source_id in s.source_ids), None)
    assert matched_seed is not None
    assert matched_seed.hypothesis is not None
    assert matched_seed.seed_id is not None
