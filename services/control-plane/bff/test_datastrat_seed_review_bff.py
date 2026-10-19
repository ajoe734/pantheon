from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from services.control_plane.bff.ports import PersonaRegistryReadsPort, create_in_memory_read_surface_ports
from services.control_plane.bff.strategies.router import create_strategies_router
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


def _build_app(read_store) -> FastAPI:
    def _list_persona_records(tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return list(read_store.list_personas() or [])

    seed_review_idempotency: Dict[str, Any] = {}
    persona_idempotency: Dict[str, Any] = {}

    app = FastAPI()
    app.include_router(
        create_strategies_router(
            read_surface=read_store,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=_utc_now,
            reject_body_idempotency_key=_reject_body_idempotency_key,
            resolve_final_idempotency_key=_resolve_final_idempotency_key,
            stable_json_hash=_stable_json_hash,
            bff_me_tenant_payload=bff_me_tenant_payload,
            list_persona_records=_list_persona_records,
            strategy_seed_review_idempotency_store=seed_review_idempotency,
            strategy_persona_idempotency_store=persona_idempotency,
        )
    )
    # Exposed so tests can simulate cache eviction (durable-store idempotency replay)
    # without reaching into shared module-level state.
    app.state.strategy_seed_review_idempotency = seed_review_idempotency
    app.state.strategy_persona_idempotency = persona_idempotency
    return app


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
        read_store = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={"personas": [persona]},
            persona_training_kwargs={
                "persona_port": PersonaRegistryReadsPort(
                    store=_StubPersonaRegistryStore([persona]),
                ),
            },
        )
        client = TestClient(_build_app(read_store))
        try:
            yield client, seed_store_path
        finally:
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

        client.app.state.strategy_seed_review_idempotency.clear()
        durable_replay = client.post(
            f"/bff/management/strategy-seeds/{SEED_ID}/review",
            json={"action": "accept", "reason": "Enough governed evidence."},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "seed-review-accept-bff"},
        )
        assert durable_replay.status_code == 202, durable_replay.text
        assert durable_replay.json()["data"]["status"] == "accepted"
        assert durable_replay.json()["meta"]["idempotency"]["replayed"] is True

        client.app.state.strategy_seed_review_idempotency.clear()
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
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        seed_store_path = root / "strategy_seeds.jsonl"
        os.environ["STRATEGY_SEED_STORE_PATH"] = str(seed_store_path)
        store = StrategySpecSeedStore(path=seed_store_path)
        store.save(_seed(SEED_ID))
        store.save(_risk_seed(RISK_SEED_ID))
        store.save(_negative_seed(NEGATIVE_SEED_ID))

        read_store = create_in_memory_read_surface_ports()
        client = TestClient(_build_app(read_store))
        try:
            yield client, seed_store_path
        finally:
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


