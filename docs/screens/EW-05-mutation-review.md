# EW-05 Mutation Review — Screen Spec

Last updated: 2026-04-20
Status: contract-ready — live BFF route and command vocabulary verified
Tier: screen spec
Feature ID: `EW-05-mutation-review`
Task: `EW-05-OPEN-001`

---

## Purpose

The Mutation Review screen lets an authorized operator review a pending `EvolutionDecision`, inspect the evidence that triggered it, and approve or reject it via a backend-shaped command. All data and authority signals come from the Pantheon BFF. No mutation authority, risk assessment, or approval chain may be inferred client-side.

---

## Route

```
/evolution/mutation-review/:decision_id
```

Path param: `decision_id` — the `EvolutionDecision` identity.

---

## Readiness Gate

Pantheon has confirmed the required EW-05 gates live:

1. `GET /api/v1/operator/mutation-review/{decision_id}` is live and returning the published field shape.
2. `POST /api/v1/operator/commands` accepts `ApproveMutation` and `RejectMutation` with the published payload shape.
3. `allowedActions.canApproveMutation` and `allowedActions.canRejectMutation` are returned by the BFF.

Frontend may build the production screen against the live handoff bundle. If the
runtime payload diverges from this contract, emit the canonical EW-05 bff-gap
handoff instead of inventing decision state or CTA authority.

---

## Surface Panels

### 1. Decision Context Header

Displays the primary identity and lifecycle state of the `EvolutionDecision`.

| Field | Source | Notes |
|---|---|---|
| `decision_id` | BFF response | Display as badge or identifier |
| `target_type` | BFF response | Normalized enum — display as readable label |
| `target_id` | BFF response | Link to the target artifact if a detail route exists |
| `action_type` | BFF response | Normalized enum — display as readable label |
| `risk_level` | BFF response | `low` / `medium` / `high` — color-coded badge |
| `decision_state` | BFF response | State machine position |
| `approval_decision_id` | BFF response | Link to ApprovalDecision detail if available |
| `created_at` | BFF response | ISO timestamp |

### 2. Proposed Changes Panel

Operator-readable summary of what the approved mutation would change.

- Source: `proposed_changes` object in BFF response.
- Must include `summary` (human-readable string), `target_stage` (if applicable), and `downstream_plane` (the plane that would execute the change).
- The frontend must not compute or narrate change semantics from raw fields. Display what the BFF provides.

### 3. Incident and Postmortem Evidence Rail

Read-only evidence context for incident-driven mutations.

- Shows `linked_incident_id` and `linked_postmortem_id` as evidence links with summary labels.
- `evidence_refs[]` items displayed as a typed list: each with `ref_type`, `ref_id`, and `summary`.
- No write actions on this panel.
- If `linked_incident_id` and `linked_postmortem_id` are both absent, show "No linked incident or postmortem" — do not hide the panel.

### 4. Rollback Follow-Through Panel

Read-only display of rollback semantics when the mutation implies runtime mitigation.

- Shows `rollback_followthrough` fields from the BFF response: `rollback_request_ref`, `rollback_action_type`, and `followthrough_note`.
- If `rollback_followthrough` is null or empty, show "No rollback follow-through associated with this decision."
- This panel cites rollback policy objects and rollback request refs — it is not a write surface. No rollback commands may be submitted from this panel.

### 5. Risk Assessment Panel

Threshold signals and risk summary that justify the mutation.

- Source: `risk_assessment` object from the BFF response.
- Fields: `risk_summary` (string), `threshold_triggers[]` (normalized threshold breaches), `severity` (if tied to an incident).
- The frontend must not compute risk level or threshold breach state.

### 6. Required Approvals

Explicit list of who must review or approve this decision.

- Source: `required_approvals[]` from the BFF response.
- Each entry: `role`, `approved_by` (null if pending), `approved_at` (null if pending), `status` (`pending` / `approved` / `rejected`).
- Display as a checklist with per-row status badges.

### 7. Approve / Reject CTA

The only two write actions on this screen. Both are gated by `allowedActions`.

| CTA | Authority signal | Command | Visible when |
|---|---|---|---|
| Approve Mutation | `allowedActions.canApproveMutation === true` | `ApproveMutation` | Only when `canApproveMutation` is `true` in BFF response |
| Reject Mutation | `allowedActions.canRejectMutation === true` | `RejectMutation` | Only when `canRejectMutation` is `true` in BFF response |

Rules:
- If either authority signal is absent from the BFF response, suppress the corresponding CTA entirely. Do not fall back to `risk_level` or `decision_state` inference.
- If `meta.surfaces.mutation_review` is `unavailable`, suppress both CTAs.
- After a command is submitted, poll the read route for state confirmation. Do not optimistically update `decision_state`.
- Both CTAs may optionally accept a `note` field. If the BFF schema includes `note`, provide a text input before confirming.

---

## Degradation Handling

| `meta.surfaces.mutation_review` | Required behavior |
|---|---|
| `fresh` | Normal display |
| `stale` | Non-dismissable staleness banner at top; data visible with caveat; CTAs still hidden if `canApproveMutation`/`canRejectMutation` absent |
| `unavailable` | Replace panel content with degradation notice; suppress both CTAs |

When the mutation evidence surface is unavailable, both CTAs must disappear entirely. Do not allow an operator to submit a governance action based on incomplete evidence.

---

## State Requirements

Every data panel must handle:

- `loading`
- `empty`
- `stale`
- `unavailable`
- `error`

Do not map `stale` to `empty`.

---

## Constraints

- All fields come from the BFF read route. No client-side inference.
- `allowedActions.canApproveMutation` and `allowedActions.canRejectMutation` are the sole CTA-visibility truth.
- The screen reviews mutation authority only — it does not create a parallel `ApprovalDecision`, submit runtime rollback commands, deploy or redeploy artifacts, or mutate `RuntimeBinding`, `DeploymentPlan`, or incident objects.
- Incident and rollback evidence are read-only references.
- Degradation banner is inherited from `PKT-005`.
- If any required field is absent from the BFF response, write a `bff-gap` coordination file instead of rendering with invented state.

---

## Navigation Context

This screen sits inside the Evolution Workbench sidebar section. Navigation:

- Back to Evolution Center (`EW-02`) via breadcrumb or back button.
- Link to the `decision_id`'s full detail on `EW-02` if the BFF provides a URL.

---

## References

- BFF contract: `docs/bff/EW-05-mutation-review.md`
- Example payload: `docs/examples/EW-05-mutation-review.json`
- Frontend change spec: `docs/pantheon-handoffs/EW-05-mutation-review/FRONTEND_CHANGE_SPEC.md`
- Packet family: `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md`
- Canonical policy: `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- Evolution decision contract: `services/control-plane/governance/evolution_decision.contract.md`
- Approval contract: `services/control-plane/governance/contract.md`
- Rollback semantics: `ROLLBACK_AND_POSITION_SEMANTICS.md`
