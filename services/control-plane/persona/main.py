"""
Control Plane — Persona Agent

Persona-owned control surface for:
  - intent classification (canonical path for router permission checks)
  - session metadata creation
  - OpenClaw agent-loop invocation when the governed runtime is available
  - truthful degraded-surrogate responses when the runtime is unavailable
"""
from __future__ import annotations

import os
import uuid
import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from integrations.openclaw.adapter import (
    OpenClawDockerGatewayRuntime,
    OpenClawGatewayConfig,
    OpenClawGatewayTransportError,
)
from persona_policy_resolver import (
    ConsultPolicy,
    PersonaPolicyResolver,
    RoutePolicy,
    ToolProfile,
)
from persona_registry import (
    CapabilitySnapshot,
    Persona,
    PersonaRegistry,
    PersonaSessionStore,
    SessionPersona,
    SessionStatus,
    SessionType,
    utc_now,
)
from persona_strategy_pool import (
    AppendPoolVersionRequest,
    PoolDecisionRequest,
    PersonaStrategyPoolStore,
    PoolError,
    build_pool_store,
)
from services.persona.write_owner import (
    _AUTHENTICATED_MUTATION_ROLES,
    PersonaAuthorityError,
    create_app as create_persona_owner_app,
    resolve_persona_tenant_scope,
)

_OWNER_READ_SURFACE = None


@asynccontextmanager
async def persona_lifespan(app):
    stop = asyncio.Event()
    worker = None
    if os.getenv("PERSONA_DAILY_CYCLE_ENABLED", "false").lower() == "true":
        from services.persona.persona_cycle_controller import CycleOwners, PersonaCycleController, run_controller
        from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts, PoolAutonomyCycle
        from services.persona.pool_dataset_authority import governed_dataset_loader
        from services.control_plane.cron.persona_cron_registrar import PersonaCronRegistrar
        from services.persona.pool_autonomy_cycle import governed_discovery_loader

        def _seed_store_reads(persona) -> list:
            # Background-safe owner read: no request is in flight, so the persona's tenant and the
            # persona service's own admitted source-ingest credential are bound for this read only.
            from services.control_plane.bff.core.owner_reads import authorization, selected_tenant
            from services.control_plane.bff.source_management_client import SourceManagementClient
            from services.service_token_file import configured_service_token
            client = SourceManagementClient()
            if not client.configured:
                raise RuntimeError("seed_store_unconfigured")
            token = configured_service_token("PERSONA_SOURCE_INGEST_SERVICE_TOKEN")
            if not token:
                raise RuntimeError("seed_store_credential_unconfigured")
            auth_mark = authorization.set("Bearer " + token)
            tenant_mark = selected_tenant.set(persona["tenant_id"])
            try:
                return client.list_all()
            finally:
                selected_tenant.reset(tenant_mark)
                authorization.reset(auth_mark)

        def _owner_surface_reads(persona, read):
            # Same owners the operator discovery route uses (bff/personas/service.py), bound to the
            # persona's tenant for this background read only.
            from services.control_plane.bff.core.owner_reads import selected_tenant
            global _OWNER_READ_SURFACE
            if _OWNER_READ_SURFACE is None:
                from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
                _OWNER_READ_SURFACE = ReadSurfacePorts()
            tenant_mark = selected_tenant.set(persona["tenant_id"])
            try:
                return read(_OWNER_READ_SURFACE, str(persona.get("persona_id") or persona.get("id") or ""))
            finally:
                selected_tenant.reset(tenant_mark)

        def _route_policy(persona):
            inline = persona.get("route_policy")
            if isinstance(inline, dict) and inline:
                return inline
            policy_id = str(persona.get("route_policy_id") or "").strip()
            if not policy_id:
                return {}
            # Resolve the exact referenced policy from the owner catalog by its own identity; the
            # persona-scoped lookup selects by persona_id and may return a stale, different policy.
            policies = _owner_surface_reads(persona, lambda surface, pid: surface.list_route_policies())
            for policy in policies or []:
                if isinstance(policy, dict) and policy and policy_id in {
                        str(policy.get("policy_id") or "").strip(), str(policy.get("id") or "").strip()} - {""}:
                    return policy
            # referenced policy unreadable/mismatched: never score without (or with other) constraints
            raise RuntimeError(f"route_policy_unavailable:{policy_id}")

        def _capability_snapshot(persona):
            inline = persona.get("capability_snapshot")
            if isinstance(inline, dict) and inline:
                return inline
            snapshot = _owner_surface_reads(persona, lambda surface, pid: surface.get_capability_snapshot_for_persona(pid))
            snapshot_id = str(persona.get("capability_snapshot_id") or "").strip()
            if not snapshot and snapshot_id:
                raise RuntimeError(f"capability_snapshot_unavailable:{snapshot_id}")
            return snapshot or {}

        # StrategySpec candidates come from the registry owner through the cycle ports (specs=None).
        discovery_loader = governed_discovery_loader(
            seeds=_seed_store_reads, route_policy=_route_policy, capability_snapshot=_capability_snapshot)
        controller = PersonaCycleController(
            PersonaCronRegistrar(), CycleOwners(PERSONA_OWNER_API.state.persona_owner, _pool_store()),
            pool_cycle=PoolAutonomyCycle(HttpPoolCyclePorts(
                pool_store=_pool_store(), dataset_loader=governed_dataset_loader(), discovery_loader=discovery_loader)))
        worker = asyncio.create_task(run_controller(controller, stop))
    try:
        yield
    finally:
        stop.set()
        if worker is not None:
            await worker


