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


OPERATOR_TOKEN = "Bearer op-agora-extended:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


def _seed_read_store(path: Path) -> ReadSurfaceStore:
    path.write_text(
        json.dumps(
            {
                "agora_signals": {
                    "sig-extended-001": {
                        "id": "sig-extended-001",
                        "signal_id": "sig-extended-001",
                        "title": "Extended Agora signal",
                        "reviewStatus": "pending_trader_review",
                        "conviction": 0.76,
                        "updatedAt": "2026-05-08T10:09:00Z",
                    }
                },
                "agora_signal_feedback": {},
                "agora_sessions": {
                    "committee-001": {
                        "id": "committee-001",
                        "sessionId": "committee-001",
                        "title": "Signal trust committee",
                        "mode": "committee",
                        "status": "active",
                        "targetEntity": {"type": "signal", "id": "sig-001"},
                        "participants": [{"type": "persona", "id": "persona-alpha"}],
                        "messages": [],
                        "createdAt": "2026-05-08T10:00:00Z",
                        "updatedAt": "2026-05-08T10:00:00Z",
                    },
                    "ask-extended-001": {
                        "id": "ask-extended-001",
                        "sessionId": "ask-extended-001",
                        "title": "Why did signal sig-extended-001 fire?",
                        "mode": "quick_ask",
                        "status": "active",
                        "targetEntity": {"type": "signal", "id": "sig-extended-001"},
                        "participants": [{"type": "operator", "id": "op-agora-extended"}],
                        "messages": [
                            {
                                "id": "msg-extended-001",
                                "sessionId": "ask-extended-001",
                                "sender": {"type": "operator", "id": "op-agora-extended"},
                                "role": "user",
                                "content": "Why did the signal fire?",
                                "language": "en-US",
                                "attachments": [],
                                "citations": [],
                                "annotations": [],
                                "createdAt": "2026-05-08T10:01:00Z",
                            }
                        ],
                        "createdAt": "2026-05-08T10:01:00Z",
                        "updatedAt": "2026-05-08T10:01:00Z",
                    }
                },
                "insight_cards": {
                    "ins-extended-001": {
                        "id": "ins-extended-001",
                        "insight_id": "ins-extended-001",
                        "summary": "Extended Agora inbox insight",
                        "scope": "signal",
                        "scope_ref": "sig-extended-001",
                        "status": "classified",
                        "confidence": {"score": 0.81},
                        "tags": ["inbox"],
                        "source_ref": "agora:sig-extended-001",
                        "supporting_evidence_refs": [],
                        "linked_sources": [],
                        "aggregation_provenance": {"aggregated_at": "2026-05-08T10:02:00Z"},
                        "created_at": "2026-05-08T10:02:00Z",
                        "updated_at": "2026-05-08T10:02:00Z",
                    }
                },
                "postmortems": {
                    "pm-agora-001": {
                        "id": "pm-agora-001",
                        "postmortem_id": "pm-agora-001",
                        "incident_id": "inc-agora-001",
                        "title": "Agora signal review postmortem",
                        "status": "published",
                        "created_at": "2026-05-08T10:03:00Z",
                        "updated_at": "2026-05-08T10:03:00Z",
                    }
                },
                "agora_skill_coaching_sessions": {
                    "skill-coach-001": {
                        "id": "skill-coach-001",
                        "sessionId": "skill-coach-001",
                        "skillId": "risk-review",
                        "personaId": "persona-alpha",
                        "status": "active",
                        "objective": "Coach risk-review skill on signal evidence.",
                        "createdAt": "2026-05-08T10:04:00Z",
                        "updatedAt": "2026-05-08T10:04:00Z",
                    }
                },
                "agora_persona_lab_runs": {
                    "persona-lab-run-001": {
                        "id": "persona-lab-run-001",
                        "runId": "persona-lab-run-001",
                        "draftId": "draft-alpha",
                        "basePersonaId": "persona-alpha",
                        "status": "ready_for_commit",
                        "evaluationRunIds": ["eval-run-001"],
                        "createdAt": "2026-05-08T10:05:00Z",
                        "updatedAt": "2026-05-08T10:05:00Z",
                    }
                },
                "agora_evaluation_suites": {
                    "suite-001": {
                        "id": "suite-001",
                        "suiteId": "suite-001",
                        "title": "Agora evidence citation suite",
                        "status": "active",
                        "createdAt": "2026-05-08T10:06:00Z",
                        "updatedAt": "2026-05-08T10:06:00Z",
                    }
                },
                "agora_evaluation_runs": {
                    "eval-run-001": {
                        "id": "eval-run-001",
                        "runId": "eval-run-001",
                        "suiteId": "suite-001",
                        "status": "passed",
                        "score": 0.92,
                        "createdAt": "2026-05-08T10:07:00Z",
                        "updatedAt": "2026-05-08T10:07:00Z",
                    }
                },
                "agora_committee_evidence_packs": {},
                "agora_handoffs": {},
                "agora_audit_events": {},
            }
        ),
        encoding="utf-8",
    )
    return ReadSurfaceStore(str(path), allow_local_snapshot_fallback=True)


