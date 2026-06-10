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
from services.source_ingestion.strategy_seed_builder import StrategySpecSeed
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}
PERSONA_ID = "persona-discovery-momentum"
SEED_ID = "seed-bff-twse-momentum"


def _seed() -> StrategySpecSeed:
    return StrategySpecSeed(
        seed_id=SEED_ID,
        source_id="src-openalex-momentum",
        evidence_bundle_id="bundle-bff-momentum",
        hypothesis="TWSE equity momentum persists over swing holding periods.",
        asset_class=["equity"],
        market_scope=["TWSE"],
        holding_period="swing",
        required_data=["ohlcv", "return labels"],
        backend_hint="qlib",
        feature_hints=["momentum"],
        label_hints=["forward returns"],
        risk_notes=["medium"],
        confidence=0.9,
        source_ids=["src-openalex-momentum"],
        evidence_item_ids=["evitem-bff-momentum"],
        citation_refs=["doi:10/example-bff"],
        trace_refs=["trace-bff-momentum"],
        created_at="2026-06-10T00:00:00Z",
        metadata={
            "strategy_family": "momentum",
            "source_license_scope": "open",
            "access_scope": ["research", "strategy_seed"],
            "source_status": "active",
            "execution_route": "none",
            "data_backfill_available": False,
        },
    )


@contextmanager
def _discovery_client():
    tracked_env = {
        "STRATEGY_SEED_STORE_PATH": os.environ.get("STRATEGY_SEED_STORE_PATH"),
    }
    original_store = bff_main.read_store
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        seed_store_path = root / "strategy_seeds.jsonl"
        os.environ["STRATEGY_SEED_STORE_PATH"] = str(seed_store_path)
        StrategySpecSeedStore(path=seed_store_path).save(_seed())

        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_main._STRATEGY_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()
        bff_main.read_store.create_persona(
            persona_id=PERSONA_ID,
            name="TWSE Momentum Persona",
            actor_id="op-2",
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
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store
            bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _seed_match(response_json: dict) -> dict:
    for item in response_json["data"]:
        if item["matched_object_id"] == SEED_ID:
            return item
    raise AssertionError(f"seed match {SEED_ID} not returned: {response_json}")


def test_bff_persona_strategy_matches_expose_deterministic_seed_match() -> None:
    with _discovery_client() as client:
        response = client.get(
            f"/bff/personas/{PERSONA_ID}/strategy-matches",
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        match = _seed_match(body)

        assert body["profile"]["persona_id"] == PERSONA_ID
        assert body["meta"]["research_only"] is True
        assert body["meta"]["execution_route"] == "none"
        assert body["meta"]["candidate_counts"]["strategy_spec_seeds"] == 1
        assert match["matched_object_type"] == "strategy_spec_seed"
        assert match["score"] >= 80
        assert match["recommended_action"]["type"] == "promote_seed_candidate"
        assert match["metadata"]["blockers"] == []
        assert "research_discovery" in match["allowed_use"]

        canonical = client.get(
            f"/api/v1/personas/{PERSONA_ID}/strategy-matches",
            headers=HEADERS,
        )
        assert canonical.status_code == 200, canonical.text
        assert _seed_match(canonical.json())["match_id"] == match["match_id"]


def test_bff_persona_strategy_match_actions_are_research_only() -> None:
    with _discovery_client() as client:
        matches = client.get(
            f"/api/v1/personas/{PERSONA_ID}/strategy-matches",
            headers=HEADERS,
        )
        match = _seed_match(matches.json())
        match_id = match["match_id"]

        ticket = client.post(
            f"/api/v1/personas/{PERSONA_ID}/strategy-matches/{match_id}/actions",
            json={"action": "create_research_ticket"},
            headers={**HEADERS, "Idempotency-Key": "match-ticket-001"},
        )
        assert ticket.status_code == 202, ticket.text
        ticket_payload = ticket.json()["data"]
        assert ticket_payload["status"] == "research_ticket_created"
        assert ticket_payload["deployment_authority"] == "none"
        assert ticket_payload["registry_write_performed"] is False
        assert ticket_payload["ticket"]["status"] == "open"

        retry = client.post(
            f"/api/v1/personas/{PERSONA_ID}/strategy-matches/{match_id}/actions",
            json={"action": "create_research_ticket"},
            headers={**HEADERS, "Idempotency-Key": "match-ticket-001"},
        )
        assert retry.status_code == 202, retry.text
        assert retry.json()["data"]["ticket"]["ticket_id"] == ticket_payload["ticket"]["ticket_id"]

        promote = client.post(
            f"/bff/personas/{PERSONA_ID}/strategy-matches/{match_id}/actions",
            json={"action": "promote_seed_candidate"},
            headers={**HEADERS, "Idempotency-Key": "match-promote-001"},
        )
        assert promote.status_code == 202, promote.text
        assert promote.json()["data"]["status"] == "queued"
        assert promote.json()["data"]["registry_write_performed"] is False
        assert promote.json()["data"]["deployment_authority"] == "none"

        deploy = client.post(
            f"/api/v1/personas/{PERSONA_ID}/strategy-matches/{match_id}/actions",
            json={"action": "deploy"},
            headers={**HEADERS, "Idempotency-Key": "match-deploy-001"},
        )
        assert deploy.status_code == 422, deploy.text
        error_payload = deploy.json()
        error = (error_payload.get("detail") or error_payload).get("error", {})
        assert error["details"]["precondition_failed"] == "action"


def test_bff_persona_strategy_discovery_rejects_invalid_page_size() -> None:
    with _discovery_client() as client:
        response = client.post(
            f"/api/v1/personas/{PERSONA_ID}/strategy-discovery",
            json={"page_size": "many"},
            headers={**HEADERS, "Idempotency-Key": "match-discovery-invalid-page"},
        )
        assert response.status_code == 422, response.text
        error_payload = response.json()
        error = (error_payload.get("detail") or error_payload).get("error", {})
        assert error["details"]["precondition_failed"] == "page_size"
