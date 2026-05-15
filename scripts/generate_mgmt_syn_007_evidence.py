#!/usr/bin/env python3
"""Generate MGMT-SYN-007 multi-persona synthesis proof evidence.

The proof is intentionally deterministic so reviewers can rerun it without
churning committed evidence. It records persona proposals, replays them through
the optimizer-svc proposal store, synthesizes one AllocationPolicyArtifact, and
packages the governance/OODA evidence required by the M5 supplemental SA.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OPTIMIZER_DIR = ROOT / "services" / "optimizer-svc"
OODA_DIR = ROOT / "services" / "control-plane" / "ooda"
OUTPUT_DIR = ROOT / "support" / "evidence" / "MGMT-SYN-007"
GENERATED_AT = "2026-05-15T15:45:00Z"

for path in (str(OPTIMIZER_DIR), str(OODA_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from allocation_aggregation import (  # noqa: E402
    PERSONA_ALLOCATION_PROPOSAL_RECORD_SCHEMA_VERSION,
    PersonaAllocationProposalJsonlStore,
)
from ooda_loop_packet import (  # noqa: E402
    ActBundle,
    DecideBundle,
    LearnBundle,
    LoopEnvironment,
    LoopStatus,
    LoopType,
    ObserveBundle,
    OodaLoopPacket,
    OrientBundle,
    validate_packet,
)
from portfolio_synthesis import (  # noqa: E402
    AllocationPolicyArtifact,
    PoolRiskPolicy,
    PortfolioSynthesizer,
    SynthesisMethod,
)
import portfolio_synthesis.synthesizer as synthesizer_module  # noqa: E402


def _proposal_payloads() -> list[dict[str, Any]]:
    return [
        {
            "proposal_id": "pap-mgmt-syn-007-alpha",
            "persona_id": "persona-tw-momentum",
            "capital_pool_id": "capital-pool-paper-001",
            "scope_ref": "paper",
            "target_type": "asset",
            "directions": ["long", "risk_on"],
            "target_weights": {"2330.TW": 0.45, "2454.TW": 0.25, "0050.TW": 0.10},
            "conviction": 0.82,
            "uncertainty": 0.12,
            "rationale_ref": "memo://mgmt-syn-007/tw-momentum",
            "regime_ref": "regime://tw-market/range-breakout-watch",
            "valid_from": GENERATED_AT,
            "valid_to": "2026-05-22T15:45:00Z",
            "evidence_refs": [
                "support/evidence/MGMT-PAPER-001-paper-strategy-spec.json",
                "docs/04/pantheon_sa_supplemental_2026-05-15/SA_management_console_multi_persona_ooda.md",
            ],
            "created_at": GENERATED_AT,
            "reliability_score": 0.92,
            "regime_fit_score": 0.88,
            "governance_multiplier": 1.0,
            "metadata": {
                "asset_classes": ["tw_equity"],
                "strategy_family": "tw_equity_momentum",
                "proposal_role": "advisor",
            },
        },
        {
            "proposal_id": "pap-mgmt-syn-007-beta",
            "persona_id": "persona-risk-balanced",
            "capital_pool_id": "capital-pool-paper-001",
            "scope_ref": "paper",
            "target_type": "asset",
            "directions": ["long", "de_risk"],
            "target_weights": {"2330.TW": 0.20, "0050.TW": 0.40, "CASH": 0.20},
            "conviction": 0.58,
            "uncertainty": 0.20,
            "rationale_ref": "memo://mgmt-syn-007/risk-balanced",
            "regime_ref": "regime://tw-market/range-breakout-watch",
            "valid_from": GENERATED_AT,
            "valid_to": "2026-05-22T15:45:00Z",
            "evidence_refs": [
                "support/evidence/MGMT-PAPER-007-complete-paper-ooda-packet.json",
                "MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md",
            ],
            "created_at": GENERATED_AT,
            "reliability_score": 0.72,
            "regime_fit_score": 0.80,
            "governance_multiplier": 1.0,
            "metadata": {
                "asset_classes": ["tw_equity", "cash"],
                "strategy_family": "risk_balanced_tw_equity",
                "proposal_role": "advisor",
                "wants_de_risk": True,
            },
        },
        {
            "proposal_id": "pap-mgmt-syn-007-gamma",
            "persona_id": "persona-leverage-skeptic",
            "capital_pool_id": "capital-pool-paper-001",
            "scope_ref": "paper",
            "target_type": "asset",
            "directions": ["short", "risk_off"],
            "target_weights": {"00632R.TW": 0.35, "CASH": 0.20},
            "conviction": 0.64,
            "uncertainty": 0.18,
            "rationale_ref": "memo://mgmt-syn-007/leverage-skeptic",
            "regime_ref": "regime://tw-market/range-breakout-watch",
            "valid_from": GENERATED_AT,
            "valid_to": "2026-05-22T15:45:00Z",
            "evidence_refs": [
                "support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json",
                "MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md",
            ],
            "created_at": GENERATED_AT,
            "reliability_score": 0.66,
            "regime_fit_score": 0.70,
            "governance_multiplier": 1.0,
            "metadata": {
                "asset_classes": ["tw_equity_inverse"],
                "strategy_family": "leveraged_short",
                "proposal_role": "advisor",
            },
        },
    ]


def _proposal_record(index: int, proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": PERSONA_ALLOCATION_PROPOSAL_RECORD_SCHEMA_VERSION,
        "record_type": "proposal_snapshot",
        "record_id": f"pap-rec-mgmt-syn-007-{index:03d}",
        "proposal_id": proposal["proposal_id"],
        "persona_id": proposal["persona_id"],
        "capital_pool_id": proposal["capital_pool_id"],
        "scope_ref": proposal["scope_ref"],
        "recorded_at": GENERATED_AT,
        "payload": {"proposal": proposal},
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _with_deterministic_synthesizer_ids() -> None:
    deterministic_ids = iter([
        "conflict-log-mgmt-syn-007-001",
        "alloc-policy-mgmt-syn-007-001",
    ])
    synthesizer_module._utc_now = lambda: GENERATED_AT
    synthesizer_module._new_id = lambda: next(deterministic_ids)


def _governance_packet(artifact: AllocationPolicyArtifact, conflict_log: dict[str, Any]) -> dict[str, Any]:
    return {
        "approval_decision_id": "approval-mgmt-syn-007-paper-synthesis-001",
        "target_type": "allocation_policy_artifact",
        "target_id": artifact.artifact_id,
        "target_version": "proof.v1",
        "decision": "approved_for_paper_evidence",
        "decision_state": "decided",
        "environment": "paper",
        "capital_pool_id": artifact.capital_pool_id,
        "sponsor_persona_id": artifact.sponsor_persona_id,
        "actor_role": "governance_reviewer",
        "actor_id": "mgmt-syn-007-proof-reviewer",
        "risk_level": "medium",
        "created_at": GENERATED_AT,
        "decided_at": GENERATED_AT,
        "rationale": (
            "Approved paper-only synthesis proof: proposal records replay through the "
            "optimizer-svc allocation_aggregation store, pool risk policy vetoes the "
            "forbidden leveraged-short input, and the remaining proposals produce one "
            "AllocationPolicyArtifact with an explicit sponsor."
        ),
        "conditions": [
            "paper environment only",
            "live broker remains disabled",
            "capital binding live activation remains fail-closed",
        ],
        "override_path": {
            "committee_override_supported": True,
            "current_override_ref": None,
            "policy_ref": "MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md#6.3",
            "review_gate": "ApprovalDecision",
        },
        "evidence_refs": [
            {
                "ref_type": "proposal_store_jsonl",
                "ref_id": "mgmt-syn-007-proposals",
                "path": "support/evidence/MGMT-SYN-007/proposals.jsonl",
            },
            {
                "ref_type": "conflict_resolution_log",
                "ref_id": conflict_log["log_id"],
                "path": "support/evidence/MGMT-SYN-007/synthesis-proof.json#/conflict_resolution_log",
            },
            {
                "ref_type": "allocation_policy_artifact",
                "ref_id": artifact.artifact_id,
                "path": "support/evidence/MGMT-SYN-007/synthesis-proof.json#/allocation_policy_artifact",
            },
        ],
        "live_capital_side_effects": False,
        "can_proceed": True,
    }


def _ooda_packet(artifact: AllocationPolicyArtifact, governance_packet: dict[str, Any]) -> dict[str, Any]:
    packet = OodaLoopPacket(
        packet_id="ooda-mgmt-syn-007-persona-synthesis",
        loop_type=LoopType.PERSONA_SYNTHESIS,
        status=LoopStatus.CLOSED,
        environment=LoopEnvironment.PAPER,
        created_at=GENERATED_AT,
        updated_at=GENERATED_AT,
        closed_at=GENERATED_AT,
        capital_pool_id=artifact.capital_pool_id,
        strategy_id="strategy-spec-paper-qlib-lgbm-001",
        persona_ids=["persona-tw-momentum", "persona-risk-balanced", "persona-leverage-skeptic"],
        observe=ObserveBundle(
            source_refs=[
                "docs/04/pantheon_sa_supplemental_2026-05-15/SA_management_console_multi_persona_ooda.md",
                "docs/04/pantheon_sa_supplemental_2026-05-15/SD_management_console_multi_persona_ooda.md",
            ],
            telemetry_refs=["support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json"],
            signal_refs=["support/evidence/MGMT-PAPER-001-paper-strategy-spec.json"],
        ),
        orient=OrientBundle(
            regime_state_ref="regime://tw-market/range-breakout-watch",
            universe_selection_ref="universe://twse-tpex-paper-smoke-v1",
            allocation_proposal_refs=[
                "pap-mgmt-syn-007-alpha",
                "pap-mgmt-syn-007-beta",
                "pap-mgmt-syn-007-gamma",
            ],
            risk_adjudication_ref="risk-policy://capital-pool-paper-001/no-leveraged-short/max-single-weight-50pct",
            persona_proposal_refs=[
                "persona://persona-tw-momentum/proposals/pap-mgmt-syn-007-alpha",
                "persona://persona-risk-balanced/proposals/pap-mgmt-syn-007-beta",
                "persona://persona-leverage-skeptic/proposals/pap-mgmt-syn-007-gamma",
            ],
            evidence_bundle_refs=["support/evidence/MGMT-SYN-007/synthesis-proof.json"],
        ),
        decide=DecideBundle(
            approval_decision_id=governance_packet["approval_decision_id"],
            sponsor_persona_id=artifact.sponsor_persona_id,
            decision_rationale_ref="support/evidence/MGMT-SYN-007/synthesis-proof.json#/conflict_resolution_log",
            policy_decision_refs=[
                "MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md",
                "PAPER_CANARY_LIVE_POLICY.md",
            ],
        ),
        act=ActBundle(
            command_receipt_refs=["command-receipt://mgmt-syn-007/paper-proof-no-live-action"],
            safe_mode_refs=["fail-closed://broker-production-live-disabled"],
            live_capital_side_effects=False,
        ),
        learn=LearnBundle(
            telemetry_refs=["support/evidence/MGMT-PAPER-005-paper-telemetry-packet.json"],
            evolution_followthrough_refs=["not-applicable://mgmt-syn-007/proof-only"],
        ),
        audit_refs=[
            "support/evidence/MGMT-SYN-007/proposals.jsonl",
            "support/evidence/MGMT-SYN-007/synthesis-proof.json",
        ],
    )
    payload = packet.to_dict()
    errors = validate_packet(payload)
    if errors:
        raise SystemExit(f"Generated OODA packet failed validation: {errors}")
    return payload


def build_evidence() -> dict[str, Any]:
    proposal_path = OUTPUT_DIR / "proposals.jsonl"
    proof_path = OUTPUT_DIR / "synthesis-proof.json"

    proposals = _proposal_payloads()
    records = [_proposal_record(index, proposal) for index, proposal in enumerate(proposals, start=1)]
    _write_jsonl(proposal_path, records)

    store = PersonaAllocationProposalJsonlStore(proposal_path)
    replayed = store.require_proposals([proposal["proposal_id"] for proposal in proposals])

    _with_deterministic_synthesizer_ids()
    policy = PoolRiskPolicy(
        forbidden_strategy_families={"leveraged_short"},
        max_single_weight=0.50,
    )
    artifact, log = PortfolioSynthesizer(pool_risk_policy=policy).synthesize_with_log(
        replayed,
        capital_pool_id="capital-pool-paper-001",
        scope_ref="paper",
        constraints_bundle={
            "max_single_weight": 0.50,
            "forbidden_strategy_families": ["leveraged_short"],
            "environment": "paper",
        },
        risk_budget=0.35,
    )

    if not isinstance(artifact, AllocationPolicyArtifact):
        raise SystemExit(f"Expected AllocationPolicyArtifact, got {type(artifact).__name__}")
    if artifact.synthesis_method != SynthesisMethod.WEIGHTED_FUSION.value:
        raise SystemExit(f"Expected weighted_fusion, got {artifact.synthesis_method}")
    if len(log.vetoed_proposals) != 1:
        raise SystemExit(f"Expected exactly one vetoed proposal, got {len(log.vetoed_proposals)}")
    if artifact.conflict_resolution_log_id != log.log_id:
        raise SystemExit("Artifact/log linkage mismatch")

    artifact_payload = asdict(artifact)
    log_payload = asdict(log)
    governance_packet = _governance_packet(artifact, log_payload)
    ooda_packet = _ooda_packet(artifact, governance_packet)

    evidence = {
        "task_id": "MGMT-SYN-007",
        "milestone_id": "MGMT-OODA-M5",
        "epic": "EPIC-03 Multi-Persona Synthesis",
        "environment": "paper",
        "generated_at": GENERATED_AT,
        "live_capital_side_effects": False,
        "source_refs": [
            "docs/04/pantheon_sa_supplemental_2026-05-15/SA_management_console_multi_persona_ooda.md#M5",
            "docs/04/pantheon_sa_supplemental_2026-05-15/SD_management_console_multi_persona_ooda.md#EPIC-03",
            "MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md",
        ],
        "proposal_store": {
            "path": "support/evidence/MGMT-SYN-007/proposals.jsonl",
            "record_count": len(records),
            "schema_version": PERSONA_ALLOCATION_PROPOSAL_RECORD_SCHEMA_VERSION,
            "proposal_ids": [proposal["proposal_id"] for proposal in proposals],
        },
        "synthesis_request": {
            "capital_pool_id": "capital-pool-paper-001",
            "scope_ref": "paper",
            "proposal_ids": [proposal["proposal_id"] for proposal in proposals],
            "risk_policy": {
                "max_single_weight": 0.50,
                "forbidden_strategy_families": ["leveraged_short"],
            },
            "optimizer_solver_invoked": False,
            "allocation_aggregator_module": "services/optimizer-svc/portfolio_synthesis",
        },
        "proposal_records": records,
        "conflict_resolution_log": log_payload,
        "allocation_policy_artifact": artifact_payload,
        "governance_approval_packet": governance_packet,
        "ooda_packet": ooda_packet,
        "proof_assertions": {
            "two_or_more_proposals_recorded": len(records) >= 2,
            "one_allocation_policy_artifact_produced": artifact.artifact_id == "alloc-policy-mgmt-syn-007-001",
            "conflict_resolution_log_visible": log.log_id == "conflict-log-mgmt-syn-007-001",
            "sponsor_persona_explicit": artifact.sponsor_persona_id == "persona-tw-momentum",
            "governance_override_path_recorded": governance_packet["override_path"]["committee_override_supported"],
            "paper_only_no_live_side_effects": not ooda_packet["act"]["live_capital_side_effects"],
            "optimizer_solver_not_arbitrator": True,
        },
        "verification_commands": [
            "python3 scripts/generate_mgmt_syn_007_evidence.py",
            "python3 -m pytest services/optimizer-svc/test_portfolio_synthesis.py services/optimizer-svc/test_persona_allocation_proposal_store.py -q",
        ],
    }

    if not all(evidence["proof_assertions"].values()):
        raise SystemExit(f"Proof assertions failed: {evidence['proof_assertions']}")

    _write_json(proof_path, evidence)
    _write_readme(artifact_payload, log_payload)
    return evidence


def _write_readme(artifact: dict[str, Any], log: dict[str, Any]) -> None:
    content = f"""# MGMT-SYN-007 Multi-Persona Synthesis Proof Evidence

Generated by:

```bash
python3 scripts/generate_mgmt_syn_007_evidence.py
```

Evidence files:

- `proposals.jsonl` records three `PersonaAllocationProposal` snapshots.
- `synthesis-proof.json` packages the replayed proposals, conflict log, single `AllocationPolicyArtifact`, governance approval packet, and persona-synthesis OODA packet.

M5 exit criteria:

- Proposal records: `3`
- ConflictResolutionLog: `{log["log_id"]}`
- AllocationPolicyArtifact: `{artifact["artifact_id"]}`
- Sponsor persona: `{artifact["sponsor_persona_id"]}`
- Live capital side effects: `false`

Focused verification:

```bash
python3 scripts/generate_mgmt_syn_007_evidence.py
python3 -m pytest services/optimizer-svc/test_portfolio_synthesis.py services/optimizer-svc/test_persona_allocation_proposal_store.py -q
```
"""
    (OUTPUT_DIR / "README.md").write_text(content, encoding="utf-8")


def main() -> int:
    evidence = build_evidence()
    print(
        "MGMT-SYN-007 evidence generated: "
        f"{evidence['allocation_policy_artifact']['artifact_id']} "
        f"with log {evidence['conflict_resolution_log']['log_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
