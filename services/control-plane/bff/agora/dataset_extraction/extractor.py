"""AgoraDatasetStore and extract_evidence — idempotent routing to observe/learn buckets.

All writes are idempotent by evidence_id.  Concurrent access is protected by
an RLock so multiple BFF worker threads do not corrupt the store.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from .models import (
    AgoraInteractionEvidenceRequest,
    DatasetKind,
    DatasetRecord,
    route_to_dataset,
)


class AgoraDatasetStore:
    """In-memory store for Agora interaction dataset records.

    Writes are idempotent: a second call with the same evidence_id returns the
    existing record with idempotent=True and does not modify state.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: Dict[str, DatasetRecord] = {}

    def add_or_get(self, record: DatasetRecord) -> tuple[DatasetRecord, bool]:
        """Upsert a record by evidence_id.

        Returns (record, is_new).  When is_new is False the returned record has
        idempotent=True to signal a duplicate submission.
        """
        with self._lock:
            existing = self._records.get(record.evidence_id)
            if existing is not None:
                return existing.model_copy(update={"idempotent": True}), False
            self._records[record.evidence_id] = record
            return record, True

    def get(self, evidence_id: str) -> Optional[DatasetRecord]:
        """Return a single record or None."""
        with self._lock:
            return self._records.get(evidence_id)

    def list_by_dataset(
        self,
        dataset_kind: DatasetKind,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        page_size: int = 50,
    ) -> List[DatasetRecord]:
        """Return records for a given dataset kind, optionally filtered by tenant/user."""
        with self._lock:
            records = [
                r for r in self._records.values()
                if r.dataset_kind == dataset_kind
                and (tenant_id is None or r.tenant_id == tenant_id)
                and (user_id is None or r.user_id == user_id)
            ]
        return records[:page_size]


def extract_evidence(
    evidence: AgoraInteractionEvidenceRequest,
    *,
    tenant_id: str,
    user_id: str,
    extracted_at: str,
    store: AgoraDatasetStore,
) -> tuple[DatasetRecord, bool]:
    """Extract an evidence record into the appropriate governed dataset.

    Routing:
      observe — ask, journal, note, insight
      learn   — feedback, training_example

    Returns (record, is_new).  When is_new is False the evidence_id was already
    present and no state was modified (idempotent).
    """
    dataset_kind = route_to_dataset(evidence.interaction_kind.value)
    record = DatasetRecord(
        evidence_id=evidence.evidence_id,
        dataset_kind=dataset_kind,
        interaction_kind=evidence.interaction_kind,
        persona_id=evidence.persona_id,
        session_id=evidence.session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        content=dict(evidence.content),
        source_refs=list(evidence.source_refs),
        learning_eligible=evidence.learning_eligible,
        captured_at=evidence.captured_at,
        extracted_at=extracted_at,
    )
    return store.add_or_get(record)


__all__ = ["AgoraDatasetStore", "extract_evidence"]
