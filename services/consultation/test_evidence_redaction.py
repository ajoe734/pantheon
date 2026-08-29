from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, List, Optional

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from command_queue import CommandStore  # noqa: E402
from ports import ReadSurfacePorts  # noqa: E402


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


AUTH_HEADERS = {"Authorization": "Bearer ask007-op:operator,reviewer,admin"}


SENSITIVE_NEEDLES = {
    "policy_internals",
    "memory_trace",
    "internal_score",
    "persona_internal_state",
    "secret_credentials",
    "secretRef",
    "secret_ref",
    "capability_map_internals",
    "capabilityMap",
    "capability_map",
    "effective_tools",
    "effective_skills",
    "ASK007-POLICY-INTERNALS",
    "ASK007-MEMORY-TRACE",
    "ASK007-SECRET-CREDENTIAL",
    "ASK007-API-KEY",
    "ASK007-CAPABILITY-SECRET",
    "ASK007-CAPABILITY-TOOL",
    "ASK007-CAPABILITY-SKILL",
}


def _assert_review_payload_redacted(payload: Any) -> None:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    leaked = sorted(needle for needle in SENSITIVE_NEEDLES if needle in serialized)
    assert leaked == []


def _idempotency_key() -> str:
    return f"ask007-{uuid.uuid4().hex[:16]}"


def _post_json(
    client: TestClient,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    expected_status: int,
) -> dict[str, Any]:
    headers = {**AUTH_HEADERS, "Idempotency-Key": _idempotency_key()}
    response = client.post(path, json=payload or {}, headers=headers)
    assert response.status_code == expected_status, response.text
    return response.json()


def _get_json(client: TestClient, path: str, *, expected_status: int = 200) -> dict[str, Any]:
    response = client.get(path, headers=AUTH_HEADERS)
    assert response.status_code == expected_status, response.text
    return response.json()


@contextmanager
def _consult_review_client() -> Iterator[TestClient]:
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
            yield TestClient(bff_main.app)
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


def test_ask_007_publish_redacts_persona_internal_evidence_before_review_queue() -> None:
    correlation_id = "corr-ask007-redaction"
    ask_session_id = "ask-007-root-session"
    committee_session_id = "committee-ask007-review"
    memo_id = "memo-ask007-redacted-review"

    with _consult_review_client() as client:
        ask_session = _post_json(
            client,
            "/bff/agora/ask/sessions",
            payload={
                "sessionId": ask_session_id,
                "title": "ASK-007 consult redaction root session",
                "contextRefs": [{"type": "correlation", "id": correlation_id}],
            },
            expected_status=201,
        )["data"]
        assert ask_session["sessionId"] == ask_session_id
        assert ask_session["mode"] == "quick_ask"

        committee = _post_json(
            client,
            "/bff/agora/committee/sessions",
            payload={
                "sessionId": committee_session_id,
                "title": "ASK-007 persona consult redaction review",
                "linkedRequestId": ask_session_id,
                "quorumState": "quorum_met",
                "consensusState": "open",
                "participantRoster": [
                    {"participantId": "persona-risk-ask007", "role": "risk_reviewer"},
                    {"participantId": "persona-exec-ask007", "role": "execution_reviewer"},
                ],
            },
            expected_status=201,
        )["data"]
        assert committee["mode"] == "committee"

        opened_committee = _post_json(
            client,
            f"/bff/agora/committee/sessions/{committee_session_id}/open",
            payload={},
            expected_status=200,
        )["data"]
        assert opened_committee["status"] == "open"

        draft_body = _post_json(
            client,
            f"/bff/agora/committee/sessions/{committee_session_id}/memos",
            payload={
                "memoId": memo_id,
                "memoType": "committee_summary",
                "linkedRequestId": ask_session_id,
                "correlationId": correlation_id,
                "authorRef": {
                    "type": "persona",
                    "id": "persona-risk-ask007",
                    "role": "risk_reviewer",
                    "policy_internals": {"decision_tree": "ASK007-POLICY-INTERNALS"},
                    "memory_trace": ["ASK007-MEMORY-TRACE"],
                    "internal_score": 0.93,
                    "secret_credentials": {"api_key": "ASK007-SECRET-CREDENTIAL"},
                    "capability_map_internals": {
                        "effective_tools": ["ASK007-CAPABILITY-TOOL"],
                        "effective_skills": ["ASK007-CAPABILITY-SKILL"],
                    },
                },
                "summary": "Persona risk review is ready for management review.",
                "recommendations": ["Forward to management review with redacted evidence."],
                "evidenceRefs": [
                    {
                        "id": "ev-ask007-persona-state",
                        "type": "persona",
                        "description": "Persona risk finding",
                        "persona_internal_state": {
                            "policy_internals": "ASK007-POLICY-INTERNALS",
                            "memory_trace": ["ASK007-MEMORY-TRACE"],
                            "internal_score": 0.81,
                        },
                    },
                    {
                        "id": "ev-ask007-secret-credential",
                        "type": "artifact",
                        "description": "Credential-backed artifact reference",
                        "secret_credentials": {"api_key": "ASK007-API-KEY"},
                        "secretRef": "env://ASK007-CAPABILITY-SECRET",
                    },
                    {
                        "id": "ev-ask007-capability-map",
                        "type": "policy",
                        "description": "Capability-gated policy evidence",
                        "capability_map_internals": {
                            "effective_tools": ["ASK007-CAPABILITY-TOOL"],
                            "effective_skills": ["ASK007-CAPABILITY-SKILL"],
                        },
                    },
                ],
            },
            expected_status=201,
        )
        draft_memo = draft_body["data"]
        assert draft_memo["author_ref"] == {
            "type": "persona",
            "id": "persona-risk-ask007",
            "role": "risk_reviewer",
        }

        published_body = _post_json(
            client,
            f"/bff/agora/committee/sessions/{committee_session_id}/memos/{memo_id}/publish",
            payload={"correlationId": correlation_id, "priority": "high"},
            expected_status=200,
        )
        published_memo = published_body["data"]
        assert published_memo["status"] == "published"
        assert published_memo["session_to_memo_mapping"]["evidence_refs"] == [
            "ev-ask007-persona-state",
            "ev-ask007-secret-credential",
            "ev-ask007-capability-map",
        ]

        memo_detail = _get_json(
            client,
            f"/bff/agora/committee/sessions/{committee_session_id}/memos/{memo_id}"
        )["data"]
        handoffs = _get_json(
            client,
            "/bff/agora/handoffs?handoffType=consult_memo_to_management_review"
        )["items"]

        assert len(handoffs) == 1
        assert handoffs[0]["destination"]["queue"] == "consult_memo_review"
        assert handoffs[0]["payload"]["memoId"] == memo_id

        for review_payload in (draft_memo, published_memo, memo_detail, handoffs[0]):
            _assert_review_payload_redacted(review_payload)
