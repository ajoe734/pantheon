"""IDS-008 — Lineage query helper.

Given a ``StrategySpecSeed`` (or its dict), returns the full audit trail
from the seed back to the original source event and ``InteractionSourceRecord``.

The query does NOT inline raw prompt/transcript content.  It returns only
references and structured metadata so the trace can be replayed without
exposing PII or raw chat.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.source_ingestion.interaction_source_store import InteractionSourceRecordStore
from services.source_ingestion.ids_audit import IDSAuditEventStore


def query_seed_lineage(
    seed: Mapping[str, Any],
    *,
    interaction_store: InteractionSourceRecordStore | None = None,
    audit_store: IDSAuditEventStore | None = None,
) -> dict[str, Any]:
    """Return the full lineage trace for a strategy seed.

    Returns a dict with:
    - ``seed_id``: the seed identifier
    - ``status``: current seed status
    - ``source_surface``: trainer / committee / agora etc.
    - ``extraction_ref``: the trainer or agora extraction reference
    - ``interaction_id``: points to the governing InteractionSourceRecord
    - ``raw_ref``: evidence reference (never inline content)
    - ``interaction_record``: the stored InteractionSourceRecord dict (without raw content)
    - ``audit_events``: ordered audit events for this interaction (if audit_store provided)
    - ``privacy_assertions``: pass/fail checks that raw content is not inlined
    """
    seed_id = str(seed.get("seed_id") or "")
    status = str(seed.get("status") or "")
    lineage = dict(seed.get("lineage") or {})
    metadata = dict(seed.get("metadata") or {})

    source_surface = str(
        metadata.get("source_surface")
        or lineage.get("source_surface")
        or ""
    )

    extraction_ref: dict[str, Any] = {}
    interaction_id: str | None = None
    raw_ref: str | None = None

    trainer_ref = lineage.get("trainer_seed_extraction_ref")
    agora_ref = lineage.get("agora_seed_extraction_ref") or lineage.get("AgoraSeedExtractionRef")

    if isinstance(trainer_ref, Mapping):
        extraction_ref = dict(trainer_ref)
        interaction_id = str(trainer_ref.get("interaction_id") or "").strip() or None
        raw_ref = str(trainer_ref.get("raw_ref") or "").strip() or None
        if not source_surface:
            source_surface = str(trainer_ref.get("source_surface") or "trainer")
    elif isinstance(agora_ref, Mapping):
        extraction_ref = dict(agora_ref)
        interaction_id = str(agora_ref.get("interaction_id") or "").strip() or None
        raw_ref = str(agora_ref.get("raw_ref") or "").strip() or None
        if not source_surface:
            source_surface = str(agora_ref.get("source_surface") or "")

    if not interaction_id:
        interaction_id = str(
            lineage.get("interaction_source_record_id")
            or metadata.get("interaction_source_record_id")
            or ""
        ).strip() or None

    interaction_record: dict[str, Any] | None = None
    if interaction_id and interaction_store is not None:
        record = interaction_store.get(interaction_id)
        if record is not None:
            record_dict = record.to_dict()
            interaction_record = _strip_raw_content(record_dict)

    audit_events: list[dict[str, Any]] = []
    if audit_store is not None and interaction_id:
        for evt in audit_store.list_by_interaction(interaction_id):
            audit_events.append(evt.to_dict())

    privacy_assertions = _check_privacy(seed, extraction_ref, interaction_record)

    return {
        "seed_id": seed_id,
        "status": status,
        "source_surface": source_surface,
        "extraction_ref": extraction_ref,
        "interaction_id": interaction_id,
        "raw_ref": raw_ref,
        "interaction_record": interaction_record,
        "audit_events": audit_events,
        "privacy_assertions": privacy_assertions,
    }


_RAW_CONTENT_KEYS = frozenset(
    {
        "raw_text",
        "raw_content",
        "raw_prompt",
        "prompt",
        "transcript",
        "messages",
        "message",
        "body",
        "content",
        "teaching_log",
        "dialogue",
        "conversation",
    }
)

_INLINE_CONTENT_EVIDENCE_KEYS = frozenset({"summary", "hypothesis"})


def _strip_raw_content(record_dict: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the interaction record with raw-content keys removed."""
    result: dict[str, Any] = {}
    for k, v in record_dict.items():
        if str(k) in _RAW_CONTENT_KEYS:
            continue
        if isinstance(v, dict):
            result[k] = _strip_raw_content(v)
        elif isinstance(v, list):
            result[k] = [_strip_raw_content(item) if isinstance(item, dict) else item for item in v]
        else:
            result[k] = v
    return result


def _check_privacy(
    seed: Mapping[str, Any],
    extraction_ref: Mapping[str, Any],
    interaction_record: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Assert that raw prompt/transcript is not inlined in seed or lineage."""
    checks: dict[str, bool] = {}

    raw_ref = str(extraction_ref.get("raw_ref") or seed.get("raw_ref") or "")
    checks["raw_ref_is_reference_not_inline"] = bool(raw_ref) and "\n" not in raw_ref and len(raw_ref) < 1024

    raw_ref_role = str(
        extraction_ref.get("raw_ref_role")
        or (seed.get("lineage") or {}).get("raw_ref_role")
        or (seed.get("metadata") or {}).get("raw_ref_role")
        or ""
    )
    checks["raw_ref_role_is_evidence_only"] = raw_ref_role == "evidence_only" or not raw_ref_role

    checks["seed_has_no_raw_content_keys"] = not _has_raw_content_keys(dict(seed))
    checks["extraction_ref_has_no_raw_content_keys"] = not _has_raw_content_keys(dict(extraction_ref))

    if interaction_record is not None:
        checks["interaction_record_has_no_raw_content_keys"] = not _has_raw_content_keys(
            dict(interaction_record)
        )

    checks["all_passed"] = all(checks.values())
    return checks


def _has_raw_content_keys(value: Any) -> bool:
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k) in _RAW_CONTENT_KEYS and _is_nonempty(v):
                return True
            if _has_raw_content_keys(v):
                return True
    elif isinstance(value, list):
        return any(_has_raw_content_keys(item) for item in value)
    return False


def _is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


__all__ = [
    "query_seed_lineage",
]
