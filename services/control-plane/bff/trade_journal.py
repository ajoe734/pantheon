"""Persona Trade Journal BFF projection and governed-command facade."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

_LOCK = Lock()
def _load(path_env: str) -> list[dict[str, Any]] | None:
    path = os.getenv(path_env, "").strip()
    if not path or not Path(path).is_file():
        return None
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [dict(x) for x in raw if isinstance(x, Mapping)]
    for key in ("items", "projections", "reflections", "patterns", "lessons"):
        if isinstance(raw.get(key), list):
            return [dict(x) for x in raw[key] if isinstance(x, Mapping)]
    if isinstance(raw, Mapping):
        return [dict(x) for x in raw.values() if isinstance(x, Mapping)]
    return []


def _err(status: int, code: str, message: str, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "retryable": retryable}})


def _allowed(identity: Any, persona_id: str) -> bool:
    if "admin" in identity.roles:
        return True
    scoped = identity.claims.get("persona_ids") or identity.claims.get("personaIds")
    return not scoped or persona_id in scoped


def _mask(value: Any, identity: Any) -> Any:
    if {"operator", "approver", "admin", "reviewer"}.intersection(identity.roles):
        return value
    sensitive = {"account", "account_id", "accountId", "broker_account", "brokerAccount"}
    if isinstance(value, dict):
        return {k: ("***" if k in sensitive else _mask(v, identity)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v, identity) for v in value]
    return value


def _command_records() -> tuple[Path | None, list[dict[str, Any]]]:
    raw_path = os.getenv("PANTHEON_BFF_TRADE_JOURNAL_COMMAND_STORE", "").strip()
    if not raw_path:
        return None, []
    path = Path(raw_path)
    try:
        if not path.is_file():
            return None, []
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError):
        return None, []
    return path, [record for record in records if isinstance(record, dict)]


def _append_command(path: Path, record: dict[str, Any]) -> bool:
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        return False
    return True


def create_trade_journal_router(*, extract_identity: Callable[..., Any], require_read_role: Callable[[Any], None], require_operator_role: Callable[[Any], None]) -> APIRouter:
    router = APIRouter()

    def identity(request: Request) -> Any:
        return extract_identity(request.headers.get("Authorization"), session_cookie=request.cookies.get("pantheon_session"))

    def read_items(request: Request, persona_id: str, env: str) -> tuple[Any, list[dict[str, Any]] | None]:
        who = identity(request)
        require_read_role(who)
        if not _allowed(who, persona_id):
            return who, None
        return who, _load(env)

    @router.get("/bff/personas/{persona_id}/trade-journal")
    async def journal_list(request: Request, persona_id: str, cursor: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), environment: str | None = None, strategy: str | None = None, instrument: str | None = None, side: str | None = None, status: str | None = None, coverage_state: str | None = None):
        who, items = read_items(request, persona_id, "PANTHEON_BFF_TRADE_EPISODES_STORE")
        if not _allowed(who, persona_id): return _err(403, "FORBIDDEN", "Cross-persona access denied")
        if items is None: return _err(503, "DEPENDENCY_UNAVAILABLE", "Trade episode projection is unavailable", retryable=True)
        filters = {"environment": environment, "strategy_id": strategy, "instrument_id": instrument, "side": side, "status": status}
        rows = [x for x in items if x.get("persona_id") == persona_id and all(v is None or x.get(k) == v for k, v in filters.items())]
        if coverage_state: rows = [x for x in rows if x.get("coverage_state") == coverage_state or any(v.get("state") == coverage_state for v in (x.get("coverage") or {}).values() if isinstance(v, dict))]
        page = rows[cursor:cursor + limit]
        state = "complete" if all(not x.get("missing_refs") for x in page) else "partial"
        return {"data": _mask(page, who), "page_info": {"next_cursor": cursor + limit if cursor + limit < len(rows) else None, "has_more": cursor + limit < len(rows)}, "meta": {"coverage_state": state, "source": "telemetry_projection", "count": len(rows)}}

    @router.get("/bff/personas/{persona_id}/trade-journal/{episode_id}")
    async def journal_detail(request: Request, persona_id: str, episode_id: str, environment: str | None = None):
        who, items = read_items(request, persona_id, "PANTHEON_BFF_TRADE_EPISODES_STORE")
        if not _allowed(who, persona_id): return _err(403, "FORBIDDEN", "Cross-persona access denied")
        if items is None: return _err(503, "DEPENDENCY_UNAVAILABLE", "Trade episode projection is unavailable", retryable=True)
        row = next((x for x in items if x.get("persona_id") == persona_id and x.get("trade_episode_id") == episode_id and (environment is None or x.get("environment") == environment)), None)
        if row is None: return _err(404, "RESOURCE_NOT_FOUND", "Trade episode not found")
        return {"data": _mask(row, who), "meta": {"source": "telemetry_projection", "source_confidence": row.get("source_confidence", "canonical_refs")}}

    @router.get("/bff/personas/{persona_id}/trade-reflections")
    async def reflections(request: Request, persona_id: str, cursor: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), environment: str | None = None, review_state: str | None = None):
        who, items = read_items(request, persona_id, "PANTHEON_BFF_TRADE_REFLECTIONS_STORE")
        if not _allowed(who, persona_id): return _err(403, "FORBIDDEN", "Cross-persona access denied")
        if items is None: return _err(503, "DEPENDENCY_UNAVAILABLE", "Trade reflection store is unavailable", retryable=True)
        rows = [x for x in items if x.get("persona_id") == persona_id and (environment is None or x.get("environment") == environment) and (review_state is None or x.get("review_state") == review_state)]
        return {"data": _mask(rows[cursor:cursor + limit], who), "page_info": {"next_cursor": cursor + limit if cursor + limit < len(rows) else None}, "meta": {"source": "persona_reflection"}}

    @router.get("/bff/personas/{persona_id}/trade-patterns")
    async def patterns(request: Request, persona_id: str, environment: str | None = None):
        who, items = read_items(request, persona_id, "PANTHEON_BFF_TRADE_PATTERNS_STORE")
        if not _allowed(who, persona_id): return _err(403, "FORBIDDEN", "Cross-persona access denied")
        if items is None: return _err(503, "DEPENDENCY_UNAVAILABLE", "Trade pattern store is unavailable", retryable=True)
        rows = [x for x in items if x.get("persona_id") == persona_id and (environment is None or x.get("environment") == environment)]
        return {"data": _mask(rows, who), "meta": {"source": "persona_pattern_review", "coverage_state": "complete" if rows else "unavailable"}}

    async def command(request: Request, persona_id: str, resource_id: str, action: str, idempotency_key: str | None) -> JSONResponse | dict[str, Any]:
        who = identity(request); require_operator_role(who)
        if not _allowed(who, persona_id): return _err(403, "FORBIDDEN", "Cross-persona access denied")
        if not idempotency_key: return _err(400, "VALIDATION_FAILED", "Idempotency-Key is required")
        body = await request.json()
        if not str(body.get("reason", "")).strip(): return _err(422, "VALIDATION_FAILED", "reason is required")
        resource_env = "PANTHEON_BFF_TRADE_EPISODES_STORE" if action == "reflection.retry" else "PANTHEON_BFF_TRADE_LESSONS_STORE"
        resources = _load(resource_env)
        if resources is None:
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Governed command target projection is unavailable", retryable=True)
        id_field = "trade_episode_id" if action == "reflection.retry" else "lesson_id"
        resource = next((item for item in resources if item.get("persona_id") == persona_id and item.get(id_field) == resource_id), None)
        if resource is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Governed command target not found")
        allowed_states = {
            "reflection.retry": {"reflection_failed", "retryable"},
            "lesson.submit_review": {"draft", "proposed"},
            "lesson.decide": {"pending_review", "in_review", "submitted"},
        }
        state = str(resource.get("status") or resource.get("review_state") or "")
        if state not in allowed_states[action]:
            return _err(409, "INVALID_TRANSITION", f"{action} is not allowed from state {state or 'unknown'}")
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        key = f"{who.operator_id}:{action}:{persona_id}:{resource_id}:{idempotency_key}"
        with _LOCK:
            store_path, records = _command_records()
            if store_path is None:
                return _err(503, "DEPENDENCY_UNAVAILABLE", "Durable trade journal command owner is unavailable", retryable=True)
            prior = next((record for record in records if record.get("idempotency_scope") == key), None)
            if prior and prior["request_hash"] != digest: return _err(409, "IDEMPOTENCY_CONFLICT", "Idempotency key was reused with a different request")
            if prior:
                response = {"data": prior["receipt"], "meta": {"idempotent_replay": True, "audit": {"durable": True, "record_ref": prior["audit_record_ref"]}}}
                return response
            now = datetime.now(timezone.utc).isoformat()
            receipt = {"receipt_id": hashlib.sha256(key.encode()).hexdigest()[:24], "action": action, "persona_id": persona_id, "resource_id": resource_id, "status": "accepted", "reason": body["reason"], "actor": who.operator_id, "created_at": now, "facts_snapshot_ref": body.get("facts_snapshot_ref")}
            audit_ref = f"trade-journal-command:{receipt['receipt_id']}"
            record = {"idempotency_scope": key, "request_hash": digest, "receipt": receipt, "audit_record_ref": audit_ref, "target_state_at_admission": state}
            if not _append_command(store_path, record):
                return _err(503, "DEPENDENCY_UNAVAILABLE", "Durable trade journal command owner rejected the command", retryable=True)
            response = {"data": receipt, "meta": {"idempotent_replay": False, "audit": {"durable": True, "record_ref": audit_ref}}}
        return response

    @router.post("/bff/personas/{persona_id}/trade-journal/{episode_id}/reflection:retry", status_code=202)
    async def retry(request: Request, persona_id: str, episode_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key")): return await command(request, persona_id, episode_id, "reflection.retry", idempotency_key)
    @router.post("/bff/personas/{persona_id}/trade-lessons/{lesson_id}:submit-review", status_code=202)
    async def submit(request: Request, persona_id: str, lesson_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key")): return await command(request, persona_id, lesson_id, "lesson.submit_review", idempotency_key)
    @router.post("/bff/personas/{persona_id}/trade-lessons/{lesson_id}:decide", status_code=202)
    async def decide(request: Request, persona_id: str, lesson_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key")): return await command(request, persona_id, lesson_id, "lesson.decide", idempotency_key)
    return router
