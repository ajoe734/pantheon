"""Strategy-version Workshop routes: list/create/select StrategySpec versions.

Split out of the former single-file strategy_workshop/router.py factory
(ACG-06-004). Route bodies below are unchanged from the original
implementation; only the surrounding closure scaffolding (this
build_versions_router wrapper binding the shared admission context) is new.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Response

from .._common import _StrategyVersionProjectionError
from ..events import _ws_publish
from ..operations import CanonicalOperationError
from ..store import WorkshopVersionProjectionConflict
from ..schemas import WorkshopVersionCreateRequest


def build_versions_router(
    *,
    store: Any,
    canonical: Any,
    utc_now: Callable[[], str],
    bff_error: Callable[..., HTTPException],
    ctx: Any,
) -> APIRouter:
    router = APIRouter(tags=["agora-workshop"])
    _scope = ctx.scope
    _scoped_session = ctx.scoped_session
    _etag = ctx.etag
    _admit_command = ctx.admit_command
    _complete_or_raise = ctx.complete_or_raise
    _require_command_headers = ctx.require_command_headers
    _project_strategy_version = ctx.project_strategy_version
    _read_strategy_version = ctx.read_strategy_version
    _canonical_error = ctx.canonical_error
    _fail_domain_command = ctx.fail_domain_command
    _command_response = ctx.command_response
    _require_replayable_receipt = ctx.require_replayable_receipt

    @router.get("/bff/agora/workshops/{workshop_id}/versions")
    def list_workshop_versions(
        workshop_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = _scoped_session(workshop_id, scope)
        etag = _etag(workshop_id, int(session.get("lock_version") or 1))
        response.headers["ETag"] = etag
        readbacks: Dict[str, tuple[Dict[str, Any], str, str]] = {}
        active_registry_id = str(
            session.get("active_strategy_spec_registry_id") or ""
        )
        if active_registry_id:
            try:
                active_projection = _read_strategy_version(
                    registry_id=active_registry_id,
                    scope=scope,
                    expected_strategy_id=str(session.get("strategy_id") or "") or None,
                    expected_workshop_id=workshop_id,
                )
                readbacks[active_registry_id] = active_projection
                active_readback, active_digest, active_strategy_id = active_projection
                store.ensure_current_version_link(
                    workshop_id=workshop_id,
                    strategy_id=active_strategy_id,
                    strategy_spec_registry_id=active_registry_id,
                    document_sha256=active_digest,
                )
            except CanonicalOperationError as exc:
                from services.control_plane.bff.models import ErrorCode
                raise bff_error(
                    503 if exc.retryable else 502,
                    ErrorCode.DEPENDENCY_UNAVAILABLE if exc.retryable else ErrorCode.UPSTREAM_ERROR,
                    "StrategySpec version readback is unavailable",
                    exc.reason,
                    precondition_failed="strategy_registry",
                ) from exc
            except _StrategyVersionProjectionError as exc:
                from services.control_plane.bff.models import ErrorCode
                code = (
                    ErrorCode.FORBIDDEN
                    if exc.status_code == 403
                    else ErrorCode.UPSTREAM_ERROR
                    if exc.status_code >= 500
                    else ErrorCode.RESOURCE_CONFLICT
                )
                raise bff_error(
                    exc.status_code,
                    code,
                    "StrategySpec version projection is inconsistent",
                    exc.reason,
                    precondition_failed="strategy_version_projection",
                ) from exc
            except WorkshopVersionProjectionConflict as exc:
                from services.control_plane.bff.models import ErrorCode
                raise bff_error(
                    409,
                    ErrorCode.RESOURCE_CONFLICT,
                    "Workshop version projection is inconsistent",
                    "WORKSHOP_VERSION_PROJECTION_CONFLICT",
                    precondition_failed="workshop_version_projection",
                ) from exc

        # Backfill is additive and leaves lock_version/ETag unchanged.  Refresh
        # the session so the response includes deterministic selected pointers.
        session = store.get_session(workshop_id) or session
        versions: List[Dict[str, Any]] = []
        for link in store.list_version_links(workshop_id):
            registry_id = str(link.get("strategy_spec_registry_id") or "")
            try:
                projected = readbacks.get(registry_id) or _read_strategy_version(
                    registry_id=registry_id,
                    scope=scope,
                    expected_strategy_id=str(link.get("strategy_id") or "") or None,
                    expected_workshop_id=workshop_id,
                )
                readback, digest, strategy_id = projected
                link = store.ensure_version_link_digest(
                    workshop_id=workshop_id,
                    workshop_version_id=str(link["workshop_version_id"]),
                    strategy_id=strategy_id,
                    document_sha256=digest,
                )
            except CanonicalOperationError as exc:
                from services.control_plane.bff.models import ErrorCode
                raise bff_error(
                    503 if exc.retryable else 502,
                    ErrorCode.DEPENDENCY_UNAVAILABLE if exc.retryable else ErrorCode.UPSTREAM_ERROR,
                    "StrategySpec version readback is unavailable",
                    exc.reason,
                    precondition_failed="strategy_registry",
                ) from exc
            except _StrategyVersionProjectionError as exc:
                from services.control_plane.bff.models import ErrorCode
                code = (
                    ErrorCode.FORBIDDEN
                    if exc.status_code == 403
                    else ErrorCode.UPSTREAM_ERROR
                    if exc.status_code >= 500
                    else ErrorCode.RESOURCE_CONFLICT
                )
                raise bff_error(
                    exc.status_code,
                    code,
                    "StrategySpec version projection is inconsistent",
                    exc.reason,
                    precondition_failed="strategy_version_projection",
                ) from exc
            except WorkshopVersionProjectionConflict as exc:
                from services.control_plane.bff.models import ErrorCode
                raise bff_error(
                    409,
                    ErrorCode.RESOURCE_CONFLICT,
                    "Workshop version projection is inconsistent",
                    "WORKSHOP_VERSION_PROJECTION_CONFLICT",
                    precondition_failed="workshop_version_projection",
                ) from exc
            versions.append({"version": link, "strategy_spec": readback})
        return {
            "data": {
                "versions": versions,
                "selected_version_id": session.get("selected_version_id"),
                "active_strategy_spec_registry_id": session.get(
                    "active_strategy_spec_registry_id"
                ),
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "request_id": x_request_id,
                "canonical_authority": "strategy_registry",
                "etag": etag,
                "no_direct_action": {
                    "deployment_triggered": False,
                    "order_submitted": False,
                    "live_capital_changed": False,
                },
            },
        }

    @router.post("/bff/agora/workshops/{workshop_id}/versions", status_code=201)
    def create_workshop_version(
        workshop_id: str,
        response: Response,
        body: Optional[WorkshopVersionCreateRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        from services.control_plane.bff.models import ErrorCode
        from services.research.strategy_spec.models import validate_strategy_spec_payload
        from services.research.strategy_spec.patching import PatchError, apply_patch_validated

        scope = _scope(
            authorization,
            x_tenant_id,
            write=True,
            x_mfa_token=x_mfa_token,
            mfa_required=True,
        )
        if body is None:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "VersionCreateRequest body is required",
                "REQUEST_BODY_REQUIRED",
                precondition_failed="request_body",
            )
        session = _scoped_session(workshop_id, scope)
        expected_version, command_key, request_id = _require_command_headers(
            workshop_id=workshop_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=x_request_id,
        )
        if (
            body.expected_workshop_version is not None
            and body.expected_workshop_version != expected_version
        ):
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Body and If-Match workshop versions disagree",
                "EXPECTED_WORKSHOP_VERSION_MISMATCH",
                precondition_failed="expected_workshop_version",
            )
        request_payload = body.model_dump(mode="json")
        receipt, request_hash = _admit_command(
            workshop_id=workshop_id,
            scope=scope,
            operation="create_version",
            expected_lock_version=expected_version,
            idempotency_key=command_key,
            request_id=request_id,
            payload=request_payload,
        )
        replay = _require_replayable_receipt(receipt=receipt, workshop_id=workshop_id)
        if replay is not None:
            return _command_response(
                receipt=receipt,
                resource=replay,
                session=store.get_session(workshop_id) or session,
                scope=scope,
                canonical_authority="strategy_registry",
                response=response,
            )

        base_registry_id = str(session.get("active_strategy_spec_registry_id") or "")
        if not base_registry_id:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.PRECONDITION_FAILED,
                message="Workshop has no active StrategySpec",
                reason="ACTIVE_STRATEGY_SPEC_REQUIRED",
                precondition_failed="active_strategy_spec_registry_id",
            )
        try:
            base_readback, base_document_sha256, base_strategy_id = (
                _read_strategy_version(
                    registry_id=base_registry_id,
                    scope=scope,
                    expected_strategy_id=str(session.get("strategy_id") or "") or None,
                    expected_workshop_id=workshop_id,
                )
            )
            base_link = store.ensure_current_version_link(
                workshop_id=workshop_id,
                strategy_id=base_strategy_id,
                strategy_spec_registry_id=base_registry_id,
                document_sha256=base_document_sha256,
            )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
            )
        except _StrategyVersionProjectionError as exc:
            code = (
                ErrorCode.FORBIDDEN
                if exc.status_code == 403
                else ErrorCode.UPSTREAM_ERROR
                if exc.status_code >= 500
                else ErrorCode.RESOURCE_CONFLICT
            )
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=exc.status_code,
                code=code,
                message="StrategySpec version projection is inconsistent",
                reason=exc.reason,
                precondition_failed="strategy_version_projection",
            )
        except WorkshopVersionProjectionConflict:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.RESOURCE_CONFLICT,
                message="Workshop version projection is inconsistent",
                reason="WORKSHOP_VERSION_PROJECTION_CONFLICT",
                precondition_failed="workshop_version_projection",
            )
        base_entry = dict(base_readback["entry"])
        base_doc = dict((base_entry.get("metadata") or {}).get("strategy_spec") or {})
        if not base_doc:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.PRECONDITION_FAILED,
                message="Active Registry entry has no inline StrategySpec document",
                reason="STRATEGY_SPEC_DOCUMENT_REQUIRED",
                precondition_failed="strategy_spec",
            )
        try:
            patched, patched_document_sha256 = apply_patch_validated(
                base_doc,
                body.patch,
                expected_base_sha256=body.base_document_sha256,
            )
            version_parts = [int(part) for part in str(base_entry.get("version") or "").split(".")]
            if len(version_parts) != 3:
                raise ValueError("Registry StrategySpec version must be semantic x.y.z")
            version_parts[2] += 1
            next_version = ".".join(str(part) for part in version_parts)
            # StrategySpec.spec_version is the document-schema version (currently
            # 1.0), not the Registry artifact semver.  The immutable Registry
            # entry's ``version`` below advances while the document contract
            # version remains unchanged.
            validate_strategy_spec_payload(patched)
        except (PatchError, ValueError) as exc:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=422,
                code=ErrorCode.VALIDATION_FAILED,
                message="StrategySpec patch is invalid",
                reason=getattr(exc, "error_code", "PATCH_VALIDATION_FAILED"),
                precondition_failed="patch",
            )
        digest = hashlib.sha256(
            f"{workshop_id}:{command_key}".encode("utf-8")
        ).hexdigest()[:20]
        registry_id = f"reg-ws-{digest}"
        workshop_version_id = f"wsv-{digest}"
        strategy_id = base_strategy_id
        try:
            registry_readback = canonical.create_strategy_spec(
                {
                    "registry_id": registry_id,
                    "strategy_id": strategy_id,
                    "version": next_version,
                    "artifact_state": "draft",
                    "lineage": {"parent_registry_ids": [base_registry_id]},
                    "metadata": {
                        "tenant_id": scope.tenant_id,
                        "owner_user_id": scope.user_id,
                        "workshop_id": workshop_id,
                        "workshop_request_id": request_id,
                        "reason": body.reason,
                    },
                    "strategy_spec": patched,
                }
            )
            (
                registry_readback,
                authoritative_document_sha256,
                authoritative_strategy_id,
            ) = _project_strategy_version(
                readback=registry_readback,
                registry_id=registry_id,
                scope=scope,
                expected_strategy_id=strategy_id,
                expected_workshop_id=workshop_id,
            )
            if (
                authoritative_strategy_id != strategy_id
                or authoritative_document_sha256 != patched_document_sha256
            ):
                raise _StrategyVersionProjectionError(
                    "STRATEGY_SPEC_AUTHORITATIVE_DIGEST_MISMATCH"
                )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
            )
        except _StrategyVersionProjectionError as exc:
            code = (
                ErrorCode.FORBIDDEN
                if exc.status_code == 403
                else ErrorCode.UPSTREAM_ERROR
                if exc.status_code >= 500
                else ErrorCode.RESOURCE_CONFLICT
            )
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=exc.status_code,
                code=code,
                message="Authoritative StrategySpec version is inconsistent",
                reason=exc.reason,
                precondition_failed="strategy_version_projection",
            )
        existing_links = store.list_version_links(workshop_id)
        link = {
            "workshop_version_id": workshop_version_id,
            "workshop_id": workshop_id,
            "strategy_id": strategy_id,
            "strategy_spec_registry_id": registry_id,
            "parent_workshop_version_id": base_link["workshop_version_id"],
            "source_event_id": f"wsevt-version-{digest}",
            "sequence_no": len(existing_links) + 1,
            "document_sha256": authoritative_document_sha256,
            "created_by": scope.user_id,
            "created_at": utc_now(),
        }
        resource = {"version": link, "strategy_spec": registry_readback}
        receipt = _complete_or_raise(
            workshop_id=workshop_id,
            operation="create_version",
            scope=scope,
            idempotency_key=command_key,
            request_hash=request_hash,
            result=resource,
            canonical_refs={
                "strategy_spec_registry_id": registry_id,
                "workshop_version_id": workshop_version_id,
            },
            version_link=link,
            session_updates={"strategy_id": strategy_id, "status": "in_review"},
            event={
                "event_id": link["source_event_id"],
                "actor_type": "operator",
                "event_type": "version_created",
                "redacted_summary": "StrategySpec draft version created",
                "payload_refs_json": {
                    "workshop_version_id": workshop_version_id,
                    "strategy_spec_registry_id": registry_id,
                },
                "trace_id": request_id,
            },
        )
        _ws_publish(
            workshop_id,
            "workshop.version.created",
            {
                "workshop_version_id": workshop_version_id,
                "strategy_spec_registry_id": registry_id,
            },
            utc_now_fn=utc_now,
        )
        return _command_response(
            receipt=receipt,
            resource=resource,
            session=store.get_session(workshop_id) or session,
            scope=scope,
            canonical_authority="strategy_registry",
            response=response,
        )

    @router.post("/bff/agora/workshops/{workshop_id}/versions/{version_id}/select")
    def select_workshop_version(
        workshop_id: str,
        version_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        from services.control_plane.bff.models import ErrorCode

        scope = _scope(
            authorization,
            x_tenant_id,
            write=True,
            x_mfa_token=x_mfa_token,
            mfa_required=True,
        )
        session = _scoped_session(workshop_id, scope)
        link = store.get_version_link(workshop_id, version_id)
        if link is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Workshop version not found",
                version_id,
            )
        expected_version, command_key, request_id = _require_command_headers(
            workshop_id=workshop_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=x_request_id,
        )
        request_payload = {"version_id": version_id}
        receipt, request_hash = _admit_command(
            workshop_id=workshop_id,
            scope=scope,
            operation="select_version",
            expected_lock_version=expected_version,
            idempotency_key=command_key,
            request_id=request_id,
            payload=request_payload,
        )
        replay = _require_replayable_receipt(receipt=receipt, workshop_id=workshop_id)
        if replay is not None:
            return _command_response(
                receipt=receipt,
                resource=replay,
                session=store.get_session(workshop_id) or session,
                scope=scope,
                canonical_authority="strategy_registry",
                response=response,
            )
        registry_id = str(link["strategy_spec_registry_id"])
        try:
            registry_readback, document_sha256, strategy_id = (
                _read_strategy_version(
                    registry_id=registry_id,
                    scope=scope,
                    expected_strategy_id=str(link.get("strategy_id") or "") or None,
                    expected_workshop_id=workshop_id,
                )
            )
            link = store.ensure_version_link_digest(
                workshop_id=workshop_id,
                workshop_version_id=version_id,
                strategy_id=strategy_id,
                document_sha256=document_sha256,
            )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="select_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
            )
        except _StrategyVersionProjectionError as exc:
            code = (
                ErrorCode.FORBIDDEN
                if exc.status_code == 403
                else ErrorCode.UPSTREAM_ERROR
                if exc.status_code >= 500
                else ErrorCode.RESOURCE_CONFLICT
            )
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="select_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=exc.status_code,
                code=code,
                message="StrategySpec version projection is inconsistent",
                reason=exc.reason,
                precondition_failed="strategy_version_projection",
            )
        except WorkshopVersionProjectionConflict:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="select_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.RESOURCE_CONFLICT,
                message="Workshop version projection is inconsistent",
                reason="WORKSHOP_VERSION_PROJECTION_CONFLICT",
                precondition_failed="workshop_version_projection",
            )
        selected_session = {
            **session,
            "strategy_id": link["strategy_id"],
            "selected_version_id": version_id,
            "active_workshop_version_id": version_id,
            "active_strategy_spec_registry_id": registry_id,
            "status": "in_review",
            "lock_version": receipt.get("admitted_lock_version"),
        }
        resource = {
            "workshop": selected_session,
            "version": link,
            "strategy_spec": registry_readback,
        }
        receipt = _complete_or_raise(
            workshop_id=workshop_id,
            operation="select_version",
            scope=scope,
            idempotency_key=command_key,
            request_hash=request_hash,
            result=resource,
            canonical_refs={
                "strategy_spec_registry_id": registry_id,
                "workshop_version_id": version_id,
            },
            session_updates={
                "strategy_id": link["strategy_id"],
                "selected_version_id": version_id,
                "active_workshop_version_id": version_id,
                "active_strategy_spec_registry_id": registry_id,
                "status": "in_review",
            },
            event={
                "event_id": f"wsevt-select-{hashlib.sha256(command_key.encode()).hexdigest()[:16]}",
                "actor_type": "operator",
                "event_type": "version_selected",
                "redacted_summary": "Workshop version selected",
                "payload_refs_json": {
                    "workshop_version_id": version_id,
                    "strategy_spec_registry_id": registry_id,
                },
                "trace_id": request_id,
            },
        )
        return _command_response(
            receipt=receipt,
            resource=resource,
            session=store.get_session(workshop_id) or selected_session,
            scope=scope,
            canonical_authority="strategy_registry",
            response=response,
        )

    return router