app = FastAPI(title="Pantheon Persona Agent", version="0.2.0", lifespan=persona_lifespan)

LLM_BACKEND = os.getenv("LLM_BACKEND", "openclaw")
DEFAULT_PERSONA_ID = os.getenv("PERSONA_ID", "persona-default")
OPENCLAW_AGENT_ID = os.getenv("OPENCLAW_AGENT_ID", "main")
OPENCLAW_AGENT_MODEL = os.getenv("OPENCLAW_AGENT_MODEL") or None
OPENCLAW_CONTAINER_NAME = os.getenv("OPENCLAW_CONTAINER_NAME", "pantheon-openclaw-gateway")
OPENCLAW_GATEWAY_HOST = os.getenv("OPENCLAW_GATEWAY_HOST", "127.0.0.1")
OPENCLAW_GATEWAY_PORT = int(os.getenv("OPENCLAW_GATEWAY_PORT", "18789"))
OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "pantheon-local-token")
OPENCLAW_GATEWAY_STATE_DIR = os.getenv("OPENCLAW_GATEWAY_STATE_DIR")
OPENCLAW_AGENT_TIMEOUT_SECONDS = int(os.getenv("OPENCLAW_AGENT_TIMEOUT_SECONDS", "30"))
OPENCLAW_HEALTH_TIMEOUT_SECONDS = float(os.getenv("OPENCLAW_HEALTH_TIMEOUT_SECONDS", "1.5"))


def _standard_health_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "persona-agent",
        "timestamp": utc_now(),
        "live": True,
        "ready": True,
        "dependencies": {
            "openclaw_gateway": {
                "status": "ok",
                "host": OPENCLAW_GATEWAY_HOST,
                "port": OPENCLAW_GATEWAY_PORT,
            }
        },
        "metrics": {"service_up": 1},
        "details": {
            "llm_backend": LLM_BACKEND,
            "runtime_backend": "openclaw-gateway",
            "persona_id": DEFAULT_PERSONA_ID,
            "agent_id": OPENCLAW_AGENT_ID,
        },
    }


@app.get("/healthz")
@app.get("/livez")
async def standard_health():
    return _standard_health_payload()


@app.get("/readyz")
async def standard_ready():
    return _standard_health_payload()


@app.get("/metrics")
async def standard_metrics():
    return {"service": "persona-agent", "timestamp": utc_now(), "metrics": {"service_up": 1}}

PERSONA_REGISTRY = PersonaRegistry()
SESSION_STORE = PersonaSessionStore()
CAPABILITY_SNAPSHOTS: dict[str, CapabilitySnapshot] = {}

