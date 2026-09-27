"""Shared fail-closed evidence-ref redaction helpers.

Any BFF read surface that emits identity-gated evidence references
(``evidence_refs``, ``linked_evidence``, ``context_refs``, and similar
capability-gated reference lists) should redact through these helpers
instead of re-deriving the same capability-resolution-failure handling
locally. A capability lookup that raises or returns ``None`` fails
closed -- it is treated as an empty capability set so the canonical
``redact_evidence_refs`` (``models.py``) gates every capability-required
ref -- rather than defaulting to open disclosure.

Originally introduced for the governance domain router
(``governance/router.py``); moved here so the control-loops domain
router can reuse the identical fail-closed wrapper instead of
duplicating it (BFF-CONTROL-LOOPS-EVIDENCE-REDACTION-SWEEP-001).
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Tuple

RedactFn = Callable[..., Tuple[List[Dict[str, Any]], int]]
CapabilitiesFn = Callable[[Any], Any]


def safe_redact_evidence_refs(
    identity: Any,
    refs: List[Dict[str, Any]],
    *,
    redact_fn: RedactFn,
    capabilities_fn: CapabilitiesFn,
) -> Tuple[List[Dict[str, Any]], int]:
    """Resolve capabilities and redact, failing closed on any error.

    A missing or failed capability lookup passes an explicit empty
    capability set so the canonical redactor gates every
    capability-required evidence ref, instead of silently letting an
    unknown capability set through unredacted.
    """
    try:
        capabilities = capabilities_fn(identity)
    except Exception:
        capabilities = None
    if capabilities is None:
        capabilities = []
    try:
        return redact_fn(identity, refs, capabilities=capabilities)
    except Exception:
        redacted: List[Dict[str, Any]] = []
        for ref in refs:
            ref_id = str(ref.get("ref_id") or ref.get("id") or "") if isinstance(ref, dict) else str(ref)
            redacted.append(
                {
                    "ref_id": ref_id,
                    "redacted": True,
                    "reason": "redaction_policy_unavailable",
                }
            )
        return redacted, len(redacted)


def redact_evidence_field_items(
    identity: Any,
    items: List[Any],
    *,
    field: str,
    redact_fn: RedactFn,
    capabilities_fn: CapabilitiesFn,
) -> Tuple[List[Any], int]:
    """Redact a top-level evidence-ref list ``field`` on each dict in ``items``.

    Shared by any handler whose response is a flat list of dicts that may
    carry a capability-gated reference list directly on the item.
    Non-dict items and items missing (or with an empty) ``field`` pass
    through unchanged.
    """
    total_redacted = 0
    redacted_items: List[Any] = []
    for item in items:
        if not isinstance(item, dict):
            redacted_items.append(item)
            continue
        item_copy = copy.deepcopy(item)
        raw_refs = item_copy.get(field)
        if isinstance(raw_refs, list) and raw_refs:
            processed_refs, count = safe_redact_evidence_refs(
                identity, raw_refs, redact_fn=redact_fn, capabilities_fn=capabilities_fn
            )
            item_copy[field] = processed_refs
            total_redacted += count
        redacted_items.append(item_copy)
    return redacted_items, total_redacted


__all__ = ["safe_redact_evidence_refs", "redact_evidence_field_items"]
