"""
Control Plane — Router  (P4-001)
POST /route { channel, user_id, message, session_id? }
     → { session_id, agent_id, response, intent, skill, permission }

Contract:    services/control-plane/router/contract.md
Permissions: services/control-plane/permissions/contract.md (OC-001)

Session TTL policy: 24h idle (SESSION_TTL_SECONDS).
  Runtime enforcement deferred to API gateway / session backend.
  TODO: add session store + TTL check once session backend is chosen.

Rate limits: defined in RATE_LIMITS constants.
  Runtime enforcement deferred to API gateway in production.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from enum import Enum

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)

app = FastAPI(title="Pantheon Router", version="0.3.0")

PERSONA_URL = os.getenv("PERSONA_URL", "http://localhost:8002")

# ---------------------------------------------------------------------------
# Policy constants (locked for v1 — must not be changed without governance review)
# ---------------------------------------------------------------------------

SESSION_TTL_SECONDS = 86_400  # 24h idle timeout; refreshed on each successful /route call


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _standard_health_payload() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "router",
        "timestamp": _utc_now(),
        "live": True,
        "ready": True,
        "dependencies": {"persona": {"status": "ok" if PERSONA_URL else "degraded", "url": PERSONA_URL}},
        "metrics": {"service_up": 1},
        "details": {
            "persona_url": PERSONA_URL,
            "session_ttl_seconds": SESSION_TTL_SECONDS,
            "classification_owner": "persona",
            "fallback_classifier_mode": "degraded_only",
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
    return {"service": "router", "timestamp": _utc_now(), "metrics": {"service_up": 1}}

# Max requests per minute per user (enforced by API gateway in production;
# documented here as the canonical policy for all implementations)
RATE_LIMITS: dict[str, int] = {
    "telegram":    20,
    "discord":     20,
    "web":         60,
    "console":    120,
}

# Tighter cap for high-risk intent classes, regardless of channel
RATE_LIMIT_HIGH_RISK_PER_MIN = 5
HIGH_RISK_INTENT_PREFIXES = ("execution.", "governance.")

# ---------------------------------------------------------------------------
# Channel → tier mapping
# ---------------------------------------------------------------------------

_CHANNEL_TIER: dict[str, str] = {
    "console":  "operator",
    "web":      "web",
    "telegram": "chat",
    "discord":  "chat",
    "cron":     "system",
}

# Channel → role mapping (v1 minimum per OC-001 §9 + Codex review finding #1)
# Full identity resolution (auth session → role) is a follow-up.
_CHANNEL_ROLE: dict[str, str] = {
    "console":  "operator",
    "cron":     "system",
    "web":      "persona",
    "telegram": "persona",
    "discord":  "persona",
}

# ---------------------------------------------------------------------------
# Intent → tool_class mapping
# Aligned with permissions/contract.md §4
# ---------------------------------------------------------------------------

_INTENT_TOOL_CLASS: dict[str, str] = {
    "research":           "research",
    "status":             "status",
    "monitor":            "monitoring",
    "execution.signal":   "execution_signal",
    "execution.live":     "lean_direct",
    "governance":         "governance",
    "governance.approve": "governance",
    "deployment":         "deployment",
}


def _intent_to_tool_class(intent: str | None) -> str:
    if intent is None:
        return "status"
    for prefix, cls in _INTENT_TOOL_CLASS.items():
        if intent.startswith(prefix):
            return cls
    return "status"


# ---------------------------------------------------------------------------
# Permission evaluation — deny-first (OC-001 contract §5–6)
# ---------------------------------------------------------------------------

class PermissionResult(str, Enum):
    ALLOW              = "allow"
    DENY               = "deny"
    ALLOW_WITH_APPROVAL = "allow_with_approval"


def _evaluate_permission(
    channel: str,
    intent: str | None,
    role: str = "persona",
) -> PermissionResult:
    """
    Deny-first permission evaluation.

    Evaluation order (per OC-001 contract §5.1):
      1. resolve context
      2. global deny rules
      3. subject deny rules
      4. explicit allow
      5. approval hooks
      6. default → deny

    This is the v1 implementation stub that encodes the mandatory deny rules.
    Full policy-object loading (from storage) is a follow-up once policy
    storage backend is chosen (OC-001 open item §9).
    """
    tier = _CHANNEL_TIER.get(channel, "chat")
    tool_class = _intent_to_tool_class(intent)

    # ── Global deny rules (mandatory, non-removable per OC-001 §6) ──

    # Rule 1: lean_direct is never allowed in live context from any channel
    if tool_class == "lean_direct":
        log.warning("DENY lean_direct tool from channel=%s intent=%s", channel, intent)
        return PermissionResult.DENY

    # Rule 2: deployment and rollback only from operator/system channels
    if tool_class == "deployment" and tier not in ("operator", "system"):
        log.warning("DENY deployment from non-operator channel=%s", channel)
        return PermissionResult.DENY

    # Rule 3: governance approval only for operator role
    if tool_class == "governance" and role not in ("operator", "approver"):
        log.warning("DENY governance from role=%s", role)
        return PermissionResult.DENY

    # Rule 4: execution_signal requires approval on all channels
    if tool_class == "execution_signal":
        if tier == "operator":
            return PermissionResult.ALLOW_WITH_APPROVAL
        # Non-operator channels cannot execute signals at all
        log.warning("DENY execution_signal from non-operator tier=%s", tier)
        return PermissionResult.DENY

    # ── Approval hooks ──

    # Governance approval transition always requires explicit approval
    if intent == "governance.approve":
        return PermissionResult.ALLOW_WITH_APPROVAL

    # ── Default allow for remaining safe tool classes ──
    # research, status, monitoring are broadly allowed
    return PermissionResult.ALLOW


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RouteRequest(BaseModel):
    channel: str
    user_id: str
    message: str
    session_id: str | None = None


class RouteResponse(BaseModel):
    session_id: str
    agent_id: str
    response: str
    intent: str | None = None
    skill: str | None = None
    permission: str | None = None   # "allow" | "allow_with_approval" (never "deny" — those 403)
    intent_source: str | None = None
    routing_mode: str | None = None
    session_status: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "router",
        "persona_url": PERSONA_URL,
        "session_ttl_seconds": SESSION_TTL_SECONDS,
        "classification_owner": "persona",
        "fallback_classifier_mode": "degraded_only",
    }


@app.post("/route", response_model=RouteResponse)
async def route(req: RouteRequest):
    if req.channel not in _CHANNEL_TIER:
        raise HTTPException(status_code=400, detail=f"Unknown channel: {req.channel!r}")

    session_id = req.session_id or str(uuid.uuid4())

    # ── Step 1: classify intent WITHOUT side effects ──
    # Persona owns the canonical classification path. The router-local
    # classifier remains only as a truthful degraded fallback.
    intent_source = "persona"
    routing_mode = "persona_authoritative"
    intent = None
    skill_hint = None

    # ── Step 3: dispatch to Persona Agent (now safe to produce side effects) ──
    async with httpx.AsyncClient() as client:
        try:
            classify_resp = await client.post(
                f"{PERSONA_URL}/classify",
                json={
                    "session_id": session_id,
                    "user_id": req.user_id,
                    "channel": req.channel,
                    "message": req.message,
                },
                timeout=15,
            )
            classify_resp.raise_for_status()
            classify_body = classify_resp.json()
            intent = classify_body.get("intent")
            skill_hint = classify_body.get("skill")
        except Exception as exc:
            log.warning("Persona classify unavailable; using degraded local classifier: %s", exc)
            intent = _classify_intent_local(req.message)
            skill_hint = _intent_to_skill(intent)
            intent_source = "router.degraded_fallback"
            routing_mode = "degraded_surrogate"

        # ── Step 2: evaluate permission BEFORE any side-effectful dispatch ──
        role = _CHANNEL_ROLE.get(req.channel, "persona")
        perm = _evaluate_permission(channel=req.channel, intent=intent, role=role)

        if perm == PermissionResult.DENY:
            log.warning(
                "Permission DENY: channel=%s intent=%s user=%s",
                req.channel, intent, req.user_id,
            )
            raise HTTPException(status_code=403, detail="Tool permission denied")

        try:
            resp = await client.post(
                f"{PERSONA_URL}/invoke",
                json={
                    "session_id": session_id,
                    "user_id": req.user_id,
                    "channel": req.channel,
                    "message": req.message,
                    "intent_hint": intent,
                },
                timeout=60,
            )
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            log.error("Persona Agent HTTP %s: %s", exc.response.status_code, exc)
            raise HTTPException(status_code=503, detail="Persona Agent error") from exc
        except Exception as exc:
            log.error("Persona Agent unreachable: %s", exc)
            raise HTTPException(status_code=503, detail="Persona Agent unavailable") from exc

    # Use Persona's refined intent if available
    refined_intent = body.get("intent") or intent

    return RouteResponse(
        session_id=session_id,
        agent_id=body.get("agent_id", "persona-default"),
        response=body.get("response", "[no response]"),
        intent=refined_intent,
        skill=body.get("skill") or skill_hint,
        permission=perm.value,
        intent_source=intent_source,
        routing_mode=body.get("runtime", {}).get("mode") or routing_mode,
        session_status=body.get("session_status"),
    )


# ---------------------------------------------------------------------------
# Local intent classifier (lightweight degraded fallback only)
# ---------------------------------------------------------------------------

_INTENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("execution.signal",   ["buy", "sell", "trade", "order", "signal", "execute"]),
    ("governance.approve", ["approve", "promote", "rollback", "deploy live"]),
    ("governance",         ["governance", "permission", "policy"]),
    ("deployment",         ["deploy", "rollback", "artifact"]),
    ("monitor",            ["pnl", "positions", "drawdown", "portfolio", "monitor"]),
    ("research",           ["backtest", "qlib", "strategy", "simulate", "research"]),
    ("status",             ["status", "tasks", "what is running"]),
]


def _classify_intent_local(message: str) -> str:
    lower = message.lower()
    for intent, keywords in _INTENT_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return intent
    return "unknown"


def _intent_to_skill(intent: str | None) -> str | None:
    if intent is None:
        return None
    return {
        "execution.signal": "execution-signal",
        "governance.approve": "governance-review",
        "governance": "governance-review",
        "deployment": "deployment-review",
        "monitor": "monitoring-summary",
        "research": "research-summary",
        "status": "status-summary",
    }.get(intent)
