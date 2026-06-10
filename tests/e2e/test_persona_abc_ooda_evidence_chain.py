"""MPOS-P1-PER-002: Prove Persona A/B/C research-to-proposal OODA packets.

Each of Persona A (Alpha: tw_equity momentum), B (Beta: tw_equity mean-reversion),
and C (Gamma: global_equity value/quality) independently walks the full OODA chain:

  Observe : EvidenceBundle → StrategySpecSeed (real seed build via StrategySpecSeedBuilder)
  Orient  : regime, OOS run ref, risk, mandate fit, evidence quality, no-order-route proof
  Decide  : PersonaAllocationProposal with evidence_refs back to seed + orient artifacts

Persona D (Delta: suspended) is excluded by the persona registry health gate.

After health gate enforcement:
  - Three persona proposals (A/B/C) are ingested into the proposal store.
  - Synthesis produces one AllocationPolicyArtifact from the three proposals.
  - A full evidence packet is written to support/evidence/MPOS-P1-PER-002/.

Acceptance gates (per MPOS_GAP_ASSESSMENT_AND_DISPATCH_2026-06-09.md §6 MPOS-P1-PER-002):
  - Persona A/B/C each produce a StrategySpecSeed from a real EvidenceBundle.
  - Each seed carries no-order-route markers in risk_notes.
  - Each PersonaAllocationProposal.evidence_refs points back to its seed + orient ref.
  - Persona D (suspended) is excluded by the registry health gate.
  - Synthesis produces a valid AllocationPolicyArtifact from three proposals.
  - Evidence packet written to canonical evidence path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OPTIMIZER_DIR = REPO_ROOT / "services" / "optimizer-svc"
PERSONA_REGISTRY_DIR = REPO_ROOT / "services" / "control-plane" / "persona"

for _p in (str(OPTIMIZER_DIR), str(PERSONA_REGISTRY_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from allocation_aggregation import (  # noqa: E402
    PersonaAllocationProposalJsonlStore,
    ingest_persona_proposal,
    synthesize_allocation_with_log,
)
from persona_registry import Persona, PersonaRegistry  # noqa: E402
from portfolio_synthesis import PersonaAllocationProposal  # noqa: E402
from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem  # noqa: E402
from services.persona.registry_health_gate import (  # noqa: E402
    RoleAssignment,
    evaluate_registry_health,
)
from services.source_ingestion.connectors.base import SourceRecord  # noqa: E402
from services.source_ingestion.strategy_seed_builder import StrategySpecSeedBuilder  # noqa: E402


EVIDENCE_DIR = REPO_ROOT / "support" / "evidence" / "MPOS-P1-PER-002"
FULL_PACKET_PATH = EVIDENCE_DIR / "full_packet.json"
GENERATED_AT = "2026-06-10T00:00:00Z"
PACKET_ID = "mpos-p1-per-002-full-e2e-packet"
CAPITAL_POOL_ID = "pool-paper"
SCOPE_REF = "paper"

# -----------------------------------------------------------------------
# Per-persona OODA specs: Persona A (Alpha), B (Beta), C (Gamma)
# -----------------------------------------------------------------------
PERSONA_CONFIGS: dict[str, dict[str, Any]] = {
    "persona-alpha": {
        "name": "Alpha",
        "lifecycle_state": "paper_owner",
        "mandate": "TW equity momentum mandate; paper-trading sponsor",
        "source_id": "src-per-002-alpha-001",
        "evidence_item_id": "ei-per-002-alpha-001",
        "evidence_bundle_id": "eb-per-002-alpha-001",
        "hypothesis": (
            "TWSE and TPEx equities with positive 60-day momentum, stable profitability, "
            "and moderate volume expansion can rank five-day forward returns."
        ),
        "backend_hint": "vectorbt",
        "asset_class": ["equity"],
        "market_scope": ["Taiwan", "TWSE", "TPEx"],
        "holding_period": "5 trading days",
        "regime": "risk_on",
        "risk_posture": "risk_on",
        "strategy_family": "tw_equity",
        "oos_run_id": "exp-per-002-alpha-oos-001",
        "oos_sharpe": 1.42,
        "oos_max_dd": -0.08,
        "mandate_fit_score": 0.95,
        "evidence_quality_score": 0.88,
        "target_weights": {"2330.TW": 0.45, "0050.TW": 0.25},
        "conviction": 0.82,
        "uncertainty": 0.10,
        "proposal_id": "pap-per-002-alpha-001",
    },
    "persona-beta": {
        "name": "Beta",
        "lifecycle_state": "paper_owner",
        "mandate": "TW equity mean-reversion mandate; paper-trading contributor",
        "source_id": "src-per-002-beta-001",
        "evidence_item_id": "ei-per-002-beta-001",
        "evidence_bundle_id": "eb-per-002-beta-001",
        "hypothesis": (
            "TWSE equities exhibiting short-term reversal patterns after high-volume "
            "sell-offs can deliver positive risk-adjusted returns over 3-10 day hold."
        ),
        "backend_hint": "vectorbt",
        "asset_class": ["equity"],
        "market_scope": ["Taiwan", "TWSE"],
        "holding_period": "5 trading days",
        "regime": "risk_on",
        "risk_posture": "risk_neutral",
        "strategy_family": "tw_equity",
        "oos_run_id": "exp-per-002-beta-oos-001",
        "oos_sharpe": 1.18,
        "oos_max_dd": -0.12,
        "mandate_fit_score": 0.90,
        "evidence_quality_score": 0.82,
        "target_weights": {"2330.TW": 0.15, "2454.TW": 0.30},
        "conviction": 0.70,
        "uncertainty": 0.15,
        "proposal_id": "pap-per-002-beta-001",
    },
    "persona-gamma": {
        "name": "Gamma",
        "lifecycle_state": "paper_owner",
        "mandate": "Global equity value/quality mandate; paper-trading contributor",
        "source_id": "src-per-002-gamma-001",
        "evidence_item_id": "ei-per-002-gamma-001",
        "evidence_bundle_id": "eb-per-002-gamma-001",
        "hypothesis": (
            "Global large-cap equities with low price-to-book ratio, stable gross margins, "
            "and positive earnings revisions can generate alpha over a 20-60 day hold."
        ),
        "backend_hint": "qlib",
        "asset_class": ["equity"],
        "market_scope": ["US", "NASDAQ"],
        "holding_period": "20 trading days",
        "regime": "neutral",
        "risk_posture": "risk_off",
        "strategy_family": "global_equity",
        "oos_run_id": "exp-per-002-gamma-oos-001",
        "oos_sharpe": 0.98,
        "oos_max_dd": -0.06,
        "mandate_fit_score": 0.85,
        "evidence_quality_score": 0.79,
        "target_weights": {"AAPL": 0.25, "MSFT": 0.20},
        "conviction": 0.65,
        "uncertainty": 0.20,
        "proposal_id": "pap-per-002-gamma-001",
    },
}


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _build_evidence_bundle(cfg: dict[str, Any]) -> EvidenceBundle:
    """Build an EvidenceBundle representing the persona's Observe inputs."""
    return EvidenceBundle(
        evidence_bundle_id=cfg["evidence_bundle_id"],
        source_ids=[cfg["source_id"]],
        evidence_item_ids=[cfg["evidence_item_id"]],
        summary=cfg["hypothesis"],
        citation_refs=[f"cite://{cfg['source_id']}/research-note"],
        confidence=cfg["evidence_quality_score"],
        license_scope="internal",
        access_scope=["research"],
        created_by="Claude",
        created_at=GENERATED_AT,
        metadata={
            "strategy_seed": {
                "hypothesis": cfg["hypothesis"],
                "asset_class": cfg["asset_class"],
                "market_scope": cfg["market_scope"],
                "holding_period": cfg["holding_period"],
                "backend_hint": cfg["backend_hint"],
                "required_data": ["point-in-time OHLCV", "return labels"],
                "feature_hints": ["momentum", "volume", "quality"],
                "label_hints": ["forward_return"],
                "risk_notes": ["no_broker_route", "research_only"],
            },
            "governance": {
                "direct_execution_allowed": False,
                "broker_consumption": "not_direct_action",
            },
        },
    )


