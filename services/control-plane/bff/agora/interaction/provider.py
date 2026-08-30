"""Governed selected-Persona OpenClaw invocation helpers.

This module contains no provider simulator.  It freezes canonical Persona and
capability records, builds the exact adapter admission, and validates provider
JSON before it can become an authoritative opinion projection.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


ADVISORY_ENVIRONMENT_ORDER = ("analysis", "research", "shadow", "paper")
ADVISORY_ENVIRONMENTS = frozenset(ADVISORY_ENVIRONMENT_ORDER)
PERSONA_WORKSPACE_ROOT = "/home/node/.openclaw/workspaces"

OPERATIONAL_LIFECYCLE_STATES = frozenset(
    {
        "research_only",
        "consultable",
        "paper_owner",
        "live_owner",
        "active",
        "paper_running",
        "paper_only",
    }
)


def is_persona_operational(persona: Dict[str, Any]) -> bool:
    """Return whether the Persona lifecycle is operational for opinion interactions.

    Only explicit Persona Registry lifecycle truth (and supported operational aliases)
    is accepted. Generic deployment labels (e.g. ``deployed``) or draft/frozen/retired
    states must not grant interaction eligibility.
    """
    lifecycle = str(persona.get("lifecycle_state") or "").strip().lower()
    return lifecycle in OPERATIONAL_LIFECYCLE_STATES


def authority_boundary() -> Dict[str, Any]:
    return {
        "execution_authority": "none",
        "order_submitted": False,
        "broker_called": False,
        "capital_changed": False,
        "runtime_bound": False,
        "lifecycle_promoted": False,
        "policy_mutated": False,
        "persona_memory_mutated": False,
    }


def provider_output_shape() -> Dict[str, Any]:
    """Return the complete provider contract shown to the selected Persona.

    Keep this example as strict as ``ProviderOpinionPayload``.  In particular,
    an empty measure example is not sufficient: a provider asked to recommend
    a measure must know every field that the fail-closed validator requires.
    """

    evidence_ref = {
        "ref_type": "non-empty string",
        "ref_id": "non-empty string",
        "version": None,
        "observed_at": "RFC3339 timestamp",
        "data_cutoff": "RFC3339 timestamp",
        "freshness": "fresh|stale|unknown",
        "summary": None,
    }
    return {
        "conclusion": "support|oppose|conditional|abstain|insufficient_evidence",
        "rationale": "non-empty string",
        "confidence": "number 0..1",
        "uncertainty": ["string"],
        "risks": ["string"],
        "invalidation_conditions": ["string"],
        "evidence_refs": [evidence_ref],
        "recommended_measures": [{
            "measure_id": "stable non-empty provider-local string",
            "measure_type": (
                "strategy_parameter_change|condition_change|risk_limit_recommendation|"
                "research_request|paper_candidate_request|allocation_review_request|"
                "containment_recommendation|journal_lesson|memory_candidate|"
                "persona_mutation_review"
            ),
            "target": {
                "kind": "non-empty string",
                "id": "non-empty string",
                "version": "non-empty string",
                "path": None,
            },
            "current_value": None,
            "proposed_value": "any JSON value",
            "rationale": "non-empty string",
            "expected_benefit": "non-empty string",
            "adverse_scenarios": ["non-empty string"],
            "confidence": "number 0..1",
            "evidence_refs": [evidence_ref],
            "environment_ceiling": "analysis|research|shadow|paper",
            "validation_plan": {
                "validator": "pantheon_candidate_validation_v1",
                "required_checks": [
                    "source_binding|evidence_freshness|target_version|"
                    "authority_boundary|rollback_plan"
                ],
            },
            "rollback_trigger": "non-empty string",
            "rollback_action": "non-empty string",
            "authority": authority_boundary(),
        }],
    }


class EvidenceRef(BaseModel):
    model_config = {"extra": "forbid"}

    ref_type: str = Field(min_length=1)
    ref_id: str = Field(min_length=1)
    version: Optional[str] = Field(default=None, min_length=1)
    observed_at: datetime
    data_cutoff: datetime
    freshness: Literal["fresh", "stale", "unknown"]
    summary: Optional[str] = None


class MeasureTarget(BaseModel):
    model_config = {"extra": "forbid"}

    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    path: Optional[str] = Field(default=None, min_length=1)


class ValidationPlan(BaseModel):
    model_config = {"extra": "forbid"}

    validator: str = Field(min_length=1)
    required_checks: List[str] = Field(min_length=1)


class RecommendedMeasure(BaseModel):
    model_config = {"extra": "forbid"}

    measure_id: str = Field(min_length=1)
    measure_type: Literal[
        "strategy_parameter_change",
        "condition_change",
        "risk_limit_recommendation",
        "research_request",
        "paper_candidate_request",
        "allocation_review_request",
        "containment_recommendation",
        "journal_lesson",
        "memory_candidate",
        "persona_mutation_review",
    ]
    target: MeasureTarget
    current_value: Any = None
    proposed_value: Any
    rationale: str = Field(min_length=1)
    expected_benefit: str = Field(min_length=1)
    adverse_scenarios: List[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: List[EvidenceRef] = Field(min_length=1)
    environment_ceiling: Literal["analysis", "research", "shadow", "paper"]
    validation_plan: ValidationPlan
    rollback_trigger: str = Field(min_length=1)
    rollback_action: str = Field(min_length=1)
    authority: Dict[str, Any]

    @model_validator(mode="after")
    def no_execution_authority(self) -> "RecommendedMeasure":
        if self.authority != authority_boundary():
            raise ValueError("recommended measure must carry the exact no-authority boundary")
        return self


class ProviderOpinionPayload(BaseModel):
    model_config = {"extra": "forbid"}

    conclusion: Literal["support", "oppose", "conditional", "abstain", "insufficient_evidence"]
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    uncertainty: List[str]
    risks: List[str]
    invalidation_conditions: List[str]
    evidence_refs: List[EvidenceRef]
    recommended_measures: List[RecommendedMeasure]


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def recommended_measure_sha256(value: Dict[str, Any]) -> str:
    """Hash the provider-validated measure before it crosses persistence.

    The provider is not allowed to claim this binding.  The BFF appends it to
    the validated payload so browser clients can reference, but never author,
    the exact persisted recommendation.
    """

    payload = {key: item for key, item in value.items() if key != "measure_sha256"}
    return _canonical_sha(payload)


def _persona_version(persona: Dict[str, Any]) -> str:
    metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
    explicit = str(
        persona.get("persona_version")
        or persona.get("version_id")
        or persona.get("version")
        or metadata.get("persona_version")
        or metadata.get("version_id")
        or persona.get("updated_at")
        or ""
    ).strip()
    if explicit:
        return explicit
    # Legacy registry rows without a version still receive an immutable content
    # version; an unversioned mutable alias is never passed to OpenClaw.
    return f"sha256:{_canonical_sha(persona)}"


def build_participant_admission(
    *,
    persona: Dict[str, Any],
    capability_snapshot: Dict[str, Any],
    environment: str,
    tenant_id: str,
    captured_at: str,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    persona_id = str(persona.get("persona_id") or persona.get("id") or "").strip()
    snapshot_persona_id = str(capability_snapshot.get("persona_id") or persona_id).strip()
    snapshot_id = str(
        capability_snapshot.get("snapshot_id") or capability_snapshot.get("id") or ""
    ).strip()
    capabilities = list(
        capability_snapshot.get("capabilities")
        or capability_snapshot.get("allowed_capabilities")
        or []
    )
    persona_tenant_id = str(persona.get("tenant_id") or "").strip()
    lifecycle = str(persona.get("lifecycle_state") or "").strip().lower()
    requested_environment = str(environment or "").strip().lower()
    if not tenant_id or persona_tenant_id != tenant_id:
        raise ValueError("Persona tenant does not match the interaction tenant")
    if not is_persona_operational(persona):
        raise ValueError("Persona lifecycle is not operational")
    if not persona_id or snapshot_persona_id != persona_id or not snapshot_id:
        raise ValueError("Persona capability snapshot identity is incomplete or mismatched")
    if capabilities != ["persona_opinion"]:
        raise ValueError("Persona capability snapshot must grant exactly persona_opinion")
    metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
    ceiling = str(
        persona.get("environment_ceiling") or metadata.get("environment_ceiling") or ""
    ).strip().lower()
    if requested_environment not in ADVISORY_ENVIRONMENTS or ceiling not in ADVISORY_ENVIRONMENTS:
        raise ValueError("Persona opinion environment exceeds the advisory ceiling")
    if ADVISORY_ENVIRONMENT_ORDER.index(requested_environment) > ADVISORY_ENVIRONMENT_ORDER.index(ceiling):
        raise ValueError("Requested environment exceeds the Persona advisory ceiling")
    persona_version = _persona_version(persona)
    digest = hashlib.sha256(
        f"{tenant_id}\0{persona_id}\0{persona_version}\0{snapshot_id}\0{requested_environment}".encode("utf-8")
    ).hexdigest()[:24]
    agent_id = f"persona-opinion-{digest}"
    workspace_ref = f"{PERSONA_WORKSPACE_ROOT}/{agent_id}"
    admission = {
        "persona_id": persona_id,
        "tenant_id": tenant_id,
        "persona_version": persona_version,
        "agent_id": agent_id,
        "workspace_ref": workspace_ref,
        "capability_snapshot_id": snapshot_id,
        "allowed_capabilities": ["persona_opinion"],
        "environment_ceiling": ceiling,
        "requested_environment": requested_environment,
        "execution_authority": "none",
        "display_name": persona.get("display_name") or persona.get("name") or persona_id,
        "mandate": persona.get("mandate"),
        "archetype": persona.get("archetype") or metadata.get("archetype"),
        "strategy_family": persona.get("strategy_family"),
        "traits": persona.get("traits") or metadata.get("traits") or {},
    }
    participant = {
        "persona_id": persona_id,
        "persona_version": persona_version,
        "session_persona_id": f"session-persona:{persona_id}:{persona_version}",
        "display_name": persona.get("display_name") or persona.get("name") or persona_id,
        "provider_agent_id": agent_id,
        "workspace_id": workspace_ref,
        "environment_ceiling": ceiling,
        "capability_snapshot": ["persona_opinion"],
        "captured_at": captured_at,
    }
    return participant, admission


def build_provider_prompt(
    *,
    topic: str,
    mode: str,
    interaction_id: str,
    participant: Dict[str, Any],
    persona_profile: Dict[str, Any],
    context_refs: List[Dict[str, Any]],
    tenant_id: str,
    submitted_at: str,
) -> tuple[str, Dict[str, Any]]:
    metadata = persona_profile.get("metadata") if isinstance(persona_profile.get("metadata"), dict) else {}
    frozen_persona_profile = {
        "persona_id": participant["persona_id"],
        "persona_version": participant["persona_version"],
        "display_name": participant["display_name"],
        "mandate": persona_profile.get("mandate"),
        "archetype": persona_profile.get("archetype") or metadata.get("archetype"),
        "strategy_family": persona_profile.get("strategy_family"),
        "traits": persona_profile.get("traits") or metadata.get("traits") or {},
    }
    context_pack = {
        "schema_version": "agora.persona-opinion.request.v1",
        "interaction_id": interaction_id,
        "tenant_id": tenant_id,
        "mode": mode,
        "immutable_human_request": {
            "request_text": topic,
            "submitted_at": submitted_at,
            "request_sha256": hashlib.sha256(topic.encode("utf-8")).hexdigest(),
        },
        "participant": participant,
        "frozen_persona_profile": frozen_persona_profile,
        "context_refs": context_refs,
        "authority": authority_boundary(),
    }
    exact_context = json.dumps(context_pack, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    schema = provider_output_shape()
    prompt = (
        "Return exactly one JSON object and no markdown. Act only as the frozen selected Persona. "
        "Do not call tools or read/write memory. Do not submit orders, call brokers, change capital, "
        "bind runtime, promote lifecycle, mutate policy, or claim execution. "
        f"Required output shape: {json.dumps(schema, separators=(',', ':'))}\n"
        "recommended_measures may be empty. If it is non-empty, every measure must include every "
        "field shown above, use at least one fully populated evidence_refs entry, carry the exact "
        "no-authority object shown above, and contain no extra fields. Do not return "
        "measure_sha256; Pantheon appends that binding only after validation.\n"
        "For each recommended measure set validation_plan.validator to "
        "pantheon_candidate_validation_v1 and choose required_checks only from "
        "source_binding,evidence_freshness,target_version,authority_boundary,rollback_plan.\n"
        f"IMMUTABLE_TYPED_CONTEXT={exact_context}"
    )
    return prompt, context_pack


def _provider_text(provider_data: Dict[str, Any]) -> tuple[str, str, str]:
    if str(provider_data.get("status") or "") != "completed":
        output = provider_data.get("output") if isinstance(provider_data.get("output"), dict) else {}
        raise ValueError(str(output.get("reason") or "provider_not_completed"))
    output = provider_data.get("output") if isinstance(provider_data.get("output"), dict) else {}
    response_id = str(output.get("request_id") or "").strip()
    agent_id = str(output.get("agent_id") or "").strip()
    events = output.get("json_events") if isinstance(output.get("json_events"), list) else []
    for event in reversed(events):
        item = event.get("item") if isinstance(event, dict) and isinstance(event.get("item"), dict) else {}
        text = str(item.get("text") or "").strip()
        if text:
            return text, response_id, agent_id
    raise ValueError("provider response contained no opinion JSON")


def validate_provider_opinion(
    provider_payload: Dict[str, Any],
    *,
    expected_agent_id: str,
) -> tuple[Dict[str, Any], str, str]:
    provider_data = (
        provider_payload.get("data")
        if isinstance(provider_payload.get("data"), dict)
        else provider_payload
    )
    text, response_id, agent_id = _provider_text(provider_data)
    if not response_id:
        raise ValueError("provider response correlation id is missing")
    if agent_id != expected_agent_id:
        raise ValueError("provider response agent does not match the admitted Persona agent")
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1])
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:].lstrip()
    try:
        raw = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("provider response is not valid JSON") from exc
    opinion = ProviderOpinionPayload.model_validate(raw).model_dump(mode="json")
    for measure in opinion["recommended_measures"]:
        measure["measure_sha256"] = recommended_measure_sha256(measure)
    return opinion, response_id, _canonical_sha(raw)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
