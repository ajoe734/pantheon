"""
Control Plane — Persona Agent
FastAPI service wrapping a LangGraph stateful agent.

State machine design (business logic is TODO until schemas are LOCKED):
  START → intent_classify → [skill_select | memory_lookup] → respond → END

Environment variables:
  LLM_BACKEND  — "anthropic" | "openai" | "ollama"  (default: anthropic)
  REDIS_URL    — for memory/session store
  FIRESTORE_COLLECTION_STRATEGIES
  GCP_PROJECT_ID
"""
import os
from typing import Annotated, TypedDict

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Pantheon Persona Agent", version="0.1.0")

LLM_BACKEND = os.getenv("LLM_BACKEND", "anthropic")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# ---------------------------------------------------------------------------
# LangGraph state definition
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    session_id: str
    user_id: str
    channel: str
    message: str
    intent: str | None           # filled by intent_classify node
    skill: str | None            # filled by skill_select node
    memory: list[dict] | None    # filled by memory_lookup node
    response: str | None         # filled by respond node
    mandate: dict | None         # from persona policy
    policy: dict | None          # allowed/blocked operations


# ---------------------------------------------------------------------------
# Stub nodes (business logic TODO — waiting for P1-001 and P2-001 LOCK)
# ---------------------------------------------------------------------------

def intent_classify(state: AgentState) -> AgentState:
    # TODO: call LLM to classify intent from state["message"]
    return {**state, "intent": "unknown"}


def skill_select(state: AgentState) -> AgentState:
    # TODO: use SkillLoader to pick a skill matching state["intent"]
    return {**state, "skill": None}


def memory_lookup(state: AgentState) -> AgentState:
    # TODO: fetch conversation history from Redis using state["session_id"]
    return {**state, "memory": []}


def respond(state: AgentState) -> AgentState:
    # TODO: invoke LLM with context (intent, skill, memory, policy)
    return {**state, "response": "[system not ready — upstream schemas not locked]"}


# ---------------------------------------------------------------------------
# Graph wiring (placeholder — real graph built after schemas locked)
# ---------------------------------------------------------------------------

def _build_graph():
    try:
        from langgraph.graph import StateGraph, END
        g = StateGraph(AgentState)
        g.add_node("intent_classify", intent_classify)
        g.add_node("skill_select", skill_select)
        g.add_node("memory_lookup", memory_lookup)
        g.add_node("respond", respond)
        g.set_entry_point("intent_classify")
        g.add_edge("intent_classify", "memory_lookup")
        g.add_edge("memory_lookup", "skill_select")
        g.add_edge("skill_select", "respond")
        g.add_edge("respond", END)
        return g.compile()
    except ImportError:
        return None  # langgraph not installed in dev; stub mode


GRAPH = _build_graph()


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

class InvokeRequest(BaseModel):
    session_id: str
    user_id: str
    channel: str
    message: str


class InvokeResponse(BaseModel):
    response: str
    intent: str | None = None
    skill: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "service": "persona-agent", "llm_backend": LLM_BACKEND}


@app.post("/invoke", response_model=InvokeResponse)
async def invoke(req: InvokeRequest):
    initial_state: AgentState = {
        "session_id": req.session_id,
        "user_id": req.user_id,
        "channel": req.channel,
        "message": req.message,
        "intent": None,
        "skill": None,
        "memory": None,
        "response": None,
        "mandate": None,
        "policy": None,
    }

    if GRAPH is not None:
        final = GRAPH.invoke(initial_state)
    else:
        # stub fallback when langgraph not available
        s = intent_classify(initial_state)
        s = memory_lookup(s)
        s = skill_select(s)
        final = respond(s)

    return InvokeResponse(
        response=final["response"],
        intent=final["intent"],
        skill=final["skill"],
    )
