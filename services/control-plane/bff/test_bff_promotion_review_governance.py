from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from models import CommandStatus, CommandType, ObjectType, TargetObject
from read_store import ReadSurfaceStore


OPERATOR_HEADERS = {"Authorization": "Bearer op-promo:operator"}
APPROVER_HEADERS = {"Authorization": "Bearer op-promo-approver:approver"}
ADMIN_HEADERS = {"Authorization": "Bearer op-promo-admin:admin"}


@contextmanager
def _isolated_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_read_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_final_idem = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.read_store = original_read_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idem)


def _idem() -> str:
    return f"promo-review-{uuid.uuid4().hex[:12]}"


def _first_review(client: TestClient) -> dict:
    response = client.get(
        "/bff/management/promotion-reviews",
        headers=OPERATOR_HEADERS,
        params={
            "quarter": "2026-Q1",
            "page_size": 5,
            "action_id": "promote_to_canary_candidate",
        },
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert items
    return items[0]


def _post_decision(
    client: TestClient,
    review_id: str,
    payload: dict,
    *,
    headers: dict,
    idem: str | None = None,
):
    request_headers = dict(headers)
    if idem is not None:
        request_headers["Idempotency-Key"] = idem
    return client.post(
        f"/bff/management/promotion-reviews/{review_id}/decisions",
        headers=request_headers,
        json=payload,
    )


def _submit_review(
    client: TestClient,
    review_id: str,
    *,
    headers: dict = OPERATOR_HEADERS,
    idem: str | None = None,
):
    request_headers = dict(headers)
    if idem is not None:
        request_headers["Idempotency-Key"] = idem
    return client.post(
        f"/bff/management/quarterly-ranking/recommendations/{review_id}/submit",
        headers=request_headers,
        json={"quarter": "2026-Q1"},
    )


def _legacy_promotion_submission_params(
    recommendation_id: str,
    *,
    persona_id: str,
) -> dict:
    return {
        "quarter": "2026-Q3",
        "review_id": recommendation_id,
        "promotion_review_id": recommendation_id,
        "recommendation_id": recommendation_id,
        "recommendationId": recommendation_id,
        "recommendation_action_id": "promote_to_canary_candidate",
        "recommendationActionId": "promote_to_canary_candidate",
        "persona_id": persona_id,
        "stage_from": "paper",
        "stage_to": "canary_candidate",
        "review_kind": "paper_to_canary_review",
        "requires_human_gate_decision": True,
        "live_capital_mutation": False,
        "direct_live_capital_mutation": False,
        "runtime_mutation": False,
    }


def _append_command(
    *,
    command_id: str,
    command_type: CommandType,
    target_type: ObjectType,
    target_id: str,
    params: dict,
    status: CommandStatus = CommandStatus.SUBMITTED,
) -> None:
    bff_main.command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=TargetObject(type=target_type, id=target_id),
        submitted_at="2026-07-13T00:00:00Z",
        params=params,
        audit_context={"operator_id": "op-promo", "reason": "PPL-ALLOC-015 regression fixture"},
    )
    if status != CommandStatus.SUBMITTED:
        assert bff_main.command_store.update_status(command_id, status)


