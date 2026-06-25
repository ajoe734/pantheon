"""VersionCompare computation for StrategySpec documents.

Implements version comparison semantics from design-closure-round2 §A5.
Schema: services/control-plane/specs/agora/v4/version_compare.schema.json

A comparison has:
  - one base version
  - one to four candidate versions
  - field_diffs     — structural field changes per path
  - metric_diffs    — metric evidence by class (predicted / backtested / paper)
  - readiness_diffs — gate state changes (preliminary_research / full_validation / trading_room)
  - risk_diffs      — optional risk domain changes

Evidence classes (§A5):
  predicted | backtested_in_sample | backtested_oos | paper_observed

The servant may produce a recommendation, but decision_authority is always 'trader'.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .patching import compute_document_sha256


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CANDIDATE_VERSIONS = 4

# Top-level StrategySpec keys that are mutable via patches (§A2 allowlist).
# These are the only keys we diff at the top level.
_MUTABLE_KEYS: tuple[str, ...] = (
    "title",
    "hypothesis",
    "objective",
    "market_scope",
    "data_dependencies",
    "execution_profile",
    "evaluation_plan",
    "governance",
    "evidence_refs",
    "code_refs",
    "metadata",
)

# Read-only keys that we track for informational diff but never modify.
_READONLY_KEYS: tuple[str, ...] = (
    "spec_version",
    "strategy_id",
    "lifecycle_state",
    "provenance",
)

VALID_EVIDENCE_CLASSES: frozenset[str] = frozenset({
    "predicted",
    "backtested_in_sample",
    "backtested_oos",
    "paper_observed",
})

VALID_GATES: frozenset[str] = frozenset({
    "preliminary_research",
    "full_validation",
    "trading_room",
})

VALID_READINESS_STATES: frozenset[str] = frozenset({
    "not_assessed",
    "blocked",
    "conditional",
    "ready",
    "stale",
})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class VersionCompareError(Exception):
    error_code: str = "VERSION_COMPARE_ERROR"


class VersionCompareLimitExceededError(VersionCompareError):
    error_code = "VERSION_COMPARE_LIMIT_EXCEEDED"


# ---------------------------------------------------------------------------
# Field diff helpers
# ---------------------------------------------------------------------------

def _diff_value(base: Any, candidate: Any) -> str:
    """Return change_kind based on whether values are equal."""
    if base == candidate:
        return "unchanged"
    return "changed"


def compute_field_diffs(
    base_doc: Mapping[str, Any],
    candidate_doc: Mapping[str, Any],
    candidate_version_id: str,
    *,
    include_unchanged: bool = False,
) -> list[dict[str, Any]]:
    """Compute top-level field diffs between base and candidate StrategySpec dicts.

    Args:
        base_doc: The base StrategySpec document.
        candidate_doc: The candidate StrategySpec document.
        candidate_version_id: Identifier for the candidate version (for attribution).
        include_unchanged: Whether to emit diff entries for unchanged fields.

    Returns:
        List of field_diff dicts matching the version_compare schema field_diffs items.
    """
    diffs: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for key in _MUTABLE_KEYS:
        seen_keys.add(key)
        in_base = key in base_doc
        in_candidate = key in candidate_doc

        if in_base and not in_candidate:
            diffs.append({
                "path": f"/{key}",
                "change_kind": "removed",
                "candidate_version_id": candidate_version_id,
                "before": base_doc[key],
            })
        elif not in_base and in_candidate:
            diffs.append({
                "path": f"/{key}",
                "change_kind": "added",
                "candidate_version_id": candidate_version_id,
                "after": candidate_doc[key],
            })
        elif in_base and in_candidate:
            change_kind = _diff_value(base_doc[key], candidate_doc[key])
            if change_kind != "unchanged" or include_unchanged:
                entry: dict[str, Any] = {
                    "path": f"/{key}",
                    "change_kind": change_kind,
                    "candidate_version_id": candidate_version_id,
                }
                if change_kind == "changed":
                    entry["before"] = base_doc[key]
                    entry["after"] = candidate_doc[key]
                diffs.append(entry)

    return diffs


# ---------------------------------------------------------------------------
# Metric diff helpers
# ---------------------------------------------------------------------------

def _extract_metrics_from_spec(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Extract evaluation metrics from a StrategySpec (evaluation_plan.metrics list)."""
    ep = doc.get("evaluation_plan") or {}
    if isinstance(ep, dict):
        return {m: None for m in (ep.get("metrics") or [])}
    return {}


