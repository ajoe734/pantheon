"""Tests for StrategySpecSeedStore and SeedMaterializationService.

Covers acceptance criteria:
- Idempotent materialization: same evidence_bundle_id + source_ids -> same seed_id
- Lineage refs persisted and retrievable
- Rejected source records block seed creation
- Seed metadata cannot request direct execution route
- Status-based filtering
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.seed_materializer import (
    MaterializationMode,
    SeedMaterializationError,
    SeedMaterializationService,
    materialize_seed_from_bundle,
)
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)
from services.source_ingestion.strategy_seed_store import (
    StrategySpecSeedReviewError,
    StrategySpecSeedStore,
    StrategySpecSeedStoreError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> StrategySpecSeedStore:
    return StrategySpecSeedStore(path=tmp_path / "seeds.jsonl")


def _bundle(bundle_id: str = "evbundle-test-001") -> EvidenceBundle:
    return EvidenceBundle(
        evidence_bundle_id=bundle_id,
        source_ids=["src-paper-test-001"],
        evidence_item_ids=["evi-test-001"],
        summary="LightGBM TW equity cross-sectional alpha strategy.",
        citation_refs=["test-paper#abstract"],
        confidence=0.85,
        license_scope="open",
        access_scope=["research", "strategy_seed"],
        created_by="Claude",
    )


def _source(status: str = "normalized") -> SourceRecord:
    return SourceRecord(
        source_id="src-paper-test-001",
        connector_id="conn-openalex-papers",
        source_type="paper",
        title="LightGBM TW equity alpha paper",
        content_ref="https://doi.org/10.1000/test-alpha",
        status=status,
        trace_id="trace-test-source",
        metadata={
            "license_scope": "open",
            "access_scope": ["research"],
            "keywords": ["LightGBM", "cross-sectional"],
            "strategy_seed": {
                "hypothesis": "LightGBM ranks TWSE equities by 5-day forward return.",
                "asset_class": ["equity"],
                "market_scope": ["Taiwan", "TWSE"],
                "holding_period": "5 trading days",
                "required_data": ["point-in-time OHLCV"],
                "backend_hint": "qlib",
                "feature_hints": ["momentum", "volatility"],
                "label_hints": ["5_day_forward_return"],
                "risk_notes": [],
            },
            "allowed_use": ["research", "strategy_seed"],
            "governance": {"direct_execution_allowed": False},
        },
    )


def _item() -> EvidenceItem:
    return EvidenceItem(
        evidence_item_id="evi-test-001",
        source_id="src-paper-test-001",
        item_type="abstract",
        content_ref="https://doi.org/10.1000/test-alpha#abstract",
        citation_label="test-paper#abstract",
        body="LightGBM ranks TWSE equities with momentum and volatility using OHLCV.",
        confidence=0.85,
        access_scope=["research"],
    )


def _memory_seed(**overrides) -> StrategySpecSeed:
    data = {
        "seed_id": "seed-rejected-memory",
        "source_id": "src-paper-test-001",
        "evidence_bundle_id": "evbundle-rejected-memory",
        "hypothesis": "LightGBM ranks TWSE equities by 5-day forward return.",
        "asset_class": ["equity"],
        "market_scope": ["Taiwan", "TWSE"],
        "holding_period": "5 trading days",
        "required_data": ["point-in-time OHLCV"],
        "backend_hint": "qlib",
        "feature_hints": ["LightGBM", "momentum", "volatility"],
        "label_hints": ["5_day_forward_return"],
        "risk_notes": ["lookahead bias postmortem"],
        "confidence": 0.4,
        "status": StrategySpecSeedStatus.REJECTED,
        "source_ids": ["src-paper-test-001"],
        "evidence_item_ids": ["evi-test-001"],
        "citation_refs": ["test-paper#abstract"],
        "trace_refs": ["trace-rejected-memory"],
        "created_at": "2026-06-11T00:00:00Z",
        "lineage": {
            "created_from": "evidence_bundle",
            "evidence_bundle_id": "evbundle-rejected-memory",
            "source_ids": ["src-paper-test-001"],
            "evidence_item_ids": ["evi-test-001"],
            "citation_refs": ["test-paper#abstract"],
            "promotion_requires": ["evidence_bundle_id"],
            "registry_write_performed": False,
            "execution_route": "none",
        },
        "metadata": {
            "research_only": True,
            "registry_write_performed": False,
            "execution_route": "none",
            "rejection_reason": "Matched failed LightGBM TWSE momentum postmortem.",
        },
    }
    data.update(overrides)
    return StrategySpecSeed(**data)


# ---------------------------------------------------------------------------
# Store: basic save / get / list
# ---------------------------------------------------------------------------


def test_store_save_and_get(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    result = service.materialize(
        _bundle(),
        source_records=[_source()],
        evidence_items=[_item()],
    )
    seed = result.seed

    retrieved = store.get(seed.seed_id)
    assert retrieved is not None
    assert retrieved.seed_id == seed.seed_id
    assert retrieved.evidence_bundle_id == "evbundle-test-001"


def test_store_list_all(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    service.materialize(_bundle("evbundle-a"), source_records=[_source()], evidence_items=[_item()])
    service.materialize(_bundle("evbundle-b"), source_records=[_source()], evidence_items=[_item()])

    all_seeds = store.list_all()
    bundle_ids = {s.evidence_bundle_id for s in all_seeds}
    assert "evbundle-a" in bundle_ids
    assert "evbundle-b" in bundle_ids


def test_store_list_by_status(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    result = service.materialize(_bundle(), source_records=[_source()], evidence_items=[_item()])
    seed = result.seed

    drafts = store.list_by_status("draft")
    assert any(s.seed_id == seed.seed_id for s in drafts)
    promoted = store.list_by_status("promoted_to_strategy_spec")
    assert not any(s.seed_id == seed.seed_id for s in promoted)


def test_store_list_by_bundle(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    service.materialize(_bundle("evbundle-c"), source_records=[_source()], evidence_items=[_item()])
    service.materialize(_bundle("evbundle-d"), source_records=[_source()], evidence_items=[_item()])

    seeds_c = store.list_by_bundle("evbundle-c")
    assert len(seeds_c) == 1
    assert seeds_c[0].evidence_bundle_id == "evbundle-c"


# ---------------------------------------------------------------------------
# Review state machine
# ---------------------------------------------------------------------------


def test_review_accept_then_convert_writes_audit_and_promotes(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)
    seed = service.materialize(_bundle(), source_records=[_source()], evidence_items=[_item()]).seed

    accepted, accept_decision = store.record_review_decision(
        seed.seed_id,
        decision="accept",
        reviewer_id="op-seed-review",
        reason="Evidence is sufficient for spec conversion review.",
        created_at="2026-06-12T01:00:00Z",
        idempotency_key="seed-review-accept",
    )
    promoted, convert_decision = store.record_review_decision(
        seed.seed_id,
        decision="convert-to-spec-seed",
        reviewer_id="op-seed-review",
        reason="Convert accepted seed into StrategySpec candidate.",
        target_refs=[{"type": "strategy_spec", "id": "strategy-alpha"}],
        created_at="2026-06-12T01:05:00Z",
        idempotency_key="seed-review-convert",
    )

    assert accepted.status == StrategySpecSeedStatus.ACCEPTED
    assert accept_decision.decision == "accept"
    assert promoted.status == StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC
    assert promoted.lineage["strategy_spec_conversion_eligible"] is True
    assert promoted.lineage["registry_write_performed"] is False
    assert promoted.lineage["execution_route"] == "none"
    decisions = promoted.lineage["review_decisions"]
    assert [item["decision"] for item in decisions] == ["accept", "convert_to_spec_seed"]
    assert decisions[-1]["target_refs"] == [{"type": "strategy_spec", "id": "strategy-alpha"}]
    assert convert_decision.to_status == "promoted_to_strategy_spec"


def test_review_decision_replays_from_durable_idempotency_key(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)
    seed = service.materialize(_bundle(), source_records=[_source()], evidence_items=[_item()]).seed

    accepted, first_decision = store.record_review_decision(
        seed.seed_id,
        decision="accept",
        reviewer_id="op-seed-review",
        reason="Evidence is sufficient.",
        created_at="2026-06-12T01:00:00Z",
        idempotency_key="seed-review-durable-accept",
        request_hash="hash-accept-v1",
    )
    replayed, replay_decision = store.record_review_decision(
        seed.seed_id,
        decision="accept",
        reviewer_id="op-seed-review",
        reason="Evidence is sufficient.",
        created_at="2026-06-12T01:10:00Z",
        idempotency_key="seed-review-durable-accept",
        request_hash="hash-accept-v1",
    )

    assert accepted.status == StrategySpecSeedStatus.ACCEPTED
    assert replayed.status == StrategySpecSeedStatus.ACCEPTED
    assert replay_decision.idempotent_replay is True
    assert replay_decision.decision_id == first_decision.decision_id
    stored = store.get(seed.seed_id)
    assert stored is not None
    assert [item["decision"] for item in stored.lineage["review_decisions"]] == ["accept"]


def test_review_decision_refuses_idempotency_key_payload_conflict(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)
    seed = service.materialize(_bundle(), source_records=[_source()], evidence_items=[_item()]).seed

    store.record_review_decision(
        seed.seed_id,
        decision="accept",
        reviewer_id="op-seed-review",
        reason="Evidence is sufficient.",
        idempotency_key="seed-review-conflict",
        request_hash="hash-accept-v1",
    )

    with pytest.raises(StrategySpecSeedReviewError) as exc_info:
        store.record_review_decision(
            seed.seed_id,
            decision="accept",
            reviewer_id="op-seed-review",
            reason="Different payload.",
            idempotency_key="seed-review-conflict",
            request_hash="hash-accept-v2",
        )
    assert exc_info.value.code == "idempotency_conflict"


def test_request_evidence_and_reject_terminal_refuses_further_transition(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)
    seed = service.materialize(_bundle(), source_records=[_source()], evidence_items=[_item()]).seed

    needs_evidence, request_decision = store.record_review_decision(
        seed.seed_id,
        decision="request-evidence",
        reviewer_id="op-seed-review",
        reason="Needs OOS validation detail.",
        created_at="2026-06-12T02:00:00Z",
    )
    rejected, reject_decision = store.record_review_decision(
        seed.seed_id,
        decision="reject",
        reviewer_id="op-seed-review",
        reason="Evidence remains insufficient.",
        created_at="2026-06-12T02:30:00Z",
    )

    assert needs_evidence.status == StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE
    assert request_decision.to_status == "needs_more_evidence"
    assert rejected.status == StrategySpecSeedStatus.REJECTED
    assert reject_decision.to_status == "rejected"
    with pytest.raises(StrategySpecSeedReviewError) as exc_info:
        store.record_review_decision(
            seed.seed_id,
            decision="accept",
            reviewer_id="op-seed-review",
        )
    assert exc_info.value.code == "terminal_seed_status"


def test_merge_seed_records_target_ref_and_terminal_status(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    source = _memory_seed(seed_id="seed-merge-source", status=StrategySpecSeedStatus.DRAFT)
    target = _memory_seed(seed_id="seed-merge-target", status=StrategySpecSeedStatus.DRAFT)
    store.save(source)
    store.save(target)

    merged, decision = store.merge_seed(
        "seed-merge-source",
        target_seed_id="seed-merge-target",
        reviewer_id="op-seed-review",
        reason="Duplicate hypothesis.",
        created_at="2026-06-12T03:00:00Z",
    )

    assert merged.status == StrategySpecSeedStatus.MERGED
    assert merged.lineage["merged_into_seed_id"] == "seed-merge-target"
    assert decision.decision == "merge"
    assert decision.target_refs == [{"type": "strategy_spec_seed", "id": "seed-merge-target"}]
    with pytest.raises(StrategySpecSeedReviewError) as exc_info:
        store.merge_seed(
            "seed-merge-source",
            target_seed_id="seed-merge-target",
            reviewer_id="op-seed-review",
        )
    assert exc_info.value.code == "terminal_seed_status"


def test_merge_seed_replays_from_durable_idempotency_key(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    source = _memory_seed(seed_id="seed-merge-replay-source", status=StrategySpecSeedStatus.DRAFT)
    target = _memory_seed(seed_id="seed-merge-replay-target", status=StrategySpecSeedStatus.DRAFT)
    store.save(source)
    store.save(target)

    merged, first_decision = store.merge_seed(
        "seed-merge-replay-source",
        target_seed_id="seed-merge-replay-target",
        reviewer_id="op-seed-review",
        reason="Duplicate hypothesis.",
        idempotency_key="seed-merge-replay",
        request_hash="hash-merge-v1",
    )
    replayed, replay_decision = store.merge_seed(
        "seed-merge-replay-source",
        target_seed_id="seed-merge-replay-target",
        reviewer_id="op-seed-review",
        reason="Duplicate hypothesis.",
        idempotency_key="seed-merge-replay",
        request_hash="hash-merge-v1",
    )

    assert merged.status == StrategySpecSeedStatus.MERGED
    assert replayed.status == StrategySpecSeedStatus.MERGED
    assert replay_decision.idempotent_replay is True
    assert replay_decision.decision_id == first_decision.decision_id
    stored = store.get("seed-merge-replay-source")
    assert stored is not None
    assert [item["decision"] for item in stored.lineage["review_decisions"]] == ["merge"]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_create_if_absent_returns_same_seed(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    r1 = service.materialize(
        _bundle(),
        source_records=[_source()],
        evidence_items=[_item()],
        mode=MaterializationMode.CREATE_IF_ABSENT,
    )
    r2 = service.materialize(
        _bundle(),
        source_records=[_source()],
        evidence_items=[_item()],
        mode=MaterializationMode.CREATE_IF_ABSENT,
    )

    assert r1.seed.seed_id == r2.seed.seed_id
    assert r1.was_created is True
    assert r2.was_created is False
    assert store.count() == 1


def test_same_bundle_same_sources_produces_same_seed_id(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    r1 = service.materialize(
        _bundle(),
        source_records=[_source()],
        evidence_items=[_item()],
        mode=MaterializationMode.REFRESH,
    )
    r2 = service.materialize(
        _bundle(),
        source_records=[_source()],
        evidence_items=[_item()],
        mode=MaterializationMode.REFRESH,
    )

    assert r1.seed.seed_id == r2.seed.seed_id
    assert store.count() == 1


def test_refresh_mode_upserts_not_duplicates(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    service.materialize(_bundle(), source_records=[_source()], evidence_items=[_item()], mode=MaterializationMode.REFRESH)
    service.materialize(_bundle(), source_records=[_source()], evidence_items=[_item()], mode=MaterializationMode.REFRESH)

    assert store.count() == 1


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


def test_lineage_fields_persisted_and_retrievable(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    result = service.materialize(
        _bundle(),
        source_records=[_source()],
        evidence_items=[_item()],
    )
    seed = result.seed
    retrieved = store.get(seed.seed_id)
    assert retrieved is not None

    lineage = retrieved.lineage
    assert lineage["created_from"] == "evidence_bundle"
    assert lineage["evidence_bundle_id"] == "evbundle-test-001"
    assert "src-paper-test-001" in lineage["source_ids"]
    assert "evi-test-001" in lineage["evidence_item_ids"]
    assert "test-paper#abstract" in lineage["citation_refs"]
    assert lineage["registry_write_performed"] is False
    assert lineage["execution_route"] == "none"
    assert "evidence_bundle_id" in lineage["promotion_requires"]


def test_license_and_allowed_use_stored(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    service.materialize(_bundle(), source_records=[_source()], evidence_items=[_item()])

    records = store._store.read_all()
    assert len(records) == 1
    record = records[0]
    assert record.get("license_scope") == "open"
    assert "research" in (record.get("allowed_use") or [])


# ---------------------------------------------------------------------------
# Rejection guard
# ---------------------------------------------------------------------------


def test_rejected_source_blocks_materialization(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    with pytest.raises(SeedMaterializationError, match="Rejected sources"):
        service.materialize(
            _bundle(),
            source_records=[_source(status="rejected")],
            evidence_items=[_item()],
        )

    assert store.count() == 0


def test_blocking_negative_memory_match_blocks_materialization(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.save(_memory_seed(status=StrategySpecSeedStatus.REJECTED))
    service = SeedMaterializationService(store=store)

    with pytest.raises(SeedMaterializationError, match="blocking negative_memory_match"):
        service.materialize(
            _bundle("evbundle-new-attempt"),
            source_records=[_source()],
            evidence_items=[_item()],
        )

    assert store.count() == 1


def test_warning_negative_memory_match_is_persisted_for_seed_card(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    result = service.materialize(
        _bundle("evbundle-warning-attempt"),
        source_records=[_source()],
        evidence_items=[_item()],
        negative_memory_records=[
            {
                "postmortem_id": "pm-twse-liquidity-warning",
                "status": "published",
                "title": "TWSE liquidity warning postmortem",
                "summary": "TWSE equity momentum showed liquidity slippage in crowded sessions.",
                "asset_class": ["equity"],
                "market_scope": ["TWSE"],
                "feature_hints": ["momentum", "liquidity"],
                "root_cause": "Crowding and slippage warning.",
            }
        ],
    )

    assert result.seed.negative_memory_match["warning_level"] == "warning"
    assert result.seed.negative_memory_match["matched_memory_id"] == "pm-twse-liquidity-warning"
    stored = store.get(result.seed.seed_id)
    assert stored is not None
    assert stored.negative_memory_match["warning_level"] == "warning"


# ---------------------------------------------------------------------------
# No direct execution route
# ---------------------------------------------------------------------------


def test_seed_with_forbidden_execution_route_blocked_by_store(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    seed = StrategySpecSeed(
        seed_id="seed-direct-exec-test",
        source_id="src-test",
        evidence_bundle_id="evbundle-exec-test",
        hypothesis="Test direct execution blocking.",
        asset_class=["equity"],
        market_scope=["Taiwan"],
        holding_period=None,
        required_data=["governed evidence"],
        backend_hint=None,
        feature_hints=[],
        label_hints=[],
        risk_notes=[],
        confidence=0.5,
        lineage={
            "created_from": "evidence_bundle",
            "evidence_bundle_id": "evbundle-exec-test",
            "source_ids": ["src-test"],
            "evidence_item_ids": ["evi-exec-test"],
            "citation_refs": ["exec-ref#1"],
            "promotion_requires": ["evidence_bundle_id"],
            "registry_write_performed": False,
            "execution_route": "none",
        },
        metadata={
            "research_only": True,
            "registry_write_performed": False,
            "execution_route": "broker_live",
        },
    )

    with pytest.raises(StrategySpecSeedStoreError, match="forbidden execution route"):
        store.save(seed)


def test_seed_with_blocking_negative_memory_match_blocked_by_store(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    seed = _memory_seed(
        seed_id="seed-blocking-negative-memory",
        status=StrategySpecSeedStatus.DRAFT,
        negative_memory_match={
            "warning_level": "blocking",
            "similarity": 0.92,
            "reason": "Blocking negative-memory match.",
            "matched_memory_id": "pm-blocking",
            "matched_memory_kind": "postmortem",
            "matched_terms": ["twse", "momentum"],
        },
    )

    with pytest.raises(StrategySpecSeedStoreError, match="blocking negative_memory_match"):
        store.save(seed)


def test_seed_with_broker_backend_hint_blocked_by_store(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    seed = StrategySpecSeed(
        seed_id="seed-broker-hint-test",
        source_id="src-test",
        evidence_bundle_id="evbundle-broker-test",
        hypothesis="Test broker backend hint blocking.",
        asset_class=["equity"],
        market_scope=["Taiwan"],
        holding_period=None,
        required_data=["governed evidence"],
        backend_hint="live_broker_direct",
        feature_hints=[],
        label_hints=[],
        risk_notes=[],
        confidence=0.5,
        lineage={
            "created_from": "evidence_bundle",
            "evidence_bundle_id": "evbundle-broker-test",
            "source_ids": ["src-test"],
            "evidence_item_ids": ["evi-broker-test"],
            "citation_refs": ["broker-ref#1"],
            "promotion_requires": ["evidence_bundle_id"],
            "registry_write_performed": False,
            "execution_route": "none",
        },
        metadata={
            "research_only": True,
            "registry_write_performed": False,
            "execution_route": "none",
        },
    )

    with pytest.raises(StrategySpecSeedStoreError, match="forbidden execution keyword"):
        store.save(seed)


def test_normal_seed_with_research_backend_is_accepted(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = SeedMaterializationService(store=store)

    result = service.materialize(_bundle(), source_records=[_source()], evidence_items=[_item()])
    assert result.seed.metadata.get("execution_route") == "none"
    assert store.count() == 1


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


def test_convenience_wrapper_materialize_seed_from_bundle(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    result = materialize_seed_from_bundle(
        _bundle(),
        store=store,
        source_records=[_source()],
        evidence_items=[_item()],
        requested_by="Claude",
    )

    assert result.was_created is True
    assert result.seed.evidence_bundle_id == "evbundle-test-001"
    assert result.seed.lineage["execution_route"] == "none"
    assert result.seed.metadata["research_only"] is True
