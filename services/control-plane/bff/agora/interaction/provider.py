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


def _clean_ref_list(raw: Any, *, max_items: int = 20) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        val = str(item or "").strip()
        if val and val not in out:
            out.append(val)
            if len(out) >= max_items:
                break
    return out


def _extract_exact_bound_artifact_parameters(
    *,
    persona: Dict[str, Any],
    active_rb: Optional[Dict[str, Any]],
    read_store: Any,
    tenant_id: str,
) -> Dict[str, Any]:
    """Resolve strategy parameters ONLY from the Persona's exact bound artifact.

    Never infer or fabricate indicator/MA parameters from ``strategy_family``.
    If no exact bound artifact or parameter payload is available, report
    ``status="not_provided"``.
    """
    persona_id = str(persona.get("persona_id") or persona.get("id") or "").strip()
    strategy_family = persona.get("strategy_family")
    metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
    rb_meta = (
        active_rb.get("metadata")
        if isinstance(active_rb, dict) and isinstance(active_rb.get("metadata"), dict)
        else {}
    )
    obj_store = rb_meta.get("object_store") if isinstance(rb_meta.get("object_store"), dict) else {}
    loader_att = (
        rb_meta.get("authoritative_loader_attestation")
        if isinstance(rb_meta.get("authoritative_loader_attestation"), dict)
        else {}
    )
    prov_readback = (
        metadata.get("provisioning_authoritative_readback")
        if isinstance(metadata.get("provisioning_authoritative_readback"), dict)
        else {}
    )

    bound_artifact_id = str(
        (active_rb or {}).get("strategy_artifact_id")
        or (active_rb or {}).get("artifact_id")
        or rb_meta.get("strategy_artifact_id")
        or rb_meta.get("artifact_id")
        or obj_store.get("artifact_id")
        or loader_att.get("artifact_id")
        or persona.get("strategy_artifact_id")
        or persona.get("artifact_id")
        or metadata.get("strategy_artifact_id")
        or metadata.get("artifact_id")
        or prov_readback.get("strategy_artifact_id")
        or prov_readback.get("artifact_id")
        or ""
    ).strip()

    bound_version = str(
        (active_rb or {}).get("strategy_version")
        or rb_meta.get("strategy_version")
        or obj_store.get("version")
        or obj_store.get("sha256")
        or loader_att.get("artifact_sha256")
        or persona.get("strategy_version")
        or metadata.get("strategy_version")
        or ""
    ).strip() or None

    candidate_params: Optional[Dict[str, Any]] = None
    source_ref: Optional[str] = None

    # 1. Check authoritative bound artifact in read_store if bound_artifact_id exists
    if bound_artifact_id and read_store is not None:
        for reader_name in ("read_registry_strategy_artifact_detail", "get_strategy_artifact"):
            reader = getattr(read_store, reader_name, None)
            if not callable(reader):
                continue
            try:
                art = reader(bound_artifact_id)
            except Exception:
                art = None
            if not isinstance(art, dict):
                continue
            art_tenant = str(art.get("tenant_id") or "").strip()
            if art_tenant and art_tenant != tenant_id:
                continue
            art_id = str(
                art.get("artifact_id") or art.get("registry_id") or art.get("id") or ""
            ).strip()
            if art_id and art_id != bound_artifact_id:
                continue
            art_persona = str(
                art.get("persona_id") or art.get("sponsor_persona_id") or ""
            ).strip()
            if art_persona and persona_id and art_persona != persona_id:
                continue
            bound_version = (
                str(art.get("version_id") or art.get("version") or art.get("sha256") or "").strip()
                or bound_version
            )
            for container in (
                art.get("parameters"),
                art.get("strategy_parameters"),
                (art.get("spec") if isinstance(art.get("spec"), dict) else {}).get("parameters"),
                (art.get("payload") if isinstance(art.get("payload"), dict) else {}).get("parameters"),
                (art.get("metadata") if isinstance(art.get("metadata"), dict) else {}).get("parameters"),
            ):
                if isinstance(container, dict) and container:
                    candidate_params = dict(container)
                    source_ref = f"strategy_artifact:{bound_artifact_id}"
                    break
            if candidate_params:
                break

    # 2. Check exact runtime binding loader attestation / object_store payload if still not resolved
    if candidate_params is None and bound_artifact_id:
        for container in (
            loader_att.get("parameters"),
            loader_att.get("strategy_parameters"),
            obj_store.get("parameters"),
            obj_store.get("strategy_parameters"),
            rb_meta.get("approved_strategy_parameters"),
        ):
            if isinstance(container, dict) and container:
                candidate_params = dict(container)
                source_ref = f"runtime_binding_artifact:{bound_artifact_id}"
                break

    if candidate_params is not None:
        return {
            "status": "bound_artifact_verified",
            "artifact_id": bound_artifact_id,
            "version": bound_version,
            "strategy_family": strategy_family,
            "parameters": json.loads(json.dumps(candidate_params, default=str)),
            "source_ref": source_ref,
        }

    return {
        "status": "not_provided",
        "artifact_id": bound_artifact_id or None,
        "version": bound_version,
        "strategy_family": strategy_family,
        "parameters": None,
        "source_ref": f"strategy_artifact:{bound_artifact_id}" if bound_artifact_id else None,
        "reason": (
            "exact_bound_artifact_parameters_not_provided"
            if bound_artifact_id
            else "no_bound_strategy_artifact_id"
        ),
        "guidance": (
            "Exact strategy parameters are not_provided in bounded operating evidence. "
            "Do not infer or fabricate indicator/MA parameters from strategy_family."
        ),
    }