def compute_metric_diffs(
    base_doc: Mapping[str, Any],
    candidate_doc: Mapping[str, Any],
    candidate_version_id: str,
    *,
    base_metric_values: Mapping[str, Any] | None = None,
    candidate_metric_values: Mapping[str, Any] | None = None,
    evidence_class: str = "predicted",
) -> list[dict[str, Any]]:
    """Compute metric diffs between base and candidate versions.

    Args:
        base_doc: Base StrategySpec document.
        candidate_doc: Candidate StrategySpec document.
        candidate_version_id: Candidate version identifier.
        base_metric_values: Optional dict of metric name → value for base.
        candidate_metric_values: Optional dict of metric name → value for candidate.
        evidence_class: One of the VALID_EVIDENCE_CLASSES.

    Returns:
        List of metric_diff dicts matching the version_compare schema.
    """
    if evidence_class not in VALID_EVIDENCE_CLASSES:
        raise VersionCompareError(
            f"Invalid evidence_class {evidence_class!r}. Must be one of {sorted(VALID_EVIDENCE_CLASSES)}"
        )

    base_metrics = set(_extract_metrics_from_spec(base_doc).keys())
    candidate_metrics = set(_extract_metrics_from_spec(candidate_doc).keys())
    all_metrics = base_metrics | candidate_metrics

    bv = base_metric_values or {}
    cv = candidate_metric_values or {}

    diffs: list[dict[str, Any]] = []
    for metric in sorted(all_metrics):
        base_val = bv.get(metric)
        cand_val = cv.get(metric)

        entry: dict[str, Any] = {
            "metric": metric,
            "candidate_version_id": candidate_version_id,
            "evidence_class": evidence_class,
            "base_value": base_val,
            "candidate_value": cand_val,
        }

        if base_val is not None and cand_val is not None:
            abs_delta = cand_val - base_val
            entry["absolute_delta"] = abs_delta
            if base_val != 0:
                entry["relative_delta_pct"] = round(100.0 * abs_delta / abs(base_val), 4)

        diffs.append(entry)

    return diffs


# ---------------------------------------------------------------------------
# Readiness diff helpers
# ---------------------------------------------------------------------------

def compute_readiness_diffs(
    base_gate_states: Mapping[str, str],
    candidate_gate_states: Mapping[str, str],
    candidate_version_id: str,
) -> list[dict[str, Any]]:
    """Compute readiness gate diffs between base and candidate versions.

    Args:
        base_gate_states: Dict of gate name → state for the base version.
        candidate_gate_states: Dict of gate name → state for the candidate.
        candidate_version_id: Candidate version identifier.

    Returns:
        List of readiness_diff dicts matching the version_compare schema.
    """
    diffs: list[dict[str, Any]] = []
    for gate in ("preliminary_research", "full_validation", "trading_room"):
        base_state = base_gate_states.get(gate, "not_assessed")
        cand_state = candidate_gate_states.get(gate, "not_assessed")
        diffs.append({
            "candidate_version_id": candidate_version_id,
            "gate": gate,
            "base_state": base_state,
            "candidate_state": cand_state,
        })
    return diffs


# ---------------------------------------------------------------------------
# VersionCompare builder
# ---------------------------------------------------------------------------