# Default policy catalog for the control-plane persona agent.
# Entries here correspond to the policy IDs set on default personas.
_DEFAULT_POLICY_RESOLVER = PersonaPolicyResolver(
    tool_profiles={
        "persona-default": ToolProfile(
            profile_id="persona-default",
            allowed_tools=["web_search", "code_exec", "file_read"],
        ),
    },
    route_policies={
        "control-plane.default": RoutePolicy(
            policy_id="control-plane.default",
            allowed_workflows=["trade", "monitor", "governance"],
            allowed_skills=["status-summary", "monitoring-summary"],
        ),
    },
    consult_policies={
        "control-plane.default": ConsultPolicy(
            policy_id="control-plane.default",
            allowed_skills=["governance-review", "deployment-review", "research-summary"],
        ),
    },
)

_INTENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("execution.signal", ["buy", "sell", "trade", "order", "signal", "execute"]),
    ("governance.approve", ["approve", "promote", "rollback", "deploy live"]),
    ("governance", ["governance", "permission", "policy"]),
    ("deployment", ["deploy", "artifact", "release"]),
    ("monitor", ["pnl", "positions", "drawdown", "portfolio", "monitor"]),
    ("research", ["backtest", "qlib", "strategy", "simulate", "research"]),
    ("status", ["status", "tasks", "what is running"]),
]

_INTENT_SKILLS: dict[str, str | None] = {
    "execution.signal": "execution-signal",
    "governance.approve": "governance-review",
    "governance": "governance-review",
    "deployment": "deployment-review",
    "monitor": "monitoring-summary",
    "research": "research-summary",
    "status": "status-summary",
    "unknown": None,
}


class RuntimeStatus(BaseModel):
    backend: str = "openclaw-gateway"
    mode: str
    gateway_ready: bool
    reason: str | None = None
    error_code: str | None = None
    owner_plane: str | None = None


class ClassifyRequest(BaseModel):
    user_id: str
    channel: str
    message: str
    session_id: str | None = None


class ClassifyResponse(BaseModel):
    intent: str
    skill: str | None = None
    classifier: str
    persona_id: str
    runtime: RuntimeStatus


class InvokeRequest(BaseModel):
    session_id: str
    user_id: str
    channel: str
    message: str
    intent_hint: str | None = None


class InvokeResponse(BaseModel):
    response: str
    intent: str | None = None
    skill: str | None = None
    agent_id: str
    persona_id: str
    session_status: str
    runtime: RuntimeStatus


def _runtime_client() -> OpenClawDockerGatewayRuntime:
    return OpenClawDockerGatewayRuntime(
        OpenClawGatewayConfig(
            container_name=OPENCLAW_CONTAINER_NAME,
            host=OPENCLAW_GATEWAY_HOST,
            host_port=OPENCLAW_GATEWAY_PORT,
            gateway_token=OPENCLAW_GATEWAY_TOKEN,
            state_dir=OPENCLAW_GATEWAY_STATE_DIR,
        )
    )


def _ensure_default_persona() -> Persona:
    persona = PERSONA_REGISTRY.get(DEFAULT_PERSONA_ID)
    if persona is not None:
        return persona

    persona = Persona(
        persona_id=DEFAULT_PERSONA_ID,
        name="Pantheon Default Persona",
        mandate="Provide governed control-plane assistance through the OpenClaw runtime boundary.",
        lifecycle_state="consultable",
        created_at=utc_now(),
        status="active",
        tool_profile_id="persona-default",
        route_policy_id="control-plane.default",
        consult_policy_id="control-plane.default",
        metadata={"source": "services/control-plane/persona/main.py"},
    )
    return PERSONA_REGISTRY.create(persona)


def _classify_intent(message: str) -> str:
    lower = message.lower()
    for intent, keywords in _INTENT_KEYWORDS:
        if any(keyword in lower for keyword in keywords):
            return intent
    return "unknown"


def _select_skill(intent: str | None) -> str | None:
    if intent is None:
        return None
    return _INTENT_SKILLS.get(intent, _INTENT_SKILLS["unknown"])


