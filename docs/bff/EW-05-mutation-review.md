# EW-05 Mutation Review — BFF Contract

Last updated: 2026-04-20
Status: contract-ready — live BFF route and command vocabulary verified
Tier: service-level BFF contract
Feature ID: `EW-05-mutation-review`
Task: `EW-05-OPEN-001`

---

## Purpose

This contract defines:

1. The operator mutation-review read route and its composed response object.
2. The `ApproveMutation` and `RejectMutation` command vocabulary that extends `POST /api/v1/operator/commands`.
3. The `allowedActions` authority signals that gate CTA visibility.
4. The `meta.surfaces.mutation_review` staleness signal.

No part of this contract may be derived client-side. The BFF is the single composition authority.

---

## Read Route

### `GET /api/v1/operator/mutation-review/{decision_id}`

Composes a full operator mutation-review projection from the `EvolutionDecision`, its linked `ApprovalDecision`, incident and postmortem evidence, rollback follow-through refs, and the calling operator's authority signals.

#### Path Parameters

| Param | Type | Required | Description |
|---|---|---|---|
| `decision_id` | string | yes | `EvolutionDecision` identity |

#### Query Parameters

None required in v1.

#### Response — `MutationReviewProjection`

```typescript
interface MutationReviewProjection {
  decision_id: string;
  target_type: EvolutionTargetType;   // "strategy_spec" | "alpha_template" | "candidate_artifact" | "allocation_policy_artifact" | "persona" | "persona_capital_binding" | "capital_pool"
  target_id: string;
  target_version: string;
  action_type: EvolutionActionType;   // normalized action enum; see EVOLUTION_REVIEW_AND_THRESHOLDS.md §5
  decision_state: EvolutionDecisionState; // "proposed" | "reviewed" | "approved" | "executed" | "rejected" | "canceled" | "superseded"
  risk_level: "low" | "medium" | "high";
  created_at: string;                 // ISO 8601
  approval_decision_id: string | null;

  proposed_changes: ProposedChanges;
  risk_assessment: RiskAssessment;
  required_approvals: RequiredApproval[];
  review_chain: ReviewStep[];

  linked_incident_id: string | null;
  linked_postmortem_id: string | null;
  evidence_refs: EvidenceRef[];

  rollback_followthrough: RollbackFollowthrough | null;

  allowedActions: MutationReviewAllowedActions;

  meta: MutationReviewMeta;
}

interface ProposedChanges {
  summary: string;                    // human-readable operator narrative
  target_stage: string | null;        // "paper" | "canary" | "live" | null
  downstream_plane: string | null;    // "governance" | "research" | "deployment" | "runtime" | null
  change_details: ChangeDetail[];     // structured detail rows
}

interface ChangeDetail {
  field: string;
  current_value: string | null;
  proposed_value: string | null;
  note: string | null;
}

interface RiskAssessment {
  risk_summary: string;
  severity: string | null;            // "severity_1" | "severity_2" | null
  threshold_triggers: ThresholdTrigger[];
}

interface ThresholdTrigger {
  trigger_type: string;               // e.g. "performance_degradation" | "execution_drift" | "feature_drift" | "human_correction"
  metric: string;
  observed_value: string;
  threshold_value: string;
  threshold_source: string;           // reference to the policy that defines this threshold
}

interface RequiredApproval {
  role: string;                       // e.g. "reviewer_on_duty" | "risk_owner" | "operator" | "governance_committee"
  approved_by: string | null;         // actor ID or null if pending
  approved_at: string | null;         // ISO 8601 or null if pending
  status: "pending" | "approved" | "rejected";
}

interface ReviewStep {
  action: "reviewed" | "approved" | "rejected" | "canceled" | "executed";
  actor_role: string;
  actor_id: string;
  acted_at: string;                   // ISO 8601
  note: string | null;
}

interface EvidenceRef {
  ref_type: string;                   // e.g. "drift_report" | "telemetry_summary" | "review_ticket" | "postmortem" | "incident"
  ref_id: string;
  summary: string;
}

interface RollbackFollowthrough {
  rollback_request_ref: string | null;
  rollback_action_type: string | null; // "replace" | "pause_then_replace" | "liquidate_then_replace"
  followthrough_note: string | null;
}

interface MutationReviewAllowedActions {
  canApproveMutation: boolean;
  canRejectMutation: boolean;
}

interface MutationReviewMeta {
  snapshot_at: string;                // ISO 8601; when this projection was composed
  surfaces: {
    mutation_review: "fresh" | "stale" | "unavailable";
  };
}
```

#### Authority Signal Rules

The BFF must evaluate `canApproveMutation` and `canRejectMutation` against the calling operator's identity and the decision's `risk_level`, `decision_state`, and `review_chain` before returning the response.

Rules:
- `canApproveMutation` may only be `true` when: `decision_state === "reviewed"` AND the calling operator holds the required approval role for the decision's `risk_level`.
- `canRejectMutation` may only be `true` when: `decision_state` is `"proposed"` or `"reviewed"` AND the calling operator holds a review or approval role.
- If the mutation evidence cannot be reliably composed (e.g. dependent services degraded), both signals must be `false` and `meta.surfaces.mutation_review` must be `"unavailable"`.
- Neither signal may be derived from `risk_level` or `action_type` alone without checking operator role.

#### `meta.surfaces.mutation_review` Semantics

| Value | Meaning | UI behavior |
|---|---|---|
| `"fresh"` | All evidence sources are healthy | Normal display; CTAs follow `allowedActions` |
| `"stale"` | Evidence was served from cache or a partial source | Non-dismissable staleness banner; CTAs follow `allowedActions` |
| `"unavailable"` | One or more required evidence sources are down | Degradation banner replaces panels; both CTAs must be suppressed regardless of `allowedActions` |

