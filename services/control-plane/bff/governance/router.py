"""Canonical Governance BFF router.

This factory owns the 35 governance policy, approval, committee, consultation,
review, and audit route decorators assigned by the operation-gap migration
inventory.  ``main.py`` remains the composition root until the later assembly
slice switches to this router.
"""
from __future__ import annotations

import copy
import json
import re
import urllib.error
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..models import safe_redact_evidence_refs
from . import approval_owner
from .approval_owner import InvalidApprovalRequest, RetiredApprovalAction, UnsupportedApprovalAction
from .service import GovernanceService, SubmitAction, page_slice, split_csv, utc_now_rfc3339


PageSlice = Callable[[Sequence[Any], Optional[str], int], Tuple[List[Any], Optional[str]]]


def _default_extract_identity(authorization: Optional[str] = None) -> Any:
    class Identity:
        operator_id = "operator-1"
        roles = {"operator", "viewer", "reviewer", "approver", "admin"}

    return Identity()


def _default_require_role(identity: Any) -> None:
    return None


def _default_bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: Optional[str] = None,
    **details: Any,
) -> HTTPException:
    value = code.value if hasattr(code, "value") else str(code)
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": value,
                "message": message,
                "reason": reason or message,
                **details,
            }
        },
    )


def _default_snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    return {"snapshot_at": snapshot_at}


_default_dataset_surface_status = GovernanceService._default_dataset_surface_status


def _default_read_surface_meta(
    dataset: str,
    surface_key: str,
    *,
    snapshot_at: str,
    total: Optional[int] = None,
    surface: Optional[Dict[str, Any]] = None,
    **_: Any,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            surface_key: surface
            or GovernanceService._default_dataset_surface_status(
                dataset, snapshot_at=snapshot_at, source="missing"
            )
        },
    }
    if total is not None:
        meta["total"] = total
    return meta