def _build_capability_snapshot(persona: Persona, channel: str) -> CapabilitySnapshot:
    snapshot = _DEFAULT_POLICY_RESOLVER.resolve_to_snapshot(
        persona,
        snapshot_id=f"cap-{uuid.uuid4()}",
        generated_at=utc_now(),
    )
    # Augment source_refs with runtime-context refs that are not in the policy catalog.
    snapshot = CapabilitySnapshot(
        **{
            **snapshot.to_dict(),
            "source_refs": snapshot.source_refs + [
                "OPENCLAW_RUNTIME_CONTRACT.md",
                "PERSONA_RUNTIME_MODEL.md",
                f"channel:{channel}",
            ],
        }
    )
    CAPABILITY_SNAPSHOTS[snapshot.snapshot_id] = snapshot
    return snapshot


def _ensure_session(req: InvokeRequest, intent: str) -> SessionPersona:
    existing = SESSION_STORE.get(req.session_id)
    if existing is not None:
        return existing

    persona = _ensure_default_persona()
    snapshot = _build_capability_snapshot(persona, req.channel)
    session = SessionPersona(
        session_id=req.session_id,
        persona_id=persona.persona_id,
        session_type=SessionType.RESEARCH_TASK.value,
        status=SessionStatus.ACTIVE.value,
        started_at=utc_now(),
        capability_snapshot_id=snapshot.snapshot_id,
        trace_id=f"trace-{uuid.uuid4()}",
        request_id=f"req-{uuid.uuid4()}",
        context_bundle_ref=f"channel:{req.channel}",
        metadata={"channel": req.channel, "user_id": req.user_id},
    )
    return SESSION_STORE.create(session)


def _extract_text(payload: Any) -> str | None:
    if isinstance(payload, str):
        text = payload.strip()
        return text or None
    if isinstance(payload, list):
        parts = [part for item in payload if (part := _extract_text(item))]
        return "\n".join(parts) if parts else None
    if isinstance(payload, dict):
        for key in ("response", "text", "content", "message", "assistant", "reply", "final"):
            text = _extract_text(payload.get(key))
            if text:
                return text
        for key in ("result", "output", "payload", "data"):
            text = _extract_text(payload.get(key))
            if text:
                return text
        choices = payload.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                text = _extract_text(choice)
                if text:
                    return text
    return None


def _runtime_probe() -> RuntimeStatus:
    runtime = _runtime_client()
    try:
        runtime.wait_until_healthy(timeout_seconds=OPENCLAW_HEALTH_TIMEOUT_SECONDS)
        return RuntimeStatus(mode="gateway_ready_surrogate", gateway_ready=True)
    except OpenClawGatewayTransportError as exc:
        return RuntimeStatus(
            mode="degraded_surrogate",
            gateway_ready=False,
            reason=str(exc),
            error_code=exc.error_code,
            owner_plane=exc.owner_plane,
        )


def _build_degraded_response(intent: str, runtime_status: RuntimeStatus) -> str:
    reason = runtime_status.reason or "OpenClaw interactive runtime is unavailable."
    return (
        f"[persona runtime degraded] intent={intent}; "
        f"no governed tool execution was attempted. {reason}"
    )


def _invoke_openclaw(req: InvokeRequest) -> tuple[str, RuntimeStatus]:
    runtime = _runtime_client()
    try:
        runtime.wait_until_healthy(timeout_seconds=OPENCLAW_HEALTH_TIMEOUT_SECONDS)
        payload = runtime.agent_turn(
            agent_id=OPENCLAW_AGENT_ID,
            session_id=req.session_id,
            message=req.message,
            timeout_seconds=OPENCLAW_AGENT_TIMEOUT_SECONDS,
            model=OPENCLAW_AGENT_MODEL,
        )
        response_text = _extract_text(payload)
        if not response_text:
            response_text = "[openclaw returned no assistant text]"
        return response_text, RuntimeStatus(mode="openclaw", gateway_ready=True)
    except OpenClawGatewayTransportError as exc:
        runtime_status = RuntimeStatus(
            mode="degraded_surrogate",
            gateway_ready=exc.error_code not in {"UPSTREAM_UNAVAILABLE", "CONNECTION_REFUSED", "RUNTIME_UNAVAILABLE"},
            reason=str(exc),
            error_code=exc.error_code,
            owner_plane=exc.owner_plane,
        )
        return _build_degraded_response(req.intent_hint or "unknown", runtime_status), runtime_status


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "persona-agent",
        "llm_backend": LLM_BACKEND,
        "runtime_backend": "openclaw-gateway",
        "persona_id": DEFAULT_PERSONA_ID,
        "agent_id": OPENCLAW_AGENT_ID,
    }


