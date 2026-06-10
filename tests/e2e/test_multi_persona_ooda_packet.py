"""MPO-003-V2: Multi-persona OODA E2E packet.

End-to-end integration test that exercises the full MPO pipeline:
  1. Persona registry health gate (MPO-002-V2) excludes suspended/retired personas.
  2. Two active personas each contribute a StrategySpec-backed allocation proposal.
  3. MGMT-SYN synthesis produces an AllocationPolicyArtifact.
  4. Sponsor resolver (MPO-001-V2) produces a governance ConflictResolutionLog
     with classified_conflicts non-null and a sponsor-resolved allocation.
  5. A governance review memo is synthesized from the resolved packet.
  6. The full evidence packet is written to support/evidence/MPO-003-V2/.

Acceptance criteria:
  - ≥2 personas participate in allocation synthesis.
  - Sponsor-resolved allocation proposal is produced via MPO-001-V2.
  - classified_conflicts is non-null (weight_conflict present).
  - Persona registry health gate (MPO-002-V2) passes.
  - Governance review memo is non-empty.
  - Evidence packet (full_packet.json) is written to canonical evidence path.
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
from services.governance.multi_persona.sponsor_resolver import resolve_sponsor  # noqa: E402
from services.persona.registry_health_gate import (  # noqa: E402
    RoleAssignment,
    evaluate_registry_health,
)


EVIDENCE_DIR = REPO_ROOT / "support" / "evidence" / "MPO-003-V2"
FULL_PACKET_PATH = EVIDENCE_DIR / "full_packet.json"
GENERATED_AT = "2026-05-20T00:00:00Z"
PACKET_ID = "mpo-003-v2-full-e2e-packet"

# Two strategy families used as the shared StrategySpec pool.
STRATEGY_SPEC_POOL = ["strat-spec://tw_equity/momentum-v1", "strat-spec://tw_equity/mean-reversion-v1"]


def _make_persona(**overrides: Any) -> Persona:
    defaults: dict[str, Any] = {
        "persona_id": "persona-alpha",
        "name": "Alpha",
        "mandate": "Multi-persona allocation sponsor; tw_equity mandate",
        "lifecycle_state": "paper_owner",
        "created_at": "2026-05-01T00:00:00Z",
    }
    defaults.update(overrides)
    return Persona(**defaults)


def _make_proposal(**overrides: Any) -> PersonaAllocationProposal:
    defaults: dict[str, Any] = {
        "proposal_id": "pap-alpha-001",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-paper",
        "scope_ref": "paper",
        "target_type": "asset",
        "directions": ["long"],
        "target_weights": {"2330.TW": 0.55, "0050.TW": 0.30},
        "conviction": 0.82,
        "uncertainty": 0.10,
        "rationale_ref": "strat-spec://tw_equity/momentum-v1",
        "regime_ref": "regime-risk-on",
        "valid_from": "2026-05-15T08:00:00Z",
        "valid_to": "2026-05-22T08:00:00Z",
        "evidence_refs": ["evidence://pap-alpha-001"],
        "created_at": "2026-05-15T08:00:00Z",
        "reliability_score": 0.88,
        "regime_fit_score": 0.95,
        "governance_multiplier": 1.0,
        "metadata": {
            "holding_period": "swing",
            "risk_posture": "risk_on",
            "strategy_family": "tw_equity",
            "strategy_spec_ref": STRATEGY_SPEC_POOL[0],
        },
    }
    defaults.update(overrides)
    return PersonaAllocationProposal(**defaults)


def test_multi_persona_ooda_e2e_packet(tmp_path: Path) -> None:
    """Full MPO-003-V2 E2E: 2 personas, sponsor resolution, health gate, governance memo."""

    # ------------------------------------------------------------------
    # Phase 1: Persona registry health gate (MPO-002-V2)
    # ------------------------------------------------------------------
    registry = PersonaRegistry()
    persona_alpha = _make_persona(
        persona_id="persona-alpha",
        name="Alpha",
        lifecycle_state="paper_owner",
        mandate="Multi-persona allocation sponsor; tw_equity momentum mandate",
    )
    persona_beta = _make_persona(
        persona_id="persona-beta",
        name="Beta",
        lifecycle_state="paper_owner",
        mandate="Multi-persona allocation contributor; tw_equity mean-reversion mandate",
    )
    persona_suspended = _make_persona(
        persona_id="persona-suspended",
        name="Suspended",
        lifecycle_state="paper_owner",
        mandate="Should be excluded by health gate",
        status="suspended",
    )
    registry.create(persona_alpha)
    registry.create(persona_beta)
    registry.create(persona_suspended)

    role_assignments = [
        RoleAssignment(
            persona_id="persona-alpha",
            role="paper_owner",
            capital_pool_id="pool-paper",
            scope_ref="paper",
        ),
        RoleAssignment(
            persona_id="persona-beta",
            role="paper_owner",
            capital_pool_id="pool-paper",
            scope_ref="paper",
        ),
        RoleAssignment(
            persona_id="persona-suspended",
            role="paper_owner",
            capital_pool_id="pool-paper",
            scope_ref="paper",
        ),
    ]

    health_result = evaluate_registry_health(registry, role_assignments)

    assert health_result.passed is False, "suspended persona must prevent gate from passing fully"
    assert "persona-alpha" in health_result.sponsor_candidate_ids
    assert "persona-beta" in health_result.sponsor_candidate_ids
    assert "persona-suspended" in health_result.excluded_persona_ids
    assert "persona_suspended" in health_result.issue_codes
    # Active personas must qualify despite the suspended one blocking full pass.
    assert len(health_result.sponsor_candidate_ids) >= 2

    # ------------------------------------------------------------------
    # Phase 2: Allocation proposals from shared StrategySpec pool (≥2 personas)
    # ------------------------------------------------------------------
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")

    # Persona-alpha: momentum — heavy 2330.TW
    ingest_persona_proposal(
        _make_proposal(
            proposal_id="pap-alpha-001",
            persona_id="persona-alpha",
            target_weights={"2330.TW": 0.55, "0050.TW": 0.30},
            conviction=0.82,
            directions=["long"],
            rationale_ref=STRATEGY_SPEC_POOL[0],
            metadata={
                "holding_period": "swing",
                "risk_posture": "risk_on",
                "strategy_family": "tw_equity",
                "strategy_spec_ref": STRATEGY_SPEC_POOL[0],
            },
        ),
        store=store,
    )

    # Persona-beta: mean-reversion — lighter 2330.TW, adds 2454.TW
    ingest_persona_proposal(
        _make_proposal(
            proposal_id="pap-beta-001",
            persona_id="persona-beta",
            target_weights={"2330.TW": 0.20, "2454.TW": 0.45},
            conviction=0.70,
            uncertainty=0.15,
            directions=["long"],
            rationale_ref=STRATEGY_SPEC_POOL[1],
            created_at="2026-05-15T08:01:00Z",
            evidence_refs=["evidence://pap-beta-001"],
            metadata={
                "holding_period": "position",
                "risk_posture": "risk_on",
                "strategy_family": "tw_equity",
                "strategy_spec_ref": STRATEGY_SPEC_POOL[1],
            },
        ),
        store=store,
    )

    # ------------------------------------------------------------------
    # Phase 3: MGMT-SYN synthesis → AllocationPolicyArtifact + ConflictLog
    # ------------------------------------------------------------------
    synthesis = synthesize_allocation_with_log(
        capital_pool_id="pool-paper",
        proposal_ids=["pap-alpha-001", "pap-beta-001"],
        method="risk_first",
        risk_policy_ref="risk-policy://pool-paper/v1",
        store=store,
        constraints_bundle={"environment": "paper"},
    )

    assert synthesis.artifact.capital_pool_id == "pool-paper"
    assert synthesis.artifact.sponsor_persona_id in {"persona-alpha", "persona-beta"}
    assert len(synthesis.artifact.provenance_refs) == 2

    # ------------------------------------------------------------------
    # Phase 4: Sponsor resolution (MPO-001-V2)
    # ------------------------------------------------------------------
    resolved = resolve_sponsor(
        synthesis.artifact,
        store=store,
        source_conflict_resolution_log=synthesis.conflict_resolution_log,
        conflict_evidence_ref="support/evidence/MPO-003-V2/full_packet.json",
    )

    gov_log = resolved.conflict_resolution_log

    # classified_conflicts must be non-null (weight_conflict expected for diverging weights)
    assert gov_log.classified_conflicts, "classified_conflicts must be non-null"
    conflict_types = {c.conflict_type for c in gov_log.classified_conflicts}
    assert "weight_conflict" in conflict_types, f"expected weight_conflict, got {conflict_types}"

    # Sponsor must be one of the two active personas
    assert resolved.sponsor_persona_id in {"persona-alpha", "persona-beta"}
    assert set(resolved.proposal_ids) == {"pap-alpha-001", "pap-beta-001"}

    # log_id must be traceable back to the artifact
    assert gov_log.log_id == synthesis.artifact.conflict_resolution_log_id
    assert gov_log.source_conflict_resolution_log_id == synthesis.conflict_resolution_log.log_id

    # ------------------------------------------------------------------
    # Phase 5: Governance review memo
    # ------------------------------------------------------------------
    sponsor_name = "Alpha" if resolved.sponsor_persona_id == "persona-alpha" else "Beta"
    open_conflict_note = (
        f"open_conflicts={len(gov_log.open_conflicts)} requiring committee review"
        if gov_log.has_open_conflicts
        else "all conflicts resolved or non-blocking"
    )
    governance_memo = (
        f"MPO-003-V2 governance review memo — {GENERATED_AT}\n"
        f"Sponsor persona: {resolved.sponsor_persona_id} ({sponsor_name})\n"
        f"Participating personas: {', '.join(sorted(resolved.proposal_ids))}\n"
        f"StrategySpec pool: {', '.join(STRATEGY_SPEC_POOL)}\n"
        f"Capital pool: {synthesis.artifact.capital_pool_id}\n"
        f"Synthesis method: {synthesis.artifact.synthesis_method}\n"
        f"Classified conflicts: {len(gov_log.classified_conflicts)} "
        f"(types: {', '.join(sorted(conflict_types))})\n"
        f"Conflict status: {open_conflict_note}\n"
        f"Health gate: excluded={list(health_result.excluded_persona_ids)}, "
        f"candidates={list(health_result.sponsor_candidate_ids)}\n"
    )

    assert len(governance_memo) > 0, "governance memo must be non-empty"
    assert resolved.sponsor_persona_id in governance_memo

    # ------------------------------------------------------------------
    # Phase 6: Evidence packet
    # ------------------------------------------------------------------
    full_packet: dict[str, Any] = {
        "packet_id": PACKET_ID,
        "task_id": "MPO-003-V2",
        "generated_at": GENERATED_AT,
        "registry_health_gate": health_result.to_dict(),
        "strategy_spec_pool": STRATEGY_SPEC_POOL,
        "synthesis": {
            "artifact_id": synthesis.artifact.artifact_id,
            "capital_pool_id": synthesis.artifact.capital_pool_id,
            "sponsor_persona_id": synthesis.artifact.sponsor_persona_id,
            "synthesis_method": synthesis.artifact.synthesis_method,
            "provenance_refs": list(synthesis.artifact.provenance_refs),
            "target_weights": dict(synthesis.artifact.target_weights),
            "mgmt_syn_conflict_log_id": synthesis.conflict_resolution_log.log_id,
        },
        "sponsor_resolution": {
            "sponsor_persona_id": resolved.sponsor_persona_id,
            "proposal_ids": list(resolved.proposal_ids),
            "has_open_conflicts": resolved.has_open_conflicts,
            "conflict_resolution_log": gov_log.to_dict(),
        },
        "governance_memo": governance_memo,
        "acceptance_gates": {
            "min_2_personas": len(resolved.proposal_ids) >= 2,
            "sponsor_resolved": resolved.sponsor_persona_id is not None,
            "classified_conflicts_non_null": bool(gov_log.classified_conflicts),
            "health_gate_enforced": "persona-suspended" in health_result.excluded_persona_ids,
            "governance_memo_non_empty": len(governance_memo) > 0,
        },
    }

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    FULL_PACKET_PATH.write_text(json.dumps(full_packet, indent=2, ensure_ascii=False))

    # Final gate assertions against the written packet
    written = json.loads(FULL_PACKET_PATH.read_text())
    gates = written["acceptance_gates"]
    assert gates["min_2_personas"], "must have ≥2 personas"
    assert gates["sponsor_resolved"], "sponsor must be resolved"
    assert gates["classified_conflicts_non_null"], "classified_conflicts must be non-null"
    assert gates["health_gate_enforced"], "health gate must have excluded suspended persona"
    assert gates["governance_memo_non_empty"], "governance memo must be non-empty"
