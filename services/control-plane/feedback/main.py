"""
Control Plane — Trader Feedback Ingestion (FB-002)

Captures explicit trader approve/edit/reject/rationale events as append-only,
governed learning input. This service records feedback only; it does not
authorize live actions or mutate registry state.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator

from schema_validation import build_trader_feedback_validator
from store import (
    QueryFilterValidationError,
    TraderFeedbackStore,
    build_query_filters,
    parse_rfc3339,
)

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STORE_PATH = ROOT / ".orchestrator" / "feedback" / "trader_feedback_events.jsonl"
SCHEMA_PATH = ROOT / "services" / "feedback" / "schema" / "trader_feedback_event.schema.json"


def _load_governance_audit():
    audit_path = ROOT / "services" / "control-plane" / "governance" / "audit.py"
    if not audit_path.exists():
        return None

    module_name = "pantheon_governance_audit"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, audit_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[module_name] = module
    return module


GOVERNANCE_AUDIT = _load_governance_audit()
TRADER_FEEDBACK_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
TRADER_FEEDBACK_VALIDATOR = build_trader_feedback_validator(TRADER_FEEDBACK_SCHEMA)

CHANNEL_ALLOWED_ROLES: dict[str, set[str]] = {
    "console": {"operator", "approver", "system"},
    "web": {"operator", "approver"},
    "telegram": {"operator", "approver"},
    "discord": {"operator", "approver"},
    "api": {"operator", "approver", "system"},
}

FEEDBACK_EVENT_TYPES = Literal["approve", "edit", "reject", "rationale"]
PROMOTION_STATES = Literal["draft", "candidate", "paper", "live", "retired"]


class FeedbackTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_id: str | None = None
    strategy_id: str
    artifact_version: str | None = None
    artifact_type: Literal[
        "strategy_spec",
        "model_artifact",
        "feature_set",
        "prompt_bundle",
        "signal_snapshot",
        "execution_bundle",
    ] | None = None
    promotion_state: Literal["draft", "candidate", "paper", "live", "retired"] | None = None
    lineage_ref: str | None = None

    @model_validator(mode="after")
    def validate_registry_linkage(self) -> "FeedbackTarget":
        has_registry_pointer = bool(self.registry_id)
        has_version_pointer = bool(self.artifact_version and self.artifact_type)
        has_lineage_pointer = bool(self.lineage_ref and self.artifact_type)
        if not (has_registry_pointer or has_version_pointer or has_lineage_pointer):
            raise ValueError(
                "target must include registry_id, or artifact_version + artifact_type, or lineage_ref + artifact_type"
            )
        return self


class FeedbackEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    operation: Literal["replace", "append", "remove", "annotate"]
    value: Any | None = None


class TraderFeedbackEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type: FEEDBACK_EVENT_TYPES
    created_at: str
    actor_id: str
    actor_role: Literal["operator", "approver", "system"]
    channel: Literal["console", "web", "telegram", "discord", "api"]
    task_ref: str | None = None
    decision_ref: str | None = None
    rationale: str | None = None
    edits: list[FeedbackEdit] | None = None
    target: FeedbackTarget

    @model_validator(mode="after")
    def validate_event(self) -> "TraderFeedbackEvent":
        if parse_rfc3339(self.created_at) is None:
            raise ValueError("created_at must be a valid RFC3339 timestamp with timezone")

        allowed_roles = CHANNEL_ALLOWED_ROLES[self.channel]
        if self.actor_role not in allowed_roles:
            raise ValueError(
                f"actor_role '{self.actor_role}' is not permitted on channel '{self.channel}'"
            )

        if self.event_type == "edit" and not self.edits:
            raise ValueError("edit events require at least one edit entry")
        if self.event_type != "edit" and self.edits:
            raise ValueError("edits may only be supplied for event_type='edit'")
        if self.event_type == "rationale" and not self.rationale:
            raise ValueError("rationale events require a non-empty rationale")
        return self


class IngestResponse(BaseModel):
    status: Literal["created", "duplicate"]
    event: dict[str, Any]


class QueryResponse(BaseModel):
    count: int
    events: list[dict[str, Any]]


def validate_against_schema(event: dict[str, Any]) -> None:
    if TRADER_FEEDBACK_VALIDATOR is None:  # pragma: no cover - exercised when dependency missing
        return
    TRADER_FEEDBACK_VALIDATOR.validate(event)


def _audit_feedback(event: dict[str, Any], stored_status: str) -> None:
    if GOVERNANCE_AUDIT is None:
        return

    GOVERNANCE_AUDIT.log_event(
        agent_id="feedback-ingestor",
        user_id=event.get("actor_id"),
        action=f"trader_feedback.{event.get('event_type')}",
        risk_level="medium" if event.get("event_type") in {"approve", "reject"} else "low",
        payload={
            "status": stored_status,
            "channel": event.get("channel"),
            "task_ref": event.get("task_ref"),
            "decision_ref": event.get("decision_ref"),
            "target": event.get("target"),
        },
    )


def create_app(store_path: str | Path | None = None) -> FastAPI:
    resolved_store_path = Path(store_path or os.getenv("TRADER_FEEDBACK_STORE_PATH", DEFAULT_STORE_PATH))
    store = TraderFeedbackStore(resolved_store_path)
    app = FastAPI(title="Pantheon Trader Feedback", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "trader-feedback", "store_path": str(resolved_store_path)}

    @app.get("/__health__")
    async def docker_health() -> dict[str, str]:
        return {"status": "ok", "service": "trader-feedback", "store_path": str(resolved_store_path)}

    @app.post("/trader-feedback", response_model=IngestResponse)
    async def ingest_trader_feedback(event: TraderFeedbackEvent):
        payload = event.model_dump(mode="json", exclude_none=True)
        try:
            validate_against_schema(payload)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"schema validation failed: {exc}") from exc

        created, stored_event = store.append(payload)
        stored_status = "created" if created else "duplicate"
        _audit_feedback(stored_event, stored_status)
        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        body = IngestResponse(status=stored_status, event=stored_event)
        return JSONResponse(status_code=response_status, content=body.model_dump(mode="json"))

    @app.get("/trader-feedback", response_model=QueryResponse)
    async def list_trader_feedback(
        strategy_id: str | None = None,
        registry_id: str | None = None,
        promotion_state: PROMOTION_STATES | None = None,
        event_type: FEEDBACK_EVENT_TYPES | None = None,
        actor_id: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> QueryResponse:
        try:
            filters = build_query_filters(
                strategy_id=strategy_id,
                registry_id=registry_id,
                promotion_state=promotion_state,
                event_type=event_type,
                actor_id=actor_id,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
            )
        except QueryFilterValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        events = store.list(filters)
        return QueryResponse(count=len(events), events=events)

    return app


app = create_app()
