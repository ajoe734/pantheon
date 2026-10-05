"""Agora domain service and orchestration facade.

Encapsulates Agora session lifecycle, quick ask assistant coordination, insight
and institutional memory management, action command submission, idempotency,
journal merge-patch validation, signal/feedback lifecycle, committee sessions,
and data projections without importing or coupling to main.py.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
import uuid
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..models import (
    ActionCommandStatus,
    CommandResponse,
    CommandStatus,
    CommandType,
    DecisionJournalEntryDTO,
    ErrorCode,
    JournalEntryMergePatch,
    ObjectType,
    OperatorIdentity,
    TargetObject,
    utc_now as default_utc_now,
)
from .identity.scope import resolve_canonical_agora_scope

from services.control_plane.bff.ports import (
    OpenClawOpsClient,
    OpenClawOpsClientError,
    ReadSurfacePorts,
    create_read_surface_ports,
)

try:
    from services.foundation import IdempotencyRecord
except ImportError:
    class IdempotencyRecord:  # type: ignore
        @classmethod
        def reserve(cls, **kwargs: Any) -> Any:
            return cls(**kwargs)

        def __init__(self, **kwargs: Any) -> None:
            self._data = kwargs

        def to_dict(self) -> Dict[str, Any]:
            return dict(self._data)

try:
    from services.governance.decision_journal import (
        DecisionJournalAccessDeniedError,
        DecisionJournalCollisionError,
        DecisionJournalConcurrencyError,
        DecisionJournalValidationError,
    )
except ImportError:
    class DecisionJournalCollisionError(ValueError): pass  # type: ignore[no-redef]
    class DecisionJournalAccessDeniedError(PermissionError): pass  # type: ignore[no-redef]
    class DecisionJournalConcurrencyError(RuntimeError): pass  # type: ignore[no-redef]
    class DecisionJournalValidationError(ValueError): pass  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

_JOURNAL_MERGE_PATCH_CONTENT_TYPE = "application/merge-patch+json"
_JOURNAL_PATCH_FIELDS = {
    "title",
    "body",
    "tags",
    "linkedStrategyIds",
    "linkedPersonaIds",
    "visibility",
}
_JOURNAL_TAG_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_JOURNAL_WRITE_ROLES = {"operator", "reviewer", "approver", "admin"}
_JOURNAL_VISIBILITY_CAPABILITY = {
    "private": "agora.journal.write.private",
    "team": "agora.journal.write.team",
    "committee": "agora.journal.write.committee",
    "public": "agora.journal.write.public",
}
_JOURNAL_VISIBILITY_ROLES = {
    "private": {"operator", "reviewer", "approver", "admin"},
    "team": {"operator", "reviewer", "approver", "admin"},
    "committee": {"reviewer", "approver", "admin"},
    "public": {"admin"},
}
_AGORA_SIGNAL_SEVERITIES = {"info", "warn", "alert"}
_AGORA_SIGNAL_WRITE_ROLES = {"analyst", "operator", "reviewer", "approver", "admin"}
_AGORA_BULK_FEEDBACK_ROLES = {"analyst", "operator", "reviewer", "approver", "admin"}
_AGORA_BULK_FEEDBACK_VERDICTS = {"useful", "noise", "false_positive"}
_AGORA_SIGNAL_DECISIONS = {"agree", "disagree", "flag_suspicious"}
_AGORA_EVIDENCE_MAX_FILES = 10
_AGORA_EVIDENCE_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
_AGORA_EVIDENCE_MAX_TOTAL_SIZE_BYTES = 25 * 1024 * 1024
_AGORA_EVIDENCE_ALLOWED_MIMES = {
    "application/json",
    "text/csv",
    "text/plain",
    "text/markdown",
    "application/pdf",
    "image/png",
    "image/jpeg",
}


def _default_stable_json_hash(payload: Any) -> str:
    import hashlib
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _default_page_slice(
    items: List[Dict[str, Any]],
    page_token: Optional[str],
    page_size: int,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    start = 0
    if page_token:
        try:
            start = int(page_token)
        except ValueError:
            start = 0
    end = start + page_size
    sliced = items[start:end]
    next_token = str(end) if end < len(items) else None
    return sliced, next_token


def _default_read_surface_meta(
    dataset: str,
    surface_key: str,
    *,
    snapshot_at: str,
    total: Optional[int] = None,
    surface: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    surf = surface or {"status": "ok", "source": "bff_local"}
    surfaces = {surface_key: surf}
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "dataset": dataset,
        "surface": surface_key,
        "surfaces": surfaces,
    }
    if total is not None:
        meta["total"] = total
    status = surf.get("status")
    label = surface_key.replace("_", " ")
    if status == "degraded":
        meta["degradation"] = {"reason": f"{label} is degraded and may be stale."}
    elif status == "unavailable":
        meta["degradation"] = {"reason": f"{label} is currently unavailable."}
    return meta


def _dedupe_nonblank_strings(raw: Any) -> List[str]:
    if not isinstance(raw, (list, tuple, set)):
        raw = [raw]
    seen = set()
    result = []
    for item in raw:
        clean = str(item or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


class AgoraService:
    """Core domain service for Agora operations, decoupled from main.py."""

    def __init__(
        self,
        *,
        get_read_store: Optional[Callable[[], Any]] = None,
        get_audit_store: Optional[Callable[[], Any]] = None,
        get_command_store: Optional[Callable[[], Any]] = None,
        idempotency_store: Optional[Dict[str, Any]] = None,
        sse_buffers: Optional[Dict[str, Any]] = None,
        sse_subscribers: Optional[Dict[str, Any]] = None,
        assistant_ask_enabled: Optional[Callable[[], bool]] = None,
        assistant_build_context_pack: Optional[Callable[..., Any]] = None,
        get_assistant_session_store: Optional[Callable[[], Any]] = None,
        get_assistant_transcript_store: Optional[Callable[[], Any]] = None,
        openclaw_ops_client_factory: Optional[Callable[[], Any]] = None,
        utc_now: Optional[Callable[[], str]] = None,
        bff_error: Optional[Callable[..., HTTPException]] = None,
        publish_event_fn: Optional[Callable[..., None]] = None,
        handle_sse_stream: Optional[Callable[..., Any]] = None,
        journal_write_owner: Optional[Any] = None,
        get_journal_write_owner: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._get_read_store = get_read_store or (lambda: None)
        self._get_audit_store = get_audit_store or (lambda: None)
        self._get_command_store = get_command_store or (lambda: None)
        self._get_journal_write_owner = get_journal_write_owner or (lambda: journal_write_owner)
        self._idempotency = idempotency_store if idempotency_store is not None else {}
        self._sse_buffers = sse_buffers if sse_buffers is not None else {"ask": [], "signal": [], "journal": [], "inbox": []}
        self._sse_subscribers = sse_subscribers if sse_subscribers is not None else {"ask": [], "signal": [], "journal": [], "inbox": []}
        self._assistant_ask_enabled = assistant_ask_enabled or (lambda: False)
        self._assistant_build_context_pack = assistant_build_context_pack
        self._get_assistant_session_store = get_assistant_session_store or (lambda: None)
        self._get_assistant_transcript_store = get_assistant_transcript_store or (lambda: None)
        self._openclaw_ops_client_factory = openclaw_ops_client_factory
        self.utc_now = utc_now or default_utc_now
        self.bff_error = bff_error or self._default_bff_error
        self.publish_event_fn = publish_event_fn or self._default_publish_event
        self._handle_sse_stream = handle_sse_stream
        self._local_sessions: Dict[str, Dict[str, Any]] = {}
        self._local_session_messages: Dict[str, List[Dict[str, Any]]] = {}
        self._local_insights: Dict[str, Dict[str, Any]] = {}
        self._local_memory: Dict[str, Dict[str, Any]] = {}
        self._local_handoffs: Dict[str, Dict[str, Any]] = {}
        self._local_signals: Dict[str, Dict[str, Any]] = {}

    @property
    def read_store(self) -> Any:
        return self._get_read_store()

    @property
    def journal_write_owner(self) -> Any:
        if self._get_journal_write_owner is not None:
            owner = self._get_journal_write_owner()
            if owner is not None:
                return owner
        if self.read_store is not None and hasattr(self.read_store, "create_decision_journal_entry"):
            return self.read_store
        return None

    @property
    def audit_store(self) -> Any:
        return self._get_audit_store()

    def _record_agora_audit_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Write an Agora audit event through its mutation-owned store.

        The read-surface ports deliberately stay read-only.  The fallback keeps
        compatibility with focused unit-test doubles that still expose the
        legacy method, while production wiring always supplies ``audit_store``.
        """
        writer = self.audit_store
        if writer is not None and hasattr(writer, "record_agora_audit_event"):
            return writer.record_agora_audit_event(event)
        legacy = self.read_store
        if legacy is not None and hasattr(legacy, "record_agora_audit_event"):
            return legacy.record_agora_audit_event(event)
        return None

    @property
    def command_store(self) -> Any:
        return self._get_command_store()

    @staticmethod
    def _default_bff_error(
        status_code: int,
        code: ErrorCode | str,
        message: str,
        reason: str,
        precondition_failed: Optional[str] = None,
        suggestion: Optional[str] = None,
        details_extra: Optional[Dict[str, Any]] = None,
    ) -> HTTPException:
        code_val = code.value if isinstance(code, ErrorCode) else str(code)
        details: Dict[str, Any] = {"reason": reason}
        if precondition_failed:
            details["precondition_failed"] = precondition_failed
        if suggestion:
            details["suggestion"] = suggestion
        if details_extra:
            details.update(details_extra)
        return HTTPException(
            status_code=status_code,
            detail={"code": code_val, "message": message, "details": details},
        )

    def _default_publish_event(
        self,
        buffer: Any,
        subscribers: Any,
        event_type: str,
        data: Dict[str, Any],
    ) -> str:
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        event = {
            "id": event_id,
            "type": event_type,
            "event": event_type,
            "data": dict(data or {}),
            "timestamp": self.utc_now(),
        }
        if hasattr(buffer, "append"):
            buffer.append((event_id, event))
            if hasattr(buffer, "__len__") and len(buffer) > 200:
                if isinstance(buffer, list):
                    del buffer[: len(buffer) - 200]
        for sub in list(subscribers or []):
            try:
                if callable(sub):
                    sub(event)
                elif hasattr(sub, "put_nowait"):
                    sub.put_nowait(event)
            except Exception:
                pass
        return event_id

    # --- Idempotency & Helper Methods --- #

    def resolve_final_idempotency_key(
        self,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
    ) -> str:
        key = str(idempotency_key or x_idempotency_key or "").strip()
        if not key:
            raise self.bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "Request must include a non-empty Idempotency-Key or X-Idempotency-Key header",
                precondition_failed="Idempotency-Key",
            )
        return key

    def reject_body_idempotency_key(self, payload: Dict[str, Any]) -> None:
        body_key = "idempotencyKey" if "idempotencyKey" in payload else "idempotency_key" if "idempotency_key" in payload else None
        if body_key is not None:
            raise self.bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                f"{body_key} must not appear in the request body",
                (
                    "Final contract routes require idempotency via the Idempotency-Key header, "
                    "not the request body"
                ),
                precondition_failed="body_idempotency_key",
                suggestion=f"Remove {body_key} from the body and set the Idempotency-Key header",
            )

    def stable_json_hash(self, payload: Any) -> str:
        return _default_stable_json_hash(payload)

    def check_idempotency(self, resolved_key: str, request_hash: str) -> Optional[Dict[str, Any]]:
        existing = self._idempotency.get(resolved_key)
        if existing is None:
            return None
        if existing.get("request_hash") != request_hash:
            raise self.bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different Agora request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        return existing.get("result")

    def record_idempotency(self, resolved_key: str, request_hash: str, result: Dict[str, Any]) -> None:
        self._idempotency[resolved_key] = {"request_hash": request_hash, "result": result}

    def dry_run_success_response(
        self,
        data: Dict[str, Any],
        *,
        status_code: int = 200,
        snapshot_at: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        evidence_kind: Optional[str] = None,
        extra_meta: Optional[Dict[str, Any]] = None,
    ) -> JSONResponse:
        meta: Dict[str, Any] = {
            "snapshot_at": snapshot_at or self.utc_now(),
            "dryRun": True,
            "durable": False,
            "liveCapitalSideEffects": False,
        }
        if idempotency_key:
            meta["idempotency"] = {
                "key": idempotency_key,
                "idempotencyKey": idempotency_key,
                "replayed": False,
            }
        if evidence_kind:
            meta["evidenceKind"] = evidence_kind
            meta["evidence_kind"] = evidence_kind
        if extra_meta:
            meta.update(extra_meta)
        return JSONResponse(
            status_code=status_code,
            content=jsonable_encoder({"data": data, "meta": meta}),
        )

    def agora_required_text(self, payload: Dict[str, Any], *fields: str) -> str:
        for field in fields:
            clean = str(payload.get(field) or "").strip()
            if clean:
                return clean
        label = fields[0] if fields else "value"
        raise self.bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{label} is required",
            f"Agora request requires a non-empty {label}",
            precondition_failed=label,
        )

    def agora_list_response(
        self,
        *,
        dataset: str,
        surface_key: str,
        items: List[Dict[str, Any]],
        page_token: Optional[str],
        page_size: int,
        snapshot_at: str,
    ) -> Dict[str, Any]:
        total = len(items)
        page_items, next_page_token = _default_page_slice(items, page_token, page_size)
        surface = self.dataset_surface_status(dataset, snapshot_at=snapshot_at, has_data=bool(items))
        return {
            "data": page_items,
            "items": page_items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": _default_read_surface_meta(dataset, surface_key, snapshot_at=snapshot_at, total=total, surface=surface),
        }

    def dataset_surface_status(
        self,
        dataset: str,
        *,
        snapshot_at: Optional[str] = None,
        source: Optional[str] = None,
        has_data: Optional[bool] = None,
    ) -> Dict[str, Any]:
        store = self.read_store
        if store is not None and hasattr(store, "dataset_surface_status") and callable(store.dataset_surface_status):
            return store.dataset_surface_status(dataset, snapshot_at=snapshot_at or self.utc_now(), source=source, has_data=has_data)
        if source is None:
            if store is not None and hasattr(store, "dataset_source") and callable(store.dataset_source):
                source = store.dataset_source(dataset)
            else:
                source = "bff_local"
        surface: Dict[str, Any] = {"status": "ok", "source": source}
        if source == "local_snapshot":
            surface["status"] = "degraded"
            surface["note"] = "Served from local BFF snapshot fallback instead of a backend-owned read store."
            surface["staleness"] = {
                "served_from": "local_snapshot",
                "last_known_at": snapshot_at or self.utc_now(),
            }
        elif source == "missing":
            surface["status"] = "unavailable"
            surface["staleness"] = {
                "served_from": "unverifiable",
                "last_known_at": snapshot_at or self.utc_now(),
            }
        return surface

    def sem_read_records(self, dataset: str) -> tuple[str, List[Dict[str, Any]]]:
        store = self.read_store
        if store is not None and hasattr(store, "_read_dataset_records") and callable(store._read_dataset_records):
            records = [dict(item) for item in store._read_dataset_records(dataset) if isinstance(item, dict)]
            source_fn = getattr(store, "dataset_source", None)
            source = source_fn(dataset) if callable(source_fn) else ("local_snapshot" if records else "missing")
            if source == "missing" and records:
                source = "local_snapshot"
            return source, records

        data = getattr(store, "_data", {}) if store is not None else {}
        raw = data.get(dataset) if isinstance(data, dict) else None
        if isinstance(raw, dict):
            return ("local_snapshot" if raw else "missing", [dict(item) for item in raw.values() if isinstance(item, dict)])
        if isinstance(raw, list):
            return ("local_snapshot" if raw else "missing", [dict(item) for item in raw if isinstance(item, dict)])
        return "missing", []

    def sem_list_payload(self, dataset: str, surface_key: str, *, filter_mode: Optional[str] = None) -> Dict[str, Any]:
        source, records = self.sem_read_records(dataset)
        if filter_mode:
            records = [record for record in records if str(record.get("mode") or "") == filter_mode]
        snapshot_at = self.utc_now()
        surface = self.dataset_surface_status(
            dataset,
            snapshot_at=snapshot_at,
            source=source,
            has_data=(source != "missing"),
        )
        meta = _default_read_surface_meta(
            dataset,
            surface_key,
            snapshot_at=snapshot_at,
            total=len(records),
            surface=surface,
        )
        return {"data": records, "items": records, "page_info": {"next_page_token": None}, "meta": meta}

    def sem_empty_final_list(self, surface_key: str) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        return {
            "data": [],
            "items": [],
            "page_info": {"next_page_token": None, "total": 0},
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {surface_key: {"status": "ok", "source": "bff_local"}},
            },
        }
    # --- Private Record Visibility Helpers --- #

    def _private_record_owner(self, record: Dict[str, Any]) -> str:
        for key in ("createdBy", "created_by", "user_id", "userId", "owner_id", "ownerId", "operator_id", "operatorId", "author"):
            clean = str(record.get(key) or "").strip()
            if clean:
                return clean
        owner_ref = record.get("owner_ref") if isinstance(record.get("owner_ref"), dict) else {}
        return str(owner_ref.get("user_id") or owner_ref.get("owner_id") or "").strip()

    def _private_record_visible(
        self,
        record: Dict[str, Any],
        identity: OperatorIdentity,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        resolved_tenant, resolved_user = resolve_canonical_agora_scope(
            identity,
            tenant_id=tenant_id,
            user_id=user_id,
            utc_now=self.utc_now,
        )
        identity_tenant = str(resolved_tenant or "").strip()
        record_tenant = str(record.get("tenant_id") or record.get("tenantId") or "").strip()

        # Tenant isolation:
        if identity_tenant:
            # Legacy row missing tenant scope must NOT default to globally visible
            if not record_tenant or record_tenant != identity_tenant:
                return False
        elif record_tenant:
            return False

        visibility = str(record.get("visibility") or "private").strip().lower()
        owner = self._private_record_owner(record)
        if visibility != "private" or not owner:
            return True
        operator_id = str(getattr(identity, "operator_id", "") or "").strip() if identity else ""
        allowed_users = {u for u in (resolved_user, operator_id) if u}
        return owner in allowed_users

    def filter_private_records(
        self,
        records: List[Dict[str, Any]],
        identity: OperatorIdentity,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        resolved_tenant, resolved_user = resolve_canonical_agora_scope(
            identity,
            tenant_id=tenant_id,
            user_id=user_id,
            utc_now=self.utc_now,
        )
        return [
            record
            for record in records
            if isinstance(record, dict)
            and self._private_record_visible(
                record,
                identity,
                tenant_id=resolved_tenant,
                user_id=resolved_user,
            )
        ]

    def raise_cross_user_forbidden(self, *, resource: str, resource_id: str) -> None:
        raise self.bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora resource is outside the current user scope",
            "CROSS_USER_ACCESS_FORBIDDEN",
            precondition_failed="agora_user_scope",
            details_extra={"resource": resource, "resource_id": resource_id},
        )

    # --- Journal Merge Patch --- #

    def require_merge_patch_content_type(self, content_type: Optional[str]) -> None:
        media_type = str(content_type or "").split(";", 1)[0].strip().lower()
        if media_type == _JOURNAL_MERGE_PATCH_CONTENT_TYPE:
            return
        raise self.bff_error(
            415,
            ErrorCode.VALIDATION_FAILED,
            "Agora journal patch requires application/merge-patch+json",
            "JSON Merge Patch endpoints reject non-merge-patch content types",
            precondition_failed="content_type",
            suggestion="Retry with Content-Type: application/merge-patch+json",
            details_extra={"requiredContentType": _JOURNAL_MERGE_PATCH_CONTENT_TYPE},
        )

    def validate_journal_merge_patch_payload(
        self,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
    ) -> Dict[str, Any]:
        unknown_fields = sorted(set(payload) - _JOURNAL_PATCH_FIELDS)
        if unknown_fields:
            raise self.bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Agora journal patch contains unsupported fields",
                f"Unsupported fields: {', '.join(unknown_fields)}",
                precondition_failed="journal_patch.fields",
                suggestion="Submit a JSON Merge Patch body containing only valid journal fields",
                details_extra={"field": "fields", "unsupportedFields": unknown_fields},
            )
        if not any(field in payload for field in _JOURNAL_PATCH_FIELDS):
            raise self.bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Agora journal patch must include at least one editable field",
                "The merge patch body did not contain any journal entry fields",
                precondition_failed="journal_patch.fields",
                suggestion="Submit a JSON Merge Patch body containing only valid journal fields",
                details_extra={"field": "fields"},
            )

        from pydantic import ValidationError
        try:
            patch_model = JournalEntryMergePatch(**payload)
        except ValidationError as exc:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Agora journal patch has invalid field types",
                str(exc),
                precondition_failed="journal_patch.payload",
                details_extra={"field": "payload"},
            ) from exc

        patch = patch_model.model_dump(exclude_unset=True)

        if "title" in patch:
            title = patch["title"]
            if title is None or not str(title).strip():
                raise self.bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Journal entry title is required when patched",
                    "title must be a non-empty string",
                    precondition_failed="journal_patch.title",
                    details_extra={"field": "title"},
                )
            title = str(title).strip()
            if len(title) > 160:
                raise self.bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Journal entry title is too long",
                    "title must be 1-160 characters",
                    precondition_failed="journal_patch.title",
                    details_extra={"field": "title", "maxLength": 160},
                )
            patch["title"] = title

        if "body" in patch:
            body = "" if patch["body"] is None else str(patch["body"])
            if len(body) > 20000:
                raise self.bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Journal entry body is too long",
                    "body must be at most 20000 characters",
                    precondition_failed="journal_patch.body",
                    details_extra={"field": "body", "maxLength": 20000},
                )
            patch["body"] = body

        for list_field in ("linkedStrategyIds", "linkedPersonaIds"):
            if list_field not in patch or patch[list_field] is None:
                continue
            cleaned = [str(item).strip() for item in patch[list_field]]
            if any(not item for item in cleaned):
                raise self.bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    f"{list_field} cannot contain empty ids",
                    f"{list_field} entries must be non-empty strings",
                    precondition_failed=f"journal_patch.{list_field}",
                    details_extra={"field": list_field},
                )
            patch[list_field] = cleaned

        if "tags" in patch and patch["tags"] is not None:
            tags = [str(tag).strip() for tag in patch["tags"]]
            invalid_tags = [tag for tag in tags if not _JOURNAL_TAG_RE.fullmatch(tag)]
            if invalid_tags:
                raise self.bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Journal entry tags must be lowercase slug or dot.case",
                    "tags must match lowercase dot.case or slug form",
                    precondition_failed="journal_patch.tags",
                    details_extra={"field": "tags", "invalidTags": invalid_tags},
                )
            patch["tags"] = tags

        if "visibility" in patch:
            visibility = patch["visibility"]
            if visibility is None:
                raise self.bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Journal entry visibility cannot be null",
                    "visibility must be a supported scope",
                    precondition_failed="journal_patch.visibility",
                    details_extra={"field": "visibility"},
                )
            visibility = str(visibility).strip().lower()
            required_capability = _JOURNAL_VISIBILITY_CAPABILITY.get(visibility)
            if not required_capability:
                raise self.bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Journal entry visibility is unsupported",
                    "visibility must be private, team, committee, or public",
                    precondition_failed="journal_patch.visibility",
                    details_extra={"field": "visibility", "allowedValues": sorted(_JOURNAL_VISIBILITY_CAPABILITY)},
                )
            allowed_roles = _JOURNAL_VISIBILITY_ROLES.get(visibility, set())
            if not bool(allowed_roles.intersection(identity.roles)):
                raise self.bff_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Operator lacks capability for requested journal visibility",
                    f"visibility={visibility} requires {required_capability}",
                    precondition_failed="journal_patch.visibility",
                    suggestion="Choose a narrower visibility or escalate to an authorized operator",
                    details_extra={"field": "visibility", "requiredCapability": required_capability},
                )
            patch["visibility"] = visibility

        return patch

    def patch_journal_entry(
        self,
        *,
        entry_id: str,
        patch: Dict[str, Any],
        identity: OperatorIdentity,
        resolved_key: str,
        correlation_id: Optional[str] = None,
        x_request_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> CommandResponse[DecisionJournalEntryDTO]:
        request_hash = self.stable_json_hash({
            "route": f"PATCH /bff/agora/journal/{entry_id}",
            "entryId": entry_id,
            "patch": patch,
        })
        resolved_tenant, resolved_user = resolve_canonical_agora_scope(
            identity,
            tenant_id=tenant_id,
            user_id=user_id,
            utc_now=self.utc_now,
        )
        store = self.read_store
        if store is not None and hasattr(store, "list_decision_journal_entries"):
            try:
                existing = [
                    e for e in store.list_decision_journal_entries(tenant_id=resolved_tenant, user_id=resolved_user)
                    if str(e.get("id") or e.get("entry_id") or "") == entry_id
                ]
            except TypeError:
                existing = [
                    e for e in store.list_decision_journal_entries()
                    if str(e.get("id") or e.get("entry_id") or "") == entry_id
                ]
            if existing and not self._private_record_visible(
                existing[0],
                identity,
                tenant_id=resolved_tenant,
                user_id=resolved_user,
            ):
                self.raise_cross_user_forbidden(resource="decision_journal_entry", resource_id=entry_id)

        now = self.utc_now()
        owner = self.journal_write_owner
        if owner is None or not hasattr(owner, "patch_decision_journal_entry"):
            raise self.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Decision Journal write owner is not configured",
                "The canonical Decision Journal owner adapter was not composed onto this service",
                precondition_failed="decision_journal_write_owner",
            )
        try:
            result = owner.patch_decision_journal_entry(
                entry_id,
                patch=patch,
                actor_id=identity.operator_id,
                correlation_id=correlation_id,
                idempotency_key=resolved_key,
                request_hash=request_hash,
                patched_at=now,
                tenant_id=resolved_tenant,
                user_id=resolved_user,
            )
        except DecisionJournalAccessDeniedError:
            self.raise_cross_user_forbidden(resource="decision_journal_entry", resource_id=entry_id)
        except DecisionJournalCollisionError as exc:
            raise self.bff_error(
                409,
                ErrorCode.CONFLICT,
                "Decision journal collision",
                str(exc),
                precondition_failed="entry_id",
            )
        except DecisionJournalConcurrencyError as exc:
            raise self.bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Concurrent update conflict on decision journal entry",
                str(exc),
                precondition_failed="version",
            )
        except (OSError, IOError) as exc:
            raise self.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Decision journal storage or outbox unavailable",
                str(exc),
                precondition_failed="decision_journal_storage",
            )

        if result is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora journal entry not found",
                f"Journal entry {entry_id} does not exist",
                precondition_failed="entry_id",
            )
        if result.get("status") == "conflict":
            raise self.bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different patch payload",
                f"Key {resolved_key!r} is bound to a different journal merge patch request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original patch payload unchanged",
                details_extra={"existingPatchId": result.get("existing_patch_id")},
            )

        entry_dict = result.get("entry") or {}
        entry_dto = DecisionJournalEntryDTO(**entry_dict)
        audit = result.get("audit") or {}
        self.publish_sse_event("journal", "journal.entry.updated", {"entryId": entry_id, "patch": patch})
        return CommandResponse[DecisionJournalEntryDTO](
            status=ActionCommandStatus.COMPLETED,
            data=entry_dto,
            meta={
                "snapshot_at": now,
                "idempotency": {
                    "key": resolved_key,
                    "idempotencyKey": resolved_key,
                    "replayed": result.get("status") == "replayed",
                },
                "canonicalWriteAuthority": entry_dict.get("canonicalWriteAuthority"),
                "persistenceMode": entry_dict.get("persistenceMode"),
                "audit": audit,
            },
        )

    # --- Journal Entries --- #

    def list_journal_entries(
        self,
        *,
        identity: OperatorIdentity,
        page_token: Optional[str] = None,
        page_size: int = 20,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        resolved_tenant, resolved_user = resolve_canonical_agora_scope(
            identity,
            tenant_id=tenant_id,
            user_id=user_id,
            utc_now=self.utc_now,
        )
        store = self.read_store
        entries = []
        if store and hasattr(store, "list_decision_journal_entries"):
            try:
                entries = store.list_decision_journal_entries(tenant_id=resolved_tenant, user_id=resolved_user)
            except TypeError:
                entries = store.list_decision_journal_entries()
        visible_entries = self.filter_private_records(
            entries,
            identity,
            tenant_id=resolved_tenant,
            user_id=resolved_user,
        )
        return self.agora_list_response(
            dataset="decision_journal_entries",
            surface_key="agora_journal_list",
            items=visible_entries,
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )

    def create_journal_entry(
        self,
        *,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
        x_dry_run: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        if identity is None:
            raise self.bff_error(401, ErrorCode.AUTH_REQUIRED, "Journal entry creation requires a verified identity")
        body_tenant = str(payload.get("tenant_id") or payload.get("tenantId") or "").strip()
        resolved_tenant, resolved_user = resolve_canonical_agora_scope(
            identity,
            tenant_id=tenant_id,
            user_id=user_id or payload.get("user_id") or payload.get("userId"),
            utc_now=self.utc_now,
        )
        if body_tenant and resolved_tenant and body_tenant != resolved_tenant:
            raise self.bff_error(403, ErrorCode.FORBIDDEN, "Tenant access denied", "Payload tenant mismatch", precondition_failed="tenant_scope")
        title = self.agora_required_text(payload, "title")
        body_text = str(payload.get("body") or payload.get("decision") or payload.get("rationale") or "").strip()
        visibility = str(payload.get("visibility") or "private").strip().lower()
        if visibility not in _JOURNAL_VISIBILITY_CAPABILITY:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Journal entry visibility is unsupported",
                "visibility must be private, team, committee, or public",
                precondition_failed="journal.visibility",
            )
        allowed_roles = _JOURNAL_VISIBILITY_ROLES.get(visibility, set())
        if not bool(allowed_roles.intersection(identity.roles)):
            raise self.bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Operator lacks capability for requested journal visibility",
                f"visibility={visibility} requires {_JOURNAL_VISIBILITY_CAPABILITY.get(visibility)}",
                precondition_failed="journal.visibility",
            )

        journal_payload = {**payload, "title": title, "body": body_text, "visibility": visibility}
        request_hash = self.stable_json_hash({"route": "POST /bff/agora/journal", "payload": journal_payload})
        dry_run = bool(x_dry_run and x_dry_run.strip().lower() in ("true", "1", "yes"))

        owner = self.journal_write_owner
        if not dry_run and (owner is None or not hasattr(owner, "create_decision_journal_entry")):
            raise self.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Decision Journal write owner is not configured",
                "The canonical Decision Journal owner adapter was not composed onto this service",
                precondition_failed="decision_journal_write_owner",
            )

        scoped_idem_key: Optional[str] = None
        if resolved_key and not dry_run:
            clean_tenant_q = urllib.parse.quote(str(resolved_tenant or "").strip(), safe="-_.~")
            clean_user_q = urllib.parse.quote(str(resolved_user or "").strip(), safe="-_.~")
            clean_key_q = urllib.parse.quote(str(resolved_key or "").strip(), safe="-_.~")
            scoped_idem_key = f"create:{clean_tenant_q}:{clean_user_q}:{clean_key_q}"
            entry_id = str(
                payload.get("id")
                or payload.get("entryId")
                or f"dje-{hashlib.sha256(scoped_idem_key.encode('utf-8')).hexdigest()[:10]}"
            )
            journal_payload = {**journal_payload, "id": entry_id, "entryId": entry_id}
            if hasattr(owner, "check_create_idempotency"):
                idem_check = owner.check_create_idempotency(
                    scoped_key=scoped_idem_key,
                    request_hash=request_hash,
                    entry_id=entry_id,
                    raw_key=resolved_key,
                    tenant_id=resolved_tenant,
                    user_id=resolved_user,
                )
            elif hasattr(owner, "stores") and getattr(owner, "stores", None) is not None:
                reservation = {
                    "idempotency_key": scoped_idem_key,
                    "raw_idempotency_key": resolved_key,
                    "tenant_id": resolved_tenant,
                    "user_id": resolved_user,
                    "actor_id": resolved_user,
                    "request_hash": request_hash,
                    "entry_id": entry_id,
                    "status": "pending",
                    "created_pid": os.getpid(),
                    "created_at": time.time(),
                    "result": None,
                }
                reserved, existing = owner.stores.idempotency.insert_if_absent(reservation)
                if reserved:
                    idem_check = None
                else:
                    rec_tenant = str(existing.get("tenant_id") or "").strip()
                    rec_user = str(existing.get("user_id") or existing.get("actor_id") or "").strip()
                    rec_entry = str(existing.get("entry_id") or "").strip()
                    req_tenant = str(resolved_tenant or "").strip()
                    req_user = str(resolved_user or "").strip()
                    req_entry = str(entry_id or "").strip()
                    if (req_tenant and rec_tenant and req_tenant != rec_tenant) or \
                       (req_user and rec_user and req_user != rec_user) or \
                       (req_entry and rec_entry and req_entry != rec_entry):
                        idem_check = {"conflict": True, "record": existing, "reason": "scope_mismatch"}
                    elif existing.get("request_hash") != request_hash:
                        idem_check = {"conflict": True, "record": existing}
                    elif existing.get("status") == "pending":
                        created_pid = existing.get("created_pid")
                        is_dead = False
                        if created_pid and created_pid != os.getpid():
                            try:
                                os.kill(created_pid, 0)
                            except ProcessLookupError:
                                is_dead = True
                            except PermissionError:
                                pass
                        if is_dead:
                            if hasattr(owner, "_recover_committed_entry_result"):
                                recovered = owner._recover_committed_entry_result(
                                    existing,
                                    entry_id=entry_id,
                                    raw_key=resolved_key,
                                    tenant_id=resolved_tenant,
                                    user_id=resolved_user,
                                )
                                if recovered is not None:
                                    idem_check = {"conflict": False, "result": recovered}
                                else:
                                    owner.stores.idempotency.put(reservation)
                                    idem_check = None
                            else:
                                owner.stores.idempotency.put(reservation)
                                idem_check = None
                        else:
                            idem_check = {"conflict": False, "pending": True, "scoped_key": scoped_idem_key}
                    elif existing.get("status") == "failed":
                        owner.stores.idempotency.put(reservation)
                        idem_check = None
                    else:
                        result = existing.get("result")
                        if isinstance(result, dict) and isinstance(result.get("data"), dict):
                            d = result["data"]
                            d_tenant = str(d.get("tenant_id") or d.get("tenantId") or "").strip()
                            d_user = str(d.get("userId") or d.get("user_id") or d.get("createdBy") or "").strip()
                            d_id = str(d.get("id") or d.get("entryId") or "").strip()
                            if (req_tenant and d_tenant and req_tenant != d_tenant) or \
                               (req_user and d_user and req_user != d_user) or \
                               (req_entry and d_id and req_entry != d_id):
                                idem_check = {"conflict": True, "record": existing, "reason": "scope_mismatch"}
                            else:
                                idem_check = {"conflict": False, "result": result}
                        else:
                            idem_check = {"conflict": False, "result": result}
            else:
                idem_check = None

            if idem_check is not None:
                if idem_check.get("conflict"):
                    raise self.bff_error(
                        409,
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Idempotency key was already used with a different payload",
                        f"Key {resolved_key!r} is bound to a different Agora request hash",
                        precondition_failed="idempotency_conflict",
                        suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                    )
                if idem_check.get("pending"):
                    resolved_idem = None
                    if hasattr(owner, "await_create_idempotency"):
                        resolved_idem = owner.await_create_idempotency(
                            scoped_key=scoped_idem_key,
                            request_hash=request_hash,
                            entry_id=entry_id,
                            raw_key=resolved_key,
                            tenant_id=resolved_tenant,
                            user_id=resolved_user,
                        )
                    elif hasattr(owner, "stores") and getattr(owner, "stores", None) is not None:
                        deadline = time.monotonic() + 10.0
                        while time.monotonic() < deadline:
                            rec = owner.stores.idempotency.get(scoped_idem_key)
                            if rec is not None:
                                if rec.get("request_hash") != request_hash:
                                    resolved_idem = {"conflict": True}
                                    break
                                if rec.get("status") == "succeeded":
                                    resolved_idem = {"conflict": False, "result": rec.get("result")}
                                    break
                                if rec.get("status") == "failed":
                                    resolved_idem = {"conflict": False, "failed": True}
                                    break
                                created_pid = rec.get("created_pid")
                                if created_pid and created_pid != os.getpid():
                                    try:
                                        os.kill(created_pid, 0)
                                    except ProcessLookupError:
                                        if hasattr(owner, "_recover_committed_entry_result"):
                                            recovered = owner._recover_committed_entry_result(rec, entry_id=entry_id, raw_key=resolved_key)
                                            if recovered is not None:
                                                resolved_idem = {"conflict": False, "result": recovered}
                                                break
                                        resolved_idem = {"conflict": False, "failed": True}
                                        break
                                    except PermissionError:
                                        pass
                            time.sleep(0.005)
                        else:
                            rec = owner.stores.idempotency.get(scoped_idem_key)
                            if rec and rec.get("status") == "succeeded":
                                resolved_idem = {"conflict": False, "result": rec.get("result")}
                            elif hasattr(owner, "_recover_committed_entry_result"):
                                recovered = owner._recover_committed_entry_result(rec or {}, entry_id=entry_id, raw_key=resolved_key)
                                if recovered is not None:
                                    resolved_idem = {"conflict": False, "result": recovered}
                    if resolved_idem and resolved_idem.get("conflict"):
                        raise self.bff_error(
                            409,
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "Idempotency key was already used with a different payload",
                            f"Key {resolved_key!r} is bound to a different Agora request hash",
                            precondition_failed="idempotency_conflict",
                        )
                    if resolved_idem and resolved_idem.get("result"):
                        cached_result = copy.deepcopy(resolved_idem["result"])
                        if "meta" in cached_result and isinstance(cached_result["meta"], dict):
                            if "idempotency" in cached_result["meta"] and isinstance(cached_result["meta"]["idempotency"], dict):
                                cached_result["meta"]["idempotency"]["replayed"] = True
                        return cached_result
                cached = idem_check.get("result")
                if cached is not None:
                    cached_result = copy.deepcopy(cached)
                    if "meta" in cached_result and isinstance(cached_result["meta"], dict):
                        if "idempotency" in cached_result["meta"] and isinstance(cached_result["meta"]["idempotency"], dict):
                            cached_result["meta"]["idempotency"]["replayed"] = True
                    return cached_result

        snapshot_at = self.utc_now()
        if "entry_id" not in locals():
            entry_id = str(payload.get("id") or payload.get("entryId") or f"dje-{uuid.uuid4().hex[:10]}")
            journal_payload = {**journal_payload, "id": entry_id, "entryId": entry_id}
        if dry_run:
            return self.dry_run_success_response(
                {
                    "id": entry_id,
                    "entryId": entry_id,
                    **journal_payload,
                    "createdBy": identity.operator_id,
                    "author": identity.operator_id,
                    "tenant_id": resolved_tenant,
                    "user_id": resolved_user,
                    "createdAt": snapshot_at,
                    "canonicalWriteAuthority": "governance-decision-journal-svc",
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.journal.create",
            )

        try:
            created = owner.create_decision_journal_entry(
                title=title,
                body=body_text,
                actor_id=identity.operator_id,
                payload=journal_payload,
                created_at=snapshot_at,
                tenant_id=resolved_tenant,
                user_id=resolved_user,
            )
        except DecisionJournalCollisionError as exc:
            if scoped_idem_key and not dry_run:
                if hasattr(owner, "fail_create_idempotency"):
                    owner.fail_create_idempotency(scoped_key=scoped_idem_key, request_hash=request_hash)
                elif hasattr(owner, "stores") and getattr(owner, "stores", None) is not None:
                    owner.stores.idempotency.put({"idempotency_key": scoped_idem_key, "request_hash": request_hash, "status": "failed"})
            raise self.bff_error(
                409,
                ErrorCode.CONFLICT,
                "Decision journal entry ID collision across tenant or actor boundary",
                str(exc),
                precondition_failed="entry_id",
            )
        except DecisionJournalAccessDeniedError as exc:
            if scoped_idem_key and not dry_run:
                if hasattr(owner, "fail_create_idempotency"):
                    owner.fail_create_idempotency(scoped_key=scoped_idem_key, request_hash=request_hash)
                elif hasattr(owner, "stores") and getattr(owner, "stores", None) is not None:
                    owner.stores.idempotency.put({"idempotency_key": scoped_idem_key, "request_hash": request_hash, "status": "failed"})
            raise self.bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Decision journal access denied",
                str(exc),
                precondition_failed="tenant_scope",
            )
        except DecisionJournalConcurrencyError as exc:
            if scoped_idem_key and not dry_run:
                if hasattr(owner, "fail_create_idempotency"):
                    owner.fail_create_idempotency(scoped_key=scoped_idem_key, request_hash=request_hash)
                elif hasattr(owner, "stores") and getattr(owner, "stores", None) is not None:
                    owner.stores.idempotency.put({"idempotency_key": scoped_idem_key, "request_hash": request_hash, "status": "failed"})
            raise self.bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Concurrent update conflict on decision journal entry",
                str(exc),
                precondition_failed="version",
            )
        except Exception:
            if scoped_idem_key and not dry_run:
                if hasattr(owner, "fail_create_idempotency"):
                    owner.fail_create_idempotency(scoped_key=scoped_idem_key, request_hash=request_hash)
                elif hasattr(owner, "stores") and getattr(owner, "stores", None) is not None:
                    owner.stores.idempotency.put({"idempotency_key": scoped_idem_key, "request_hash": request_hash, "status": "failed"})
            raise

        result = {
            "data": created,
            "meta": {
                "snapshot_at": snapshot_at,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_journal_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        if scoped_idem_key and not dry_run:
            if hasattr(owner, "record_create_idempotency"):
                owner.record_create_idempotency(
                    scoped_key=scoped_idem_key,
                    raw_key=resolved_key,
                    tenant_id=resolved_tenant,
                    user_id=resolved_user,
                    request_hash=request_hash,
                    result=result,
                    created_at=snapshot_at,
                    entry_id=entry_id,
                )
            elif hasattr(owner, "stores") and getattr(owner, "stores", None) is not None:
                owner.stores.idempotency.put({
                    "idempotency_key": scoped_idem_key,
                    "raw_idempotency_key": resolved_key,
                    "tenant_id": resolved_tenant,
                    "user_id": resolved_user,
                    "actor_id": resolved_user,
                    "request_hash": request_hash,
                    "entry_id": entry_id,
                    "status": "succeeded",
                    "result": result,
                    "created_at": snapshot_at,
                })
        return result

    # --- Read Surface Projections & Semantic Lists --- #

    def list_postmortems(self) -> Dict[str, Any]:
        return self.sem_list_payload("postmortems", "agora_postmortems")

    def publish_sse_event(self, channel: str, event_type: str, data: Dict[str, Any]) -> None:
        buf = self._sse_buffers.get(channel, [])
        subs = self._sse_subscribers.get(channel, [])
        self.publish_event_fn(buf, subs, event_type, data)
