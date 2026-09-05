"""Canonical Governance BFF router.

This factory owns the 35 governance policy, approval, committee, consultation,
review, and audit route decorators assigned by the operation-gap migration
inventory.  ``main.py`` remains the composition root until the later assembly
slice switches to this router.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from .service import GovernanceService, page_slice, split_csv, utc_now_rfc3339


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


def _default_dataset_surface_status(
    dataset: str, *, snapshot_at: str, source: Optional[str] = None, **_: Any
) -> Dict[str, Any]:
    source = source or "ok"
    if source in {"missing", "unavailable"}:
        status = "unavailable"
    elif source in {"local_snapshot", "degraded"}:
        status = "degraded"
    else:
        status = "ok"
    return {"status": status, "source": source, "dataset": dataset, "snapshot_at": snapshot_at}


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
            or {"status": "ok", "source": "ok", "dataset": dataset, "snapshot_at": snapshot_at}
        },
    }
    if total is not None:
        meta["total"] = total
    return meta


def _default_redact_evidence_refs(
    identity: Any, refs: List[Dict[str, Any]], *, capabilities: Any = None
) -> Tuple[List[Dict[str, Any]], int]:
    return list(refs), 0


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
    submit_action: Optional[Callable[..., Any]] = None,
    publish_event: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    get_interventions: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    governance_service: Optional[GovernanceService] = None,
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
    _capabilities = capabilities_for_identity or (lambda identity: None)

    resolved_service = governance_service

    def _service() -> GovernanceService:
        nonlocal resolved_service
        current_store = _get_store()
        if resolved_service is None or getattr(resolved_service, "read_store", None) is not current_store:
            resolved_service = GovernanceService(
                current_store,
                utc_now=_now,
                page_slice_fn=_page,
                submit_action=submit_action,
                publish_event=publish_event,
                get_interventions=get_interventions,
                dataset_surface_status=_surface,
                redact_evidence_refs=_redact,
                capabilities_for_identity=_capabilities,
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

    def _require_approver(identity: Any) -> None:
        roles = set(getattr(identity, "roles", set()) or set())
        if not {"approver", "admin"}.intersection(roles):
            _fail(
                403,
                "FORBIDDEN",
                "Approval decision requires 'approver' or 'admin' role",
                "Operator does not hold the required role",
                precondition_failed="role_check",
            )

    def _idempotency_key(primary: Optional[str], alternate: Optional[str]) -> str:
        first = str(primary or "").strip()
        second = str(alternate or "").strip()
        if first and second and first != second:
            _fail(
                422,
                "VALIDATION_FAILED",
                "Conflicting idempotency headers",
                "Idempotency-Key and X-Idempotency-Key must match when both are provided",
                precondition_failed="idempotency_key",
            )
        return first or second or str(uuid.uuid4())

    def _not_found(label: str, resource_id: str) -> None:
        _fail(
            404,
            "RESOURCE_NOT_FOUND",
            f"{label} not found",
            f"{label} {resource_id} does not exist",
        )

    def _paged(
        items: List[Dict[str, Any]],
        *,
        page_token: Optional[str],
        page_size: int,
        surface_key: str,
        dataset: str,
    ) -> Dict[str, Any]:
        snapshot_at = _now()
        surface = _surface(dataset, snapshot_at=snapshot_at, source=_service().dataset_source(dataset))
        if surface.get("status") == "unavailable":
            page_items, next_token = [], None
        else:
            page_items, next_token = _page(items, page_token, page_size)
        meta = _snapshot(snapshot_at)
        surfaces = {surface_key: surface}
        if surface_key == "governance_review_queue":
            surfaces["review_queue"] = surface
            surfaces["allowedActions"] = {
                "status": surface.get("status", "ok"),
                "available": surface.get("status") != "unavailable",
                "snapshot_at": snapshot_at,
            }
        elif surface_key == "governance_approval_queue":
            surfaces["approval_queue"] = surface
        meta["surfaces"] = surfaces
        staleness = _staleness()
        if staleness is not None:
            meta["staleness"] = staleness
        return {
            "items": page_items,
            "page_info": {"next_page_token": next_token, "total": len(items), "page_size": page_size},
            "meta": meta,
        }

    # 1-3. Approval decisions ------------------------------------------

    @router.get("/api/v1/approval-decisions")
    async def list_approval_decisions(
        outcome: Optional[str] = None,
        state: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        decisions = _service().list_approval_decisions(outcome=outcome, state=state)
        snapshot_at = _now()
        return {
            "data": decisions,
            "meta": _read_meta(
                "approval_decisions",
                "approval_decision_list",
                snapshot_at=snapshot_at,
                total=len(decisions),
            ),
        }

    @router.post("/api/v1/approval-decisions", status_code=202)
    async def create_approval_decision(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
    ) -> Any:
        identity = _identity(authorization, operator=True)
        _require_approver(identity)
        key = _idempotency_key(idempotency_key, x_idempotency_key)
        dry_run = str(x_dry_run or "").strip().lower() in {"1", "true", "yes"}
        correlation_id = str(x_correlation_id or "").strip() or str(uuid.uuid4())
        try:
            result = _service().create_approval_decision(
                payload,
                identity=identity,
                idempotency_key=key,
                dry_run=dry_run,
                correlation_id=correlation_id,
            )
        except ValueError as exc:
            field = str(exc)
            _fail(422, "VALIDATION_FAILED", f"{field} is invalid", f"Invalid or missing {field}", precondition_failed=field)
        except RuntimeError:
            _fail(409, "IDEMPOTENCY_CONFLICT", "Idempotency key conflict", "The key is bound to another payload", precondition_failed="idempotency_conflict")
        return JSONResponse(status_code=200 if dry_run else 202, content=result)

    @router.get("/api/v1/approval-decisions/{decision_id}")
    async def get_approval_decision_detail(
        decision_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        snapshot_at = _now()
        decision = _service().get_approval_detail(decision_id)
        surface = _surface(
            "approval_decisions",
            snapshot_at=snapshot_at,
            source=_service().dataset_source("approval_decisions"),
        )
        if decision is None:
            if surface.get("status") == "unavailable":
                _fail(503, "DEPENDENCY_UNAVAILABLE", "Approval decision unavailable", "Approval decision read surface is unavailable")
            _not_found("Approval decision", decision_id)
        return {
            "data": decision,
            "meta": _read_meta(
                "approval_decisions",
                "approval_decision_detail",
                snapshot_at=snapshot_at,
                surface=surface,
            ),
        }

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
        _identity(authorization)
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
        return {
            **record,
            "links": {
                "self": f"/api/v1/consult/requests/{request_id}",
                "workbench_detail": f"/consultation/requests/{request_id}",
            },
            "meta": _read_meta(
                "consult_requests",
                "consult_request_detail",
                snapshot_at=snapshot_at,
                surface=surface,
            ),
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
        items, next_token, total = _service().list_committees(
            quorum_state=quorum_state,
            consensus_state=consensus_state,
            page_token=page_token,
            page_size=page_size,
        )
        surface = _surface(
            "consultation_sessions",
            snapshot_at=snapshot_at,
            source=_service().dataset_source("consult_requests"),
        )
        meta = _snapshot(snapshot_at)
        meta["surfaces"] = {"committee_board": surface.get("status", "ok")}
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
        _identity(authorization)
        snapshot_at = _now()
        try:
            items, next_token, total, surface_state = _service().list_consult_memos(
                status=status, page_token=page_token, page_size=page_size, snapshot_at=snapshot_at
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
        _identity(authorization)
        items = _service().list_review_queue(
            item_types=split_csv(item_type),
            risk_levels=split_csv(risk_level),
            statuses=split_csv(status),
        )
        return _paged(
            items,
            page_token=page_token,
            page_size=page_size,
            surface_key="governance_review_queue",
            dataset="governance_review_queue_items",
        )

    @router.get("/api/v1/operator/governance/approval-queue")
    async def list_governance_approval_queue(
        decision_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        state: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        items = _service().list_approval_queue(
            decision_types=split_csv(decision_type),
            risk_levels=split_csv(risk_level),
            states=split_csv(state),
        )
        return _paged(
            items,
            page_token=page_token,
            page_size=page_size,
            surface_key="governance_approval_queue",
            dataset="approval_queue_items",
        )

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
        _identity(authorization)
        items = _service().list_audit_events(
            actor=actor,
            action_types=split_csv(action_type),
            target_type=target_type,
            from_ts=_parse_datetime(from_ts),
            to_ts=_parse_datetime(to_ts),
        )
        return _paged(
            items,
            page_token=page_token,
            page_size=page_size,
            surface_key="governance_audit",
            dataset="governance_audit_events",
        )

    @router.get("/api/v1/operator/mutation-review/{decision_id}")
    async def get_mutation_review(
        decision_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        payload = _service().mutation_review(decision_id)
        if payload is None:
            _not_found("Mutation review decision", decision_id)
        required = (
            "decision_id",
            "target_type",
            "target_id",
            "target_version",
            "action_type",
            "decision_state",
            "risk_level",
            "created_at",
        )
        missing = [field for field in required if payload.get(field) in (None, "")]
        if missing:
            _fail(503, "DEPENDENCY_UNAVAILABLE", "Mutation review evidence is incomplete", f"Missing fields: {missing}")
        return payload

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
        _identity(authorization)
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
                for session in page_data
            ],
            "meta": {"total": len(consultations), "page": page, "page_size": page_size, "staleness": _staleness()},
        }

    @router.get("/api/v1/consultations/{session_id}")
    def get_consultation(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        session = _service().get_consultation(session_id)
        if session is None:
            _not_found("Consultation session", session_id)
        return {
            "data": {
                **session,
                "_links": {
                    "self": f"/api/v1/consultations/{session_id}",
                    "participants": f"/api/v1/consultations/{session_id}/participants",
                    "outcome": f"/api/v1/consultations/{session_id}/outcome",
                    "evidence": f"/api/v1/consultations/{session_id}/evidence",
                },
            },
            "meta": {"staleness": _staleness()},
        }

    @router.get("/api/v1/consultations/{session_id}/participants")
    def get_consultation_participants(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        participants = _service().get_consultation_participants(session_id)
        if participants is None:
            _not_found("Consultation session", session_id)
        return {
            "data": [
                {
                    **participant,
                    "_links": {
                        "self": f"/api/v1/sessions/{participant['session_id']}",
                        "persona": f"/api/v1/personas/{participant['persona_id']}",
                    },
                }
                for participant in participants
            ],
            "meta": {"total": len(participants), "staleness": _staleness()},
        }

    @router.get("/api/v1/consultations/{session_id}/outcome")
    def get_consultation_outcome(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        outcome = _service().get_consultation_outcome(session_id)
        if outcome is None:
            _not_found("Consultation session", session_id)
        return {"data": outcome, "meta": {"staleness": _staleness()}}

    @router.get("/api/v1/consultations/{session_id}/evidence")
    def get_consultation_evidence(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        evidence = _service().get_consultation_evidence(session_id)
        if evidence is None:
            _not_found("Consultation session", session_id)
        processed, redacted_count = _redact(identity, list(evidence), capabilities=_capabilities(identity))
        return {"data": processed, "meta": {"total": len(processed), "staleness": _staleness(), "supporting_counts": {"redacted_evidence_count": redacted_count}}}

    @router.get("/api/v1/consultations/{session_id}/transcript")
    def get_consultation_transcript(
        session_id: str,
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        from_sequence_no: Optional[int] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        transcript = _service().get_consult_transcript(
            session_id,
            from_sequence_no=from_sequence_no,
            page_size=page_size,
            page_token=page_token,
        )
        if transcript is None:
            _not_found("Consultation session", session_id)
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
        _identity(authorization)
        items = _service().list_pending_approvals()
        return {"items": items, "count": len(items), "generated_at": _now()}

    @router.get("/bff/management/governance-ledger")
    async def bff_management_governance_ledger(
        source_type: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        q: str = Query(default=""),
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        return _service().governance_ledger(
            source_type=source_type,
            status=status,
            q=q,
            page_token=page_token,
            page_size=page_size,
        )

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
        _identity(authorization)
        items = _service().list_review_queue(
            item_types=split_csv(item_type),
            risk_levels=split_csv(risk_level),
            statuses=split_csv(status),
        )
        return _paged(items, page_token=page_token, page_size=page_size, surface_key="review_queue", dataset="governance_review_queue_items")

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
        _identity(authorization)
        review = _service().get_review(review_id.strip())
        if review is None:
            _not_found("Review item", review_id)
        return {"data": review, "meta": {"snapshot_at": _now(), "correlation_id": review_id, "staleness": _staleness()}}

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
        try:
            return await _service().submit_governance_action(
                action_kind="review",
                target_id=review_id.strip(),
                action_id=action_id.strip(),
                payload=payload,
                identity=identity,
                idempotency_key=_idempotency_key(idempotency_key, x_idempotency_key),
            )
        except RuntimeError:
            _fail(409, "IDEMPOTENCY_CONFLICT", "Idempotency key conflict", "The key is bound to another payload")

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
        _identity(authorization)
        clean_id = review_id.strip()
        events = [
            event
            for event in _service().list_audit_events()
            if str(event.get("target_id") or event.get("item_id") or "") == clean_id
            and str(event.get("target_type") or "") in {"Review", "GovernanceReviewItem"}
        ]
        return {"review_id": clean_id, "events": events, "meta": {"snapshot_at": _now(), "correlation_id": clean_id, "staleness": _staleness()}}

    @router.get("/bff/approvals/{approval_id}/evidence")
    async def bff_approval_evidence(
        approval_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _identity(authorization)
        clean_id = approval_id.strip()
        refs = _service().approval_evidence(clean_id)
        decision = _service().get_approval_detail(clean_id)
        if refs is None or decision is None:
            _not_found("Approval decision", approval_id)
        processed, redacted_count = _redact(identity, refs, capabilities=_capabilities(identity))
        return {
            "approval_id": clean_id,
            "evidence": processed,
            "correlation_id": decision.get("correlation_id") or decision.get("decision_id") or decision.get("id") or clean_id,
            "audit_ref": decision.get("audit_ref") or {"target_type": "ApprovalDecision", "target_id": clean_id, "href": f"/bff/audit/entities/ApprovalDecision/{clean_id}"},
            "meta": {"snapshot_at": _now(), "redacted_count": redacted_count, "staleness": _staleness()},
        }

    # 33. Explicit typed approval detail; replaces generic alias -------

    @router.get("/bff/approvals/{approval_id}")
    async def get_approval_detail(
        approval_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _identity(authorization)
        detail = _service().get_approval_detail(approval_id)
        if detail is None:
            _not_found("Approval decision", approval_id)
        return {"data": detail, "meta": _snapshot(_now())}

    # 34-35. Single and batch approval decisions -----------------------

    @router.post("/bff/approvals/{approval_id}/decide", status_code=202)
    async def bff_approvals_decide(
        approval_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = _identity(authorization, operator=True)
        _require_approver(identity)
        clean_id = approval_id.strip()
        if _service().get_approval_detail(clean_id) is None and _service().dataset_source("approval_decisions") != "missing":
            _not_found("Approval decision", clean_id)
        try:
            decision = _service().validate_decision(payload)
            result = await _service().submit_governance_action(
                action_kind="approval",
                target_id=clean_id,
                action_id=decision,
                payload={**payload, "decision_id": clean_id},
                identity=identity,
                idempotency_key=_idempotency_key(idempotency_key, x_idempotency_key),
            )
        except ValueError as exc:
            field = str(exc)
            _fail(422, "VALIDATION_FAILED", f"{field} is required or invalid", f"Invalid approval decision field: {field}", precondition_failed=field)
        except RuntimeError:
            _fail(409, "IDEMPOTENCY_CONFLICT", "Idempotency key conflict", "The key is bound to another payload")
        if publish_event is not None:
            publish_event("approval.stage.changed" if decision in {"request_revision", "request_changes", "escalate", "freeze"} else "approval.decided", {"approval_id": clean_id, "decision": decision, "actor_id": getattr(identity, "operator_id", None)})
        return result

    @router.post("/bff/approvals/batch-decide", status_code=202)
    async def bff_approvals_batch_decide(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> JSONResponse:
        identity = _identity(authorization, operator=True)
        _require_approver(identity)
        decisions = payload.get("decisions") if isinstance(payload.get("decisions"), list) else None
        if not decisions:
            _fail(422, "VALIDATION_FAILED", "decisions must be a non-empty list", "The decisions field must contain at least one item", precondition_failed="decisions")
        if len(decisions) > 50:
            _fail(422, "VALIDATION_FAILED", "batch-decide accepts at most 50 items", f"Received {len(decisions)} items", precondition_failed="decisions")
        batch_key = _idempotency_key(idempotency_key, x_idempotency_key)
        results: List[Dict[str, Any]] = []
        for index, item in enumerate(decisions):
            if not isinstance(item, dict) or not str(item.get("id") or "").strip():
                results.append({"index": index, "id": None, "status": "failed", "error": {"code": "VALIDATION_FAILED", "message": "id is required for each decision item"}})
                continue
            item_id = str(item["id"]).strip()
            try:
                decision = _service().validate_decision(item)
                if _service().get_approval_detail(item_id) is None and _service().dataset_source("approval_decisions") != "missing":
                    raise LookupError(item_id)
                command = await _service().submit_governance_action(
                    action_kind="approval",
                    target_id=item_id,
                    action_id=decision,
                    payload={**item, "decision_id": item_id},
                    identity=identity,
                    idempotency_key=f"{batch_key}::{index}::{item_id}",
                )
                data = command.get("data", {}) if isinstance(command, dict) else {}
                results.append({"index": index, "id": item_id, "status": "accepted", "command_id": data.get("command_id"), "commandId": data.get("commandId")})
            except LookupError:
                results.append({"index": index, "id": item_id, "status": "failed", "error": {"code": "RESOURCE_NOT_FOUND", "message": f"approval_id={item_id!r} does not exist"}})
            except ValueError as exc:
                results.append({"index": index, "id": item_id, "status": "failed", "error": {"code": "VALIDATION_FAILED", "message": f"{exc} is required or invalid"}})
            except RuntimeError:
                results.append({"index": index, "id": item_id, "status": "failed", "error": {"code": "IDEMPOTENCY_CONFLICT", "message": "idempotency key conflict"}})
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