def test_strategy_seed_owner_boundary_read_review_merge_replication_and_persona_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import io
    import time
    import urllib.error
    import urllib.parse
    import urllib.request

    from services.control_plane.bff.core.owner_reads import authorization as _owner_auth, selected_tenant as _owner_tenant
    from services.control_plane.bff.personas.service import _load_strategy_seed_match_candidates
    from services.runtime_auth_inbound import encode_jwt_hs256
    from services.source_ingestion.connectors.base import SourceRecord, SourceRecordStatus, SourceType
    from services.source_ingestion.routers.management import create_management_router
    from services.source_ingestion.runtime import SourceIngestionRuntime

    owner_dir = tmp_path / "source-ingest"
    owner_dir.mkdir(parents=True, exist_ok=True)
    jwt_secret = "seed-owner-boundary-jwt-secret"
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(owner_dir))
    monkeypatch.delenv("STRATEGY_SEED_STORE_PATH", raising=False)
    monkeypatch.delenv("STRATEGY_SPEC_SEED_STORE_PATH", raising=False)
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_API_URL", "http://source-ingest.test")
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", jwt_secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", jwt_secret)
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-dev")

    runtime1 = SourceIngestionRuntime()
    assert runtime1.strategy_seed_store.path == owner_dir / "distill_seeds.jsonl"

    runtime1.evidence_repository.add_source_record(
        SourceRecord(
            source_id="src-finmind-twse-seed-001",
            connector_id="conn-finmind-twse",
            source_type=SourceType.MARKET,
            title="TWSE Institutional Flow Source",
            content_ref="https://api.finmindtrade.com/api/v4/data",
            status=SourceRecordStatus.NORMALIZED,
            created_at="2026-10-18T00:00:00Z",
            metadata={"tenant_id": "tenant-dev"},
        )
    )

    primary_id = "seed-1d23a6d2fbc149f4"
    dup_id = "seed-dup-owner-002"
    risk_id = "seed-risk-owner-003"
    neg_id = "seed-neg-owner-004"
    other_tenant_id = "seed-other-tenant-005"

    def _source_owned_seed(seed_id: str, *, seed_kind: str = "strategy_spec_seed", metadata_extra: Dict[str, Any] | None = None) -> StrategySpecSeed:
        base = _seed(seed_id)
        payload = base.to_dict()
        payload["source_id"] = "src-finmind-twse-seed-001"
        payload["source_ids"] = ["src-finmind-twse-seed-001"]
        meta = dict(payload.get("metadata") or {})
        meta["seed_kind"] = seed_kind
        if metadata_extra:
            meta.update(metadata_extra)
        payload["metadata"] = meta
        return StrategySpecSeed.from_dict(payload)

    runtime1.strategy_seed_store.save(_source_owned_seed(primary_id))
    runtime1.strategy_seed_store.save(_source_owned_seed(dup_id))
    runtime1.strategy_seed_store.save(_source_owned_seed(risk_id, seed_kind="risk_constraint", metadata_extra={"source_surface": "trainer"}))
    runtime1.strategy_seed_store.save(_source_owned_seed(neg_id, seed_kind="negative", metadata_extra={"source_surface": "trainer"}))
    runtime1.strategy_seed_store.save(
        _source_owned_seed(other_tenant_id, metadata_extra={"tenant_id": "tenant-other"})
    )

    owner_app = FastAPI()
    owner_app.include_router(create_management_router(runtime1))
    owner_client = TestClient(owner_app)

    class _FakeHttpResponse(io.BytesIO):
        def __init__(self, body: bytes, status: int) -> None:
            super().__init__(body)
            self.status = status

        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb) -> None:
            self.close()

    def _fake_urlopen(req: urllib.request.Request, timeout: float = 5.0):
        parsed = urllib.parse.urlsplit(req.full_url)
        path_with_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        headers = {k: v for k, v in req.header_items()}
        resp = owner_client.request(
            req.get_method(),
            path_with_query,
            content=req.data,
            headers=headers,
        )
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(
                req.full_url,
                resp.status_code,
                resp.reason_phrase,
                resp.headers,
                io.BytesIO(resp.content),
            )
        return _FakeHttpResponse(resp.content, resp.status_code)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    read_store = create_in_memory_read_surface_ports()
    bff_client = TestClient(_build_app(read_store))
    op_jwt = encode_jwt_hs256(
        {"sub": "seed-review-op", "roles": ["operator"], "tenant_id": "tenant-dev", "exp": int(time.time()) + 600},
        secret=jwt_secret,
    )
    rev_jwt = encode_jwt_hs256(
        {"sub": "seed-reviewer", "roles": ["reviewer"], "tenant_id": "tenant-dev", "exp": int(time.time()) + 600},
        secret=jwt_secret,
    )
    op_headers = {"Authorization": f"Bearer {op_jwt}", "X-Tenant-Id": "tenant-dev"}
    rev_headers = {"Authorization": f"Bearer {rev_jwt}", "X-Tenant-Id": "tenant-dev"}

    # 1. List and detail routes read from source-ingest owner and filter by tenant
    list_resp = bff_client.get("/bff/management/strategy-seeds", headers=rev_headers)
    assert list_resp.status_code == 200, list_resp.text
    list_body = list_resp.json()
    assert list_body["meta"]["store_path"] == "http://source-ingest.test/api/source-ingest/management/strategy-seeds"
    listed_ids = [item["seed_id"] for item in _seed_list_items(list_body)]
    assert listed_ids == [primary_id, dup_id, risk_id, neg_id]

    detail_resp = bff_client.get(f"/bff/management/strategy-seeds/{primary_id}", headers=rev_headers)
    assert detail_resp.status_code == 200, detail_resp.text
    assert detail_resp.json()["data"]["seed_id"] == primary_id

    cross_tenant_resp = bff_client.get(f"/bff/management/strategy-seeds/{other_tenant_id}", headers=rev_headers)
    assert cross_tenant_resp.status_code == 404, cross_tenant_resp.text
    assert _error(cross_tenant_resp.json())["code"] == ErrorCode.RESOURCE_NOT_FOUND

    missing_resp = bff_client.get("/bff/management/strategy-seeds/seed-missing-999", headers=rev_headers)
    assert missing_resp.status_code == 404, missing_resp.text
    assert _error(missing_resp.json())["code"] == ErrorCode.RESOURCE_NOT_FOUND

    # 2. Review (accept + replay + convert-to-spec-seed), convert-to-risk, convert-to-negative, merge, and submit-replication persist in source-ingest
    accept_resp = bff_client.post(
        f"/bff/management/strategy-seeds/{primary_id}/review",
        json={"action": "accept", "reason": "Verified TWSE flow signal."},
        headers={**op_headers, "Idempotency-Key": "owner-bff-accept-1"},
    )
    assert accept_resp.status_code == 202, accept_resp.text
    assert accept_resp.json()["data"]["status"] == "accepted"
    assert accept_resp.json()["data"]["decision"]["reviewer_id"] == "seed-review-op"

    bff_client.app.state.strategy_seed_review_idempotency.clear()
    replay_resp = bff_client.post(
        f"/bff/management/strategy-seeds/{primary_id}/review",
        json={"action": "accept", "reason": "Verified TWSE flow signal."},
        headers={**op_headers, "Idempotency-Key": "owner-bff-accept-1"},
    )
    assert replay_resp.status_code == 202, replay_resp.text
    assert replay_resp.json()["meta"]["idempotency"]["replayed"] is True

    promote_resp = bff_client.post(
        f"/bff/management/strategy-seeds/{primary_id}/review",
        json={"action": "convert-to-spec-seed", "reason": "Promote to StrategySpec."},
        headers={**op_headers, "Idempotency-Key": "owner-bff-promote-1"},
    )
    assert promote_resp.status_code == 202, promote_resp.text
    assert promote_resp.json()["data"]["status"] == "promoted_to_strategy_spec"

    risk_resp = bff_client.post(
        f"/bff/management/strategy-seeds/{risk_id}/review",
        json={"action": "convert-to-risk", "reason": "Convert trainer risk constraint."},
        headers={**op_headers, "Idempotency-Key": "owner-bff-risk-1"},
    )
    assert risk_resp.status_code == 202, risk_resp.text
    assert risk_resp.json()["data"]["status"] == "converted_to_risk_constraint"

    neg_resp = bff_client.post(
        f"/bff/management/strategy-seeds/{neg_id}/review",
        json={"action": "convert-to-negative", "reason": "Convert trainer negative memory."},
        headers={**op_headers, "Idempotency-Key": "owner-bff-neg-1"},
    )
    assert neg_resp.status_code == 202, neg_resp.text
    assert neg_resp.json()["data"]["status"] == "converted_to_negative"

    merge_resp = bff_client.post(
        f"/bff/management/strategy-seeds/{dup_id}/merge",
        json={"target_seed_id": primary_id, "reason": "Merge duplicate hypothesis."},
        headers={**op_headers, "Idempotency-Key": "owner-bff-merge-1"},
    )
    assert merge_resp.status_code == 202, merge_resp.text
    assert merge_resp.json()["data"]["status"] == "merged"

    rep_resp = bff_client.post(
        f"/bff/management/strategy-seeds/{primary_id}/submit-replication",
        json={"strategy_id": "strat-twse-flow-001", "strategy_spec_version": "1.0.0"},
        headers={**op_headers, "Idempotency-Key": "owner-bff-rep-1"},
    )
    assert rep_resp.status_code == 202, rep_resp.text

    # 3. Fresh SourceIngestionRuntime reading the same owner directory observes all persisted mutations
    runtime2 = SourceIngestionRuntime()
    reloaded_primary = runtime2.strategy_seed_store.get(primary_id)
    assert reloaded_primary is not None
    assert reloaded_primary.status == StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC
    assert reloaded_primary.lineage["submitted_by"] == "seed-review-op"
    assert reloaded_primary.lineage["replication_ref"] == rep_resp.json()["data"]["replication_ref"]
    assert runtime2.strategy_seed_store.get(dup_id).status == StrategySpecSeedStatus.MERGED
    assert runtime2.strategy_seed_store.get(risk_id).status == StrategySpecSeedStatus.CONVERTED_TO_RISK_CONSTRAINT
    assert runtime2.strategy_seed_store.get(neg_id).status == StrategySpecSeedStatus.CONVERTED_TO_NEGATIVE

    # 4. Persona discovery loads seed candidates via SourceManagementClient without calling .exists() on remote URI
    _owner_auth.set(op_headers["Authorization"])
    _owner_tenant.set("tenant-dev")
    persona_seeds, surface = _load_strategy_seed_match_candidates(snapshot_at="2026-10-18T00:00:00Z")
    assert surface["status"] == "ok"
    assert surface["path"] == "http://source-ingest.test/api/source-ingest/management/strategy-seeds"
    assert [s.seed_id for s in persona_seeds] == [primary_id, dup_id, risk_id, neg_id]