def _default_redact_evidence_refs(
    identity: Any, refs: List[Dict[str, Any]], *, capabilities: Any = None
) -> Tuple[List[Dict[str, Any]], int]:
    return GovernanceService._fail_closed_redact_evidence_refs(
        identity, refs, capabilities=capabilities
    )


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def create_governance_router(
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Optional[Callable[[Optional[str]], Any]] = None,
    require_read_role: Optional[Callable[[Any], None]] = None,
    require_operator_role: Optional[Callable[[Any], None]] = None,
    bff_error: Optional[Callable[..., Exception]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    page_slice_fn: Optional[PageSlice] = None,
    snapshot_meta: Optional[Callable[[str], Dict[str, Any]]] = None,
    dataset_surface_status: Optional[Callable[..., Dict[str, Any]]] = None,
    read_surface_meta: Optional[Callable[..., Dict[str, Any]]] = None,
    meta_staleness: Optional[Callable[[], Any]] = None,
    redact_evidence_refs: Optional[Callable[..., Tuple[List[Dict[str, Any]], int]]] = None,
    capabilities_for_identity: Optional[Callable[[Any], Any]] = None,
    publish_event: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    read_surface_state: Optional[Callable[[], str]] = None,
    submit_action: Optional["SubmitAction"] = None,
    governance_service: Optional[GovernanceService] = None,
    reject_body_idempotency_key: Optional[Callable[[Dict[str, Any]], None]] = None,
    command_store: Optional[Any] = None,
) -> APIRouter:
    """Build the exact 35-route Governance domain router."""

    router = APIRouter()
    _get_store = (
        (lambda: read_surface() if callable(read_surface) else read_surface)
        if read_surface is not None
        else (get_read_store or (lambda: getattr(governance_service, "read_store", None)))
    )
    _extract = extract_identity or _default_extract_identity
    _require_read = require_read_role or _default_require_role
    _require_operator = require_operator_role or _default_require_role
    _err = bff_error or _default_bff_error
    _now = utc_now or utc_now_rfc3339
    _page = page_slice_fn or page_slice
    _snapshot = snapshot_meta or _default_snapshot_meta
    _surface = dataset_surface_status or _default_dataset_surface_status
    _read_meta = read_surface_meta or _default_read_surface_meta
    _staleness = meta_staleness or (lambda: None)
    _redact = redact_evidence_refs or _default_redact_evidence_refs
    _reject_body_idempotency_key = reject_body_idempotency_key or (lambda payload: None)
    _capabilities = capabilities_for_identity or (lambda identity: [])
    _read_surface_state = read_surface_state or (lambda: "fresh")

    def _safe_redact(identity: Any, refs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
        return safe_redact_evidence_refs(
            identity, refs, redact_fn=_redact, capabilities_fn=_capabilities
        )

    def _redact_review_queue_items(identity: Any, items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
        total, res = 0, []
        for item in items:
            it = copy.deepcopy(item) if isinstance(item, dict) else item
            if isinstance(it, dict) and it.get("review_summary"):
                refs = list((it["review_summary"] or {}).get("evidence_refs") or [])
                if refs:
                    proc, cnt = _safe_redact(identity, refs)
                    it["review_summary"] = {**it["review_summary"], "evidence_refs": proc}
                    total += cnt
            res.append(it)
        return res, total

    def _redact_evidence_field_items(identity: Any, items: List[Any], *, field: str = "evidence_refs") -> Tuple[List[Any], int]:
        total, res = 0, []
        for item in items:
            it = copy.deepcopy(item) if isinstance(item, dict) else item
            if isinstance(it, dict) and isinstance(it.get(field), list) and it[field]:
                proc, cnt = _safe_redact(identity, it[field])
                it[field] = proc
                total += cnt
            res.append(it)
        return res, total

    def _redact_consultation_metadata_evidence(identity: Any, items: List[Any]) -> Tuple[List[Any], int]:
        total, res = 0, []
        for item in items:
            it = copy.deepcopy(item) if isinstance(item, dict) else item
            if isinstance(it, dict) and isinstance(it.get("metadata"), dict):
                consult = it["metadata"].get("consultation")
                if isinstance(consult, dict) and isinstance(consult.get("evidence_refs"), list) and consult["evidence_refs"]:
                    proc, cnt = _safe_redact(identity, consult["evidence_refs"])
                    it["metadata"] = {**it["metadata"], "consultation": {**consult, "evidence_refs": proc}}
                    total += cnt
            res.append(it)
        return res, total

    resolved_service = governance_service

    def _service() -> GovernanceService:
        nonlocal resolved_service
        current_store = _get_store()
        if resolved_service is None or getattr(resolved_service, "read_store", None) is not current_store:
            resolved_service = GovernanceService(
                current_store,
                utc_now=_now,
                page_slice_fn=_page,
                publish_event=publish_event,
                dataset_surface_status=_surface,
                redact_evidence_refs=_redact,
                capabilities_for_identity=_capabilities,
                read_surface_state=_read_surface_state,
                submit_action=submit_action,
                command_store=command_store,
            )
        return resolved_service

    def _fail(
        status_code: int,
        code: str,
        message: str,
        reason: str,
        *,
        precondition_failed: Optional[str] = None,
    ) -> None:
        details: Dict[str, Any] = {}
        if precondition_failed:
            details["precondition_failed"] = precondition_failed
        raise _err(status_code, code, message, reason, **details)

    def _identity(authorization: Optional[str], *, operator: bool = False) -> Any:
        identity = _extract(authorization)
        (_require_operator if operator else _require_read)(identity)
        return identity

    async def _forward(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return await run_in_threadpool(call, *args, **kwargs)
        except urllib.error.HTTPError as exc:
            try:
                content = json.loads(exc.read().decode("utf-8"))
            except Exception:
                content = {"detail": f"Governance owner returned HTTP {exc.code}"}
            raise HTTPException(status_code=exc.code, detail=content.get("detail", content)) from exc
        except RetiredApprovalAction:
            _fail(410, "VALIDATION_FAILED", "RequestApprovalRevision is retired", "Use RejectDecision with notes", precondition_failed="retired_action")
        except UnsupportedApprovalAction as exc:
            _fail(501, "NOT_IMPLEMENTED", str(exc), "Unsupported approval action", precondition_failed="unsupported_action")
        except InvalidApprovalRequest as exc:
            _fail(422, "VALIDATION_FAILED", f"{exc} is required or invalid", f"Invalid approval decision field: {exc}", precondition_failed=str(exc))
        except (urllib.error.URLError, OSError, RuntimeError):
            _fail(503, "DEPENDENCY_UNAVAILABLE", "Governance approval owner unavailable", "Governance approval owner unreachable")

    def _idempotency_key(primary: Optional[str], alternate: Optional[str], *, required: bool = False) -> str:
        first, second = str(primary or "").strip(), str(alternate or "").strip()
        if first and second and first != second:
            _fail(422, "VALIDATION_FAILED", "Conflicting idempotency headers", "Idempotency-Key and X-Idempotency-Key must match when both are provided", precondition_failed="idempotency_key")
        if required and not (first or second):
            _fail(422, "VALIDATION_FAILED", "Idempotency-Key is required", "Approval writes need a stable Idempotency-Key", precondition_failed="idempotency_key")
        return first or second or str(uuid.uuid4())

    def _publish_decision(aid: str, res: Dict[str, Any], ident: Any) -> None:
        if publish_event is not None:
            publish_event("approval.decided" if res.get("decision_state") == "decided" else "approval.stage.changed",
                          {"approval_id": aid, "decision_state": res.get("decision_state"), "version": res.get("version"), "actor_id": getattr(ident, "operator_id", None)})

    def _not_found(label: str, resource_id: str) -> None:
        _fail(404, "RESOURCE_NOT_FOUND", f"{label} not found", f"{label} {resource_id} does not exist")

    def _paged(
        items: List[Dict[str, Any]], *, page_token: Optional[str], page_size: int, surface_key: str, dataset: str,
    ) -> Dict[str, Any]:
        snapshot_at = _now()
        surface = _surface(dataset, snapshot_at=snapshot_at, source=_service().dataset_source(dataset))
        page_items, next_token = ([], None) if surface.get("status") == "unavailable" else _page(items, page_token, page_size)
        meta = _snapshot(snapshot_at)
        surfaces = {surface_key: surface}
        if surface_key in {"governance_review_queue", "governance_approval_queue"}:
            surfaces["review_queue" if surface_key == "governance_review_queue" else "approval_queue"] = surface
            surfaces["allowedActions"] = {"status": surface.get("status", "ok"), "available": surface.get("status") != "unavailable", "snapshot_at": snapshot_at}
        meta["surfaces"] = surfaces
        staleness = _staleness()
        if staleness is not None:
            meta["staleness"] = staleness
        return {"items": page_items, "page_info": {"next_page_token": next_token, "total": len(items), "page_size": page_size}, "meta": meta}

    # 1-3. Approval decisions ------------------------------------------

    @router.get("/api/v1/approval-decisions")
    async def list_approval_decisions(
        outcome: Optional[str] = None,
        state: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        decisions = await _forward(approval_owner.list_decisions, authorization, state=state, outcome=outcome)
        redacted_decisions, total_redacted = _redact_evidence_field_items(identity, decisions)
        meta = _read_meta(
            "approval_decisions",
            "approval_decision_list",
            snapshot_at=_now(),
            total=len(redacted_decisions),
        )
        meta["redacted_evidence_count"] = total_redacted
        return {"data": redacted_decisions, "meta": meta}

    @router.post("/api/v1/approval-decisions", status_code=201)
    async def create_approval_decision(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ) -> Any:
        _extract(authorization)
        if str(x_dry_run or "").strip().lower() in {"1", "true", "yes"}:
            _fail(501, "NOT_IMPLEMENTED", "Dry-run is not supported by the Governance owner", "Unsupported approval action", precondition_failed="unsupported_action")
        key = _idempotency_key(idempotency_key, x_idempotency_key, required=True)
        return await _forward(approval_owner.propose, authorization, payload, key)

    @router.get("/api/v1/approval-decisions/{decision_id}")
    async def get_approval_decision_detail(
        decision_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        decision = await _forward(approval_owner.get_decision, authorization, decision_id)
        redacted, total_redacted = _redact_evidence_field_items(identity, [decision])
        meta = _read_meta("approval_decisions", "approval_decision_detail", snapshot_at=_now())
        meta["redacted_evidence_count"] = total_redacted
        return {"data": redacted[0], "meta": meta}

    # 4-12. Consultation workbench, requests, committees, and memos ----

    @router.get("/api/v1/workbench/consultation")
    async def get_consultation_workbench_overview(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        return _service().consultation_workbench()

    @router.post("/api/v1/consult/requests")
    async def create_consult_request(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization, operator=True)
        try:
            request = _service().create_consult_request(payload, identity)
        except ValueError as exc:
            field = str(exc)
            _fail(422, "VALIDATION_FAILED", f"{field} is invalid", f"Invalid or missing {field}", precondition_failed=field)
        except RuntimeError:
            _fail(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "Consult request store unavailable",
                "Create operation could not be persisted.",
            )
        return {
            key: request.get(key)
            for key in (
                "request_id",
                "status",
                "created_at",
                "linked_session_id",
                "request_to_session_status",
                "allowedActions",
            )
        }

    @router.get("/api/v1/consult/requests")
    async def list_consult_requests(
        status: Optional[str] = None,
        target_type: Optional[str] = None,
        consultation_type: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        snapshot_at = _now()
        items = _service().list_consult_requests(
            status=status,
            target_type=target_type,
            consultation_type=consultation_type,
        )
        surface = _surface(
            "consult_requests",
            snapshot_at=snapshot_at,
            source=_service().dataset_source("consult_requests"),
        )
        if surface.get("status") == "unavailable":
            page_items: List[Dict[str, Any]] = []
            next_token = None
            total = 0
        else:
            page_items, next_token = _page(items, page_token, page_size)
            total = len(items)
        meta = _snapshot(snapshot_at)
        meta["surfaces"] = {"consult_request_list": surface}
        return {
            "data": page_items,
            "page_info": {"next_page_token": next_token, "total": total, "page_size": page_size},
            "meta": meta,
        }

    @router.get("/api/v1/consult/requests/{request_id}")
    async def get_consult_request(
        request_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        snapshot_at = _now()
        record = _service().get_consult_request(request_id)
        surface = _surface(
            "consult_requests",
            snapshot_at=snapshot_at,
            source=_service().dataset_source("consult_requests"),
        )
        if record is None:
            if surface.get("status") == "unavailable":
                _fail(
                    503,
                    "DEPENDENCY_UNAVAILABLE",
                    "Consult request unavailable",
                    "Consult request read surface is unavailable",
                )
            _not_found("Consult request", request_id)
        record = dict(record)
        raw_context_refs = list(record.get("context_refs") or [])
        total_redacted = 0
        if raw_context_refs:
            redacted_refs, total_redacted = _safe_redact(identity, raw_context_refs)
            record["context_refs"] = redacted_refs
        meta = _read_meta(
            "consult_requests",
            "consult_request_detail",
            snapshot_at=snapshot_at,
            surface=surface,
        )
        meta["redacted_evidence_count"] = total_redacted
        return {
            **record,
            "links": {
                "self": f"/api/v1/consult/requests/{request_id}",
                "workbench_detail": f"/consultation/requests/{request_id}",
            },
            "meta": meta,
        }

    @router.post("/api/v1/consult/requests/{request_id}/cancel")
    async def cancel_consult_request(
        request_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization, operator=True)
        record = _service().get_consult_request(request_id)
        if record is None:
            _not_found("Consult request", request_id)
        if not (record.get("allowedActions") or {}).get("canCancel"):
            _fail(
                409,
                "PRECONDITION_FAILED",
                "Consult request cannot be canceled",
                f"allowedActions.canCancel is false for request {request_id}",
                precondition_failed="allowedActions.canCancel",
            )
        canceled = _service().cancel_consult_request(request_id, identity)
        if canceled is None:
            refreshed = _service().get_consult_request(request_id)
            if refreshed and not (refreshed.get("allowedActions") or {}).get("canCancel"):
                _fail(
                    409,
                    "PRECONDITION_FAILED",
                    "Consult request cannot be canceled",
                    f"allowedActions.canCancel is false for request {request_id}",
                    precondition_failed="allowedActions.canCancel",
                )
            _fail(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "Consult request store unavailable",
                "Cancel operation could not be persisted.",
            )
        return {
            key: canceled.get(key)
            for key in (
                "request_id",
                "status",
                "canceled_at",
                "linked_session_id",
                "request_to_session_status",
                "allowedActions",
            )
        }

    @router.get("/api/v1/committees")
    async def list_committees(
        quorum_state: Optional[str] = None,
        consensus_state: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        snapshot_at = _now()
        service = _service()
        surface_state = service.committee_collection_surface_state(snapshot_at=snapshot_at)
        items, next_token, total = service.list_committees(
            quorum_state=quorum_state,
            consensus_state=consensus_state,
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )
        meta = _snapshot(snapshot_at)
        meta["surfaces"] = {"committee_board": surface_state}
        return {
            "data": items,
            "page_info": {"next_page_token": next_token, "total": total, "page_size": page_size},
            "meta": meta,
        }

    @router.get("/api/v1/committees/{committee_id}")
    async def get_committee(
        committee_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        projection = _service().committee_projection(committee_id, identity=identity, snapshot_at=_now())
        if projection is None:
            _not_found("Committee", committee_id)
        return projection

    @router.get("/api/v1/consult/memos")
    async def list_consult_memos(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=25, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        snapshot_at = _now()
        try:
            items, next_token, total, surface_state = _service().list_consult_memos(
                status=status,
                page_token=page_token,
                page_size=page_size,
                snapshot_at=snapshot_at,
                identity=identity,
            )
        except ValueError as exc:
            field = str(exc)
            _fail(422, "VALIDATION_FAILED", f"{field} is invalid", f"Invalid {field} filter", precondition_failed=field)
        return {
            "items": items,
            "page_info": {"next_page_token": next_token, "page_size": page_size, "total": total},
            "meta": {
                "snapshot_at": snapshot_at,
                "staleness": {"status": "fresh" if surface_state == "ok" else "stale", "as_of": snapshot_at},
                "surfaces": {"redteam_memo": {"state": surface_state}},
            },
        }

    @router.get("/api/v1/consult/memos/{memo_id}")
    async def get_consult_memo(
        memo_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        projection = _service().consult_memo_projection(memo_id, identity=identity, snapshot_at=_now())
        if projection is None:
            _not_found("Consult memo", memo_id)
        return projection

    # 13-16. Operator governance queues, audit, and mutation review -----

    @router.get("/api/v1/operator/governance/review-queue")
    async def list_governance_review_queue(
        item_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        items = _service().list_review_queue(
            item_types=split_csv(item_type),
            risk_levels=split_csv(risk_level),
            statuses=split_csv(status),
        )
        response = _paged(
            items,
            page_token=page_token,
            page_size=page_size,
            surface_key="governance_review_queue",
            dataset="governance_review_queue_items",
        )
        redacted_page, total_redacted = _redact_review_queue_items(identity, response["items"])
        response["items"] = redacted_page
        response["meta"]["redacted_evidence_count"] = total_redacted
        return response

    @router.get("/api/v1/operator/governance/approval-queue")
    async def list_governance_approval_queue(
        decision_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        decision_state: Optional[str] = None,
        state: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        resolved_state = decision_state if decision_state is not None else state
        items = _service().list_approval_queue(
            decision_types=split_csv(decision_type),
            risk_levels=split_csv(risk_level),
            decision_states=split_csv(resolved_state),
        )
        response = _paged(
            items,
            page_token=page_token,
            page_size=page_size,
            surface_key="governance_approval_queue",
            dataset="approval_queue_items",
        )
        redacted_page, total_redacted = _redact_evidence_field_items(identity, response["items"])
        response["items"] = redacted_page
        response["meta"]["redacted_evidence_count"] = total_redacted
        return response

    @router.get("/api/v1/operator/governance/audit")
    async def list_governance_audit_trail(
        actor: Optional[str] = None,
        action_type: Optional[str] = None,
        target_type: Optional[str] = None,
        from_ts: Optional[str] = Query(default=None, alias="from"),
        to_ts: Optional[str] = Query(default=None, alias="to"),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        items = _service().list_audit_events(
            actor=actor,
            action_types=split_csv(action_type),
            target_type=target_type,
            from_ts=_parse_datetime(from_ts),
            to_ts=_parse_datetime(to_ts),
        )
        response = _paged(
            items,
            page_token=page_token,
            page_size=page_size,
            surface_key="governance_audit",
            dataset="governance_audit_events",
        )
        redacted_page, total_redacted = _redact_evidence_field_items(identity, response["items"])
        response["items"] = redacted_page
        response["meta"]["redacted_evidence_count"] = total_redacted
        return response

    @router.get("/api/v1/operator/mutation-review/{decision_id}")
    async def get_mutation_review(
        decision_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Any:
        identity = _identity(authorization)
        projection = _service().mutation_review_projection(
            decision_id, identity=identity, snapshot_at=_now()
        )
        if projection is None:
            _not_found("Mutation review decision", decision_id)
        if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "DEPENDENCY_UNAVAILABLE",
                        "message": "Mutation review evidence is unavailable",
                        "reason": "Mutation-review evidence cannot be composed reliably",
                    },
                    "surfaces": {"mutation_review": "unavailable"},
                },
            )
        return projection

    @router.get("/api/v1/operator/rollback-review/{rollback_id}")
    async def get_rollback_review(
        rollback_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)

        review = _get_store().get_rollback_review(rollback_id)
        if not review:
            _fail(404, "RESOURCE_NOT_FOUND", "Rollback review not found", f"Rollback review {rollback_id} does not exist")

        snapshot_at = (
            ((review.get("meta") or {}).get("snapshot_at"))
            or utc_now_rfc3339()
        )
        meta = dict(review.get("meta") or {})
        meta["snapshot_at"] = snapshot_at
        surfaces = dict(meta.get("surfaces") or {})
        surfaces.setdefault(
            "rollback_review",
            {"status": "ok", "snapshot_at": snapshot_at, "available": True},
        )
        surfaces.setdefault(
            "position_data",
            {"status": "ok", "snapshot_at": snapshot_at, "available": True},
        )
        surfaces.setdefault(
            "allowedActions",
            {
                "status": "ok" if review.get("allowedActions") is not None else "degraded",
                "snapshot_at": snapshot_at,
                "available": review.get("allowedActions") is not None,
                "missing_message": None if review.get("allowedActions") is not None else "Rollback approval authority unavailable.",
            },
        )
        meta["surfaces"] = surfaces

        payload = dict(review)
        payload["meta"] = meta
        return payload

    # 17-23. Consultation session read surfaces ------------------------

    @router.get("/api/v1/personas/{persona_id}/consultations")
    def list_consultations(
        persona_id: str,
        consultation_type: Optional[str] = Query(default=None, alias="filter.consultation_type"),
        status: Optional[str] = Query(default=None, alias="filter.status"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        if _service().get_persona(persona_id) is None:
            _not_found("Persona", persona_id)
        consultations = _service().list_consultations_for_persona(
            persona_id,
            consultation_type=consultation_type,
            status=status,
            page=page,
            page_size=page_size,
        )
        if consultations is None:
            return {"data": [], "meta": {"total": 0, "page": page, "page_size": page_size, "staleness": {"served_from": "unavailable", "last_known_at": _now()}}}
        start = (page - 1) * page_size
        page_data = consultations[start : start + page_size]
        redacted_page, total_redacted = _redact_consultation_metadata_evidence(identity, page_data)
        return {
            "data": [
                {
                    **session,
                    "_links": {
                        "self": f"/api/v1/consultations/{session['session_id']}",
                        "participants": f"/api/v1/consultations/{session['session_id']}/participants",
                        "outcome": f"/api/v1/consultations/{session['session_id']}/outcome",
                    },
                }
                for session in redacted_page
            ],
            "meta": {
                "total": len(consultations),
                "page": page,
                "page_size": page_size,
                "staleness": _staleness(),
                "supporting_counts": {"redacted_evidence_count": total_redacted},
            },
        }

    @router.get("/api/v1/consultations/{session_id}")
    def get_consultation(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        session = _service().get_consultation(session_id)
        if session is None:
            _not_found("Consultation session", session_id)
        redacted, total_redacted = _redact_consultation_metadata_evidence(identity, [session])
        return {
            "data": {
                **redacted[0],
                "_links": {
                    "self": f"/api/v1/consultations/{session_id}",
                    "participants": f"/api/v1/consultations/{session_id}/participants",
                    "outcome": f"/api/v1/consultations/{session_id}/outcome",
                    "evidence": f"/api/v1/consultations/{session_id}/evidence",
                },
            },
            "meta": {"staleness": _staleness(), "supporting_counts": {"redacted_evidence_count": total_redacted}},
        }

    @router.get("/api/v1/consultations/{session_id}/participants")
    def get_consultation_participants(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        participants = _service().get_consultation_participants(session_id)
        if participants is None:
            _not_found("Consultation session", session_id)
        redacted_participants, total_redacted = _redact_consultation_metadata_evidence(identity, participants)
        return {
            "data": [
                {
                    **participant,
                    "_links": {
                        "self": f"/api/v1/sessions/{participant['session_id']}",
                        "persona": f"/api/v1/personas/{participant['persona_id']}",
                    },
                }
                for participant in redacted_participants
            ],
            "meta": {
                "total": len(participants),
                "staleness": _staleness(),
                "supporting_counts": {"redacted_evidence_count": total_redacted},
            },
        }

    @router.get("/api/v1/consultations/{session_id}/outcome")
    def get_consultation_outcome(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        outcome = _service().get_consultation_outcome(session_id)
        if outcome is None:
            _not_found("Consultation session", session_id)
        redacted, total_redacted = _redact_consultation_metadata_evidence(identity, [outcome])
        return {
            "data": redacted[0],
            "meta": {"staleness": _staleness(), "supporting_counts": {"redacted_evidence_count": total_redacted}},
        }

    @router.get("/api/v1/consultations/{session_id}/evidence")
    def get_consultation_evidence(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        evidence = _service().get_consultation_evidence(session_id)
        if evidence is None:
            _not_found("Consultation session", session_id)
        processed, redacted_count = _safe_redact(identity, list(evidence))
        return {"data": processed, "meta": {"total": len(processed), "staleness": _staleness(), "supporting_counts": {"redacted_evidence_count": redacted_count}}}

    @router.get("/api/v1/consultations/{session_id}/transcript")
    def get_consultation_transcript(
        session_id: str,
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        from_sequence_no: Optional[int] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        transcript = _service().get_consult_transcript(
            session_id,
            from_sequence_no=from_sequence_no,
            page_size=page_size,
            page_token=page_token,
        )
        if transcript is None:
            _not_found("Consultation session", session_id)
        events = transcript.get("events") if isinstance(transcript, dict) else None
        if isinstance(events, list):
            transcript = dict(transcript)
            redacted_events, total_redacted = _redact_evidence_field_items(identity, events)
            transcript["events"] = redacted_events
            meta = dict(transcript.get("meta") or {})
            meta["redacted_evidence_count"] = total_redacted
            transcript["meta"] = meta
        return transcript

    @router.get("/api/v1/personas/{persona_id}/consult-policy")
    def get_consult_policy(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        if _service().get_persona(persona_id) is None:
            _not_found("Persona", persona_id)
        policy = _service().get_consult_policy(persona_id)
        if policy is None:
            return {
                "data": {
                    "id": None,
                    "persona_id": persona_id,
                    "required_reviewers": 0,
                    "required_committees": [],
                    "trigger_rules": [],
                    "forbidden_solo_actions": [],
                    "escalation_rules": [],
                },
                "meta": {"staleness": _staleness(), "note": "No consult policy found for this persona. Defaulting to empty policy."},
            }
        return {"data": policy, "meta": {"staleness": _staleness()}}

    # 24-25. Approval resync and management ledger ---------------------

    @router.get("/bff/approvals")
    async def list_bff_approvals(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        items = await _forward(approval_owner.list_decisions, authorization, pending_only=True)
        redacted_items, total_redacted = _redact_evidence_field_items(identity, items)
        return {
            "items": redacted_items,
            "count": len(redacted_items),
            "generated_at": _now(),
            "meta": {"redacted_evidence_count": total_redacted},
        }

    @router.get("/bff/management/governance-ledger")
    async def bff_management_governance_ledger(
        source_type: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        q: str = Query(default=""),
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        response = _service().governance_ledger(
            source_type=source_type,
            status=status,
            q=q,
            page_token=page_token,
            page_size=page_size,
        )
        data = dict(response.get("data") or {})
        items = data.get("items") if isinstance(data.get("items"), list) else []
        redacted_items, total_redacted = _redact_evidence_field_items(identity, items)
        data["items"] = redacted_items
        response["data"] = data
        meta = dict(response.get("meta") or {})
        meta["redacted_evidence_count"] = total_redacted
        response["meta"] = meta
        return response

    # 26-32. Review compatibility surfaces -----------------------------

    @router.get("/bff/reviews")
    async def bff_list_reviews(
        item_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        items = _service().list_review_queue(
            item_types=split_csv(item_type),
            risk_levels=split_csv(risk_level),
            statuses=split_csv(status),
        )
        response = _paged(items, page_token=page_token, page_size=page_size, surface_key="review_queue", dataset="governance_review_queue_items")
        redacted_page, total_redacted = _redact_review_queue_items(identity, response["items"])
        response["items"] = redacted_page
        response["meta"]["redacted_evidence_count"] = total_redacted
        return response

    @router.post("/bff/reviews", status_code=202)
    async def bff_create_review(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = _identity(authorization, operator=True)
        review_id = str(payload.get("review_id") or payload.get("id") or uuid.uuid4())
        try:
            return await _service().submit_governance_action(
                action_kind="review",
                target_id=review_id,
                action_id="submit",
                payload=payload,
                identity=identity,
                idempotency_key=_idempotency_key(idempotency_key, x_idempotency_key),
            )
        except RuntimeError:
            _fail(409, "IDEMPOTENCY_CONFLICT", "Idempotency key conflict", "The key is bound to another payload")

    @router.get("/bff/reviews/{review_id}")
    async def bff_get_review(
        review_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        review = _service().get_review(review_id.strip())
        if review is None:
            _not_found("Review item", review_id)
        redacted_reviews, total_redacted = _redact_review_queue_items(identity, [review])
        review = redacted_reviews[0]
        return {
            "data": review,
            "meta": {
                "snapshot_at": _now(),
                "correlation_id": review_id,
                "staleness": _staleness(),
                "redacted_evidence_count": total_redacted,
            },
        }

    @router.post("/bff/reviews/{review_id}/actions/{action_id}", status_code=202)
    async def bff_review_action(
        review_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = _identity(authorization, operator=True)
        clean_action = re.sub(r"[^a-z0-9]", "", str(action_id or "").strip().lower())
        candidates = [clean_action] + [re.sub(r"[^a-z0-9]", "", str(payload.get(k) or "").strip().lower()) for k in ("decision", "action", "verb", "action_id", "actionId", "outcome") if payload.get(k)]
        if any(v in {"requestrevision", "requestapprovalrevision", "requestchanges", "requestchange"} for v in candidates) or payload.get("revision_notes") or payload.get("revisionNotes"):
            _fail(410, "VALIDATION_FAILED", "RequestApprovalRevision is retired", "Use RejectDecision with notes", precondition_failed="retired_action")
        if any(payload.get(k) not in (None, "") for k in ("stage_name", "stageName", "stage_id", "stageId", "stage")):
            _fail(501, "NOT_IMPLEMENTED", "named stage approvals are unsupported", "Unsupported approval action", precondition_failed="unsupported_action")
        unsupported = [v for v in candidates if v not in {"approve", "approved", "reject", "rejected", "approvedwithconditions", "approvewithconditions"}]
        if unsupported:
            _fail(501, "NOT_IMPLEMENTED", f"approval action {unsupported[0]!r} has no Governance owner transition", "Unsupported approval action", precondition_failed="unsupported_action")
        app_c = [v for v in candidates if v in {"approve", "approved", "approvedwithconditions", "approvewithconditions"}]
        rej_c = [v for v in candidates if v in {"reject", "rejected"}]
        if app_c and rej_c:
            _fail(422, "VALIDATION_FAILED", "Conflicting action and decision", "URL action and body decision conflict", precondition_failed="conflicting_decision")
        vote_verb = "approved_with_conditions" if any("condition" in v for v in app_c) else ("approve" if app_c else ("reject" if rej_c else None))
        clean_id = review_id.strip()
        params = dict(payload)
        params["decision"] = vote_verb
        key = _idempotency_key(idempotency_key, x_idempotency_key, required=False)
        result = await _forward(approval_owner.decide, authorization, clean_id, params, key)
        _publish_decision(clean_id, result, identity)
        return JSONResponse(status_code=202, content=result)

    @router.get("/bff/reviews/{review_id}/validators")
    async def bff_review_validators(
        review_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        review = _service().get_review(review_id.strip())
        summary = review.get("review_summary") if isinstance(review, dict) else {}
        validators = summary.get("validators") if isinstance(summary, dict) else []
        return {"review_id": review_id.strip(), "validators": validators or [], "meta": {"snapshot_at": _now(), "staleness": _staleness()}}

    @router.get("/bff/reviews/{review_id}/audit")
    async def bff_review_audit(
        review_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        clean_id = review_id.strip()
        events = [
            event
            for event in _service().list_audit_events()
            if str(event.get("target_id") or event.get("item_id") or "") == clean_id
            and str(event.get("target_type") or "") in {"Review", "GovernanceReviewItem"}
        ]
        redacted_events, total_redacted = _redact_evidence_field_items(identity, events)
        return {
            "review_id": clean_id,
            "events": redacted_events,
            "meta": {
                "snapshot_at": _now(),
                "correlation_id": clean_id,
                "staleness": _staleness(),
                "redacted_evidence_count": total_redacted,
            },
        }

    @router.get("/bff/approvals/{approval_id}/evidence")
    async def bff_approval_evidence(
        approval_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        clean_id = approval_id.strip()
        decision = await _forward(approval_owner.get_decision, authorization, clean_id)
        processed, redacted_count = _safe_redact(identity, list(decision.get("evidence_refs") or []))
        return {
            "approval_id": clean_id,
            "evidence": processed,
            "correlation_id": decision.get("decision_id") or clean_id,
            "audit_ref": {"target_type": "ApprovalDecision", "target_id": clean_id, "href": f"/bff/audit/entities/ApprovalDecision/{clean_id}"},
            "meta": {"snapshot_at": _now(), "redacted_count": redacted_count, "staleness": _staleness()},
        }

    # 33. Explicit typed approval detail; replaces generic alias -------

    @router.get("/bff/approvals/{approval_id}")
    async def get_approval_detail(
        approval_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        detail = await _forward(approval_owner.get_decision, authorization, approval_id.strip())
        redacted, total_redacted = _redact_evidence_field_items(identity, [detail])
        meta = _snapshot(_now())
        meta["redacted_evidence_count"] = total_redacted
        return {"data": redacted[0], "meta": meta}

    # 34-35. Single and batch approval decisions -----------------------

    @router.post("/bff/approvals/{approval_id}/decide", status_code=202)
    async def bff_approvals_decide(
        approval_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = _extract(authorization)
        clean_id = approval_id.strip()
        result = await _forward(approval_owner.decide, authorization, clean_id, payload,
                                _idempotency_key(idempotency_key, x_idempotency_key, required=True))
        _publish_decision(clean_id, result, identity)
        return JSONResponse(status_code=202, content=result)

    @router.post("/bff/approvals/batch-decide", status_code=202)
    async def bff_approvals_batch_decide(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> JSONResponse:
        _extract(authorization)
        _reject_body_idempotency_key(payload)
        decisions = payload.get("decisions") if isinstance(payload.get("decisions"), list) else None
        if not decisions:
            _fail(422, "VALIDATION_FAILED", "decisions must be a non-empty list", "The decisions field must contain at least one item", precondition_failed="decisions")
        if len(decisions) > 50:
            _fail(422, "VALIDATION_FAILED", "batch-decide accepts at most 50 items", f"Received {len(decisions)} items", precondition_failed="decisions")
        batch_key = _idempotency_key(idempotency_key, x_idempotency_key, required=True)
        results: List[Dict[str, Any]] = []
        for index, item in enumerate(decisions):
            if not isinstance(item, dict) or not str(item.get("id") or "").strip():
                results.append({"index": index, "id": None, "status": "failed", "error": {"code": "VALIDATION_FAILED", "message": "id is required for each decision item"}})
                continue
            item_id = str(item["id"]).strip()
            try:
                owner = await _forward(approval_owner.decide, authorization, item_id, item, f"{batch_key}::{index}::{item_id}")
                results.append({"index": index, "id": item_id, "status": "accepted", "result": owner})
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                results.append({"index": index, "id": item_id, "status": "failed", "http_status": exc.status_code, "error": detail.get("error", detail)})
        accepted = sum(item["status"] == "accepted" for item in results)
        failed = len(results) - accepted
        status = "accepted" if not failed else "partial" if accepted else "failed"
        return JSONResponse(
            status_code=202 if not failed else 207,
            content={
                "status": status,
                "results": results,
                "summary": {"total": len(results), "accepted": accepted, "failed": failed},
                "meta": {"snapshot_at": _now(), "batch_idempotency_key": batch_key},
            },
        )

    return router
