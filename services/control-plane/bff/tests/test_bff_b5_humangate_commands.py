from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.action_catalog import get_catalog_entry
from services.control_plane.bff.command_executor import execute_command_with_status
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import CommandStatus, CommandType
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


HEADERS = {
    "Authorization": "Bearer op-b5-human:operator,reviewer,approver:mfa",
    "X-Trace-Id": "trace-bff-b5-001",
    "X-Correlation-Id": "corr-bff-b5-001",
    "X-Request-Id": "req-bff-b5-001",
}


async def _noop_process_command(_command_id: str) -> None:
    return None


@contextmanager
def _isolated_b5_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_command_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command
        original_worker_stub = bff_main._process_command_stub
        original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        store = create_in_memory_read_surface_ports()
        store.ranking_snapshots = {}
        def _put_snapshot(rec):
            sid = rec.get("ranking_snapshot_id") or rec.get("id")
            store.ranking_snapshots[sid] = rec
            return rec
        store.put_ranking_snapshot = _put_snapshot
        store.get_ranking_snapshot = lambda snapshot_id: store.ranking_snapshots.get(snapshot_id)
        store.get_capability_snapshot_for_persona = lambda pid: {"persona_id": pid, "capabilities": []}
        store.list_persona_league = lambda **kw: [
            {"id": "p-1", "persona_id": "p-1", "rank": 1, "league_tier": "champion", "score": 95.0, "status": "active"},
        ]
        store.list_personas = lambda **kw: [
            {"id": "p-1", "persona_id": "p-1", "name": "Alpha-Ops", "lifecycle_state": "active", "status": "healthy"},
        ]
        bff_main.read_store = store
        bff_main._process_command = _noop_process_command
        bff_main._process_command_stub = _noop_process_command
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        if hasattr(bff_main, "_COMMAND_IDEMPOTENCY"):
            bff_main._COMMAND_IDEMPOTENCY.clear()
        bff_main._V5_INTERVENTIONS_STORE.clear()
        bff_main._V5_INTERVENTIONS_STORE.append(
            {
                "intervention_id": "intv-b5-human-001",
                "kind": "hiq_sentinel",
                "status": "pending",
                "target_type": "Runtime",
                "target_id": "runtime-b5-human-001",
                "triggered_at": "2026-05-23T10:00:00Z",
                "description": "B5 HumanGate intervention fixture.",
                "correlation_id": "corr-b5-human-001",
            }
        )
        bff_main._V5_INTERVENTIONS_STORE.append(
            {
                "intervention_id": "b5-revoke",
                "kind": "hiq_sentinel",
                "status": "pending",
                "risk_level": "medium",
                "target_type": "Runtime",
                "target_id": "runtime-b5-revoke-001",
                "triggered_at": "2026-05-23T10:01:00Z",
                "description": "B5 HumanGate revocation source fixture.",
                "triggered_by": "governance-queue",
                "correlation_id": "corr-b5-revoke-001",
            }
        )
        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.command_store = original_command_store
            bff_main.read_store = original_read_store
            bff_main._process_command = original_worker
            bff_main._process_command_stub = original_worker_stub
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._V5_INTERVENTIONS_STORE.clear()
            bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


def _submit_command(
    client: TestClient,
    *,
    command: str,
    target_type: str,
    target_id: str,
    params: dict | None = None,
    idempotency_key: str,
):
    return client.post(
        "/bff/v1/commands",
        headers={**HEADERS, "Idempotency-Key": idempotency_key},
        json={
            "command": command,
            "target": {"type": target_type, "id": target_id},
            "action": "submit",
            "params": params or {},
            "audit_context": {"reason": f"BFF-B5-001 {command} contract test"},
        },
    )


def _accepted_command_id(payload: dict) -> str:
    assert payload["status"] == "accepted"
    assert payload["data"]["status"] == "accepted"
    assert payload["data"]["trackingUrl"].startswith("/api/v1/operator/commands/")
    assert payload["data"]["receipt"]["status"] == "accepted"
    assert payload["meta"]["durable"] is True
    assert payload["meta"]["liveCapitalSideEffects"] is False
    return payload["data"]["command_id"]


def test_humangate_command_names_are_admitted_through_bff_v1_commands() -> None:
    cases = [
        ("HumanGateApprove", "approval:b5-approve", {}),
        ("HumanGateReject", "approval:b5-reject", {"rejection_reason": "risk budget exceeded"}),
        ("HumanGateRequestMoreEvidence", "approval:b5-evidence", {"evidence_request": "attach PM-12 packet"}),
        ("HumanGateRevoke", "intervention:b5-revoke", {"revoke_reason": "stale intervention"}),
        ("HumanGateExtendTtl", "intervention:b5-ttl", {"ttlSeconds": 3600}),
    ]

    with _isolated_b5_client() as client:
        for command, target_id, params in cases:
            response = _submit_command(
                client,
                command=command,
                target_type="HumanGateItem",
                target_id=target_id,
                params=params,
                idempotency_key=f"bff-b5-{command}",
            )

            assert response.status_code == 202, response.text
            payload = response.json()
            command_id = _accepted_command_id(payload)
            assert payload["data"]["command"] == command
            assert payload["meta"]["idempotency"]["idempotencyKey"] == f"bff-b5-{command}"

            record = bff_main.command_store.get_command(command_id)
            assert record is not None
            assert record["type"] == command
            assert record["target"] == {"type": "HumanGateItem", "id": target_id}
            assert record["params"]["human_gate_item_id"] == target_id
            assert record["params"]["itemId"] == target_id
            assert record["params"]["decision"] in {
                "approve",
                "reject",
                "request_more_evidence",
                "revoke",
                "extend_ttl",
            }
            assert record["params"]["audit_event"].startswith("human_gate.")
            assert record["foundation"]["admission_route"] == "POST /bff/v1/commands"