def test_promotion_reviews_list_and_detail_are_readable_by_operator() -> None:
    with _isolated_client() as client:
        list_response = client.get(
            "/bff/management/promotion-reviews",
            headers=OPERATOR_HEADERS,
            params={
                "quarter": "2026-Q1",
                "page_size": 5,
                "action_id": "promote_to_canary_candidate",
            },
        )
        assert list_response.status_code == 200, list_response.text
        list_body = list_response.json()
        assert list_body["meta"]["live_capital_mutation"] is False
        assert list_body["meta"]["requires_human_gate_decision"] is True
        review = list_body["data"]["items"][0]
        assert review["requires_human_gate_decision"] is True
        assert review["live_capital_mutation"] is False
        assert review["status"] == "recommended_not_submitted"
        assert review["submitted"] is False
        assert review["allowedActions"]["canSubmit"] is True
        assert review["allowedActions"]["canApprove"] is False
        assert review["promotion_path"]["from_stage"] == "paper"
        assert review["promotion_path"]["target_stage"] == "canary_candidate"
        assert review["links"]["decisions"].endswith("/decisions")
        assert review["links"]["submit"].endswith("/submit")

        detail_response = client.get(
            f"/bff/management/promotion-reviews/{review['review_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert detail_response.status_code == 200, detail_response.text
        detail_body = detail_response.json()
        assert detail_body["data"]["review_id"] == review["review_id"]
        assert detail_body["meta"]["live_capital_mutation"] is False


def test_quarterly_recommendation_submit_creates_promotion_review_inbox_item(monkeypatch) -> None:
    with _isolated_client() as client:
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        body = submit.json()
        assert body["data"]["submitted"] is True
        assert body["data"]["review_id"] == review["review_id"]
        assert body["data"]["human_inbox_id"].startswith("promotion_review:")
        assert body["data"]["live_capital_mutation"] is False

        records = bff_main.command_store._get_all_commands()
        assert len(records) == 1
        assert records[0]["type"] == "QuarterlyRankingRecommendationSubmit"
        assert records[0]["target"]["type"] == ObjectType.RANKING.value
        assert records[0]["params"]["recommendation_id"] == review["recommendation_id"]
        assert records[0]["params"]["live_capital_mutation"] is False

        detail = client.get(
            f"/bff/management/promotion-reviews/{review['review_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert detail.status_code == 200, detail.text
        detail_data = detail.json()["data"]
        assert detail_data["submitted"] is True
        assert detail_data["status"] == "pending_human_gate"
        assert detail_data["allowedActions"]["canApprove"] is True

        def fail_if_ranking_is_rebuilt(*_args, **_kwargs):
            raise AssertionError("Human Inbox must project the durable submission without rebuilding PM12")

        monkeypatch.setattr(bff_main, "_promotion_review_find", fail_if_ranking_is_rebuilt)
        monkeypatch.setattr(bff_main, "_build_persona_health_items", fail_if_ranking_is_rebuilt)
        for method_name in (
            "list_governance_review_queue_items",
            "list_approval_queue_items",
            "list_v5_interventions",
            "list_sentinel_findings",
        ):
            monkeypatch.setattr(bff_main.read_store, method_name, fail_if_ranking_is_rebuilt)

        inbox = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "promotion_review", "page_size": 10},
        )
        assert inbox.status_code == 200, inbox.text
        inbox_items = inbox.json()["data"]["items"]
        assert any(item["promotion_review_id"] == review["review_id"] for item in inbox_items)
        assert inbox.json()["meta"]["surfaces"]["promotion_reviews"]["source"] == "command_store"

        inbox_detail = client.get(
            f"/bff/management/human-inbox/{detail_data['human_inbox_id']}",
            headers=OPERATOR_HEADERS,
        )
        assert inbox_detail.status_code == 200, inbox_detail.text
        assert inbox_detail.json()["data"]["source_type"] == "promotion_review"


def test_quarterly_recommendation_submit_ignores_caller_source_snapshot() -> None:
    with _isolated_client() as client:
        review = _first_review(client)
        authoritative = review["source_recommendation"]
        forged = {
            **authoritative,
            "name": "FORGED VIEWER TITLE",
            "rationale": "FORGED VIEWER RATIONALE",
            "priority": "critical",
            "evidence_refs": [
                {
                    "ref_id": "private-evidence",
                    "source_document": "FORGED PRIVATE EVIDENCE",
                }
            ],
            "evidence_ref_ids": ["private-evidence"],
        }
        submit = client.post(
            f"/bff/management/quarterly-ranking/recommendations/{review['review_id']}/submit",
            headers={**OPERATOR_HEADERS, "Idempotency-Key": _idem()},
            json={"quarter": "2026-Q1", "source_recommendation": forged},
        )

        assert submit.status_code == 202, submit.text
        records = bff_main.command_store._get_all_commands()
        stored = records[0]["params"]["source_recommendation"]
        assert stored["name"] == authoritative["name"]
        assert stored.get("rationale") == authoritative.get("rationale")
        assert stored.get("priority") == authoritative.get("priority")
        assert stored["evidence_refs"] == []
        assert stored["evidence_ref_ids"] == []

        inbox = client.get(
            "/bff/management/human-inbox",
            headers={"Authorization": "Bearer promotion-viewer:viewer"},
            params={"source_type": "promotion_review", "page_size": 10},
        )

        assert inbox.status_code == 200, inbox.text
        item = next(
            item
            for item in inbox.json()["data"]["items"]
            if item["promotion_review_id"] == review["review_id"]
        )
        serialized = json.dumps(item, sort_keys=True)
        assert "FORGED VIEWER TITLE" not in serialized
        assert "FORGED VIEWER RATIONALE" not in serialized
        assert "FORGED PRIVATE EVIDENCE" not in serialized