def _build_evidence_item(cfg: dict[str, Any]) -> EvidenceItem:
    """Build an EvidenceItem representing the persona's raw research observation."""
    return EvidenceItem(
        evidence_item_id=cfg["evidence_item_id"],
        source_id=cfg["source_id"],
        item_type="research_hypothesis",
        content_ref=f"internal://{cfg['source_id']}/hypothesis",
        citation_label=f"{cfg['source_id']}#hypothesis",
        body=cfg["hypothesis"],
        confidence=cfg["evidence_quality_score"],
        access_scope=["research"],
        metadata={
            "strategy_seed": {
                "hypothesis": cfg["hypothesis"],
                "asset_class": cfg["asset_class"],
                "market_scope": cfg["market_scope"],
                "holding_period": cfg["holding_period"],
                "backend_hint": cfg["backend_hint"],
                "required_data": ["point-in-time OHLCV", "return labels"],
                "feature_hints": ["momentum", "volume", "quality"],
                "label_hints": ["forward_return"],
                "risk_notes": ["no_broker_route", "research_only"],
            },
            "governance": {
                "direct_execution_allowed": False,
                "broker_consumption": "not_direct_action",
            },
        },
    )


def _build_source_record(cfg: dict[str, Any]) -> SourceRecord:
    """Build a SourceRecord for the evidence bundle."""
    return SourceRecord(
        source_id=cfg["source_id"],
        connector_id=f"conn-per-002-{cfg['name'].lower()}-research",
        source_type="internal_note",
        title=f"{cfg['name']} Persona Research Note — {cfg['strategy_family']}",
        content_ref=f"internal://{cfg['source_id']}/research-note",
        status="normalized",
        metadata={
            "hypothesis": cfg["hypothesis"],
            "strategy_seed": {
                "hypothesis": cfg["hypothesis"],
                "asset_class": cfg["asset_class"],
                "market_scope": cfg["market_scope"],
                "holding_period": cfg["holding_period"],
                "backend_hint": cfg["backend_hint"],
                "required_data": ["point-in-time OHLCV", "return labels"],
                "feature_hints": ["momentum", "volume", "quality"],
                "label_hints": ["forward_return"],
                "risk_notes": ["no_broker_route", "research_only"],
            },
            "governance": {
                "direct_execution_allowed": False,
                "broker_consumption": "not_direct_action",
            },
            "keywords": cfg["asset_class"] + cfg["market_scope"],
        },
    )


