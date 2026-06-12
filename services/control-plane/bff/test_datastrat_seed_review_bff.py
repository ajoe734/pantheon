from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


OPERATOR_HEADERS = {"Authorization": "Bearer seed-review-op:operator"}
REVIEWER_HEADERS = {"Authorization": "Bearer seed-reviewer:reviewer"}
SEED_ID = "seed-review-bff-alpha"
ARCHIVE_SEED_ID = "seed-review-bff-archive"
MERGE_SOURCE_ID = "seed-review-bff-merge-source"
MERGE_TARGET_ID = "seed-review-bff-merge-target"


def _seed(
    seed_id: str,
    *,
    status: StrategySpecSeedStatus | str = StrategySpecSeedStatus.DRAFT,
    confidence: float = 0.91,
) -> StrategySpecSeed:
    return StrategySpecSeed(
        seed_id=seed_id,
        source_id=f"src-{seed_id}",
        evidence_bundle_id=f"bundle-{seed_id}",
        hypothesis="TWSE equity momentum persists over swing holding periods.",
        asset_class=["equity"],
        market_scope=["TWSE"],
        holding_period="swing",
        required_data=["ohlcv", "return labels"],
        backend_hint="qlib",
        feature_hints=["momentum"],
        label_hints=["forward returns"],
        risk_notes=["survivorship bias check"],
        confidence=confidence,
        status=status,
        source_ids=[f"src-{seed_id}"],
        evidence_item_ids=[f"evi-{seed_id}"],
        citation_refs=[f"{seed_id}#abstract"],
        trace_refs=[f"trace-{seed_id}"],
        created_at="2026-06-12T00:00:00Z",
        lineage={
            "created_from": "evidence_bundle",
            "evidence_bundle_id": f"bundle-{seed_id}",
            "source_ids": [f"src-{seed_id}"],
            "evidence_item_ids": [f"evi-{seed_id}"],
            "citation_refs": [f"{seed_id}#abstract"],
            "registry_write_performed": False,
            "execution_route": "none",
        },
        metadata={
            "source_kind": "paper",
            "strategy_family": "momentum",
            "seed_kind": "strategy_spec_seed",
            "source_license_scope": "open",
            "access_scope": ["research", "strategy_seed"],
            "source_status": "active",
            "execution_route": "none",
            "data_backfill_available": False,
        },
    )


@contextmanager
def _review_client():
    tracked_env = {
        "STRATEGY_SEED_STORE_PATH": os.environ.get("STRATEGY_SEED_STORE_PATH"),
    }
    original_store = bff_main.read_store
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        seed_store_path = root / "strategy_seeds.jsonl"
        os.environ["STRATEGY_SEED_STORE_PATH"] = str(seed_store_path)
        store = StrategySpecSeedStore(path=seed_store_path)
        for seed_id in (SEED_ID, ARCHIVE_SEED_ID, MERGE_SOURCE_ID, MERGE_TARGET_ID):
            store.save(_seed(seed_id))

        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main.read_store.create_persona(
            persona_id="persona-seed-review-momentum",
            name="Seed Review Momentum Persona",
            actor_id="seed-review-op",
            archetype="momentum",
            lifecycle_state="research_only",
            risk_level="medium",
            metadata={
                "market_scope": ["TWSE"],
                "asset_classes": ["equity"],
                "holding_periods": ["swing"],
                "allowed_research_backends": ["qlib"],
                "allowed_source_scopes": ["open"],
                "data_availability_scope": ["ohlcv", "return labels"],
                "risk_level": "medium",
            },
        )
        bff_main._STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        client = TestClient(bff_main.app)
        try:
            yield client, seed_store_path
        finally:
            bff_main.read_store = original_store
            bff_main._STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY.clear()
            bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _error(response_json: dict) -> dict:
    return (response_json.get("detail") or response_json).get("error", {})


