"""Pure BFF projections retained after the read-surface store deletion.

This module intentionally owns no data, persistence, fixture loading, network
selection, or fallback behavior.  ``main.py`` is the only production caller:

* ``_market_persona_required_data_sources`` projects the governed requirements
  attached to a TW persona provisioning request.  The provisioning workflow,
  not this helper, owns the resulting record.
* ``redact_evidence_refs`` applies the capability policy declared by
  :mod:`models` to an already-owned response payload.

Keeping these two pure helpers here avoids broadening the final hot-file task
into ``main.py`` while making the former compatibility store impossible to
import or construct.
"""

from __future__ import annotations

from typing import Any, Optional

from models import (
    EVIDENCE_CAPABILITY_MAP,
    SOURCE_TYPE_TO_EVIDENCE_KIND,
    EvidenceKind,
    OperatorIdentity,
    RedactedEvidenceRef,
)


def _market_persona_required_data_sources(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the declared source requirements for a provisioned persona."""

    market = str(item.get("market") or "").upper()
    if market == "TW":
        return [
            {
                "dataset": "tw_price_daily",
                "market": "TW",
                "cadence": "daily",
                "source_class": "live_pull",
                "connector_candidates": [
                    "tw-finmind-datasets",
                    "tw-twse-tpex-official-market",
                ],
                "policy_gates": [
                    "require_connector_approved",
                    "require_schedule_active",
                    "require_source_health_ok",
                ],
            },
            {
                "dataset": "tw_broker_top",
                "market": "TW",
                "cadence": "daily",
                "source_class": "live_push",
                "connector_candidates": [
                    "tw-finmind-broker-daily-report",
                    "tw-finmind-broker-bulk-parquet",
                ],
                "policy_gates": [
                    "require_connector_approved",
                    "require_schedule_active",
                    "require_payload_push_health",
                ],
            },
        ]
    return []


def redact_evidence_refs(
    identity: OperatorIdentity,
    evidence_refs: list[dict[str, Any]],
    capabilities: Optional[list[str]] = None,
) -> tuple[list[dict[str, Any]], int]:
    """Redact evidence references that require an unavailable capability.

    ``identity`` remains part of the route-facing contract even though the
    current policy is expressed entirely by the supplied capability set.
    """

    del identity
    if capabilities is None:
        return list(evidence_refs), 0

    capability_set = set(capabilities)
    processed: list[dict[str, Any]] = []
    redacted_count = 0

    for ref in evidence_refs:
        if not isinstance(ref, dict):
            processed.append(ref)
            continue

        kind_key = (
            str(ref.get("evidence_type") or "").strip()
            or str(ref.get("type") or "").strip()
            or str(ref.get("ref_type") or "").strip()
            or str(ref.get("link_type") or "").strip()
        )
        if not kind_key:
            source_document = ref.get("source_document")
            if isinstance(source_document, dict):
                source_type = str(source_document.get("source_type") or "").strip()
                kind_key = SOURCE_TYPE_TO_EVIDENCE_KIND.get(source_type, "")

        required_capability = EVIDENCE_CAPABILITY_MAP.get(kind_key) if kind_key else None
        if required_capability and required_capability not in capability_set:
            redacted_count += 1
            ref_id = str(ref.get("ref_id") or ref.get("id") or "")
            try:
                evidence_kind = EvidenceKind(kind_key)
            except (TypeError, ValueError):
                evidence_kind = None
            redacted = RedactedEvidenceRef(
                ref_id=ref_id,
                kind=evidence_kind,
                required_capability=required_capability,
                reason="insufficient_capability",
            )
            processed.append(redacted.model_dump())
            continue
        processed.append(ref)

    return processed, redacted_count
