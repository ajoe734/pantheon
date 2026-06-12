from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.source_ingestion.agora_seed_bridge import (
    AgoraSeedBridgeError,
    InteractionSeedKind,
    extract_agora_seed,
)
from services.source_ingestion.interaction_source_store import (
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionVisibility,
)
from services.source_ingestion.redaction_guard import RedactionContext, SeedCandidateBlockedError
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


def _ctx() -> RedactionContext:
    return RedactionContext(
        tenant_id="tenant-ids-005",
        requesting_visibility=InteractionVisibility.SHARED,
    )


def _record(
    *,
    interaction_id: str = "interaction-ids-005",
    source_surface: str = "committee",
    artifact_kind: str = "committee_verdict",
    artifact_ref: str | None = None,
    summary: str = "Committee verdict: risk overlay should cap max drawdown for TWSE momentum candidates.",
    seed_kind: str | None = None,
    strategy_seed: dict[str, Any] | None = None,
    evidence_kind: str = "artifact",
) -> InteractionSourceRecord:
    raw_ref = f"evidence://{source_surface}/session-ids-005/raw-transcript"
    artifact = artifact_ref or f"agora://{artifact_kind}/artifact-ids-005"
    metadata: dict[str, Any] = {
        "artifact_kind": artifact_kind,
        "artifact_ref": artifact,
        "research_only": True,
        "execution_route": "none",
        "strategy_seed": {
            "hypothesis": summary,
            "asset_class": ["equity"],
            "market_scope": ["TWSE"],
            "required_data": ["governed interaction artifact"],
            "risk_notes": ["review before promotion"],
            **dict(strategy_seed or {}),
        },
    }
    if seed_kind:
        metadata["seed_kind"] = seed_kind
    return InteractionSourceRecord(
        interaction_id=interaction_id,
        source_surface=source_surface,
        actor_type="reviewer",
        persona_refs=["persona-alpha"],
        session_id="session-ids-005",
        raw_ref=raw_ref,
        summary=summary,
        evidence_refs=[
            {"ref": raw_ref, "kind": "raw_interaction", "content_hash": "sha256:raw"},
            {"ref": artifact, "kind": evidence_kind, "content_hash": "sha256:artifact"},
        ],
        visibility=InteractionVisibility.SHARED.value,
        redaction_status=InteractionRedactionStatus.PENDING.value,
        created_at="2026-06-12T00:00:00Z",
        updated_at="2026-06-12T00:00:00Z",
        metadata=metadata,
    )


def _store(tmp_path: Path) -> StrategySpecSeedStore:
    return StrategySpecSeedStore(path=tmp_path / "strategy_seed_review_inbox.jsonl")


def test_committee_verdict_enters_review_store_with_agora_lineage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = extract_agora_seed(
        _record(seed_kind="risk_constraint"),
        context=_ctx(),
        store=store,
        created_at="2026-06-12T00:00:00Z",
    )

    saved = store.get(result.seed.seed_id)

    assert result.stored is True
    assert saved is not None
    assert saved.metadata["seed_kind"] == InteractionSeedKind.RISK_CONSTRAINT.value
    assert saved.metadata["research_only"] is True
    assert saved.metadata["execution_route"] == "none"
    assert saved.lineage["execution_route"] == "none"
    assert saved.lineage["AgoraSeedExtractionRef"]["artifact_kind"] == "committee_verdict"
    assert saved.lineage["AgoraSeedExtractionRef"]["raw_ref_role"] == "evidence_only"
    assert saved.lineage["AgoraSeedExtractionRef"]["raw_ref"].startswith("evidence://committee/")
    assert saved.lineage["AgoraSeedExtractionRef"]["artifact_ref"].startswith("agora://committee_verdict/")
    assert "seed_review_inbox" in saved.lineage["promotion_requires"]
    assert saved.metadata["source_trust_profile"]["trust_source"] == "committee_verdict"
    assert saved.metadata["source_trust_profile"]["review_bias"] == "committee_prioritized"


