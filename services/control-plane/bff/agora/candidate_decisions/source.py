"""Build a governed candidate from one persisted Persona recommendation."""
from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any, Mapping

from ..interaction.provider import (
    RecommendedMeasure,
    authority_boundary,
    recommended_measure_sha256,
)
from .models import CandidateFromMeasureCommand, canonical_sha256


_MEASURE_TO_PROPOSAL_TYPE = {
    "strategy_parameter_change": "strategy_patch",
    "condition_change": "condition_change",
    "risk_limit_recommendation": "risk_limit_recommendation",
    "research_request": "research_request",
    "paper_candidate_request": "paper_candidate_request",
    "allocation_review_request": "allocation_review_request",
    "containment_recommendation": "containment_recommendation",
    "journal_lesson": "journal_lesson",
    "memory_candidate": "memory_candidate",
    "persona_mutation_review": "persona_mutation_review",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _interaction_tenant(interaction: Mapping[str, Any]) -> str:
    context = interaction.get("context_snapshot")
    if not isinstance(context, Mapping):
        context = {}
    return _clean(interaction.get("tenant_id") or context.get("tenant_id"))


def _find_unique(rows: Any, field: str, expected: str, label: str) -> Mapping[str, Any]:
    matches = [
        row for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, Mapping) and _clean(row.get(field)) == expected
    ]
    if len(matches) != 1:
        raise ValueError(f"persisted {label} binding is missing or ambiguous")
    return matches[0]


def build_candidate_from_persisted_measure(
    *,
    interaction: Mapping[str, Any],
    command: CandidateFromMeasureCommand,
    tenant_id: str,
    owner_user_id: str,
    proposer_id: str,
    now: datetime,
    expires_at: datetime,
) -> dict[str, Any]:
    """Freeze the exact persisted opinion/measure as candidate revision one.

    Human request/topic text is intentionally never read by this function.
    """

    if _clean(interaction.get("interaction_id")) != command.interaction_id:
        raise ValueError("interaction id does not match persisted interaction")
    if _interaction_tenant(interaction) != tenant_id:
        raise ValueError("interaction tenant does not match candidate tenant")
    if not owner_user_id or not proposer_id:
        raise ValueError("candidate owner and proposer are required")
    if now.tzinfo is None or expires_at.tzinfo is None or expires_at <= now:
        raise ValueError("candidate expiry must be a future timezone-aware time")

    opinion = _find_unique(interaction.get("opinions"), "opinion_id", command.opinion_id, "opinion")
    if _clean(opinion.get("interaction_id")) != command.interaction_id:
        raise ValueError("opinion interaction binding does not match")
    provenance = opinion.get("provenance") if isinstance(opinion.get("provenance"), Mapping) else {}
    if (
        provenance.get("content_origin") != "selected_persona_provider_response"
        or provenance.get("provider_kind") != "openclaw"
        or provenance.get("request_correlated") is not True
        or provenance.get("response_correlated") is not True
        or provenance.get("canned_template") is not False
        or provenance.get("magic_topic_trigger") is not False
        or provenance.get("simulation") is not False
    ):
        raise ValueError("opinion is not a persisted selected-Persona provider result")

    measure_raw = _find_unique(
        opinion.get("recommended_measures"), "measure_id", command.measure_id, "recommended measure"
    )
    persisted_measure = dict(measure_raw)
    persisted_measure_sha = _clean(persisted_measure.pop("measure_sha256", ""))
    measure = RecommendedMeasure.model_validate(persisted_measure).model_dump(mode="json")
    measure_sha = recommended_measure_sha256(measure)
    if not persisted_measure_sha or measure_sha != persisted_measure_sha:
        raise ValueError("persisted recommended measure server digest is missing or invalid")
    opinion_sha = canonical_sha256(dict(opinion))
    participant = opinion.get("participant") if isinstance(opinion.get("participant"), Mapping) else {}
    persona_id = _clean(participant.get("persona_id"))
    persona_version = _clean(participant.get("persona_version"))
    invocation_id = _clean(opinion.get("provider_invocation_id"))
    if not persona_id or not persona_version or not invocation_id:
        raise ValueError("opinion Persona/provider provenance is incomplete")
    if _clean(provenance.get("provider_invocation_id")) != invocation_id:
        raise ValueError("opinion provider invocation binding does not match provenance")
    if opinion.get("authority") != authority_boundary():
        raise ValueError("opinion must carry the exact no-authority boundary")

    target = measure["target"]
    record: dict[str, Any] = {
        "proposal_id": f"prop_{uuid.uuid4().hex}",
        "revision": 1,
        "state": "draft",
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "proposer_id": proposer_id,
        "interaction_id": command.interaction_id,
        "opinion_id": command.opinion_id,
        "opinion_sha256": opinion_sha,
        "provider_invocation_id": invocation_id,
        "persona_id": persona_id,
        "persona_version": persona_version,
        "measure_id": command.measure_id,
        "measure_sha256": measure_sha,
        "source_measure": copy.deepcopy(measure),
        "proposal_type": _MEASURE_TO_PROPOSAL_TYPE[measure["measure_type"]],
        "target_kind": target["kind"],
        "target_id": target["id"],
        "target_version": target["version"],
        "target_path": target.get("path"),
        "current_value": copy.deepcopy(measure.get("current_value")),
        "proposed_value": copy.deepcopy(measure["proposed_value"]),
        "rationale": measure["rationale"],
        "expected_benefit": measure["expected_benefit"],
        "adverse_scenarios": copy.deepcopy(measure["adverse_scenarios"]),
        "confidence": measure["confidence"],
        "evidence_refs": copy.deepcopy(measure["evidence_refs"]),
        "environment_ceiling": measure["environment_ceiling"],
        "validation_plan": copy.deepcopy(measure["validation_plan"]),
        "rollback_trigger": measure["rollback_trigger"],
        "rollback_action": measure["rollback_action"],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "execution_authority": "none",
        "authority": authority_boundary(),
        "audit": [
            {
                "audit_ref": f"audit_{uuid.uuid4().hex}",
                "action": "created_from_recommended_measure",
                "actor_id": proposer_id,
                "at": now.isoformat(),
                "interaction_id": command.interaction_id,
                "opinion_id": command.opinion_id,
                "opinion_sha256": opinion_sha,
                "measure_id": command.measure_id,
                "measure_sha256": measure_sha,
            }
        ],
    }
    record["proposal_digest"] = candidate_digest(record)
    return record


_DIGEST_FIELDS = (
    "proposal_id",
    "revision",
    "state",
    "tenant_id",
    "owner_user_id",
    "proposer_id",
    "interaction_id",
    "opinion_id",
    "opinion_sha256",
    "provider_invocation_id",
    "persona_id",
    "persona_version",
    "measure_id",
    "measure_sha256",
    "proposal_type",
    "target_kind",
    "target_id",
    "target_version",
    "target_path",
    "current_value",
    "proposed_value",
    "rationale",
    "evidence_refs",
    "environment_ceiling",
    "validation_plan",
    "rollback_trigger",
    "rollback_action",
    "expires_at",
)


def candidate_digest(record: Mapping[str, Any]) -> str:
    return canonical_sha256({field: record.get(field) for field in _DIGEST_FIELDS})
