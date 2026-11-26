"""Pure role / data-scope classification for persona fleet reads.

Role and data scope are separate dimensions from capital/execution mode.
Only canonical metadata and provenance are consulted; names and ID patterns
are never authoritative. Unclassifiable legacy records stay ``unknown``.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, Tuple

PURPOSE_TRADING = "trading"
PURPOSE_ADVISORY = "advisory_assistant"
PURPOSE_UNKNOWN = "unknown"

DATA_SCOPE_REGULAR = "regular"
DATA_SCOPE_TEST = "test"
DATA_SCOPE_UNKNOWN = "unknown"

LIST_SCOPE_CURRENT = "current"
LIST_SCOPE_TEST = "test"
LIST_SCOPE_RETIRED = "retired"
LIST_SCOPE_RESEARCH = "research"
LIST_SCOPE_UNBOUND = "unbound"
LIST_SCOPE_UNKNOWN = "unknown"

RELEASE_CASE_KEY_PREFIX = "dev-paper-release-"
DEV_PAPER_BASELINE_STRATEGY_FAMILY = "dev_paper_baseline"

# Query values. ``all`` disables the corresponding filter.
PURPOSE_FILTERS = frozenset({PURPOSE_TRADING, PURPOSE_ADVISORY, PURPOSE_UNKNOWN, "all"})
DATA_SCOPE_FILTERS = frozenset({DATA_SCOPE_REGULAR, DATA_SCOPE_TEST, DATA_SCOPE_UNKNOWN, "all"})
ARCHIVED_FILTERS = frozenset({"exclude", "only", "include"})

_ADVISORY_CLASSES = frozenset({"agora_servant", "consult_panel"})
_TEST_SCOPE_VALUES = frozenset({"test", "acceptance", "fixture", "synthetic"})
# Values a create request may declare; the test-like ones reuse the set above.
DECLARABLE_DATA_SCOPES = frozenset({DATA_SCOPE_REGULAR}) | _TEST_SCOPE_VALUES
_TRUTHY = frozenset({"1", "true", "yes"})


def _meta(record: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = record.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _text(record: Mapping[str, Any], *keys: str) -> str:
    metadata = _meta(record)
    for key in keys:
        for source in (metadata, record):
            value = source.get(key)
            if value not in (None, ""):
                return str(value).strip().lower()
    return ""


def _truthy(record: Mapping[str, Any], *keys: str) -> bool:
    metadata = _meta(record)
    for key in keys:
        for source in (metadata, record):
            value = source.get(key)
            if value is True or str(value).strip().lower() in _TRUTHY:
                return True
    return False


def classify_persona_purpose(record: Mapping[str, Any]) -> Tuple[str, str]:
    """Return ``(purpose, source)`` from canonical role metadata."""
    persona_class = _text(record, "persona_class", "personaClass")
    if persona_class in _ADVISORY_CLASSES:
        return PURPOSE_ADVISORY, "persona_class"
    if persona_class == "trading":
        return PURPOSE_TRADING, "persona_class"
    # Agora servants are also identifiable by their user-private scope with
    # no execution authority; that is explicit canonical metadata too.
    if _text(record, "agora_user_id", "agoraUserId") and _text(record, "execution_authority") == "none":
        return PURPOSE_ADVISORY, "agora_scope"
    # Records created through the canonical persona provisioning coordinator,
    # or carrying a trading mandate/stage, are trading personas.
    if _text(record, "provisioning_idempotency_key", "provisioning_request_hash"):
        return PURPOSE_TRADING, "provisioning_provenance"
    if _text(record, "deployment_stage", "capital_mode", "initial_mode", "execution_mode"):
        return PURPOSE_TRADING, "execution_metadata"
    if record.get("record_kind") == "catalog_default":
        return PURPOSE_TRADING, "catalog_default"
    return PURPOSE_UNKNOWN, "unclassified"


def classify_persona_data_scope(record: Mapping[str, Any], *, catalog_default: bool = False) -> Tuple[str, str]:
    """Return ``(data_scope, source)``; paper capital never implies test."""
    explicit = _text(record, "data_scope", "dataScope")
    if explicit in _TEST_SCOPE_VALUES:
        return DATA_SCOPE_TEST, "data_scope"
    if explicit == DATA_SCOPE_REGULAR:
        return DATA_SCOPE_REGULAR, "data_scope"
    if _truthy(record, "is_test", "test_data", "acceptance_run", "is_acceptance"):
        return DATA_SCOPE_TEST, "test_flag"
    if catalog_default or record.get("record_kind") == "catalog_default":
        return DATA_SCOPE_REGULAR, "catalog_default"
    persona_class = _text(record, "persona_class", "personaClass")
    if persona_class in _ADVISORY_CLASSES:
        return DATA_SCOPE_REGULAR, "persona_class"
    if _text(record, "provisioning_idempotency_key", "provisioning_request_hash"):
        return DATA_SCOPE_REGULAR, "provisioning_provenance"
    return DATA_SCOPE_UNKNOWN, "unclassified"


def persona_classification(record: Mapping[str, Any], *, catalog_default: bool = False) -> dict:
    purpose, purpose_source = classify_persona_purpose(record)
    data_scope, data_scope_source = classify_persona_data_scope(record, catalog_default=catalog_default)
    return {
        "persona_purpose": purpose,
        "data_scope": data_scope,
        "classification_source": {"purpose": purpose_source, "data_scope": data_scope_source},
        "classification_confirmed": PURPOSE_UNKNOWN != purpose and DATA_SCOPE_UNKNOWN != data_scope,
    }


def is_non_trading_persona(record: Mapping[str, Any]) -> bool:
    """True only for explicit non-trading assistants (never for unknowns)."""
    return classify_persona_purpose(record)[0] == PURPOSE_ADVISORY


def normalize_filter(value: Optional[str], allowed: frozenset, default: str, field: str) -> str:
    clean = str(value).strip().lower() if value is not None and str(value).strip() else default
    if clean not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)}")
    return clean


def matches_scope(classification: Mapping[str, Any], *, purpose: str, data_scope: str) -> bool:
    if purpose != "all" and classification["persona_purpose"] != purpose:
        return False
    if data_scope != "all" and classification["data_scope"] != data_scope:
        return False
    return True


def is_persona_test_scoped(record: Mapping[str, Any]) -> bool:
    """True when the persona is test-scoped by canonical metadata or baseline provenance (never by name)."""
    if classify_persona_data_scope(record)[0] == DATA_SCOPE_TEST:
        return True
    if _text(record, "strategy_family", "strategyFamily") == DEV_PAPER_BASELINE_STRATEGY_FAMILY:
        return True
    prov_key = _text(
        record,
        "provisioning_idempotency_key",
        "provisioningIdempotencyKey",
        "idempotency_key",
        "idempotencyKey",
    )
    if prov_key.startswith(RELEASE_CASE_KEY_PREFIX):
        return True
    return False


def classify_persona_list_scope(
    record: Mapping[str, Any],
    *,
    projected_state: Optional[str] = None,
) -> str:
    """Return ``current`` | ``test`` | ``retired`` | ``research`` for ``GET /bff/personas`` rows."""
    if is_persona_test_scoped(record):
        return LIST_SCOPE_TEST
    admin_status = _text(record, "status")
    state = (
        str(projected_state).strip().lower()
        if projected_state is not None and str(projected_state).strip()
        else _text(record, "lifecycle_state", "state", "status")
    )
    if state in {"retired", "archived", "rejected", "superseded"} or admin_status == "archived":
        return LIST_SCOPE_RETIRED
    purpose, _ = classify_persona_purpose(record)
    if (
        purpose == PURPOSE_ADVISORY
        or _text(record, "archetype", "strategy_family", "strategyFamily", "strategy_type", "type") in _ADVISORY_CLASSES
        or (_meta(record).get("trading") is False and _truthy(record, "consultable"))
    ):
        return LIST_SCOPE_CURRENT
    if state in {"draft", "research_only"}:
        return LIST_SCOPE_RESEARCH
    return LIST_SCOPE_CURRENT


def classify_artifact_row_list_scope(
    row: Mapping[str, Any],
    *,
    persona_directory_ok: bool,
    persona_scopes_by_id: Mapping[str, str],
    strategy_persona_bindings: Optional[Mapping[str, Sequence[str]]],
) -> str:
    """Return ``current`` | ``test`` | ``retired`` | ``unbound`` | ``research`` | ``unknown`` for ``GET /bff/artifacts`` rows."""
    if not persona_directory_ok:
        return LIST_SCOPE_UNKNOWN
    source_kind = str(row.get("source_kind") or "").strip().lower()
    if source_kind == "research_run_output":
        return LIST_SCOPE_RESEARCH
    if strategy_persona_bindings is None:
        return LIST_SCOPE_UNKNOWN
    strategy_id = str(
        row.get("strategy_id")
        or (row.get("name") if source_kind in ("registry", "") else "")
        or ""
    ).strip()
    if not strategy_id:
        return LIST_SCOPE_UNBOUND
    persona_ids = [
        clean
        for pid in (strategy_persona_bindings.get(strategy_id) or ())
        if (clean := str(pid).strip())
    ]
    if not persona_ids:
        return LIST_SCOPE_UNBOUND
    matched_scopes = [
        persona_scopes_by_id[pid]
        for pid in persona_ids
        if pid in persona_scopes_by_id
    ]
    if not matched_scopes:
        return LIST_SCOPE_UNBOUND
    if all(scope == LIST_SCOPE_TEST for scope in matched_scopes):
        return LIST_SCOPE_TEST
    if any(scope in (LIST_SCOPE_CURRENT, LIST_SCOPE_RESEARCH) for scope in matched_scopes):
        return LIST_SCOPE_CURRENT
    if any(scope == LIST_SCOPE_TEST for scope in matched_scopes):
        return LIST_SCOPE_TEST
    if all(scope == LIST_SCOPE_RETIRED for scope in matched_scopes):
        return LIST_SCOPE_RETIRED
    return LIST_SCOPE_CURRENT