def test_strategy_seed_owner_boundary_unconfigured_or_unreachable_fails_closed_503(
    monkeypatch,
) -> None:
    import io
    import time
    import urllib.error
    import urllib.request
    import pytest

    from services.control_plane.bff.core.owner_reads import authorization as _owner_auth, selected_tenant as _owner_tenant
    from services.control_plane.bff.personas.service import _load_strategy_seed_match_candidates
    from services.control_plane.bff.source_management_client import SourceManagementClient, SourceManagementClientError
    from services.runtime_auth_inbound import encode_jwt_hs256

    jwt_secret = "seed-owner-unreach-jwt-secret"
    monkeypatch.delenv("STRATEGY_SEED_STORE_PATH", raising=False)
    monkeypatch.delenv("STRATEGY_SPEC_SEED_STORE_PATH", raising=False)
    monkeypatch.delenv("PANTHEON_SOURCE_INGEST_API_URL", raising=False)
    monkeypatch.delenv("PANTHEON_SOURCE_INGEST_URL", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_SERVICE_URL", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_URL", raising=False)
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", jwt_secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", jwt_secret)
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-dev")

    read_store = create_in_memory_read_surface_ports()
    bff_client = TestClient(_build_app(read_store))
    op_jwt = encode_jwt_hs256(
        {"sub": "seed-review-op", "roles": ["operator"], "tenant_id": "tenant-dev", "exp": int(time.time()) + 600},
        secret=jwt_secret,
    )
    op_headers = {"Authorization": f"Bearer {op_jwt}", "X-Tenant-Id": "tenant-dev"}

    # Unconfigured owner URL -> 503 DEPENDENCY_UNAVAILABLE (not 200 empty or 404)
    unconf_list = bff_client.get("/bff/management/strategy-seeds", headers=op_headers)
    assert unconf_list.status_code == 503, unconf_list.text
    assert _error(unconf_list.json())["code"] == ErrorCode.DEPENDENCY_UNAVAILABLE
    assert _error(unconf_list.json())["details"]["owner"] == "source-ingest"

    unconf_detail = bff_client.get("/bff/management/strategy-seeds/seed-1d23a6d2fbc149f4", headers=op_headers)
    assert unconf_detail.status_code == 503, unconf_detail.text
    assert _error(unconf_detail.json())["code"] == ErrorCode.DEPENDENCY_UNAVAILABLE

    # Unreachable owner -> 503 DEPENDENCY_UNAVAILABLE on read, review, merge, and submit-replication
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_API_URL", "http://source-ingest.unreachable")

    def _unreachable_urlopen(req, timeout=5.0):
        raise OSError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _unreachable_urlopen)

    for resp in (
        bff_client.get("/bff/management/strategy-seeds", headers=op_headers),
        bff_client.get("/bff/management/strategy-seeds/seed-1d23a6d2fbc149f4", headers=op_headers),
        bff_client.post(
            "/bff/management/strategy-seeds/seed-1d23a6d2fbc149f4/review",
            json={"action": "accept", "reason": "test"},
            headers={**op_headers, "Idempotency-Key": "unreach-rev-1"},
        ),
        bff_client.post(
            "/bff/management/strategy-seeds/seed-1d23a6d2fbc149f4/merge",
            json={"target_seed_id": "seed-other", "reason": "test"},
            headers={**op_headers, "Idempotency-Key": "unreach-mrg-1"},
        ),
        bff_client.post(
            "/bff/management/strategy-seeds/seed-1d23a6d2fbc149f4/submit-replication",
            json={},
            headers={**op_headers, "Idempotency-Key": "unreach-rep-1"},
        ),
    ):
        assert resp.status_code == 503, resp.text
        err = _error(resp.json())
        assert err["code"] == ErrorCode.DEPENDENCY_UNAVAILABLE
        assert err["details"]["owner"] == "source-ingest"
        assert err["details"]["upstream_error_code"] == "CONNECTION_ERROR"

    _owner_auth.set(op_headers["Authorization"])
    _owner_tenant.set("tenant-dev")
    persona_seeds, surface = _load_strategy_seed_match_candidates(snapshot_at="2026-10-18T00:00:00Z")
    assert persona_seeds == []
    assert surface["status"] == "unavailable"

    # Untyped owner HTTP 404 (missing owner endpoint) -> SourceManagementClient.get raises (not None) and BFF fails closed 503
    def _missing_endpoint_urlopen(req, timeout=5.0):
        raise urllib.error.HTTPError(
            req.full_url,
            404,
            "Not Found",
            {"Content-Type": "application/json"},
            io.BytesIO(b'{"detail":"Not Found"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", _missing_endpoint_urlopen)
    with pytest.raises(SourceManagementClientError) as exc_info:
        SourceManagementClient().get("seed-1d23a6d2fbc149f4")
    assert exc_info.value.status_code == 404
    assert exc_info.value.error_code == "HTTP_404"

    owner_404_detail = bff_client.get("/bff/management/strategy-seeds/seed-1d23a6d2fbc149f4", headers=op_headers)
    assert owner_404_detail.status_code == 503, owner_404_detail.text
    owner_404_err = _error(owner_404_detail.json())
    assert owner_404_err["code"] == ErrorCode.DEPENDENCY_UNAVAILABLE
    assert owner_404_err["details"]["owner"] == "source-ingest"
    assert owner_404_err["details"]["upstream_error_code"] == "HTTP_404"
    assert owner_404_err["details"]["upstream_status"] == 404


