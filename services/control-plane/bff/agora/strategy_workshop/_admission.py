"""Shared command-admission/compensation context for the Workshop routes.

The strategy-workshop factory (router.py) builds one admission context per
router assembly and hands it to each route-group module (routes/session.py,
routes/versions.py, routes/execution.py) so the ETag/idempotency/CAS command
lifecycle and the two-phase-commit-style compensation logic have exactly one
implementation shared across all route groups instead of being duplicated
per file (ACG-06-004: "package router becomes composition only").

Every function below was moved verbatim out of the old single-file
create_strategy_workshop_router() closure; only the outer wrapper
(build_admission_context) and the returned namespace are new.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, Mapping, Optional

from fastapi import HTTPException, Response

from ._common import (
    _StrategyVersionProjectionError,
    _identity_for_scope,
    _parse_etag_lock_version,
    _raise_cross_user_forbidden,
)
from .operations import CanonicalOperationError
from .readiness import build_readiness_assessment as _build_readiness_assessment


def build_admission_context(
    *,
    store: Any,
    canonical: Any,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> SimpleNamespace:
    """Build the shared scope/admission/compensation closures for one router assembly."""

    # Stores with claim_resumable_command also accept resolve_compensation on
    # complete_command/fail_command, so adopting a lineage source and
    # resolving it happen atomically with the successor's terminal write.
    _supports_atomic_lineage = hasattr(store, "claim_resumable_command")

    def _source_resolution(
        resume: Mapping[str, Any],
        *,
        resolution: str,
        resolved_by: str,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Transactional resolution payload for an adopted lineage source."""
        return {
            "operation": (resume["receipt"] or {}).get("operation"),
            "idempotency_key": str(
                (resume["receipt"] or {}).get("idempotency_key") or ""
            ),
            "resolution": {
                "resolved_at": utc_now(),
                "resolution": resolution,
                "resolved_by_idempotency_key": resolved_by,
                **dict(extra or {}),
            },
        }

    def _source_claim_release(
        resume: Mapping[str, Any],
        *,
        released_by: str,
    ) -> Dict[str, Any]:
        """Release an adoption claim without resolving the source lineage."""
        return {
            "operation": (resume["receipt"] or {}).get("operation"),
            "idempotency_key": str(
                (resume["receipt"] or {}).get("idempotency_key") or ""
            ),
            "resolution": {
                "claimed_by_idempotency_key": None,
                "claimed_at": None,
                "claim_released_at": utc_now(),
                "claim_released_by_idempotency_key": released_by,
            },
        }

    # Lazy import to avoid circular import at module load time
    def _scope(
        authorization: Optional[str],
        x_tenant_id: Optional[str] = None,
        *,
        write: bool = False,
        x_mfa_token: Optional[str] = None,
        mfa_required: bool = False,
    ) -> Any:
        from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
        from ..models import AgoraErrorCode

        try:
            raw_identity = extract_identity(authorization, mfa_token=x_mfa_token)
        except TypeError:
            # Narrow test adapters written before the MFA-bearing factory
            # signature remain supported.  Production assembly accepts the
            # keyword and validates it in the shared auth facade.
            raw_identity = extract_identity(authorization)
        identity = _identity_for_scope(raw_identity)
        require_read_role(identity)
        if write:
            require_write_role(identity)
            if mfa_required and not bool(getattr(identity, "mfa_verified", False)):
                # The explicit header is accepted only for the dev auth stub.
                # Strict JWT/OIDC paths must set mfa_verified in the shared
                # inbound-auth facade after validating the token/claim.
                stub_mfa = bool(
                    str(x_mfa_token or "").strip()
                    and getattr(identity, "token_kind", "") in {"stub", "test"}
                )
                if not stub_mfa:
                    from models import ErrorCode
                    raise bff_error(
                        401,
                        ErrorCode.AUTH_REQUIRED,
                        "MFA verification is required for workshop commands",
                        "MFA_REQUIRED",
                        precondition_failed="mfa_verification",
                        suggestion="Supply a valid X-MFA-Token or MFA-verified identity",
                    )
        try:
            return resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id,
            )
        except AgoraScopeResolutionError as exc:
            from models import ErrorCode  # BFF top-level models
            code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
            raise bff_error(
                exc.status_code,
                code,
                exc.message,
                exc.reason,
                precondition_failed="agora_user_scope",
                details_extra=exc.details,
            )

    def _scoped_session(workshop_id: str, scope: Any) -> Dict[str, Any]:
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        return session

    def _etag(workshop_id: str, lock_version: int) -> str:
        return f'W/"workshop:{workshop_id}:v{lock_version}"'

    def _project_strategy_version(
        *,
        readback: Mapping[str, Any],
        registry_id: str,
        scope: Any,
        expected_strategy_id: Optional[str] = None,
        expected_workshop_id: Optional[str] = None,
    ) -> tuple[Dict[str, Any], str, str]:
        from services.research.strategy_spec.patching import compute_document_sha256

        entry = readback.get("entry")
        if not isinstance(entry, Mapping):
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_READBACK_MISSING",
                status_code=502,
            )
        if str(entry.get("registry_id") or "") != registry_id:
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_REGISTRY_ID_MISMATCH",
                status_code=502,
            )
        strategy_id = str(entry.get("strategy_id") or "")
        if not strategy_id or (
            expected_strategy_id and strategy_id != expected_strategy_id
        ):
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_STRATEGY_ID_MISMATCH"
            )
        metadata = entry.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        tenant_id = str(metadata.get("tenant_id") or "")
        owner_user_id = str(metadata.get("owner_user_id") or "")
        if (tenant_id and tenant_id != scope.tenant_id) or (
            owner_user_id and owner_user_id != scope.user_id
        ):
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_SCOPE_MISMATCH",
                status_code=403,
            )
        linked_workshop_id = str(metadata.get("workshop_id") or "")
        if (
            linked_workshop_id
            and expected_workshop_id
            and linked_workshop_id != expected_workshop_id
        ):
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_WORKSHOP_ID_MISMATCH"
            )
        document = metadata.get("strategy_spec")
        if not isinstance(document, Mapping):
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_DOCUMENT_REQUIRED",
                status_code=502,
            )
        return dict(readback), compute_document_sha256(document), strategy_id

    def _read_strategy_version(
        *,
        registry_id: str,
        scope: Any,
        expected_strategy_id: Optional[str] = None,
        expected_workshop_id: Optional[str] = None,
    ) -> tuple[Dict[str, Any], str, str]:
        readback = canonical.get_strategy_spec(registry_id)
        return _project_strategy_version(
            readback=readback,
            registry_id=registry_id,
            scope=scope,
            expected_strategy_id=expected_strategy_id,
            expected_workshop_id=expected_workshop_id,
        )

    def _request_hash(payload: Mapping[str, Any]) -> str:
        canonical_payload = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical_payload).hexdigest()

    def _require_command_headers(
        *,
        workshop_id: str,
        if_match: Optional[str],
        idempotency_key: Optional[str],
        request_id: Optional[str],
    ) -> tuple[int, str, str]:
        from models import ErrorCode

        if if_match is None:
            raise bff_error(
                428,
                ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required for workshop mutations",
                "missing_if_match",
                precondition_failed="if_match",
                suggestion="GET the workshop and retry with its current ETag",
            )
        if not str(idempotency_key or "").strip():
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                precondition_failed="idempotency_key",
            )
        if not str(request_id or "").strip():
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "X-Request-Id header is required",
                "missing_request_id",
                precondition_failed="request_id",
            )
        return (
            _parse_etag_lock_version(if_match, workshop_id),
            str(idempotency_key).strip(),
            str(request_id).strip(),
        )

    def _public_receipt(receipt: Mapping[str, Any]) -> Dict[str, Any]:
        status = str(receipt.get("status") or "")
        result: Dict[str, Any] = {
            "receipt_id": receipt.get("receipt_id") or receipt.get("command_id"),
            "operation": receipt.get("operation"),
            "status": status,
            "command_terminal": status in {"completed", "failed"},
            "request_id": receipt.get("request_id"),
            "trace_id": receipt.get("trace_id"),
            "idempotency_key": receipt.get("idempotency_key"),
            "request_hash": receipt.get("request_hash"),
            "expected_lock_version": receipt.get("expected_lock_version"),
            "resulting_lock_version": (
                receipt.get("resulting_lock_version")
                or receipt.get("admitted_lock_version")
            ),
            "admitted_at": receipt.get("admitted_at"),
            "completed_at": receipt.get("completed_at"),
            "canonical_refs": receipt.get("canonical_refs")
            or receipt.get("canonical_refs_json")
            or {},
        }
        return {key: value for key, value in result.items() if value is not None}

    def _command_response(
        *,
        receipt: Mapping[str, Any],
        resource: Mapping[str, Any],
        session: Mapping[str, Any],
        scope: Any,
        canonical_authority: str,
        response: Response,
    ) -> Dict[str, Any]:
        lock_version = int(
            receipt.get("resulting_lock_version")
            or receipt.get("admitted_lock_version")
            or session.get("lock_version")
            or 1
        )
        value = _etag(str(session["workshop_id"]), lock_version)
        response.headers["ETag"] = value
        no_direct_action = {
            "deployment_triggered": False,
            "order_submitted": False,
            "live_capital_changed": False,
        }
        return {
            "data": {
                "command_receipt": _public_receipt(receipt),
                "resource": dict(resource),
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "etag": value,
                "canonical_authority": canonical_authority,
                "no_direct_action": no_direct_action,
            },
        }

    def _raise_admission_failure(
        *,
        workshop_id: str,
        result: Mapping[str, Any],
    ) -> None:
        from models import ErrorCode

        outcome = str(result.get("outcome") or "")
        current_version = int(result.get("current_lock_version") or 1)
        if outcome == "idempotency_conflict":
            raise bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency-Key was already used with a different request",
                "IDEMPOTENCY_REQUEST_HASH_MISMATCH",
                precondition_failed="idempotency_key",
            )
        if outcome in {"scope_mismatch"}:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        if outcome == "not_found":
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Workshop not found",
                workshop_id,
            )
        if outcome == "terminal":
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Workshop is in a terminal state",
                "WORKSHOP_TERMINAL_STATE",
                precondition_failed="workshop_status",
            )
        if outcome == "stale":
            current_etag = _etag(workshop_id, current_version)
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Concurrent modification: ETag mismatch",
                "CONCURRENT_MODIFICATION",
                precondition_failed="if_match",
                details_extra={
                    "current_etag": current_etag,
                    "latest_href": f"/bff/agora/workshops/{workshop_id}",
                },
            )
        raise bff_error(
            409,
            ErrorCode.RESOURCE_CONFLICT,
            "Workshop command could not be admitted",
            outcome or "COMMAND_ADMISSION_FAILED",
        )

    def _admit_command(
        *,
        workshop_id: str,
        scope: Any,
        operation: str,
        expected_lock_version: int,
        idempotency_key: str,
        request_id: str,
        payload: Mapping[str, Any],
    ) -> tuple[Dict[str, Any], str]:
        payload_hash = _request_hash(payload)
        admission = store.admit_command(
            workshop_id=workshop_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
            expected_lock_version=expected_lock_version,
            request_payload=dict(payload),
            request_id=request_id,
            trace_id=request_id,
        )
        outcome = str(admission.get("outcome") or "")
        if outcome not in {"admitted", "replay"}:
            _raise_admission_failure(workshop_id=workshop_id, result=admission)
        receipt = admission.get("receipt")
        if not isinstance(receipt, dict):
            from models import ErrorCode
            raise bff_error(
                500,
                ErrorCode.UPSTREAM_ERROR,
                "Workshop command admission did not return a receipt",
                "COMMAND_RECEIPT_MISSING",
            )
        return receipt, payload_hash

    def _canonical_error(
        *,
        workshop_id: str,
        operation: str,
        scope: Any,
        idempotency_key: str,
        request_hash: str,
        error: CanonicalOperationError,
        compensation: Optional[Mapping[str, Any]] = None,
        resume_digest: Optional[str] = None,
        resume: Optional[Mapping[str, Any]] = None,
    ) -> None:
        from models import ErrorCode

        partial_effects = {
            key: value
            for key, value in {
                # An adopted source's recorded ids stay part of the lineage
                # even when the failing downstream call reports none itself.
                **dict((resume or {}).get("partial_effects") or {}),
                **dict(getattr(error, "partial_effects", None) or {}),
            }.items()
            if value
        }
        compensation_payload = dict(compensation or {})
        resumable = bool(resume_digest) and (bool(partial_effects) or error.retryable)
        if resumable:
            # Durable partial-effect lineage: a new-key retry of the same
            # request body resumes these downstream resources (and reuses the
            # recorded downstream idempotency digest) instead of duplicating.
            compensation_payload["resumable"] = True
            compensation_payload["downstream_idempotency_digest"] = resume_digest
            if partial_effects:
                compensation_payload["partial_effects"] = partial_effects
        fail_kwargs: Dict[str, Any] = {}
        if partial_effects:
            fail_kwargs["canonical_refs"] = partial_effects
        if resume is not None and _supports_atomic_lineage:
            if resumable:
                # The lineage moves onto this failed receipt in the same
                # transaction, so exactly one receipt stays resumable.
                fail_kwargs["resolve_compensation"] = _source_resolution(
                    resume,
                    resolution="superseded",
                    resolved_by=idempotency_key,
                )
            else:
                # This attempt cannot carry the lineage forward; release the
                # claim so the recorded source stays adoptable by a retry.
                fail_kwargs["resolve_compensation"] = _source_claim_release(
                    resume,
                    released_by=idempotency_key,
                )
        store.fail_command(
            workshop_id=workshop_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            failure={
                "authority": error.authority,
                "reason": error.reason,
                "status_code": error.status_code,
                "retryable": error.retryable,
            },
            compensation=compensation_payload,
            **fail_kwargs,
        )
        status_code = 503 if error.retryable else 502
        if error.status_code == 404:
            status_code = 409
        raise bff_error(
            status_code,
            ErrorCode.DEPENDENCY_UNAVAILABLE if error.retryable else ErrorCode.UPSTREAM_ERROR,
            "Canonical downstream operation failed",
            error.reason,
            precondition_failed=error.authority,
            suggestion=(
                "Refetch the workshop and retry with a new command key; "
                "recorded partial downstream effects will be resumed, not duplicated"
                if resumable
                else "Refetch the workshop before retrying with a new command key"
            ),
        )

    def _fail_domain_command(
        *,
        workshop_id: str,
        operation: str,
        scope: Any,
        idempotency_key: str,
        request_hash: str,
        status_code: int,
        code: Any,
        message: str,
        reason: str,
        precondition_failed: str,
        resolve_source: Optional[Mapping[str, Any]] = None,
    ) -> None:
        fail_kwargs: Dict[str, Any] = {}
        if resolve_source is not None and _supports_atomic_lineage:
            fail_kwargs["resolve_compensation"] = dict(resolve_source)
        store.fail_command(
            workshop_id=workshop_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            failure={
                "reason": reason,
                "status_code": status_code,
                "precondition_failed": precondition_failed,
            },
            compensation={"workshop_effect": "none"},
            **fail_kwargs,
        )
        raise bff_error(
            status_code,
            code,
            message,
            reason,
            precondition_failed=precondition_failed,
        )

    @staticmethod
    def _receipt_result(receipt: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        value = receipt.get("result")
        if value is None:
            value = receipt.get("result_json")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None
        return dict(value) if isinstance(value, Mapping) else None

    def _require_replayable_receipt(
        *,
        receipt: Mapping[str, Any],
        workshop_id: str,
    ) -> Optional[Dict[str, Any]]:
        from models import ErrorCode

        status = str(receipt.get("status") or "")
        if status == "completed":
            result = _receipt_result(receipt)
            if result is None:
                raise bff_error(
                    500,
                    ErrorCode.UPSTREAM_ERROR,
                    "Completed command receipt is missing its canonical result",
                    "COMMAND_RESULT_MISSING",
                )
            return result
        if status == "failed":
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "The prior command attempt failed and was compensated",
                "COMMAND_PREVIOUSLY_FAILED",
                precondition_failed="idempotency_key",
                suggestion="GET the workshop and retry with its latest ETag and a new Idempotency-Key",
                details_extra={"latest_href": f"/bff/agora/workshops/{workshop_id}"},
            )
        return None

    def _complete_or_raise(
        *,
        workshop_id: str,
        operation: str,
        scope: Any,
        idempotency_key: str,
        request_hash: str,
        result: Mapping[str, Any],
        canonical_refs: Mapping[str, Any],
        version_link: Optional[Mapping[str, Any]] = None,
        session_updates: Optional[Mapping[str, Any]] = None,
        event: Optional[Mapping[str, Any]] = None,
        downstream_digest: Optional[str] = None,
        resume: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        from models import ErrorCode

        def _commit_failure_compensation() -> Dict[str, Any]:
            # The canonical downstream effect exists; only the local
            # projection commit failed.  With a downstream digest the
            # compensation is resumable: a new-key retry adopts the recorded
            # downstream resources instead of dispatching duplicates.
            compensation: Dict[str, Any] = {
                "required": True,
                "canonical_refs": dict(canonical_refs),
            }
            if downstream_digest:
                compensation["resumable"] = True
                compensation["downstream_idempotency_digest"] = downstream_digest
                compensation["partial_effects"] = {
                    key: value
                    for key, value in dict(canonical_refs).items()
                    if value
                }
            return compensation

        def _lineage_kwargs(resolution: str) -> Dict[str, Any]:
            # Adopted-source resolution rides the successor's terminal write
            # so the transaction commits both or neither.
            if resume is None or not _supports_atomic_lineage:
                return {}
            if resolution == "superseded" and not downstream_digest:
                # This failed receipt carries no resumable lineage of its
                # own; release the claim so the source stays adoptable.
                return {
                    "resolve_compensation": _source_claim_release(
                        resume,
                        released_by=idempotency_key,
                    )
                }
            return {
                "resolve_compensation": _source_resolution(
                    resume,
                    resolution=resolution,
                    resolved_by=idempotency_key,
                )
            }

        try:
            completed = store.complete_command(
                workshop_id=workshop_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=dict(result),
                canonical_refs=dict(canonical_refs),
                version_link=dict(version_link) if version_link else None,
                session_updates=dict(session_updates or {}),
                event=dict(event) if event else None,
                **_lineage_kwargs("resumed"),
            )
        except Exception as exc:
            try:
                store.fail_command(
                    workshop_id=workshop_id,
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    failure={"reason": "WORKSHOP_COMMIT_EXCEPTION"},
                    compensation=_commit_failure_compensation(),
                    **_lineage_kwargs("superseded"),
                )
            except Exception:
                pass
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Workshop command store could not commit canonical readback",
                "WORKSHOP_COMMIT_EXCEPTION",
                precondition_failed="workshop_command_store",
            ) from exc
        if str(completed.get("outcome") or "") not in {"completed", "replay"}:
            store.fail_command(
                workshop_id=workshop_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                failure={"reason": "WORKSHOP_COMMIT_FAILED"},
                compensation=_commit_failure_compensation(),
                **_lineage_kwargs("superseded"),
            )
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Canonical effect was recorded but workshop projection could not commit",
                "WORKSHOP_COMMIT_FAILED",
                precondition_failed="workshop_command_store",
            )
        receipt = completed.get("receipt")
        if not isinstance(receipt, dict):
            raise bff_error(
                500,
                ErrorCode.UPSTREAM_ERROR,
                "Workshop command completion did not return a receipt",
                "COMMAND_RECEIPT_MISSING",
            )
        return receipt

    def _resume_context(
        *,
        workshop_id: str,
        scope: Any,
        operation: str,
        request_hash: str,
        claimed_by: str,
    ) -> Optional[Dict[str, Any]]:
        """Durable partial-effect lineage of the same logical request.

        Matching by ``request_hash`` restricts resume to a retry of the same
        request body; a genuinely different request never adopts another
        command's downstream resources.  With a claim-capable store the find
        and the claim are one atomic step, so concurrent new-key retries can
        never both adopt the same source: the loser opens fresh downstream
        resources under its own digest instead.
        """

        if _supports_atomic_lineage:
            receipt = store.claim_resumable_command(
                workshop_id=workshop_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                operation=operation,
                request_hash=request_hash,
                claimed_by_idempotency_key=claimed_by,
            )
        elif hasattr(store, "find_resumable_command"):
            receipt = store.find_resumable_command(
                workshop_id=workshop_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                operation=operation,
                request_hash=request_hash,
            )
        else:
            return None
        if not receipt:
            return None
        compensation = receipt.get("compensation") or {}
        return {
            "receipt": receipt,
            "digest": str(compensation.get("downstream_idempotency_digest") or ""),
            "partial_effects": {
                key: value
                for key, value in dict(
                    compensation.get("partial_effects") or {}
                ).items()
                if value
            },
        }

    def _resolve_resumed_compensation(
        *,
        workshop_id: str,
        scope: Any,
        operation: str,
        resume: Optional[Dict[str, Any]],
        resolved_by_idempotency_key: str,
    ) -> None:
        # Legacy best-effort fallback only: an atomic-lineage store resolves
        # the source inside the successor's completion transaction instead.
        if (
            resume is None
            or _supports_atomic_lineage
            or not hasattr(store, "resolve_command_compensation")
        ):
            return
        try:
            store.resolve_command_compensation(
                workshop_id=workshop_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                operation=operation,
                idempotency_key=str(resume["receipt"].get("idempotency_key") or ""),
                resolution={
                    "resolved_at": utc_now(),
                    "resolution": "resumed",
                    "resolved_by_idempotency_key": resolved_by_idempotency_key,
                },
            )
        except Exception:
            # Resolution is bookkeeping over an already-failed receipt; the
            # new completed receipt remains the authoritative delivery truth.
            pass

    @staticmethod
    def _approval_actor(record: Mapping[str, Any]) -> str:
        value = (
            record.get("reviewer")
            or record.get("approver")
            or record.get("decided_by")
            or record.get("actor_id")
        )
        if isinstance(value, Mapping):
            value = value.get("actor_id") or value.get("id")
        return str(value or "").strip()

    def _require_approval(
        *,
        approval_decision_id: str,
        workshop_id: str,
        version_id: str,
        session: Mapping[str, Any],
        scope: Any,
    ) -> Dict[str, Any]:
        from models import ErrorCode

        try:
            decision = canonical.get_approval_decision(approval_decision_id)
        except CanonicalOperationError as exc:
            status_code = 503 if exc.retryable else 409
            code = ErrorCode.DEPENDENCY_UNAVAILABLE if exc.retryable else ErrorCode.HUMAN_GATE_PENDING
            raise bff_error(
                status_code,
                code,
                "Authoritative approval is required",
                exc.reason,
                precondition_failed="approval_decision_id",
            ) from exc
        outcome_values = [
            decision[field]
            for field in ("decision", "outcome")
            if field in decision
        ]
        state_values = [
            decision[field]
            for field in ("decision_state", "state")
            if field in decision
        ]
        if not outcome_values or any(
            value not in ("approved", "approved_with_conditions")
            for value in outcome_values
        ):
            raise bff_error(
                409,
                ErrorCode.HUMAN_GATE_PENDING,
                "Approval decision is not approved",
                "APPROVAL_NOT_APPROVED",
                precondition_failed="approval_decision_id",
            )
        if not state_values or any(value != "decided" for value in state_values):
            raise bff_error(
                409,
                ErrorCode.HUMAN_GATE_PENDING,
                "Approval decision is not terminal",
                "APPROVAL_NOT_DECIDED",
                precondition_failed="approval_decision_id",
            )
        tenant_id = str(decision.get("tenant_id") or "").strip()
        owner_user_id = str(
            decision.get("owner_user_id") or decision.get("user_id") or ""
        ).strip()
        if not tenant_id or tenant_id != scope.tenant_id:
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Approval decision is outside the workshop tenant",
                "APPROVAL_TENANT_MISMATCH",
                precondition_failed="approval_scope",
            )
        if not owner_user_id or owner_user_id != scope.user_id:
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Approval decision is outside the workshop user scope",
                "APPROVAL_USER_MISMATCH",
                precondition_failed="approval_scope",
            )
        target_id = str(decision.get("target_id") or "").strip()
        target_version = str(decision.get("target_version") or "").strip()
        target_type = str(decision.get("target_type") or "").strip().lower()
        if target_type != "strategy_workshop":
            raise bff_error(
                409,
                ErrorCode.HUMAN_GATE_PENDING,
                "Approval decision target type is not a Strategy Workshop",
                "APPROVAL_TARGET_TYPE_MISMATCH",
                precondition_failed="approval_binding",
            )
        if target_id != workshop_id or target_version != version_id:
            raise bff_error(
                409,
                ErrorCode.HUMAN_GATE_PENDING,
                "Approval decision is not bound to this workshop version",
                "APPROVAL_TARGET_MISMATCH",
                precondition_failed="approval_binding",
            )
        requester = str(session.get("user_id") or "").strip()
        approver = _approval_actor(decision)
        if not requester or not approver or requester == approver:
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "A distinct approver is required",
                "APPROVAL_DISTINCT_ACTOR_REQUIRED",
                precondition_failed="distinct_approver",
            )
        return {
            "approval_decision_id": approval_decision_id,
            "requested_by": requester,
            "approved_by": approver,
            "distinct_actors": True,
            "decision": "approved",
        }

    def _readiness_from_store_or_state(session: Dict[str, Any]) -> Dict[str, Any]:
        latest = (
            store.get_latest_readiness_assessment(session["workshop_id"])
            if hasattr(store, "get_latest_readiness_assessment")
            else None
        )
        if latest is not None:
            return latest
        events = store.list_events(session["workshop_id"])
        snapshot = store.get_latest_completeness_snapshot(session["workshop_id"])
        return _build_readiness_assessment(
            session=session,
            events=events,
            snapshot=snapshot,
            assessed_at=utc_now(),
            assessment_version=1,
        )


    return SimpleNamespace(
        scope=_scope,
        scoped_session=_scoped_session,
        etag=_etag,
        admit_command=_admit_command,
        complete_or_raise=_complete_or_raise,
        require_approval=_require_approval,
        readiness_from_store_or_state=_readiness_from_store_or_state,
        require_command_headers=_require_command_headers,
        project_strategy_version=_project_strategy_version,
        read_strategy_version=_read_strategy_version,
        canonical_error=_canonical_error,
        fail_domain_command=_fail_domain_command,
        command_response=_command_response,
        resume_context=_resume_context,
        resolve_resumed_compensation=_resolve_resumed_compensation,
        source_resolution=_source_resolution,
        require_replayable_receipt=_require_replayable_receipt,
    )
