from __future__ import annotations

import os
import re
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, List, Optional

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from command_queue import CommandStore  # noqa: E402
from ports import ReadSurfacePorts, create_in_memory_read_surface_ports  # noqa: E402


def _redact_payload(value: Any) -> Any:
    redacted_keys = {
        "policyinternals", "memorytrace", "internalscore", "personainternalstate",
        "secretcredentials", "secretref", "capabilitymapinternals", "capabilitymap",
        "effectivetools", "effectiveskills"
    }
    if isinstance(value, dict):
        res = {}
        for k, v in value.items():
            norm_k = re.sub(r"[^a-z0-9]", "", str(k).lower())
            if norm_k in redacted_keys:
                continue
            res[k] = _redact_payload(v)
        return res
    if isinstance(value, list):
        return [_redact_payload(x) for x in value]
    return value


class ConsultationE2ETestStore(ReadSurfacePorts):
    def __init__(self) -> None:
        super().__init__()
        self._agora_sessions: dict[str, dict[str, Any]] = {}
        self._consult_memos: dict[str, dict[str, Any]] = {}
        self._agora_handoffs: dict[str, dict[str, Any]] = {}

    def create_agora_session(
        self,
        *,
        session_id: str,
        title: str,
        actor_id: str,
        payload: dict[str, Any],
        created_at: Optional[str] = None,
    ) -> dict[str, Any]:
        timestamp = created_at or "2026-04-11T12:00:00Z"
        session = {
            "id": session_id,
            "sessionId": session_id,
            "title": title,
            "mode": payload.get("mode") or payload.get("sessionType") or "quick_ask",
            "status": payload.get("status") or "active",
            "participants": list(payload.get("participants") or []),
            "contextRefs": list(payload.get("contextRefs") or payload.get("context_refs") or []),
            "messages": list(payload.get("messages") or []),
            "createdBy": actor_id,
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        for _field in ("quorumState", "consensusState", "participantRoster", "linkedRequestId"):
            if payload.get(_field) is not None:
                session[_field] = payload[_field]
        self._agora_sessions[session_id] = session
        return dict(session)

    def get_agora_session(self, session_id: str) -> Optional[dict[str, Any]]:
        return self._agora_sessions.get(session_id)

    def append_agora_session_message(
        self,
        session_id: str,
        message: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        session = self._agora_sessions.get(session_id)
        if session is None:
            return None
        session.setdefault("messages", []).append(message)
        return dict(message)

    def open_committee_session(
        self,
        session_id: str,
        *,
        opened_at: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        session = self._agora_sessions.get(session_id)
        if session is None:
            return None
        session["status"] = "open"
        session["openedAt"] = opened_at or "2026-04-11T12:00:00Z"
        session["updatedAt"] = session["openedAt"]
        return dict(session)

    def close_committee_session(
        self,
        session_id: str,
        *,
        closed_at: Optional[str] = None,
        outcome: Optional[str] = None,
        memo_ids: Optional[List[str]] = None,
    ) -> Optional[dict[str, Any]]:
        session = self._agora_sessions.get(session_id)
        if session is None:
            return None
        session["status"] = "closed"
        session["closedAt"] = closed_at or "2026-04-11T12:00:00Z"
        session["updatedAt"] = session["closedAt"]
        if outcome is not None:
            session["outcome"] = outcome
        if memo_ids is not None:
            session["memoIds"] = memo_ids
        return dict(session)

    def submit_committee_session_memo(
        self,
        session_id: str,
        *,
        memo_id: str,
        actor_id: str,
        payload: dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        session = self._agora_sessions.get(session_id)
        if session is None:
            return None
        timestamp = created_at or "2026-04-11T12:00:00Z"
        raw_author = payload.get("authorRef") or payload.get("author_ref") or {"type": "operator", "id": actor_id}
        redacted_author = _redact_payload(raw_author)
        raw_evidence = payload.get("evidenceRefs") or payload.get("evidence_refs") or []
        redacted_evidence = _redact_payload(raw_evidence)
        memo = {
            "id": memo_id,
            "memo_id": memo_id,
            "memoId": memo_id,
            "memo_type": payload.get("memoType") or payload.get("memo_type") or "committee_summary",
            "memoType": payload.get("memoType") or payload.get("memo_type") or "committee_summary",
            "status": "draft",
            "lifecycle_state": "draft",
            "lifecycleState": "draft",
            "linked_session_id": session_id,
            "linkedSessionId": session_id,
            "linked_request_id": payload.get("linkedRequestId") or payload.get("linked_request_id"),
            "linkedRequestId": payload.get("linkedRequestId") or payload.get("linked_request_id"),
            "author_ref": redacted_author,
            "authorRef": redacted_author,
            "summary": payload.get("summary"),
            "recommendations": list(payload.get("recommendations") or []),
            "evidence_refs": redacted_evidence,
            "evidenceRefs": redacted_evidence,
            "created_at": timestamp,
            "createdAt": timestamp,
            "published_at": None,
            "publishedAt": None,
            "session_to_memo_mapping": {
                "mapping_id": f"map-{memo_id}",
                "source_session_id": session_id,
                "memo_id": memo_id,
                "evidence_refs": [
                    item.get("id") if isinstance(item, dict) else str(item)
                    for item in redacted_evidence
                ],
                "created_at": timestamp,
            },
        }
        self._consult_memos[memo_id] = memo
        return dict(memo)

    def list_committee_session_memos(self, session_id: str) -> List[dict[str, Any]]:
        return [
            dict(m) for m in self._consult_memos.values()
            if m.get("linked_session_id") == session_id or m.get("linkedSessionId") == session_id
        ]

    def get_committee_session_memo(self, session_id: str, memo_id: str) -> Optional[dict[str, Any]]:
        memo = self._consult_memos.get(memo_id)
        if memo is None:
            return None
        return dict(memo)

    def publish_committee_session_memo(
        self,
        session_id: str,
        memo_id: str,
        *,
        actor_id: str,
        published_at: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        memo = self._consult_memos.get(memo_id)
        if memo is None:
            return None
        timestamp = published_at or "2026-04-11T12:00:00Z"
        memo["status"] = "published"
        memo["lifecycleState"] = "published"
        memo["lifecycle_state"] = "published"
        memo["publishedAt"] = timestamp
        memo["publishedBy"] = actor_id
        return dict(memo)

    def get_consult_memo(self, memo_id: str) -> Optional[dict[str, Any]]:
        memo = self._consult_memos.get(memo_id)
        if memo is None:
            return None
        return dict(memo)

    def create_agora_handoff(
        self,
        *,
        handoff_id: str,
        handoff_type: str,
        source_route: str,
        source_entity: dict[str, Any],
        destination_route: str,
        destination_queue: str,
        priority: str,
        payload: dict[str, Any],
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> dict[str, Any]:
        timestamp = created_at or "2026-04-11T12:00:00Z"
        record = {
            "id": handoff_id,
            "handoffId": handoff_id,
            "handoffType": handoff_type,
            "status": "submitted",
            "source": {
                "app": "agora",
                "route": source_route,
                "entity": dict(source_entity),
            },
            "destination": {
                "app": "management",
                "route": destination_route,
                "queue": destination_queue,
            },
            "priority": priority,
            "slaDueAt": "2026-04-13T12:00:00Z",
            "rerouteCount": 0,
            "payload": dict(payload),
            "createdBy": {"type": "operator", "id": actor_id},
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        self._agora_handoffs[handoff_id] = record
        return dict(record)

    def get_agora_handoff(self, handoff_id: str) -> Optional[dict[str, Any]]:
        return self._agora_handoffs.get(handoff_id)

    def list_agora_handoffs(self, **kwargs: Any) -> List[dict[str, Any]]:
        return list(self._agora_handoffs.values())


AUTH_HEADERS = {"Authorization": "Bearer ask006-op:operator,reviewer,admin"}


def idempotency_key(prefix: str = "ask006") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


@dataclass(frozen=True)
class ConsultReviewE2E:
    client: TestClient
    root_dir: Path
    auth_headers: dict[str, str]

    def post_json(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        expected_status: int,
        idempotent: bool = True,
    ) -> dict[str, Any]:
        headers = dict(self.auth_headers)
        if idempotent:
            headers["Idempotency-Key"] = idempotency_key()
        response = self.client.post(path, json=payload or {}, headers=headers)
        assert response.status_code == expected_status, response.text
        return response.json()

    def get_json(self, path: str, *, expected_status: int = 200) -> dict[str, Any]:
        response = self.client.get(path, headers=self.auth_headers)
        assert response.status_code == expected_status, response.text
        return response.json()

    def create_ask_session(self, *, session_id: str, correlation_id: str) -> dict[str, Any]:
        body = self.post_json(
            "/bff/agora/ask/sessions",
            payload={
                "sessionId": session_id,
                "title": "ASK-006 consult review trigger",
                "contextRefs": [{"type": "correlation", "id": correlation_id}],
            },
            expected_status=201,
        )
        return body["data"]

    def invoke_committee(
        self,
        *,
        session_id: str,
        linked_request_id: str,
    ) -> dict[str, Any]:
        create_body = self.post_json(
            "/bff/agora/committee/sessions",
            payload={
                "sessionId": session_id,
                "title": "ASK-006 management review committee",
                "linkedRequestId": linked_request_id,
                "quorumState": "quorum_met",
                "consensusState": "open",
                "participantRoster": [
                    {"participantId": "persona-risk", "role": "risk_reviewer"},
                    {"participantId": "persona-exec", "role": "execution_reviewer"},
                ],
            },
            expected_status=201,
        )
        assert create_body["data"]["mode"] == "committee"

        open_body = self.post_json(
            f"/bff/agora/committee/sessions/{session_id}/open",
            payload={},
            expected_status=200,
        )
        return open_body["data"]

    def submit_committee_memo(
        self,
        *,
        session_id: str,
        memo_id: str,
        linked_request_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        body = self.post_json(
            f"/bff/agora/committee/sessions/{session_id}/memos",
            payload={
                "memoId": memo_id,
                "memoType": "committee_summary",
                "linkedRequestId": linked_request_id,
                "summary": "Committee recommends conditional management review.",
                "recommendations": ["Forward to management review with risk conditions."],
                "evidenceRefs": [{"id": "ev-ask006-risk", "type": "evidence_link"}],
                "correlationId": correlation_id,
            },
            expected_status=201,
        )
        return body["data"]

    def publish_committee_memo(
        self,
        *,
        session_id: str,
        memo_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        body = self.post_json(
            f"/bff/agora/committee/sessions/{session_id}/memos/{memo_id}/publish",
            payload={"correlation_id": correlation_id, "priority": "high"},
            expected_status=200,
        )
        return body["data"]

    def ask_events(self) -> list[dict[str, Any]]:
        return [event for _, event in bff_main._sse_buffers["ask"]]


@contextmanager
def consult_review_e2e() -> Iterator[ConsultReviewE2E]:
    with tempfile.TemporaryDirectory() as td:
        root_dir = Path(td)
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_env = {
            key: os.environ.get(key)
            for key in (
                "PANTHEON_BFF_CONSULTATION_DATA_DIR",
                "PANTHEON_CONSULTATION_DATA_DIR",
                "CONSULTATION_DATA_DIR",
                "PANTHEON_BFF_AUTH_STUB",
                "PANTHEON_BFF_AUTH_MODE",
            )
        }
        bff_main.read_store = ConsultationE2ETestStore()
        bff_main.command_store = CommandStore(str(root_dir / "commands.jsonl"))
        bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()
        bff_main._sse_subscribers["ask"].clear()
        for key in (
            "PANTHEON_BFF_CONSULTATION_DATA_DIR",
            "PANTHEON_CONSULTATION_DATA_DIR",
            "CONSULTATION_DATA_DIR",
        ):
            os.environ.pop(key, None)
        os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
        os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"

        try:
            yield ConsultReviewE2E(
                client=TestClient(bff_main.app),
                root_dir=root_dir,
                auth_headers=dict(AUTH_HEADERS),
            )
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
            bff_main._sse_buffers["ask"].clear()
            bff_main._sse_subscribers["ask"].clear()
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
