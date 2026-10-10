"""Servant research-plan drafting: one data-only structured turn, no other tool."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError

from services.research.constants import ALLOWLISTED_STAGE_BACKENDS

_INSTRUCTION = (
    "Draft a research plan (spec_version '1.0') for the request below. "
    "Answer with one JSON object only that satisfies the schema; do not run anything.\n\n"
)


class ServantDraftError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def research_plan_extraction_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["spec_version", "strategy_id", "strategy_spec_registry_id", "stages"],
        "properties": {
            "spec_version": {"type": "string", "enum": ["1.0"]},
            "strategy_id": {"type": "string"},
            "strategy_spec_registry_id": {"type": "string"},
            "stages": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["stage_type"],
                    "properties": {"stage_type": {"type": "string", "enum": sorted(ALLOWLISTED_STAGE_BACKENDS)}},
                },
            },
        },
    }


def _invoke_structured(client: OpenClawOpsClient, *, prompt: str, operator_id: str, trace_id: Optional[str]) -> Dict[str, Any]:
    """Data-only turn: asks for one JSON object matching the caller schema and validated by the adapter."""
    headers = {"X-Operator-Id": operator_id}
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    return client._request(
        "POST",
        "/api/openclaw-adapter/assistant/providers/openclaw/structured",
        body={"mode": "user", "prompt": prompt, "extraction_schema": research_plan_extraction_schema()},
        headers=headers,
        expected_status={200},
        timeout_seconds=client._assistant_timeout_seconds(),
    )


def draft_research_plan(prompt: str, *, operator_id: str, trace_id: Optional[str] = None) -> Dict[str, Any]:
    """Return the raw, unvalidated draft; the caller validates it against the create contract."""
    try:
        raw = _invoke_structured(
            OpenClawOpsClient(), prompt=_INSTRUCTION + prompt, operator_id=operator_id, trace_id=trace_id
        )
    except OpenClawOpsClientError as exc:
        raise ServantDraftError(exc.message, exc.status_code if exc.status_code in {422, 503, 504} else 502) from exc
    data = raw.get("data") if isinstance(raw, dict) else None
    draft = (data.get("output") or {}).get("structured_data") if isinstance(data, dict) else None
    if not isinstance(draft, dict):
        raise ServantDraftError("structured draft missing from provider response")
    return draft
