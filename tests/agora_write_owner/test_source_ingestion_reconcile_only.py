"""
Tests verifying that Source Ingestion remains reconcile-only and does not become
the write owner for Agora persistent tables.
"""
from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from services.agora.service import AgoraWriteService
from services.agora.store import AgoraStore
from services.agora.write_authority import is_authorized
from services.source_ingestion.agora_seed_bridge import (
    AgoraSeedArtifactKind,
    AgoraSeedBridge,
    InteractionSeedKind,
)
from services.source_ingestion.interaction_source_store import (
    InteractionActorType,
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionSourceSurface,
    InteractionVisibility,
)
from services.source_ingestion.redaction_guard import RedactionContext
from services.source_ingestion.strategy_seed_builder import StrategySpecSeed
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


def test_source_ingestion_has_no_agora_write_authority() -> None:
    # Source Ingestion worker / reconciler roles
    ingestion_roles = ["source_ingestion", "ingestion_worker", "reconciler"]

    # Source Ingestion must NOT hold write authority over Agora core tables
    assert not is_authorized("AgoraSession", "create", ingestion_roles)
    assert not is_authorized("AgoraSession", "open", ingestion_roles)
    assert not is_authorized("AgoraSession", "close", ingestion_roles)
    assert not is_authorized("AgoraCommitteeMemo", "submit", ingestion_roles)
    assert not is_authorized("AgoraCommitteeMemo", "publish", ingestion_roles)
    assert not is_authorized("AgoraCommitteeEvidencePack", "create", ingestion_roles)
    assert not is_authorized("DecisionJournalEntry", "create", ingestion_roles)
    assert not is_authorized("DecisionJournalEntry", "patch", ingestion_roles)
    assert not is_authorized("AgoraSignal", "create", ingestion_roles)


def test_source_ingestion_reconciles_published_agora_memo(temp_workspace: Path) -> None:
    agora_store = AgoraStore()
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    memo_id = f"memo-{uuid.uuid4().hex[:8]}"

    # Agora creates and publishes committee memo
    agora_store.create_session(
        session_id=session_id,
        title="Momentum Strategy Review",
        actor_id="operator-alice",
        payload={"mode": "committee", "targetEntity": {"type": "strategy", "id": "strat-momentum-01"}},
    )
    memo = agora_store.submit_committee_memo(
        session_id,
        memo_id=memo_id,
        actor_id="operator-alice",
        payload={
            "memoType": "committee_summary",
            "summary": "Implement ATR-based trailing stop for momentum strategy",
            "recommendations": [{"action": "mutate", "target": "strat-momentum-01"}],
            "evidenceRefs": ["ev-1"],
        },
    )
    assert memo is not None
    published_memo = agora_store.publish_committee_memo(session_id, memo_id, actor_id="approver-bob")
    assert published_memo is not None
    assert published_memo.status == "published"

    # Source Ingestion reads the published memo in reconcile-only mode and extracts a strategy seed
    seed_store = StrategySpecSeedStore(path=temp_workspace / "seeds.jsonl")
    bridge = AgoraSeedBridge(store=seed_store)

    artifact_ref = f"agora://committee/{session_id}/memos/{memo_id}"
    raw_ref = f"agora://sessions/{session_id}/raw_transcript"
    interaction_record = InteractionSourceRecord(
        interaction_id=f"int-{uuid.uuid4().hex[:8]}",
        source_surface="committee",
        actor_type="operator",
        persona_refs=["persona-quant"],
        session_id=session_id,
        raw_ref=raw_ref,
        summary="Trading strategy hypothesis for momentum breakout on equities using ATR trailing stop",
        evidence_refs=[
            {"ref": raw_ref, "kind": "raw_interaction"},
            {"ref": artifact_ref, "kind": "memo"},
        ],
        visibility="shared",
        redaction_status="passed",
        metadata={
            "target_strategy_id": "strat-momentum-01",
            "memo_id": memo_id,
            "memo_ref": artifact_ref,
            "hypothesis": "ATR trailing stop improves momentum Sharpe",
        },
    )

    context = RedactionContext(tenant_id="tenant-pantheon", requesting_visibility=InteractionVisibility.SHARED)
    result = bridge.extract_seed(
        interaction_record,
        context=context,
        artifact_kind=AgoraSeedArtifactKind.COMMITTEE_VERDICT,
        artifact_ref=artifact_ref,
        seed_kind=InteractionSeedKind.MUTATION,
    )

    assert result.stored is True
    assert result.seed.seed_id is not None
    assert "ATR" in result.seed.hypothesis or "momentum" in result.seed.hypothesis.lower()

    # Verify Agora table remains untouched by Source Ingestion
    fresh_memo = agora_store.get_committee_memo(session_id, memo_id)
    assert fresh_memo is not None
    assert fresh_memo.status == "published"
    assert fresh_memo.published_by == "approver-bob"
