"""Agora domain service and orchestration facade.

Encapsulates Agora session lifecycle, quick ask assistant coordination, insight
and institutional memory management, action command submission, idempotency,
journal merge-patch validation, signal/feedback lifecycle, committee sessions,
and data projections without importing or coupling to main.py.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from models import (
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

try:
    from ports import (
        ReadSurfacePorts,
        create_read_surface_ports,
        OpenClawOpsClient,
        OpenClawOpsClientError,
    )
except ImportError:
    try:
        from ..ports import (  # type: ignore[no-redef]
            ReadSurfacePorts,
            create_read_surface_ports,
            OpenClawOpsClient,
            OpenClawOpsClientError,
        )
    except ImportError:
        ReadSurfacePorts = Any  # type: ignore
        create_read_surface_ports = None  # type: ignore
        OpenClawOpsClient = None  # type: ignore
        OpenClawOpsClientError = Exception  # type: ignore

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
    ) -> None:
        self._get_read_store = get_read_store or (lambda: None)
        self._get_audit_store = get_audit_store or (lambda: None)
        self._get_command_store = get_command_store or (lambda: None)
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

    def sem_agora_inbox_payload(self) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        datasets = [
            ("insight_cards", "insight", "agora_inbox_insights"),
            ("agora_signals", "signal", "agora_inbox_signals"),
            ("research_tickets", "research_task", "agora_inbox_research_tasks"),
        ]
        records: List[Dict[str, Any]] = []
        sources: Dict[str, str] = {}
        counts: Dict[str, int] = {}
        surfaces: Dict[str, Dict[str, Any]] = {}

        for dataset, inbox_type, surface_key in datasets:
            source, raw_records = self.sem_read_records(dataset)
            typed_records = []
            for record in raw_records:
                item = dict(record)
                item.setdefault("inboxType", inbox_type)
                item.setdefault("sourceDataset", dataset)
                typed_records.append(item)
            sources[dataset] = source
            counts[inbox_type] = len(typed_records)
            records.extend(typed_records)
            surfaces[surface_key] = self.dataset_surface_status(
                dataset,
                snapshot_at=snapshot_at,
                source=source,
                has_data=(source != "missing"),
            )

        def _sort_key(record: Dict[str, Any]) -> str:
            for field in ("updatedAt", "updated_at", "createdAt", "created_at"):
                val = record.get(field)
                if val:
                    return str(val)
            return str(record.get("id") or record.get("signal_id") or record.get("ticket_id") or "")

        records.sort(key=_sort_key, reverse=True)
        primary_surface = self.dataset_surface_status(
            "insight_cards",
            snapshot_at=snapshot_at,
            source=sources.get("insight_cards"),
            has_data=(sources.get("insight_cards") != "missing"),
        )
        meta = {
            "snapshot_at": snapshot_at,
            "dataset": "insight_cards",
            "surface": "agora_inbox",
            "total": len(records),
            "surfaces": {"agora_inbox": primary_surface, **surfaces},
            "composition": {
                "datasets": [dataset for dataset, _, _ in datasets],
                "itemCounts": counts,
            },
        }
        return {"data": records, "items": records, "page_info": {"next_page_token": None}, "meta": meta}

    # --- Private Record Visibility Helpers --- #

    def _private_record_owner(self, record: Dict[str, Any]) -> str:
        for key in ("createdBy", "created_by", "user_id", "userId", "owner_id", "ownerId", "operator_id", "operatorId", "author"):
            clean = str(record.get(key) or "").strip()
            if clean:
                return clean
        owner_ref = record.get("owner_ref") if isinstance(record.get("owner_ref"), dict) else {}
        return str(owner_ref.get("user_id") or owner_ref.get("owner_id") or "").strip()

    def _private_record_visible(self, record: Dict[str, Any], identity: OperatorIdentity) -> bool:
        visibility = str(record.get("visibility") or "private").strip().lower()
        owner = self._private_record_owner(record)
        if visibility != "private" or not owner:
            return True
        return owner == identity.operator_id

    def filter_private_records(
        self,
        records: List[Dict[str, Any]],
        identity: OperatorIdentity,
    ) -> List[Dict[str, Any]]:
        return [
            record
            for record in records
            if isinstance(record, dict) and self._private_record_visible(record, identity)
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
    ) -> CommandResponse[DecisionJournalEntryDTO]:
        request_hash = self.stable_json_hash({
            "route": f"PATCH /bff/agora/journal/{entry_id}",
            "entryId": entry_id,
            "patch": patch,
        })
        store = self.read_store
        if store is not None and hasattr(store, "list_decision_journal_entries"):
            existing = [
                e for e in store.list_decision_journal_entries()
                if str(e.get("id") or e.get("entry_id") or "") == entry_id
            ]
            if existing and not self._private_record_visible(existing[0], identity):
                self.raise_cross_user_forbidden(resource="decision_journal_entry", resource_id=entry_id)

        now = self.utc_now()
        result = None
        if store is not None and hasattr(store, "patch_decision_journal_entry"):
            result = store.patch_decision_journal_entry(
                entry_id,
                patch=patch,
                actor_id=identity.operator_id,
                correlation_id=correlation_id,
                idempotency_key=resolved_key,
                request_hash=request_hash,
                patched_at=now,
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
                "canonicalWriteAuthority": "agora_journal_service",
                "persistenceMode": "bff_local_dev_store",
                "degraded": True,
                "audit": audit,
            },
        )

    # --- Signals & Feedback --- #

    def get_daily_brief(self) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        store = self.read_store
        signals = store.list_agora_signals() if store and hasattr(store, "list_agora_signals") else []
        watchlist = store.list_agora_watchlist() if store and hasattr(store, "list_agora_watchlist") else []
        journal = store.list_decision_journal_entries() if store and hasattr(store, "list_decision_journal_entries") else []
        tasks = store.list_research_tickets(statuses=["new", "triaged", "open", "in_progress"]) if store and hasattr(store, "list_research_tickets") else []

        pending_signals = [s for s in signals if str(s.get("reviewStatus") or s.get("status") or "") in ("pending", "open", "new", "pending_trader_review")]
        brief = {
            "id": f"agora-daily-{snapshot_at[:10]}",
            "date": snapshot_at[:10],
            "generatedAt": snapshot_at,
            "kpis": {
                "watchlistMoveCount": len(watchlist),
                "signalReviewQueue": len(pending_signals),
                "personaBriefCount": len(journal),
                "researchQuestionCount": len(tasks),
                "openSignals": len(pending_signals),
                "totalWatchlist": len(watchlist),
                "activeResearchTasks": len(tasks),
                "recentJournalEntries": len(journal[:5]),
            },
            "sections": {
                "signals": signals[:5],
                "watchlist": watchlist[:5],
                "journal": journal[:5],
                "researchTasks": tasks[:5],
                "research_tasks": tasks[:5],
            },
            "topSignals": signals[:5],
            "watchlistSummary": watchlist[:10],
            "researchHighlights": tasks[:5],
        }
        meta = self.dataset_surface_status("agora_daily", snapshot_at=snapshot_at)
        return {
            "data": brief,
            "items": [brief],
            "meta": _default_read_surface_meta("agora_daily", "agora_daily_brief", snapshot_at=snapshot_at, surface=meta),
        }

    def list_signals(
        self,
        *,
        review_status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        store = self.read_store
        signals = store.list_agora_signals(review_status=review_status) if store and hasattr(store, "list_agora_signals") else []
        if not signals and self._local_signals:
            signals = list(self._local_signals.values())
            if review_status:
                signals = [s for s in signals if str(s.get("reviewStatus") or s.get("review_status") or "") == review_status]
        return self.agora_list_response(
            dataset="agora_signals",
            surface_key="agora_signal_list",
            items=signals,
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )

    def create_signal(
        self,
        *,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
        x_correlation_id: Optional[str] = None,
        x_request_id: Optional[str] = None,
        x_dry_run: Optional[str] = None,
        response: Optional[Any] = None,
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        title = self.agora_required_text(payload, "title")
        body = self.agora_required_text(payload, "body")
        severity = str(payload.get("severity") or "info").strip().lower()
        if severity not in _AGORA_SIGNAL_SEVERITIES:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Agora signal severity is invalid",
                "severity must be one of info, warn, or alert",
                precondition_failed="signal.severity",
            )
        signal_payload = {**payload, "title": title, "body": body, "severity": severity}
        request_hash = self.stable_json_hash({"route": "POST /bff/agora/signals", "payload": signal_payload})
        dry_run = bool(x_dry_run and x_dry_run.strip().lower() in ("true", "1", "yes"))
        if not dry_run:
            cached = self.check_idempotency(resolved_key, request_hash)
            if cached is not None:
                return cached

        snapshot_at = self.utc_now()
        signal_id = str(payload.get("id") or payload.get("signalId") or f"sig-{uuid.uuid4().hex[:10]}")
        store = self.read_store
        if store is not None and hasattr(store, "get_agora_signal") and store.get_agora_signal(signal_id):
            raise self.bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Signal ID already exists",
                f"Signal {signal_id} already exists",
                precondition_failed="signal_id",
            )

        if dry_run:
            res = self.dry_run_success_response(
                {
                    "id": signal_id,
                    "signalId": signal_id,
                    **signal_payload,
                    "author": identity.operator_id,
                    "createdAt": snapshot_at,
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.signal.create",
            )
            if x_correlation_id:
                res.headers["X-Correlation-Id"] = x_correlation_id
            if x_request_id:
                res.headers["X-Request-Id"] = x_request_id
            return res

        created_signal = None
        if store is not None and hasattr(store, "create_agora_signal"):
            created_signal = store.create_agora_signal(
                signal_id=signal_id,
                title=title,
                body=body,
                payload=signal_payload,
                actor_id=identity.operator_id,
                created_at=snapshot_at,
            )
        if not created_signal:
            created_signal = {
                "id": signal_id,
                "signalId": signal_id,
                **signal_payload,
                "author": identity.operator_id,
                "createdAt": snapshot_at,
            }
        self._local_signals[signal_id] = created_signal

        audit = {
            "evidenceKind": "agora.signal.create",
            "action": "agora.signal.create",
            "targetType": "signal",
            "targetId": signal_id,
            "actorId": identity.operator_id,
            "correlationId": x_correlation_id,
            "idempotencyKey": resolved_key,
            "recordedAt": snapshot_at,
        }
        recorded_audit = self._record_agora_audit_event(audit)
        if recorded_audit:
            audit = {**audit, **recorded_audit}

        self.publish_sse_event("signal", "agora.signal.created", {"signalId": signal_id, "signal": created_signal})
        self.publish_sse_event("inbox", "agora.inbox.updated", {"type": "signal", "id": signal_id})

        result = {
            "data": created_signal,
            "meta": {
                "snapshot_at": snapshot_at,
                "dryRun": False,
                "durable": True,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_signal_detail": {"status": "ok", "source": "bff_local"}},
                "audit": audit,
            },
        }

        self.record_idempotency(resolved_key, request_hash, result)
        return result

    def get_signal(self, signal_id: str) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        store = self.read_store
        signal = store.get_agora_signal(signal_id) if store and hasattr(store, "get_agora_signal") else None
        if signal is None:
            signal = self._local_signals.get(signal_id)
        if signal is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora signal not found",
                f"Signal {signal_id} does not exist",
                precondition_failed="signal_id",
            )
        return {
            "data": signal,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {"agora_signal_detail": {"status": "ok", "source": "bff_local"}},
            },
        }

    def create_bulk_feedback(
        self,
        *,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
        x_dry_run: Optional[str] = None,
        x_correlation_id: Optional[str] = None,
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        signal_id = str(payload.get("signal_id") or payload.get("signalId") or "").strip()
        if not signal_id:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Agora feedback signal_id is required",
                "POST /bff/agora/feedback requires a non-empty signal_id",
                precondition_failed="agora_feedback.signal_id",
            )
        verdict = str(payload.get("verdict") or "").strip()
        if verdict not in _AGORA_BULK_FEEDBACK_VERDICTS:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Agora feedback verdict is invalid",
                "verdict must be one of useful, noise, or false_positive",
                precondition_failed="agora_feedback.verdict",
            )
        memo = str(payload.get("memo") or "").strip() or None
        clean_payload = {"signal_id": signal_id, "verdict": verdict, "memo": memo}
        request_hash = self.stable_json_hash({"route": "POST /bff/agora/feedback", "payload": clean_payload})
        dry_run = bool(x_dry_run and x_dry_run.strip().lower() in ("true", "1", "yes"))
        if not dry_run:
            cached = self.check_idempotency(resolved_key, request_hash)
            if cached is not None:
                return cached

        snapshot_at = self.utc_now()
        store = self.read_store
        signal = store.get_agora_signal(signal_id) if store and hasattr(store, "get_agora_signal") else None
        if signal is None:
            signal = self._local_signals.get(signal_id)
        if signal is None and not dry_run:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Target signal not found",
                f"Signal {signal_id} does not exist",
                precondition_failed="signal_id",
            )

        if dry_run:
            return self.dry_run_success_response(
                {
                    "feedbackId": f"fb-{signal_id}",
                    "signalId": signal_id,
                    "verdict": verdict,
                    "memo": memo,
                    "actorId": identity.operator_id,
                    "createdAt": snapshot_at,
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.feedback.bulk_create",
            )

        fb_record = None
        if store is not None and hasattr(store, "create_agora_feedback"):
            fb_record = store.create_agora_feedback(
                signal_id=signal_id,
                actor_id=identity.operator_id,
                decision=verdict,
                confidence=0,
                reason=memo or "",
                created_at=snapshot_at,
            )
        if not fb_record:
            fb_record = {
                "feedbackId": f"fb-{signal_id}",
                "signalId": signal_id,
                "verdict": verdict,
                "memo": memo,
                "actorId": identity.operator_id,
                "createdAt": snapshot_at,
            }

        audit = None
        recorded_audit = self._record_agora_audit_event(
            {
                "action": "agora.feedback.bulk_create",
                "targetType": "signal",
                "targetId": signal_id,
                "actorId": identity.operator_id,
                "correlationId": x_correlation_id,
                "idempotencyKey": resolved_key,
                "recordedAt": snapshot_at,
            }
        )
        if recorded_audit:
            audit = recorded_audit

        self.publish_sse_event("signal", "agora.feedback.created", {"signalId": signal_id, "verdict": verdict})
        result = {
            "data": fb_record,
            "meta": {
                "snapshot_at": snapshot_at,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_feedback": {"status": "ok", "source": "bff_local"}},
            },
        }
        if audit:
            result["meta"]["audit"] = audit
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    def record_signal_feedback(
        self,
        *,
        signal_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
        x_dry_run: Optional[str] = None,
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        decision = str(payload.get("decision") or "").strip()
        if decision not in _AGORA_SIGNAL_DECISIONS:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Signal feedback decision is invalid",
                "decision must be one of agree, disagree, or flag_suspicious",
                precondition_failed="signal_feedback.decision",
            )
        try:
            confidence = int(payload.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Signal feedback confidence is invalid",
                "confidence must be an integer from 1 to 5",
                precondition_failed="signal_feedback.confidence",
            ) from exc
        if confidence < 1 or confidence > 5:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Signal feedback confidence is invalid",
                "confidence must be an integer from 1 to 5",
                precondition_failed="signal_feedback.confidence",
            )
        reason = str(payload.get("reason") or "").strip() or None
        if (decision == "disagree" and confidence >= 4 and not reason) or (
            decision == "flag_suspicious" and not reason
        ):
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Signal feedback reason is required",
                "reason is required for high-confidence disagree and flag_suspicious feedback",
                precondition_failed="signal_feedback.reason",
            )
        try:
            edit_window_seconds = int(payload.get("editWindowSeconds") or payload.get("edit_window_seconds") or 30)
        except (TypeError, ValueError) as exc:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Signal feedback edit window is invalid",
                "editWindowSeconds must be a positive integer",
                precondition_failed="signal_feedback.editWindowSeconds",
            ) from exc

        feedback_payload = {
            "decision": decision,
            "confidence": confidence,
            "reason": reason,
            "edit_window_seconds": max(1, edit_window_seconds),
        }
        request_hash = self.stable_json_hash({
            "route": f"POST /bff/agora/signals/{signal_id}/feedback",
            "signal_id": signal_id,
            "payload": feedback_payload,
        })
        dry_run = bool(x_dry_run and x_dry_run.strip().lower() in ("true", "1", "yes"))
        if not dry_run:
            cached = self.check_idempotency(resolved_key, request_hash)
            if cached is not None:
                return cached

        snapshot_at = self.utc_now()
        store = self.read_store
        signal = store.get_agora_signal(signal_id) if store and hasattr(store, "get_agora_signal") else None
        if signal is None and not dry_run:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Target signal not found",
                f"Signal {signal_id} does not exist",
                precondition_failed="signal_id",
            )

        if dry_run:
            return self.dry_run_success_response(
                {
                    "feedback": {
                        "feedbackId": f"fb-{signal_id}",
                        "signalId": signal_id,
                        **feedback_payload,
                        "actorId": identity.operator_id,
                        "createdAt": snapshot_at,
                    },
                    "signal": {**(signal or {}), "reviewStatus": decision},
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.signal.feedback",
            )

        fb_record = None
        if store is not None and hasattr(store, "record_agora_signal_feedback"):
            fb_record = store.record_agora_signal_feedback(
                signal_id=signal_id,
                actor_id=identity.operator_id,
                decision=decision,
                confidence=confidence,
                reason=reason or "",
                created_at=snapshot_at,
            )
        if not fb_record:
            fb_record = {
                "feedbackId": f"fb-{signal_id}",
                "signalId": signal_id,
                **feedback_payload,
                "actorId": identity.operator_id,
                "createdAt": snapshot_at,
            }

        cmd_res = self.submit_action_command(
            route=f"POST /bff/agora/signals/{signal_id}/feedback",
            entity_type=ObjectType.AGORA_SIGNAL,
            entity_id=signal_id,
            action_id="record-feedback",
            resolved_key=resolved_key,
            identity=identity,
            payload=feedback_payload,
            command_type=CommandType.AGORA_SIGNAL_FEEDBACK,
        )

        updated_signal = store.get_agora_signal(signal_id) if store and hasattr(store, "get_agora_signal") else signal
        self.publish_sse_event("signal", "agora.signal.feedback_recorded", {"signalId": signal_id, "feedback": fb_record})
        result = {
            "status": "completed",
            "data": {"feedback": fb_record, "signal": updated_signal or {}},
            "meta": {
                "snapshot_at": snapshot_at,
                "command": {"command": CommandType.AGORA_SIGNAL_FEEDBACK.value, "commandId": cmd_res.get("commandId")},
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_signal_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    # --- Watchlist & Markets --- #

    def list_watchlist(self, *, page_token: Optional[str] = None, page_size: int = 50) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        store = self.read_store
        items = store.list_agora_watchlist() if store and hasattr(store, "list_agora_watchlist") else []
        return self.agora_list_response(
            dataset="agora_watchlist",
            surface_key="agora_watchlist",
            items=items,
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )

    # --- Notes & Notebook --- #

    def list_notes(self, *, page_token: Optional[str] = None, page_size: int = 20) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        store = self.read_store
        items = store.list_agora_notes() if store and hasattr(store, "list_agora_notes") else []
        return self.agora_list_response(
            dataset="research_notes",
            surface_key="agora_note_list",
            items=items,
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )

    def create_note(
        self,
        *,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
        x_dry_run: Optional[str] = None,
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        body_text = self.agora_required_text(payload, "body", "content")
        title = str(payload.get("title") or "").strip() or body_text[:60]
        note_payload = {**payload, "title": title, "body": body_text}
        request_hash = self.stable_json_hash({"route": "POST /bff/agora/notes", "payload": note_payload})
        dry_run = bool(x_dry_run and x_dry_run.strip().lower() in ("true", "1", "yes"))
        if not dry_run:
            cached = self.check_idempotency(resolved_key, request_hash)
            if cached is not None:
                return cached

        snapshot_at = self.utc_now()
        note_id = str(payload.get("id") or payload.get("noteId") or f"note-{uuid.uuid4().hex[:10]}")
        if dry_run:
            return self.dry_run_success_response(
                {
                    "id": note_id,
                    "noteId": note_id,
                    **note_payload,
                    "author": identity.operator_id,
                    "createdAt": snapshot_at,
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.note.create",
            )

        store = self.read_store
        created = None
        if store is not None and hasattr(store, "create_agora_note"):
            created = store.create_agora_note(
                title=title,
                body=body_text,
                actor_id=identity.operator_id,
                payload=note_payload,
                created_at=snapshot_at,
            )
        if not created:
            created = {
                "id": note_id,
                "noteId": note_id,
                **note_payload,
                "author": identity.operator_id,
                "createdAt": snapshot_at,
            }

        result = {
            "data": created,
            "meta": {
                "snapshot_at": snapshot_at,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_note_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    # --- Journal Entries --- #

    def list_journal_entries(
        self,
        *,
        identity: OperatorIdentity,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        store = self.read_store
        entries = store.list_decision_journal_entries() if store and hasattr(store, "list_decision_journal_entries") else []
        visible_entries = self.filter_private_records(entries, identity)
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
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
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
        if not dry_run:
            cached = self.check_idempotency(resolved_key, request_hash)
            if cached is not None:
                return cached

        snapshot_at = self.utc_now()
        entry_id = str(payload.get("id") or payload.get("entryId") or f"dje-{uuid.uuid4().hex[:10]}")
        if dry_run:
            return self.dry_run_success_response(
                {
                    "id": entry_id,
                    "entryId": entry_id,
                    **journal_payload,
                    "createdBy": identity.operator_id,
                    "author": identity.operator_id,
                    "createdAt": snapshot_at,
                    "canonicalWriteAuthority": "agora_journal_service",
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.journal.create",
            )

        store = self.read_store
        created = None
        if store is not None and hasattr(store, "create_decision_journal_entry"):
            created = store.create_decision_journal_entry(
                title=title,
                body=body_text,
                actor_id=identity.operator_id,
                payload=journal_payload,
                created_at=snapshot_at,
            )
        if not created:
            created = {
                "id": entry_id,
                "entryId": entry_id,
                **journal_payload,
                "createdBy": identity.operator_id,
                "author": identity.operator_id,
                "createdAt": snapshot_at,
                "canonicalWriteAuthority": "agora_journal_service",
            }

        result = {
            "data": created,
            "meta": {
                "snapshot_at": snapshot_at,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_journal_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    # --- Training Examples --- #

    def list_training_examples(self, *, page_token: Optional[str] = None, page_size: int = 20) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        store = self.read_store
        examples = store.list_agora_training_examples() if store and hasattr(store, "list_agora_training_examples") else []
        return self.agora_list_response(
            dataset="agora_training_examples",
            surface_key="agora_training_example_list",
            items=examples,
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )

    def create_training_example(
        self,
        *,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
        x_dry_run: Optional[str] = None,
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = self.stable_json_hash({"route": "POST /bff/agora/training-examples", "payload": payload})
        dry_run = bool(x_dry_run and x_dry_run.strip().lower() in ("true", "1", "yes"))
        if not dry_run:
            cached = self.check_idempotency(resolved_key, request_hash)
            if cached is not None:
                return cached

        snapshot_at = self.utc_now()
        example_id = str(payload.get("id") or payload.get("trainingExampleId") or f"trn-agora-{uuid.uuid4().hex[:10]}")
        if dry_run:
            return self.dry_run_success_response(
                {
                    "id": example_id,
                    "trainingExampleId": example_id,
                    "input": copy.deepcopy(payload.get("input") or {}),
                    "expected": copy.deepcopy(payload.get("expected") or {}),
                    "labels": list(payload.get("labels") or []),
                    "createdBy": identity.operator_id,
                    "createdAt": snapshot_at,
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.training_example.create",
            )

        store = self.read_store
        created = None
        if store is not None and hasattr(store, "create_agora_training_example"):
            created = store.create_agora_training_example(
                example_id=example_id,
                payload=payload,
                actor_id=identity.operator_id,
                created_at=snapshot_at,
            )
        if not created:
            created = {
                "id": example_id,
                "trainingExampleId": example_id,
                "input": copy.deepcopy(payload.get("input") or {}),
                "expected": copy.deepcopy(payload.get("expected") or {}),
                "labels": list(payload.get("labels") or []),
                "createdBy": identity.operator_id,
                "createdAt": snapshot_at,
            }

        result = {
            "data": created,
            "meta": {
                "snapshot_at": snapshot_at,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_training_example_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    # --- Research Tasks --- #

    def list_research_tasks(
        self,
        *,
        status: Optional[str] = None,
        owner: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        store = self.read_store
        statuses = [s.strip() for s in status.split(",")] if status else None
        tasks = store.list_research_tickets(statuses=statuses, owner=owner) if store and hasattr(store, "list_research_tickets") else []
        return self.agora_list_response(
            dataset="research_tickets",
            surface_key="research_task_list",
            items=tasks,
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )

    # --- Persona Lab Submit Commit --- #

    def submit_persona_lab_commit(
        self,
        *,
        draft_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        payload_draft = str(payload.get("personaDraftId") or payload.get("persona_draft_id") or draft_id).strip()
        if payload_draft and payload_draft != draft_id:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Persona draft id mismatch",
                "personaDraftId in the request body must match the draftId route parameter.",
                precondition_failed="personaDraftId",
            )
        raw_runs = payload.get("evaluationRunIds") or payload.get("evaluation_run_ids") or []
        evaluation_run_ids = _dedupe_nonblank_strings(raw_runs)
        if not evaluation_run_ids:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "evaluationRunIds are required",
                "Persona lab commit requires at least one evaluation run id before handoff.",
                precondition_failed="evaluationRunIds",
            )
        change_summary = str(payload.get("changeSummary") or payload.get("change_summary") or "").strip()
        if not change_summary:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "changeSummary is required",
                "Persona lab commit requires a concise change summary for management review.",
                precondition_failed="changeSummary",
            )
        priority = str(payload.get("priority") or "normal").strip().lower()
        if priority not in {"low", "normal", "high", "urgent"}:
            priority = "normal"

        commit_payload = {
            "personaDraftId": draft_id,
            "basePersonaId": str(payload.get("basePersonaId") or payload.get("base_persona_id") or "").strip() or None,
            "evaluationRunIds": evaluation_run_ids,
            "changeSummary": change_summary,
            "requestedRoutePolicyId": str(payload.get("requestedRoutePolicyId") or payload.get("requested_route_policy_id") or "").strip() or None,
            "priority": priority,
        }
        request_hash = self.stable_json_hash({
            "route": "POST /bff/agora/persona-lab/{draftId}/actions/submit-commit",
            "draftId": draft_id,
            "payload": commit_payload,
        })
        cached = self.check_idempotency(resolved_key, request_hash)
        if cached is not None:
            return cached

        snapshot_at = self.utc_now()
        handoff_id = f"handoff-persona-{uuid.uuid4().hex[:12]}"
        base_persona_id = commit_payload.get("basePersonaId") or draft_id
        store = self.read_store
        handoff = None
        if store is not None and hasattr(store, "create_agora_handoff"):
            handoff = store.create_agora_handoff(
                handoff_id=handoff_id,
                handoff_type="trainer_feedback_to_persona_update",
                source_route=f"/agora/persona-lab/{draft_id}",
                source_entity={"type": "persona_draft", "id": draft_id},
                destination_route=f"/personas/{base_persona_id}/management-review",
                destination_queue="persona",
                priority=str(commit_payload.get("priority") or "normal"),
                payload={
                    "personaDraftId": commit_payload["personaDraftId"],
                    "basePersonaId": commit_payload.get("basePersonaId"),
                    "evaluationRunIds": commit_payload["evaluationRunIds"],
                    "changeSummary": commit_payload["changeSummary"],
                    "requestedRoutePolicyId": commit_payload.get("requestedRoutePolicyId"),
                },
                actor_id=identity.operator_id,
                created_at=snapshot_at,
            )
        if not handoff:
            handoff = {
                "id": handoff_id,
                "handoffId": handoff_id,
                "handoffType": "trainer_feedback_to_persona_update",
                "sourceRoute": f"/agora/persona-lab/{draft_id}",
                "sourceEntity": {"type": "persona_draft", "id": draft_id},
                "destination": {"queue": "persona", "route": f"/personas/{base_persona_id}/management-review"},
                "destinationQueue": "persona",
                "priority": str(commit_payload.get("priority") or "normal"),
                "payload": {
                    "personaDraftId": commit_payload["personaDraftId"],
                    "basePersonaId": commit_payload.get("basePersonaId"),
                    "evaluationRunIds": commit_payload["evaluationRunIds"],
                    "changeSummary": commit_payload["changeSummary"],
                    "requestedRoutePolicyId": commit_payload.get("requestedRoutePolicyId"),
                },
                "actorId": identity.operator_id,
                "createdBy": identity.operator_id,
                "createdAt": snapshot_at,
            }

        cmd_res = self.submit_action_command(
            route=f"POST /bff/agora/persona-lab/{draft_id}/actions/submit-commit",
            entity_type=ObjectType.PERSONA,
            entity_id=str(base_persona_id),
            action_id="submit-commit",
            resolved_key=resolved_key,
            identity=identity,
            payload=commit_payload,
            command_type=CommandType.PERSONA_ACTION,
        )

        self.publish_sse_event(
            "ask",
            "agora.persona_lab.handoff_submitted",
            {"draftId": draft_id, "handoffId": handoff_id, "basePersonaId": base_persona_id},
        )

        result = {
            "status": "accepted",
            "data": handoff,
            "meta": {
                "snapshot_at": snapshot_at,
                "command": cmd_res.get("data") or {"command": CommandType.PERSONA_ACTION.value, "commandId": cmd_res.get("commandId")},
                "audit": (cmd_res.get("meta") or {}).get("audit"),
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_persona_lab_commit": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    # --- Committee Evidence Pack --- #

    def validate_evidence_files(
        self,
        *,
        existing_files: List[Dict[str, Any]],
        incoming_files: List[Dict[str, Any]],
    ) -> None:
        violations: List[Dict[str, Any]] = []
        if not incoming_files:
            violations.append({"code": "missing_metadata", "field": "files"})
        if len(existing_files) + len(incoming_files) > _AGORA_EVIDENCE_MAX_FILES:
            violations.append({"code": "too_many_files"})

        total_size = 0
        for existing in existing_files:
            try:
                total_size += int(existing.get("sizeBytes") or existing.get("size_bytes") or 0)
            except (TypeError, ValueError):
                continue

        for item in incoming_files:
            file_name = str(item.get("fileName") or item.get("filename") or item.get("name") or "").strip()
            mime_type = str(item.get("mimeType") or item.get("mime_type") or "").strip()
            raw_size = item.get("sizeBytes")
            if raw_size is None:
                raw_size = item.get("size_bytes")
            try:
                size_bytes = int(raw_size)
            except (TypeError, ValueError):
                size_bytes = -1
            total_size += max(size_bytes, 0)

            if size_bytes < 0:
                violations.append({"code": "missing_metadata", "fileName": file_name, "field": "sizeBytes"})
            elif size_bytes > _AGORA_EVIDENCE_MAX_FILE_SIZE_BYTES:
                violations.append({"code": "file_too_large", "fileName": file_name})
            if mime_type not in _AGORA_EVIDENCE_ALLOWED_MIMES:
                violations.append({"code": "mime_not_allowed", "fileName": file_name})
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            missing = [
                key
                for key in ("source", "title", "uploadedBy", "createdAt")
                if not str(metadata.get(key) or "").strip()
            ]
            if missing:
                violations.append({"code": "missing_metadata", "fileName": file_name, "fields": missing})

        if total_size > _AGORA_EVIDENCE_MAX_TOTAL_SIZE_BYTES:
            violations.append({"code": "total_too_large"})

        if violations:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Committee evidence upload rejected",
                "One or more committee evidence files failed server-side validation.",
                precondition_failed="committee_evidence.files",
                suggestion="Check file count, file size, MIME type, and required metadata before retrying.",
                details_extra={"violations": violations},
            )

    def create_committee_evidence_pack(
        self,
        *,
        session_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = self.stable_json_hash({
            "route": f"POST /bff/agora/committee/{session_id}/evidence-pack",
            "sessionId": session_id,
            "payload": payload,
        })
        cached = self.check_idempotency(resolved_key, request_hash)
        if cached is not None:
            return cached

        snapshot_at = self.utc_now()
        store = self.read_store
        session = store.get_agora_session(session_id) if store and hasattr(store, "get_agora_session") else None
        if session is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee session not found",
                f"Committee session {session_id} does not exist",
                precondition_failed="session_id",
            )

        pack_id = str(payload.get("packId") or f"ep-{session_id}-{uuid.uuid4().hex[:8]}")
        created_pack = None
        if store is not None and hasattr(store, "create_agora_committee_evidence_pack"):
            created_pack = store.create_agora_committee_evidence_pack(
                session_id=session_id,
                pack_id=pack_id,
                payload=payload,
                actor_id=identity.operator_id,
                created_at=snapshot_at,
            )
        if not isinstance(created_pack, dict):
            created_pack = {"packId": pack_id, "sessionId": session_id, **payload, "createdAt": snapshot_at}
        created_pack.setdefault("uploadedFiles", [])
        created_pack.setdefault("targetEntityType", payload.get("targetEntityType"))
        created_pack.setdefault("targetEntityId", payload.get("targetEntityId"))

        result = {
            "data": created_pack,
            "meta": {
                "snapshot_at": snapshot_at,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_committee_evidence_pack": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    def upload_committee_evidence_files(
        self,
        *,
        session_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        raw_files = payload.get("files")
        if raw_files is None and any(key in payload for key in ("fileName", "filename", "name")):
            raw_files = [payload]
        if not isinstance(raw_files, list):
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Evidence files are required",
                "Committee evidence upload requires a files array.",
                precondition_failed="committee_evidence.files",
            )
        incoming_files = [item for item in raw_files if isinstance(item, dict)]
        if not incoming_files:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Evidence files are required",
                "Incoming files array cannot be empty.",
                precondition_failed="committee_evidence.files",
            )

        store = self.read_store
        session = store.get_agora_session(session_id) if store and hasattr(store, "get_agora_session") else None
        if session is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee session not found",
                f"Committee session {session_id} does not exist",
                precondition_failed="session_id",
            )

        existing_files = list((session.get("evidencePack") or {}).get("files") or [])
        self.validate_evidence_files(existing_files=existing_files, incoming_files=incoming_files)

        request_hash = self.stable_json_hash({
            "route": f"POST /bff/agora/committee/{session_id}/evidence-pack/files",
            "sessionId": session_id,
            "payload": incoming_files,
        })
        cached = self.check_idempotency(resolved_key, request_hash)
        if cached is not None:
            return cached

        snapshot_at = self.utc_now()
        uploaded_files = None
        if store is not None and hasattr(store, "append_agora_committee_evidence_files"):
            uploaded_files = store.append_agora_committee_evidence_files(
                session_id=session_id,
                files=incoming_files,
                actor_id=identity.operator_id,
                uploaded_at=snapshot_at,
            )
        pack_data = uploaded_files if isinstance(uploaded_files, dict) else {"sessionId": session_id, "files": incoming_files, "uploadedFiles": incoming_files}
        items_list = pack_data.get("newFiles") or pack_data.get("files") or incoming_files

        result = {
            "data": pack_data,
            "items": items_list,
            "meta": {
                "snapshot_at": snapshot_at,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_committee_evidence_files": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    # --- Committee Session Lifecycle & Memos (ASK-003, ASK-004) --- #

    def list_committee_sessions(self) -> Dict[str, Any]:
        return self.sem_list_payload("agora_sessions", "agora_committee_sessions", filter_mode="committee")

    def create_committee_session(
        self,
        *,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = self.stable_json_hash({"route": "POST /bff/agora/committee/sessions", "payload": payload})
        cached = self.check_idempotency(resolved_key, request_hash)
        if cached is not None:
            return cached

        now = self.utc_now()
        session_id = str(payload.get("sessionId") or payload.get("session_id") or f"committee-{uuid.uuid4().hex[:10]}")
        title = str(payload.get("title") or "Committee session").strip()
        store = self.read_store
        session = None
        if store is not None and hasattr(store, "create_agora_session"):
            session = store.create_agora_session(
                session_id=session_id,
                title=title,
                actor_id=identity.operator_id,
                payload={
                    **dict(payload),
                    "mode": "committee",
                    "status": "pending",
                    "participants": payload.get("participants") or [],
                    "quorumState": payload.get("quorumState") or "pending",
                    "consensusState": payload.get("consensusState") or "open",
                    "participantRoster": payload.get("participantRoster") or [],
                    "linkedRequestId": payload.get("linkedRequestId") or payload.get("linked_request_id"),
                },
                created_at=now,
            )
        if not session:
            session = {
                "id": session_id,
                "sessionId": session_id,
                "title": title,
                "mode": "committee",
                "status": "pending",
                "createdBy": identity.operator_id,
                "createdAt": now,
            }

        result = {
            "data": session,
            "meta": {
                "snapshot_at": now,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_committee_session_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    def get_committee_session(self, session_id: str) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        store = self.read_store
        session = store.get_agora_session(session_id) if store and hasattr(store, "get_agora_session") else None
        if session is None or str(session.get("mode") or "").strip() != "committee":
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee session not found",
                f"Committee session {session_id} does not exist",
                precondition_failed="session_id",
            )
        return {
            "data": session,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {"agora_committee_session_detail": {"status": "ok", "source": "bff_local"}},
            },
        }

    def open_committee_session(
        self,
        session_id: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        identity: Optional[OperatorIdentity] = None,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
    ) -> Any:
        payload = payload or {}
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = self.stable_json_hash({
            "route": f"POST /bff/agora/committee/sessions/{session_id}/open",
            "sessionId": session_id,
            "payload": payload,
        })
        cached = self.check_idempotency(resolved_key, request_hash)
        if cached is not None:
            return cached

        now = self.utc_now()
        store = self.read_store
        session = None
        if store is not None and hasattr(store, "open_committee_session"):
            session = store.open_committee_session(session_id, opened_at=now)
        if session is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee session not found",
                f"Committee session {session_id} does not exist",
                precondition_failed="session_id",
            )

        self.publish_sse_event("ask", "ask.session.started", {"sessionId": session_id, "mode": "committee"})
        result = {
            "data": session,
            "meta": {
                "snapshot_at": now,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_committee_session_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    def close_committee_session(
        self,
        session_id: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        identity: Optional[OperatorIdentity] = None,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
    ) -> Any:
        payload = payload or {}
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = self.stable_json_hash({
            "route": f"POST /bff/agora/committee/sessions/{session_id}/close",
            "sessionId": session_id,
            "payload": payload,
        })
        cached = self.check_idempotency(resolved_key, request_hash)
        if cached is not None:
            return cached

        now = self.utc_now()
        outcome = str(payload.get("outcome") or "").strip() or None
        memo_ids = payload.get("memoIds") or payload.get("memo_ids") or None
        store = self.read_store
        session = None
        if store is not None and hasattr(store, "close_committee_session"):
            session = store.close_committee_session(
                session_id,
                closed_at=now,
                outcome=outcome,
                memo_ids=memo_ids,
            )
        if session is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee session not found",
                f"Committee session {session_id} does not exist",
                precondition_failed="session_id",
            )

        self.publish_sse_event("ask", "ask.session.completed", {"sessionId": session_id, "mode": "committee", "outcome": outcome})
        result = {
            "data": session,
            "meta": {
                "snapshot_at": now,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_committee_session_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    def list_committee_session_memos(self, session_id: str) -> Dict[str, Any]:
        store = self.read_store
        session = store.get_agora_session(session_id) if store and hasattr(store, "get_agora_session") else None
        if session is None or str(session.get("mode") or "").strip() != "committee":
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee session not found",
                f"Committee session {session_id} does not exist",
                precondition_failed="session_id",
            )
        snapshot_at = self.utc_now()
        memos = store.list_committee_session_memos(session_id) if store and hasattr(store, "list_committee_session_memos") else []
        return {
            "items": memos,
            "page_info": {"next_page_token": None, "total": len(memos)},
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {"agora_committee_session_memos": {"status": "ok", "source": "bff_local"}},
            },
        }

    def submit_committee_session_memo(
        self,
        session_id: str,
        *,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
    ) -> Any:
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = self.stable_json_hash({
            "route": f"POST /bff/agora/committee/sessions/{session_id}/memos",
            "sessionId": session_id,
            "payload": payload,
        })
        cached = self.check_idempotency(resolved_key, request_hash)
        if cached is not None:
            return cached

        now = self.utc_now()
        store = self.read_store
        session = store.get_agora_session(session_id) if store and hasattr(store, "get_agora_session") else None
        if session is None or str(session.get("mode") or "").strip() != "committee":
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee session not found",
                f"Committee session {session_id} does not exist",
                precondition_failed="session_id",
            )

        memo_id = str(payload.get("memoId") or payload.get("memo_id") or "").strip() or f"memo-{uuid.uuid4().hex[:12]}"
        if store is not None and hasattr(store, "get_consult_memo") and store.get_consult_memo(memo_id) is not None:
            raise self.bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Committee memo id already exists",
                f"Memo {memo_id} already exists in the consult memo registry",
                precondition_failed="memo_id",
                suggestion="Retry with a new memoId or replay the original request with the same Idempotency-Key",
            )

        memo = None
        if store is not None and hasattr(store, "submit_committee_session_memo"):
            memo = store.submit_committee_session_memo(
                session_id,
                memo_id=memo_id,
                actor_id=identity.operator_id,
                payload=payload,
                created_at=now,
            )
        if not memo:
            memo = {"memoId": memo_id, "sessionId": session_id, **payload, "createdBy": identity.operator_id, "createdAt": now}

        result = {
            "data": memo,
            "meta": {
                "snapshot_at": now,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_committee_memo_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    def get_committee_session_memo(self, session_id: str, memo_id: str) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        store = self.read_store
        session = store.get_agora_session(session_id) if store and hasattr(store, "get_agora_session") else None
        if session is None or str(session.get("mode") or "").strip() != "committee":
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee session not found",
                f"Committee session {session_id} does not exist",
                precondition_failed="session_id",
            )
        memo = store.get_committee_session_memo(session_id, memo_id) if store and hasattr(store, "get_committee_session_memo") else None
        if memo is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee memo not found",
                f"Memo {memo_id} for session {session_id} does not exist",
                precondition_failed="memo_id",
            )
        return {
            "data": memo,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {"agora_committee_memo_detail": {"status": "ok", "source": "bff_local"}},
            },
        }

    def publish_committee_session_memo(
        self,
        session_id: str,
        memo_id: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        identity: Optional[OperatorIdentity] = None,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
    ) -> Any:
        payload = payload or {}
        self.reject_body_idempotency_key(payload)
        resolved_key = self.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = self.stable_json_hash({
            "route": f"POST /bff/agora/committee/sessions/{session_id}/memos/{memo_id}/publish",
            "sessionId": session_id,
            "memoId": memo_id,
            "payload": payload,
        })
        cached = self.check_idempotency(resolved_key, request_hash)
        if cached is not None:
            return cached

        now = self.utc_now()
        store = self.read_store
        session = store.get_agora_session(session_id) if store and hasattr(store, "get_agora_session") else None
        if session is None or str(session.get("mode") or "").strip() != "committee":
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee session not found",
                f"Committee session {session_id} does not exist",
                precondition_failed="session_id",
            )
        existing_memo = store.get_committee_session_memo(session_id, memo_id) if store and hasattr(store, "get_committee_session_memo") else None
        was_published = str((existing_memo or {}).get("status") or "").strip().lower() == "published"
        actor_id = identity.operator_id if identity else "system"
        memo = store.publish_committee_session_memo(
            session_id,
            memo_id,
            actor_id=actor_id,
            published_at=now,
        ) if store and hasattr(store, "publish_committee_session_memo") else None
        if memo is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee memo not found",
                f"Memo {memo_id} for session {session_id} does not exist",
                precondition_failed="memo_id",
            )
        if not was_published:
            correlation_id = str(
                payload.get("correlation_id")
                or payload.get("correlationId")
                or memo.get("correlation_id")
                or memo.get("correlationId")
                or memo.get("linked_request_id")
                or session_id
            ).strip()
            if store is not None and hasattr(store, "create_agora_handoff"):
                handoff = store.create_agora_handoff(
                    handoff_id=f"handoff-consult-{uuid.uuid4().hex[:12]}",
                    handoff_type="consult_memo_to_management_review",
                    source_route=f"/agora/committee/sessions/{session_id}/memos/{memo_id}",
                    source_entity={"type": "consult_memo", "id": memo_id, "sessionId": session_id},
                    destination_route=f"/consultation/memos/{memo_id}/management-review",
                    destination_queue="consult_memo_review",
                    priority=str(payload.get("priority") or "normal"),
                    payload={
                        "memoId": memo_id,
                        "sessionId": session_id,
                        "linkedRequestId": memo.get("linked_request_id"),
                        "publishedAt": memo.get("published_at"),
                        "correlationId": correlation_id,
                    },
                    actor_id=actor_id,
                    created_at=now,
                )
                self.publish_sse_event("ask", "consult_memo_published", {
                    "session_id": session_id,
                    "memo_id": memo_id,
                    "handoff_id": handoff.get("handoffId"),
                    "correlation_id": correlation_id,
                })
            self.publish_sse_event("ask", "ask.memo.published", {"sessionId": session_id, "memoId": memo_id})

        result = {
            "data": memo,
            "meta": {
                "snapshot_at": now,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "surfaces": {"agora_committee_memo_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        self.record_idempotency(resolved_key, request_hash, result)
        return result

    # --- Read Surface Projections & Semantic Lists --- #

    def list_skill_coaching_sessions(self) -> Dict[str, Any]:
        return self.sem_list_payload("agora_skill_coaching_sessions", "agora_skill_coaching_sessions")

    def list_persona_lab_runs(self) -> Dict[str, Any]:
        return self.sem_list_payload("agora_persona_lab_runs", "agora_persona_lab_runs")

    def list_postmortems(self) -> Dict[str, Any]:
        return self.sem_list_payload("postmortems", "agora_postmortems")

    def list_evaluation_suites(self) -> Dict[str, Any]:
        return self.sem_list_payload("agora_evaluation_suites", "agora_evaluation_suites")

    def list_evaluation_runs(self) -> Dict[str, Any]:
        return self.sem_list_payload("agora_evaluation_runs", "agora_evaluation_runs")

    def list_alerts_triage(self) -> Dict[str, Any]:
        return self.sem_empty_final_list("agora_alerts_triage")

    # --- SSE Streaming --- #

    def stream_channel_events(self, channel: str, *, last_event_id: Optional[str] = None) -> Any:
        if self._handle_sse_stream is not None:
            buf = self._sse_buffers.get(channel, [])
            subs = self._sse_subscribers.get(channel, [])
            return self._handle_sse_stream(channel, buf, subs, last_event_id)
        from fastapi.responses import StreamingResponse
        async def _dummy_stream():
            yield f": connected to {channel}\n\n"
        return StreamingResponse(_dummy_stream(), media_type="text/event-stream")

    # --- Session & Identity Operations --- #

    def list_sessions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        store = self.read_store
        items: List[Dict[str, Any]] = []
        if store is not None and hasattr(store, "list_agora_sessions") and callable(store.list_agora_sessions):
            # ``ReadSurfacePorts`` exposes the Agora session projection through
            # the canonical consultation read port.  That port's contract uses
            # ``statuses`` (plural), while this HTTP surface accepts one
            # ``status`` query value.  Read the projection without leaking the
            # HTTP-shaped keyword into the port and apply the single-value
            # predicate at this service boundary.
            items = list(store.list_agora_sessions() or [])
            if status:
                wanted_status = str(status).strip().lower()
                items = [
                    item
                    for item in items
                    if str(item.get("status") or item.get("lifecycle_state") or "").strip().lower()
                    == wanted_status
                ]
        if not items and self._local_sessions:
            items = list(self._local_sessions.values())
            if status:
                wanted_status = str(status).strip().lower()
                items = [
                    it
                    for it in items
                    if str(it.get("status") or it.get("lifecycle_state") or "").strip().lower()
                    == wanted_status
                ]
        return items

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        store = self.read_store
        if store is not None and hasattr(store, "get_agora_session") and callable(store.get_agora_session):
            sess = store.get_agora_session(session_id)
            if sess is not None:
                return sess
        return self._local_sessions.get(session_id)

    def create_session(
        self,
        *,
        session_id: str,
        title: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: str,
    ) -> Dict[str, Any]:
        store = self.read_store
        record = {
            "id": session_id,
            "sessionId": session_id,
            "title": title,
            "actorId": actor_id,
            "createdAt": created_at,
            "updatedAt": created_at,
            **payload,
        }
        self._local_sessions[session_id] = record
        if store is not None and hasattr(store, "create_agora_session") and callable(store.create_agora_session):
            res = store.create_agora_session(
                session_id=session_id,
                title=title,
                actor_id=actor_id,
                payload=payload,
                created_at=created_at,
            )
            if res:
                return res
        return record

    def list_session_messages(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        if self.get_session(session_id) is None:
            return None
        store = self.read_store
        if store is not None and hasattr(store, "list_agora_session_messages") and callable(store.list_agora_session_messages):
            msgs = store.list_agora_session_messages(session_id)
            if msgs is not None:
                return msgs
        return self._local_session_messages.get(session_id, [])

    def append_session_message(
        self,
        session_id: str,
        *,
        message_id: str,
        content: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: str,
    ) -> Optional[Dict[str, Any]]:
        if self.get_session(session_id) is None:
            return None
        store = self.read_store
        record = {
            "id": message_id,
            "messageId": message_id,
            "sessionId": session_id,
            "content": content,
            "actorId": actor_id,
            "createdAt": created_at,
            **payload,
        }
        self._local_session_messages.setdefault(session_id, []).append(record)
        if store is not None and hasattr(store, "append_agora_session_message") and callable(store.append_agora_session_message):
            res = store.append_agora_session_message(
                session_id,
                message_id=message_id,
                content=content,
                actor_id=actor_id,
                payload=payload,
                created_at=created_at,
            )
            if res:
                return res
        return record

    def close_session(
        self,
        session_id: str,
        *,
        closed_at: str,
        outcome: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if self.get_session(session_id) is None:
            return None
        store = self.read_store
        if session_id in self._local_sessions:
            self._local_sessions[session_id]["status"] = "closed"
            self._local_sessions[session_id]["closedAt"] = closed_at
            if outcome:
                self._local_sessions[session_id]["outcome"] = outcome
        if store is not None and hasattr(store, "close_agora_session") and callable(store.close_agora_session):
            res = store.close_agora_session(session_id, closed_at=closed_at, outcome=outcome)
            if res:
                return res
        return self._local_sessions.get(session_id)

    def list_handoffs(
        self,
        *,
        status: Optional[str] = None,
        handoff_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        store = self.read_store
        items: List[Dict[str, Any]] = []
        if store is not None and hasattr(store, "list_agora_handoffs") and callable(store.list_agora_handoffs):
            items = list(store.list_agora_handoffs(status=status, handoff_type=handoff_type) or [])
        if not items and self._local_handoffs:
            items = list(self._local_handoffs.values())
        return items

    # --- Insights & Memory Operations --- #

    def list_insights(self) -> List[Dict[str, Any]]:
        store = self.read_store
        items: List[Dict[str, Any]] = []
        if store is not None and hasattr(store, "list_agora_insights") and callable(store.list_agora_insights):
            items = list(store.list_agora_insights() or [])
        if not items and self._local_insights:
            items = list(self._local_insights.values())
        return items

    def get_insight(self, insight_id: str) -> Optional[Dict[str, Any]]:
        store = self.read_store
        if store is not None:
            if hasattr(store, "get_insight_card") and callable(store.get_insight_card):
                item = store.get_insight_card(insight_id)
                if item:
                    return item
            if hasattr(store, "list_agora_insights") and callable(store.list_agora_insights):
                for item in store.list_agora_insights():
                    if str(item.get("insight_id") or item.get("id") or "") == insight_id:
                        return item
        return self._local_insights.get(insight_id)

    def create_insight(
        self,
        *,
        insight_id: str,
        summary: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: str,
    ) -> Dict[str, Any]:
        store = self.read_store
        record = {
            "id": insight_id,
            "insightId": insight_id,
            "summary": summary,
            "actorId": actor_id,
            "createdAt": created_at,
            **payload,
        }
        self._local_insights[insight_id] = record
        if store is not None and hasattr(store, "create_agora_insight") and callable(store.create_agora_insight):
            res = store.create_agora_insight(
                insight_id=insight_id,
                summary=summary,
                actor_id=actor_id,
                payload=payload,
                created_at=created_at,
            )
            if res:
                return res
        return record

    def list_memory(self) -> List[Dict[str, Any]]:
        store = self.read_store
        items: List[Dict[str, Any]] = []
        if store is not None and hasattr(store, "list_agora_memory") and callable(store.list_agora_memory):
            items = list(store.list_agora_memory() or [])
        if not items and self._local_memory:
            items = list(self._local_memory.values())
        return items

    def get_memory_entry(self, memory_id: str) -> Optional[Dict[str, Any]]:
        store = self.read_store
        if store is not None and hasattr(store, "get_agora_memory_entry") and callable(store.get_agora_memory_entry):
            res = store.get_agora_memory_entry(memory_id)
            if res is not None:
                return res
        for item in self.list_memory():
            if str(item.get("memory_id") or item.get("id") or "") == memory_id:
                return item
        return self._local_memory.get(memory_id)

    # --- Command Submission & Event Coordination --- #

    def submit_action_command(
        self,
        *,
        route: str,
        entity_type: ObjectType,
        entity_id: str,
        action_id: str,
        resolved_key: str,
        identity: OperatorIdentity,
        payload: Dict[str, Any],
        command_type: CommandType,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        request_hash = self.stable_json_hash({
            "route": route,
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "action_id": action_id,
            "payload": payload,
        })
        if dry_run:
            command_id = f"dryrun-cmd-{uuid.uuid4().hex[:12]}"
            submitted_at = self.utc_now()
            tracking_url = f"/api/v1/operator/commands/{command_id}"
            receipt = {
                "command": command_type.value,
                "status": "accepted",
                "tracking_url": tracking_url,
                "trackingUrl": tracking_url,
            }
            data_payload = {
                "command_id": command_id,
                "commandId": command_id,
                "command": command_type.value,
                "status": "accepted",
                "accepted_at": submitted_at,
                "acceptedAt": submitted_at,
                "tracking_url": tracking_url,
                "trackingUrl": tracking_url,
                "receipt": receipt,
            }
            return {
                "commandId": command_id,
                "command": command_type.value,
                "status": "accepted",
                "acceptedAt": submitted_at,
                "data": data_payload,
                "meta": {
                    "dryRun": True,
                    "idempotencyKey": resolved_key,
                },
            }
        cached = self.check_idempotency(resolved_key, request_hash)
        if cached is not None:
            return cached

        command_id = str(uuid.uuid4())
        submitted_at = self.utc_now()
        target = TargetObject(type=entity_type, id=entity_id)
        request_payload = {
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "action_id": action_id,
            "payload": payload,
        }
        idempotency_record = IdempotencyRecord.reserve(
            idempotency_key=resolved_key,
            operation_type=f"bff.{command_type.value}",
            target_ref=f"{entity_type.value}:{entity_id}",
            request_payload=request_payload,
            trace_id=command_id,
        )
        audit_record = {
            "operator_id": identity.operator_id,
            "roles_at_submission": identity.roles,
            "action_id": action_id,
            "preconditions_checked": ["authentication", "authorization", "idempotency"],
            "timestamp": submitted_at,
            "idempotency_key": resolved_key,
            "command_id": command_id,
        }
        cmd_store = self.command_store
        if cmd_store is not None and hasattr(cmd_store, "submit_command"):
            cmd_store.submit_command(
                command_id=command_id,
                command_type=command_type,
                target=target,
                submitted_at=submitted_at,
                params={"action_id": action_id, **payload},
                audit_context=audit_record,
                foundation_context={"idempotency_record": idempotency_record.to_dict()},
            )

        audit_result = self._record_agora_audit_event(
            {
                "action": f"agora.{action_id}",
                "targetType": entity_type.value,
                "targetId": entity_id,
                "commandId": command_id,
                "actorId": identity.operator_id,
                "recordedAt": submitted_at,
                "idempotencyKey": resolved_key,
            }
        )

        tracking_url = f"/api/v1/operator/commands/{command_id}"
        receipt = {
            "command": command_type.value,
            "status": "accepted",
            "tracking_url": tracking_url,
            "trackingUrl": tracking_url,
        }
        data_payload = {
            "command_id": command_id,
            "commandId": command_id,
            "command": command_type.value,
            "status": "accepted",
            "accepted_at": submitted_at,
            "acceptedAt": submitted_at,
            "tracking_url": tracking_url,
            "trackingUrl": tracking_url,
            "receipt": receipt,
        }
        result: Dict[str, Any] = {
            "commandId": command_id,
            "command": command_type.value,
            "status": "accepted",
            "acceptedAt": submitted_at,
            "data": data_payload,
            "meta": {
                "snapshot_at": submitted_at,
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
            },
        }
        if audit_result:
            result["meta"]["audit"] = audit_result

        self.record_idempotency(resolved_key, request_hash, result)
        return result

    def publish_sse_event(self, channel: str, event_type: str, data: Dict[str, Any]) -> None:
        buf = self._sse_buffers.get(channel, [])
        subs = self._sse_subscribers.get(channel, [])
        self.publish_event_fn(buf, subs, event_type, data)

    def deterministic_ask_fallback(self, prompt: str) -> str:
        prompt_lower = (prompt or "").lower()
        if "risk" in prompt_lower or "var" in prompt_lower:
            return "Agora Sentinel reports portfolio risk parameters remain within nominal operating thresholds."
        if "market" in prompt_lower or "price" in prompt_lower:
            return "Agora Market Data projection indicates normal session continuity across active venues."
        return "Agora Assistant acknowledged query and recorded interaction context in session memory."
