"""Independent persistent write owner for Decision Journal entries.

Decision Journal entries never had a real owner. The BFF's ``read_store.py``
degraded path (see
``docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-008-agora-journal-merge-patch.md``)
persists them to a ``bff_local_dev_store`` overlay while asserting
``canonicalWriteAuthority=agora_journal_service`` -- a claim nothing actually
backed. This module is that real owner.

It reuses the same durable-store posture the rest of the governance service
already uses for freeze orders and rollbacks
(:mod:`services.governance.record_store`): a JSON file on disk in dev, a
Postgres-owned table in staging/production. There is no in-memory dict, no
local overlay, and no BFF/``read_store`` import anywhere in this module --
every entry, idempotency record, and audit event is written through and read
back from a real owner store.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .record_store import GovernanceRecordStore, build_governance_record_store

CANONICAL_WRITE_AUTHORITY = "governance-decision-journal-svc"

_ENTRY_ID_FIELDS: Sequence[str] = ("id", "entry_id")
_IDEMPOTENCY_ID_FIELDS: Sequence[str] = ("idempotency_key",)
_AUDIT_ID_FIELDS: Sequence[str] = ("audit_id",)

_PATCHABLE_FIELDS: Sequence[str] = (
    "title",
    "body",
    "tags",
    "linkedStrategyIds",
    "linkedPersonaIds",
    "visibility",
)
_LIST_FIELDS = {"tags", "linkedStrategyIds", "linkedPersonaIds"}

_MAX_CAS_ATTEMPTS = 8
_TITLE_MAX_LENGTH = 160
_BODY_MAX_LENGTH = 20000


class DecisionJournalValidationError(ValueError):
    """Raised when a decision journal write violates the field contract."""


class DecisionJournalConcurrencyError(RuntimeError):
    """Raised when a patch could not commit after retrying compare-and-set."""


def _persistence_mode() -> str:
    backend = os.getenv("GOVERNANCE_STORE_BACKEND", "json").strip().lower()
    return "governance_postgres_store" if backend == "postgres" else "governance_json_store"


def _validate_title(title: Any) -> str:
    clean = str(title if title is not None else "").strip()
    if not clean or len(clean) > _TITLE_MAX_LENGTH:
        raise DecisionJournalValidationError(
            f"title must be 1-{_TITLE_MAX_LENGTH} characters"
        )
    return clean


def _validate_body(body: Any) -> str:
    text = str(body if body is not None else "")
    if len(text) > _BODY_MAX_LENGTH:
        raise DecisionJournalValidationError(f"body must be at most {_BODY_MAX_LENGTH} characters")
    return text


class DecisionJournalStores:
    """Bundle of the durable owner stores backing decision journal writes."""

    def __init__(
        self,
        *,
        entries: GovernanceRecordStore,
        idempotency: GovernanceRecordStore,
        audit: GovernanceRecordStore,
    ) -> None:
        self.entries = entries
        self.idempotency = idempotency
        self.audit = audit


def build_decision_journal_stores(data_dir: str | Path) -> DecisionJournalStores:
    """Build the durable stores backing decision journal writes.

    Uses ``GOVERNANCE_STORE_BACKEND`` (``json`` for dev, ``postgres`` for
    staging/production) -- the same posture already governing freeze orders
    and rollbacks -- so this owner never silently downgrades to a
    process-local dict when a durable backend is configured.
    """

    base = Path(data_dir)
    entries = build_governance_record_store(
        base / "decision_journal_entries.json",
        table="governance.decision_journal_entries",
        id_fields=_ENTRY_ID_FIELDS,
    )
    idempotency = build_governance_record_store(
        base / "decision_journal_idempotency.json",
        table="governance.decision_journal_idempotency",
        id_fields=_IDEMPOTENCY_ID_FIELDS,
    )
    audit = build_governance_record_store(
        base / "decision_journal_audit.json",
        table="governance.decision_journal_audit",
        id_fields=_AUDIT_ID_FIELDS,
    )
    return DecisionJournalStores(entries=entries, idempotency=idempotency, audit=audit)


def _project(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(record.get("id") or ""),
        "title": str(record.get("title") or ""),
        "body": str(record.get("body") or ""),
        "tags": list(record.get("tags") or []),
        "linkedStrategyIds": list(record.get("linkedStrategyIds") or []),
        "linkedPersonaIds": list(record.get("linkedPersonaIds") or []),
        "visibility": str(record.get("visibility") or "private"),
        "createdAt": str(record.get("createdAt") or ""),
        "updatedAt": str(record.get("updatedAt") or ""),
        "version": int(record.get("version") or 1),
        "createdBy": str(record.get("createdBy") or ""),
        "canonicalWriteAuthority": CANONICAL_WRITE_AUTHORITY,
        "persistenceMode": str(record.get("persistenceMode") or _persistence_mode()),
    }


def create_entry(
    stores: DecisionJournalStores,
    *,
    entry_id: str,
    title: str,
    body: str,
    actor_id: str,
    created_at: str,
    tags: Optional[List[str]] = None,
    linked_strategy_ids: Optional[List[str]] = None,
    linked_persona_ids: Optional[List[str]] = None,
    visibility: str = "private",
) -> Dict[str, Any]:
    """Create a decision journal entry, persisted through the owner store.

    Idempotent by ``entry_id``: a second create for an id that already exists
    returns the canonical persisted record instead of silently overwriting it.
    """

    clean_id = str(entry_id or "").strip()
    if not clean_id:
        raise DecisionJournalValidationError("entry_id is required")

    record = {
        "id": clean_id,
        "title": _validate_title(title),
        "body": _validate_body(body),
        "tags": list(tags or []),
        "linkedStrategyIds": list(linked_strategy_ids or []),
        "linkedPersonaIds": list(linked_persona_ids or []),
        "visibility": str(visibility or "private"),
        "createdAt": created_at,
        "updatedAt": created_at,
        "version": 1,
        "createdBy": str(actor_id or ""),
        "canonicalWriteAuthority": CANONICAL_WRITE_AUTHORITY,
        "persistenceMode": _persistence_mode(),
    }
    _inserted, canonical = stores.entries.insert_if_absent(record)
    return _project(canonical)


def get_entry(stores: DecisionJournalStores, entry_id: str) -> Optional[Dict[str, Any]]:
    record = stores.entries.get(str(entry_id or "").strip())
    return _project(record) if record is not None else None


def list_entries(stores: DecisionJournalStores) -> List[Dict[str, Any]]:
    entries = [_project(record) for record in stores.entries.list_all()]
    entries.sort(key=lambda entry: (entry.get("updatedAt") or entry.get("createdAt") or ""), reverse=True)
    return entries


def _diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    changes = []
    for field in _PATCHABLE_FIELDS:
        if before.get(field) == after.get(field):
            continue
        changes.append({"field": field, "before": before.get(field), "after": after.get(field)})
    return {
        "changedFields": [change["field"] for change in changes],
        "changes": changes,
        "before": before,
        "after": after,
    }


def patch_entry(
    stores: DecisionJournalStores,
    entry_id: str,
    *,
    patch: Dict[str, Any],
    actor_id: str,
    idempotency_key: str,
    request_hash: str,
    patched_at: str,
    correlation_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Apply a merge patch to a decision journal entry.

    Returns ``None`` when the entry does not exist, a ``conflict`` result
    when ``idempotency_key`` was already used with a different
    ``request_hash``, a ``replayed`` result when the identical request was
    already applied, or an ``updated`` result on a fresh, durably persisted
    write. Concurrent patches are resolved with compare-and-set against the
    owner store, never a local lock.
    """

    clean_id = str(entry_id or "").strip()
    if not clean_id:
        return None

    existing_idem = stores.idempotency.get(idempotency_key)
    if isinstance(existing_idem, dict):
        if existing_idem.get("request_hash") != request_hash:
            return {
                "status": "conflict",
                "existing_patch_id": existing_idem.get("patch_id"),
                "entry": existing_idem.get("entry"),
                "audit": existing_idem.get("audit"),
            }
        return {
            "status": "replayed",
            "entry": existing_idem.get("entry"),
            "audit": existing_idem.get("audit"),
        }

    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    for _attempt in range(_MAX_CAS_ATTEMPTS):
        stored = stores.entries.get(clean_id)
        if stored is None:
            return None
        before = dict(stored)
        candidate = dict(before)
        for field in _PATCHABLE_FIELDS:
            if field not in patch:
                continue
            value = patch[field]
            if value is None and field in _LIST_FIELDS:
                candidate[field] = []
            elif value is not None:
                candidate[field] = value
        if "title" in patch and patch["title"] is not None:
            candidate["title"] = _validate_title(candidate["title"])
        if "body" in patch and patch["body"] is not None:
            candidate["body"] = _validate_body(candidate["body"])
        candidate["updatedAt"] = patched_at
        candidate["version"] = int(before.get("version") or 0) + 1
        candidate["canonicalWriteAuthority"] = CANONICAL_WRITE_AUTHORITY
        candidate["persistenceMode"] = _persistence_mode()

        updated, canonical = stores.entries.compare_and_set(before, candidate)
        if updated:
            after = canonical if canonical is not None else candidate
            break
    else:
        raise DecisionJournalConcurrencyError(
            f"decision journal entry {clean_id} could not be updated after "
            f"{_MAX_CAS_ATTEMPTS} compare-and-set attempts"
        )

    before_projected = _project(before)
    after_projected = _project(after)
    diff = _diff(before_projected, after_projected)
    audit_id = f"aud-decision-journal-{uuid.uuid4().hex[:12]}"
    audit = {
        "auditId": audit_id,
        "action": "governance.decision_journal.merge_patch",
        "target": {"type": "DecisionJournalEntry", "id": clean_id},
        "actorId": actor_id,
        "correlationId": correlation_id,
        "idempotencyKey": idempotency_key,
        "recordedAt": patched_at,
        "canonicalWriteAuthority": CANONICAL_WRITE_AUTHORITY,
        "persistenceMode": _persistence_mode(),
        "diff": diff,
    }
    stores.audit.put({"audit_id": audit_id, **audit})
    stores.idempotency.insert_if_absent(
        {
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "patch_id": audit_id,
            "status": "succeeded",
            "entry": after_projected,
            "audit": audit,
        }
    )
    return {"status": "updated", "entry": after_projected, "audit": audit}


def list_audit_events(stores: DecisionJournalStores, *, entry_id: Optional[str] = None) -> List[Dict[str, Any]]:
    events = list(stores.audit.list_all())
    if entry_id:
        events = [event for event in events if (event.get("target") or {}).get("id") == entry_id]
    events.sort(key=lambda event: str(event.get("recordedAt") or ""), reverse=True)
    return events
