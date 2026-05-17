"""TRL preference-pair bridge for governed imitation research data.

IMT-008 converts IMT-002 PreferenceExample and CorrectionTrace payloads into
the explicit-prompt DPO dataset shape accepted by TRL's DPOTrainer:
``{"prompt": ..., "chosen": ..., "rejected": ...}``.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

try:  # Support both package imports and service-local test execution.
    from .preference_models import CorrectionTrace, PreferenceExample
except ImportError:  # pragma: no cover - exercised by service-local tests.
    from preference_models import CorrectionTrace, PreferenceExample


TRLPairsRow = dict[str, str]


class TrlBridgeError(ValueError):
    """Raised when governed preference data cannot form a TRL pair row."""


def to_trl_pairs(preference_examples: Iterable[PreferenceExample | Mapping[str, Any]]) -> list[TRLPairsRow]:
    """Convert PreferenceExample values into TRL DPO preference rows.

    Each output row has exactly the explicit standard-format DPO columns
    ``prompt``, ``chosen``, and ``rejected``. Artifacts and prompts are
    serialized as canonical JSON strings to keep rows deterministic and
    dataset-backend neutral.
    """

    rows: list[TRLPairsRow] = []
    for index, raw_example in enumerate(_require_iterable(preference_examples, "preference_examples")):
        example = _coerce_preference_example(raw_example, index)
        if example.chosen_artifact is None or example.rejected_artifact is None:
            raise TrlBridgeError(
                f"preference_examples[{index}] must include both chosen_artifact and rejected_artifact"
            )
        rows.append(
            {
                "prompt": _preference_prompt(example),
                "chosen": _artifact_text(example.chosen_artifact),
                "rejected": _artifact_text(example.rejected_artifact),
            }
        )
    return rows


def from_correction_traces(correction_traces: Iterable[CorrectionTrace | Mapping[str, Any]]) -> list[TRLPairsRow]:
    """Convert CorrectionTrace values into TRL DPO preference rows.

    The corrected ``after_artifact`` becomes ``chosen`` and the original
    ``before_artifact`` becomes ``rejected``.
    """

    rows: list[TRLPairsRow] = []
    for index, raw_trace in enumerate(_require_iterable(correction_traces, "correction_traces")):
        trace = _coerce_correction_trace(raw_trace, index)
        rows.append(
            {
                "prompt": _trace_prompt(trace),
                "chosen": _artifact_text(trace.after_artifact),
                "rejected": _artifact_text(trace.before_artifact),
            }
        )
    return rows


def _coerce_preference_example(raw_example: PreferenceExample | Mapping[str, Any], index: int) -> PreferenceExample:
    if isinstance(raw_example, PreferenceExample):
        return raw_example
    if isinstance(raw_example, Mapping):
        return PreferenceExample.from_dict(raw_example)
    raise TrlBridgeError(f"preference_examples[{index}] must be a PreferenceExample or mapping")


def _coerce_correction_trace(raw_trace: CorrectionTrace | Mapping[str, Any], index: int) -> CorrectionTrace:
    if isinstance(raw_trace, CorrectionTrace):
        return raw_trace
    if isinstance(raw_trace, Mapping):
        return CorrectionTrace.from_dict(raw_trace)
    raise TrlBridgeError(f"correction_traces[{index}] must be a CorrectionTrace or mapping")


def _require_iterable(values: Iterable[Any], field_name: str) -> Iterable[Any]:
    if isinstance(values, Mapping) or isinstance(values, (str, bytes)) or values is None:
        raise TrlBridgeError(f"{field_name} must be an iterable of examples, not a single value")
    return values


def _preference_prompt(example: PreferenceExample) -> str:
    prompt: dict[str, Any] = {
        "source_type": "preference_example",
        "example_id": example.example_id,
        "strategy_id": example.strategy_id,
        "source_feedback_event_id": example.source_feedback_event_id,
        "actor_id": example.actor_id,
        "actor_role": example.actor_role,
        "action": example.action,
        "target": example.target.to_dict(),
    }
    _add_optional(prompt, "correction_trace_id", example.correction_trace_id)
    _add_optional(prompt, "rationale", example.rationale)
    if example.context:
        prompt["context"] = dict(example.context)
    return _canonical_json(prompt)


def _trace_prompt(trace: CorrectionTrace) -> str:
    prompt: dict[str, Any] = {
        "source_type": "correction_trace",
        "trace_id": trace.trace_id,
        "strategy_id": trace.strategy_id,
        "source_feedback_event_id": trace.source_feedback_event_id,
        "actor_id": trace.actor_id,
        "actor_role": trace.actor_role,
        "target": trace.target.to_dict(),
        "operations": [operation.to_dict() for operation in trace.operations],
    }
    _add_optional(prompt, "rationale", trace.rationale)
    _add_optional(prompt, "task_ref", trace.task_ref)
    _add_optional(prompt, "decision_ref", trace.decision_ref)
    return _canonical_json(prompt)


def _artifact_text(artifact: Mapping[str, Any]) -> str:
    return _canonical_json(dict(artifact))


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _add_optional(payload: dict[str, Any], key: str, value: Any) -> None:
    if value not in (None, "", {}, []):
        payload[key] = value


__all__ = [
    "TRLPairsRow",
    "TrlBridgeError",
    "from_correction_traces",
    "to_trl_pairs",
]
