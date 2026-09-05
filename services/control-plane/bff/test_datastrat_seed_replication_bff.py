from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.research.store import ResearchOrchestratorStore
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


OPERATOR_HEADERS = {"Authorization": "Bearer seed-op:operator"}
VIEWER_HEADERS = {"Authorization": "Bearer seed-viewer:viewer"}
SEED_ID = "seed-bff-replication-alpha"


def _seed(status: StrategySpecSeedStatus | str) -> StrategySpecSeed:
    return StrategySpecSeed(
        seed_id=SEED_ID,
        source_id="src-bff-alpha",
        evidence_bundle_id="bundle-bff-alpha",
        hypothesis="TWSE momentum features can rank five-day forward returns.",
        asset_class=["equity"],
        market_scope=["TWSE"],
        holding_period="5 trading days",
        required_data=["point-in-time daily OHLCV", "adjusted close"],
        backend_hint="qlib",
        feature_hints=["momentum", "volatility"],
        label_hints=["5_day_forward_return"],
        risk_notes=["survivorship bias check"],
        confidence=0.9,
        status=status,
        source_ids=["src-bff-alpha"],
        evidence_item_ids=["evi-bff-alpha"],
        citation_refs=["bff-alpha#abstract"],
        trace_refs=["trace-bff-alpha"],
        created_at="2026-06-12T00:00:00Z",
        lineage={
            "created_from": "evidence_bundle",
            "evidence_bundle_id": "bundle-bff-alpha",
            "source_ids": ["src-bff-alpha"],
            "evidence_item_ids": ["evi-bff-alpha"],
            "citation_refs": ["bff-alpha#abstract"],
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


@contextmanager
def _client_with_seed(status: StrategySpecSeedStatus | str):
    tracked_env = {
        "STRATEGY_SEED_STORE_PATH": os.environ.get("STRATEGY_SEED_STORE_PATH"),
        "RESEARCH_ORCHESTRATOR_DATA_DIR": os.environ.get("RESEARCH_ORCHESTRATOR_DATA_DIR"),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        seed_store_path = root / "strategy_seeds.jsonl"
        research_dir = root / "research-orchestrator"
        os.environ["STRATEGY_SEED_STORE_PATH"] = str(seed_store_path)
        os.environ["RESEARCH_ORCHESTRATOR_DATA_DIR"] = str(research_dir)
        StrategySpecSeedStore(path=seed_store_path).save(_seed(status))
        bff_main._STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY.clear()
        client = TestClient(bff_main.app)
        try:
            yield client, seed_store_path, research_dir
        finally:
            bff_main._STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY.clear()
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _error(response_json: dict) -> dict:
    return (response_json.get("detail") or response_json).get("error", {})


def test_bff_submit_replication_requires_operator_and_returns_ref() -> None:
    with _client_with_seed(StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC) as (
        client,
        seed_store_path,
        research_dir,
    ):
        response = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/submit-replication",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-repl-bff-001"},
        )

        assert response.status_code == 202, response.text
        body = response.json()
        data = body["data"]
        assert data["seed_id"] == SEED_ID
        assert data["replication_ref"].startswith("research-orchestrator://experiment-tasks/")
        assert data["experiment_task_id"]
        assert data["registry_write_performed"] is False
        assert data["execution_route"] == "none"
        assert data["deployment_authority"] == "none"
        assert data["approved_artifact_created"] is False
        assert data["deployment_plan_created"] is False
        assert data["runtime_binding_created"] is False

        research_task = ResearchOrchestratorStore(research_dir).get_task(data["experiment_task_id"])
        assert research_task is not None
        assert research_task["experiment_task"]["metadata"]["source_seed_id"] == SEED_ID
        stored_seed = StrategySpecSeedStore(path=seed_store_path).get(SEED_ID)
        assert stored_seed is not None
        assert stored_seed.lineage["replication_ref"] == data["replication_ref"]

        replay = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/submit-replication",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-repl-bff-001"},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["data"]["replication_ref"] == data["replication_ref"]
        assert replay.json()["meta"]["idempotency"]["replayed"] is True


def test_bff_submit_replication_rejects_read_role() -> None:
    with _client_with_seed(StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC) as (
        client,
        seed_store_path,
        _research_dir,
    ):
        response = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/submit-replication",
            headers={**VIEWER_HEADERS, "Idempotency-Key": "seed-repl-viewer"},
        )

        assert response.status_code == 403, response.text
        assert _error(response.json())["details"]["precondition_failed"] == "role_check"
        stored_seed = StrategySpecSeedStore(path=seed_store_path).get(SEED_ID)
        assert stored_seed is not None
        assert stored_seed.lineage.get("replication_ref") is None


def test_bff_submit_replication_refuses_unpromoted_seed() -> None:
    with _client_with_seed(StrategySpecSeedStatus.DRAFT) as (client, _seed_store_path, research_dir):
        response = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/submit-replication",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-repl-draft"},
        )

        assert response.status_code == 409, response.text
        assert _error(response.json())["details"]["precondition_failed"] == "status"
        assert ResearchOrchestratorStore(research_dir).list_tasks() == []