def hydrate_persona_operating_context(
    *,
    persona: Dict[str, Any],
    read_store: Any,
    tenant_id: str,
    captured_at: str,
    evidence_cutoff: Optional[str] = None,
    authorization: Optional[str] = None,
) -> Dict[str, Any]:
    """Hydrate and freeze bounded operating evidence for a selected Persona.

    All reads are strictly scoped to ``(tenant_id, persona_id)`` and bounded to
    recent records. Missing or unavailable upstream surfaces are explicitly
    reported as ``not_provided`` / ``unavailable`` and never as proof of zero
    trades or positions.
    """
    persona_id = str(persona.get("persona_id") or persona.get("id") or "").strip()
    persona_tenant = str(persona.get("tenant_id") or "").strip()
    if not tenant_id or (persona_tenant and persona_tenant != tenant_id):
        raise ValueError("Persona tenant does not match the interaction tenant")

    existing = persona.get("operating_context")
    if (
        isinstance(existing, dict)
        and existing.get("schema_version") == "agora.persona-operating-context.v1"
        and str(existing.get("persona_id") or "") == persona_id
        and str(existing.get("tenant_id") or "") == tenant_id
    ):
        return json.loads(json.dumps(existing, default=str))

    cutoff = str(evidence_cutoff or captured_at or utc_timestamp()).strip()
    persona_version = _persona_version(persona)
    metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
    prov_readback = (
        metadata.get("provisioning_authoritative_readback")
        if isinstance(metadata.get("provisioning_authoritative_readback"), dict)
        else {}
    )

    candidate_rb_ids = {
        str(val).strip()
        for val in (
            persona.get("runtime_binding_id"),
            persona.get("runtime_id"),
            metadata.get("runtime_binding_id"),
            metadata.get("runtime_id"),
            prov_readback.get("runtime_binding_id"),
            prov_readback.get("runtime_id"),
        )
        if val and str(val).strip()
    }
    candidate_cap_binding_ids = {
        str(val).strip()
        for val in (
            persona.get("persona_capital_binding_id"),
            persona.get("binding_id"),
            metadata.get("persona_capital_binding_id"),
            metadata.get("binding_id"),
            prov_readback.get("persona_capital_binding_id"),
            prov_readback.get("binding_id"),
        )
        if val and str(val).strip()
    }
    candidate_pool_ids = {
        str(val).strip()
        for val in (
            persona.get("capital_pool_id"),
            persona.get("internal_paper_capital_pool_id"),
            persona.get("legacy_paper_capital_pool_id"),
            metadata.get("capital_pool_id"),
            metadata.get("internal_paper_capital_pool_id"),
            metadata.get("legacy_paper_capital_pool_id"),
            prov_readback.get("capital_pool_id"),
            prov_readback.get("internal_paper_capital_pool_id"),
        )
        if val and str(val).strip()
    }
    candidate_plan_ids = {
        str(val).strip()
        for val in (
            persona.get("deployment_plan_id"),
            persona.get("plan_id"),
            metadata.get("deployment_plan_id"),
            metadata.get("plan_id"),
            prov_readback.get("deployment_plan_id"),
            prov_readback.get("plan_id"),
        )
        if val and str(val).strip()
    }

    matched_rbs: List[Dict[str, Any]] = []
    rb_surface_status = "unavailable"
    if read_store is not None and callable(getattr(read_store, "list_runtime_bindings", None)):
        try:
            raw_rbs = read_store.list_runtime_bindings()
            rb_surface_status = "available"
        except Exception:
            raw_rbs = None
            rb_surface_status = "unavailable"
        if isinstance(raw_rbs, list):
            for rb in raw_rbs:
                if not isinstance(rb, dict):
                    continue
                rb_meta = rb.get("metadata") if isinstance(rb.get("metadata"), dict) else {}
                rb_tenant = str(rb.get("tenant_id") or rb_meta.get("tenant_id") or "").strip()
                if rb_tenant and rb_tenant != tenant_id:
                    continue
                rb_persona = str(
                    rb.get("persona_id")
                    or rb_meta.get("persona_id")
                    or rb_meta.get("sponsor_persona_id")
                    or ""
                ).strip()
                if rb_persona and rb_persona != persona_id:
                    continue
                rb_id = str(
                    rb.get("runtime_id") or rb.get("binding_id") or rb.get("id") or ""
                ).strip()
                rb_plan_id = str(rb.get("plan_id") or rb_meta.get("plan_id") or "").strip()
                rb_cap_binding_id = str(
                    rb.get("persona_capital_binding_id")
                    or rb_meta.get("persona_capital_binding_id")
                    or ""
                ).strip()
                rb_pool_id = str(
                    rb.get("capital_pool_id") or rb_meta.get("capital_pool_id") or ""
                ).strip()
                if (
                    rb_persona == persona_id
                    or (rb_id and rb_id in candidate_rb_ids)
                    or (rb_plan_id and rb_plan_id in candidate_plan_ids)
                    or (rb_cap_binding_id and rb_cap_binding_id in candidate_cap_binding_ids)
                    or (rb_pool_id and rb_pool_id in candidate_pool_ids)
                ):
                    matched_rbs.append(rb)

    active_rb: Optional[Dict[str, Any]] = None
    for rb in matched_rbs:
        st = str(rb.get("status") or rb.get("state") or "").strip().lower()
        if st in ("active", "running", "healthy", "paper_running"):
            active_rb = rb
            break
    if active_rb is None and matched_rbs:
        active_rb = matched_rbs[0]

    rb_meta = (
        active_rb.get("metadata")
        if isinstance(active_rb, dict) and isinstance(active_rb.get("metadata"), dict)
        else {}
    )
    active_runtime_id = (
        str(active_rb.get("runtime_id") or active_rb.get("binding_id") or active_rb.get("id") or "").strip()
        if isinstance(active_rb, dict)
        else ""
    )

    if active_rb is not None:
        runtime_binding_info: Dict[str, Any] = {
            "status": "resolved",
            "runtime_id": active_runtime_id or None,
            "plan_id": str(active_rb.get("plan_id") or rb_meta.get("plan_id") or "").strip() or None,
            "binding_status": str(active_rb.get("status") or active_rb.get("state") or "unknown").strip().lower(),
            "health_status": str(active_rb.get("health_status") or rb_meta.get("health_status") or "not_provided").strip().lower(),
            "environment": str(
                active_rb.get("environment") or active_rb.get("mode") or rb_meta.get("environment") or "paper"
            ).strip().lower(),
            "capital_pool_id": str(
                active_rb.get("capital_pool_id") or rb_meta.get("capital_pool_id") or ""
            ).strip() or None,
            "persona_capital_binding_id": str(
                active_rb.get("persona_capital_binding_id") or rb_meta.get("persona_capital_binding_id") or ""
            ).strip() or None,
            "updated_at": active_rb.get("updated_at") or active_rb.get("created_at"),
        }
    elif rb_surface_status == "unavailable":
        runtime_binding_info = {
            "status": "unavailable",
            "runtime_id": next(iter(sorted(candidate_rb_ids)), None),
            "reason": "runtime_binding_read_surface_unavailable",
        }
    else:
        runtime_binding_info = {
            "status": "none_bound",
            "runtime_id": None,
            "reason": "no_matching_runtime_binding_for_persona",
        }

    lifecycle_maturity = str(persona.get("lifecycle_state") or "unknown").strip().lower()
    paper_runtime_state = str(
        persona.get("paper_runtime_state")
        or metadata.get("paper_runtime_state")
        or (
            "paper_running"
            if runtime_binding_info.get("status") == "resolved"
            and runtime_binding_info.get("environment") == "paper"
            and runtime_binding_info.get("binding_status") in ("active", "running", "healthy", "paper_running")
            else "not_provided"
        )
    ).strip().lower()
    execution_mode = (
        str(runtime_binding_info.get("environment") or "paper")
        if runtime_binding_info.get("status") == "resolved"
        else ("paper" if paper_runtime_state in ("paper_running", "paper_active", "active") else "research")
    )
    operational_state = (
        "paper_running"
        if paper_runtime_state in ("paper_running", "paper_active")
        or (
            runtime_binding_info.get("status") == "resolved"
            and execution_mode == "paper"
            and runtime_binding_info.get("binding_status") in ("active", "running", "healthy", "paper_running")
        )
        else (paper_runtime_state if paper_runtime_state != "not_provided" else lifecycle_maturity)
    )
    lifecycle_and_execution = {
        "lifecycle_maturity": lifecycle_maturity,
        "execution_mode": execution_mode,
        "operational_state": operational_state,
        "paper_runtime_state": paper_runtime_state,
    }

    strategy_parameters_info = _extract_exact_bound_artifact_parameters(
        persona=persona,
        active_rb=active_rb,
        read_store=read_store,
        tenant_id=tenant_id,
    )

    telemetry_info: Dict[str, Any]
    telemetry_not_provided: List[str] = []
    telemetry_pos_refs: List[str] = []
    if active_runtime_id and read_store is not None and callable(getattr(read_store, "get_telemetry_summary", None)):
        try:
            raw_telem = read_store.get_telemetry_summary(active_runtime_id)
        except Exception:
            raw_telem = None
        if isinstance(raw_telem, dict):
            telem_tenant = str(raw_telem.get("tenant_id") or "").strip()
            telem_persona = str(raw_telem.get("persona_id") or "").strip()
            if (telem_tenant and telem_tenant != tenant_id) or (telem_persona and telem_persona != persona_id):
                telemetry_info = {
                    "status": "unavailable",
                    "runtime_id": active_runtime_id,
                    "positions": None,
                    "reason": "telemetry_scope_mismatch_excluded",
                }
            else:
                raw_positions = raw_telem.get("positions")
                bounded_positions: Optional[List[Dict[str, Any]]] = None
                if isinstance(raw_positions, list):
                    bounded_positions = []
                    for pos in raw_positions[:10]:
                        if not isinstance(pos, dict):
                            continue
                        bounded_positions.append({
                            "symbol": pos.get("symbol") or pos.get("instrument"),
                            "side": pos.get("side"),
                            "quantity": pos.get("quantity") if pos.get("quantity") is not None else pos.get("net_quantity"),
                            "avg_entry_price": pos.get("avg_entry_price") if pos.get("avg_entry_price") is not None else pos.get("entry_price"),
                            "mark_price": pos.get("mark_price") if pos.get("mark_price") is not None else raw_telem.get("mark_price"),
                            "mark_as_of": pos.get("mark_as_of") or raw_telem.get("mark_as_of") or raw_telem.get("updated_at"),
                            "unrealized_pnl": pos.get("unrealized_pnl"),
                            "realized_pnl": pos.get("realized_pnl"),
                            "snapshot_ref": pos.get("snapshot_ref") or pos.get("position_snapshot_ref"),
                        })
                        snap_ref = str(pos.get("snapshot_ref") or pos.get("position_snapshot_ref") or "").strip()
                        if snap_ref and snap_ref not in telemetry_pos_refs:
                            telemetry_pos_refs.append(snap_ref)
                else:
                    telemetry_not_provided.append("positions")

                for ref in _clean_ref_list(
                    raw_telem.get("position_snapshot_refs") or raw_telem.get("position_risk_snapshot_refs")
                ):
                    if ref not in telemetry_pos_refs:
                        telemetry_pos_refs.append(ref)

                tracked_metrics = (
                    "mark_price",
                    "last_fill",
                    "total_trades",
                    "pnl",
                    "realized_pnl",
                    "unrealized_pnl",
                    "return_percent",
                    "drawdown",
                    "fill_rate",
                )
                for field_name in tracked_metrics:
                    if raw_telem.get(field_name) is None:
                        telemetry_not_provided.append(field_name)

                telemetry_info = {
                    "status": "partial" if telemetry_not_provided else "complete",
                    "runtime_id": active_runtime_id,
                    "positions": bounded_positions,
                    "position_snapshot_refs": telemetry_pos_refs,
                    "mark_price": raw_telem.get("mark_price"),
                    "mark_as_of": raw_telem.get("mark_as_of") or raw_telem.get("updated_at"),
                    "mark_source_ref": raw_telem.get("mark_source_ref") or f"telemetry:{active_runtime_id}",
                    "last_fill": raw_telem.get("last_fill"),
                    "total_trades": raw_telem.get("total_trades"),
                    "pnl": raw_telem.get("pnl"),
                    "realized_pnl": raw_telem.get("realized_pnl"),
                    "unrealized_pnl": raw_telem.get("unrealized_pnl"),
                    "return_percent": raw_telem.get("return_percent"),
                    "drawdown": raw_telem.get("drawdown"),
                    "fill_rate": raw_telem.get("fill_rate"),
                    "updated_at": raw_telem.get("updated_at"),
                    "not_provided_fields": sorted(set(telemetry_not_provided)),
                }
        else:
            telemetry_info = {
                "status": "unavailable",
                "runtime_id": active_runtime_id,
                "positions": None,
                "position_snapshot_refs": [],
                "not_provided_fields": ["positions", "mark_price", "last_fill", "pnl", "realized_pnl", "return_percent"],
                "reason": "telemetry_summary_not_found",
            }
    else:
        telemetry_info = {
            "status": "unavailable",
            "runtime_id": active_runtime_id or None,
            "positions": None,
            "position_snapshot_refs": [],
            "not_provided_fields": ["positions", "mark_price", "last_fill", "pnl", "realized_pnl", "return_percent"],
            "reason": "no_bound_runtime_telemetry",
        }

    # Hydrate bounded recent trade journal episodes for the selected Persona
    journal_info: Dict[str, Any]
    store_episode_reader = (
        getattr(read_store, "read_persona_operating_episodes", None)
        or getattr(read_store, "list_persona_trade_episodes", None)
        if read_store is not None
        else None
    )
    if callable(store_episode_reader):
        try:
            raw_journal = store_episode_reader(
                persona_id,
                tenant_id=tenant_id,
                limit=5,
                evidence_cutoff=cutoff,
            )
        except TypeError:
            raw_journal = store_episode_reader(persona_id)
        if isinstance(raw_journal, dict) and "episodes" in raw_journal:
            journal_info = json.loads(json.dumps(raw_journal, default=str))
        elif isinstance(raw_journal, list):
            from services.control_plane.bff.trade_journal import _normalize_episode_for_persona

            norm_eps = []
            for item in raw_journal:
                if not isinstance(item, dict):
                    continue
                norm = _normalize_episode_for_persona(
                    item,
                    persona_id=persona_id,
                    tenant_id=tenant_id,
                    evidence_cutoff=cutoff,
                )
                if norm is not None:
                    norm_eps.append(norm)
            norm_eps = norm_eps[:5]
            journal_info = {
                "status": (
                    "empty"
                    if not norm_eps
                    else (
                        "partial"
                        if any(ep.get("not_provided_fields") for ep in norm_eps)
                        else "complete"
                    )
                ),
                "source": "read_store.persona_trade_episodes",
                "persona_id": persona_id,
                "tenant_id": tenant_id,
                "evidence_cutoff": cutoff,
                "episode_count": len(norm_eps),
                "open_episode_count": sum(
                    1 for ep in norm_eps if str(ep.get("status") or "").lower() == "open"
                ),
                "episodes": norm_eps,
                "reason": None if norm_eps else "no_persona_episodes_returned",
            }
        else:
            journal_info = {
                "status": "unavailable",
                "source": "read_store.persona_trade_episodes",
                "persona_id": persona_id,
                "tenant_id": tenant_id,
                "evidence_cutoff": cutoff,
                "episode_count": None,
                "open_episode_count": None,
                "episodes": [],
                "reason": "invalid_persona_episode_payload",
            }
    else:
        from services.control_plane.bff.trade_journal import read_persona_operating_episodes

        journal_info = read_persona_operating_episodes(
            persona_id,
            authorization,
            tenant_id,
            limit=5,
            evidence_cutoff=cutoff,
        )

    owner_source_refs: List[Dict[str, Optional[str]]] = [
        {"kind": "persona", "id": persona_id, "version": persona_version},
    ]
    if active_runtime_id:
        owner_source_refs.append({
            "kind": "performance_window",
            "id": f"runtime_telemetry:{active_runtime_id}",
            "version": str(telemetry_info.get("updated_at") or runtime_binding_info.get("updated_at") or cutoff),
        })
    for ep in journal_info.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        ep_id = str(ep.get("episode_id") or "").strip()
        if ep_id:
            owner_source_refs.append({
                "kind": "journal_entry",
                "id": ep_id,
                "version": str(ep.get("updated_at") or ep.get("opened_at") or cutoff),
            })

    position_snapshot_refs: List[str] = list(telemetry_pos_refs)
    for ep in journal_info.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        for ref in _clean_ref_list(ep.get("position_snapshot_refs")):
            if ref not in position_snapshot_refs:
                position_snapshot_refs.append(ref)

    all_not_provided: List[str] = list(telemetry_info.get("not_provided_fields") or [])
    if strategy_parameters_info.get("status") != "bound_artifact_verified":
        all_not_provided.append("strategy_parameters")
    for ep in journal_info.get("episodes") or []:
        if isinstance(ep, dict):
            for field_name in ep.get("not_provided_fields") or []:
                all_not_provided.append(f"trade_journal.{field_name}")

    return {
        "schema_version": "agora.persona-operating-context.v1",
        "persona_id": persona_id,
        "persona_version": persona_version,
        "tenant_id": tenant_id,
        "captured_at": captured_at,
        "evidence_cutoff": cutoff,
        "lifecycle_and_execution": lifecycle_and_execution,
        "runtime_binding": runtime_binding_info,
        "strategy_parameters": strategy_parameters_info,
        "telemetry_and_positions": telemetry_info,
        "trade_journal": journal_info,
        "owner_source_refs": owner_source_refs,
        "position_snapshot_refs": position_snapshot_refs[:20],
        "coverage": {
            "runtime_binding": runtime_binding_info.get("status"),
            "strategy_parameters": strategy_parameters_info.get("status"),
            "telemetry_and_positions": telemetry_info.get("status"),
            "trade_journal": journal_info.get("status"),
            "not_provided_fields": sorted(set(all_not_provided)),
            "missing_semantics": "not_provided_or_unavailable_never_proves_zero_records",
        },
        "authority": authority_boundary(),
    }


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
    operating_context: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, Any]]:
    metadata = persona_profile.get("metadata") if isinstance(persona_profile.get("metadata"), dict) else {}
    resolved_operating_context = (
        operating_context
        if isinstance(operating_context, dict)
        else (
            persona_profile.get("operating_context")
            if isinstance(persona_profile.get("operating_context"), dict)
            else None
        )
    )
    frozen_persona_profile: Dict[str, Any] = {
        "persona_id": participant["persona_id"],
        "persona_version": participant["persona_version"],
        "display_name": participant["display_name"],
        "mandate": persona_profile.get("mandate"),
        "archetype": persona_profile.get("archetype") or metadata.get("archetype"),
        "strategy_family": persona_profile.get("strategy_family"),
        "traits": persona_profile.get("traits") or metadata.get("traits") or {},
    }
    if resolved_operating_context is not None:
        frozen_persona_profile["operating_context"] = resolved_operating_context
    context_pack: Dict[str, Any] = {
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
    if resolved_operating_context is not None:
        context_pack["operating_context"] = resolved_operating_context
    exact_context = json.dumps(context_pack, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    schema = provider_output_shape()
    prompt = (
        "Return exactly one JSON object and no markdown. Act only as the frozen selected Persona. "
        "Do not call tools or read/write memory. Do not submit orders, call brokers, change capital, "
        "bind runtime, promote lifecycle, mutate policy, or claim execution. "
        "Ground any claims about your positions, fills, trade episodes, PnL, mark prices, runtime state, "
        "or strategy parameters strictly in IMMUTABLE_TYPED_CONTEXT.operating_context; when a surface or "
        "field is marked not_provided or unavailable, state that it is not_provided/unavailable rather "
        "than claiming no trades or positions exist, and never invent indicator/MA parameters from "
        "strategy_family. "
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