#### Error Responses

| Status | When | Body |
|---|---|---|
| 404 | `decision_id` not found | `{ "error": "decision_not_found", "decision_id": "<id>" }` |
| 403 | Calling operator lacks read permission | `{ "error": "forbidden" }` |
| 503 | Evidence sources unavailable | `{ "error": "evidence_unavailable", "meta": { "surfaces": { "mutation_review": "unavailable" } } }` |

---

## Command Vocabulary

The mutation-review write surface extends `POST /api/v1/operator/commands`.

### `ApproveMutation`

Approves a reviewed `EvolutionDecision` at the review-authority level appropriate to the calling operator. This changes governance-review state and may trigger downstream follow-through per canonical policy. It does not directly execute deployment, runtime, or research-plane actions.

```typescript
interface ApproveMutationCommand {
  command_type: "ApproveMutation";
  decision_id: string;
  note?: string;
}
```

#### Preconditions (enforced by BFF / command handler)

- `decision_state === "reviewed"` at the time the command is received.
- Calling operator holds the required approval role for the decision's `risk_level` per `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.
- `allowedActions.canApproveMutation === true` in the current read projection.

#### Effects

- Writes `decision_state -> "approved"` via the governance service.
- Appends a `ReviewStep` with `action = "approved"` to `review_chain`.
- May trigger downstream follow-through initiation per the action-routing matrix in `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §11.1. The BFF does not execute downstream actions directly.

#### Response

```typescript
interface ApproveMutationResponse {
  command_accepted: true;
  decision_id: string;
  new_state: "approved";
  committed_at: string;          // ISO 8601
}
```

---

### `RejectMutation`

Rejects a proposed or reviewed `EvolutionDecision`. The decision is closed without execution.

```typescript
interface RejectMutationCommand {
  command_type: "RejectMutation";
  decision_id: string;
  note?: string;
}
```

#### Preconditions

- `decision_state` is `"proposed"` or `"reviewed"` at the time the command is received.
- Calling operator holds a review or approval role for the decision's `risk_level`.
- `allowedActions.canRejectMutation === true` in the current read projection.

#### Effects

- Writes `decision_state -> "rejected"`.
- Appends a `ReviewStep` with `action = "rejected"` to `review_chain`.
- Does not initiate any downstream follow-through.

#### Response

```typescript
interface RejectMutationResponse {
  command_accepted: true;
  decision_id: string;
  new_state: "rejected";
  committed_at: string;          // ISO 8601
}
```

---

### Command Error Responses

| Status | Reason | Body |
|---|---|---|
| 400 | Unknown `command_type` or missing required field | `{ "error": "invalid_command", "detail": "..." }` |
| 403 | Operator lacks authority for this action | `{ "error": "forbidden", "detail": "..." }` |
| 409 | `decision_state` no longer allows this command | `{ "error": "state_conflict", "current_state": "...", "detail": "..." }` |
| 503 | Governance service unavailable | `{ "error": "governance_unavailable" }` |

---

## Write-Owner Boundary

`ApproveMutation` and `RejectMutation` change governance-review state only. They do not:

- Create or modify a `DeploymentPlan`.
- Initiate a rollback request.
- Modify `RuntimeBinding`.
- Trigger retrain or research work items directly.

Downstream follow-through — if any — is initiated by the governance service after accepting the command. The BFF is not the downstream executor.

This boundary is enforced by the governance service contract in `services/control-plane/governance/contract.md` and the action-routing matrix in `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §11.1.

---

## Dependent Canonical Objects

| Object | Canonical source | Consumed fields |
|---|---|---|
| `EvolutionDecision` | `services/control-plane/governance/evolution_decision.contract.md`, `evolution_decision.schema.json` | `decision_id`, `target_type`, `target_id`, `target_version`, `action_type`, `decision_state`, `risk_level`, `approval_decision_id`, `linked_incident_id`, `linked_postmortem_id`, `evidence_refs`, `review_chain`, `created_at` |
| `ApprovalDecision` | `services/control-plane/governance/contract.md` | `approval_decision_id`, approval authority chain |
| `IncidentCase` | `services/incident/contract.md`, `incident_case.schema.json` | `linked_incident_id` evidence |
| `Postmortem` | `services/incident/contract.md`, `postmortem.schema.json` | `linked_postmortem_id` evidence |
| Rollback semantics | `ROLLBACK_AND_POSITION_SEMANTICS.md` | `rollback_action_type` vocabulary, rollback ownership rules |
| Evolution policy | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | `risk_level`, owner tiers, action routing |

---

## Relationship to Existing Operator Command Surface

`ApproveMutation` and `RejectMutation` are governance-review commands that extend `POST /api/v1/operator/commands`. They must:

- Use the same command-dispatch infrastructure.
- Respect the same idempotency and audit requirements.
- Not be treated as a separate command endpoint.

The operator commands surface is documented in the Operator Console packet family.

---

## References

- Screen spec: `docs/screens/EW-05-mutation-review.md`
- Example payload: `docs/examples/EW-05-mutation-review.json`
- Frontend handoff: `docs/pantheon-handoffs/EW-05-mutation-review/FRONTEND_CHANGE_SPEC.md`
- Packet family: `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md`
- Evolution policy: `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- Evolution decision contract: `services/control-plane/governance/evolution_decision.contract.md`
- Approval contract: `services/control-plane/governance/contract.md`
- Rollback semantics: `ROLLBACK_AND_POSITION_SEMANTICS.md`
