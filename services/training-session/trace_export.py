"""Export trainer TeachingEvent traces into imitation training datasets.

TRN-007 bridge module. This code is intentionally downstream of the
TeachingSession / TeachingEvent schemas: it reads the stream and projects it
into research-layer imitation formats without changing the TRN-001 contracts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from store import TrainingSessionStore, build_training_session_store
except ImportError:  # pragma: no cover - package-style import fallback
    from .store import TrainingSessionStore, build_training_session_store

from services.research.imitation.dataset_builder import (
    DatasetBuildRequest,
    ImitationDatasetBuilderConfig,
    build_dataset,
)
from services.research.imitation.preference_models import (
    CorrectionTrace,
    PreferenceExample,
    validate_correction_trace_payload,
    validate_preference_example_payload,
)


SUPPORTED_TARGET_FORMATS = frozenset({"bc", "trl", "preference"})
NULL_ARTIFACT = {"artifact_id": "__null__", "is_null": True}


class TraceExportError(ValueError):
    """Raised when a trainer trace cannot be exported safely."""


def export_session(
    session_id: str,
    target_format: str,
    *,
    data_dir: str | Path | None = None,
    store: TrainingSessionStore | None = None,
) -> dict[str, Any]:
    """Export one trainer session into a target imitation dataset format.

    Args:
        session_id: TeachingSession identifier.
        target_format: One of "bc", "trl", or "preference".
        data_dir: Optional data directory for the default TrainingSessionStore.
        store: Optional prebuilt store. Tests and callers can inject this to
            avoid relying on process environment.
    """

    normalized_format = _normalize_target_format(target_format)
    export_store = store or build_training_session_store(data_dir or _default_data_dir())
    session = export_store.get_session(session_id)
    if not isinstance(session, Mapping):
        raise TraceExportError(f"training session not found: {session_id}")
    events = _load_ordered_events(export_store, session_id, session)
    if not events:
        raise TraceExportError(f"training session has no teaching events: {session_id}")

    if normalized_format == "bc":
        return _export_bc(session, events)
    preference_payload = _export_preference(session, events)
    if normalized_format == "preference":
        return preference_payload
    return _export_trl(session, preference_payload)


def _default_data_dir() -> str:
    return os.getenv("TRAINING_SESSION_DATA_DIR", "/tmp/pantheon/training-session")


def _normalize_target_format(target_format: str) -> str:
    normalized = str(target_format or "").strip().lower()
    if normalized not in SUPPORTED_TARGET_FORMATS:
        allowed = ", ".join(sorted(SUPPORTED_TARGET_FORMATS))
        raise TraceExportError(f"target_format must be one of: {allowed}")
    return normalized


def _load_ordered_events(
    store: TrainingSessionStore,
    session_id: str,
    session: Mapping[str, Any],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    for raw in list(store.list_event_log(session_id)) + list(session.get("events") or []):
        if not isinstance(raw, Mapping):
            continue
        event = dict(raw)
        event_id = _text(event.get("event_id"))
        if event_id:
            if event_id in seen:
                continue
            seen.add(event_id)
        events.append(event)
    return sorted(events, key=_event_sort_key)


def _event_sort_key(event: Mapping[str, Any]) -> tuple[int, str, str]:
    return (
        _int(event.get("sequence_number"), default=10**9),
        _event_time(event),
        _text(event.get("event_id")),
    )


def _export_bc(session: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    session_id = _session_id(session)
    target = _training_target(session, events)
    steps = [_step_from_event(event) for event in events]
    steps = [step for step in steps if step is not None]
    if not steps:
        raise TraceExportError(f"no imitation trajectory steps could be derived for {session_id}")

    trajectory = {
        "trajectory_id": f"traj-{session_id}",
        "actor_id": _actor_id(session, _decision_event(events)),
        "actor_role": _actor_role(_decision_event(events)),
        "decision": _trajectory_decision(events),
        "target": target,
        "steps": steps,
    }
    request_payload: dict[str, Any] = {
        "dataset_id": f"trn-trace-{session_id}-bc",
        "strategy_id": target["strategy_id"],
        "source_dataset_refs": [f"teaching-session://{session_id}"],
        "sessions": [trajectory],
    }
    source_strategy_spec_id = _source_strategy_spec_id(target)
    if source_strategy_spec_id:
        request_payload["source_strategy_spec_id"] = source_strategy_spec_id

    result = build_dataset(
        DatasetBuildRequest.from_dict(request_payload),
        config=ImitationDatasetBuilderConfig(require_feedback_event_ids=True),
    )
    payload = dict(result.dataset_payload)
    payload["target_format"] = "bc"
    payload["export_metadata"] = _export_metadata(session, events, produced_records=result.num_included)
    return payload


def _export_preference(
    session: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    preference_examples: list[dict[str, Any]] = []
    correction_traces: list[dict[str, Any]] = []

    for event in events:
        event_type = _event_type(event)
        if event_type in {"control_patch", "correction", "patch_proposed"}:
            trace = _correction_trace_from_event(session, events, event)
            if trace is None:
                continue
            example = _edit_preference_from_trace(session, event, trace)
            correction_traces.append(trace)
            preference_examples.append(example)
        elif event_type == "commit":
            preference_examples.append(_decision_preference_from_event(session, events, event, action="approve"))
        elif event_type == "discard":
            preference_examples.append(_decision_preference_from_event(session, events, event, action="reject"))

    if not preference_examples:
        raise TraceExportError(f"no preference examples could be derived for {_session_id(session)}")

    for trace in correction_traces:
        validate_correction_trace_payload(trace)
    for example in preference_examples:
        validate_preference_example_payload(example)

    return {
        "target_format": "preference",
        "session_id": _session_id(session),
        "preference_examples": preference_examples,
        "correction_traces": correction_traces,
        "metadata": _export_metadata(session, events, produced_records=len(preference_examples)),
    }


def _export_trl(session: Mapping[str, Any], preference_payload: Mapping[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for example in preference_payload.get("preference_examples") or []:
        if not isinstance(example, Mapping):
            continue
        chosen = example.get("chosen_artifact") or NULL_ARTIFACT
        rejected = example.get("rejected_artifact") or NULL_ARTIFACT
        prompt_context = {
            "session_id": preference_payload.get("session_id") or _session_id(session),
            "strategy_id": example.get("strategy_id"),
            "source_feedback_event_id": example.get("source_feedback_event_id"),
            "action": example.get("action"),
            "actor_role": example.get("actor_role"),
            "target": example.get("target"),
            "context": example.get("context") or {},
        }
        rows.append(
            {
                "prompt": f"pantheon_trainer_preference {stable_json(prompt_context)}",
                "chosen": stable_json(chosen),
                "rejected": stable_json(rejected),
                "source_feedback_event_id": example.get("source_feedback_event_id"),
                "preference_example_id": example.get("example_id"),
            }
        )
    if not rows:
        raise TraceExportError(f"no TRL preference pairs could be derived for {_session_id(session)}")
    return {
        "target_format": "trl",
        "session_id": preference_payload.get("session_id") or _session_id(session),
        "pairs": rows,
        "metadata": {
            **dict(preference_payload.get("metadata") or {}),
            "pair_shape": "prompt/chosen/rejected",
            "chosen_rejected_encoding": "stable_json_string",
        },
    }


def _correction_trace_from_event(
    session: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    event: Mapping[str, Any],
) -> dict[str, Any] | None:
    payload_trace = _mapping(_payload(event).get("correction_trace"))
    if payload_trace:
        trace = dict(payload_trace)
        trace.setdefault("trace_id", f"corr-{_event_id(event)}")
        trace.setdefault("source_feedback_event_id", _event_id(event))
        trace.setdefault("created_at", _event_time(event))
        return CorrectionTrace.from_dict(trace).to_dict()

    patch_delta = _patch_delta(event)
    operations = _operations_from_patch_delta(patch_delta)
    if not operations:
        return None
    target = _training_target(session, events)
    before_artifact, after_artifact = _patch_artifacts(session, events, event, target, patch_delta)
    trace = CorrectionTrace(
        trace_id=f"corr-{_event_id(event)}",
        strategy_id=target["strategy_id"],
        source_feedback_event_id=_event_id(event),
        actor_id=_actor_id(session, event),
        actor_role=_actor_role(event),
        target=target,
        before_artifact=before_artifact,
        after_artifact=after_artifact,
        operations=operations,
        rationale=_rationale(event),
        task_ref="TRN-007",
        decision_ref=_decision_ref(session, events),
        created_at=_event_time(event),
        metadata={
            "source": "training-session.trace_export",
            "teaching_session_id": _session_id(session),
        },
    ).to_dict()
    return trace


def _edit_preference_from_trace(
    session: Mapping[str, Any],
    event: Mapping[str, Any],
    trace: Mapping[str, Any],
) -> dict[str, Any]:
    example = PreferenceExample(
        example_id=f"pref-{_event_id(event)}",
        strategy_id=_text(trace.get("strategy_id")),
        source_feedback_event_id=_event_id(event),
        correction_trace_id=_text(trace.get("trace_id")),
        actor_id=_actor_id(session, event),
        actor_role=_actor_role(event),
        action="edit",
        target=_mapping(trace.get("target")),
        chosen_artifact=_mapping(trace.get("after_artifact")),
        rejected_artifact=_mapping(trace.get("before_artifact")),
        confidence=_confidence(event, default=0.8),
        rationale=_rationale(event),
        observed_at=_event_time(event),
        created_at=_event_time(event),
        context=_example_context(session, event),
        metadata={
            "source": "training-session.trace_export",
            "teaching_session_id": _session_id(session),
        },
    ).to_dict()
    return example


def _decision_preference_from_event(
    session: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    event: Mapping[str, Any],
    *,
    action: str,
) -> dict[str, Any]:
    target = _training_target(session, events)
    chosen_artifact = _decision_artifact(session, events, event, target, preferred=True) if action == "approve" else None
    rejected_artifact = _decision_artifact(session, events, event, target, preferred=False) if action == "reject" else None
    return PreferenceExample(
        example_id=f"pref-{_event_id(event)}",
        strategy_id=target["strategy_id"],
        source_feedback_event_id=_event_id(event),
        actor_id=_actor_id(session, event),
        actor_role=_actor_role(event),
        action=action,
        target=target,
        chosen_artifact=chosen_artifact,
        rejected_artifact=rejected_artifact,
        confidence=_confidence(event, default=1.0),
        rationale=_rationale(event),
        observed_at=_event_time(event),
        created_at=_event_time(event),
        context=_example_context(session, event),
        metadata={
            "source": "training-session.trace_export",
            "teaching_session_id": _session_id(session),
        },
    ).to_dict()


def _step_from_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    explicit = _mapping(_payload(event).get("imitation_step") or event.get("imitation_step"))
    if explicit:
        step = {
            "observation": _numeric_sequence(explicit.get("observation")),
            "action": _text(explicit.get("action")),
            "feedback_event_id": _event_id(event),
        }
        if explicit.get("reward") is not None:
            step["reward"] = _float(explicit.get("reward"))
        if explicit.get("context") is not None:
            step["context"] = dict(_mapping(explicit.get("context")))
        return _clean_step(step)

    patch_delta = _patch_delta(event)
    if patch_delta:
        observation = _observation_from_patch_delta(patch_delta)
        if observation:
            first_key = _text(_mapping(patch_delta[0]).get("parameter_key")) or "control"
            return _clean_step(
                {
                    "observation": observation,
                    "action": f"patch:{first_key}",
                    "reward": _reward_for_event(event),
                    "feedback_event_id": _event_id(event),
                    "context": {"event_type": _event_type(event)},
                }
            )

    if _event_type(event) in {"commit", "discard"}:
        reward = 1.0 if _event_type(event) == "commit" else -1.0
        return _clean_step(
            {
                "observation": [float(_int(event.get("sequence_number"), default=0)), reward],
                "action": _event_type(event),
                "reward": reward,
                "feedback_event_id": _event_id(event),
                "context": {"event_type": _event_type(event)},
            }
        )
    return None


def _clean_step(step: Mapping[str, Any]) -> dict[str, Any]:
    observation = _numeric_sequence(step.get("observation"))
    action = _text(step.get("action"))
    if not observation or not action:
        raise TraceExportError("imitation step requires numeric observation and action")
    payload: dict[str, Any] = {
        "observation": observation,
        "action": action,
        "feedback_event_id": _text(step.get("feedback_event_id")),
    }
    if step.get("reward") is not None:
        payload["reward"] = _float(step.get("reward"))
    context = _mapping(step.get("context"))
    if context:
        payload["context"] = context
    return payload


def _training_target(
    session: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidates: list[Mapping[str, Any]] = []
    for source in (session, _mapping(session.get("metadata"))):
        target = _mapping(source.get("target"))
        if target:
            candidates.append(target)
    for event in events:
        payload = _payload(event)
        target = _mapping(payload.get("target") or event.get("target"))
        if target:
            candidates.append(target)

    base: dict[str, Any] = {}
    for candidate in candidates:
        base.update({key: value for key, value in candidate.items() if value not in (None, "")})
        if base.get("strategy_id"):
            break

    session_id = _session_id(session)
    artifacts = _session_artifacts(session)
    strategy_id = _text(base.get("strategy_id") or session.get("strategy_id") or session.get("persona_id") or session_id)
    promotion_state = _text(base.get("promotion_state") or "candidate")
    if promotion_state not in {"candidate", "paper"}:
        raise TraceExportError("target.promotion_state must be candidate or paper for imitation export")
    target = {
        "strategy_id": strategy_id,
        "promotion_state": promotion_state,
        "registry_id": _text(base.get("registry_id") or artifacts.get("after_artifact_ref") or artifacts.get("candidate_artifact_ref") or artifacts.get("before_artifact_ref") or f"training-session:{session_id}"),
        "artifact_version": _text(base.get("artifact_version") or "0.0.0"),
        "artifact_type": _text(base.get("artifact_type") or "strategy_spec"),
    }
    lineage_ref = _text(base.get("lineage_ref") or artifacts.get("lineage_ref"))
    if lineage_ref:
        target["lineage_ref"] = lineage_ref
    return target


def _patch_artifacts(
    session: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    event: Mapping[str, Any],
    target: Mapping[str, Any],
    patch_delta: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    refs = _artifact_refs(session, event)
    before = _artifact_snapshot(
        target,
        refs.get("before_artifact_ref") or target.get("registry_id"),
        patch_delta,
        value_key="previous_value",
    )
    after = _artifact_snapshot(
        target,
        refs.get("after_artifact_ref") or refs.get("candidate_artifact_ref") or f"{target.get('registry_id')}:patched",
        patch_delta,
        value_key="new_value",
    )
    return before, after


def _artifact_snapshot(
    target: Mapping[str, Any],
    artifact_id: Any,
    patch_delta: Sequence[Mapping[str, Any]] | None = None,
    *,
    value_key: str | None = None,
) -> dict[str, Any]:
    snapshot = {
        "artifact_id": _text(artifact_id) or _text(target.get("registry_id")) or _text(target.get("lineage_ref")),
        "strategy_id": _text(target.get("strategy_id")),
        "artifact_type": _text(target.get("artifact_type") or "strategy_spec"),
        "artifact_version": _text(target.get("artifact_version") or "0.0.0"),
        "promotion_state": _text(target.get("promotion_state") or "candidate"),
    }
    if patch_delta and value_key:
        snapshot["parameters"] = {
            _text(_mapping(row).get("parameter_key")): _mapping(row).get(value_key)
            for row in patch_delta
            if _text(_mapping(row).get("parameter_key"))
        }
    return snapshot


def _decision_artifact(
    session: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    event: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    preferred: bool,
) -> dict[str, Any]:
    refs = _artifact_refs(session, event)
    artifact_id = (
        refs.get("after_artifact_ref")
        or refs.get("candidate_artifact_ref")
        or refs.get("before_artifact_ref")
        or target.get("registry_id")
    )
    if not preferred:
        artifact_id = refs.get("candidate_artifact_ref") or refs.get("before_artifact_ref") or artifact_id
    return _artifact_snapshot(target, artifact_id)


def _operations_from_patch_delta(patch_delta: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for row in patch_delta:
        mapping = _mapping(row)
        key = _text(mapping.get("parameter_key") or mapping.get("path"))
        if not key:
            continue
        operations.append(
            {
                "path": key if key.startswith("/") else f"/{key}",
                "operation": _text(mapping.get("operation") or "replace"),
                "old_value": mapping.get("previous_value", mapping.get("old_value")),
                "new_value": mapping.get("new_value"),
                "rationale": _text(mapping.get("rationale") or "trainer correction"),
            }
        )
    return operations


def _patch_delta(event: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    payload = _payload(event)
    raw = event.get("patch_delta")
    if raw is None:
        raw = payload.get("patch_delta")
    if raw is None:
        raw = payload.get("patches")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _observation_from_patch_delta(patch_delta: Sequence[Mapping[str, Any]]) -> list[float]:
    observation: list[float] = []
    for row in patch_delta:
        mapping = _mapping(row)
        old_value = mapping.get("previous_value", mapping.get("old_value"))
        new_value = mapping.get("new_value")
        for value in (old_value, new_value):
            if isinstance(value, (int, float)):
                observation.append(float(value))
        if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)):
            observation.append(float(new_value) - float(old_value))
    return observation


def _trajectory_decision(events: Sequence[Mapping[str, Any]]) -> str:
    for event in reversed(events):
        event_type = _event_type(event)
        if event_type == "commit":
            return "approve"
        if event_type == "discard":
            return "reject"
    return "edit"


def _decision_event(events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    for event in reversed(events):
        if _event_type(event) in {"commit", "discard"}:
            return event
    for event in reversed(events):
        if _event_type(event) in {"control_patch", "correction", "patch_proposed"}:
            return event
    return events[-1] if events else {}


def _source_strategy_spec_id(target: Mapping[str, Any]) -> str:
    return _text(target.get("registry_id") or target.get("lineage_ref"))


def _decision_ref(session: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> str:
    artifacts = _session_artifacts(session)
    return _text(artifacts.get("decision_record_ref") or f"teaching-session://{_session_id(session)}#decision")


def _artifact_refs(session: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    refs.update(_session_artifacts(session))
    refs.update(_mapping(_payload(event).get("artifact_refs")))
    refs.update(_mapping(event.get("artifact_refs")))
    return refs


def _session_artifacts(session: Mapping[str, Any]) -> dict[str, Any]:
    return dict(_mapping(session.get("artifacts")))


def _example_context(session: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "operator_id": _actor_id(session, event),
        "timestamp": _event_time(event),
        "promotion_state": _training_target(session, [event])["promotion_state"],
        "feedback_action": _event_type(event),
        "teaching_event_id": _event_id(event),
    }


def _export_metadata(
    session: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    produced_records: int,
) -> dict[str, Any]:
    return {
        "source": "training-session.trace_export",
        "schema_version": "1.0",
        "session_id": _session_id(session),
        "source_trace_refs": [_text(session.get("trace_id") or f"trace-{_session_id(session)}")],
        "source_feedback_event_ids": [_event_id(event) for event in events if _event_id(event)],
        "event_count": len(events),
        "produced_records": produced_records,
        "research_only": True,
        "direct_live_influence": False,
    }


def _event_type(event: Mapping[str, Any]) -> str:
    return _text(event.get("event_type") or _payload(event).get("event_type")).lower()


def _event_id(event: Mapping[str, Any]) -> str:
    return _text(event.get("event_id") or f"event-{_int(event.get('sequence_number'), default=0)}")


def _event_time(event: Mapping[str, Any]) -> str:
    return _text(event.get("emitted_at") or event.get("timestamp") or _payload(event).get("timestamp"))


def _session_id(session: Mapping[str, Any]) -> str:
    session_id = _text(session.get("session_id") or session.get("id"))
    if not session_id:
        raise TraceExportError("session_id is required")
    return session_id


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(event.get("payload"))


def _actor_id(session: Mapping[str, Any], event: Mapping[str, Any]) -> str:
    payload = _payload(event)
    resolution = _mapping(session.get("replay_resolution"))
    actor_id = (
        payload.get("actor_id")
        or event.get("actor_id")
        or resolution.get("decision_by")
        or event.get("actor")
        or session.get("opened_by")
    )
    return _text(actor_id) or "operator"


def _actor_role(event: Mapping[str, Any]) -> str:
    payload = _payload(event)
    raw = _text(payload.get("actor_role") or event.get("actor_role") or event.get("actor"))
    normalized = raw.lower()
    if normalized in {"operator", "user", "trader"}:
        return "operator"
    if normalized in {"approver", "risk_approver", "reviewer"}:
        return "approver"
    return "operator"


def _rationale(event: Mapping[str, Any]) -> str:
    payload = _payload(event)
    return _text(payload.get("rationale") or event.get("summary") or event.get("message_body") or _event_type(event))


def _confidence(event: Mapping[str, Any], *, default: float) -> float:
    payload = _payload(event)
    raw = payload.get("confidence", event.get("confidence"))
    if raw is None:
        return default
    value = _float(raw)
    if value < 0 or value > 1:
        raise TraceExportError("confidence must be between 0 and 1")
    return value


def _reward_for_event(event: Mapping[str, Any]) -> float | None:
    payload = _payload(event)
    raw = payload.get("reward", event.get("reward"))
    if raw is None:
        return None
    return _float(raw)


def _numeric_sequence(value: Any) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    numbers: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)):
            raise TraceExportError("observation values must be numeric")
        numbers.append(float(item))
    return numbers


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float:
    if not isinstance(value, (int, float)):
        raise TraceExportError("numeric value is required")
    return float(value)


def stable_json(value: Any) -> str:
    """Return stable JSON for DPO text fields and deterministic tests."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