def _empty_read_store(path: Path) -> ReadSurfaceStore:
    path.write_text(json.dumps({}), encoding="utf-8")
    return ReadSurfaceStore(str(path), allow_local_snapshot_fallback=False)


@contextmanager
def _isolated_agora_extended_bff() -> Iterator[TestClient]:
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


@contextmanager
def _isolated_empty_agora_extended_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        store_path = Path(td) / "read_surfaces.json"
        bff_main.read_store = _empty_read_store(store_path)
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()


def _has_item(items: list[dict], field: str, expected: str) -> bool:
    return any(str(item.get(field) or "") == expected for item in items)


def test_agora_extended_final_read_routes_return_seeded_data_with_source_meta() -> None:
    with _isolated_agora_extended_bff() as client:
        cases = [
            ("/bff/agora/inbox", "agora_inbox", "id", "ins-extended-001"),
            ("/bff/agora/ask/sessions", "agora_ask_sessions", "sessionId", "ask-extended-001"),
            (
                "/bff/agora/skill-coaching/sessions",
                "agora_skill_coaching_sessions",
                "sessionId",
                "skill-coach-001",
            ),
            (
                "/bff/agora/persona-lab/runs",
                "agora_persona_lab_runs",
                "runId",
                "persona-lab-run-001",
            ),
            ("/bff/agora/postmortems", "agora_postmortems", "postmortem_id", "pm-agora-001"),
            (
                "/bff/agora/evaluation-suites",
                "agora_evaluation_suites",
                "suiteId",
                "suite-001",
            ),
            (
                "/bff/agora/evaluation-runs",
                "agora_evaluation_runs",
                "runId",
                "eval-run-001",
            ),
        ]

        for path, surface_key, id_field, expected_id in cases:
            response = client.get(path, headers=HEADERS)

            assert response.status_code == 200, response.text
            payload = response.json()
            assert _has_item(payload["items"], id_field, expected_id)
            surface = payload["meta"]["surfaces"][surface_key]
            assert surface["source"] == "local_snapshot"
            assert surface["status"] == "degraded"