def test_strategy_seed_inbox_filters_and_surfaces_persona_suggestion() -> None:
    with _review_client() as (client, seed_store_path):
        response = client.get(
            "/bff/management/strategy-seeds",
            params={
                "status": "draft",
                "source_kind": "paper",
                "strategy_family": "momentum",
                "min_confidence": "0.8",
            },
            headers=REVIEWER_HEADERS,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        cards = body["data"]
        card = next(item for item in cards if item["seed_id"] == SEED_ID)
        assert card["source"]["source_kind"] == "paper"
        assert card["seed_kind"] == "strategy_spec_seed"
        assert card["hypothesis"]
        assert card["market"]["market_scope"] == ["TWSE"]
        assert card["required_data"] == ["ohlcv", "return labels"]
        assert card["evidence_count"] == 2
        assert "accept" in card["allowedActions"]
        assert card["recommended_action"]["type"] == "promote_seed_candidate"
        assert card["recommended_action"]["mode"] == "suggestion"
        assert card["recommended_action"]["auto_promote"] is False

        stored = StrategySpecSeedStore(path=seed_store_path).get(SEED_ID)
        assert stored is not None
        assert stored.status == StrategySpecSeedStatus.DRAFT


def test_strategy_seed_review_accept_convert_and_idempotent_replay() -> None:
    with _review_client() as (client, seed_store_path):
        accept = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/review",
            json={"action": "accept", "reason": "Enough governed evidence."},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-review-accept-bff"},
        )
        assert accept.status_code == 202, accept.text
        assert accept.json()["data"]["status"] == "accepted"
        assert accept.json()["data"]["decision"]["decision"] == "accept"

        replay = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/review",
            json={"action": "accept", "reason": "Enough governed evidence."},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-review-accept-bff"},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["meta"]["idempotency"]["replayed"] is True

        bff_main._STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY.clear()
        durable_replay = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/review",
            json={"action": "accept", "reason": "Enough governed evidence."},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-review-accept-bff"},
        )
        assert durable_replay.status_code == 202, durable_replay.text
        assert durable_replay.json()["data"]["status"] == "accepted"
        assert durable_replay.json()["meta"]["idempotency"]["replayed"] is True

        bff_main._STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY.clear()
        conflict = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/review",
            json={"action": "accept", "reason": "Different payload."},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-review-accept-bff"},
        )
        assert conflict.status_code == 409, conflict.text
        assert _error(conflict.json())["code"] == "IDEMPOTENCY_CONFLICT"
        assert _error(conflict.json())["details"]["precondition_failed"] == "idempotency_conflict"

        convert = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/review",
            json={
                "action": "convert-to-spec-seed",
                "reason": "Accepted seed can enter replication bridge.",
                "strategy_spec_id": "strategy-seed-review-alpha",
            },
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-review-convert-bff"},
        )
        assert convert.status_code == 202, convert.text
        data = convert.json()["data"]
        assert data["status"] == "promoted_to_strategy_spec"
        assert "submit-replication" in data["seed"]["allowedActions"]
        assert data["registry_write_performed"] is False
        assert data["execution_route"] == "none"

        stored = StrategySpecSeedStore(path=seed_store_path).get(SEED_ID)
        assert stored is not None
        assert stored.status == StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC
        assert [item["decision"] for item in stored.lineage["review_decisions"]] == [
            "accept",
            "convert_to_spec_seed",
        ]


def test_strategy_seed_review_request_reject_archive_merge_and_terminal_refusal() -> None:
    with _review_client() as (client, seed_store_path):
        request = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/review",
            json={"action": "request-evidence", "reason": "Need OOS validation."},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-review-request-bff"},
        )
        assert request.status_code == 202, request.text
        assert request.json()["data"]["status"] == "needs_more_evidence"

        reject = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/review",
            json={"action": "reject", "reason": "No replication evidence."},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-review-reject-bff"},
        )
        assert reject.status_code == 202, reject.text
        assert reject.json()["data"]["status"] == "rejected"

        refused = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/review",
            json={"action": "accept"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-review-refused-bff"},
        )
        assert refused.status_code == 409, refused.text
        assert _error(refused.json())["details"]["precondition_failed"] == "status"

        archive = client.post(
            f"/bff/management/strategy-seeds/{ARCHIVE_SEED_ID}/review",
            json={"action": "archive", "reason": "Keep as insight only."},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-review-archive-bff"},
        )
        assert archive.status_code == 202, archive.text
        assert archive.json()["data"]["status"] == "archived_as_insight"

        merge = client.post(
            f"/bff/management/strategy-seeds/{MERGE_SOURCE_ID}/merge",
            json={"target_seed_id": MERGE_TARGET_ID, "reason": "Duplicate candidate."},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-review-merge-bff"},
        )
        assert merge.status_code == 202, merge.text
        assert merge.json()["data"]["status"] == "merged"
        assert merge.json()["data"]["decision"]["target_refs"] == [
            {"type": "strategy_spec_seed", "id": MERGE_TARGET_ID}
        ]

        stored = StrategySpecSeedStore(path=seed_store_path).get(MERGE_SOURCE_ID)
        assert stored is not None
        assert stored.status == StrategySpecSeedStatus.MERGED
        assert stored.lineage["merged_into_seed_id"] == MERGE_TARGET_ID


def test_strategy_seed_review_commands_reject_read_role() -> None:
    with _review_client() as (client, _seed_store_path):
        detail = client.get(
            f"/bff/management/strategy-seeds/{SEED_ID}",
            headers=REVIEWER_HEADERS,
        )
        assert detail.status_code == 200, detail.text

        command = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/review",
            json={"action": "accept"},
            headers={**REVIEWER_HEADERS, "Idempotency-Key": "seed-review-read-role"},
        )
        assert command.status_code == 403, command.text
        assert _error(command.json())["details"]["precondition_failed"] == "role_check"
