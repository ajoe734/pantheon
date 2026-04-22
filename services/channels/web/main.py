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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

ROUTER_URL = os.getenv("ROUTER_URL", "http://localhost:8001")

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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "web-channel"}


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