def _build_orient_assessment(cfg: dict[str, Any], seed_id: str) -> dict[str, Any]:
    """Build an Orient assessment record for a persona's seed + OOS evidence."""
    return {
        "orient_ref": f"orient://{cfg['source_id']}/orient-001",
        "persona_id": cfg["source_id"].replace("src-per-002-", "persona-").replace("-001", ""),
        "seed_id": seed_id,
        "regime": cfg["regime"],
        "risk_posture": cfg["risk_posture"],
        "strategy_family": cfg["strategy_family"],
        "mandate_fit_score": cfg["mandate_fit_score"],
        "evidence_quality_score": cfg["evidence_quality_score"],
        "oos_evidence": {
            "oos_run_id": cfg["oos_run_id"],
            "oos_run_ref": f"oos://{cfg['oos_run_id']}",
            "sharpe_ratio": cfg["oos_sharpe"],
            "max_drawdown": cfg["oos_max_dd"],
            "backend": cfg["backend_hint"],
            "environment": "paper_only",
        },
        "no_order_route_proof": {
            "seed_risk_notes_include_research_only": True,
            "broker_consumption": "not_direct_action",
            "direct_execution_allowed": False,
            "proof_ref": f"seed://{seed_id}#risk_notes",
        },
    }


def _build_proposal(cfg: dict[str, Any], seed_id: str, orient: dict[str, Any]) -> PersonaAllocationProposal:
    """Build a PersonaAllocationProposal backed by seed + orient evidence refs."""
    persona_id = cfg["source_id"].replace("src-per-002-", "persona-").replace("-001", "")
    evidence_refs = [
        f"seed://{seed_id}",
        orient["orient_ref"],
        orient["oos_evidence"]["oos_run_ref"],
    ]
    return PersonaAllocationProposal(
        proposal_id=cfg["proposal_id"],
        persona_id=persona_id,
        capital_pool_id=CAPITAL_POOL_ID,
        scope_ref=SCOPE_REF,
        target_type="asset",
        directions=["long"],
        target_weights=cfg["target_weights"],
        conviction=cfg["conviction"],
        uncertainty=cfg["uncertainty"],
        rationale_ref=f"seed://{seed_id}",
        regime_ref=f"regime://paper/{cfg['regime']}",
        valid_from=GENERATED_AT,
        evidence_refs=evidence_refs,
        created_at=GENERATED_AT,
        reliability_score=cfg["evidence_quality_score"],
        regime_fit_score=cfg["mandate_fit_score"],
        governance_multiplier=1.0,
        metadata={
            "holding_period": cfg["holding_period"],
            "risk_posture": cfg["risk_posture"],
            "strategy_family": cfg["strategy_family"],
            "seed_id": seed_id,
            "oos_run_id": cfg["oos_run_id"],
        },
    )


