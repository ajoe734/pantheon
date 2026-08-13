"""Product V2 Research Loop Closure R2 Acceptance Test.

Verifies the shortest actual product path from a normalized SourceRecord,
through distillation readback and StrategySpecSeed promotion, to research
replication task enqueueing and alpha replication worker revalidation.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from services.source_ingestion.connectors.base import (
    SourceRecord,
    SourceRecordStatus,
    SourceType,
)
from services.source_ingestion.distillation_worker import (
    DistillationJobQueue,
    DistillationJobStatus,
    make_distillation_worker,
)
from services.source_ingestion.replication_bridge import StrategySeedReplicationBridge
from services.source_ingestion.strategy_seed_builder import StrategySpecSeedStatus
from services.source_ingestion.strategy_seed_store import (
    SeedReviewDecisionAction,
    StrategySpecSeedStore,
)
from services.research.alpha_replication.queue import AlphaReplicationQueue
from services.research.alpha_replication.revalidation_worker import AlphaRevalidationWorker
from services.research.experiment_orchestrator.authority import (
    AuthoritativeRunReceipt,
    AuthoritativeTaskReceipt,
    ExperimentAuthority,
)
from services.research.store import ResearchOrchestratorStore


class FakeAuthority:
    def __init__(self) -> None:
        self.tasks: dict[str, Any] = {}
        self.runs: dict[str, Any] = {}

    def ensure_task(
        self,
        task: Any,
        *,
        approval_decision_id: str,
        approver: str,
        approved_at: str,
        checksum: str,
    ) -> AuthoritativeTaskReceipt:
        existing = self.tasks.setdefault(task.idempotency_key, task)
        return AuthoritativeTaskReceipt(
            authority_task_id=f"rtask:{existing.task_id}",
            task=existing,
            record={
                "approval_decision_id": approval_decision_id,
                "approver": approver,
                "approved_at": approved_at,
                "checksum": checksum,
            },
        )

    def ensure_run(
        self,
        authority_task_id: str,
        run: Any,
        *,
        approval_decision_id: str,
    ) -> AuthoritativeRunReceipt:
        key = str(run.metadata["idempotency_key"])
        existing = self.runs.setdefault(key, run)
        return AuthoritativeRunReceipt(
            authority_run_id=f"rrun:{existing.run_id}",
            run=existing,
            record={
                "task_id": authority_task_id,
                "approval_decision_id": approval_decision_id,
                "production_activation": "disabled",
            },
        )

    def list_runs(
        self,
        *,
        tenant_id: str | None = None,
        strategy_spec_id: str | None = None,
    ) -> list[Any]:
        return list(self.runs.values())


def test_source_record_distillation_to_alpha_replication_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E proof: SourceRecord -> Distillation -> Seed Promotion -> Replication Bridge -> Alpha Replication."""
    # 1. Setup isolated directories
    ingest_dir = tmp_path / "ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)
    queue_path = ingest_dir / "distillation_queue.sqlite3"
    seed_store_path = ingest_dir / "seeds.jsonl"
    research_dir = tmp_path / "research-orchestrator"
    alpha_dir = tmp_path / "alpha-replication"
    alpha_dir.mkdir(parents=True, exist_ok=True)

    # Stores & Worker initialization
    seed_store = StrategySpecSeedStore(path=seed_store_path)
    research_store = ResearchOrchestratorStore(data_dir=research_dir)
    worker = make_distillation_worker(
        queue_path=queue_path,
        seed_store_path=seed_store_path,
        created_by="product-v2-worker",
    )

    # 2. Ingest real SourceRecord (internal research note)
    source_id = "src-prod-v2-r2-001"
    note_markdown = """# TWSE Cross-Sectional Momentum Strategy
## Strategy
- Hypothesis: 20-day price momentum predicts 5-day forward equity returns.
- Symbols: 2330.TW, 2317.TW, 2454.TW
- Market: TWSE
- Asset Class: Equity
- Frequency: daily
- Holding Period: 5 trading days
- Required Data: point-in-time daily OHLCV
- Backend Hint: qlib

## Risk Limits
- Max Position Size: 0.10
- Max Drawdown: 0.15
- Leverage Cap: 1.0

## Feature Hints
- momentum_20d
- volatility_30d

## Metrics
- sharpe_ratio
- annual_return
"""

    source = SourceRecord(
        source_id=source_id,
        connector_id="internal-research-notes",
        source_type=SourceType.INTERNAL_NOTE,
        title="TWSE Cross-Sectional Momentum Strategy",
        content_ref="docs/research/notes/twse_momentum.md",
        status=SourceRecordStatus.NORMALIZED,
        created_at="2026-08-13T00:00:00Z",
        metadata={
            "body": note_markdown,
            "tenant_id": "tenant-prod-v2",
            "access_scope": ["research"],
            "source_status": "active",
        },
    )

    # 3. Distillation Process & Readback
    job = worker.enqueue_from_source_record(source)
    assert job.source_id == source_id

    run_result = worker.run_pending()
    assert run_result.processed == 1
    assert run_result.created == 1
    assert run_result.failed == 0

    # Distillation readback
    jobs = DistillationJobQueue(queue_path).list_all()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source_id == source_id
    assert job.status == DistillationJobStatus.DONE.value
    assert job.seed_id is not None

    seed = seed_store.get(job.seed_id)
    assert seed is not None
    assert seed.source_id == source_id
    assert seed.status == StrategySpecSeedStatus.DRAFT

    # 4. Governed Seed Review & Promotion
    # Transition: DRAFT -> ACCEPTED
    seed, decision1 = seed_store.record_review_decision(
        seed.seed_id,
        decision=SeedReviewDecisionAction.ACCEPT,
        reviewer_id="lead-researcher",
        reason="Validated hypothesis and data requirements.",
    )
    assert seed.status == StrategySpecSeedStatus.ACCEPTED

    # Transition: ACCEPTED -> PROMOTED_TO_STRATEGY_SPEC
    promoted_seed, decision2 = seed_store.record_review_decision(
        seed.seed_id,
        decision=SeedReviewDecisionAction.CONVERT_TO_SPEC_SEED,
        reviewer_id="lead-researcher",
        reason="Promoting to StrategySpec candidate for alpha replication.",
    )
    assert promoted_seed.status == StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC

    # 5. Submit seed to Replication Bridge
    bridge = StrategySeedReplicationBridge(
        seed_store=seed_store,
        research_store=research_store,
    )
    submission = bridge.submit_seed_to_replication(
        promoted_seed.seed_id,
        requested_by="strategy-review-board",
        idempotency_key=f"idem-repl-{promoted_seed.seed_id}",
    )

    assert submission.seed_id == promoted_seed.seed_id
    assert submission.replication_ref.startswith("research-orchestrator://experiment-tasks/")
    assert submission.experiment_task_id is not None

    # Verify task in ResearchOrchestratorStore
    queued_task = research_store.get_task(submission.experiment_task_id)
    assert queued_task is not None
    assert queued_task["status"] == "queued"
    exp_task = queued_task["experiment_task"]
    assert exp_task["metadata"]["source_seed_id"] == promoted_seed.seed_id

    # 6. Alpha Replication Queue & Revalidation Worker
    authority = FakeAuthority()
    alpha_queue = AlphaReplicationQueue(alpha_dir)

    # Enqueue StrategySpec payload into AlphaReplicationQueue
    strategy_spec_payload = exp_task["metadata"]["strategy_spec_candidate"]
    strategy_id = exp_task["strategy_id"]
    spec_version = exp_task["strategy_spec_version"]
    strategy_spec_id = f"spec-{strategy_id}-{spec_version}"

    queue_entry = {
        "tenant_id": "tenant-prod-v2",
        "strategy_spec_id": strategy_spec_id,
        "strategy_id": strategy_id,
        "spec_version": spec_version,
        "artifact_state": "approved",
        "checksum": "sha256:11223344556677889900aabbccddeeff",
        "approval_decision_id": "decision-rev-001",
        "approver": "lead-researcher",
        "approved_at": "2026-08-13T01:00:00Z",
        "strategy_spec": strategy_spec_payload,
    }
    enqueued_item = alpha_queue.enqueue(queue_entry, enqueued_by="bridge-test")
    assert enqueued_item is not None
    assert enqueued_item["status"] == "pending"

    # Run AlphaRevalidationWorker to execute replication pass
    reval_worker = AlphaRevalidationWorker(
        queue=alpha_queue,
        data_dir=alpha_dir,
        worker_id="revalidation-worker-01",
        authority=authority,
    )

    registry_entry = {
        "registry_id": strategy_spec_id,
        "artifact_type": "strategy_spec",
        "strategy_id": strategy_id,
        "version": spec_version,
        "artifact_state": "approved",
        "checksum": "sha256:11223344556677889900aabbccddeeff",
        "approval_decision_id": "decision-rev-001",
        "approver": "lead-researcher",
        "approved_at": "2026-08-13T01:00:00Z",
        "metadata": {
            "tenant_id": "tenant-prod-v2",
            "strategy_spec": strategy_spec_payload,
        },
    }

    class FakeGateResponse:
        def __init__(self, passed: bool = True, summary: str = "replication passed") -> None:
            self.passed = passed
            self.summary = summary

        def to_dict(self) -> dict[str, Any]:
            return {"passed": self.passed, "summary": self.summary}

    monkeypatch.setattr(reval_worker, "_fetch_strategy_spec_entry", lambda spec_id: registry_entry)
    from services.research.replication.gate import ReplicationGate
    monkeypatch.setattr(ReplicationGate, "evaluate_candidate", lambda self, task: FakeGateResponse(passed=True))

    tick_result = reval_worker.run_once(tenant_id="tenant-prod-v2")

    assert tick_result.get("errors") == [], f"tick_result errors: {tick_result.get('errors')}"
    assert tick_result["processed"] == 1
    assert len(tick_result["created_authority_task_ids"]) == 1
    assert len(tick_result["authority_receipts"]) == 1

    receipt = tick_result["authority_receipts"][0]
    assert receipt["authority_task_id"] is not None
    assert receipt["authority_run_id"] is not None

    # Readback from authority to prove product truth
    assert len(authority.tasks) == 1
    assert len(authority.runs) == 1

    metrics = alpha_queue.get_metrics()
    assert metrics["revalidated"] == 1
    assert metrics["pending"] == 0
