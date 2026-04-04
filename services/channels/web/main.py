"""
Channel — Web API
FastAPI REST + WebSocket on port 8000.

Endpoints:
  POST /chat                  — synchronous chat
  GET  /stream/{session_id}   — SSE stream (stub, TODO)

Env vars:
  ROUTER_URL — default http://localhost:8001
"""
import asyncio
import os
import uuid

import httpx
from fastapi import FastAPI, Request
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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "web-channel"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{ROUTER_URL}/route",
                json={"channel": "web", "user_id": req.user_id, "message": req.message},
                timeout=30,
            )
            resp.raise_for_status()
            reply = resp.json().get("response", "[no response]")
        except Exception as exc:
            reply = f"[router error: {exc}]"

    return ChatResponse(session_id=session_id, response=reply)


@app.get("/stream/{session_id}")
async def stream(session_id: str):
    # TODO: implement SSE streaming once Persona Agent streams tokens
    async def event_generator():
        yield f"data: [stream not yet implemented for session {session_id}]\n\n"
        await asyncio.sleep(0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