def test_raw_debate_transcript_is_refused_before_store_write(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(AgoraSeedBridgeError, match="evidence-only"):
        extract_agora_seed(
            _record(
                artifact_kind="debate_transcript",
                artifact_ref="agora://debate-transcript/session-ids-005",
                summary="Debate transcript artifact should stay evidence only.",
            ),
            context=_ctx(),
            store=store,
        )

    assert store.count() == 0


def test_inline_raw_transcript_summary_fails_redaction_before_store_write(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(SeedCandidateBlockedError, match="redaction_status=failed"):
        extract_agora_seed(
            _record(
                source_surface="agora",
                artifact_kind="consult_memo",
                artifact_ref="agora://consult-memo/published-001",
                evidence_kind="memo",
                summary="User: Buy 100 AAPL now.\nAssistant: Placing order.",
            ),
            context=_ctx(),
            store=store,
        )

    assert store.count() == 0


def test_trust_profile_distinguishes_ordinary_memo_from_red_team() -> None:
    ordinary = extract_agora_seed(
        _record(
            source_surface="agora",
            artifact_kind="consult_memo",
            artifact_ref="agora://consult-memo/published-ordinary",
            evidence_kind="memo",
            summary="Consult memo: investable hypothesis for a TWSE momentum strategy seed.",
        ),
        context=_ctx(),
    )
    red_team = extract_agora_seed(
        _record(
            interaction_id="interaction-ids-005-red-team",
            source_surface="red_team",
            artifact_kind="red_team_memo",
            artifact_ref="agora://red-team-memo/published-001",
            summary=(
                "Negative memory: never repeat the retired TWSE momentum strategy "
                "after the failed experiment and drawdown incident."
            ),
        ),
        context=_ctx(),
    )

    ordinary_trust = ordinary.extraction_ref["trust_profile"]
    red_team_trust = red_team.extraction_ref["trust_profile"]
    assert ordinary_trust["trust_source"] == "ordinary_agora_memo"
    assert ordinary_trust["requires_human_review"] is True
    assert red_team_trust["trust_source"] == "red_team_memo"
    assert red_team_trust["source_weight"] > ordinary_trust["source_weight"]
    assert red_team_trust["review_bias"] == "safety_first"
    assert red_team.seed.metadata["seed_kind"] == InteractionSeedKind.NEGATIVE.value


@pytest.mark.parametrize(
    ("source_surface", "artifact_kind", "summary", "expected_seed_kind", "evidence_kind"),
    [
        (
            "postmortem",
            "postmortem",
            (
                "Negative memory: never repeat the retired TWSE momentum strategy "
                "after the failed experiment and postmortem."
            ),
            InteractionSeedKind.NEGATIVE.value,
            "postmortem",
        ),
        (
            "decision_journal",
            "evolution_decision",
            "Execution policy: tighten slippage guard and limit order policy for paper routes.",
            InteractionSeedKind.EXECUTION_CONSTRAINT.value,
            "artifact",
        ),
    ],
)
def test_postmortem_and_evolution_map_to_risk_execution_negative_seed_kinds(
    source_surface: str,
    artifact_kind: str,
    summary: str,
    expected_seed_kind: str,
    evidence_kind: str,
) -> None:
    result = extract_agora_seed(
        _record(
            interaction_id=f"interaction-ids-005-{artifact_kind}",
            source_surface=source_surface,
            artifact_kind=artifact_kind,
            artifact_ref=f"agora://{artifact_kind}/published-001",
            summary=summary,
            evidence_kind=evidence_kind,
        ),
        context=_ctx(),
    )

    assert result.seed.metadata["seed_kind"] == expected_seed_kind
    assert result.seed.lineage["AgoraSeedExtractionRef"]["artifact_kind"] == artifact_kind
    assert result.seed.lineage["AgoraSeedExtractionRef"]["raw_ref_role"] == "evidence_only"