@app.post("/classify", response_model=ClassifyResponse)
async def classify(req: ClassifyRequest):
    _ensure_default_persona()
    intent = _classify_intent(req.message)
    return ClassifyResponse(
        intent=intent,
        skill=_select_skill(intent),
        classifier="persona.local_surrogate",
        persona_id=DEFAULT_PERSONA_ID,
        runtime=_runtime_probe(),
    )


@app.post("/invoke", response_model=InvokeResponse)
async def invoke(req: InvokeRequest):
    intent = req.intent_hint or _classify_intent(req.message)
    skill = _select_skill(intent)
    _ensure_session(req, intent)

    response_text, runtime_status = _invoke_openclaw(req)
    if runtime_status.mode == "degraded_surrogate":
        SESSION_STORE.mark_degraded(req.session_id)
        session_status = SessionStatus.DEGRADED.value
    else:
        SESSION_STORE.activate(req.session_id)
        session_status = SessionStatus.ACTIVE.value

    return InvokeResponse(
        response=response_text,
        intent=intent,
        skill=skill,
        agent_id=OPENCLAW_AGENT_ID,
        persona_id=DEFAULT_PERSONA_ID,
        session_status=session_status,
        runtime=runtime_status,
    )


def _admitted_persona_tenant(authorization: str | None, tenant_id: str | None) -> str:
    try:
        return resolve_persona_tenant_scope(authorization, tenant_id)[1]
    except PersonaAuthorityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc


def _session_for_tenant(session_id: str, tenant_id: str):
    session = SESSION_STORE.get(session_id)
    raw = getattr(getattr(PERSONA_OWNER_API.state, "persona_owner", None), "_records", None)
    if not session or not raw or (raw.get(session.persona_id) or {}).get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.get("/api/sessions")
def list_sessions(
    persona_id: str | None = None, status: str | None = None, session_type: str | None = None,
    tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"), authorization: str | None = Header(default=None),
):
    admitted = _admitted_persona_tenant(authorization, tenant_id)
    try:
        raw = getattr(getattr(PERSONA_OWNER_API.state, "persona_owner", None), "_records", None) or {}
        return [s.to_dict() for s in SESSION_STORE.list(persona_id=persona_id, status=status, session_type=session_type) if (raw.get(s.persona_id) or {}).get("tenant_id") == admitted]
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Persona session owner unavailable") from exc


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"), authorization: str | None = Header(default=None)):
    return _session_for_tenant(session_id, _admitted_persona_tenant(authorization, tenant_id)).to_dict()


POOL_STORE: PersonaStrategyPoolStore | None = None


def _pool_store() -> PersonaStrategyPoolStore:
    global POOL_STORE
    if POOL_STORE is None:
        POOL_STORE = build_pool_store()
    return POOL_STORE


def _pool_scope(persona_id: str, authorization: str | None, tenant_id: str | None, *, write: bool):
    """Authenticate, admit the tenant, and require the persona to belong to it.

    A persona owned by another tenant is refused: 403 on write, 404 on read
    (so a read never confirms the persona exists elsewhere).
    """
    try:
        authority, admitted = resolve_persona_tenant_scope(
            authorization, tenant_id, *( (_AUTHENTICATED_MUTATION_ROLES,) if write else () )
        )
    except PersonaAuthorityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc
    raw = getattr(getattr(PERSONA_OWNER_API.state, "persona_owner", None), "_records", None)
    record = raw.get(persona_id) if raw is not None else None
    if record is None:
        raise HTTPException(status_code=404, detail="persona not found")
    if record.get("tenant_id") != admitted:
        raise HTTPException(
            status_code=403 if write else 404,
            detail="TENANT_SCOPE_DENIED: persona belongs to another tenant" if write else "persona not found",
        )
    return authority, admitted