def test_agora_extended_routes_use_service_backed_dataset_adapters(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        inbox_store = td_path / "insights.json"
        skill_store = td_path / "skill_coaching.json"
        inbox_store.write_text(
            json.dumps(
                {
                    "ins-env-001": {
                        "id": "ins-env-001",
                        "insight_id": "ins-env-001",
                        "summary": "Service-backed Agora inbox insight",
                    }
                }
            ),
            encoding="utf-8",
        )
        skill_store.write_text(
            json.dumps(
                {
                    "skill-env-001": {
                        "id": "skill-env-001",
                        "sessionId": "skill-env-001",
                        "skillId": "risk-review",
                        "status": "active",
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("PANTHEON_BFF_INSIGHT_CARD_STORE", str(inbox_store))
        monkeypatch.setenv("PANTHEON_BFF_AGORA_SKILL_COACHING_SESSION_STORE", str(skill_store))

        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        bff_main.read_store = ReadSurfaceStore(
            str(td_path / "empty_read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        try:
            client = TestClient(bff_main.app)
            cases = [
                ("/bff/agora/inbox", "agora_inbox", "id", "ins-env-001"),
                (
                    "/bff/agora/skill-coaching/sessions",
                    "agora_skill_coaching_sessions",
                    "sessionId",
                    "skill-env-001",
                ),
            ]
            for path, surface_key, id_field, expected_id in cases:
                response = client.get(path, headers=HEADERS)

                assert response.status_code == 200, response.text
                payload = response.json()
                assert _has_item(payload["items"], id_field, expected_id)
                surface = payload["meta"]["surfaces"][surface_key]
                assert surface["source"] == "service_store"
                assert surface["status"] == "ok"
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store


def test_agora_ask_submission_creates_session_message_and_command_receipt() -> None:
    with _isolated_agora_extended_bff() as client:
        body = {
            "sessionId": "ask-created-001",
            "messageId": "msg-created-001",
            "prompt": "Explain why sig-extended-001 needs review.",
            "contextRefs": [{"type": "signal", "id": "sig-extended-001"}],
        }
        accepted = client.post(
            "/bff/agora/ask",
            headers={**HEADERS, "Idempotency-Key": "agora-ask-created-001"},
            json=body,
        )
        replay = client.post(
            "/bff/agora/ask",
            headers={**HEADERS, "Idempotency-Key": "agora-ask-created-001"},
            json=body,
        )

        assert accepted.status_code == 202, accepted.text
        assert replay.status_code == 202, replay.text
        payload = accepted.json()
        assert payload["status"] == "accepted"
        assert payload["data"]["session"]["sessionId"] == "ask-created-001"
        assert payload["data"]["message"]["id"] == "msg-created-001"
        assert payload["data"]["message"]["content"] == body["prompt"]
        assert payload["meta"]["command"]["command"] == CommandType.AGORA_MESSAGE_ACTION.value
        assert replay.json()["data"]["message"]["id"] == "msg-created-001"

        messages = client.get("/bff/agora/sessions/ask-created-001/messages", headers=HEADERS)
        assert messages.status_code == 200, messages.text
        assert _has_item(messages.json()["items"], "id", "msg-created-001")


def test_agora_extended_empty_fallback_reports_unavailable_source() -> None:
    with _isolated_empty_agora_extended_bff() as client:
        response = client.get("/bff/agora/skill-coaching/sessions", headers=HEADERS)

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["items"] == []
        surface = payload["meta"]["surfaces"]["agora_skill_coaching_sessions"]
        assert surface["source"] == "missing"
        assert surface["status"] == "unavailable"
        assert "degradation" in payload["meta"]


def test_committee_evidence_pack_create_and_upload_files() -> None:
    with _isolated_agora_extended_bff() as client:
        created = client.post(
            "/bff/agora/committee/committee-001/evidence-pack",
            headers={**HEADERS, "Idempotency-Key": "agora-evidence-pack-001"},
            json={
                "targetEntityType": "signal",
                "targetEntityId": "sig-001",
                "linkedEntities": [{"type": "strategy", "id": "strategy-alpha"}],
                "notes": "Committee review evidence pack.",
            },
        )
        assert created.status_code == 201, created.text
        pack = created.json()["data"]
        assert pack["sessionId"] == "committee-001"
        assert pack["targetEntityType"] == "signal"
        assert pack["uploadedFiles"] == []

        uploaded = client.post(
            "/bff/agora/committee/committee-001/evidence-pack/files",
            headers={**HEADERS, "Idempotency-Key": "agora-evidence-files-001"},
            json={
                "files": [
                    {
                        "fileName": "committee-memo.md",
                        "mimeType": "text/markdown",
                        "sizeBytes": 2048,
                        "metadata": {
                            "source": "committee_upload",
                            "title": "Committee memo",
                            "uploadedBy": "trader@local",
                            "createdAt": "2026-05-08T10:05:00Z",
                        },
                    }
                ]
            },
        )
        replay = client.post(
            "/bff/agora/committee/committee-001/evidence-pack/files",
            headers={**HEADERS, "Idempotency-Key": "agora-evidence-files-001"},
            json={
                "files": [
                    {
                        "fileName": "committee-memo.md",
                        "mimeType": "text/markdown",
                        "sizeBytes": 2048,
                        "metadata": {
                            "source": "committee_upload",
                            "title": "Committee memo",
                            "uploadedBy": "trader@local",
                            "createdAt": "2026-05-08T10:05:00Z",
                        },
                    }
                ]
            },
        )

        assert uploaded.status_code == 201, uploaded.text
        assert replay.status_code == 201, replay.text
        payload = uploaded.json()
        assert payload["items"][0]["fileName"] == "committee-memo.md"
        assert payload["data"]["uploadedFiles"][0]["storageUrl"].startswith("bff://agora/committee/")
        assert replay.json()["items"][0]["id"] == payload["items"][0]["id"]


def test_committee_evidence_file_validation_uses_final_error_envelope() -> None:
    with _isolated_agora_extended_bff() as client:
        rejected = client.post(
            "/bff/agora/committee/committee-001/evidence-pack/files",
            headers={**HEADERS, "Idempotency-Key": "agora-evidence-files-invalid"},
            json={
                "files": [
                    {
                        "fileName": "script.exe",
                        "mimeType": "application/octet-stream",
                        "sizeBytes": 512,
                        "metadata": {"source": "committee_upload"},
                    }
                ]
            },
        )

        assert rejected.status_code == 422, rejected.text
        detail = rejected.json()["detail"]["error"]["details"]
        assert detail["precondition_failed"] == "committee_evidence.files"
        assert any(item["code"] == "mime_not_allowed" for item in detail["violations"])


def test_persona_lab_submit_commit_creates_management_handoff() -> None:
    with _isolated_agora_extended_bff() as client:
        submitted = client.post(
            "/bff/agora/persona-lab/draft-alpha/actions/submit-commit",
            headers={**HEADERS, "Idempotency-Key": "agora-persona-lab-001"},
            json={
                "personaDraftId": "draft-alpha",
                "basePersonaId": "persona-alpha",
                "evaluationRunIds": ["eval-001"],
                "changeSummary": "Tighten evidence citation rules before publishing.",
                "requestedRoutePolicyId": "route-policy-001",
            },
        )
        replay = client.post(
            "/bff/agora/persona-lab/draft-alpha/actions/submit-commit",
            headers={**HEADERS, "Idempotency-Key": "agora-persona-lab-001"},
            json={
                "personaDraftId": "draft-alpha",
                "basePersonaId": "persona-alpha",
                "evaluationRunIds": ["eval-001"],
                "changeSummary": "Tighten evidence citation rules before publishing.",
                "requestedRoutePolicyId": "route-policy-001",
            },
        )

        assert submitted.status_code == 202, submitted.text
        assert replay.status_code == 202, replay.text
        payload = submitted.json()
        assert payload["status"] == "accepted"
        assert payload["data"]["handoffType"] == "trainer_feedback_to_persona_update"
        assert payload["data"]["destination"]["queue"] == "persona"
        assert payload["data"]["payload"]["evaluationRunIds"] == ["eval-001"]
        assert payload["meta"]["command"]["command"] == CommandType.PERSONA_ACTION.value
        assert replay.json()["data"]["id"] == payload["data"]["id"]

        listed = client.get("/bff/agora/handoffs", headers=HEADERS)
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["id"] == payload["data"]["id"]


def test_persona_lab_submit_commit_validates_required_review_context() -> None:
    with _isolated_agora_extended_bff() as client:
        rejected = client.post(
            "/bff/agora/persona-lab/draft-alpha/actions/submit-commit",
            headers={**HEADERS, "Idempotency-Key": "agora-persona-lab-invalid"},
            json={"personaDraftId": "draft-alpha", "changeSummary": ""},
        )

        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["detail"]["error"]["details"]["precondition_failed"] == "evaluationRunIds"
