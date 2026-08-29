from __future__ import annotations

import copy
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main


OPERATOR_TOKEN = "Bearer op-2:operator"

_PATCH_CANDIDATE_FIELDS = [
    "title",
    "body",
    "tags",
    "linkedStrategyIds",
    "linkedPersonaIds",
    "visibility",
]


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class InMemoryAgoraJournalStore:
    """Narrow in-memory double for the two decision-journal mutation methods
    the ``PATCH /bff/agora/journal/{id}`` handler calls on ``read_store``.

    ``create_decision_journal_entry``, ``patch_decision_journal_entry``, and
    ``record_agora_audit_event`` were intentionally excluded from
    ``ReadSurfacePorts`` (see ``RETAINED_WRITES_DEFERRED_FROM_READ_SURFACE``
    in tests/test_read_surface_caller_migration.py), since canonical journal
    write ownership lives outside the BFF. This double reproduces the
    legacy read-surface store's exact local-dev-projection behavior for
    those two methods (project/patch/diff/idempotency-replay), without
    importing that legacy store or touching a JSON file on disk.
    """

    def __init__(self, entries: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self._entries: Dict[str, Dict[str, Any]] = copy.deepcopy(entries or {})
        self._idempotency_records: Dict[str, Dict[str, Any]] = {}
        self._audit_events: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _project(record: Dict[str, Any]) -> Dict[str, Any]:
        entry_id = str(record.get("id") or record.get("entry_id") or "").strip()
        timestamp = _utc_now_rfc3339()
        return {
            "id": entry_id,
            "title": str(record.get("title") or "").strip(),
            "body": str(record.get("body") or ""),
            "tags": list(record.get("tags") or []),
            "linkedStrategyIds": list(record.get("linkedStrategyIds") or []),
            "linkedPersonaIds": list(record.get("linkedPersonaIds") or []),
            "visibility": str(record.get("visibility") or "private").strip() or "private",
            "createdAt": str(record.get("createdAt") or timestamp),
            "updatedAt": str(record.get("updatedAt") or timestamp),
            "version": int(record.get("version") or 1),
            "createdBy": str(record.get("createdBy") or ""),
            "canonicalWriteAuthority": "agora_journal_service",
            "persistenceMode": str(record.get("persistenceMode") or "bff_local_dev_store"),
        }

    @staticmethod
    def _diff(before: Dict[str, Any], after: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
        changes = []
        for field in fields:
            if before.get(field) == after.get(field):
                continue
            changes.append(
                {
                    "field": field,
                    "before": copy.deepcopy(before.get(field)),
                    "after": copy.deepcopy(after.get(field)),
                }
            )
        return {
            "changedFields": [change["field"] for change in changes],
            "changes": changes,
            "before": copy.deepcopy(before),
            "after": copy.deepcopy(after),
        }

    def list_decision_journal_entries(self) -> List[Dict[str, Any]]:
        entries = [self._project(record) for record in self._entries.values()]
        entries.sort(key=lambda entry: str(entry.get("updatedAt") or ""), reverse=True)
        return copy.deepcopy(entries)

    def patch_decision_journal_entry(
        self,
        entry_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        correlation_id: Optional[str],
        idempotency_key: str,
        request_hash: str,
        patched_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        clean_entry_id = str(entry_id or "").strip()
        if not clean_entry_id:
            return None

        existing_idem = self._idempotency_records.get(idempotency_key)
        if isinstance(existing_idem, dict):
            if existing_idem.get("request_hash") != request_hash:
                return {
                    "status": "conflict",
                    "existing_patch_id": existing_idem.get("patch_id"),
                    "entry": existing_idem.get("entry"),
                    "audit": existing_idem.get("audit"),
                }
            if isinstance(existing_idem.get("entry"), dict) and isinstance(existing_idem.get("audit"), dict):
                return {
                    "status": "replayed",
                    "entry": copy.deepcopy(existing_idem["entry"]),
                    "audit": copy.deepcopy(existing_idem["audit"]),
                }

        stored = self._entries.get(clean_entry_id)
        if not isinstance(stored, dict):
            return None

        timestamp = patched_at or _utc_now_rfc3339()
        before = self._project(stored)
        after = copy.deepcopy(before)
        for field in _PATCH_CANDIDATE_FIELDS:
            if field not in patch:
                continue
            value = patch[field]
            if value is None and field in {"tags", "linkedStrategyIds", "linkedPersonaIds"}:
                after[field] = []
            elif value is not None:
                after[field] = copy.deepcopy(value)

        after["updatedAt"] = timestamp
        after["version"] = int(before.get("version") or 0) + 1
        after["canonicalWriteAuthority"] = "agora_journal_service"
        after["persistenceMode"] = "bff_local_dev_store"

        diff = self._diff(before, after, _PATCH_CANDIDATE_FIELDS)
        audit_id = f"aud-agora-journal-{uuid.uuid4().hex[:12]}"
        audit = {
            "auditId": audit_id,
            "action": "agora.journal.merge_patch",
            "target": {"type": "DecisionJournalEntry", "id": clean_entry_id},
            "actorId": actor_id,
            "correlationId": correlation_id,
            "idempotencyKey": idempotency_key,
            "recordedAt": timestamp,
            "canonicalWriteAuthority": "agora_journal_service",
            "persistenceMode": "bff_local_dev_store",
            "degraded": True,
            "diff": diff,
        }

        self._entries[clean_entry_id] = copy.deepcopy(after)
        self._audit_events[audit_id] = copy.deepcopy(audit)
        self._idempotency_records[idempotency_key] = {
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "patch_id": audit_id,
            "status": "succeeded",
            "entry": copy.deepcopy(after),
            "audit": copy.deepcopy(audit),
        }
        return {"status": "updated", "entry": after, "audit": audit}

    def list_agora_journal_audit_events(self) -> Dict[str, Dict[str, Any]]:
        """Expose the recorded audit events for test assertions.

        Replaces the old approach of re-reading the on-disk JSON snapshot's
        ``agora_journal_audit_events`` key: there is no JSON file backing
        this double, so the equivalent proof is reading the audit record
        straight back out of the double's own in-memory state.
        """
        return copy.deepcopy(self._audit_events)


def _seeded_store() -> InMemoryAgoraJournalStore:
    return InMemoryAgoraJournalStore(
        {
            "journal-001": {
                "id": "journal-001",
                "title": "Original decision",
                "body": "Initial body",
                "tags": ["risk.review"],
                "linkedStrategyIds": ["strategy-alpha"],
                "linkedPersonaIds": ["persona-alpha"],
                "visibility": "private",
                "createdAt": "2026-05-07T12:00:00Z",
                "updatedAt": "2026-05-07T12:00:00Z",
                "version": 1,
            }
        }
    )


def _headers(key: str, *, content_type: str = "application/merge-patch+json") -> dict[str, str]:
    return {
        "Authorization": OPERATOR_TOKEN,
        "Idempotency-Key": key,
        "X-Correlation-Id": "corr-journal-001",
        "Content-Type": content_type,
    }


def test_agora_journal_patch_rejects_non_merge_patch_content_type() -> None:
    original_store = bff_main.read_store
    bff_main.read_store = _seeded_store()
    client = TestClient(bff_main.app)

    try:
        response = client.patch(
            "/bff/agora/journal/journal-001",
            headers=_headers("idem-journal-content-type", content_type="application/json"),
            json={"title": "Updated decision"},
        )

        assert response.status_code == 415, response.text
        detail = response.json()
        assert detail["error"]["code"] == "VALIDATION_FAILED"
        assert detail["error"]["details"]["precondition_failed"] == "content_type"
    finally:
        bff_main.read_store = original_store


def test_agora_journal_patch_rejects_body_idempotency_key() -> None:
    original_store = bff_main.read_store
    bff_main.read_store = _seeded_store()
    client = TestClient(bff_main.app)

    try:
        response = client.patch(
            "/bff/agora/journal/journal-001",
            headers=_headers("idem-journal-body-idempotency"),
            json={
                "title": "Updated decision",
                "idempotencyKey": "must-not-be-here",
            },
        )

        assert response.status_code == 400, response.text
        detail = response.json()
        assert detail["error"]["code"] == "VALIDATION_FAILED"
        assert detail["error"]["details"]["precondition_failed"] == "body_idempotency_key"
    finally:
        bff_main.read_store = original_store


def test_agora_journal_patch_returns_data_and_audit_diff() -> None:
    original_store = bff_main.read_store
    store = _seeded_store()
    bff_main.read_store = store
    client = TestClient(bff_main.app)

    try:
        response = client.patch(
            "/bff/agora/journal/journal-001",
            headers=_headers("idem-journal-success"),
            json={
                "title": "Approved rollout notes",
                "body": "The committee approved the paper rollout.",
                "tags": ["paper.rollout", "committee-review"],
                "linkedStrategyIds": ["strategy-alpha", "strategy-beta"],
                "visibility": "team",
            },
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "completed"
        assert payload["data"]["id"] == "journal-001"
        assert payload["data"]["title"] == "Approved rollout notes"
        assert payload["data"]["linkedStrategyIds"] == ["strategy-alpha", "strategy-beta"]
        assert payload["data"]["canonicalWriteAuthority"] == "agora_journal_service"
        assert payload["data"]["persistenceMode"] == "bff_local_dev_store"

        audit = payload["meta"]["audit"]
        assert audit["action"] == "agora.journal.merge_patch"
        assert audit["correlationId"] == "corr-journal-001"
        assert audit["degraded"] is True
        assert audit["diff"]["before"]["title"] == "Original decision"
        assert audit["diff"]["after"]["title"] == "Approved rollout notes"
        assert set(audit["diff"]["changedFields"]) >= {
            "title",
            "body",
            "tags",
            "linkedStrategyIds",
            "visibility",
        }

        audit_records = store.list_agora_journal_audit_events()
        assert len(audit_records) == 1
        stored_audit = next(iter(audit_records.values()))
        assert stored_audit["diff"]["before"]["body"] == "Initial body"
        assert stored_audit["diff"]["after"]["body"] == "The committee approved the paper rollout."
    finally:
        bff_main.read_store = original_store


def test_agora_journal_patch_idempotency_conflict_rejected() -> None:
    original_store = bff_main.read_store
    bff_main.read_store = _seeded_store()
    client = TestClient(bff_main.app)

    try:
        headers = _headers("idem-journal-conflict")
        first = client.patch(
            "/bff/agora/journal/journal-001",
            headers=headers,
            json={"title": "First patch"},
        )
        second = client.patch(
            "/bff/agora/journal/journal-001",
            headers=headers,
            json={"title": "Different patch"},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 409, second.text
        detail = second.json()
        assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert detail["error"]["details"]["precondition_failed"] == "idempotency_conflict"
    finally:
        bff_main.read_store = original_store
