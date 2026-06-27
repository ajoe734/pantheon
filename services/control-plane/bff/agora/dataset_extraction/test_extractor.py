"""Unit tests for AgoraDatasetStore and extract_evidence.

Covers:
  - Routing: each interaction kind reaches the correct dataset
  - Idempotency: duplicate evidence_id returns existing record unmodified
  - Governance proof: records carry required boundary fields
  - Listing: scoped by tenant/user
  - Concurrent safety: multiple threads do not corrupt the store
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from .extractor import AgoraDatasetStore, extract_evidence
from .models import (
    AgoraInteractionEvidenceRequest,
    DatasetKind,
    InteractionKind,
    route_to_dataset,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_evidence(
    evidence_id: str = "ev-001",
    interaction_kind: str = "ask",
    persona_id: str = "persona-a",
    session_id: str | None = "sess-001",
    content: dict | None = None,
    captured_at: str = "2026-06-27T10:00:00Z",
) -> AgoraInteractionEvidenceRequest:
    return AgoraInteractionEvidenceRequest(
        evidence_id=evidence_id,
        interaction_kind=interaction_kind,  # type: ignore[arg-type]
        persona_id=persona_id,
        session_id=session_id,
        content=content or {"text": "question text"},
        source_refs=["session://sess-001"],
        captured_at=captured_at,
    )


def _extract(
    evidence: AgoraInteractionEvidenceRequest,
    store: AgoraDatasetStore,
    *,
    tenant_id: str = "t1",
    user_id: str = "u1",
    extracted_at: str = "2026-06-27T10:01:00Z",
) -> tuple[Any, bool]:
    return extract_evidence(
        evidence,
        tenant_id=tenant_id,
        user_id=user_id,
        extracted_at=extracted_at,
        store=store,
    )


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------

class TestRouteToDataset:
    def test_ask_routes_to_observe(self) -> None:
        assert route_to_dataset("ask") == DatasetKind.OBSERVE

    def test_journal_routes_to_observe(self) -> None:
        assert route_to_dataset("journal") == DatasetKind.OBSERVE

    def test_note_routes_to_observe(self) -> None:
        assert route_to_dataset("note") == DatasetKind.OBSERVE

    def test_insight_routes_to_observe(self) -> None:
        assert route_to_dataset("insight") == DatasetKind.OBSERVE

    def test_feedback_routes_to_learn(self) -> None:
        assert route_to_dataset("feedback") == DatasetKind.LEARN

    def test_training_example_routes_to_learn(self) -> None:
        assert route_to_dataset("training_example") == DatasetKind.LEARN

    def test_unknown_kind_defaults_to_observe(self) -> None:
        assert route_to_dataset("unknown_future_kind") == DatasetKind.OBSERVE


# ---------------------------------------------------------------------------
# Extraction tests
# ---------------------------------------------------------------------------

class TestExtractEvidence:
    def test_first_extraction_is_new(self) -> None:
        store = AgoraDatasetStore()
        evidence = _make_evidence()
        record, is_new = _extract(evidence, store)
        assert is_new is True

    def test_record_has_correct_dataset_kind(self) -> None:
        store = AgoraDatasetStore()
        record, _ = _extract(_make_evidence(interaction_kind="ask"), store)
        assert record.dataset_kind == DatasetKind.OBSERVE

        store2 = AgoraDatasetStore()
        record2, _ = _extract(_make_evidence(interaction_kind="feedback"), store2)
        assert record2.dataset_kind == DatasetKind.LEARN

    def test_record_carries_governance_proof(self) -> None:
        store = AgoraDatasetStore()
        record, _ = _extract(_make_evidence(), store)
        assert record.governance_boundary == "observe_or_learn_only"
        assert record.no_promote_proof == "agora_observe_learn_only"
        assert record.no_runtime_mutation_proof == "agora_evidence_extract_only"

    def test_record_not_idempotent_on_first_call(self) -> None:
        store = AgoraDatasetStore()
        record, _ = _extract(_make_evidence(), store)
        assert record.idempotent is False

    def test_duplicate_evidence_id_is_idempotent(self) -> None:
        store = AgoraDatasetStore()
        evidence = _make_evidence(evidence_id="ev-dup")
        first, is_new_first = _extract(evidence, store)
        second, is_new_second = _extract(evidence, store)

        assert is_new_first is True
        assert is_new_second is False
        assert second.idempotent is True
        assert second.evidence_id == "ev-dup"

    def test_duplicate_does_not_overwrite_extracted_at(self) -> None:
        store = AgoraDatasetStore()
        evidence = _make_evidence(evidence_id="ev-ts")
        _extract(evidence, store, extracted_at="2026-06-27T10:00:00Z")
        second, _ = _extract(evidence, store, extracted_at="2026-06-27T11:00:00Z")
        # Second call returns original extracted_at
        assert second.extracted_at == "2026-06-27T10:00:00Z"

    def test_record_stores_content_and_source_refs(self) -> None:
        store = AgoraDatasetStore()
        evidence = _make_evidence(
            content={"text": "how does the strategy work?"},
            session_id="sess-xyz",
        )
        record, _ = _extract(evidence, store)
        assert record.content == {"text": "how does the strategy work?"}
        assert record.source_refs == ["session://sess-001"]
        assert record.session_id == "sess-xyz"

    def test_record_tenant_and_user_from_context(self) -> None:
        store = AgoraDatasetStore()
        record, _ = _extract(
            _make_evidence(),
            store,
            tenant_id="tenant-abc",
            user_id="user-xyz",
        )
        assert record.tenant_id == "tenant-abc"
        assert record.user_id == "user-xyz"


# ---------------------------------------------------------------------------
# Store listing tests
# ---------------------------------------------------------------------------

class TestAgoraDatasetStore:
    def test_get_returns_none_for_missing_id(self) -> None:
        store = AgoraDatasetStore()
        assert store.get("missing") is None

    def test_get_returns_record_after_add(self) -> None:
        store = AgoraDatasetStore()
        evidence = _make_evidence(evidence_id="ev-get")
        _extract(evidence, store)
        record = store.get("ev-get")
        assert record is not None
        assert record.evidence_id == "ev-get"

    def test_list_by_dataset_observe_only(self) -> None:
        store = AgoraDatasetStore()
        _extract(_make_evidence("ask-1", "ask"), store)
        _extract(_make_evidence("fb-1", "feedback"), store)
        _extract(_make_evidence("note-1", "note"), store)
        observe = store.list_by_dataset(DatasetKind.OBSERVE)
        assert len(observe) == 2
        assert all(r.dataset_kind == DatasetKind.OBSERVE for r in observe)

    def test_list_by_dataset_learn_only(self) -> None:
        store = AgoraDatasetStore()
        _extract(_make_evidence("fb-1", "feedback"), store)
        _extract(_make_evidence("te-1", "training_example"), store)
        _extract(_make_evidence("ask-1", "ask"), store)
        learn = store.list_by_dataset(DatasetKind.LEARN)
        assert len(learn) == 2
        assert all(r.dataset_kind == DatasetKind.LEARN for r in learn)

    def test_list_scoped_by_tenant(self) -> None:
        store = AgoraDatasetStore()
        _extract(_make_evidence("ev-t1"), store, tenant_id="t1")
        _extract(_make_evidence("ev-t2"), store, tenant_id="t2")
        result = store.list_by_dataset(DatasetKind.OBSERVE, tenant_id="t1")
        assert len(result) == 1
        assert result[0].tenant_id == "t1"

    def test_list_scoped_by_user(self) -> None:
        store = AgoraDatasetStore()
        _extract(_make_evidence("ev-ua"), store, user_id="ua")
        _extract(_make_evidence("ev-ub"), store, user_id="ub")
        result = store.list_by_dataset(DatasetKind.OBSERVE, user_id="ua")
        assert len(result) == 1
        assert result[0].user_id == "ua"

    def test_page_size_limits_results(self) -> None:
        store = AgoraDatasetStore()
        for i in range(10):
            _extract(_make_evidence(f"ev-{i}", "ask"), store)
        result = store.list_by_dataset(DatasetKind.OBSERVE, page_size=3)
        assert len(result) == 3

    def test_concurrent_writes_are_safe(self) -> None:
        store = AgoraDatasetStore()
        errors: list[Exception] = []

        def add(i: int) -> None:
            try:
                _extract(_make_evidence(f"ev-{i}"), store)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=add, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(store.list_by_dataset(DatasetKind.OBSERVE)) == 50


# ---------------------------------------------------------------------------
# Model field tests
# ---------------------------------------------------------------------------

class TestInteractionKindEnum:
    def test_all_kinds_are_routed(self) -> None:
        for kind in InteractionKind:
            dataset = route_to_dataset(kind.value)
            assert dataset in (DatasetKind.OBSERVE, DatasetKind.LEARN)

    def test_feedback_and_training_example_are_learn(self) -> None:
        assert route_to_dataset(InteractionKind.FEEDBACK.value) == DatasetKind.LEARN
        assert route_to_dataset(InteractionKind.TRAINING_EXAMPLE.value) == DatasetKind.LEARN

    def test_remaining_kinds_are_observe(self) -> None:
        observe_kinds = {InteractionKind.ASK, InteractionKind.JOURNAL, InteractionKind.NOTE, InteractionKind.INSIGHT}
        for kind in observe_kinds:
            assert route_to_dataset(kind.value) == DatasetKind.OBSERVE


class TestAgoraInteractionEvidenceRequest:
    def test_valid_request_constructs(self) -> None:
        req = AgoraInteractionEvidenceRequest(
            evidence_id="ev-valid",
            interaction_kind="feedback",
            persona_id="persona-1",
            captured_at="2026-06-27T00:00:00Z",
        )
        assert req.spec_version == "1.0"
        assert req.learning_eligible is True

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception):
            AgoraInteractionEvidenceRequest(
                evidence_id="ev-x",
                interaction_kind="ask",
                persona_id="p",
                captured_at="2026-06-27T00:00:00Z",
                unknown_field="bad",  # type: ignore[call-arg]
            )

    def test_evidence_id_required_non_empty(self) -> None:
        with pytest.raises(Exception):
            AgoraInteractionEvidenceRequest(
                evidence_id="",
                interaction_kind="ask",
                persona_id="p",
                captured_at="2026-06-27T00:00:00Z",
            )