def test_human_inbox_timeout_keeps_durable_promotion_review_visible(monkeypatch) -> None:
    with _isolated_client() as client:
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text

        monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "0.05")

        def slow_persona_readiness(*_args, **_kwargs):
            time.sleep(0.25)
            return []

        monkeypatch.setattr(bff_main, "_build_persona_health_items", slow_persona_readiness)
        inbox = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"page_size": 20},
        )

        assert inbox.status_code == 200, inbox.text
        body = inbox.json()
        assert any(
            item["promotion_review_id"] == review["review_id"]
            for item in body["data"]["items"]
            if item["source_type"] == "promotion_review"
        )
        assert body["meta"]["partial"] is True
        assert body["meta"]["surfaces"]["human_inbox"]["status"] == "degraded"
        assert body["meta"]["surfaces"]["persona_readiness"]["reason"] == "read_timeout"


def test_human_inbox_filtered_local_snapshot_empty_remains_degraded(monkeypatch) -> None:
    with _isolated_client() as client:
        monkeypatch.setattr(
            bff_main.read_store,
            "list_approval_queue_items",
            lambda **_: [
                {
                    "decision_id": "approval-local-snapshot",
                    "decision_type": "DeploymentPlan",
                    "decision_state": "pending",
                    "risk_level": "high",
                    "submitted_at": "2026-07-13T00:00:00Z",
                }
            ],
        )
        original_dataset_source = bff_main.read_store.dataset_source

        def local_snapshot_source(dataset: str, **kwargs):
            if dataset == "approval_queue_items":
                return "local_snapshot"
            return original_dataset_source(dataset, **kwargs)

        monkeypatch.setattr(bff_main.read_store, "dataset_source", local_snapshot_source)

        response = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "approval", "status": "no-such-status"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"]["items"] == []
        approval_surface = body["meta"]["surfaces"]["approval_queue"]
        assert approval_surface["source"] == "local_snapshot"
        assert approval_surface["status"] == "degraded"
        assert body["meta"]["surfaces"]["human_inbox"]["status"] == "degraded"


def test_hiq_backlog_remains_available_after_human_inbox_surface_extension(monkeypatch) -> None:
    with _isolated_client() as client:
        monkeypatch.setattr(bff_main.read_store, "list_governance_review_queue_items", lambda **_: [])
        monkeypatch.setattr(bff_main.read_store, "list_approval_queue_items", lambda **_: [])
        monkeypatch.setattr(bff_main.read_store, "list_v5_interventions", lambda **_: [])
        monkeypatch.setattr(bff_main.read_store, "list_sentinel_findings", lambda **_: (True, []))
        monkeypatch.setattr(
            bff_main,
            "_build_persona_health_items",
            lambda *_args, **_kwargs: [],
        )

        response = client.get(
            "/bff/management/hiq-backlog",
            headers=OPERATOR_HEADERS,
            params={"page_size": 10},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["id"] == "management-hiq-backlog"


def test_human_inbox_promotion_projection_reads_command_log_once(monkeypatch) -> None:
    with _isolated_client() as client:
        recommendation_ids = [
            "pm12-2026-q3-persona-alpha-promote_to_canary_candidate",
            "pm12-2026-q3-persona-beta-promote_to_canary_candidate",
        ]
        for index, recommendation_id in enumerate(recommendation_ids, start=1):
            _append_command(
                command_id=f"cmd-promotion-submit-{index}",
                command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
                target_type=ObjectType.RANKING,
                target_id=recommendation_id,
                params=_legacy_promotion_submission_params(
                    recommendation_id,
                    persona_id=f"persona-{'alpha' if index == 1 else 'beta'}",
                ),
            )
        _append_command(
            command_id="cmd-promotion-decision-1",
            command_type=CommandType.HUMAN_GATE_APPROVE,
            target_type=ObjectType.HUMAN_GATE_ITEM,
            target_id=f"promotion_review:{recommendation_ids[0]}",
            params={
                "review_id": recommendation_ids[0],
                "recommendation_id": recommendation_ids[0],
                "decision": "approve",
                "rationale": "Single-pass projection fixture.",
            },
            status=CommandStatus.EXECUTED,
        )

        original_get_all_commands = bff_main.command_store._get_all_commands
        command_log_reads = 0

        def counted_get_all_commands():
            nonlocal command_log_reads
            command_log_reads += 1
            return original_get_all_commands()

        monkeypatch.setattr(
            bff_main.command_store,
            "_get_all_commands",
            counted_get_all_commands,
        )

        response = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "promotion_review", "page_size": 10},
        )

        assert response.status_code == 200, response.text
        assert {
            item["promotion_review_id"] for item in response.json()["data"]["items"]
        } == set(recommendation_ids)
        assert command_log_reads == 1