def build_version_compare(
    *,
    comparison_id: str,
    workshop_id: str,
    strategy_id: str,
    base_version: Mapping[str, Any],
    candidate_versions: Sequence[Mapping[str, Any]],
    base_doc: Mapping[str, Any],
    candidate_docs: Sequence[Mapping[str, Any]],
    base_gate_states: Mapping[str, str] | None = None,
    candidate_gate_states_list: Sequence[Mapping[str, str]] | None = None,
    base_metric_values: Mapping[str, Any] | None = None,
    candidate_metric_values_list: Sequence[Mapping[str, Any]] | None = None,
    evidence_class: str = "predicted",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a VersionCompare dict matching the v4 version_compare schema.

    Args:
        comparison_id: Unique identifier (pattern: vcmp_<ulid>).
        workshop_id: The workshop this comparison belongs to.
        strategy_id: The strategy being compared.
        base_version: Base version_ref dict (workshop_version_id, strategy_spec_registry_id, label).
        candidate_versions: List of version_ref dicts (1 to 4 candidates).
        base_doc: The base StrategySpec document.
        candidate_docs: Corresponding StrategySpec documents for each candidate.
        base_gate_states: Optional readiness gate states for the base version.
        candidate_gate_states_list: Optional readiness gate states per candidate.
        base_metric_values: Optional metric values for the base version.
        candidate_metric_values_list: Optional metric values per candidate.
        evidence_class: Evidence class for metric diffs.
        created_at: ISO 8601 timestamp; defaults to now if None.

    Returns:
        A VersionCompare dict conforming to the v4 schema.

    Raises:
        VersionCompareLimitExceededError: More than 4 candidates supplied.
        VersionCompareError: Mismatched lengths or invalid inputs.
    """
    n_candidates = len(candidate_versions)
    if n_candidates < 1 or n_candidates > MAX_CANDIDATE_VERSIONS:
        raise VersionCompareLimitExceededError(
            f"candidate_versions must have 1–{MAX_CANDIDATE_VERSIONS} entries, got {n_candidates}"
        )
    if len(candidate_docs) != n_candidates:
        raise VersionCompareError(
            f"candidate_docs length ({len(candidate_docs)}) must match "
            f"candidate_versions length ({n_candidates})"
        )

    if created_at is None:
        from datetime import datetime, timezone
        created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    # Ensure base_version has document_sha256
    _base_version = dict(base_version)
    if "document_sha256" not in _base_version:
        _base_version["document_sha256"] = compute_document_sha256(base_doc)

    # Ensure each candidate_version has document_sha256
    _candidate_versions: list[dict[str, Any]] = []
    for i, cv_ref in enumerate(candidate_versions):
        cv = dict(cv_ref)
        if "document_sha256" not in cv:
            cv["document_sha256"] = compute_document_sha256(candidate_docs[i])
        _candidate_versions.append(cv)

    # Compute field diffs for each candidate
    all_field_diffs: list[dict[str, Any]] = []
    for i, (cv_ref, cv_doc) in enumerate(zip(_candidate_versions, candidate_docs)):
        candidate_version_id = cv_ref["workshop_version_id"]
        all_field_diffs.extend(
            compute_field_diffs(base_doc, cv_doc, candidate_version_id)
        )

    # Compute metric diffs for each candidate
    all_metric_diffs: list[dict[str, Any]] = []
    for i, (cv_ref, cv_doc) in enumerate(zip(_candidate_versions, candidate_docs)):
        candidate_version_id = cv_ref["workshop_version_id"]
        cv_metric_values = (candidate_metric_values_list or [])[i] if candidate_metric_values_list and i < len(candidate_metric_values_list) else None
        all_metric_diffs.extend(
            compute_metric_diffs(
                base_doc,
                cv_doc,
                candidate_version_id,
                base_metric_values=base_metric_values,
                candidate_metric_values=cv_metric_values,
                evidence_class=evidence_class,
            )
        )

    # Compute readiness diffs for each candidate
    all_readiness_diffs: list[dict[str, Any]] = []
    _base_gates = base_gate_states or {}
    for i, cv_ref in enumerate(_candidate_versions):
        candidate_version_id = cv_ref["workshop_version_id"]
        cv_gates = (candidate_gate_states_list or [])[i] if candidate_gate_states_list and i < len(candidate_gate_states_list) else {}
        all_readiness_diffs.extend(
            compute_readiness_diffs(_base_gates, cv_gates, candidate_version_id)
        )

    result: dict[str, Any] = {
        "spec_version": "1.0",
        "comparison_id": comparison_id,
        "workshop_id": workshop_id,
        "strategy_id": strategy_id,
        "base_version": _base_version,
        "candidate_versions": _candidate_versions,
        "field_diffs": all_field_diffs,
        "metric_diffs": all_metric_diffs,
        "readiness_diffs": all_readiness_diffs,
        "created_at": created_at,
    }

    return result


def build_recommendation(
    recommended_version_id: str,
    rationale: str,
    confidence: float,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Build a comparison recommendation.

    Note: decision_authority is always 'trader' per §A5 spec.
    The servant may recommend a version but the human trader makes the final decision.
    """
    return {
        "recommended_version_id": recommended_version_id,
        "rationale": rationale,
        "confidence": max(0.0, min(1.0, confidence)),
        "limitations": limitations or [],
        "decision_authority": "trader",
    }
