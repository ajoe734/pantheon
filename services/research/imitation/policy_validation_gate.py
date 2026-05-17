"""Policy validation gate for behavior_policy artifacts (IMT-007).

Validates a behavior_policy artifact before it enters the registry or governance
pipeline.  All checks are applied together; all failures are collected and
returned in a single ValidationResult.  This module is intentionally standalone
and has no registry, runtime, or trainer dependencies.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping


FORBIDDEN_TRIGGER_WORDS: frozenset[str] = frozenset({"deploy", "canary", "live"})
DEFAULT_ACTION_MATCH_THRESHOLD: float = 0.6


class PolicyValidationError(ValueError):
    """Raised when behavior_policy_ref is not a mapping (structural pre-check)."""


def validate(
    behavior_policy_ref: Mapping[str, Any],
    threshold: float = DEFAULT_ACTION_MATCH_THRESHOLD,
) -> dict[str, Any]:
    """Validate a behavior_policy artifact before registry/governance ingestion.

    Args:
        behavior_policy_ref: Artifact dict produced by bc_trainer.train() with an
            optional ``eval_result`` sub-dict from eval_metrics.evaluate().  The
            gate resolves ``producer_run_id`` from ``training.run_id``,
            ``dataset_ref`` from ``lineage.source_dataset_ref``, and
            ``training_config`` from the ``training`` mapping.
        threshold: Minimum action_match_rate required to pass (default 0.6).

    Returns:
        ValidationResult dict:
        - ``passed``: True only when all checks pass.
        - ``rejection_reasons``: List of human-readable rejection reason strings.
    """
    if not isinstance(behavior_policy_ref, Mapping):
        raise PolicyValidationError("behavior_policy_ref must be a mapping")

    rejection_reasons: list[str] = []

    _check_metadata(behavior_policy_ref, rejection_reasons)
    _check_checksum(behavior_policy_ref, rejection_reasons)
    _check_action_match_rate(behavior_policy_ref, threshold, rejection_reasons)
    _check_trigger_words(behavior_policy_ref, rejection_reasons)

    return {
        "passed": len(rejection_reasons) == 0,
        "rejection_reasons": rejection_reasons,
    }


# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------

def _check_metadata(
    artifact: Mapping[str, Any],
    rejection_reasons: list[str],
) -> None:
    missing: list[str] = []

    # producer_run_id: direct key or training.run_id
    if not _resolve_text(artifact, ("producer_run_id",)) and \
       not _resolve_text(artifact, ("training", "run_id")):
        missing.append("producer_run_id")

    # dataset_ref: direct key or lineage.source_dataset_ref
    if not _resolve_text(artifact, ("dataset_ref",)) and \
       not _resolve_text(artifact, ("lineage", "source_dataset_ref")):
        missing.append("dataset_ref")

    # training_config: direct key or training dict
    if not _is_nonempty_mapping(artifact, ("training_config",)) and \
       not _is_nonempty_mapping(artifact, ("training",)):
        missing.append("training_config")

    if missing:
        rejection_reasons.append(
            f"metadata missing required field(s): {', '.join(missing)}"
        )


def _check_checksum(
    artifact: Mapping[str, Any],
    rejection_reasons: list[str],
) -> None:
    stored = artifact.get("checksum")
    if not stored:
        rejection_reasons.append("checksum is absent")
        return
    expected = _compute_checksum(artifact)
    if stored != expected:
        rejection_reasons.append(
            f"checksum mismatch: stored={stored!r} expected={expected!r}"
        )


def _check_action_match_rate(
    artifact: Mapping[str, Any],
    threshold: float,
    rejection_reasons: list[str],
) -> None:
    rate = _extract_action_match_rate(artifact)
    if rate is None:
        rejection_reasons.append(
            "action_match_rate absent; run eval_metrics.evaluate() before validation"
        )
        return
    if rate < threshold:
        rejection_reasons.append(
            f"action_match_rate={rate:.4f} is below threshold={threshold:.4f}"
        )


def _check_trigger_words(
    artifact: Mapping[str, Any],
    rejection_reasons: list[str],
) -> None:
    found = _collect_trigger_words_in_values(artifact)
    if found:
        words = sorted(found)
        rejection_reasons.append(
            f"policy string values contain forbidden trigger word(s): {', '.join(words)}"
        )


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def _resolve_text(artifact: Mapping[str, Any], path: tuple[str, ...]) -> str | None:
    current: Any = artifact
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    if current in (None, ""):
        return None
    text = str(current).strip()
    return text or None


def _is_nonempty_mapping(artifact: Mapping[str, Any], path: tuple[str, ...]) -> bool:
    current: Any = artifact
    for key in path:
        if not isinstance(current, Mapping):
            return False
        current = current.get(key)
    return isinstance(current, Mapping) and bool(current)


def _extract_action_match_rate(artifact: Mapping[str, Any]) -> float | None:
    for container_key in ("eval_result", "evaluation", "metrics"):
        container = artifact.get(container_key)
        if isinstance(container, Mapping):
            rate = container.get("action_match_rate")
            if isinstance(rate, (int, float)) and not isinstance(rate, bool):
                return float(rate)
    rate = artifact.get("action_match_rate")
    if isinstance(rate, (int, float)) and not isinstance(rate, bool):
        return float(rate)
    return None


def _collect_trigger_words_in_values(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, Mapping):
        for value in obj.values():
            found |= _collect_trigger_words_in_values(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            found |= _collect_trigger_words_in_values(item)
    elif isinstance(obj, str):
        lower = obj.lower()
        for word in FORBIDDEN_TRIGGER_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", lower):
                found.add(word)
    return found


def _compute_checksum(artifact: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(artifact))
    payload.pop("checksum", None)
    stable = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
