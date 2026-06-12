"""IDS-008 — Audit, lineage, and end-to-end tests.

Covers:
1. Trainer-commit e2e: trainer_commit event -> InteractionSourceRecord ->
   classify/redact/negative-match -> SeedCandidate in review inbox.
2. Committee-memo e2e: committee_verdict artifact -> InteractionSourceRecord ->
   classify/redact/negative-match -> SeedCandidate in review inbox.
3. Audit events: every step (record/redact/classify/negative_match/candidate)
   emits a structured IDSAuditEvent captured by IDSAuditEventStore.
4. Accept/reject audit: seed store review transitions emit accept/reject events.
5. Lineage query: query_seed_lineage returns the full trace from seed back to
   the source InteractionSourceRecord; raw_ref is a reference, not inline text.
6. Privacy assertions: raw prompt never surfaces in the seed, lineage, or
   audit events.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.source_ingestion.agora_seed_bridge import (
    AgoraSeedArtifactKind,
    extract_agora_seed,
)
from services.source_ingestion.ids_audit import (
    IDSAuditEventStore,
    IDSAuditOutcome,
    IDSAuditStep,
)
from services.source_ingestion.ids_lineage import query_seed_lineage
from services.source_ingestion.interaction_source_store import (
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionSourceRecordStore,
    InteractionVisibility,
)
from services.source_ingestion.redaction_guard import RedactionContext
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore
from services.source_ingestion.trainer_seed_bridge import TrainerSeedBridge, TrainerSeedKind


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_stores(
    tmp_path: Path,
) -> tuple[InteractionSourceRecordStore, StrategySpecSeedStore, IDSAuditEventStore]:
    interaction_store = InteractionSourceRecordStore(path=tmp_path / "interactions.jsonl")
    audit_store = IDSAuditEventStore(path=tmp_path / "ids_audit_events.jsonl")
    seed_store = StrategySpecSeedStore(path=tmp_path / "seeds.jsonl", audit_store=audit_store)
    return interaction_store, seed_store, audit_store


def _trainer_event(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_type": "trainer_commit",
        "event_id": "tevt-ids008-001",
        "session_id": "trn-ids008-001",
        "persona_id": "persona-alpha",
        "summary": (
            "TWSE momentum factor signal can rank liquid equities for "
            "5-day forward returns using point-in-time OHLCV."
        ),
        "seed_kind": "new_strategy",
        "committed_by": "operator-ids008",
        "committed_at": "2026-06-12T06:00:00Z",
        "raw_ref": "evidence://trainer/trn-ids008-001/tevt-ids008-001",
        "artifact_refs": {
            "candidate_artifact_ref": "artifact-ids008-candidate-001",
            "after_artifact_ref": "artifact-ids008-committed-001",
        },
        "strategy_seed": {
            "hypothesis": "TWSE momentum can rank equities for 5-day returns.",
            "asset_class": ["equity"],
            "market_scope": ["TWSE"],
            "holding_period": "5 trading days",
            "required_data": ["point-in-time OHLCV"],
            "backend_hint": "qlib",
            "feature_hints": ["momentum"],
            "label_hints": ["5_day_forward_return"],
        },
    }
    payload.update(overrides)
    return payload


def _committee_record(tmp_path: Path | None = None) -> InteractionSourceRecord:
    raw_ref = "evidence://committee/session-ids008/raw-transcript"
    artifact_ref = "agora://committee_verdict/artifact-ids008-verdict-001"
    return InteractionSourceRecord(
        interaction_id="interaction-ids008-committee",
        source_surface="committee",
        actor_type="reviewer",
        persona_refs=["persona-alpha"],
        session_id="session-ids008",
        raw_ref=raw_ref,
        summary=(
            "Committee verdict: cap max drawdown at 15% for all TWSE momentum "
            "strategy candidates with holding period under 10 days."
        ),
        evidence_refs=[
            {"ref": raw_ref, "kind": "raw_interaction"},
            {"ref": artifact_ref, "kind": "verdict"},
        ],
        visibility=InteractionVisibility.SHARED.value,
        redaction_status=InteractionRedactionStatus.PENDING.value,
        created_at="2026-06-12T06:00:00Z",
        updated_at="2026-06-12T06:00:00Z",
        metadata={
            "artifact_kind": "committee_verdict",
            "artifact_ref": artifact_ref,
            "research_only": True,
            "execution_route": "none",
            "strategy_seed": {
                "hypothesis": "Risk overlay: cap max drawdown for momentum seeds.",
                "asset_class": ["equity"],
                "market_scope": ["TWSE"],
                "required_data": ["governed committee artifact"],
                "risk_notes": ["drawdown_cap_required"],
            },
        },
    )


# ---------------------------------------------------------------------------
# 1. Trainer-commit e2e
# ---------------------------------------------------------------------------


class TestTrainerE2E:
    def test_full_pipeline_produces_draft_seed(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        bridge = TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            created_by="operator-ids008",
            audit_store=audit_store,
        )

        result = bridge.ingest_event(_trainer_event())

        # Interaction record stored and passed redaction
        record = interaction_store.get(result.interaction_record.interaction_id)
        assert record is not None
        assert record.redaction_status == InteractionRedactionStatus.PASSED
        assert record.source_surface.value == "trainer"

        # Seed is in draft status in the review inbox
        drafts = seed_store.list_by_status("draft")
        assert any(s.seed_id == result.seed.seed_id for s in drafts)

        # Lineage recorded in seed
        stored = seed_store.get(result.seed.seed_id)
        assert stored is not None
        assert stored.lineage["created_from"] == "trainer_seed_bridge"
        extraction = stored.lineage["trainer_seed_extraction_ref"]
        assert extraction["event_type"] == "trainer_commit"
        assert extraction["event_id"] == "tevt-ids008-001"

    def test_all_audit_steps_emitted(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        bridge = TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            audit_store=audit_store,
        )

        result = bridge.ingest_event(_trainer_event())

        events = audit_store.list_by_interaction(result.interaction_record.interaction_id)
        steps_seen = {e.step for e in events}

        assert IDSAuditStep.RECORD in steps_seen
        assert IDSAuditStep.CLASSIFY in steps_seen
        assert IDSAuditStep.REDACT in steps_seen
        assert IDSAuditStep.NEGATIVE_MATCH in steps_seen
        assert IDSAuditStep.CANDIDATE in steps_seen

    def test_all_audit_outcomes_are_success(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        bridge = TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            audit_store=audit_store,
        )

        result = bridge.ingest_event(_trainer_event())

        events = audit_store.list_by_interaction(result.interaction_record.interaction_id)
        for event in events:
            assert event.outcome != IDSAuditOutcome.FAILED, (
                f"Step {event.step.value} had outcome={event.outcome.value}"
            )

    def test_audit_candidate_event_links_seed_id(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        bridge = TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            audit_store=audit_store,
        )

        result = bridge.ingest_event(_trainer_event())

        candidate_events = [
            e for e in audit_store.list_by_seed(result.seed.seed_id)
            if e.step == IDSAuditStep.CANDIDATE
        ]
        assert candidate_events, "No CANDIDATE audit event found for the seed"
        assert candidate_events[0].seed_id == result.seed.seed_id

    def test_accept_emits_audit_event(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        bridge = TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            audit_store=audit_store,
        )

        result = bridge.ingest_event(_trainer_event())
        seed_store.record_review_decision(
            result.seed.seed_id,
            decision="accept",
            reviewer_id="reviewer-ids008",
            reason="Well-formed hypothesis with evidence",
        )

        accept_events = [
            e for e in audit_store.list_by_seed(result.seed.seed_id)
            if e.step == IDSAuditStep.ACCEPT
        ]
        assert accept_events, "No ACCEPT audit event found after review accept"

    def test_reject_emits_audit_event(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        bridge = TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            audit_store=audit_store,
        )

        result = bridge.ingest_event(_trainer_event())
        seed_store.record_review_decision(
            result.seed.seed_id,
            decision="reject",
            reviewer_id="reviewer-ids008",
            reason="Insufficient evidence",
        )

        reject_events = [
            e for e in audit_store.list_by_seed(result.seed.seed_id)
            if e.step == IDSAuditStep.REJECT
        ]
        assert reject_events, "No REJECT audit event found after review reject"


# ---------------------------------------------------------------------------
# 2. Committee-memo e2e (IDS-005 bridge)
# ---------------------------------------------------------------------------


class TestCommitteeE2E:
    def test_committee_memo_produces_draft_seed(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        ctx = RedactionContext(
            tenant_id="tenant-ids008",
            requesting_visibility=InteractionVisibility.SHARED,
        )

        result = extract_agora_seed(
            _committee_record(),
            context=ctx,
            store=seed_store,
            artifact_kind=AgoraSeedArtifactKind.COMMITTEE_VERDICT,
            created_by="operator-ids008",
            audit_store=audit_store,
        )

        assert result.seed is not None
        assert result.stored is True
        stored = seed_store.get(result.seed.seed_id)
        assert stored is not None
        assert stored.status.value in ("draft",)

    def test_committee_all_audit_steps_emitted(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        ctx = RedactionContext(
            tenant_id="tenant-ids008",
            requesting_visibility=InteractionVisibility.SHARED,
        )

        result = extract_agora_seed(
            _committee_record(),
            context=ctx,
            store=seed_store,
            artifact_kind=AgoraSeedArtifactKind.COMMITTEE_VERDICT,
            created_by="operator-ids008",
            audit_store=audit_store,
        )

        events = audit_store.list_by_interaction(result.interaction_record.interaction_id)
        steps_seen = {e.step for e in events}
        assert IDSAuditStep.RECORD in steps_seen
        assert IDSAuditStep.REDACT in steps_seen
        assert IDSAuditStep.CLASSIFY in steps_seen
        assert IDSAuditStep.NEGATIVE_MATCH in steps_seen
        assert IDSAuditStep.CANDIDATE in steps_seen

    def test_committee_audit_no_raw_content_in_events(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        ctx = RedactionContext(
            tenant_id="tenant-ids008",
            requesting_visibility=InteractionVisibility.SHARED,
        )

        result = extract_agora_seed(
            _committee_record(),
            context=ctx,
            store=seed_store,
            audit_store=audit_store,
        )

        _RAW_KEYS = {
            "raw_text", "raw_content", "raw_prompt", "prompt",
            "transcript", "messages", "message", "body", "content",
        }
        for event in audit_store.list_all():
            details = event.to_dict().get("details") or {}
            for key in details:
                assert key not in _RAW_KEYS, (
                    f"Audit event step={event.step.value} has raw content key {key!r}"
                )


# ---------------------------------------------------------------------------
# 3. Lineage query
# ---------------------------------------------------------------------------


class TestLineageQuery:
    def test_trainer_lineage_traces_to_source(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        bridge = TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            audit_store=audit_store,
        )

        result = bridge.ingest_event(_trainer_event())
        seed = seed_store.get(result.seed.seed_id)
        assert seed is not None

        lineage = query_seed_lineage(
            seed.to_dict(),
            interaction_store=interaction_store,
            audit_store=audit_store,
        )

        assert lineage["seed_id"] == seed.seed_id
        assert lineage["source_surface"] == "trainer"
        assert lineage["interaction_id"] == result.interaction_record.interaction_id
        assert lineage["extraction_ref"]["event_type"] == "trainer_commit"
        assert lineage["interaction_record"] is not None
        assert lineage["raw_ref"] == "evidence://trainer/trn-ids008-001/tevt-ids008-001"
        assert len(lineage["audit_events"]) > 0

    def test_committee_lineage_traces_to_source(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        ctx = RedactionContext(
            tenant_id="tenant-ids008",
            requesting_visibility=InteractionVisibility.SHARED,
        )

        result = extract_agora_seed(
            _committee_record(),
            context=ctx,
            store=seed_store,
            audit_store=audit_store,
        )
        seed = seed_store.get(result.seed.seed_id)
        assert seed is not None

        lineage = query_seed_lineage(
            seed.to_dict(),
            interaction_store=interaction_store,
            audit_store=audit_store,
        )

        assert lineage["seed_id"] == seed.seed_id
        assert lineage["interaction_id"] == result.interaction_record.interaction_id
        assert lineage["extraction_ref"] is not None


# ---------------------------------------------------------------------------
# 4. Privacy assertions
# ---------------------------------------------------------------------------


class TestPrivacyAssertions:
    def test_raw_prompt_never_surfaces_in_trainer_seed(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        bridge = TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            audit_store=audit_store,
        )

        result = bridge.ingest_event(_trainer_event())
        seed = seed_store.get(result.seed.seed_id)
        assert seed is not None

        seed_dict = seed.to_dict()
        _assert_no_raw_keys(seed_dict, "seed")

        lineage = query_seed_lineage(
            seed_dict,
            interaction_store=interaction_store,
            audit_store=audit_store,
        )
        assert lineage["privacy_assertions"]["all_passed"], (
            f"Privacy assertion failed: {lineage['privacy_assertions']}"
        )

    def test_raw_prompt_never_surfaces_in_committee_seed(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        ctx = RedactionContext(
            tenant_id="tenant-ids008",
            requesting_visibility=InteractionVisibility.SHARED,
        )

        result = extract_agora_seed(
            _committee_record(),
            context=ctx,
            store=seed_store,
            audit_store=audit_store,
        )
        seed = seed_store.get(result.seed.seed_id)
        assert seed is not None

        seed_dict = seed.to_dict()
        _assert_no_raw_keys(seed_dict, "seed")

        lineage = query_seed_lineage(
            seed_dict,
            interaction_store=interaction_store,
            audit_store=audit_store,
        )
        assert lineage["privacy_assertions"]["all_passed"], (
            f"Privacy assertion failed: {lineage['privacy_assertions']}"
        )

    def test_raw_ref_is_a_reference_not_inline_text(self, tmp_path: Path) -> None:
        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        bridge = TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            audit_store=audit_store,
        )

        result = bridge.ingest_event(_trainer_event())
        seed = seed_store.get(result.seed.seed_id)
        assert seed is not None

        lineage = query_seed_lineage(seed.to_dict(), interaction_store=interaction_store)
        raw_ref = lineage["raw_ref"]
        assert raw_ref is not None
        assert "\n" not in raw_ref
        assert len(raw_ref) < 1024

    def test_trainer_seed_with_raw_prompt_refused_at_record_boundary(self, tmp_path: Path) -> None:
        """Raw teaching log in the event payload must be rejected before record creation."""
        from services.source_ingestion.trainer_seed_bridge import TrainerSeedBridgeError

        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        bridge = TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            audit_store=audit_store,
        )

        bad_event = _trainer_event(transcript="User: buy signal?\nAssistant: yes always buy")
        with pytest.raises(TrainerSeedBridgeError) as exc_info:
            bridge.ingest_event(bad_event)
        assert "raw" in str(exc_info.value).lower() or "teaching" in str(exc_info.value).lower()
        assert seed_store.list_all() == [], "No seed should be created when raw teaching log is present"

    def test_raw_transcript_in_summary_refused_at_redaction_boundary(self, tmp_path: Path) -> None:
        """A summary that IS a raw transcript must fail redaction and block the seed."""
        from services.source_ingestion.trainer_seed_bridge import TrainerSeedBridgeError

        interaction_store, seed_store, audit_store = _make_stores(tmp_path)
        bridge = TrainerSeedBridge(
            interaction_store=interaction_store,
            seed_store=seed_store,
            audit_store=audit_store,
        )

        raw_transcript_summary = "User: what signal?\nAssistant: momentum signal"
        bad_event = _trainer_event(summary=raw_transcript_summary)
        with pytest.raises(TrainerSeedBridgeError) as exc_info:
            bridge.ingest_event(bad_event)
        assert exc_info.value.code == "redaction_failed"
        assert seed_store.list_all() == [], "No seed should be created when summary is a raw transcript"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

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


def _assert_no_raw_keys(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k) in _RAW_CONTENT_KEYS and _nonempty(v):
                raise AssertionError(
                    f"Raw content key {k!r} found at {path}.{k}: {str(v)[:60]!r}"
                )
            _assert_no_raw_keys(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _assert_no_raw_keys(item, f"{path}[{i}]")


def _nonempty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict)):
        return bool(v)
    return True
