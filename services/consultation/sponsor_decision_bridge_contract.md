# Sponsor Decision Bridge Contract

Status: ASK-008 implementation contract
Scope: committee sponsor decision -> governance action proposal payload

## Purpose

`services/consultation/sponsor_decision_bridge.py` is the narrow bridge between
consultation committee sponsor outcomes and downstream governance workflows.

The bridge is intentionally side-effect free. It validates a sponsor decision and
returns one proposal object:

- `ApprovalDecisionProposal` for normal approval-governance work
- `EvolutionDecisionProposal` for evolution-governance work

It does not create, update, or persist `ApprovalDecision`, `EvolutionDecision`,
or any governance store record. The authoritative governance service remains
responsible for accepting the proposal, assigning review ownership, writing audit
events, and persisting lifecycle state.

## Input

`bridge(sponsor_decision)` accepts either a mapping, the local `SponsorDecision`
dataclass, or a structured model exposing `model_dump()` / `dict()`.

Required shared fields:

| Field | Description |
|---|---|
| `decision_id` | Stable sponsor decision identity used for deterministic proposal IDs |
| `type` | `approval`, `approval_decision`, `evolution`, or `evolution_decision` |
| `sponsor_persona_id` | Committee sponsor persona identity; required for lineage |
| `target_type` | Governance target type for the selected route |
| `target_id` | Target object identity |
| `target_version` | Immutable target version or snapshot key |

Recommended shared fields:

| Field | Description |
|---|---|
| `sponsor_decision` | `approved`, `conditional`, or `rejected`; stored as advisory metadata |
| `rationale` / `rationale_ref` | Human-readable rationale or pointer to committee rationale |
| `evidence_refs[]` | Strings or governance evidence objects; strings become `manual_review_ticket` refs |
| `committee_id`, `handoff_id`, `trace_id` | Consultation lineage copied into proposal metadata |
| `conditions[]` | Advisory conditions for conditional sponsor outcomes |
| `capital_pool_id`, `persona_id` | Optional target context passed through to governance |

Evolution route also requires:

| Field | Description |
|---|---|
| `action_type` | Canonical `EvolutionDecision.action_type` |
| `target_stage` | Required when `action_type = "freeze"` |

Evolution route may also include `threshold_snapshots[]`, `linked_incident_id`,
and `linked_postmortem_id`.

## Output Semantics

Both proposal objects expose `.to_dict()` and always set
`decision_state = "proposed"`.

Approval proposals contain:

- `proposal_type = "approval_decision"`
- deterministic `decision_id = "approval-proposal-from-{source_decision_id}"`,
  unless `proposal_id` is provided
- governance target identity and risk level
- advisory rationale copied from the committee sponsor decision
- normalized evidence refs
- lineage and recommendation metadata

Evolution proposals contain:

- `proposal_type = "evolution_decision"`
- deterministic `decision_id = "evolution-proposal-from-{source_decision_id}"`,
  unless `proposal_id` is provided
- canonical evolution target identity, action type, risk level, and evidence
- `created_by_role = "operator"` by default
- `created_by_id = sponsor_persona_id`
- consultation lineage metadata

`sponsor_decision` is advisory metadata. The bridge never converts it into a
decided governance result, because final approval/rejection authority belongs to
the governance service lifecycle.

## Validation Rules

- Missing `sponsor_persona_id` is rejected.
- Unsupported `type` is rejected.
- Approval route target and risk values must fit `ApprovalDecision` contract
  enums.
- Evolution route target, action, stage, and risk values must fit
  `EvolutionDecision` contract enums.
- Evolution risk is inferred from action semantics. A supplied `risk_level` must
  match the inferred value.
- Evidence refs must be strings or objects with a non-empty `ref_id` / `id`.

## Verification

Focused verification:

```bash
pytest -q services/consultation/test_sponsor_decision_bridge.py
```