def test_human_inbox_decision_flow_can_submit_decisions_via_command_path() -> None:
    with _isolated_b5_client() as client:
        inbox = client.get(
            "/bff/management/human-inbox",
            headers=HEADERS,
            params={"source_type": "intervention", "page_size": 1},
        )
        assert inbox.status_code == 200, inbox.text
        item = inbox.json()["data"]["items"][0]
        assert item["id"].startswith("intervention:")

        for command in (
            "HumanGateApprove",
            "HumanGateReject",
            "HumanGateRequestMoreEvidence",
        ):
            response = _submit_command(
                client,
                command=command,
                target_type="HumanGateItem",
                target_id=item["id"],
                params={"source_record_id": item["source_id"]},
                idempotency_key=f"bff-b5-inbox-{command}",
            )

            assert response.status_code == 202, response.text
            payload = response.json()
            command_id = _accepted_command_id(payload)
            record = bff_main.command_store.get_command(command_id)
            assert record is not None
            assert record["target"]["id"] == item["id"]
            assert record["params"]["source_type"] == "intervention"
            assert record["params"]["source_record_id"] == item["source_id"]
            bff_main.command_store.update_status(command_id, CommandStatus.EXECUTED)


def test_quarterly_ranking_recommendation_submit_uses_command_response_without_live_mutation() -> None:
    with _isolated_b5_client() as client:
        recommendations = client.get(
            "/bff/management/quarterly-ranking/recommendations",
            headers=HEADERS,
            params={"quarter": "2026-Q1", "page_size": 1},
        )
        assert recommendations.status_code == 200, recommendations.text
        item = recommendations.json()["data"]["items"][0]

        response = _submit_command(
            client,
            command="QuarterlyRankingRecommendationSubmit",
            target_type="Ranking",
            target_id=item["recommendation_id"],
            params={
                "quarter": item["quarter"],
                "recommendation_id": item["recommendation_id"],
                "recommendation_action_id": item["action_id"],
                "persona_id": item["persona_id"],
                "ranking_snapshot_id": item["ranking_snapshot_id"],
                "live_capital_mutation": item["live_capital_mutation"],
            },
            idempotency_key="bff-b5-quarterly-recommendation-submit",
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        command_id = _accepted_command_id(payload)
        assert payload["data"]["command"] == "QuarterlyRankingRecommendationSubmit"

        record = bff_main.command_store.get_command(command_id)
        assert record is not None
        assert record["type"] == "QuarterlyRankingRecommendationSubmit"
        assert record["target"] == {"type": "Ranking", "id": item["recommendation_id"]}
        assert record["params"]["recommendation_id"] == item["recommendation_id"]
        assert record["params"]["recommendation_action_id"] == item["action_id"]
        assert record["params"]["ranking_snapshot_id"] == item["ranking_snapshot_id"]
        assert record["params"]["action_id"] == "submit_recommendation"
        assert record["params"]["actionId"] == "submit_recommendation"
        assert record["params"]["audit_event"] == "quarterly_ranking.recommendation_submitted"
        assert record["audit"]["receipt_dual_write"]["command_receipt"]["command"] == (
            "QuarterlyRankingRecommendationSubmit"
        )


def test_b5_commands_are_in_action_catalog_and_executor_dispatch(monkeypatch) -> None:
    from services.control_plane.bff import command_executor as command_executor
    monkeypatch.setitem(command_executor._EXECUTORS, CommandType.HUMAN_GATE_APPROVE, command_executor._execute_bff_action_adapter)
    expected = {
        "HumanGateApprove": "HumanGateItem",
        "HumanGateReject": "HumanGateItem",
        "HumanGateRequestMoreEvidence": "HumanGateItem",
        "HumanGateRevoke": "HumanGateItem",
        "HumanGateExtendTtl": "HumanGateItem",
        "QuarterlyRankingRecommendationSubmit": "Ranking",
    }

    for command, entity_type in expected.items():
        entry = get_catalog_entry(command)
        assert entry is not None
        assert entry.entity_type == entity_type
        assert entry.endpoint == "/bff/v1/commands"

    status, result, error = execute_command_with_status(
        "cmd-b5-human-executor",
        CommandType.HUMAN_GATE_APPROVE,
        {
            "action_id": "approve",
            "entity_type": "human_gate_item",
            "entity_id": "approval:b5-executor",
            "audit_event": "human_gate.approve",
        },
    )
    assert status == CommandStatus.EXECUTED
    assert error is None
    assert result is not None
    assert result["dispatch_path"] == "bff_action_adapter"
    assert result["live_capital_side_effects"] is False
    assert result["two_man_signature_id"] is None
