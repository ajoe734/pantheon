from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from models import CommandType
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-agora:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


def _error(resp):
    body = resp.json()
    if isinstance(body.get("error"), dict):
        return body["error"]
    detail = body.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return detail["error"]
    raise AssertionError(f"response did not contain BFF error envelope: {body}")


def _seed_read_store(path: Path) -> ReadSurfaceStore:
    path.write_text(
        json.dumps(
            {
                "agora_signals": {
                    "sig-001": {
                        "id": "sig-001",
                        "signal_id": "sig-001",
                        "title": "Opening auction momentum",
                        "reviewStatus": "pending_trader_review",
                        "conviction": 0.71,
                        "alpha": "auction-momentum",
                        "updatedAt": "2026-05-08T09:00:00Z",
                    }
                },
                "agora_watchlist": {
                    "AAPL": {
                        "id": "watch-AAPL",
                        "symbol": "AAPL",
                        "return1dPct": 2.6,
                    }
                },
                "agora_sessions": {
                    "sess-001": {
                        "id": "sess-001",
                        "sessionId": "sess-001",
                        "title": "Signal review",
                        "status": "active",
                        "messages": [
                            {
                                "id": "msg-001",
                                "sessionId": "sess-001",
                                "sender": {"type": "operator", "id": "op-agora"},
                                "role": "user",
                                "content": "Explain signal sig-001",
                                "language": "en-US",
                                "attachments": [],
                                "citations": [],
                                "annotations": [],
                                "createdAt": "2026-05-08T09:01:00Z",
                            }
                        ],
                        "createdAt": "2026-05-08T09:00:00Z",
                        "updatedAt": "2026-05-08T09:01:00Z",
                    }
                },
                "research_notes": {},
                "decision_journal_entries": {},
                "insight_cards": {
                    "ins-001": {
                        "id": "ins-001",
                        "insight_id": "ins-001",
                        "summary": "Auction momentum needs risk review",
                        "scope": "strategy",
                        "status": "classified",
                        "confidence": {"score": 0.82},
                        "tags": ["signal"],
                        "source_ref": "agora:sig-001",
                        "supporting_evidence_refs": [],
                        "linked_sources": [],
                        "aggregation_provenance": {"aggregated_at": "2026-05-08T09:02:00Z"},
                        "created_at": "2026-05-08T09:02:00Z",
                        "updated_at": "2026-05-08T09:02:00Z",
                    }
                },
                "institutional_memory_entries": {
                    "mem-001": {
                        "id": "mem-001",
                        "entry_id": "mem-001",
                        "headline": "Auction slippage memory",
                        "body": "Opening auction slippage should constrain momentum rollout.",
                        "scope": {"type": "strategy", "id": "strategy-alpha"},
                        "tags": ["risk"],
                        "created_at": "2026-05-08T08:00:00Z",
                        "updated_at": "2026-05-08T08:00:00Z",
                    }
                },
                "agora_training_examples": {},
                "research_tickets": {
                    "rt-001": {
                        "ticket_id": "rt-001",
                        "title": "Review signal feedback",
                        "description": "Decide whether sig-001 should become a research ticket.",
                        "status": "new",
                        "priority": "normal",
                        "owner": "research",
                        "created_at": "2026-05-08T08:30:00Z",
                        "updated_at": "2026-05-08T08:30:00Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return ReadSurfaceStore(str(path), allow_local_snapshot_fallback=True)


@contextmanager
def _isolated_agora_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        store_path = Path(td) / "read_surfaces.json"
        bff_main.read_store = _seed_read_store(store_path)
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()


def _assert_command(payload: dict, command: CommandType) -> None:
    assert payload["status"] == "accepted"
    assert payload["data"]["command"] == command.value
    assert payload["data"]["receipt"]["command"] == command.value
    assert payload["data"]["receipt"]["status"] == "accepted"


def test_agora_daily_signals_sessions_and_research_task_reads() -> None:
    with _isolated_agora_bff() as client:
        daily = client.get("/bff/agora/daily", headers=HEADERS)
        assert daily.status_code == 200, daily.text
        daily_payload = daily.json()
        assert daily_payload["data"]["kpis"]["watchlistMoveCount"] >= 1
        assert daily_payload["data"]["kpis"]["signalReviewQueue"] >= 1
        assert daily_payload["data"]["kpis"]["researchQuestionCount"] >= 1

        signals = client.get("/bff/agora/signals", headers=HEADERS)
        assert signals.status_code == 200, signals.text
        assert signals.json()["items"][0]["id"] == "sig-001"

        signal = client.get("/bff/agora/signals/sig-001", headers=HEADERS)
        assert signal.status_code == 200, signal.text
        assert signal.json()["data"]["signal_id"] == "sig-001"

        watchlist = client.get("/bff/agora/watchlist", headers=HEADERS)
        assert watchlist.status_code == 200, watchlist.text
        assert watchlist.json()["items"][0]["symbol"] == "AAPL"

        sessions = client.get("/bff/agora/sessions", headers=HEADERS)
        assert sessions.status_code == 200, sessions.text
        assert sessions.json()["items"][0]["sessionId"] == "sess-001"

        messages = client.get("/bff/agora/sessions/sess-001/messages", headers=HEADERS)
        assert messages.status_code == 200, messages.text
        assert messages.json()["items"][0]["id"] == "msg-001"

        tasks = client.get("/bff/research/tasks?status=new", headers=HEADERS)
        assert tasks.status_code == 200, tasks.text
        assert tasks.json()["items"][0]["ticket_id"] == "rt-001"


def test_agora_signal_feedback_validates_records_and_replays() -> None:
    with _isolated_agora_bff() as client:
        rejected = client.post(
            "/bff/agora/signals/sig-001/feedback",
            headers={**HEADERS, "Idempotency-Key": "agora-feedback-invalid"},
            json={"decision": "disagree", "confidence": 5},
        )
        assert rejected.status_code == 422, rejected.text
        assert _error(rejected)["details"]["precondition_failed"] == "signal_feedback.reason"

        body = {"decision": "disagree", "confidence": 5, "reason": "Auction slippage risk is elevated"}
        accepted = client.post(
            "/bff/agora/signals/sig-001/feedback",
            headers={**HEADERS, "Idempotency-Key": "agora-feedback-001"},
            json=body,
        )
        replay = client.post(
            "/bff/agora/signals/sig-001/feedback",
            headers={**HEADERS, "Idempotency-Key": "agora-feedback-001"},
            json=body,
        )

        assert accepted.status_code == 200, accepted.text
        assert replay.status_code == 200, replay.text
        payload = accepted.json()
        assert payload["status"] == "completed"
        assert payload["data"]["feedback"]["signalId"] == "sig-001"
        assert payload["data"]["signal"]["reviewStatus"] == "disagree"
        assert payload["meta"]["command"]["command"] == CommandType.AGORA_SIGNAL_FEEDBACK.value
        assert replay.json()["data"]["feedback"]["feedbackId"] == payload["data"]["feedback"]["feedbackId"]


def test_agora_note_journal_insight_and_training_creation() -> None:
    with _isolated_agora_bff() as client:
        note = client.post(
            "/bff/agora/notes",
            headers={**HEADERS, "Idempotency-Key": "agora-note-001"},
            json={"title": "Desk note", "body": "Signal reviewed by the trader.", "tags": ["signal"]},
        )
        assert note.status_code == 201, note.text
        assert note.json()["data"]["title"] == "Desk note"

        journal = client.post(
            "/bff/agora/journal",
            headers={**HEADERS, "Idempotency-Key": "agora-journal-001"},
            json={
                "title": "Delay promotion",
                "decision": "Keep sig-001 in paper observation.",
                "rationale": "Auction slippage risk remains elevated.",
                "tags": ["paper.rollout"],
                "linkedStrategyIds": ["strategy-alpha"],
            },
        )
        assert journal.status_code == 201, journal.text
        assert journal.json()["data"]["canonicalWriteAuthority"] == "agora_journal_service"

        journal_list = client.get("/bff/agora/journal", headers=HEADERS)
        assert journal_list.status_code == 200, journal_list.text
        assert journal_list.json()["items"][0]["title"] == "Delay promotion"

        insight = client.post(
            "/bff/agora/insights",
            headers={**HEADERS, "Idempotency-Key": "agora-insight-001"},
            json={"summary": "Auction risk should gate strategy promotion", "tags": ["risk"]},
        )
        assert insight.status_code == 201, insight.text
        assert insight.json()["data"]["summary"] == "Auction risk should gate strategy promotion"

        training = client.post(
            "/bff/agora/training-examples",
            headers={**HEADERS, "Idempotency-Key": "agora-training-001"},
            json={"input": {"signal": "sig-001"}, "expected": {"decision": "defer"}, "labels": ["risk"]},
        )
        assert training.status_code == 201, training.text
        assert training.json()["data"]["trainingExampleId"].startswith("trn-agora-")


def test_agora_action_routes_emit_command_envelopes() -> None:
    with _isolated_agora_bff() as client:
        message_action = client.post(
            "/bff/agora/messages/msg-001/actions/create-research-task",
            headers={**HEADERS, "Idempotency-Key": "agora-message-action-001"},
            json={"reason": "Escalate message to research"},
        )
        assert message_action.status_code == 202, message_action.text
        _assert_command(message_action.json(), CommandType.AGORA_MESSAGE_ACTION)

        insight_action = client.post(
            "/bff/agora/insights/ins-001/actions/convert-to-training",
            headers={**HEADERS, "Idempotency-Key": "agora-insight-action-001"},
            json={"reason": "Useful training example"},
        )
        assert insight_action.status_code == 202, insight_action.text
        _assert_command(insight_action.json(), CommandType.AGORA_INSIGHT_ACTION)

        memory_action = client.post(
            "/bff/agora/memory/mem-001/actions/merge",
            headers={**HEADERS, "Idempotency-Key": "agora-memory-action-001"},
            json={"reason": "Merge duplicate memory"},
        )
        assert memory_action.status_code == 202, memory_action.text
        _assert_command(memory_action.json(), CommandType.AGORA_MEMORY_ACTION)

        quarantine = client.post(
            "/bff/memory/mem-001/actions/quarantine",
            headers={**HEADERS, "Idempotency-Key": "agora-memory-quarantine-001"},
            json={"reason": "Sensitive memory review"},
        )
        assert quarantine.status_code == 202, quarantine.text
        _assert_command(quarantine.json(), CommandType.AGORA_MEMORY_ACTION)

        attach = client.post(
            "/bff/insights/ins-001/actions/attach-strategy",
            headers={**HEADERS, "Idempotency-Key": "agora-insight-attach-001"},
            json={"strategyId": "strategy-alpha"},
        )
        assert attach.status_code == 202, attach.text
        _assert_command(attach.json(), CommandType.AGORA_INSIGHT_ACTION)


def test_agora_core_error_envelopes_for_missing_objects() -> None:
    with _isolated_agora_bff() as client:
        signal = client.get("/bff/agora/signals/no-such-signal", headers=HEADERS)
        assert signal.status_code == 404
        assert _error(signal)["code"] == "RESOURCE_NOT_FOUND"

        memory = client.post(
            "/bff/memory/no-such-memory/actions/quarantine",
            headers={**HEADERS, "Idempotency-Key": "agora-missing-memory"},
            json={"reason": "test"},
        )
        assert memory.status_code == 404
        assert _error(memory)["details"]["precondition_failed"] == "memory_id"
