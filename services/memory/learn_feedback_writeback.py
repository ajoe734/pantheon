"""Learn feedback writeback adapter for persona and institutional memory."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional
from uuid import NAMESPACE_URL, uuid5

from .institutional_memory_store import (
    InstitutionalMemoryEntry,
    InstitutionalMemoryStore,
    KnowledgeType,
    Scope,
    SourceEventType,
    WriteAuthority,
    utc_now,
)
from .persona_memory_store import (
    PersonaMemoryEntry,
    PersonaMemoryStore,
    PersonaMemoryType,
    PersonaRelevanceScope,
)


class LearnFeedbackWritebackError(ValueError):
    """Raised when a Learn feedback writeback payload is malformed."""


class LearnFeedbackUnauthorizedError(PermissionError):
    """Raised when a source service is not authorized to write Learn feedback."""


_SOURCE_WRITE_AUTHORITY = {
    SourceEventType.RUNTIME_TELEMETRY_OUTCOME.value: WriteAuthority.TELEMETRY_SVC.value,
    SourceEventType.POSTMORTEM_PUBLISHED.value: WriteAuthority.INCIDENT_SVC.value,
    SourceEventType.EVOLUTION_DECISION_APPROVED.value: WriteAuthority.EVOLUTION_SVC.value,
    SourceEventType.RESEARCH_FINDING_PUBLISHED.value: WriteAuthority.RESEARCH_SVC.value,
    SourceEventType.RESEARCH_TASK_COMPLETED.value: WriteAuthority.RESEARCH_SVC.value,
}

_PERSONA_MEMORY_TYPE = {
    SourceEventType.RUNTIME_TELEMETRY_OUTCOME.value: PersonaMemoryType.RISK_OBSERVATION.value,
    SourceEventType.POSTMORTEM_PUBLISHED.value: PersonaMemoryType.STRATEGY_LESSON.value,
    SourceEventType.EVOLUTION_DECISION_APPROVED.value: PersonaMemoryType.STRATEGY_LESSON.value,
    SourceEventType.RESEARCH_FINDING_PUBLISHED.value: PersonaMemoryType.STRATEGY_LESSON.value,
    SourceEventType.RESEARCH_TASK_COMPLETED.value: PersonaMemoryType.STRATEGY_LESSON.value,
}

_INSTITUTIONAL_KNOWLEDGE_TYPE = {
    SourceEventType.RUNTIME_TELEMETRY_OUTCOME.value: KnowledgeType.CROSS_PERSONA_OBSERVATION.value,
    SourceEventType.POSTMORTEM_PUBLISHED.value: KnowledgeType.INCIDENT_LESSON.value,
    SourceEventType.EVOLUTION_DECISION_APPROVED.value: KnowledgeType.EVOLUTION_RATIONALE.value,
    SourceEventType.RESEARCH_FINDING_PUBLISHED.value: KnowledgeType.RESEARCH_FINDING.value,
    SourceEventType.RESEARCH_TASK_COMPLETED.value: KnowledgeType.RESEARCH_FINDING.value,
}


def write_learn_feedback(
    payload: Mapping[str, Any],
    *,
    persona_store: PersonaMemoryStore,
    institutional_store: InstitutionalMemoryStore,
) -> Dict[str, Any]:
    """Write one source event into persona and sponsor-attributed memory."""
    normalized = _normalize_payload(payload)
    existing_persona_entries = persona_store.find_by_source_event(
        source_event_type=normalized["source_event_type"],
        source_event_id=normalized["source_event_id"],
        write_authority=normalized["write_authority"],
        active_only=False,
    )
    existing_institutional_entries = institutional_store.find_by_source_event(
        source_event_type=normalized["source_event_type"],
        source_event_id=normalized["source_event_id"],
        write_authority=normalized["write_authority"],
        active_only=False,
    )
    if existing_persona_entries or existing_institutional_entries:
        return _result(
            created=False,
            normalized=normalized,
            persona_entries=existing_persona_entries,
            institutional_entries=existing_institutional_entries,
        )

    persona_entries = _build_persona_entries(normalized)
    institutional_entry = _build_institutional_entry(normalized)

    saved_institutional = institutional_store.create(institutional_entry)
    saved_persona = [persona_store.create(entry) for entry in persona_entries]

    # Handle supersession of prior entries if declared
    for target_ref in normalized.get("supersedes") or []:
        target_str = str(target_ref).strip()
        if not target_str:
            continue
        try:
            institutional_store.supersede(target_str, saved_institutional.entry_id)
        except Exception:
            pass
        # Also check by source_event_id
        for prior_inst in institutional_store.find_by_source_event(
            source_event_type=normalized["source_event_type"],
            source_event_id=target_str,
            active_only=True,
        ):
            if prior_inst.entry_id != saved_institutional.entry_id:
                try:
                    institutional_store.supersede(prior_inst.entry_id, saved_institutional.entry_id)
                except Exception:
                    pass
        if saved_persona:
            try:
                persona_store.supersede(target_str, saved_persona[0].memory_id)
            except Exception:
                pass
            for prior_p in persona_store.find_by_source_event(
                source_event_type=normalized["source_event_type"],
                source_event_id=target_str,
                active_only=True,
            ):
                if prior_p.memory_id not in [p.memory_id for p in saved_persona]:
                    try:
                        persona_store.supersede(prior_p.memory_id, saved_persona[0].memory_id)
                    except Exception:
                        pass

    return _result(
        created=True,
        normalized=normalized,
        persona_entries=saved_persona,
        institutional_entries=[saved_institutional],
    )


def _normalize_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LearnFeedbackWritebackError("payload must be an object")

    source_event_type = _required_text(payload.get("source_event_type"), "source_event_type")
    source_event_id = _required_text(payload.get("source_event_id"), "source_event_id")
    write_authority = _required_text(payload.get("write_authority"), "write_authority")
    sponsor_persona_id = _required_text(payload.get("sponsor_persona_id"), "sponsor_persona_id")
    summary = _required_text(
        payload.get("summary")
        or payload.get("feedback_summary")
        or payload.get("outcome_summary")
        or payload.get("headline"),
        "summary",
    )

    if source_event_type not in _SOURCE_WRITE_AUTHORITY:
        raise LearnFeedbackWritebackError(f"Unsupported source_event_type: {source_event_type!r}")
    required_authority = _SOURCE_WRITE_AUTHORITY[source_event_type]
    if write_authority != required_authority:
        raise LearnFeedbackUnauthorizedError(
            f"{write_authority!r} cannot write Learn feedback for {source_event_type!r}"
        )

    # License and allowed-use enforcement
    allowed_use = _string_list(payload.get("allowed_use") or [], "allowed_use")
    if allowed_use:
        prohibited_terms = {"no_derivative", "no_derivatives", "no_memory", "raw_only"}
        if any(term in [u.lower().strip() for u in allowed_use] for term in prohibited_terms):
            raise LearnFeedbackWritebackError("license allowed_use prohibits derived memory writeback")
    license_scope = payload.get("license_scope")
    if license_scope:
        if str(license_scope).lower().strip() in {"prohibited", "no_derivative", "no_derivatives"}:
            raise LearnFeedbackWritebackError(f"license_scope '{license_scope}' does not permit derived memory writeback")

    evidence_refs = _evidence_refs_list(payload.get("evidence_refs") or [], "evidence_refs")
    dataset_refs = _string_list(payload.get("dataset_refs") or [], "dataset_refs")
    runtime_telemetry_evidence = _object_list(
        payload.get("runtime_telemetry_evidence") or payload.get("telemetry_evidence") or [],
        "runtime_telemetry_evidence",
    )
    if not runtime_telemetry_evidence and evidence_refs:
        runtime_telemetry_evidence = [
            ref if isinstance(ref, dict) else {"type": "evidence", "ref": ref}
            for ref in evidence_refs
        ]
    if not runtime_telemetry_evidence and dataset_refs:
        runtime_telemetry_evidence = [{"type": "dataset", "ref": ref} for ref in dataset_refs]
    if not runtime_telemetry_evidence and not evidence_refs and not dataset_refs:
        raise LearnFeedbackWritebackError("runtime_telemetry_evidence, evidence_refs, or dataset_refs is required")

    top_level_persona_ids = _string_list(payload.get("contributing_persona_ids") or [], "contributing_persona_ids")
    raw_contributor_feedback = payload.get("contributor_feedback") or []
    if not isinstance(raw_contributor_feedback, list):
        raise LearnFeedbackWritebackError("contributor_feedback must be a list")

    contributor_persona_ids = [
        _required_text(item.get("persona_id"), "contributor_feedback[].persona_id")
        for item in raw_contributor_feedback
        if isinstance(item, Mapping)
    ]
    if len(contributor_persona_ids) != len(raw_contributor_feedback):
        raise LearnFeedbackWritebackError("contributor_feedback entries must be objects")

    contributing_persona_ids = _dedupe([*top_level_persona_ids, *contributor_persona_ids])
    is_research_event = source_event_type in (
        SourceEventType.RESEARCH_FINDING_PUBLISHED.value,
        SourceEventType.RESEARCH_TASK_COMPLETED.value,
    )
    if not contributing_persona_ids and is_research_event and sponsor_persona_id:
        contributing_persona_ids = [sponsor_persona_id]
    if not contributing_persona_ids:
        raise LearnFeedbackWritebackError("at least one contributing persona id is required")

    proposal_ids_by_persona = _proposal_ids_by_persona(payload.get("proposal_ids_by_persona") or {})
    top_level_proposal_ids = _string_list(payload.get("proposal_ids") or [], "proposal_ids")
    contributor_feedback = _normalize_contributor_feedback(
        raw_contributor_feedback,
        contributing_persona_ids=contributing_persona_ids,
        proposal_ids_by_persona=proposal_ids_by_persona,
        top_level_proposal_ids=top_level_proposal_ids,
        default_summary=summary,
    )

    institutional_persona_ids = _dedupe([sponsor_persona_id, *contributing_persona_ids])
    scope = str(payload.get("scope") or Scope.SYSTEM_WIDE.value).strip() or Scope.SYSTEM_WIDE.value
    scope_filter = payload.get("scope_filter")
    if scope == Scope.SYSTEM_WIDE.value:
        scope_filter = None

    confidence = payload.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (ValueError, TypeError):
            confidence = 1.0

    supersedes = _string_list(payload.get("supersedes") or [], "supersedes")
    contradicts = _string_list(payload.get("contradicts") or [], "contradicts")
    expires_at = str(payload.get("expires_at")).strip() if payload.get("expires_at") else None
    trace_id = str(payload.get("trace_id") or source_event_id).strip()

    return {
        "source_event_type": source_event_type,
        "source_event_id": source_event_id,
        "write_authority": write_authority,
        "sponsor_persona_id": sponsor_persona_id,
        "contributing_persona_ids": contributing_persona_ids,
        "institutional_persona_ids": institutional_persona_ids,
        "summary": summary,
        "headline": str(payload.get("headline") or f"Learn feedback from {source_event_type}"),
        "body": str(payload.get("body") or summary),
        "runtime_telemetry_evidence": runtime_telemetry_evidence,
        "evidence_refs": evidence_refs or runtime_telemetry_evidence,
        "dataset_refs": dataset_refs,
        "license_scope": str(license_scope).strip() if license_scope else None,
        "allowed_use": allowed_use,
        "confidence": confidence,
        "supersedes": supersedes,
        "contradicts": contradicts,
        "expires_at": expires_at,
        "trace_id": trace_id,
        "contributor_feedback": contributor_feedback,
        "proposal_ids": _dedupe(
            [
                *top_level_proposal_ids,
                *[pid for item in contributor_feedback for pid in item["proposal_ids"]],
            ]
        ),
        "written_at": str(payload.get("written_at") or utc_now()),
        "scope": scope,
        "scope_filter": str(scope_filter).strip() if scope_filter is not None and str(scope_filter).strip() else None,
        "tags": _dedupe(
            [
                "learn_feedback",
                source_event_type,
                *(_string_list(payload.get("tags") or [], "tags")),
            ]
        ),
    }


def _normalize_contributor_feedback(
    raw_items: List[Any],
    *,
    contributing_persona_ids: List[str],
    proposal_ids_by_persona: Mapping[str, List[str]],
    top_level_proposal_ids: List[str],
    default_summary: str,
) -> List[Dict[str, Any]]:
    by_persona: Dict[str, Dict[str, Any]] = {}
    for raw in raw_items:
        persona_id = _required_text(raw.get("persona_id"), "contributor_feedback[].persona_id")
        proposal_ids = _proposal_ids_from(raw)
        by_persona[persona_id] = {
            "persona_id": persona_id,
            "summary": str(raw.get("summary") or default_summary),
            "proposal_ids": proposal_ids or proposal_ids_by_persona.get(persona_id, []) or top_level_proposal_ids,
            "tags": _string_list(raw.get("tags") or [], "contributor_feedback[].tags"),
        }

    for persona_id in contributing_persona_ids:
        by_persona.setdefault(
            persona_id,
            {
                "persona_id": persona_id,
                "summary": default_summary,
                "proposal_ids": proposal_ids_by_persona.get(persona_id, []) or top_level_proposal_ids,
                "tags": [],
            },
        )

    return [by_persona[persona_id] for persona_id in contributing_persona_ids]


def _build_persona_entries(normalized: Mapping[str, Any]) -> List[PersonaMemoryEntry]:
    entries: List[PersonaMemoryEntry] = []
    for feedback in normalized["contributor_feedback"]:
        memory_id = _stable_id(
            "pmem",
            "persona",
            normalized["source_event_type"],
            normalized["source_event_id"],
            feedback["persona_id"],
        )
        tags = _dedupe([*normalized["tags"], *feedback["tags"], feedback["persona_id"]])
        structured_payload = {
            "source_event_type": normalized["source_event_type"],
            "source_event_id": normalized["source_event_id"],
            "sponsor_persona_id": normalized["sponsor_persona_id"],
            "contributing_persona_ids": normalized["contributing_persona_ids"],
            "proposal_ids": feedback["proposal_ids"],
            "runtime_telemetry_evidence": normalized["runtime_telemetry_evidence"],
            "evidence_refs": normalized["evidence_refs"],
        }
        if normalized.get("dataset_refs"):
            structured_payload["dataset_refs"] = normalized["dataset_refs"]
        if normalized.get("license_scope"):
            structured_payload["license_scope"] = normalized["license_scope"]
        if normalized.get("allowed_use"):
            structured_payload["allowed_use"] = normalized["allowed_use"]
        if normalized.get("confidence") is not None:
            structured_payload["confidence"] = normalized["confidence"]
        if normalized.get("supersedes"):
            structured_payload["supersedes"] = normalized["supersedes"]
        if normalized.get("contradicts"):
            structured_payload["contradicts"] = normalized["contradicts"]
        if normalized.get("expires_at"):
            structured_payload["expires_at"] = normalized["expires_at"]
        if normalized.get("trace_id"):
            structured_payload["trace_id"] = normalized["trace_id"]
        entries.append(
            PersonaMemoryEntry(
                memory_id=memory_id,
                persona_id=feedback["persona_id"],
                memory_type=_PERSONA_MEMORY_TYPE[normalized["source_event_type"]],
                content={
                    "summary": feedback["summary"],
                    "structured_payload": structured_payload,
                    "tags": tags,
                },
                source_event_type=normalized["source_event_type"],
                source_event_id=normalized["source_event_id"],
                written_at=normalized["written_at"],
                write_authority=normalized["write_authority"],
                relevance_scope=PersonaRelevanceScope.PERSONA_AND_COMMITTEE.value,
            )
        )
    return entries


def _build_institutional_entry(normalized: Mapping[str, Any]) -> InstitutionalMemoryEntry:
    entry_id = _stable_id(
        "mem",
        "institutional",
        normalized["source_event_type"],
        normalized["source_event_id"],
    )
    structured_payload = {
        "source_event_type": normalized["source_event_type"],
        "source_event_id": normalized["source_event_id"],
        "sponsor_persona_id": normalized["sponsor_persona_id"],
        "contributing_persona_ids": normalized["contributing_persona_ids"],
        "proposal_ids": normalized["proposal_ids"],
        "contributor_feedback": normalized["contributor_feedback"],
        "runtime_telemetry_evidence": normalized["runtime_telemetry_evidence"],
        "evidence_refs": normalized["evidence_refs"],
    }
    if normalized.get("dataset_refs"):
        structured_payload["dataset_refs"] = normalized["dataset_refs"]
    if normalized.get("license_scope"):
        structured_payload["license_scope"] = normalized["license_scope"]
    if normalized.get("allowed_use"):
        structured_payload["allowed_use"] = normalized["allowed_use"]
    if normalized.get("confidence") is not None:
        structured_payload["confidence"] = normalized["confidence"]
    if normalized.get("supersedes"):
        structured_payload["supersedes"] = normalized["supersedes"]
    if normalized.get("contradicts"):
        structured_payload["contradicts"] = normalized["contradicts"]
    if normalized.get("expires_at"):
        structured_payload["expires_at"] = normalized["expires_at"]
    if normalized.get("trace_id"):
        structured_payload["trace_id"] = normalized["trace_id"]
    return InstitutionalMemoryEntry(
        entry_id=entry_id,
        knowledge_type=_INSTITUTIONAL_KNOWLEDGE_TYPE[normalized["source_event_type"]],
        content={
            "headline": normalized["headline"],
            "body": normalized["body"],
            "structured_payload": structured_payload,
            "tags": normalized["tags"],
        },
        source_event_type=normalized["source_event_type"],
        source_event_id=normalized["source_event_id"],
        written_at=normalized["written_at"],
        write_authority=normalized["write_authority"],
        scope=normalized["scope"],
        scope_filter=normalized["scope_filter"],
        expires_at=normalized.get("expires_at"),
        contributing_persona_ids=normalized["institutional_persona_ids"],
    )


def _result(
    *,
    created: bool,
    normalized: Mapping[str, Any],
    persona_entries: Iterable[PersonaMemoryEntry],
    institutional_entries: Iterable[InstitutionalMemoryEntry],
) -> Dict[str, Any]:
    persona_ids = [entry.memory_id for entry in persona_entries]
    institutional_ids = [entry.entry_id for entry in institutional_entries]
    return {
        "created": created,
        "source_event_type": normalized["source_event_type"],
        "source_event_id": normalized["source_event_id"],
        "sponsor_persona_id": normalized["sponsor_persona_id"],
        "contributing_persona_ids": normalized["contributing_persona_ids"],
        "persona_memory_ids": persona_ids,
        "institutional_entry_id": institutional_ids[0] if institutional_ids else None,
        "institutional_entry_ids": institutional_ids,
    }


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise LearnFeedbackWritebackError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise LearnFeedbackWritebackError(f"{field_name} must not be empty")
    return text


def _string_list(value: Any, field_name: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raise LearnFeedbackWritebackError(f"{field_name} must be a list")
    return _dedupe(str(item).strip() for item in raw_items if str(item).strip())


def _evidence_refs_list(value: Any, field_name: str) -> List[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise LearnFeedbackWritebackError(f"{field_name} must be a list")
    result: List[Any] = []
    for item in value:
        if isinstance(item, Mapping):
            result.append(dict(item))
        elif isinstance(item, str) and item.strip():
            result.append(item.strip())
        else:
            raise LearnFeedbackWritebackError(f"{field_name} entries must be objects or strings")
    return result


def _object_list(value: Any, field_name: str) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise LearnFeedbackWritebackError(f"{field_name} must be a list")
    if any(not isinstance(item, Mapping) for item in value):
        raise LearnFeedbackWritebackError(f"{field_name} entries must be objects")
    return [dict(item) for item in value]


def _proposal_ids_by_persona(value: Any) -> Dict[str, List[str]]:
    if not isinstance(value, Mapping):
        raise LearnFeedbackWritebackError("proposal_ids_by_persona must be an object")
    return {
        str(persona_id).strip(): _string_list(proposal_ids, "proposal_ids_by_persona[]")
        for persona_id, proposal_ids in value.items()
        if str(persona_id).strip()
    }


def _proposal_ids_from(value: Mapping[str, Any]) -> List[str]:
    proposal_ids = _string_list(value.get("proposal_ids") or [], "contributor_feedback[].proposal_ids")
    proposal_id = value.get("proposal_id")
    if proposal_id is not None and str(proposal_id).strip():
        proposal_ids.insert(0, str(proposal_id).strip())
    return _dedupe(proposal_ids)


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "pantheon:learn-feedback:" + "|".join(str(part) for part in parts)
    return f"{prefix}-{uuid5(NAMESPACE_URL, raw)}"


def _dedupe(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
