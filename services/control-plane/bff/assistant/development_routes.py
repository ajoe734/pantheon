"""Development-tooling HTTP adapter mounted by the Pantheon BFF.

This module is deliberately removable. Product assistant routes do not import
it, and no product readiness check depends on it. The BFF composition root may
mount this router while engineering ingress is needed.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import ValidationError

from models import ErrorCode
from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError

from .command_idempotency import CommandIdempotencyStore
from .context_composer import AssistantContextPolicyError
from .control_mode import ControlModeStore, actor_capabilities
from .dev_bridge_inbox import queue_task_packet
from .dev_bridge_models import (
    BridgeActor,
    BridgeDocument,
    BridgeOperatorAuthorization,
    BridgeTask,
    DevTaskPacket,
)
from .dev_bridge_signer import sign_packet
from .dev_docs_archiver import archive_packet, infer_repo_root
from .dev_docs_generator import generate_dev_doc_packet
from .dev_docs_models import DevDocGenerateRequest, DevDocGenerateResponse, DevDocPacket
from .mode_policy import ModePolicyViolation, assert_kernel_allowed, turn_to_dict
from .models import AssistantContextPackRequest, AssistantMode
from .orchestrator_status import read_orchestrator_status
from .repair_receipts import RepairReceiptError, issue_repair_receipt
from .routes import (
    BffErrorFactory,
    BuildContextPack,
    ExtractIdentity,
    RequireReadRole,
    _assistant_command_idempotency,
    _operator_role_from_identity,
    _raise_error,
    _require_active_control_mode,
    _resolve_identity_tenant,
    _resolve_tenant_header,
)
from .transcript_store import InMemoryTranscriptStore


ProviderReadiness = Callable[[], Dict[str, Any]]
OpenClawToolPolicy = Callable[[], Dict[str, Any]]
OpenClawEffectiveTools = Callable[[str], Dict[str, Any]]
AuthorizeAssistantSkill = Callable[[str, Dict[str, Any], str, Optional[str]], Dict[str, Any]]
PrepareRepairWorktree = Callable[[Dict[str, Any], str, Optional[str]], Dict[str, Any]]

ASSISTANT_SA_SD_GENERATE_SKILL_ID = "assistant.sa_sd.generate"
CANONICAL_MUTATION_CAPABILITY = "assistant.canonical.mutate"

DEFAULT_DEV_DOC_CONTEXT_SOURCES = [
    "ui",
    "control_room",
    "jobs",
    "alerts",
    "audit",
    "recent_sse",
    "persona_health",
    "strategy_health",
    "management_nl",
    "docs_rag",
    "repo_status",
]


def create_development_router(
    *,
    build_context_pack: BuildContextPack,
    extract_identity: ExtractIdentity,
    require_read_role: RequireReadRole,
    bff_error: Optional[BffErrorFactory] = None,
    transcript_store: Optional[Any] = None,
    control_mode_store: Optional[ControlModeStore] = None,
    dev_docs_repo_root: Optional[str] = None,
    bridge_key_store: Optional[Dict[str, bytes]] = None,
    provider_readiness: Optional[ProviderReadiness] = None,
    openclaw_tool_policy: Optional[OpenClawToolPolicy] = None,
    openclaw_effective_tools: Optional[OpenClawEffectiveTools] = None,
    authorize_assistant_skill: Optional[AuthorizeAssistantSkill] = None,
    prepare_repair_worktree: Optional[PrepareRepairWorktree] = None,
) -> APIRouter:
    """Create the removable development-tooling route surface."""

    router = APIRouter(prefix="/bff/assistant", tags=["assistant-development"])
    current_transcript_store = (
        transcript_store if transcript_store is not None else InMemoryTranscriptStore()
    )
    current_control_mode_store = (
        control_mode_store if control_mode_store is not None else ControlModeStore()
    )
    command_idempotency_store = CommandIdempotencyStore()
    provider_readiness = provider_readiness or _development_provider_readiness
    openclaw_tool_policy = openclaw_tool_policy or _development_tool_policy
    openclaw_effective_tools = openclaw_effective_tools or _development_effective_tools
    authorize_assistant_skill = authorize_assistant_skill or (
        lambda skill_id, payload, operator_id, trace_id: _development_authorize_skill(
            skill_id,
            payload,
            operator_id,
            trace_id,
            bff_error=bff_error,
        )
    )
    prepare_repair_worktree = prepare_repair_worktree or (
        lambda payload, operator_id, trace_id: _development_prepare_repair_worktree(
            payload,
            operator_id,
            trace_id,
            bff_error=bff_error,
        )
    )

    @router.get("/orchestrator/status")
    async def get_orchestrator_status(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        operator_id = str(getattr(identity, "operator_id", "management-ai"))
        effective_tools = (
            (lambda: openclaw_effective_tools(operator_id))
            if openclaw_effective_tools
            else None
        )
        status = read_orchestrator_status(
            provider_readiness=provider_readiness,
            openclaw_tool_policy=openclaw_tool_policy,
            openclaw_effective_tools=effective_tools,
        )
        return {"data": status.model_dump(mode="json", by_alias=True)}

    @router.post("/repair-worktrees/prepare", status_code=201)
    async def prepare_assistant_repair_worktree(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        idempotency_recovery_id: Optional[str] = Header(
            default=None, alias="X-Idempotency-Recovery-Id"
        ),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        control_status = _require_active_control_mode(
            identity,
            current_control_mode_store,
            bff_error=bff_error,
        )
        _require_kernel_repair_control(control_status, bff_error=bff_error)
        if prepare_repair_worktree is None:
            _raise_error(
                bff_error,
                503,
                ErrorCode.PRECONDITION_FAILED,
                "Assistant repair worktree preparation is not configured",
                "OpenClaw adapter repair-worktree preparation is not configured for this BFF.",
                field="openclaw_adapter",
            )

        request_payload = _prepare_repair_worktree_payload(
            payload,
            identity,
            control_status=control_status,
            bff_error=bff_error,
        )
        trace_id = str(payload.get("traceId") or payload.get("trace_id") or "").strip() or None
        actor_id = str(getattr(identity, "operator_id", None) or "management-ai")
        tenant_id = _resolve_identity_tenant(
            identity,
            requested_tenant=_resolve_tenant_header(
                x_tenant_id,
                x_pantheon_tenant,
                bff_error=bff_error,
            ),
            bff_error=bff_error,
        )
        activation_id = control_status.get("activation_id") or control_status.get("activationId")
        with _assistant_command_idempotency(
            command_idempotency_store,
            actor_id=actor_id,
            route="/bff/assistant/repair-worktrees/prepare",
            payload={
                "payload": payload,
                "tenant_id": tenant_id,
                "control_activation_id": activation_id,
            },
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            recovery_id=idempotency_recovery_id,
            bff_error=bff_error,
        ) as transaction:
            if transaction is not None and transaction.replayed:
                return transaction.response or {}
            prepared = prepare_repair_worktree(request_payload, actor_id, trace_id)
            if isinstance(prepared, dict) and isinstance(prepared.get("data"), dict):
                prepared_data = dict(prepared["data"])
                raw_repair = prepared_data.get("repair")
                if not isinstance(raw_repair, dict):
                    _raise_error(
                        bff_error,
                        502,
                        ErrorCode.PRECONDITION_FAILED,
                        "OpenClaw adapter did not return prepared repair metadata",
                        "The repair-worktree response cannot be authorized without canonical repair metadata.",
                        field="openclaw_adapter",
                        reason="repair_metadata_missing",
                    )
                repair = dict(raw_repair)
                try:
                    repair["receipt"] = issue_repair_receipt(
                        repair,
                        actor_id=actor_id,
                        tenant_id=tenant_id,
                        control_status=control_status,
                    )
                except RepairReceiptError as exc:
                    _raise_error(
                        bff_error,
                        503,
                        ErrorCode.PRECONDITION_FAILED,
                        "Assistant repair receipt could not be issued",
                        str(exc),
                        field="repair_receipt",
                        reason=exc.reason,
                    )
                prepared_data["repair"] = repair
                response = {
                    "data": prepared_data,
                    "meta": {
                        "openclawAdapterStatus": prepared.get("status"),
                        "openclaw_adapter_status": prepared.get("status"),
                    },
                }
            else:
                response = {"data": prepared}
            if transaction is not None:
                transaction.complete(response)
            return response

    @router.post("/dev-docs/generate", status_code=201)
    async def generate_assistant_dev_docs(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        idempotency_recovery_id: Optional[str] = Header(
            default=None, alias="X-Idempotency-Recovery-Id"
        ),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        control_status = _require_active_control_mode(
            identity,
            current_control_mode_store,
            bff_error=bff_error,
        )
        request = _parse_dev_doc_request(payload, bff_error=bff_error)
        trace_id = str(payload.get("traceId") or payload.get("trace_id") or "").strip() or None
        actor_id = str(getattr(identity, "operator_id", None) or "management-ai")
        activation_id = control_status.get("activation_id") or control_status.get("activationId")
        tenant_id = _resolve_identity_tenant(
            identity,
            requested_tenant=_resolve_tenant_header(
                x_tenant_id,
                x_pantheon_tenant,
                bff_error=bff_error,
            ),
            bff_error=bff_error,
        )
        with _assistant_command_idempotency(
            command_idempotency_store,
            actor_id=actor_id,
            route="/bff/assistant/dev-docs/generate",
            payload={
                "payload": payload,
                "control_activation_id": activation_id,
                "tenant_id": tenant_id,
            },
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            recovery_id=idempotency_recovery_id,
            bff_error=bff_error,
        ) as transaction:
            if transaction is not None and transaction.replayed:
                return transaction.response or {}
            _require_assistant_skill_authorized(
                skill_id=ASSISTANT_SA_SD_GENERATE_SKILL_ID,
                authorize_assistant_skill=authorize_assistant_skill,
                identity=identity,
                control_status=control_status,
                session_id=request.conversation_id,
                trace_id=trace_id,
                request_type="assistant_dev_docs_generate",
                audit_extra={
                    "archive": request.archive,
                    "queue_task_packet": _should_queue_task_packet(payload),
                    "affected_module_count": len(request.affected_modules),
                },
                bff_error=bff_error,
            )
            turns = _conversation_turns_for_request(current_transcript_store, request)
            context_pack = _context_pack_for_dev_docs(
                payload,
                request,
                identity,
                control_status=control_status,
                build_context_pack=build_context_pack,
                bff_error=bff_error,
            )
            packet = generate_dev_doc_packet(
                request=request,
                turns=turns,
                context_pack=context_pack,
            )

            archive_locations = None
            if request.archive:
                archive_locations = archive_packet(
                    packet,
                    repo_root=_dev_docs_repo_root(dev_docs_repo_root),
                )
                packet = packet.model_copy(update={"archive_locations": archive_locations})
            meta: Dict[str, Any] = {
                "archived": archive_locations is not None,
                "archiveLocations": archive_locations.model_dump(mode="json", by_alias=True)
                if archive_locations is not None
                else None,
                "devBridge": _dev_bridge_meta(),
            }

            task_packet: Optional[DevTaskPacket] = None
            should_emit_task_packet = _should_emit_task_packet(payload)
            should_queue_task_packet = _should_queue_task_packet(payload)
            if should_emit_task_packet or should_queue_task_packet:
                _require_canonical_mutation_capability(identity, bff_error=bff_error)
                task_packet = _signed_dev_task_packet(
                    packet,
                    identity,
                    control_activation_id=str(activation_id or ""),
                    mode=str(control_status.get("mode") or AssistantMode.KERNEL_DEBUG.value),
                    key_store=bridge_key_store,
                )
            if task_packet is not None and should_emit_task_packet:
                meta["taskPacket"] = task_packet.model_dump(mode="json", by_alias=True)
            if task_packet is not None and should_queue_task_packet:
                queue_receipt = queue_task_packet(
                    task_packet,
                    repo_root=_dev_bridge_queue_repo_root(dev_docs_repo_root),
                    key_store=bridge_key_store,
                    source="bff_assistant_dev_docs_generate",
                )
                meta["taskPacketQueued"] = bool(queue_receipt.get("queued"))
                meta["taskPacketQueueReceipt"] = queue_receipt
                if "taskPacket" not in meta:
                    meta["taskPacket"] = task_packet.model_dump(mode="json", by_alias=True)
            response = DevDocGenerateResponse(data=packet, meta=meta).model_dump(
                mode="json", by_alias=True
            )
            if transaction is not None:
                transaction.complete(response)
            return response

    @router.get("/dev-docs/{packet_id}")
    async def get_assistant_dev_doc_packet(
        packet_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        _require_active_control_mode(
            identity,
            current_control_mode_store,
            bff_error=bff_error,
        )
        packet = _load_archived_dev_doc_packet(
            packet_id,
            repo_root=_dev_docs_repo_root(dev_docs_repo_root),
            bff_error=bff_error,
        )
        return {"data": packet.model_dump(mode="json", by_alias=True)}

    @router.post("/dev-bridge/task-packet", status_code=201)
    async def create_assistant_dev_task_packet(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        idempotency_recovery_id: Optional[str] = Header(
            default=None, alias="X-Idempotency-Recovery-Id"
        ),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        control_status = _require_active_control_mode(
            identity,
            current_control_mode_store,
            bff_error=bff_error,
        )
        _require_canonical_mutation_capability(identity, bff_error=bff_error)
        actor_id = str(getattr(identity, "operator_id", None) or "management-ai")
        activation_id = control_status.get("activation_id") or control_status.get("activationId")
        tenant_id = _resolve_identity_tenant(
            identity,
            requested_tenant=_resolve_tenant_header(
                x_tenant_id,
                x_pantheon_tenant,
                bff_error=bff_error,
            ),
            bff_error=bff_error,
        )
        with _assistant_command_idempotency(
            command_idempotency_store,
            actor_id=actor_id,
            route="/bff/assistant/dev-bridge/task-packet",
            payload={
                "payload": payload,
                "control_activation_id": activation_id,
                "tenant_id": tenant_id,
            },
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            recovery_id=idempotency_recovery_id,
            bff_error=bff_error,
        ) as transaction:
            if transaction is not None and transaction.replayed:
                return transaction.response or {}
            packet = _parse_dev_doc_packet(payload, bff_error=bff_error)
            task_packet = _signed_dev_task_packet(
                packet,
                identity,
                control_activation_id=str(activation_id or ""),
                mode=str(
                    payload.get("mode")
                    or control_status.get("mode")
                    or AssistantMode.KERNEL_DEBUG.value
                ),
                key_store=bridge_key_store,
            )
            meta = _dev_bridge_meta()
            if _should_queue_task_packet(payload):
                queue_receipt = queue_task_packet(
                    task_packet,
                    repo_root=_dev_bridge_queue_repo_root(dev_docs_repo_root),
                    key_store=bridge_key_store,
                    source="bff_assistant_dev_bridge_route",
                )
                meta["taskPacketQueued"] = bool(queue_receipt.get("queued"))
                meta["taskPacketQueueReceipt"] = queue_receipt
            response = {
                "data": task_packet.model_dump(mode="json", by_alias=True),
                "meta": meta,
            }
            if transaction is not None:
                transaction.complete(response)
            return response

    return router


def _development_provider_readiness() -> Dict[str, Any]:
    provider = str(os.environ.get("PANTHEON_ASSISTANT_PROVIDER") or "openclaw").strip()
    try:
        return OpenClawOpsClient().get_assistant_readiness(provider=provider)
    except OpenClawOpsClientError as exc:
        return {
            "provider": provider,
            "runtime": "openclaw_gateway_cli_mount",
            "ready": False,
            "status": "unavailable",
            "reason": exc.error_code,
            "message": exc.message,
            "httpStatus": exc.status_code,
        }


def _development_tool_policy() -> Dict[str, Any]:
    try:
        return OpenClawOpsClient().get_tool_policy()
    except OpenClawOpsClientError as exc:
        return {
            "status": "unavailable",
            "reason": exc.error_code,
            "message": exc.message,
            "httpStatus": exc.status_code,
        }


def _development_effective_tools(operator_id: str) -> Dict[str, Any]:
    try:
        return OpenClawOpsClient().list_effective_tools(
            agent_id="management-ai",
            operator_id=operator_id or "management-ai",
            mode="kernel_debug",
            operator_role="operator",
        )
    except OpenClawOpsClientError as exc:
        return {
            "status": "unavailable",
            "reason": exc.error_code,
            "message": exc.message,
            "httpStatus": exc.status_code,
        }


def _development_authorize_skill(
    skill_id: str,
    payload: Dict[str, Any],
    operator_id: str,
    trace_id: Optional[str],
    *,
    bff_error: Optional[BffErrorFactory],
) -> Dict[str, Any]:
    try:
        return OpenClawOpsClient().authorize_assistant_skill(
            skill_id=skill_id,
            operator_id=operator_id or "management-ai",
            mode=payload.get("mode"),
            operator_role=payload.get("operator_role") or payload.get("operatorRole"),
            confirmed=bool(payload.get("confirmed")),
            confirm_token=payload.get("confirm_token") or payload.get("confirmToken"),
            control_mode=payload.get("control_mode") or payload.get("controlMode"),
            session_id=payload.get("session_id") or payload.get("sessionId"),
            request_type=payload.get("request_type")
            or payload.get("requestType")
            or "assistant_skill_authorize",
            audit_extra=payload.get("audit_extra") or payload.get("auditExtra"),
            trace_id=trace_id,
        )
    except OpenClawOpsClientError as exc:
        _raise_development_dependency_error(exc, bff_error=bff_error)
    raise AssertionError("unreachable")


def _development_prepare_repair_worktree(
    payload: Dict[str, Any],
    operator_id: str,
    trace_id: Optional[str],
    *,
    bff_error: Optional[BffErrorFactory],
) -> Dict[str, Any]:
    try:
        return OpenClawOpsClient().prepare_assistant_repair_worktree(
            payload=payload,
            operator_id=operator_id or "management-ai",
            trace_id=trace_id,
        )
    except OpenClawOpsClientError as exc:
        _raise_development_dependency_error(exc, bff_error=bff_error)
    raise AssertionError("unreachable")


def _raise_development_dependency_error(
    exc: OpenClawOpsClientError,
    *,
    bff_error: Optional[BffErrorFactory],
) -> None:
    status_code = exc.status_code or 502
    if status_code == 404:
        code = ErrorCode.RESOURCE_NOT_FOUND
    elif status_code == 409:
        code = ErrorCode.RESOURCE_CONFLICT
    elif status_code == 403:
        code = ErrorCode.PRECONDITION_FAILED
    elif status_code >= 500:
        code = ErrorCode.DEPENDENCY_UNAVAILABLE
    else:
        code = ErrorCode.VALIDATION_FAILED
    _raise_error(
        bff_error,
        status_code,
        code,
        exc.message,
        exc.error_code,
        precondition_failed="openclaw_adapter",
        suggestion="Inspect GET /api/v1/operator/openclaw/ops for current adapter degradation state",
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _should_emit_task_packet(payload: dict) -> bool:
    return _truthy(
        payload.get(
            "emitTaskPacket",
            payload.get(
                "emit_task_packet",
                payload.get("signTaskPacket", payload.get("sign_task_packet")),
            ),
        )
    )


def _should_queue_task_packet(payload: dict) -> bool:
    return _truthy(
        payload.get(
            "queueTaskPacket",
            payload.get(
                "queue_task_packet",
                payload.get("queueDevTaskPacket", payload.get("queue_dev_task_packet")),
            ),
        )
    )


def _dev_docs_repo_root(configured_root: Optional[str]) -> str:
    return str(configured_root) if configured_root else infer_repo_root()


def _dev_bridge_queue_repo_root(configured_root: Optional[str]) -> Optional[str]:
    status_root = str(os.environ.get("PANTHEON_STATUS_ROOT") or "").strip()
    if status_root and Path(status_root, "ai-status.json").exists():
        return status_root
    return str(configured_root) if configured_root else None


def _require_canonical_mutation_capability(
    identity: Any, *, bff_error: Optional[BffErrorFactory]
) -> None:
    if CANONICAL_MUTATION_CAPABILITY in set(actor_capabilities(identity)):
        return
    _raise_error(
        bff_error,
        403,
        ErrorCode.FORBIDDEN,
        "Canonical task mutation capability is required",
        "The authenticated operator lacks assistant.canonical.mutate",
        field="capability",
    )


def _require_assistant_skill_authorized(
    *,
    skill_id: str,
    authorize_assistant_skill: Optional[AuthorizeAssistantSkill],
    identity: Any,
    control_status: Dict[str, Any],
    session_id: Optional[str],
    trace_id: Optional[str],
    request_type: str,
    audit_extra: Optional[Dict[str, Any]],
    bff_error: Optional[BffErrorFactory],
) -> Dict[str, Any]:
    if authorize_assistant_skill is None:
        _raise_error(
            bff_error,
            503,
            ErrorCode.PRECONDITION_FAILED,
            "OpenClaw assistant skill authorization is not configured",
            "Assistant dev-doc generation must be authorized through the OpenClaw skill catalog.",
            field="openclaw_skill_authorizer",
            required_skill=skill_id,
        )
    actor_id = str(getattr(identity, "operator_id", None) or "management-ai")
    mode = str(control_status.get("mode") or "")
    activation_id = control_status.get("activation_id") or control_status.get("activationId")
    payload = {
        "mode": mode,
        "operator_role": _operator_role_from_identity(identity),
        "confirmed": False,
        "control_mode": {
            "active": True,
            "mode": mode,
            "activation_id": activation_id,
        },
        "session_id": session_id,
        "request_type": request_type,
        "audit_extra": audit_extra or {},
    }
    try:
        result = authorize_assistant_skill(skill_id, payload, actor_id, trace_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed around the policy boundary.
        _raise_error(
            bff_error,
            502,
            ErrorCode.PRECONDITION_FAILED,
            "OpenClaw assistant skill authorization failed",
            str(exc),
            field="openclaw_skill_authorizer",
            required_skill=skill_id,
        )
    data = result.get("data") if isinstance(result, dict) else None
    status = (
        str(data.get("status") or "")
        if isinstance(data, dict)
        else str(result.get("status") or "")
        if isinstance(result, dict)
        else ""
    )
    if status not in {"allowed", "ok"}:
        _raise_error(
            bff_error,
            403,
            ErrorCode.FORBIDDEN,
            "OpenClaw assistant skill authorization denied",
            "The OpenClaw skill catalog did not authorize this assistant action.",
            field="openclaw_skill_authorizer",
            required_skill=skill_id,
            policy_status=status or "unknown",
        )
    return result


def _require_kernel_repair_control(
    control_status: Dict[str, Any],
    *,
    bff_error: Optional[BffErrorFactory],
) -> None:
    mode = str(control_status.get("mode") or "")
    capabilities = {str(value) for value in (control_status.get("capabilities") or [])}
    if mode != AssistantMode.KERNEL_REPAIR.value:
        _raise_error(
            bff_error,
            409,
            ErrorCode.PRECONDITION_FAILED,
            "Assistant repair worktree preparation requires active kernel_repair control mode",
            "Activate assistant control mode in kernel_repair before preparing a repair worktree",
            field="control_mode",
            reason="kernel_repair_required",
            mode=mode or None,
        )
    if "assistant.kernel.repair" not in capabilities:
        _raise_error(
            bff_error,
            403,
            ErrorCode.FORBIDDEN,
            "Assistant repair worktree preparation requires assistant.kernel.repair capability",
            "The active control-mode activation does not include assistant.kernel.repair",
            field="capabilities",
            required_capability="assistant.kernel.repair",
        )


def _prepare_repair_worktree_payload(
    payload: Dict[str, Any],
    identity: Any,
    *,
    control_status: Dict[str, Any],
    bff_error: Optional[BffErrorFactory],
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "Repair worktree preparation payload must be an object",
            field="payload",
        )
    declared_scope = _declared_scope_from_payload(payload, bff_error=bff_error)
    task_id = str(payload.get("taskId") or payload.get("task_id") or "").strip()
    if not task_id:
        task_id = f"MGMT-AI-REPAIR-{_utc_now_z().replace('-', '').replace(':', '')}-{uuid.uuid4().hex[:8]}"
    request_payload: Dict[str, Any] = {
        "task_id": task_id,
        "taskId": task_id,
        "declared_scope": declared_scope,
        "declaredScope": declared_scope,
        "operator_id": str(getattr(identity, "operator_id", None) or "management-ai"),
        "control_mode": {
            "activationId": control_status.get("activationId") or control_status.get("activation_id"),
            "mode": control_status.get("mode"),
        },
    }
    repo_key = str(payload.get("repoKey") or payload.get("repo_key") or payload.get("repository") or "").strip()
    if repo_key:
        request_payload.update(repo_key=repo_key, repoKey=repo_key, repository=repo_key)
    for camel, snake in (
        ("taskWorktree", "task_worktree"),
        ("expectedBranch", "expected_branch"),
        ("mergeTarget", "merge_target"),
        ("traceId", "trace_id"),
    ):
        value = payload.get(camel, payload.get(snake))
        if value not in (None, ""):
            request_payload[snake] = value
            request_payload[camel] = value
    if payload.get("remote") not in (None, ""):
        request_payload["remote"] = payload["remote"]
    reason = str(payload.get("reason") or control_status.get("reason") or "").strip()
    if reason:
        request_payload["reason"] = reason
    return request_payload


def _declared_scope_from_payload(
    payload: Dict[str, Any],
    *,
    bff_error: Optional[BffErrorFactory],
) -> List[str]:
    raw = payload.get("declaredScope", payload.get("declared_scope"))
    if isinstance(raw, str):
        values = [item.strip() for item in re.split(r"[,\n]", raw) if item.strip()]
    elif isinstance(raw, list):
        values = [str(item).strip() for item in raw if str(item).strip()]
    else:
        values = []
    if not values:
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "Repair worktree preparation requires declaredScope",
            "declaredScope must contain one or more repo-relative paths",
            field="declaredScope",
        )
    invalid_scope = any(
        value == "."
        or value.startswith("/")
        or value.startswith("../")
        or "/../" in value
        or value.endswith("/..")
        or "\\" in value
        or ".git" in value.split("/")
        for value in values
    )
    if invalid_scope:
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "Repair declaredScope entries must be repo-relative paths",
            "declaredScope cannot include absolute paths, '.', '..', '.git', or parent traversal",
            field="declaredScope",
        )
    return values


def _parse_dev_doc_request(
    payload: dict,
    *,
    bff_error: Optional[BffErrorFactory],
) -> DevDocGenerateRequest:
    try:
        return DevDocGenerateRequest(**payload)
    except ValidationError as exc:
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid assistant dev docs request",
            str(exc),
            field="payload",
            errors=exc.errors(),
        )
    raise AssertionError("unreachable")


def _parse_dev_doc_packet(
    payload: dict,
    *,
    bff_error: Optional[BffErrorFactory],
) -> DevDocPacket:
    raw_packet = (
        payload.get("devDocPacket")
        or payload.get("dev_doc_packet")
        or payload.get("packet")
        or payload
    )
    if isinstance(raw_packet, dict) and isinstance(raw_packet.get("data"), dict):
        raw_packet = raw_packet["data"]
    if not isinstance(raw_packet, dict):
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "devDocPacket must be an object",
            field="devDocPacket",
        )
    try:
        return DevDocPacket(**raw_packet)
    except ValidationError as exc:
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid assistant dev doc packet",
            str(exc),
            field="devDocPacket",
            errors=exc.errors(),
        )
    raise AssertionError("unreachable")


def _conversation_turns_for_request(
    transcript_store: Any,
    request: DevDocGenerateRequest,
) -> List[Dict[str, Any]]:
    turns = [turn_to_dict(turn) for turn in transcript_store.list_turns(request.conversation_id)]
    if not request.turn_ids:
        return turns
    wanted = {str(turn_id) for turn_id in request.turn_ids}
    return [turn for turn in turns if str(turn.get("turn_id")) in wanted]


def _context_pack_for_dev_docs(
    payload: dict,
    request: DevDocGenerateRequest,
    identity: Any,
    *,
    control_status: Dict[str, Any],
    build_context_pack: BuildContextPack,
    bff_error: Optional[BffErrorFactory],
) -> Any:
    explicit_pack = payload.get("contextPack", payload.get("context_pack"))
    if explicit_pack is not None:
        return explicit_pack
    raw_request = payload.get("contextPackRequest", payload.get("context_pack_request"))
    if raw_request is None:
        raw_request = {
            "mode": payload.get("mode") or control_status.get("mode") or AssistantMode.KERNEL_DEBUG.value,
            "include": payload.get("include") or DEFAULT_DEV_DOC_CONTEXT_SOURCES,
            "question": request.feature_summary,
        }
    if not isinstance(raw_request, dict):
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "contextPackRequest must be an object",
            field="contextPackRequest",
        )
    try:
        context_request = AssistantContextPackRequest(**raw_request)
    except ValidationError as exc:
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid assistant context pack request",
            str(exc),
            field="contextPackRequest",
            errors=exc.errors(),
        )
        raise AssertionError("unreachable")
    try:
        assert_kernel_allowed(context_request.mode)
    except ModePolicyViolation as exc:
        _raise_error(
            bff_error,
            403,
            ErrorCode.FORBIDDEN,
            f"Mode policy violation: {exc}",
            str(exc),
            field=exc.field,
        )
    try:
        return build_context_pack(request.conversation_id, context_request, identity)
    except AssistantContextPolicyError as exc:
        _raise_error(
            bff_error,
            403,
            ErrorCode.FORBIDDEN,
            "Assistant context source is not allowed for this mode",
            str(exc),
            precondition_failed="assistant_context_mode_policy",
            denied_sources=exc.denied_sources,
        )
    raise AssertionError("unreachable")


def _load_archived_dev_doc_packet(
    packet_id: str,
    *,
    repo_root: str,
    bff_error: Optional[BffErrorFactory],
) -> DevDocPacket:
    clean_packet_id = str(packet_id or "").strip()
    if not clean_packet_id or any(
        not (character.isalnum() or character in {"_", "-"})
        for character in clean_packet_id
    ):
        _raise_error(
            bff_error,
            400,
            ErrorCode.VALIDATION_FAILED,
            "Invalid packet_id",
            field="packet_id",
        )
    matches = sorted(
        Path(repo_root).glob(f"docs/04/sa_sd_{clean_packet_id}_*/dev_doc_packet.json")
    )
    if not matches:
        _raise_error(
            bff_error,
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"Archived dev doc packet not found: {clean_packet_id!r}",
            field="packet_id",
        )
    try:
        return DevDocPacket(**json.loads(matches[0].read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        _raise_error(
            bff_error,
            500,
            ErrorCode.INTERNAL_ERROR,
            f"Archived dev doc packet is unreadable: {clean_packet_id!r}",
            str(exc),
            field="packet_id",
        )
    raise AssertionError("unreachable")


def _signed_dev_task_packet(
    packet: DevDocPacket,
    identity: Any,
    *,
    mode: str,
    control_activation_id: str,
    key_store: Optional[Dict[str, bytes]],
) -> DevTaskPacket:
    now = datetime.now(timezone.utc)
    task_packet = DevTaskPacket(
        packetId=f"bridge_{packet.packet_id}",
        emittedAt=_utc_now_z(),
        actor=BridgeActor(
            id=str(packet.actor_id or "management-ai"),
            roles=["source"],
            capabilities=["assistant.dev.source"],
        ),
        operatorAuthorization=BridgeOperatorAuthorization(
            operatorId=str(getattr(identity, "operator_id", None) or ""),
            controlActivationId=control_activation_id,
            capability=CANONICAL_MUTATION_CAPABILITY,
            mfaVerified=bool(getattr(identity, "mfa_verified", False)),
            issuedAt=now.isoformat().replace("+00:00", "Z"),
            expiresAt=(now + timedelta(seconds=300)).isoformat().replace("+00:00", "Z"),
            nonce=uuid.uuid4().hex,
        ),
        mode=mode,
        sourceConversationId=packet.conversation_id,
        sourceTurnIds=[
            ref.turn_id for ref in (packet.requirement_capture.source_turn_refs or [])
        ],
        documents=_bridge_documents(packet),
        tasks=_bridge_tasks(packet),
        auditConversationHref=f"/bff/assistant/sessions/{packet.conversation_id}/transcript",
    )
    return sign_packet(task_packet, key_store=key_store)


def _bridge_documents(packet: DevDocPacket) -> List[BridgeDocument]:
    locations = packet.archive_locations
    documents: List[BridgeDocument] = []
    if locations is not None:
        doc_specs = [
            (locations.requirement_capture, "REQUIREMENT_CAPTURE", packet.requirement_capture.source_refs),
            (locations.system_analysis, "SYSTEM_ANALYSIS", packet.system_analysis.source_refs),
            (locations.system_design, "SYSTEM_DESIGN", packet.system_design.source_refs),
        ]
        for path, kind, refs in doc_specs:
            if path:
                documents.append(
                    BridgeDocument(path=path, kind=kind, sourceRefs=_source_ref_ids(refs))
                )
        for path in locations.task_briefs or []:
            documents.append(
                BridgeDocument(
                    path=path,
                    kind="TASK_BRIEF",
                    sourceRefs=_source_ref_ids(packet.source_refs),
                )
            )
        for path in locations.architecture_docs or []:
            documents.append(
                BridgeDocument(
                    path=path,
                    kind="ARCHITECTURE_NOTE",
                    sourceRefs=_source_ref_ids(packet.system_design.source_refs or packet.source_refs),
                )
            )
        for path in locations.ui_docs or []:
            documents.append(
                BridgeDocument(
                    path=path,
                    kind="UI_FLOW_NOTE",
                    sourceRefs=_source_ref_ids(packet.system_design.source_refs or packet.source_refs),
                )
            )
        return documents

    seen_paths = set()
    for task in packet.execution_tasks:
        for path in task.artifacts or []:
            clean_path = str(path or "").strip()
            if not clean_path or clean_path in seen_paths:
                continue
            if clean_path.startswith("docs/") or clean_path.startswith(".orchestrator/task-briefs/"):
                seen_paths.add(clean_path)
                documents.append(
                    BridgeDocument(
                        path=clean_path,
                        kind="PLANNED_ARTIFACT",
                        sourceRefs=_source_ref_ids(task.source_refs or packet.source_refs),
                    )
                )
    return documents


def _bridge_tasks(packet: DevDocPacket) -> List[BridgeTask]:
    return [
        BridgeTask(
            id=task.task_id,
            title=task.title,
            owner=task.owner,
            reviewer=task.reviewer,
            phase=task.phase,
            dependsOn=list(task.depends_on or []),
            artifacts=list(task.artifacts or []),
            acceptance=list(task.acceptance or []),
            summary=task.summary,
        )
        for task in packet.execution_tasks
    ]


def _source_ref_ids(refs: List[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for ref in refs or []:
        source_id = (
            ref.get("source_id") or ref.get("sourceId")
            if isinstance(ref, dict)
            else getattr(ref, "source_id", None)
        )
        clean = str(source_id or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _dev_bridge_meta() -> Dict[str, Any]:
    return {
        "dispatchMode": "repo_local_required",
        "handoffMode": "repo_local_supervisor_inbox",
        "noDirectShellFromWeb": True,
        "dispatcher": "assistant.dev_bridge_dispatcher.dispatch_task_packet",
        "queueCommand": "python3 scripts/queue_assistant_dev_task_packet.py",
        "queueEndpoint": "/bff/assistant/dev-bridge/task-packet",
        "queueTaskPacketField": "queueTaskPacket",
        "drainCommand": "python3 scripts/drain_assistant_dev_task_packet_inbox.py",
        "supervisorInboxPath": ".orchestrator/assistant-dev-packets",
        "orchestratorStatusHref": "/bff/assistant/orchestrator/status",
    }


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
