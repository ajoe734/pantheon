from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import CommandType
from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-agora-extended:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


class AgoraExtendedTestReadPorts(ReadSurfacePorts):
    def __init__(self, data: dict | None = None, *, fallback_degraded: bool = True) -> None:
        super().__init__()
        self._data = data or {}
        self._fallback_degraded = fallback_degraded

    def dataset_source(self, dataset: str) -> str:
        env_map = {
            "insights": "PANTHEON_BFF_INSIGHT_CARD_STORE",
            "insight_cards": "PANTHEON_BFF_INSIGHT_CARD_STORE",
            "agora_skill_coaching_sessions": "PANTHEON_BFF_AGORA_SKILL_COACHING_SESSION_STORE",
        }
        if dataset in env_map and os.environ.get(env_map[dataset]):
            return "service_store"
        if not self._data:
            return "missing"
        return "local_snapshot" if self._fallback_degraded else "in_memory"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        source = self.dataset_source(dataset)
        if source == "service_store":
            return {"status": "ok", "source": "service_store", "snapshot_at": snapshot_at}
        if source == "local_snapshot":
            return {"status": "degraded", "source": "local_snapshot", "snapshot_at": snapshot_at}
        if source == "missing":
            return {"status": "unavailable", "source": "missing", "snapshot_at": snapshot_at}
        return {"status": "ok", "source": source, "snapshot_at": snapshot_at}

    def list_agora_signals(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_signals", {}).values())

    def get_agora_signal(self, signal_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_signals", {}).get(str(signal_id or ""))

    def list_agora_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_sessions", {}).values())

    def get_agora_session(self, session_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_sessions", {}).get(str(session_id or ""))

    def create_agora_session(self, session_id: str | None = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        sid = session_id or kwargs.get("session_id") or kwargs.get("sessionId") or f"sess-{uuid.uuid4().hex[:8]}"
        title = kwargs.get("title") or "Agora session"
        payload = kwargs.get("payload") or {}
        sess = {
            "id": sid,
            "sessionId": sid,
            "title": title,
            "messages": [],
            "createdAt": kwargs.get("created_at", "2026-05-08T10:00:00Z"),
            "updatedAt": kwargs.get("created_at", "2026-05-08T10:00:00Z"),
            **payload,
        }
        self._data.setdefault("agora_sessions", {})[sid] = sess
        return sess

    def append_agora_session_message(self, session_id: str | None = None, message: dict[str, Any] | None = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        sid = session_id or kwargs.get("session_id") or kwargs.get("sessionId")
        sess = self.get_agora_session(sid) or self.create_agora_session(session_id=sid, title="Session")
        msg = dict(message or kwargs.get("message") or kwargs.get("payload") or {})
        if not msg and args and isinstance(args[0], dict):
            msg = dict(args[0])
        for k in ("content", "role", "sender", "language"):
            if k in kwargs:
                msg[k] = kwargs[k]
        if "id" not in msg:
            msg["id"] = kwargs.get("message_id") or f"msg-{uuid.uuid4().hex[:8]}"
        sess.setdefault("messages", []).append(msg)
        return msg

    def list_agora_messages(self, session_id: str | None, **kwargs: Any) -> list[dict[str, Any]]:
        sess = self.get_agora_session(session_id)
        return list(sess.get("messages", [])) if sess else []

    def list_agora_session_messages(self, session_id: str | None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_agora_messages(session_id, **kwargs)

    def list_insight_cards(self, **kwargs: Any) -> list[dict[str, Any]]:
        inbox_env = os.environ.get("PANTHEON_BFF_INSIGHT_CARD_STORE")
        if inbox_env and os.path.exists(inbox_env):
            try:
                return list(json.loads(Path(inbox_env).read_text(encoding="utf-8")).values())
            except Exception:
                pass
        return list(self._data.get("insight_cards", {}).values())

    def list_agora_insights(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_insight_cards(**kwargs)

    def get_insight_card(self, card_id: str | None) -> dict[str, Any] | None:
        return self._data.get("insight_cards", {}).get(str(card_id or ""))

    def list_postmortems(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("postmortems", {}).values())

    def list_agora_postmortems(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_postmortems(**kwargs)

    def list_agora_skill_coaching_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        coach_env = os.environ.get("PANTHEON_BFF_AGORA_SKILL_COACHING_SESSION_STORE")
        if coach_env and os.path.exists(coach_env):
            try:
                return list(json.loads(Path(coach_env).read_text(encoding="utf-8")).values())
            except Exception:
                pass
        return list(self._data.get("agora_skill_coaching_sessions", {}).values())

    def get_agora_skill_coaching_session(self, session_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_skill_coaching_sessions", {}).get(str(session_id or ""))

    def list_agora_persona_lab_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_persona_lab_runs", {}).values())

    def get_agora_persona_lab_run(self, run_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_persona_lab_runs", {}).get(str(run_id or ""))

    def list_agora_evaluation_suites(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_evaluation_suites", {}).values())

    def get_agora_evaluation_suite(self, suite_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_evaluation_suites", {}).get(str(suite_id or ""))

    def list_agora_evaluation_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_evaluation_runs", {}).values())

    def get_agora_evaluation_run(self, run_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_evaluation_runs", {}).get(str(run_id or ""))

    def list_agora_committee_evidence_packs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_committee_evidence_packs", {}).values())

    def get_agora_committee_evidence_pack(self, session_id: str | None) -> dict[str, Any] | None:
        sid = str(session_id or "")
        return self._data.get("agora_committee_evidence_packs", {}).get(sid) or self._data.get("agora_committee_evidence_packs", {}).get(f"pack-{sid}")

    def create_agora_committee_evidence_pack(self, session_id: str | None = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        sid = session_id or kwargs.get("session_id") or kwargs.get("sessionId") or ""
        payload = kwargs.get("payload") or {}
        actor_id = kwargs.get("actor_id") or "op-agora-extended"
        pack = {
            "id": f"pack-{sid}",
            "sessionId": sid,
            "targetEntityType": payload.get("targetEntityType"),
            "targetEntityId": payload.get("targetEntityId"),
            "uploadedFiles": [],
            "files": [],
            "uploadedBy": actor_id,
            **payload,
        }
        self._data.setdefault("agora_committee_evidence_packs", {})[sid] = pack
        self._data.setdefault("agora_committee_evidence_packs", {})[pack["id"]] = pack
        return pack

    def _read_dataset_records(self, dataset: str) -> list[dict[str, Any]]:
        if dataset in ("insight_cards", "insights"):
            return self.list_insight_cards()
        if dataset == "agora_skill_coaching_sessions":
            return self.list_agora_skill_coaching_sessions()
        if dataset == "agora_signals":
            return self.list_agora_signals()
        if dataset == "agora_sessions":
            return self.list_agora_sessions()
        if dataset == "postmortems":
            return self.list_postmortems()
        if dataset == "agora_persona_lab_runs":
            return self.list_agora_persona_lab_runs()
        if dataset == "agora_evaluation_suites":
            return self.list_agora_evaluation_suites()
        if dataset == "agora_evaluation_runs":
            return self.list_agora_evaluation_runs()
        if dataset == "agora_committee_evidence_packs":
            return self.list_agora_committee_evidence_packs()
        if dataset == "agora_handoffs":
            return self.list_agora_handoffs()
        raw = self._data.get(dataset, {})
        return list(raw.values()) if isinstance(raw, dict) else list(raw)

    def append_agora_committee_evidence_files(self, session_id: str | None = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        sid = session_id or kwargs.get("session_id") or kwargs.get("sessionId") or ""
        files = kwargs.get("files") or (args[0] if args and isinstance(args[0], list) else [])
        pack = self.get_agora_committee_evidence_pack(sid) or self.create_agora_committee_evidence_pack(session_id=sid)
        uploaded = []
        for idx, f in enumerate(files):
            file_record = {
                "id": f"file-{sid}-{len(pack.get('files', [])) + idx}",
                "fileName": f["fileName"],
                "mimeType": f["mimeType"],
                "sizeBytes": f["sizeBytes"],
                "storageUrl": f"bff://agora/committee/{sid}/{f['fileName']}",
                "metadata": f.get("metadata", {}),
            }
            uploaded.append(file_record)
            pack.setdefault("files", []).append(file_record)
            pack.setdefault("uploadedFiles", []).append(file_record)
        return {"id": pack["id"], "sessionId": sid, "uploadedFiles": pack.get("uploadedFiles", []), "files": pack["files"], "newFiles": uploaded}

    def list_agora_handoffs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_handoffs", {}).values())

    def get_agora_handoff(self, handoff_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_handoffs", {}).get(str(handoff_id or ""))

    def create_agora_handoff(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        h_id = kwargs.get("handoff_id") or f"handoff-{uuid.uuid4().hex[:8]}"
        destination_queue = kwargs.get("destination_queue") or "persona"
        destination = kwargs.get("destination") or {"queue": destination_queue, "route": kwargs.get("destination_route")}
        h = {
            "id": h_id,
            "handoffId": h_id,
            "handoffType": kwargs.get("handoff_type", "trainer_feedback_to_persona_update"),
            "destination": destination,
            "destinationQueue": destination_queue,
            "payload": kwargs.get("payload", {}),
            "actorId": kwargs.get("actor_id", "op-agora-extended"),
            "sourceRoute": kwargs.get("source_route"),
            "sourceEntity": kwargs.get("source_entity"),
            "createdAt": kwargs.get("created_at", "2026-05-08T10:00:00Z"),
        }
        self._data.setdefault("agora_handoffs", {})[h_id] = h
        return h

    def record_agora_audit_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return event


def _seed_read_store() -> AgoraExtendedTestReadPorts:
    data = {
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
    return AgoraExtendedTestReadPorts(data, fallback_degraded=True)


def _empty_read_store() -> AgoraExtendedTestReadPorts:
    return AgoraExtendedTestReadPorts({}, fallback_degraded=False)


@contextmanager
def _isolated_agora_extended_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        bff_main.read_store = _seed_read_store()
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
        bff_main.read_store = _empty_read_store()
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
        bff_main.read_store = _empty_read_store()
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
        detail = rejected.json()["error"]["details"]
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
        assert rejected.json()["error"]["details"]["precondition_failed"] == "evaluationRunIds"
