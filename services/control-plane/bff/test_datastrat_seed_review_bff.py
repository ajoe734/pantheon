from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import PersonaRegistryReadsPort, create_in_memory_read_surface_ports
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


class _StubPersonaRegistryStore:
    """Backs `PersonaRegistryReadsPort` with the single seeded persona.

    Only `list_personas`/`get_persona` are exercised (by the seed-inbox's
    persona-strategy-match suggestion path); the rest return empty results.
    """

    def __init__(self, personas: list) -> None:
        self._personas = {p["persona_id"]: p for p in personas}

    def list_personas(self, **kwargs) -> list:
        return list(self._personas.values())

    def get_persona(self, persona_id):
        return self._personas.get(persona_id)

    def get_bindings_for_persona(self, persona_id) -> list:
        return []

    def list_sessions_for_persona(self, persona_id, **kwargs) -> list:
        return []

    def list_teaching_sessions_for_persona(self, persona_id, **kwargs) -> list:
        return []

    def get_capability_snapshot_for_persona(self, persona_id):
        return None


def _persona_record(
    *,
    persona_id: str,
    name: str,
    actor_id: str,
    archetype: str,
    lifecycle_state: str,
    risk_level: str,
    metadata: dict,
) -> dict:
    """Build a persona record shaped like the legacy read-store persona-creation output.

    None of the `/bff/management/strategy-seeds*` handlers under test read
    from `read_store` (they operate entirely on `StrategySpecSeedStore`), so
    this persona is not exercised by any assertion here; it is retained only
    to keep the fixture's shape unchanged for readers/future extension.
    """
    clean_metadata = dict(metadata or {})
    clean_metadata.update({
        "owner": actor_id,
        "archetype": archetype,
        "risk_level": risk_level,
    })
    return {
        "id": persona_id,
        "persona_id": persona_id,
        "name": name,
        "mandate": archetype,
        "strategy_family": archetype,
        "lifecycle_state": lifecycle_state,
        "status": lifecycle_state,
        "created_by": actor_id,
        "required_data_sources": [],
        "metadata": clean_metadata,
    }


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

        persona = _persona_record(
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
        bff_main.read_store = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={"personas": [persona]},
            persona_training_kwargs={
                "persona_port": PersonaRegistryReadsPort(
                    store=_StubPersonaRegistryStore([persona]),
                ),
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


def _seed_list_items(response_json: dict) -> list[dict]:
    assert set(response_json) == {"data", "page_info", "meta"}
    assert "items" not in response_json
    return response_json["data"]["items"]


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
        cards = _seed_list_items(body)
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


RISK_SEED_ID = "seed-review-bff-risk-constraint"
NEGATIVE_SEED_ID = "seed-review-bff-negative"


def _risk_seed(seed_id: str) -> StrategySpecSeed:
    return StrategySpecSeed(
        seed_id=seed_id,
        source_id=f"src-{seed_id}",
        evidence_bundle_id=f"bundle-{seed_id}",
        hypothesis="Risk constraint: max drawdown 10% per symbol per month.",
        asset_class=["equity"],
        market_scope=["TWSE"],
        holding_period="daily",
        required_data=["ohlcv"],
        backend_hint="policy_review",
        feature_hints=["drawdown"],
        label_hints=["drawdown_control"],
        risk_notes=["trainer_seed_requires_review"],
        confidence=0.85,
        status=StrategySpecSeedStatus.DRAFT,
        source_ids=[f"src-{seed_id}"],
        evidence_item_ids=[f"evi-{seed_id}"],
        citation_refs=[f"{seed_id}#ref"],
        trace_refs=[f"trace-{seed_id}"],
        created_at="2026-06-12T00:00:00Z",
        lineage={
            "created_from": "trainer_seed_bridge",
            "registry_write_performed": False,
            "execution_route": "none",
        },
        metadata={
            "source_kind": "trainer",
            "source_surface": "trainer",
            "seed_kind": "risk_constraint",
            "research_only": True,
            "registry_write_performed": False,
            "execution_route": "none",
        },
    )


def _negative_seed(seed_id: str) -> StrategySpecSeed:
    return StrategySpecSeed(
        seed_id=seed_id,
        source_id=f"src-{seed_id}",
        evidence_bundle_id=f"bundle-{seed_id}",
        hypothesis="Negative memory: avoid TWSE momentum strategies during Q4 earnings.",
        asset_class=["equity"],
        market_scope=["TWSE"],
        holding_period="swing",
        required_data=["ohlcv"],
        backend_hint=None,
        feature_hints=["momentum"],
        label_hints=[],
        risk_notes=["trainer_seed_requires_review"],
        confidence=0.80,
        status=StrategySpecSeedStatus.DRAFT,
        source_ids=[f"src-{seed_id}"],
        evidence_item_ids=[f"evi-{seed_id}"],
        citation_refs=[f"{seed_id}#ref"],
        trace_refs=[f"trace-{seed_id}"],
        created_at="2026-06-12T00:00:00Z",
        lineage={
            "created_from": "trainer_seed_bridge",
            "registry_write_performed": False,
            "execution_route": "none",
        },
        metadata={
            "source_kind": "trainer",
            "source_surface": "trainer",
            "seed_kind": "negative",
            "research_only": True,
            "registry_write_performed": False,
            "execution_route": "none",
        },
    )


@contextmanager
def _ids006_client():
    tracked_env = {
        "STRATEGY_SEED_STORE_PATH": os.environ.get("STRATEGY_SEED_STORE_PATH"),
    }
    original_store = bff_main.read_store
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        seed_store_path = root / "strategy_seeds.jsonl"
        os.environ["STRATEGY_SEED_STORE_PATH"] = str(seed_store_path)
        store = StrategySpecSeedStore(path=seed_store_path)
        store.save(_seed(SEED_ID))
        store.save(_risk_seed(RISK_SEED_ID))
        store.save(_negative_seed(NEGATIVE_SEED_ID))

        bff_main.read_store = create_in_memory_read_surface_ports()
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


def test_ids006_seed_card_exposes_source_surface_and_negative_memory_warning() -> None:
    with _ids006_client() as (client, _):
        response = client.get(
            f"/bff/management/strategy-seeds/{RISK_SEED_ID}",
            headers=REVIEWER_HEADERS,
        )
        assert response.status_code == 200, response.text
        card = response.json()["data"]
        assert card["source"]["source_surface"] == "trainer"
        assert "negative_memory_warning" in card
        assert card["negative_memory_warning"]["warning_level"] in {"info", "warning", "blocking"}

        response2 = client.get(
            f"/bff/management/strategy-seeds/{SEED_ID}",
            headers=REVIEWER_HEADERS,
        )
        assert response2.status_code == 200, response2.text
        card2 = response2.json()["data"]
        assert card2["source"]["source_surface"] is None
        assert "negative_memory_warning" in card2


def test_ids006_seed_kind_filter_on_list_endpoint() -> None:
    with _ids006_client() as (client, _):
        response = client.get(
            "/bff/management/strategy-seeds",
            params={"seed_kind": "risk_constraint"},
            headers=REVIEWER_HEADERS,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        ids = [card["seed_id"] for card in _seed_list_items(body)]
        assert RISK_SEED_ID in ids
        assert SEED_ID not in ids
        assert NEGATIVE_SEED_ID not in ids
        assert body["meta"]["filters"]["seed_kind"] == "risk_constraint"

        response2 = client.get(
            "/bff/management/strategy-seeds",
            params={"seed_kind": "negative"},
            headers=REVIEWER_HEADERS,
        )
        assert response2.status_code == 200, response2.text
        ids2 = [card["seed_id"] for card in _seed_list_items(response2.json())]
        assert NEGATIVE_SEED_ID in ids2
        assert RISK_SEED_ID not in ids2


def test_ids006_convert_to_risk_action_for_risk_constraint_seed() -> None:
    with _ids006_client() as (client, seed_store_path):
        list_resp = client.get(
            "/bff/management/strategy-seeds",
            params={"seed_kind": "risk_constraint"},
            headers=REVIEWER_HEADERS,
        )
        card = next(c for c in _seed_list_items(list_resp.json()) if c["seed_id"] == RISK_SEED_ID)
        assert "convert-to-risk" in card["allowedActions"]
        assert "convert-to-spec-seed" not in card["allowedActions"]

        convert = client.post(
            f"/bff/management/strategy-seeds/{RISK_SEED_ID}/review",
            json={"action": "convert-to-risk", "reason": "Reviewed; this is a risk overlay constraint."},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "ids006-convert-risk"},
        )
        assert convert.status_code == 202, convert.text
        data = convert.json()["data"]
        assert data["status"] == "converted_to_risk_constraint"
        assert data["decision"]["decision"] == "convert_to_risk"

        stored = StrategySpecSeedStore(path=seed_store_path).get(RISK_SEED_ID)
        assert stored is not None
        assert stored.status == StrategySpecSeedStatus.CONVERTED_TO_RISK_CONSTRAINT

        refused = client.post(
            f"/bff/management/strategy-seeds/{RISK_SEED_ID}/review",
            json={"action": "accept"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "ids006-convert-risk-refused"},
        )
        assert refused.status_code == 409, refused.text


def test_ids006_convert_to_negative_action_for_negative_seed() -> None:
    with _ids006_client() as (client, seed_store_path):
        list_resp = client.get(
            "/bff/management/strategy-seeds",
            params={"seed_kind": "negative"},
            headers=REVIEWER_HEADERS,
        )
        card = next(c for c in _seed_list_items(list_resp.json()) if c["seed_id"] == NEGATIVE_SEED_ID)
        assert "convert-to-negative" in card["allowedActions"]
        assert "convert-to-spec-seed" not in card["allowedActions"]

        convert = client.post(
            f"/bff/management/strategy-seeds/{NEGATIVE_SEED_ID}/review",
            json={"action": "convert-to-negative", "reason": "Confirmed negative memory; do not re-propose."},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "ids006-convert-negative"},
        )
        assert convert.status_code == 202, convert.text
        data = convert.json()["data"]
        assert data["status"] == "converted_to_negative"
        assert data["decision"]["decision"] == "convert_to_negative"

        stored = StrategySpecSeedStore(path=seed_store_path).get(NEGATIVE_SEED_ID)
        assert stored is not None
        assert stored.status == StrategySpecSeedStatus.CONVERTED_TO_NEGATIVE


def test_ids006_recommended_action_for_kind_seeds() -> None:
    with _ids006_client() as (client, _):
        risk_card_resp = client.get(
            f"/bff/management/strategy-seeds/{RISK_SEED_ID}",
            headers=REVIEWER_HEADERS,
        )
        risk_card = risk_card_resp.json()["data"]
        assert risk_card["recommended_action"]["next"] == "convert-to-risk"

        neg_card_resp = client.get(
            f"/bff/management/strategy-seeds/{NEGATIVE_SEED_ID}",
            headers=REVIEWER_HEADERS,
        )
        neg_card = neg_card_resp.json()["data"]
        assert neg_card["recommended_action"]["next"] == "convert-to-negative"

        strategy_card_resp = client.get(
            f"/bff/management/strategy-seeds/{SEED_ID}",
            headers=REVIEWER_HEADERS,
        )
        strategy_card = strategy_card_resp.json()["data"]
        assert strategy_card["recommended_action"]["type"] == "accept"
        assert "next" not in strategy_card["recommended_action"]


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