def _pool_call(fn):
    try:
        return fn()
    except PoolError as exc:
        reason = getattr(exc, "reason", None)
        raise HTTPException(status_code=exc.status_code, detail=reason or str(exc)) from exc


@app.get("/api/personas/{persona_id}/strategy-pool")
def get_strategy_pool(
    persona_id: str,
    tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    authorization: str | None = Header(default=None),
):
    _, admitted = _pool_scope(persona_id, authorization, tenant_id, write=False)
    return _pool_call(lambda: _pool_store().current(persona_id, admitted))


@app.get("/api/personas/{persona_id}/strategy-pool/versions")
def list_strategy_pool_versions(
    persona_id: str,
    tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    authorization: str | None = Header(default=None),
):
    _, admitted = _pool_scope(persona_id, authorization, tenant_id, write=False)
    store = _pool_store()
    return _pool_call(
        lambda: {
            "versions": store.versions(persona_id, admitted),
            "refusals": store.refusals(persona_id, admitted),
        }
    )


@app.get("/api/personas/{persona_id}/strategy-pool/versions/{pool_version}")
def get_strategy_pool_version(
    persona_id: str,
    pool_version: int,
    tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    authorization: str | None = Header(default=None),
):
    _, admitted = _pool_scope(persona_id, authorization, tenant_id, write=False)
    return _pool_call(lambda: _pool_store().version(persona_id, admitted, pool_version))


@app.post("/api/personas/{persona_id}/strategy-pool/versions", status_code=201)
def append_strategy_pool_version(
    persona_id: str,
    body: AppendPoolVersionRequest,
    tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    authorization: str | None = Header(default=None),
):
    authority, admitted = _pool_scope(persona_id, authorization, tenant_id, write=True)
    return _pool_call(
        lambda: _pool_store().append(persona_id, admitted, body, actor_id=authority.actor_id)
    )


@app.post("/api/personas/{persona_id}/strategy-pool/seed", status_code=201)
def seed_strategy_pool(
    persona_id: str,
    tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    authorization: str | None = Header(default=None),
):
    """Create version 1 from the persona's approved paper artifact (owner records)."""
    authority, admitted = _pool_scope(persona_id, authorization, tenant_id, write=True)
    return _pool_call(
        lambda: _pool_store().seed_from_owner_records(persona_id, admitted, actor_id=authority.actor_id)
    )


@app.post("/api/personas/{persona_id}/strategy-pool/decisions", status_code=201)
def decide_strategy_pool(
    persona_id: str,
    body: PoolDecisionRequest,
    tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    authorization: str | None = Header(default=None),
):
    authority, admitted = _pool_scope(persona_id, authorization, tenant_id, write=True)
    return _pool_call(lambda: _pool_store().decide(persona_id, admitted, body, actor_id=authority.actor_id))


class PoolHealthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    books: dict[str, dict[str, Any]] = Field(default_factory=dict)
    observations: list[dict[str, Any]] = Field(default_factory=list)


@app.post("/api/personas/{persona_id}/strategy-pool/health")
def strategy_pool_health(
    persona_id: str,
    body: PoolHealthRequest,
    tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    authorization: str | None = Header(default=None),
):
    _, admitted = _pool_scope(persona_id, authorization, tenant_id, write=False)
    return _pool_call(lambda: _pool_store().health(persona_id, admitted, body.books, body.observations))


# The deployed control-plane Persona process owns the durable Registry and
# capability APIs.  The SESSION_STORE above remains process-local in-memory;
# authenticated reads expose only existing live state, not fresh-process history.
PERSONA_OWNER_API = create_persona_owner_app()
app.include_router(PERSONA_OWNER_API.router)
