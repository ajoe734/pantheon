from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore


ROOT = Path(__file__).resolve().parents[4]
SCHEMA_PATH = ROOT / "services/control-plane/specs/persona_paper_live.schema.json"
OPERATOR_HEADERS = {"Authorization": "Bearer op-pplg-promote:operator:mfa"}
APPROVER_HEADERS = {"Authorization": "Bearer risk-owner-pplg:approver:mfa"}


def _validator_for(def_name: str) -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(
        {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": f"#/$defs/{def_name}",
        }
    )


def _paper_payload(persona_id: str) -> Dict[str, Any]:
    return {
        "name": "Promotion Review Paper Persona",
        "mandate": "Paper trade governed promotion review strategy.",
        "strategy_family": ["stat_arb"],
        "market_scope": ["US"],
        "source_scope": ["polygon"],
        "risk_profile_id": "risk-paper-default",
        "paper_capital_pool": {
            "mode": "create_from_template",
            "capital_scope": "paper",
            "capital_pool_id": None,
            "template_id": "paper-default",
        },
        "paper_budget": 100000,
        "artifact_id": "artifact-promotion-review-v1",
        "persona_id": persona_id,
    }


def _fresh_client() -> Iterator[Tuple[TestClient, ReadSurfaceStore]]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=True,
            )
            bff_main.read_store = store
            yield TestClient(bff_main.app, raise_server_exceptions=False), store
        finally:
            bff_main.read_store = original_store


def _launch_paper_persona(client: TestClient, persona_id: str) -> None:
    response = client.post(
        "/bff/management/personas/paper-launch",
        json=_paper_payload(persona_id),
        headers={
            **OPERATOR_HEADERS,
            "Idempotency-Key": f"idem-launch-{persona_id}",
            "X-Trace-Id": f"trace-launch-{persona_id}",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["status"] == "paper_running"


def _request_review(client: TestClient, persona_id: str, *, idem: str = "idem-review") -> Dict[str, Any]:
    response = client.post(
        f"/bff/management/personas/{persona_id}/promotion-reviews",
        json={
            "recommendation": "approve",
            "recommended_allocation": 25000,
            "risk_notes": ["14-day paper window passed"],
            "blocking_findings": [],
            "evidence_refs": [f"evidence:{persona_id}:paper-score"],
        },
        headers={**OPERATOR_HEADERS, "Idempotency-Key": idem},
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()


def test_promotion_review_request_is_queued_and_decidable_from_human_inbox() -> None:
    for client, _store in _fresh_client():
        persona_id = "persona-promotion-request"
        _launch_paper_persona(client, persona_id)

        body = _request_review(client, persona_id, idem="idem-review-create")
        review = body["data"]
        _validator_for("HumanReviewRequest").validate(review)
        assert review["review_type"] == "promotion_to_canary"
        assert review["persona_id"] == persona_id
        assert review["decision_required"] is True
        assert review["system_authority"] == "advisory_only"
        assert body["status"] == "pending"

        replay = _request_review(client, persona_id, idem="idem-review-create")
        assert replay["data"]["review_id"] == review["review_id"]
        assert replay["meta"]["idempotency"]["replayed"] is True

        queue_resp = client.get(
            "/bff/management/promotion-reviews?status=pending",
            headers=OPERATOR_HEADERS,
        )
        assert queue_resp.status_code == 200, queue_resp.text
        queue_items = {item["review_id"]: item for item in queue_resp.json()["items"]}
        assert review["review_id"] in queue_items
        assert queue_items[review["review_id"]]["status"] == "pending"

        detail_resp = client.get(
            f"/bff/management/promotion-reviews/{review['review_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert detail_resp.status_code == 200, detail_resp.text
        assert detail_resp.json()["data"]["review_id"] == review["review_id"]

        inbox_resp = client.get(
            "/bff/management/human-inbox?source_type=promotion_review",
            headers=OPERATOR_HEADERS,
        )
        assert inbox_resp.status_code == 200, inbox_resp.text
        inbox_items = {item["review_id"]: item for item in inbox_resp.json()["items"]}
        inbox_item = inbox_items[review["review_id"]]
        assert inbox_item["source_type"] == "promotion_review"
        assert inbox_item["canDecide"] is True
        assert inbox_item["allowedActions"]["canApprove"] is True
        assert inbox_item["decisionHref"].endswith(f"/promotion-reviews/{review['review_id']}/decisions")


def test_promotion_review_decision_records_approval_and_closes_human_inbox_item() -> None:
    for client, store in _fresh_client():
        persona_id = "persona-promotion-approve"
        _launch_paper_persona(client, persona_id)
        review_body = _request_review(client, persona_id, idem="idem-review-approve")
        review_id = review_body["data"]["review_id"]

        decision_resp = client.post(
            f"/bff/management/promotion-reviews/{review_id}/decisions",
            json={
                "decision": "approve",
                "rationale": "Paper evidence passed risk and cost gates.",
                "evidence_refs": [f"evidence:{persona_id}:human-review"],
            },
            headers={**APPROVER_HEADERS, "Idempotency-Key": "idem-review-decision"},
        )

        assert decision_resp.status_code == 200, decision_resp.text
        decision_body = decision_resp.json()
        assert decision_body["status"] == "approved"
        assert decision_body["decision"]["outcome"] == "approved"
        assert decision_body["decision"]["target_type"] == "HumanReviewRequest"
        assert decision_body["decision"]["target_id"] == review_id
        approval_id = decision_body["decision"]["decision_id"]
        assert store.get_approval_decision(approval_id)["outcome"] == "approved"

        replay = client.post(
            f"/bff/management/promotion-reviews/{review_id}/decisions",
            json={
                "decision": "approve",
                "rationale": "Paper evidence passed risk and cost gates.",
                "evidence_refs": [f"evidence:{persona_id}:human-review"],
            },
            headers={**APPROVER_HEADERS, "Idempotency-Key": "idem-review-decision"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["meta"]["idempotency"]["replayed"] is True

        queue_resp = client.get(
            "/bff/management/promotion-reviews?status=approved",
            headers=OPERATOR_HEADERS,
        )
        assert queue_resp.status_code == 200, queue_resp.text
        assert queue_resp.json()["items"][0]["review_id"] == review_id

        inbox_resp = client.get(
            "/bff/management/human-inbox?source_type=promotion_review",
            headers=OPERATOR_HEADERS,
        )
        assert inbox_resp.status_code == 200, inbox_resp.text
        assert review_id not in {item.get("review_id") for item in inbox_resp.json()["items"]}

        fleet_resp = client.get("/bff/management/persona-fleet", headers=OPERATOR_HEADERS)
        assert fleet_resp.status_code == 200, fleet_resp.text
        rows = {item["persona_id"]: item for item in fleet_resp.json()["items"]}
        assert rows[persona_id]["state"] == "paper_running"
        assert rows[persona_id]["requiredHumanReview"] is None
        assert rows[persona_id]["reviewStatus"] == "none"
