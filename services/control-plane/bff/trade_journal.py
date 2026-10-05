"""Persona Trade Journal BFF projection and governed-command facade."""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import quote, urlencode

from .command_adapters.base import ActionUnavailableError, bound_tenant

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

def _err(status: int, code: str, message: str, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "retryable": retryable}})


def _allowed(identity: Any, persona_id: str) -> bool:
    if "admin" in identity.roles:
        return True
    scoped = identity.claims.get("persona_ids", identity.claims.get("personaIds"))
    return scoped is None or (isinstance(scoped, list) and persona_id in scoped)


def _mask(value: Any, identity: Any) -> Any:
    if {"operator", "approver", "admin", "reviewer"}.intersection(identity.roles):
        return value
    sensitive = {"account", "account_id", "accountId", "broker_account", "brokerAccount"}
    if isinstance(value, dict):
        return {k: ("***" if k in sensitive else _mask(v, identity)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v, identity) for v in value]
    return value


def _episode_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt canonical projection names without inventing missing measures."""
    if not isinstance(row.get("coverage", {}), Mapping):
        raise ValueError("invalid projection coverage")
    if isinstance(row.get("invalidation_conditions"), list) and not all(isinstance(item, str) for item in row["invalidation_conditions"]):
        raise ValueError("invalid projection invalidation conditions")
    result = dict(row)
    for target, source in {"requested_qty": "requested_quantity", "filled_qty": "filled_quantity",
                           "remaining_qty": "remaining_quantity", "return": "return_percent",
                           "holding_duration": "holding_duration_seconds"}.items():
        if source in row:
            result[target] = row[source]
    if isinstance(row.get("rejects"), list):
        result["rejects"] = len(row["rejects"])
    if isinstance(row.get("invalidation_conditions"), list):
        result["invalidation_conditions"] = "\n".join(row["invalidation_conditions"])
    if isinstance(row.get("coverage"), Mapping) and "state" in row["coverage"]:
        result["coverage"] = {"telemetry": dict(row["coverage"])}
    return result


def _reflection_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """Translate persisted owner field names to the existing browser DTO."""
    if not all(isinstance(row.get(key), str) for key in ("reflection_id", "trigger")):
        raise ValueError("invalid reflection identity")
    if not isinstance(row.get("mistakes"), list) or not all(isinstance(item, str) for item in row["mistakes"]):
        raise ValueError("invalid reflection mistakes")
    for key in ("counterfactuals", "lesson_candidates"):
        if not isinstance(row.get(key), list) or not all(isinstance(item, Mapping) for item in row[key]):
            raise ValueError("invalid reflection evidence")
    return {
        **row,
        "prompt_version": row.get("prompt_version", row.get("version")),
        "counterfactuals": [
            {**item, "action": item.get("action", item.get("alternative_action")),
             "impact": item.get("impact", item.get("estimated_impact")),
             "assumption": item.get("assumption", item.get("assumptions"))}
            for item in row.get("counterfactuals", [])
        ],
        "lesson_candidates": [
            {**item, "id": item.get("id", item.get("lesson_candidate_id"))}
            for item in row.get("lesson_candidates", [])
        ],
    }


def _decode_command_owner_body(raw: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("command owner returned invalid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise RuntimeError("command owner returned a non-object body")
    return dict(decoded)


def _http_call(url: str, data: dict[str, Any] | None, headers: dict[str, str], method: str = "POST") -> tuple[int, dict[str, Any]]:
    req = urllib_request.Request(url, data=json.dumps(data).encode("utf-8") if data is not None else None, headers=headers, method=method)
    try:
        with urllib_request.urlopen(req, timeout=5) as r: return r.status, _decode_command_owner_body(r.read())
    except urllib_error.HTTPError as e:
        try:
            return e.code, _decode_command_owner_body(e.read())
        except RuntimeError as decode_error:
            raise RuntimeError("command owner returned an invalid error response") from decode_error
    except (OSError, RuntimeError) as exc:
        raise RuntimeError("command owner is unavailable") from exc


def read_context_episode(episode_id: str, authorization: str | None, tenant_id: str, *, required: bool = False) -> dict[str, Any] | None:
    """Read one canonical context artifact, never a BFF projection file.

    Absence of this optional owner preserves Governance journal resolution;
    a focused Persona journal source instead requires Telemetry availability.
    A configured owner's rejection/outage must never become a fallback read.
    """
    base = os.getenv("PANTHEON_TELEMETRY_API_URL", "").strip().rstrip("/")
    if not base:
        if required:
            raise RuntimeError("Telemetry context owner is not configured")
        return None
    tenant = bound_tenant({}, tenant_id, authorization)
    status, row = _http_call(
        f"{base}/api/telemetry/trade-episodes/{quote(episode_id, safe='')}", None,
        {"Authorization": authorization or "", "X-Tenant-Id": tenant}, method="GET",
    )
    if status == 404:
        return None
    if status != 200:
        raise ActionUnavailableError("Telemetry context read rejected", error_code="DEPENDENCY_UNAVAILABLE", downstream_status=status)
    if not isinstance(row, Mapping) or row.get("tenant_id") != tenant or row.get("trade_episode_id") != episode_id:
        raise RuntimeError("Telemetry context owner returned an invalid scope")
    return dict(row)


def _dispatch_command(payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    legacy = os.getenv("PANTHEON_TRADE_JOURNAL_COMMAND_OWNER_URL", "").strip().rstrip("/")
    if legacy:
        return _http_call(f"{legacy}/v1/trade-journal/commands", dict(payload), {"Content-Type": "application/json", "Idempotency-Key": str(payload["idempotency_key"])})
    act, p_id, r_id = payload.get("action"), payload.get("persona_id"), payload.get("resource_id")
    auth_hdr = {"Authorization": payload["authorization"]} if payload.get("authorization") else {}
    idemp_hdr = {"Idempotency-Key": str(payload["idempotency_key"])}
    if act == "reflection.retry":
        url = (os.getenv("PERSONA_URL") or os.getenv("PANTHEON_PERSONA_SERVICE_URL") or "").strip().rstrip("/")
        if not url: raise RuntimeError("persona owner is not configured")
        return _http_call(f"{url}/api/personas/{p_id}/trade-journal/{r_id}/reflection:retry", {"reason": payload.get("reason"), "facts_snapshot_ref": payload.get("facts_snapshot_ref")}, {"Content-Type": "application/json", **idemp_hdr, **auth_hdr})
    if act in {"lesson.submit_review", "lesson.decide"}:
        url = (os.getenv("PANTHEON_MEMORY_API_URL") or os.getenv("PANTHEON_MEMORY_SERVICE_URL") or os.getenv("MEMORY_URL") or "").strip().rstrip("/")
        if not url: raise RuntimeError("memory owner is not configured")
        sub = act == "lesson.submit_review"
        path = f"{url}/api/memory/trade-lessons/{r_id}/" + ("submit-review" if sub else "decide")
        body = {"reason": payload.get("reason")} if sub else {"action": payload.get("decision") or "endorse", "operator_id": payload.get("actor") or "operator", "reason": payload.get("reason"), "audit_receipt_id": payload.get("facts_snapshot_ref") or "gov-approval-default", "episodes": payload.get("episodes"), "target_env": payload.get("target_env"), "promotion_stage": payload.get("promotion_stage")}
        st, res = _http_call(path, body, {"Content-Type": "application/json", **idemp_hdr, **auth_hdr})
        if st not in (200, 202): return st, res
        rev = res.get("review_state", "accepted")
        return 202, {"data": {"receipt_id": f"lesson-{r_id}", "action": act, "persona_id": res.get("persona_id", p_id), "resource_id": r_id, "status": "accepted", "review_state": rev}, "audit": {"durable": True, "record_ref": f"memory:trade-lesson:{r_id}:{rev}"}, "meta": res.get("meta", {})}
    raise RuntimeError(f"unsupported action: {act}")


def create_trade_journal_router(*, extract_identity: Callable[..., Any], require_read_role: Callable[[Any], None], require_operator_role: Callable[[Any], None], dispatch_command: Callable[[Mapping[str, Any]], tuple[int, dict[str, Any]]] = _dispatch_command) -> APIRouter:
    router = APIRouter()

    def identity(request: Request) -> Any:
        return extract_identity(request.headers.get("Authorization"), session_cookie=request.cookies.get("pantheon_session"))

    def read_owner(request: Request, persona_id: str, owner: str, path: str, params: Mapping[str, Any]):
        who = identity(request)
        require_read_role(who)
        if not _allowed(who, persona_id):
            return who, None, _err(403, "FORBIDDEN", "Cross-persona access denied")
        authorization = request.headers.get("Authorization")
        if not authorization and request.cookies.get("pantheon_session"):
            authorization = f"Bearer {request.cookies['pantheon_session']}"
        try:
            tenant = bound_tenant({}, request.headers.get("X-Tenant-Id"), authorization)
        except ActionUnavailableError:
            return who, None, _err(403, "FORBIDDEN", "A verified caller tenant is required")
        base = ((os.getenv("PERSONA_URL") or os.getenv("PANTHEON_PERSONA_SERVICE_URL"))
                if owner == "persona" else os.getenv("PANTHEON_TELEMETRY_API_URL"))
        if not base:
            return who, None, _err(503, "DEPENDENCY_UNAVAILABLE", f"{owner} owner is not configured", retryable=True)
        query = urlencode({k: v for k, v in params.items() if v is not None})
        try:
            status, body = _http_call(
                base.rstrip("/") + path + ("?" + query if query else ""), None,
                {"Authorization": authorization or "", "X-Tenant-Id": tenant}, method="GET",
            )
        except RuntimeError:
            return who, None, _err(503, "DEPENDENCY_UNAVAILABLE", f"{owner} owner is unavailable", retryable=True)
        if not isinstance(body, Mapping):
            return who, None, _err(503, "DEPENDENCY_UNAVAILABLE", "Invalid owner response", retryable=True)
        if status != 200:
            code = {401: "AUTH_REQUIRED", 403: "FORBIDDEN", 404: "RESOURCE_NOT_FOUND"}.get(status, "DEPENDENCY_UNAVAILABLE")
            return who, None, _err(status if status in (400, 401, 403, 404, 422) else 503, code, f"{owner} owner rejected the read", retryable=status >= 500)
        if owner == "persona" and (not isinstance(body.get("meta"), Mapping) or body["meta"].get("tenant_id") != tenant):
            return who, None, _err(503, "DEPENDENCY_UNAVAILABLE", "Persona owner tenant binding is unavailable", retryable=True)
        return who, tenant, body

    def valid_rows(rows: Any, persona_id: str, tenant: str, *, tenant_required: bool) -> bool:
        return isinstance(rows, list) and all(
            isinstance(row, Mapping) and row.get("persona_id") == persona_id
            and (row.get("tenant_id") == tenant if tenant_required else row.get("tenant_id", tenant) == tenant)
            for row in rows
        )

    @router.get("/bff/personas/{persona_id}/trade-journal")
    def journal_list(request: Request, persona_id: str, cursor: str | None = None, limit: int = Query(20, ge=1, le=100), environment: str | None = None, strategy: str | None = None, instrument: str | None = None, side: str | None = None, status: str | None = None, coverage_state: str | None = None):
        who, tenant, result = read_owner(request, persona_id, "telemetry", "/api/telemetry/trade-episodes", {
            "persona_id": persona_id, "cursor": None if cursor in (None, "", "0") else cursor,
            "limit": limit, "environment": environment, "strategy_id": strategy,
            "instrument_id": instrument, "side": side, "status": status, "coverage_state": coverage_state,
        })
        if isinstance(result, JSONResponse):
            return result
        rows = result.get("projections")
        next_cursor = result.get("next_cursor")
        count = result.get("count")
        if (not valid_rows(rows, persona_id, tenant, tenant_required=True)
                or (next_cursor is not None and not isinstance(next_cursor, str))
                or type(count) is not int or count < len(rows)):
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Invalid telemetry owner projection", retryable=True)
        try:
            rows = [_episode_view(row) for row in rows]
        except (TypeError, ValueError):
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Invalid telemetry owner projection", retryable=True)
        state = "partial" if any(
            row.get("missing_refs") or any(
                isinstance(section, Mapping) and section.get("state") != "complete"
                for section in (row.get("coverage") or {}).values()
            ) for row in rows
        ) else "complete"
        return {"data": _mask(rows, who), "page_info": {"next_cursor": next_cursor, "has_more": bool(next_cursor)}, "meta": {"coverage_state": state, "source": "telemetry_projection", "count": count}}

    @router.get("/bff/personas/{persona_id}/trade-journal/{episode_id}")
    def journal_detail(request: Request, persona_id: str, episode_id: str, environment: str | None = None):
        who, tenant, row = read_owner(request, persona_id, "telemetry", f"/api/telemetry/trade-episodes/{quote(episode_id, safe='')}", {})
        if isinstance(row, JSONResponse):
            return row
        if not valid_rows([row], persona_id, tenant, tenant_required=True) or row.get("trade_episode_id") != episode_id:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade episode not found")
        if environment is not None and row.get("environment") != environment:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade episode not found")
        try:
            view = _episode_view(row)
        except (TypeError, ValueError):
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Invalid telemetry owner projection", retryable=True)
        return {"data": _mask(view, who), "meta": {"source": "telemetry_projection", "source_confidence": row.get("source_confidence", "canonical_refs")}}

    @router.get("/bff/personas/{persona_id}/trade-reflections")
    def reflections(request: Request, persona_id: str, cursor: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), environment: str | None = None, review_state: str | None = None):
        who, tenant, result = read_owner(request, persona_id, "persona", f"/api/personas/{quote(persona_id, safe='')}/trade-reflections", {"environment": environment, "review_state": review_state})
        if isinstance(result, JSONResponse):
            return result
        rows = result.get("data")
        # Persona owner authenticates tenant before loading its durable metadata.
        if not valid_rows(rows, persona_id, tenant, tenant_required=False):
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Invalid Persona owner reflections", retryable=True)
        try:
            page = [_reflection_view(row) for row in rows[cursor:cursor + limit]]
        except (TypeError, ValueError):
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Invalid Persona reflection evidence", retryable=True)
        return {"data": _mask(page, who), "page_info": {"next_cursor": cursor + limit if cursor + limit < len(rows) else None}, "meta": {"source": "persona_reflection"}}

    @router.get("/bff/personas/{persona_id}/trade-patterns")
    def patterns(request: Request, persona_id: str, environment: str | None = None):
        who, tenant, result = read_owner(request, persona_id, "persona", f"/api/personas/{quote(persona_id, safe='')}/trade-reflections", {"environment": environment})
        if isinstance(result, JSONResponse):
            return result
        rows = result.get("data")
        if not valid_rows(rows, persona_id, tenant, tenant_required=False):
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Invalid Persona owner reflections", retryable=True)
        # Show actual saved multi-episode reviews, not an unowned pattern file or
        # BFF-generated conclusions/confidence. The existing owner remains sole writer.
        rows = [row for row in rows if row.get("trigger") == "scheduled_pattern"]
        try:
            views = [_reflection_view(row) for row in rows]
        except (TypeError, ValueError):
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Invalid Persona reflection evidence", retryable=True)
        return {"data": _mask(views, who), "meta": {"source": "persona_reflection", "coverage_state": "complete" if rows else "empty"}}

    async def command(request: Request, persona_id: str, resource_id: str, action: str, idempotency_key: str | None) -> JSONResponse | dict[str, Any]:
        who = identity(request); require_operator_role(who)
        if not _allowed(who, persona_id): return _err(403, "FORBIDDEN", "Cross-persona access denied")
        if not idempotency_key: return _err(400, "VALIDATION_FAILED", "Idempotency-Key is required")
        body = await request.json()
        if not str(body.get("reason", "")).strip(): return _err(422, "VALIDATION_FAILED", "reason is required")
        payload = {
            "action": action, "persona_id": persona_id, "resource_id": resource_id,
            "reason": body["reason"], "facts_snapshot_ref": body.get("facts_snapshot_ref"),
            "decision": body.get("decision"), "variance_attribution": body.get("variance_attribution"),
            "actor": who.operator_id, "idempotency_key": idempotency_key,
            "authorization": request.headers.get("Authorization"),
            "tenant_id": getattr(who, "tenant_id", None),
            "episodes": body.get("episodes"), "target_env": body.get("target_env"),
            "promotion_stage": body.get("promotion_stage"),
        }
        try:
            status, downstream = dispatch_command(payload)
        except RuntimeError:
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Durable trade journal command owner is unavailable", retryable=True)
        if status not in (200, 202):
            error = downstream.get("error") or (downstream.get("detail", {}).get("error") if isinstance(downstream.get("detail"), Mapping) else downstream.get("detail")) if isinstance(downstream, Mapping) else None
            if isinstance(error, Mapping):
                return _err(status, str(error.get("code", "COMMAND_REJECTED")), str(error.get("message", "Command owner rejected the command")), retryable=bool(error.get("retryable")))
            if isinstance(error, str):
                return _err(status, "COMMAND_REJECTED", error)
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
