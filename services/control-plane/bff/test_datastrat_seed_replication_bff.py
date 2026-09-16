from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import (
    bff_error,
    bff_me_tenant_payload,
    extract_identity,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.models import utc_now as _utc_now
from services.control_plane.bff.strategies.router import create_strategies_router
from services.research.store import ResearchOrchestratorStore
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


def _stable_json_hash(payload: Dict[str, Any]) -> str:
    """Mirrors bff/main.py::_stable_json_hash (sha256 of canonical JSON)."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_final_idempotency_key(
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str],
) -> str:
    """Mirrors bff/main.py::_resolve_final_idempotency_key."""
    canonical = str(idempotency_key or "").strip()
    if canonical:
        return canonical
    alias = str(x_idempotency_key or "").strip()
    if alias:
        return alias
    raise bff_error(
        400,
        ErrorCode.VALIDATION_FAILED,
        "Idempotency-Key is required for operator commands",
        (
            "Final contract routes require a non-empty Idempotency-Key header; "
            "X-Idempotency-Key is accepted as a temporary compatibility alias"
        ),
        precondition_failed="idempotency_key",
        suggestion="Retry with Idempotency-Key set to a stable client retry key",
    )


def _reject_body_idempotency_key(payload: Dict[str, Any]) -> None:
    """Mirrors bff/main.py::_reject_body_idempotency_key."""
    body_key = "idempotencyKey" if "idempotencyKey" in payload else "idempotency_key" if "idempotency_key" in payload else None
    if body_key is not None:
        raise bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            f"{body_key} must not appear in the request body",
            (
                "Final contract routes require idempotency via the Idempotency-Key header, "
                "not the request body"
            ),
            precondition_failed="body_idempotency_key",
            suggestion=f"Remove {body_key} from the body and set the Idempotency-Key header",
        )


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(
        create_strategies_router(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=_utc_now,
            reject_body_idempotency_key=_reject_body_idempotency_key,
            resolve_final_idempotency_key=_resolve_final_idempotency_key,
            stable_json_hash=_stable_json_hash,
            bff_me_tenant_payload=bff_me_tenant_payload,
            strategy_seed_replication_idempotency_store={},
        )
    )
    return app


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
        client = TestClient(_build_app())
        try:
            yield client, seed_store_path, research_dir
        finally:
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
