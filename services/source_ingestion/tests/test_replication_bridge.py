"""Tests for StrategySpecSeed -> research replication bridge."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.research.experiments.models import ExperimentTask
from services.research.store import ResearchOrchestratorStore
from services.source_ingestion.replication_bridge import (
    StrategySeedReplicationBridge,
    StrategySeedReplicationBridgeError,
)
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


def _seed(status: StrategySpecSeedStatus | str) -> StrategySpecSeed:
    return StrategySpecSeed(
        seed_id="seed-replication-alpha",
        source_id="src-paper-alpha",
        evidence_bundle_id="bundle-replication-alpha",
        hypothesis="TWSE momentum features can rank five-day forward returns.",
        asset_class=["equity"],
        market_scope=["TWSE"],
        holding_period="5 trading days",
        required_data=["point-in-time daily OHLCV", "adjusted close"],
        backend_hint="qlib",
        feature_hints=["momentum", "volatility"],
        label_hints=["5_day_forward_return"],
        risk_notes=["survivorship bias check"],
        confidence=0.88,
        status=status,
        source_ids=["src-paper-alpha"],
        evidence_item_ids=["evi-alpha"],
        citation_refs=["alpha-paper#abstract"],
        trace_refs=["trace-alpha"],
        created_at="2026-06-12T00:00:00Z",
        lineage={
            "created_from": "evidence_bundle",
            "evidence_bundle_id": "bundle-replication-alpha",
            "source_ids": ["src-paper-alpha"],
            "evidence_item_ids": ["evi-alpha"],
            "citation_refs": ["alpha-paper#abstract"],
            "registry_write_performed": False,
            "execution_route": "none",
        },
        metadata={
            "source_license_scope": "open",
            "access_scope": ["research", "strategy_seed"],
            "source_status": "active",
            "execution_route": "none",
        },
    )


def _stores(tmp_path: Path) -> tuple[StrategySpecSeedStore, ResearchOrchestratorStore]:
    return (
        StrategySpecSeedStore(path=tmp_path / "seeds.jsonl"),
        ResearchOrchestratorStore(tmp_path / "research-orchestrator"),
    )


def test_promoted_seed_submits_experiment_task_and_writes_lineage(tmp_path: Path) -> None:
    seed_store, research_store = _stores(tmp_path)
    seed_store.save(_seed(StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC))

    bridge = StrategySeedReplicationBridge(seed_store=seed_store, research_store=research_store)
    submission = bridge.submit_seed_to_replication(
        "seed-replication-alpha",
        requested_by="op-seed-review",
        idempotency_key="idem-seed-replication-alpha",
        created_at="2026-06-12T01:02:03Z",
    )

    assert submission.replication_ref.startswith("research-orchestrator://experiment-tasks/")
    assert submission.experiment_task_id
    assert submission.research_task["status"] == "queued"

    task_record = research_store.get_task(submission.experiment_task_id)
    assert task_record is not None
    experiment_task = ExperimentTask.from_dict(task_record["experiment_task"])
    assert experiment_task.task_id == submission.experiment_task_id
    assert experiment_task.metadata["source_seed_id"] == "seed-replication-alpha"
    assert experiment_task.metadata["registry_write_performed"] is False
    assert experiment_task.metadata["execution_route"] == "none"
    assert experiment_task.metadata["approved_artifact_created"] is False
    assert experiment_task.metadata["deployment_plan_created"] is False
    assert experiment_task.metadata["runtime_binding_created"] is False

    stored_seed = seed_store.get("seed-replication-alpha")
    assert stored_seed is not None
    assert stored_seed.lineage["replication_ref"] == submission.replication_ref
    assert stored_seed.lineage["experiment_task_id"] == submission.experiment_task_id
    assert stored_seed.lineage["registry_write_performed"] is False
    assert stored_seed.lineage["execution_route"] == "none"


@pytest.mark.parametrize(
    "status",
    [StrategySpecSeedStatus.DRAFT, StrategySpecSeedStatus.REJECTED],
)
def test_wrong_status_seed_is_refused(tmp_path: Path, status: StrategySpecSeedStatus) -> None:
    seed_store, research_store = _stores(tmp_path)
    seed_store.save(_seed(status))
    bridge = StrategySeedReplicationBridge(seed_store=seed_store, research_store=research_store)

    with pytest.raises(StrategySeedReplicationBridgeError) as exc_info:
        bridge.submit_seed_to_replication(
            "seed-replication-alpha",
            requested_by="op-seed-review",
            idempotency_key="idem-refused",
            created_at="2026-06-12T01:02:03Z",
        )

    assert exc_info.value.code == "invalid_seed_status"
    assert research_store.list_tasks() == []
    assert seed_store.get("seed-replication-alpha").lineage.get("replication_ref") is None


def test_resubmitting_same_seed_returns_same_replication_ref(tmp_path: Path) -> None:
    seed_store, research_store = _stores(tmp_path)
    seed_store.save(_seed(StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC))
    bridge = StrategySeedReplicationBridge(seed_store=seed_store, research_store=research_store)

    first = bridge.submit_seed_to_replication(
        "seed-replication-alpha",
        requested_by="op-seed-review",
        idempotency_key="idem-first",
        created_at="2026-06-12T01:02:03Z",
    )
    second = bridge.submit_seed_to_replication(
        "seed-replication-alpha",
        requested_by="op-seed-review",
        idempotency_key="idem-second",
        created_at="2026-06-12T01:03:03Z",
    )

    assert second.idempotent_replay is True
    assert second.replication_ref == first.replication_ref
    assert second.experiment_task_id == first.experiment_task_id
    assert len(research_store.list_tasks()) == 1


def test_bridge_outputs_keep_no_execution_route_guard(tmp_path: Path) -> None:
    seed_store, research_store = _stores(tmp_path)
    seed_store.save(_seed(StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC))
    bridge = StrategySeedReplicationBridge(seed_store=seed_store, research_store=research_store)

    payload = bridge.submit_seed_to_replication(
        "seed-replication-alpha",
        requested_by="op-seed-review",
        idempotency_key="idem-no-exec",
        created_at="2026-06-12T01:02:03Z",
    ).to_dict()

    assert payload["registry_write_performed"] is False
    assert payload["execution_route"] == "none"
    assert payload["approved_artifact_created"] is False
    assert payload["deployment_plan_created"] is False
    assert payload["runtime_binding_created"] is False
    assert payload["experiment_task"]["metadata"]["registry_write_performed"] is False
    assert payload["experiment_task"]["metadata"]["execution_route"] == "none"
