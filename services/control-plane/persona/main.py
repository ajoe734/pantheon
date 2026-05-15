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
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from integrations.openclaw.adapter import (
    OpenClawDockerGatewayRuntime,
    OpenClawGatewayConfig,
    OpenClawGatewayTransportError,
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

app = FastAPI(title="Pantheon Persona Agent", version="0.2.0")

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


def _build_capability_snapshot(persona_id: str, intent: str, channel: str) -> CapabilitySnapshot:
    skill = _select_skill(intent)
    snapshot = CapabilitySnapshot(
        snapshot_id=f"cap-{uuid.uuid4()}",
        persona_id=persona_id,
        generated_at=utc_now(),
        effective_tools=[intent] if intent != "unknown" else [],
        effective_skills=[skill] if skill else [],
        restrictions=[],
        source_refs=[
            "OPENCLAW_RUNTIME_CONTRACT.md",
            "PERSONA_RUNTIME_MODEL.md",
            f"channel:{channel}",
        ],
    )
    CAPABILITY_SNAPSHOTS[snapshot.snapshot_id] = snapshot
    return snapshot


def _ensure_session(req: InvokeRequest, intent: str) -> SessionPersona:
    existing = SESSION_STORE.get(req.session_id)
    if existing is not None:
        return existing

    persona = _ensure_default_persona()
    snapshot = _build_capability_snapshot(persona.persona_id, intent, req.channel)
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