def test_human_inbox_omits_inconsistent_generic_snapshot_and_private_evidence() -> None:
    with _isolated_client() as client:
        recommendation_id = "pm12-2026-q3-persona-forged-promote_to_canary_candidate"
        params = _legacy_promotion_submission_params(
            recommendation_id,
            persona_id="persona-forged",
        )
        params.update(
            {
                "ranking_snapshot_id": "ranking-quarter-authoritative",
                "source_recommendation": {
                    "id": recommendation_id,
                    "recommendation_id": recommendation_id,
                    "ranking_snapshot_id": "ranking-quarter-attacker-controlled",
                    "quarter": "2026-Q3",
                    "persona_id": "persona-forged",
                    "name": "Forged Persona",
                    "action_id": "promote_to_canary_candidate",
                    "state": "paper",
                    "evidence_refs": [
                        {
                            "ref_id": "private-evidence",
                            "source_document": "viewer-only-secret",
                        }
                    ],
                },
            }
        )
        _append_command(
            command_id="cmd-promotion-forged-snapshot",
            command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
            target_type=ObjectType.RANKING,
            target_id=recommendation_id,
            params=params,
        )

        response = client.get(
            "/bff/management/human-inbox",
            headers={"Authorization": "Bearer promotion-viewer:viewer"},
            params={"source_type": "promotion_review", "page_size": 10},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["items"] == []
        assert "viewer-only-secret" not in response.text


def test_human_inbox_legacy_snapshotless_submission_is_safe_and_minimal() -> None:
    with _isolated_client() as client:
        recommendation_id = "pm12-2026-q3-persona-legacy-promote_to_canary_candidate"
        params = _legacy_promotion_submission_params(
            recommendation_id,
            persona_id="persona-legacy",
        )
        params["source_document"] = "must-not-be-projected"
        _append_command(
            command_id="cmd-promotion-legacy",
            command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
            target_type=ObjectType.RANKING,
            target_id=recommendation_id,
            params=params,
        )

        response = client.get(
            "/bff/management/human-inbox",
            headers={"Authorization": "Bearer promotion-viewer:viewer"},
            params={"source_type": "promotion_review", "page_size": 10},
        )

        assert response.status_code == 200, response.text
        items = response.json()["data"]["items"]
        assert len(items) == 1
        item = items[0]
        assert item["promotion_review_id"] == recommendation_id
        assert item["persona_id"] == "persona-legacy"
        assert item["promotion_review"]["evidence_refs"] == []
        assert item["promotion_review"]["source_recommendation"]["recommendation_id"] == (
            recommendation_id
        )
        assert "must-not-be-projected" not in json.dumps(item, sort_keys=True)


def test_human_inbox_omits_failed_promotion_submission() -> None:
    with _isolated_client() as client:
        recommendation_id = "pm12-2026-q3-persona-failed-promote_to_canary_candidate"
        _append_command(
            command_id="cmd-promotion-failed",
            command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
            target_type=ObjectType.RANKING,
            target_id=recommendation_id,
            params=_legacy_promotion_submission_params(
                recommendation_id,
                persona_id="persona-failed",
            ),
            status=CommandStatus.FAILED,
        )

        response = client.get(
            "/bff/management/human-inbox",
            headers=OPERATOR_HEADERS,
            params={"source_type": "promotion_review", "page_size": 10},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["items"] == []


def test_promotion_review_decision_requires_prior_submit() -> None:
    with _isolated_client() as client:
        review = _first_review(client)
        response = _post_decision(
            client,
            review["review_id"],
            {"decision": "approve", "rationale": "Cannot approve before submit."},
            headers=APPROVER_HEADERS,
            idem=_idem(),
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "HUMAN_GATE_PENDING"
        assert bff_main.command_store._get_all_commands() == []


def test_promotion_review_approve_submits_human_gate_command() -> None:
    with _isolated_client() as client:
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        response = _post_decision(
            client,
            review["review_id"],
            {"decision": "approve", "rationale": "Paper evidence supports canary admission."},
            headers=APPROVER_HEADERS,
            idem=_idem(),
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["data"]["decision"] == "approve"
        assert body["data"]["decision_status"] == "accepted"
        assert body["meta"]["live_capital_mutation"] is False
        assert body["meta"]["requires_human_gate_decision"] is True

        records = bff_main.command_store._get_all_commands()
        assert len(records) == 2
        record = records[1]
        assert record["type"] == "HumanGateApprove"
        assert record["target"]["type"] == ObjectType.HUMAN_GATE_ITEM.value
        assert record["params"]["review_id"] == review["review_id"]
        assert record["params"]["live_capital_mutation"] is False
        assert record["audit"]["live_capital_side_effects"] is False


def test_promotion_review_approve_with_conditions_preserves_conditions_and_rationale() -> None:
    with _isolated_client() as client:
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        conditions = [
            "Run canary with paper-sized notional for one full market week.",
            {"metric": "slippage_bps", "max": 8},
        ]
        rationale = "Canary is acceptable only with explicit execution drift guardrails."
        response = _post_decision(
            client,
            review["review_id"],
            {
                "decision": "approve_with_conditions",
                "conditions": conditions,
                "rationale": rationale,
            },
            headers=ADMIN_HEADERS,
            idem=_idem(),
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["data"]["decision"] == "approve_with_conditions"
        assert body["data"]["conditions"] == conditions
        assert body["data"]["rationale"] == rationale

        record = bff_main.command_store._get_all_commands()[1]
        assert record["type"] == "HumanGateApprove"
        assert record["params"]["decision"] == "approve_with_conditions"
        assert record["params"]["conditions"] == conditions
        assert record["params"]["rationale"] == rationale


def test_promotion_review_reject_requires_non_empty_rationale() -> None:
    with _isolated_client() as client:
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        response = _post_decision(
            client,
            review["review_id"],
            {"decision": "reject", "rationale": "  "},
            headers=APPROVER_HEADERS,
            idem=_idem(),
        )
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_FAILED"
        assert error["details"]["precondition_failed"] == "rationale"
        assert [record["type"] for record in bff_main.command_store._get_all_commands()] == [
            "QuarterlyRankingRecommendationSubmit"
        ]


def test_promotion_review_decision_requires_approver_or_admin_role() -> None:
    with _isolated_client() as client:
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        response = _post_decision(
            client,
            review["review_id"],
            {"decision": "approve", "rationale": "Operator can read but cannot approve."},
            headers=OPERATOR_HEADERS,
            idem=_idem(),
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "FORBIDDEN"
        assert [record["type"] for record in bff_main.command_store._get_all_commands()] == [
            "QuarterlyRankingRecommendationSubmit"
        ]


def test_promotion_review_idempotency_replay_has_no_direct_live_mutation() -> None:
    with _isolated_client() as client:
        review = _first_review(client)
        submit = _submit_review(client, review["review_id"], idem=_idem())
        assert submit.status_code == 202, submit.text
        idem_key = _idem()
        payload = {"decision": "approve", "rationale": "Replay should return the same receipt."}
        first = _post_decision(
            client,
            review["review_id"],
            payload,
            headers=APPROVER_HEADERS,
            idem=idem_key,
        )
        second = _post_decision(
            client,
            review["review_id"],
            payload,
            headers=APPROVER_HEADERS,
            idem=idem_key,
        )
        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        first_body = first.json()
        second_body = second.json()
        assert first_body["data"]["command_id"] == second_body["data"]["command_id"]
        assert second_body["meta"]["idempotency"]["replayed"] is True
        assert second_body["meta"]["live_capital_mutation"] is False
        assert second_body["data"]["live_capital_mutation"] is False

        records = bff_main.command_store._get_all_commands()
        assert len(records) == 2
        assert records[1]["target"]["type"] != ObjectType.RUNTIME.value
        assert records[1]["params"]["live_capital_mutation"] is False
        assert records[1]["params"]["runtime_mutation"] is False
