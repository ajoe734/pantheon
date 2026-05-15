"""
Channel — Web API
FastAPI REST + WebSocket on port 8000.

Endpoints:
  POST /chat                  — synchronous chat
  GET  /stream/{session_id}   — truthful SSE availability/status stream

Env vars:
  ROUTER_URL — default http://localhost:8001
"""
import asyncio
import json
import os
import uuid

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from services.foundation.health import health_payload, metrics_payload, readiness_status_code

SERVICE_NAME = "web-channel"
ROUTER_URL = os.getenv("ROUTER_URL", "http://localhost:8001")
ROUTER_HEALTH_TIMEOUT_SECONDS = float(os.getenv("ROUTER_HEALTH_TIMEOUT_SECONDS", "2.0"))

app = FastAPI(title="Pantheon Web Channel", version="0.1.0")


class ChatRequest(BaseModel):
    user_id: str
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    response: str
    intent: str | None = None
    skill: str | None = None
    permission: str | None = None
    intent_source: str | None = None
    routing_mode: str | None = None
    session_status: str | None = None


def _router_probe_url(path: str) -> str:
    return f"{ROUTER_URL.rstrip('/')}{path}"


def _health_details() -> dict[str, object]:
    return {
        "router_url": ROUTER_URL,
        "router_probe": "/readyz",
        "channel": "web",
        "compose_default": True,
    }


def _router_dependency_from_response(response: httpx.Response, url: str, *, probe: str) -> dict[str, object]:
    try:
        body = response.json()
    except Exception:
        body = {}

    router_status = str(body.get("status") or "").lower() if isinstance(body, dict) else ""
    if 200 <= response.status_code < 300:
        status = "ok" if router_status in {"", "ok"} else "degraded"
    elif router_status == "degraded":
        status = "degraded"
    else:
        status = "unavailable"

    payload: dict[str, object] = {
        "status": status,
        "url": url,
        "probe": probe,
        "status_code": response.status_code,
    }
    if router_status:
        payload["router_status"] = router_status
    if isinstance(body, dict) and body.get("service"):
        payload["service"] = body["service"]
    if status != "ok" and not router_status:
        payload["reason"] = "router_ready_probe_failed"
    return payload


async def _router_dependency() -> dict[str, object]:
    ready_url = _router_probe_url("/readyz")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(ready_url, timeout=ROUTER_HEALTH_TIMEOUT_SECONDS)
        except Exception as exc:
            return {
                "status": "unavailable",
                "url": ready_url,
                "probe": "/readyz",
                "reason": str(exc),
            }

        if response.status_code != 404:
            return _router_dependency_from_response(response, ready_url, probe="/readyz")

        legacy_url = _router_probe_url("/health")
        try:
            legacy_response = await client.get(legacy_url, timeout=ROUTER_HEALTH_TIMEOUT_SECONDS)
        except Exception as exc:
            return {
                "status": "unavailable",
                "url": legacy_url,
                "probe": "/health",
                "reason": str(exc),
            }
        return _router_dependency_from_response(legacy_response, legacy_url, probe="/health")


async def _health_payload_with_router() -> dict[str, object]:
    return health_payload(
        SERVICE_NAME,
        dependencies={"router": await _router_dependency()},
        details=_health_details(),
    )


@app.get("/healthz")
@app.get("/health")
async def health():
    return await _health_payload_with_router()


@app.get("/livez")
async def livez():
    return health_payload(SERVICE_NAME, details=_health_details())


@app.get("/readyz")
async def readyz():
    payload = await _health_payload_with_router()
    return JSONResponse(payload, status_code=readiness_status_code(payload))


@app.get("/metrics")
async def metrics():
    return metrics_payload(SERVICE_NAME)


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    reply = "[no response]"
    intent = None
    skill = None
    permission = None
    intent_source = None
    routing_mode = None
    session_status = None

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{ROUTER_URL}/route",
                json={
                    "channel": "web",
                    "user_id": req.user_id,
                    "message": req.message,
                    "session_id": session_id,
                },
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            reply = body.get("response", "[no response]")
            session_id = body.get("session_id", session_id)
            intent = body.get("intent")
            skill = body.get("skill")
            permission = body.get("permission")
            intent_source = body.get("intent_source")
            routing_mode = body.get("routing_mode")
            session_status = body.get("session_status")
        except Exception as exc:
            reply = f"[router unavailable] {exc}"
            routing_mode = "degraded_surrogate"
            session_status = "degraded"

    return ChatResponse(
        session_id=session_id,
        response=reply,
        intent=intent,
        skill=skill,
        permission=permission,
        intent_source=intent_source,
        routing_mode=routing_mode,
        session_status=session_status,
    )


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


@app.get("/stream/{session_id}")
async def stream(session_id: str):
    async def event_generator():
        yield _sse_event(
            "session",
            {
                "session_id": session_id,
                "stream_mode": "status_only",
            },
        )
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(f"{ROUTER_URL}/health", timeout=10)
                resp.raise_for_status()
                yield _sse_event(
                    "router_health",
                    {
                        "session_id": session_id,
                        "router": resp.json(),
                    },
                )
            except Exception as exc:
                yield _sse_event(
                    "router_health",
                    {
                        "session_id": session_id,
                        "status": "unavailable",
                        "reason": str(exc),
                    },
                )
        yield _sse_event(
            "notice",
            {
                "session_id": session_id,
                "streaming": "disabled",
                "reason": "Incremental token streaming is not enabled for the web channel; this SSE surface only reports truthful availability metadata.",
            },
        )
        await asyncio.sleep(0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
