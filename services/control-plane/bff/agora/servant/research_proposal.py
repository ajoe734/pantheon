"""Servant research-plan drafting: one data-only structured turn, no other tool."""
from __future__ import annotations

from typing import Any, Dict, Optional, Set

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


def _project_canonical_schema(raw_schema: Dict[str, Any]) -> Dict[str, Any]:
    defs = raw_schema.get("$defs", {})
    if not isinstance(defs, dict):
        raise ValueError("Malformed $defs in canonical schema")

    def resolve_ref(ref_str: str, seen: Set[str]) -> tuple[Dict[str, Any], Set[str]]:
        if not isinstance(ref_str, str) or not ref_str.startswith("#/$defs/"):
            raise ValueError(f"Unknown or non-local reference: {ref_str}")
        name = ref_str[len("#/$defs/"):]
        if name not in defs:
            raise ValueError(f"Unknown reference: {name}")
        if name in seen:
            raise ValueError(f"Cyclic reference detected: {name}")
        target = defs[name]
        if not isinstance(target, dict):
            raise ValueError(f"Malformed definition for reference: {name}")
        return target, seen | {name}

    def transform(node: Any, seen: Set[str]) -> Any:
        if not isinstance(node, dict):
            return node

        if "$ref" in node:
            target, new_seen = resolve_ref(node["$ref"], seen)
            merged = {k: v for k, v in node.items() if k != "$ref"}
            merged = {**target, **merged}
            return transform(merged, new_seen)

        if "anyOf" in node:
            any_of = node["anyOf"]
            if not isinstance(any_of, list):
                raise ValueError("Malformed anyOf in schema")
            null_branches = [b for b in any_of if isinstance(b, dict) and b.get("type") == "null"]
            non_null_branches = [b for b in any_of if not (isinstance(b, dict) and b.get("type") == "null")]

            if len(null_branches) == 1 and len(non_null_branches) == 1:
                base = transform(non_null_branches[0], seen)
                if not isinstance(base, dict):
                    raise ValueError("Malformed non-null branch in nullable union")
                base_type = base.get("type")
                if base_type is None:
                    if "properties" in base:
                        base_type = "object"
                    elif "items" in base:
                        base_type = "array"
                if isinstance(base_type, list):
                    new_types = [t for t in base_type if t != "null"] + ["null"]
                elif isinstance(base_type, str):
                    new_types = [base_type, "null"]
                else:
                    new_types = ["null"]

                result = {k: v for k, v in base.items() if k != "default"}
                result["type"] = new_types
                if "enum" in result and isinstance(result["enum"], list) and None not in result["enum"]:
                    result["enum"] = list(result["enum"]) + [None]
                for k, v in node.items():
                    if k not in ("anyOf", "default"):
                        result[k] = transform(v, seen)
                return result
            else:
                raise ValueError(f"Unsupported general union: {any_of}")

        result: Dict[str, Any] = {}
        for k, v in node.items():
            if k == "default":
                continue
            if k == "properties" and isinstance(v, dict):
                result["properties"] = {pk: transform(pv, seen) for pk, pv in v.items()}
            elif k == "items":
                result["items"] = transform(v, seen) if isinstance(v, dict) else v
            elif k == "additionalProperties" and isinstance(v, dict):
                result["additionalProperties"] = transform(v, seen)
            else:
                result[k] = v
        return result

    base = {k: v for k, v in raw_schema.items() if k != "$defs"}
    projected = transform(base, set())
    if not isinstance(projected, dict):
        raise ValueError("Projected schema must be a dict")

    # Preserve domain constraints: spec_version 1.0 and allowlisted stage types
    props = projected.get("properties")
    if isinstance(props, dict):
        spec_ver = props.get("spec_version")
        if isinstance(spec_ver, dict):
            spec_ver["enum"] = ["1.0"]
        stages = props.get("stages")
        if isinstance(stages, dict):
            stage_items = stages.get("items")
            if isinstance(stage_items, dict):
                stage_props = stage_items.get("properties")
                if isinstance(stage_props, dict):
                    stage_type = stage_props.get("stage_type")
                    if isinstance(stage_type, dict):
                        stage_type["enum"] = sorted(ALLOWLISTED_STAGE_BACKENDS)

    return projected


def research_plan_extraction_schema(canonical_schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if canonical_schema is None:
        from ..research.routes.common import ResearchPlanCreateRequest
        canonical_schema = ResearchPlanCreateRequest.model_json_schema()
    return _project_canonical_schema(canonical_schema)


def _invoke_structured(
    client: OpenClawOpsClient,
    *,
    prompt: str,
    operator_id: str,
    trace_id: Optional[str],
    canonical_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Data-only turn: asks for one JSON object matching the caller schema and validated by the adapter."""
    headers = {"X-Operator-Id": operator_id}
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    extraction_schema = research_plan_extraction_schema(canonical_schema)
    return client._request(
        "POST",
        "/api/openclaw-adapter/assistant/providers/openclaw/structured",
        body={"mode": "user", "prompt": prompt, "extraction_schema": extraction_schema},
        headers=headers,
        expected_status={200},
        timeout_seconds=client._assistant_timeout_seconds(),
    )


def draft_research_plan(
    prompt: str,
    *,
    operator_id: str,
    trace_id: Optional[str] = None,
    canonical_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the raw, unvalidated draft; the caller validates it against the create contract."""
    try:
        raw = _invoke_structured(
            OpenClawOpsClient(),
            prompt=_INSTRUCTION + prompt,
            operator_id=operator_id,
            trace_id=trace_id,
            canonical_schema=canonical_schema,
        )
    except OpenClawOpsClientError as exc:
        raise ServantDraftError(exc.message, exc.status_code if exc.status_code in {422, 503, 504} else 502) from exc
    except ValueError as exc:
        raise ServantDraftError(str(exc), 422) from exc
    data = raw.get("data") if isinstance(raw, dict) else None
    draft = (data.get("output") or {}).get("structured_data") if isinstance(data, dict) else None
    if not isinstance(draft, dict):
        raise ServantDraftError("structured draft missing from provider response")
    return draft
