"""Persona Trade Journal BFF projection and governed-command facade."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

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


def _dispatch_command(payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    base_url = os.getenv("PANTHEON_TRADE_JOURNAL_COMMAND_OWNER_URL", "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("command owner is not configured")
    req = urllib_request.Request(
        f"{base_url}/v1/trade-journal/commands",
        data=json.dumps(dict(payload)).encode("utf-8"),
        headers={"Content-Type": "application/json", "Idempotency-Key": str(payload["idempotency_key"])},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib_error.HTTPError as exc:
        return exc.code, json.loads(exc.read())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("command owner is unavailable") from exc


def create_trade_journal_router(*, extract_identity: Callable[..., Any], require_read_role: Callable[[Any], None], require_operator_role: Callable[[Any], None], dispatch_command: Callable[[Mapping[str, Any]], tuple[int, dict[str, Any]]] = _dispatch_command) -> APIRouter:
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
        payload = {"action": action, "persona_id": persona_id, "resource_id": resource_id, "reason": body["reason"], "facts_snapshot_ref": body.get("facts_snapshot_ref"), "decision": body.get("decision"), "actor": who.operator_id, "idempotency_key": idempotency_key}
        try:
            status, downstream = dispatch_command(payload)
        except RuntimeError:
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Durable trade journal command owner is unavailable", retryable=True)
        if status not in (200, 202):
            error = downstream.get("error") if isinstance(downstream, Mapping) else None
            if isinstance(error, Mapping):
                return _err(status, str(error.get("code", "COMMAND_REJECTED")), str(error.get("message", "Command owner rejected the command")), retryable=bool(error.get("retryable")))
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Durable trade journal command owner returned an invalid response", retryable=True)
        receipt = downstream.get("data")
        audit = downstream.get("audit") or downstream.get("meta", {}).get("audit")
        if not isinstance(receipt, Mapping) or receipt.get("status") != "accepted" or not isinstance(audit, Mapping) or not audit.get("record_ref"):
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Durable command receipt or audit evidence is missing", retryable=True)
        return {"data": dict(receipt), "meta": {"idempotent_replay": bool(downstream.get("idempotent_replay") or downstream.get("meta", {}).get("idempotent_replay")), "audit": dict(audit)}}

    @router.post("/bff/personas/{persona_id}/trade-journal/{episode_id}/reflection:retry", status_code=202)
    async def retry(request: Request, persona_id: str, episode_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key")): return await command(request, persona_id, episode_id, "reflection.retry", idempotency_key)
    @router.post("/bff/personas/{persona_id}/trade-lessons/{lesson_id}:submit-review", status_code=202)
    async def submit(request: Request, persona_id: str, lesson_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key")): return await command(request, persona_id, lesson_id, "lesson.submit_review", idempotency_key)
    @router.post("/bff/personas/{persona_id}/trade-lessons/{lesson_id}:decide", status_code=202)
    async def decide(request: Request, persona_id: str, lesson_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key")): return await command(request, persona_id, lesson_id, "lesson.decide", idempotency_key)
    return router
