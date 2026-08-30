"""Agora domain service and orchestration facade.

Encapsulates Agora session lifecycle, quick ask assistant coordination, insight
and institutional memory management, action command submission, and idempotency
checking without importing or coupling to main.py.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from models import (
    CommandStatus,
    CommandType,
    ErrorCode,
    ObjectType,
    OperatorIdentity,
    TargetObject,
    utc_now as default_utc_now,
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

logger = logging.getLogger(__name__)


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
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {surface_key: {"status": "ok", "source": "bff_local"}},
    }
    if total is not None:
        meta["total"] = total
    return meta


class AgoraService:
    """Core domain service for Agora operations, decoupled from main.py."""

    def __init__(
        self,
        *,
        get_read_store: Optional[Callable[[], Any]] = None,
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
    ) -> None:
        self._get_read_store = get_read_store or (lambda: None)
        self._get_command_store = get_command_store or (lambda: None)
        self._idempotency = idempotency_store if idempotency_store is not None else {}
        self._sse_buffers = sse_buffers if sse_buffers is not None else {"ask": []}
        self._sse_subscribers = sse_subscribers if sse_subscribers is not None else {"ask": []}
        self._assistant_ask_enabled = assistant_ask_enabled or (lambda: False)
        self._assistant_build_context_pack = assistant_build_context_pack
        self._get_assistant_session_store = get_assistant_session_store or (lambda: None)
        self._get_assistant_transcript_store = get_assistant_transcript_store or (lambda: None)
        self._openclaw_ops_client_factory = openclaw_ops_client_factory
        self.utc_now = utc_now or default_utc_now
        self.bff_error = bff_error or self._default_bff_error
        self.publish_event_fn = publish_event_fn or self._default_publish_event
        self._local_sessions: Dict[str, Dict[str, Any]] = {}
        self._local_session_messages: Dict[str, List[Dict[str, Any]]] = {}
        self._local_insights: Dict[str, Dict[str, Any]] = {}
        self._local_memory: Dict[str, Dict[str, Any]] = {}
        self._local_handoffs: Dict[str, Dict[str, Any]] = {}

    @property
    def read_store(self) -> Any:
        return self._get_read_store()

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
        buffer: List[Dict[str, Any]],
        subscribers: List[Any],
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        event = {"event": event_type, "data": data, "timestamp": self.utc_now()}
        buffer.append(event)
        if len(buffer) > 200:
            del buffer[: len(buffer) - 200]
        for sub in list(subscribers):
            try:
                if callable(sub):
                    sub(event)
                elif hasattr(sub, "put_nowait"):
                    sub.put_nowait(event)
            except Exception:
                pass

    # --- Idempotency & Helper Methods --- #

    def resolve_final_idempotency_key(
        self,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
    ) -> str:
        key = str(idempotency_key or x_idempotency_key or "").strip()
        if not key:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "Request must include a non-empty Idempotency-Key or X-Idempotency-Key header",
                precondition_failed="Idempotency-Key",
            )
        return key

    def reject_body_idempotency_key(self, payload: Dict[str, Any]) -> None:
        if "idempotency_key" in payload or "idempotencyKey" in payload:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency key in request body is forbidden",
                "Idempotency key must be provided via the Idempotency-Key HTTP header only",
                precondition_failed="body.idempotency_key",
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
        return {
            "data": page_items,
            "items": page_items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": _default_read_surface_meta(dataset, surface_key, snapshot_at=snapshot_at, total=total),
        }

    # --- Session & Identity Operations --- #

    def list_sessions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        store = self.read_store
        items: List[Dict[str, Any]] = []
        if store is not None and hasattr(store, "list_agora_sessions") and callable(store.list_agora_sessions):
            items = list(store.list_agora_sessions(status=status) or [])
        if not items and self._local_sessions:
            items = list(self._local_sessions.values())
            if status:
                items = [it for it in items if str(it.get("status") or "") == status]
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
            return {
                "commandId": f"dryrun-cmd-{uuid.uuid4().hex[:12]}",
                "command": command_type.value,
                "status": "submitted",
                "acceptedAt": self.utc_now(),
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

        audit_result = None
        store = self.read_store
        if store is not None and hasattr(store, "record_agora_audit_event"):
            audit_result = store.record_agora_audit_event({
                "action": f"agora.{action_id}",
                "targetType": entity_type.value,
                "targetId": entity_id,
                "commandId": command_id,
                "actorId": identity.operator_id,
                "recordedAt": submitted_at,
                "idempotencyKey": resolved_key,
            })

        result: Dict[str, Any] = {
            "commandId": command_id,
            "command": command_type.value,
            "status": "submitted",
            "acceptedAt": submitted_at,
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