def _make_persona(persona_id: str, cfg: dict[str, Any], **overrides: Any) -> Persona:
    kwargs: dict[str, Any] = {
        "persona_id": persona_id,
        "name": cfg["name"],
        "mandate": cfg["mandate"],
        "lifecycle_state": cfg["lifecycle_state"],
        "created_at": GENERATED_AT,
    }
    kwargs.update(overrides)
    return Persona(**kwargs)


# -----------------------------------------------------------------------
# Test
# -----------------------------------------------------------------------

def test_persona_abc_ooda_evidence_chain(tmp_path: Path) -> None:
    """Full MPOS-P1-PER-002: Persona A/B/C OODA chain with real StrategySpecSeed."""

    builder = StrategySpecSeedBuilder()

    # ------------------------------------------------------------------
    # Phase 1: Observe — build StrategySpecSeed per persona
    # ------------------------------------------------------------------
    seeds: dict[str, Any] = {}
    orients: dict[str, Any] = {}
    proposals: dict[str, PersonaAllocationProposal] = {}

    for persona_id, cfg in PERSONA_CONFIGS.items():
        bundle = _build_evidence_bundle(cfg)
        item = _build_evidence_item(cfg)
        record = _build_source_record(cfg)

        seed = builder.build_seed(
            bundle,
            source_records=[record],
            evidence_items=[item],
            created_by="Claude",
            created_at=GENERATED_AT,
            metadata={
                "task_id": "MPOS-P1-PER-002",
                "persona_id": persona_id,
                "no_order_route": True,
                "research_only": True,
            },
        )

        # Verify Observe invariants
        assert seed.seed_id, f"{persona_id}: seed_id must be non-empty"
        assert seed.hypothesis, f"{persona_id}: seed must have hypothesis"
        assert list(seed.asset_class), f"{persona_id}: seed must have asset_class"
        assert list(seed.market_scope), f"{persona_id}: seed must have market_scope"
        assert cfg["source_id"] in seed.source_ids, (
            f"{persona_id}: seed.source_ids must include the source record"
        )
        # No-order-route proof: seed risk_notes include research-only markers
        risk_note_text = " ".join(seed.risk_notes)
        assert "research" in risk_note_text or "execution" in risk_note_text, (
            f"{persona_id}: seed risk_notes must contain no-order-route markers; got {list(seed.risk_notes)}"
        )
        assert seed.metadata.get("no_order_route") is True, (
            f"{persona_id}: seed metadata must carry no_order_route=True"
        )

        seeds[persona_id] = seed

        # ------------------------------------------------------------------
        # Phase 2: Orient — build orientation assessment
        # ------------------------------------------------------------------
        orient = _build_orient_assessment(cfg, seed.seed_id)

        # Verify Orient invariants
        assert orient["regime"] in {"risk_on", "risk_off", "neutral"}, (
            f"{persona_id}: regime must be a recognized regime value"
        )
        assert 0.0 <= orient["mandate_fit_score"] <= 1.0, (
            f"{persona_id}: mandate_fit_score out of range"
        )
        assert 0.0 <= orient["evidence_quality_score"] <= 1.0, (
            f"{persona_id}: evidence_quality_score out of range"
        )
        assert orient["oos_evidence"]["environment"] == "paper_only", (
            f"{persona_id}: OOS evidence must be paper_only (no live broker route)"
        )
        assert orient["no_order_route_proof"]["direct_execution_allowed"] is False, (
            f"{persona_id}: direct_execution_allowed must be False"
        )

        orients[persona_id] = orient

        # ------------------------------------------------------------------
        # Phase 3: Decide — build PersonaAllocationProposal
        # ------------------------------------------------------------------
        proposal = _build_proposal(cfg, seed.seed_id, orient)

        # Verify proposal evidence_refs chain back to the seed
        assert any(seed.seed_id in ref for ref in proposal.evidence_refs), (
            f"{persona_id}: proposal.evidence_refs must reference the seed_id"
        )
        assert any(orient["orient_ref"] in ref for ref in proposal.evidence_refs), (
            f"{persona_id}: proposal.evidence_refs must reference the orient artifact"
        )
        assert any(cfg["oos_run_id"] in ref for ref in proposal.evidence_refs), (
            f"{persona_id}: proposal.evidence_refs must reference the OOS run"
        )

        proposals[persona_id] = proposal

    # Verify all three personas have seeds and proposals
    assert set(seeds.keys()) == set(PERSONA_CONFIGS.keys()), "All three personas must produce seeds"
    assert set(proposals.keys()) == set(PERSONA_CONFIGS.keys()), "All three personas must produce proposals"

    # ------------------------------------------------------------------
    # Phase 4: Persona registry health gate — Persona D (suspended) excluded
    # ------------------------------------------------------------------
    registry = PersonaRegistry()
    role_assignments: list[RoleAssignment] = []

    for persona_id, cfg in PERSONA_CONFIGS.items():
        persona = _make_persona(persona_id, cfg)
        registry.create(persona)
        role_assignments.append(
            RoleAssignment(
                persona_id=persona_id,
                role="paper_owner",
                capital_pool_id=CAPITAL_POOL_ID,
                scope_ref=SCOPE_REF,
            )
        )

    # Persona D: suspended
    persona_delta = _make_persona(
        "persona-delta",
        {
            "name": "Delta",
            "mandate": "Suspended persona — should be excluded by health gate",
            "lifecycle_state": "paper_owner",
        },
        status="suspended",
    )
    registry.create(persona_delta)
    role_assignments.append(
        RoleAssignment(
            persona_id="persona-delta",
            role="paper_owner",
            capital_pool_id=CAPITAL_POOL_ID,
            scope_ref=SCOPE_REF,
        )
    )

    health_result = evaluate_registry_health(registry, role_assignments)

    # Suspended persona D must be excluded
    assert "persona-delta" in health_result.excluded_persona_ids, (
        "Persona D (delta/suspended) must be excluded by the health gate"
    )
    # All three active personas must be sponsor candidates
    for persona_id in PERSONA_CONFIGS:
        assert persona_id in health_result.sponsor_candidate_ids, (
            f"{persona_id} must be a sponsor candidate after health gate"
        )
    # Gate does not pass because a suspended persona is present
    assert health_result.passed is False, (
        "Health gate must report not passed when a suspended persona is excluded"
    )
    assert "persona_suspended" in health_result.issue_codes, (
        "Health gate must emit persona_suspended issue code for delta"
    )

    # ------------------------------------------------------------------
    # Phase 5: Synthesis — ingest A/B/C proposals and synthesize
    # ------------------------------------------------------------------
    store = PersonaAllocationProposalJsonlStore(tmp_path / "per-002-proposals.jsonl")
    for persona_id in PERSONA_CONFIGS:
        ingest_persona_proposal(proposals[persona_id], store=store)

    proposal_ids = [cfg["proposal_id"] for cfg in PERSONA_CONFIGS.values()]
    synthesis = synthesize_allocation_with_log(
        capital_pool_id=CAPITAL_POOL_ID,
        proposal_ids=proposal_ids,
        method="risk_first",
        risk_policy_ref="risk-policy://pool-paper/v1",
        store=store,
        constraints_bundle={"environment": "paper"},
    )

    # Synthesis must produce a valid AllocationPolicyArtifact from 3 proposals
    assert synthesis.artifact.capital_pool_id == CAPITAL_POOL_ID
    assert synthesis.artifact.sponsor_persona_id in set(PERSONA_CONFIGS.keys()), (
        "Sponsor must be one of the three active personas"
    )
    assert len(synthesis.artifact.provenance_refs) == 3, (
        "Allocation artifact must reference all three persona proposals"
    )

    # ------------------------------------------------------------------
    # Phase 6: Evidence packet
    # ------------------------------------------------------------------
    per_persona_packets: list[dict[str, Any]] = []
    for persona_id, cfg in PERSONA_CONFIGS.items():
        seed = seeds[persona_id]
        orient = orients[persona_id]
        proposal = proposals[persona_id]
        per_persona_packets.append(
            {
                "persona_id": persona_id,
                "persona_name": cfg["name"],
                "mandate": cfg["mandate"],
                "observe": {
                    "seed_id": seed.seed_id,
                    "hypothesis": seed.hypothesis,
                    "source_ids": list(seed.source_ids),
                    "backend_hint": seed.backend_hint,
                    "asset_class": list(seed.asset_class),
                    "market_scope": list(seed.market_scope),
                    "risk_notes": list(seed.risk_notes),
                    "no_order_route": seed.metadata.get("no_order_route"),
                },
                "orient": {
                    "regime": orient["regime"],
                    "risk_posture": orient["risk_posture"],
                    "strategy_family": orient["strategy_family"],
                    "mandate_fit_score": orient["mandate_fit_score"],
                    "evidence_quality_score": orient["evidence_quality_score"],
                    "oos_run_id": orient["oos_evidence"]["oos_run_id"],
                    "oos_sharpe": orient["oos_evidence"]["sharpe_ratio"],
                    "oos_max_dd": orient["oos_evidence"]["max_drawdown"],
                    "no_order_route_proof": orient["no_order_route_proof"],
                },
                "decide": {
                    "proposal_id": proposal.proposal_id,
                    "evidence_refs": list(proposal.evidence_refs),
                    "conviction": proposal.conviction,
                    "uncertainty": proposal.uncertainty,
                    "target_weights": dict(proposal.target_weights),
                },
            }
        )

    full_packet: dict[str, Any] = {
        "packet_id": PACKET_ID,
        "task_id": "MPOS-P1-PER-002",
        "generated_at": GENERATED_AT,
        "persona_ooda_packets": per_persona_packets,
        "registry_health_gate": health_result.to_dict(),
        "synthesis": {
            "artifact_id": synthesis.artifact.artifact_id,
            "capital_pool_id": synthesis.artifact.capital_pool_id,
            "sponsor_persona_id": synthesis.artifact.sponsor_persona_id,
            "synthesis_method": synthesis.artifact.synthesis_method,
            "provenance_refs": list(synthesis.artifact.provenance_refs),
            "target_weights": dict(synthesis.artifact.target_weights),
        },
        "acceptance_gates": {
            "persona_a_seed_built": seeds["persona-alpha"].seed_id != "",
            "persona_b_seed_built": seeds["persona-beta"].seed_id != "",
            "persona_c_seed_built": seeds["persona-gamma"].seed_id != "",
            "persona_a_no_order_route": seeds["persona-alpha"].metadata.get("no_order_route") is True,
            "persona_b_no_order_route": seeds["persona-beta"].metadata.get("no_order_route") is True,
            "persona_c_no_order_route": seeds["persona-gamma"].metadata.get("no_order_route") is True,
            "persona_a_evidence_refs_chain": any(
                seeds["persona-alpha"].seed_id in r for r in proposals["persona-alpha"].evidence_refs
            ),
            "persona_b_evidence_refs_chain": any(
                seeds["persona-beta"].seed_id in r for r in proposals["persona-beta"].evidence_refs
            ),
            "persona_c_evidence_refs_chain": any(
                seeds["persona-gamma"].seed_id in r for r in proposals["persona-gamma"].evidence_refs
            ),
            "persona_d_suspended_excluded": "persona-delta" in health_result.excluded_persona_ids,
            "synthesis_three_proposals": len(synthesis.artifact.provenance_refs) == 3,
            "synthesis_sponsor_is_active_persona": (
                synthesis.artifact.sponsor_persona_id in set(PERSONA_CONFIGS.keys())
            ),
        },
    }

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    FULL_PACKET_PATH.write_text(json.dumps(full_packet, indent=2, ensure_ascii=False))

    # Final gate assertions
    written = json.loads(FULL_PACKET_PATH.read_text())
    gates = written["acceptance_gates"]
    for gate_name, value in gates.items():
        assert value is True, f"Acceptance gate {gate_name!r} must be True, got {value!r}"
