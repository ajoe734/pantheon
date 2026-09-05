"""ASK-004: committee session memo publish to registry / review contract tests.

Covers:
  GET  /bff/agora/committee/sessions/{id}/memos              — list session memos
  POST /bff/agora/committee/sessions/{id}/memos              — submit draft memo
  GET  /bff/agora/committee/sessions/{id}/memos/{memoId}     — get memo for review
  POST /bff/agora/committee/sessions/{id}/memos/{memoId}/publish — publish to registry

Canonical basis:
  AI_COLLABORATION_GUIDE.md (ASK-004 scope)
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.router import create_agora_router
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import OperatorIdentity

AUTH = {"Authorization": "Bearer ask-test-op:operator"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _project_consult_memo_summary(memo: Dict[str, Any]) -> Dict[str, Any]:
    memo_id = str(memo.get("memo_id") or memo.get("id") or "").strip()
    recommendations = list(memo.get("recommendations") or [])
    return {
        "object_ref": {"type": "ConsultMemo", "id": memo_id},
        "memo_id": memo_id,
        "memo_type": memo.get("memo_type") or "red_team",
        "status": memo.get("status") or memo.get("lifecycle_state") or "draft",
        "linked_request_id": memo.get("linked_request_id"),
        "recommendation_count": len(recommendations),
        "published_at": memo.get("published_at"),
        "created_at": memo.get("created_at"),
    }


def _project_consult_memo_detail(memo: Dict[str, Any]) -> Dict[str, Any]:
    memo_id = str(memo.get("memo_id") or memo.get("id") or "").strip()
    mapping = memo.get("session_to_memo_mapping") if isinstance(memo.get("session_to_memo_mapping"), dict) else {}
    governance_target = memo.get("governance_target") if isinstance(memo.get("governance_target"), dict) else {}
    return {
        "object_ref": {"type": "ConsultMemo", "id": memo_id},
        "memo_id": memo_id,
        "memo_type": memo.get("memo_type") or "red_team",
        "status": memo.get("status") or memo.get("lifecycle_state") or "draft",
        "lifecycle_state": memo.get("lifecycle_state") or memo.get("status") or "draft",
        "author_ref": memo.get("author_ref"),
        "linked_request_id": memo.get("linked_request_id"),
        "linked_session_id": memo.get("linked_session_id"),
        "session_to_memo_mapping": {
            "mapping_id": mapping.get("mapping_id"),
            "source_session_id": mapping.get("source_session_id"),
            "transcript_id": mapping.get("transcript_id"),
            "transcript_version": mapping.get("transcript_version"),
            "memo_id": mapping.get("memo_id") or memo_id,
            "memo_type": mapping.get("memo_type") or memo.get("memo_type") or "red_team",
            "created_by": copy.deepcopy(mapping.get("created_by") or {}),
            "evidence_refs": list(mapping.get("evidence_refs") or []),
            "mapping_status": mapping.get("mapping_status"),
            "created_at": mapping.get("created_at"),
        },
        "summary": memo.get("summary"),
        "recommendations": list(memo.get("recommendations") or []),
        "evidence_refs": list(memo.get("evidence_refs") or []),
        "published_at": memo.get("published_at"),
        "created_at": memo.get("created_at"),
        "supersedes_memo_id": memo.get("supersedes_memo_id"),
        "superseded_by_memo_id": memo.get("superseded_by_memo_id"),
        "surface_state": memo.get("surface_state") or "ok",
        "governance_target": copy.deepcopy(governance_target),
        "suppressed": bool(memo.get("suppressed")),
        "withdrawn": bool(memo.get("withdrawn")),
        "active_governance_review_id": memo.get("active_governance_review_id"),
    }


class _CommitteeMemoReadStore:
    """Local in-memory double for ASK-004 committee-session memo publish.

    ``ReadSurfacePorts`` (the migrated read-only container) deliberately
    excludes session/memo mutation methods such as ``create_agora_session``,
    ``submit_committee_session_memo``, ``publish_committee_session_memo`` and
    ``create_agora_handoff`` -- see
    ``RETAINED_WRITES_DEFERRED_FROM_READ_SURFACE`` in
    tests/test_read_surface_caller_migration.py -- so this test double
    hand-implements the small set of methods the ASK-004 routes in main.py
    call directly on the ``read_store`` global, backed by plain dicts
    instead of the retired legacy read-surface store class.
    """

    def __init__(self, seed_sessions: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        # ``_data`` is read by main.py's ``_sem_local_records``/``dataset_source``
        # fallbacks (via ``getattr(read_store, "_data", {})``) for list routes.
        self._data: Dict[str, Any] = {
            "agora_sessions": copy.deepcopy(seed_sessions or {}),
            "consult_memos": {},
            "agora_handoffs": {},
        }

    # ---- sessions ---- #

    def get_agora_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        session = self._data["agora_sessions"].get(str(session_id))
        return copy.deepcopy(session) if session is not None else None

    def create_agora_session(
        self,
        *,
        session_id: str,
        title: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now()
        session = {
            "id": session_id,
            "sessionId": session_id,
            "title": title,
            "mode": payload.get("mode") or payload.get("sessionType") or "quick_ask",
            "status": payload.get("status") or "active",
            "participants": copy.deepcopy(payload.get("participants") or []),
            "messages": copy.deepcopy(payload.get("messages") or []),
            "createdBy": actor_id,
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        for field in ("quorumState", "consensusState", "participantRoster", "linkedRequestId"):
            if payload.get(field) is not None:
                session[field] = copy.deepcopy(payload[field])
        self._data["agora_sessions"][session_id] = session
        return copy.deepcopy(session)

    # ---- consult memos ---- #

    def dataset_source(self, dataset: str, **_kwargs: Any) -> str:
        records = self._data.get(dataset)
        if isinstance(records, dict) and records:
            return "local_snapshot"
        if isinstance(records, list) and records:
            return "local_snapshot"
        return "missing"

    def get_consult_memo(self, memo_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not memo_id:
            return None
        record = self._data["consult_memos"].get(str(memo_id))
        return _project_consult_memo_detail(record) if record is not None else None

    def submit_committee_session_memo(
        self,
        session_id: str,
        *,
        memo_id: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        session = self.get_agora_session(session_id)
        if session is None or str(session.get("mode") or "").strip() != "committee":
            return None
        timestamp = created_at or _utc_now()
        memo_type = str(payload.get("memoType") or payload.get("memo_type") or "committee_summary").strip() or "committee_summary"
        author_ref = copy.deepcopy(
            payload.get("authorRef") or payload.get("author_ref") or {"type": "operator", "id": actor_id}
        )
        evidence_refs = copy.deepcopy(list(payload.get("evidenceRefs") or payload.get("evidence_refs") or []))
        evidence_ref_ids: List[str] = []
        for item in evidence_refs:
            if isinstance(item, dict):
                ref_id = str(item.get("id") or item.get("ref_id") or item.get("artifact_ref") or "").strip()
            else:
                ref_id = str(item or "").strip()
            if ref_id and ref_id not in evidence_ref_ids:
                evidence_ref_ids.append(ref_id)
        if isinstance(author_ref, dict):
            created_by = copy.deepcopy(author_ref)
        else:
            created_by = {"actor_type": "operator", "actor_id": str(author_ref or actor_id)}
        memo: Dict[str, Any] = {
            "id": memo_id,
            "memo_id": memo_id,
            "memo_type": memo_type,
            "status": "draft",
            "lifecycle_state": "draft",
            "linked_session_id": session_id,
            "linked_request_id": (
                payload.get("linkedRequestId")
                or payload.get("linked_request_id")
                or session.get("linkedRequestId")
            ),
            "author_ref": author_ref,
            "session_to_memo_mapping": {
                "mapping_id": f"map-{memo_id}",
                "source_session_id": session_id,
                "transcript_id": payload.get("transcriptId") or payload.get("transcript_id") or f"tr-{session_id}",
                "transcript_version": payload.get("transcriptVersion") or payload.get("transcript_version"),
                "memo_id": memo_id,
                "memo_type": memo_type,
                "created_by": created_by,
                "evidence_refs": evidence_ref_ids,
                "mapping_status": "draft",
                "created_at": timestamp,
            },
            "summary": str(payload.get("summary") or "").strip() or None,
            "recommendations": copy.deepcopy(list(payload.get("recommendations") or [])),
            "evidence_refs": evidence_refs,
            "created_at": timestamp,
            "published_at": None,
            "governance_target": {
                "target_type": "artifact",
                "target_id": None,
                "deployment_plan_id": None,
                "artifact_id": None,
                "strategy_id": None,
            },
        }
        self._data["consult_memos"][memo_id] = memo
        return _project_consult_memo_detail(copy.deepcopy(memo))

    def list_committee_session_memos(self, session_id: str) -> List[Dict[str, Any]]:
        memos = [
            memo
            for memo in self._data["consult_memos"].values()
            if str(memo.get("linked_session_id") or "") == str(session_id)
        ]
        memos.sort(key=lambda memo: str(memo.get("created_at") or ""), reverse=True)
        return [_project_consult_memo_summary(memo) for memo in memos]

    def get_committee_session_memo(self, session_id: str, memo_id: str) -> Optional[Dict[str, Any]]:
        record = self._data["consult_memos"].get(str(memo_id))
        if record is None or str(record.get("linked_session_id") or "") != str(session_id):
            return None
        return _project_consult_memo_detail(record)

    def publish_committee_session_memo(
        self,
        session_id: str,
        memo_id: str,
        *,
        actor_id: str,
        published_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        record = self._data["consult_memos"].get(str(memo_id))
        if record is None or str(record.get("linked_session_id") or "") != str(session_id):
            return None
        timestamp = published_at or _utc_now()
        memo = copy.deepcopy(record)
        if str(memo.get("status") or memo.get("lifecycle_state") or "").strip().lower() == "published":
            return _project_consult_memo_detail(memo)
        memo["status"] = "published"
        memo["lifecycle_state"] = "published"
        memo["published_at"] = timestamp
        memo["published_by"] = actor_id
        mapping = memo.get("session_to_memo_mapping")
        if isinstance(mapping, dict):
            mapping["mapping_status"] = "active"
        self._data["consult_memos"][memo_id] = memo
        return _project_consult_memo_detail(copy.deepcopy(memo))

    def list_consult_memos(self, *, statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        memos = list(self._data["consult_memos"].values())
        if statuses:
            requested = {str(value).strip().lower() for value in statuses if str(value).strip()}
            memos = [
                memo
                for memo in memos
                if str(memo.get("status") or memo.get("lifecycle_state") or "").strip().lower() in requested
            ]
        memos.sort(
            key=lambda memo: (
                str(memo.get("published_at") or memo.get("created_at") or ""),
                str(memo.get("created_at") or ""),
                str(memo.get("memo_id") or ""),
            ),
            reverse=True,
        )
        return [_project_consult_memo_summary(memo) for memo in memos]

    # ---- handoffs ---- #

    def create_agora_handoff(
        self,
        *,
        handoff_id: str,
        handoff_type: str,
        source_route: str,
        source_entity: Dict[str, Any],
        destination_route: str,
        destination_queue: str,
        priority: str,
        payload: Dict[str, Any],
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now()
        record = {
            "id": handoff_id,
            "handoffId": handoff_id,
            "handoffType": handoff_type,
            "status": "submitted",
            "source": {"app": "agora", "route": source_route, "entity": copy.deepcopy(source_entity)},
            "destination": {"app": "management", "route": destination_route, "queue": destination_queue},
            "priority": priority,
            "payload": copy.deepcopy(payload),
            "createdBy": {"type": "operator", "id": actor_id},
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        self._data["agora_handoffs"][handoff_id] = record
        return copy.deepcopy(record)

_SEED_SESSIONS = {
    "committee-memo-001": {
        "id": "committee-memo-001",
        "sessionId": "committee-memo-001",
        "title": "Strategy Alpha committee review",
        "mode": "committee",
        "status": "open",
        "participants": [{"type": "persona", "id": "persona-alpha"}],
        "quorumState": "quorum",
        "consensusState": "open",
        "participantRoster": [],
        "messages": [],
        "createdAt": "2026-05-16T09:00:00Z",
        "updatedAt": "2026-05-16T09:00:00Z",
    },
    "ask-session-001": {
        "id": "ask-session-001",
        "sessionId": "ask-session-001",
        "title": "Quick ask — not a committee session",
        "mode": "quick_ask",
        "status": "active",
        "participants": [{"type": "operator", "id": "ask-test-op"}],
        "messages": [],
        "createdAt": "2026-05-16T08:00:00Z",
        "updatedAt": "2026-05-16T08:00:00Z",
    },
}


def _idem() -> str:
    return f"idem-{uuid.uuid4().hex[:16]}"


def _extract_identity(auth: Optional[str]) -> OperatorIdentity:
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    raw = auth[len("Bearer "):].strip()
    parts = raw.split(":")
    operator_id = parts[0] if parts else "op"
    roles = parts[1].split(",") if len(parts) > 1 else []
    return OperatorIdentity(operator_id=operator_id, roles=roles, claims={})


def _require_read_role(identity: OperatorIdentity) -> None:
    if not identity or not identity.roles:
        raise HTTPException(status_code=403, detail="Forbidden")


def _require_write_role(identity: OperatorIdentity) -> None:
    if not identity or not identity.roles or "operator" not in identity.roles:
        raise HTTPException(status_code=403, detail="Forbidden: operator role required")


def _bff_error(status_code: int, code: Any, message: str, reason: Optional[str] = None, **kwargs: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail=f"{code}: {message}")


@contextmanager
def _client(*, seeded: bool = False) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        read_store = _CommitteeMemoReadStore(_SEED_SESSIONS if seeded else None)
        cmd_store = CommandStore(os.path.join(td, "commands.jsonl"))
        idempotency_store: Dict[str, Any] = {}

        app = FastAPI(title="Agora Committee Memo Contract")
        app.include_router(
            create_agora_router(
                extract_identity=_extract_identity,
                require_read_role=_require_read_role,
                require_write_role=_require_write_role,
                require_operator_role=_require_write_role,
                bff_error=_bff_error,
                utc_now=_utc_now,
                get_read_store=lambda: read_store,
                get_command_store=lambda: cmd_store,
                idempotency_store=idempotency_store,
                sync_servant_agent=lambda p: dict(p),
            )
        )
        app.include_router(
            create_governance_router(
                get_read_store=lambda: read_store,
                extract_identity=_extract_identity,
                require_read_role=_require_read_role,
                require_operator_role=_require_write_role,
            )
        )
        yield TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# GET /bff/agora/committee/sessions/{id}/memos  (list)
# --------------------------------------------------------------------------- #


def test_ask_004_list_memos_returns_envelope() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions/committee-memo-001/memos", headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert "page_info" in body
        assert "meta" in body
        assert "agora_committee_session_memos" in body["meta"]["surfaces"]


def test_ask_004_list_memos_empty_for_new_session() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions/committee-memo-001/memos", headers=AUTH)
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []
        assert resp.json()["page_info"]["total"] == 0


def test_ask_004_list_memos_404_for_missing_session() -> None:
    with _client() as client:
        resp = client.get("/bff/agora/committee/sessions/nonexistent-999/memos", headers=AUTH)
        assert resp.status_code == 404, resp.text


def test_ask_004_list_memos_404_for_ask_session() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions/ask-session-001/memos", headers=AUTH)
        assert resp.status_code == 404, resp.text


def test_ask_004_list_memos_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.get("/bff/agora/committee/sessions/committee-memo-001/memos")
        assert resp.status_code == 401, resp.text


def test_ask_004_list_memos_shows_submitted_memos() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-list-test-001", "summary": "Committee analysis"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.get("/bff/agora/committee/sessions/committee-memo-001/memos", headers=AUTH)
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["memo_id"] == "memo-list-test-001"


# --------------------------------------------------------------------------- #
# POST /bff/agora/committee/sessions/{id}/memos  (submit)
# --------------------------------------------------------------------------- #


def test_ask_004_submit_memo_returns_201() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-submit-001", "summary": "Risk analysis complete"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 201, resp.text


def test_ask_004_submit_memo_response_shape() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={
                "memoId": "memo-shape-001",
                "memoType": "committee_summary",
                "summary": "All signals reviewed",
                "recommendations": ["Approve deployment"],
            },
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        data = body["data"]
        assert data["memo_id"] == "memo-shape-001"
        assert data["memo_type"] == "committee_summary"
        assert data["status"] == "draft"
        assert data["linked_session_id"] == "committee-memo-001"
        assert data["recommendations"] == ["Approve deployment"]
        assert data["session_to_memo_mapping"]["source_session_id"] == "committee-memo-001"
        assert data["session_to_memo_mapping"]["memo_id"] == "memo-shape-001"
        assert data["session_to_memo_mapping"]["mapping_status"] == "draft"


def test_ask_004_submit_memo_autogenerates_memo_id() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"summary": "No explicit memo id"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 201, resp.text
        memo_id = resp.json()["data"]["memo_id"]
        assert memo_id.startswith("memo-")


def test_ask_004_submit_memo_404_for_missing_session() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/agora/committee/sessions/nonexistent-999/memos",
            json={"summary": "Should fail"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_submit_memo_404_for_ask_session() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/ask-session-001/memos",
            json={"summary": "Should fail — not a committee session"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_submit_memo_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"summary": "No auth"},
        )
        assert resp.status_code == 401, resp.text


def test_ask_004_submit_memo_idempotency_replays() -> None:
    with _client(seeded=True) as client:
        key = _idem()
        payload = {"memoId": "memo-idem-001", "summary": "Idempotent"}
        headers = {**AUTH, "Idempotency-Key": key}
        r1 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json=payload,
            headers=headers,
        )
        r2 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json=payload,
            headers=headers,
        )
        assert r1.status_code == 201, r1.text
        assert r2.status_code in {200, 201}, r2.text
        assert r1.json()["data"]["memo_id"] == r2.json()["data"]["memo_id"]


def test_ask_004_submit_memo_conflicts_on_duplicate_memo_id_with_new_key() -> None:
    with _client(seeded=True) as client:
        payload = {"memoId": "memo-duplicate-001", "summary": "First draft"}
        first = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json=payload,
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        second = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={**payload, "summary": "Different draft"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 409, second.text


def test_ask_004_submit_memo_rejects_body_idempotency_key() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"summary": "bad", "idempotency_key": "should-reject"},
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text


# --------------------------------------------------------------------------- #
# GET /bff/agora/committee/sessions/{id}/memos/{memoId}  (review)
# --------------------------------------------------------------------------- #


def test_ask_004_memo_detail_returns_200() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-detail-001", "summary": "Detail test"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-detail-001",
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text


def test_ask_004_memo_detail_shape() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={
                "memoId": "memo-shape-detail-001",
                "memoType": "committee_summary",
                "summary": "Full detail shape",
                "recommendations": ["rec-1", "rec-2"],
            },
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-shape-detail-001",
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        data = body["data"]
        assert data["memo_id"] == "memo-shape-detail-001"
        assert data["memo_type"] == "committee_summary"
        assert data["status"] == "draft"
        assert data["linked_session_id"] == "committee-memo-001"
        assert data["summary"] == "Full detail shape"
        assert data["recommendations"] == ["rec-1", "rec-2"]
        assert "agora_committee_memo_detail" in body["meta"]["surfaces"]


def test_ask_004_memo_detail_404_for_missing_memo() -> None:
    with _client(seeded=True) as client:
        resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-does-not-exist",
            headers=AUTH,
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_memo_detail_404_for_wrong_session() -> None:
    with _client(seeded=True) as client:
        # Create a second committee session
        client.post(
            "/bff/agora/committee/sessions",
            json={"sessionId": "committee-other-001", "title": "Other session"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        # Submit memo to first session
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-wrong-session-001", "summary": "Belongs to first session"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        # Fetch from second session should 404
        resp = client.get(
            "/bff/agora/committee/sessions/committee-other-001/memos/memo-wrong-session-001",
            headers=AUTH,
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_memo_detail_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-any",
        )
        assert resp.status_code == 401, resp.text


# --------------------------------------------------------------------------- #
# POST /bff/agora/committee/sessions/{id}/memos/{memoId}/publish
# --------------------------------------------------------------------------- #


def test_ask_004_publish_memo_returns_200() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-001", "summary": "Ready to publish"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 200, resp.text


def test_ask_004_publish_memo_sets_status_published() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-status-001", "summary": "Publish status test"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-status-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "published"
        assert data["lifecycle_state"] == "published"
        assert data["published_at"] is not None
        assert data["session_to_memo_mapping"]["mapping_status"] == "active"


def test_ask_004_publish_memo_visible_in_registry() -> None:
    """Published memo should appear via GET /api/v1/consult/memos."""
    with _client(seeded=True) as client:
        memo_id = f"memo-registry-{uuid.uuid4().hex[:8]}"
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": memo_id, "summary": "Registry visible"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        client.post(
            f"/bff/agora/committee/sessions/committee-memo-001/memos/{memo_id}/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        # Verify the memo is now in the /api/v1/consult/memos registry
        list_resp = client.get(
            "/api/v1/consult/memos?status=published",
            headers=AUTH,
        )
        assert list_resp.status_code == 200, list_resp.text
        memo_ids = [item["memo_id"] for item in list_resp.json().get("items", [])]
        assert memo_id in memo_ids


def test_ask_004_publish_memo_detail_still_accessible() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-detail-001", "summary": "Post-publish detail"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-detail-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        detail_resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-detail-001",
            headers=AUTH,
        )
        assert detail_resp.status_code == 200, detail_resp.text
        assert detail_resp.json()["data"]["status"] == "published"


def test_ask_004_publish_memo_appears_in_session_memo_list() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-list-001", "summary": "In list after publish"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-list-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        list_resp = client.get(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            headers=AUTH,
        )
        items = list_resp.json()["items"]
        memo = next((m for m in items if m["memo_id"] == "memo-pub-list-001"), None)
        assert memo is not None
        assert memo["status"] == "published"


def test_ask_004_publish_memo_404_for_missing_memo() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-nonexistent/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_publish_memo_404_for_wrong_session() -> None:
    with _client(seeded=True) as client:
        # Create second committee session
        client.post(
            "/bff/agora/committee/sessions",
            json={"sessionId": "committee-other-pub-001", "title": "Other session"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-wrong-pub-001", "summary": "Belongs to first session"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.post(
            "/bff/agora/committee/sessions/committee-other-pub-001/memos/memo-wrong-pub-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 404, resp.text


def test_ask_004_publish_memo_requires_auth() -> None:
    with _client(seeded=True) as client:
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-any/publish",
            json={},
        )
        assert resp.status_code == 401, resp.text


def test_ask_004_publish_memo_idempotency_replays() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-idem-001", "summary": "Idempotent publish"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        key = _idem()
        headers = {**AUTH, "Idempotency-Key": key}
        r1 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-idem-001/publish",
            json={},
            headers=headers,
        )
        r2 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-idem-001/publish",
            json={},
            headers=headers,
        )
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r1.json()["data"]["memo_id"] == r2.json()["data"]["memo_id"]


def test_ask_004_publish_memo_already_published_is_stable_with_new_key() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-pub-stable-001", "summary": "Stable publish"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        r1 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-stable-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        r2 = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-pub-stable-001/publish",
            json={},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r1.json()["data"]["published_at"] == r2.json()["data"]["published_at"]


def test_ask_004_publish_memo_rejects_body_idempotency_key() -> None:
    with _client(seeded=True) as client:
        client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-body-idem-001", "summary": "Body idem test"},
            headers={**AUTH, "Idempotency-Key": _idem()},
        )
        resp = client.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos/memo-body-idem-001/publish",
            json={"idempotency_key": "reject-me"},
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text


def test_ask_004_nested_client_isolation_and_replay_behavior() -> None:
    """Verify nested client contexts do not bleed stores or clear outer idempotency cache."""
    with _client(seeded=True) as outer:
        resp = outer.get("/bff/agora/committee/sessions/committee-memo-001/memos", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["items"] == []

        key = _idem()
        post_resp = outer.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-nested-001", "summary": "Nested test memo"},
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert post_resp.status_code == 201

        resp_with_memo = outer.get("/bff/agora/committee/sessions/committee-memo-001/memos", headers=AUTH)
        assert resp_with_memo.status_code == 200
        assert len(resp_with_memo.json()["items"]) == 1

        with _client(seeded=False) as inner:
            inner_resp = inner.get("/bff/agora/committee/sessions/committee-memo-001/memos", headers=AUTH)
            assert inner_resp.status_code == 404

        resp_after = outer.get("/bff/agora/committee/sessions/committee-memo-001/memos", headers=AUTH)
        assert resp_after.status_code == 200
        assert len(resp_after.json()["items"]) == 1
        assert resp_after.json()["items"][0]["memo_id"] == "memo-nested-001"

        replay_resp = outer.post(
            "/bff/agora/committee/sessions/committee-memo-001/memos",
            json={"memoId": "memo-nested-001", "summary": "Nested test memo"},
            headers={**AUTH, "Idempotency-Key": key},
        )
        assert replay_resp.status_code == 201
        assert replay_resp.json()["data"]["memo_id"] == post_resp.json()["data"]["memo_id"]

        resp_final = outer.get("/bff/agora/committee/sessions/committee-memo-001/memos", headers=AUTH)
        assert len(resp_final.json()["items"]) == 1


