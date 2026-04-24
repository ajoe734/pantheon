# Delivery Closure And Loop States

Last updated: 2026-04-19
Status: canonical semantics for Pantheon delivery-loop state and closure
Tier: L2 Planning & Execution
Scope: `.coordination` status meanings, closure rules, and when a frontend or backend loop is actually complete
Conflict rule: this file defines delivery-loop semantics, but packet-specific contracts and L1 policy still define the underlying product truth

## 1. Core Rule

A packet is not complete merely because a spec exists, a frontend implementation exists, or a backend route exists.

The loop is complete only when:

1. the canonical packet artifacts are published
2. Pantheon-owned backend or command gaps for that packet are resolved
3. the current frontend return cycle has either been accepted or explicitly closed with no remaining follow-up

## 2. Authority Order Inside A Delivery Loop

Read `.coordination` in this order:

1. `*-contract-ready.yaml`
2. `*-backend-delivery.yaml` or `*-needs-runtime.yaml`
3. `*-lovable-ui-task.yaml`
4. `*-ui-done.yaml`
5. `*-frontend-feedback.yaml`

If these disagree, the later artifact may describe a newer execution fact, but it does not rewrite the canonical contract.

## 3. Status Vocabulary

| Status | Meaning | Terminal for the current file? | Counts as packet loop closure? |
|---|---|---|---|
| `published` | canonical packet or contract artifact was published | yes | no |
| `ready` | Pantheon side says the next external loop may start honestly | no | no |
| `blocked` | a real blocker exists and the current owner cannot truthfully continue | no | no |
| `resolved` | a request or blocker was satisfied | yes | only if no later loop step remains |
| `delivered` | Pantheon-owned backend or runtime work for the packet landed and was verified | yes for that backend-delivery record | no by itself |
| `followup-required` | another loop iteration is required; same packet remains open | no | no |
| `follow-up-required` | same meaning as `followup-required`; use one spelling going forward | no | no |
| `cycle-2-dispatched` | the next follow-up cycle has been launched | no | no |
| `loop-complete` | the current packet scope is closed for both Pantheon and the paired frontend loop | yes | yes |
| `closed` | record is administratively closed | yes | only if it is the final active loop record |
| `completed` | work item finished | yes | only if it is the final active loop record |

## 4. Stage Meanings

| Artifact type | What it means | What it does not mean |
|---|---|---|
| `contract-ready` | packet truth exists and may be handed off honestly once authorized endpoints exist | backend is live |
| `backend-delivery` | Pantheon has implemented the promised backend slice and verified it | frontend loop is closed |
| `needs-runtime` | a frontend or review loop found a Pantheon runtime gap that must be fixed before truth resumes | packet contract is wrong |
| `lovable-ui-task` | frontend may implement against the published contract and allowed endpoints | Pantheon accepts the returned UI automatically |
| `ui-done` | frontend reports an implementation return | the return is accepted |
| `frontend-feedback` | Pantheon reviewed the returned UI and decided whether another cycle is needed | backend obligations are automatically satisfied |

## 5. Closure Rules

Use these rules when deciding whether a packet is done:

1. `published` is never sufficient.
2. `delivered` is never sufficient if the paired UI loop still has `followup-required`.
3. `resolved` on a `needs-runtime` request means the runtime blocker is gone, not that the whole packet is closed.
4. `loop-complete` is the clearest packet-level closure state and should be preferred on the active response artifact once the loop truly ends.
5. If a packet still has an active frontend replay or metadata-truthfulness issue, keep it open even when the backend route is already live.

## 6. Missing Or Invalid State

These are considered coordination defects:

- a non-template `.coordination` YAML file with no explicit `status`
- YAML that cannot be parsed
- contradictory live records that leave Pantheon unable to tell whether a packet is blocked or closed

Templates and `*.example.yaml` files may omit `status`.
Active request or response artifacts should not.

When a coordination file is syntactically invalid, fix the file before interpreting the loop. Parse-invalid state is not a soft warning; it breaks machine truth.

## 7. Current Repo Implication

In the current repo:

### Governance Workbench (GV)

- `GV-02` (PKT-006 Approval Queue) is **loop-complete**; the frontend loop closed and `lovable-ui-task` status is `loop-complete`
- `GV-04` (PKT-007 Deployment Diff) is backend-delivered but **not fully loop-complete**; the `lovable-ui-task` status is `followup-required` and the frontend replay is still open
- `GV-05` (PKT-008 Rollback Review) and `GV-06` (PKT-009 Governance Audit Rail) are **loop-complete** for the current packet scope
- `GV-01` (PKT-001 Governance Review Queue) and `GV-03` (F-042 Promotion Review) have frontend feedback returned but Pantheon review disposition is `follow-up-required`; loops remain open

### Persona Workbench (PKT-004)

All four PKT-004 surfaces completed their frontend loops and are loop-complete:

- `PKT-004-persona-drilldowns`: loop-complete
- `PKT-004-persona-management`: loop-complete
- `PKT-004-capital-binding-drilldowns`: loop-complete
- `PKT-004-deployment-approval-drilldowns`: loop-complete

### Evolution Workbench Baseline (PKT-003)

- `PKT-003-evolution-center`: **loop-complete** (Pantheon review disposition: `close`)
- `PKT-003-post-incident-review`: Lovable `lovable-ui-task` status is `closed`; the Pantheon response `frontend-feedback` disposition was `follow-up-required` — treat as loop-closed for the current scope pending any future reopening
- `PKT-003-lineage-view`: backend-delivered; `lovable-ui-task` is `ready` (loop not yet fully returned)

### Operator Console (PKT-010 to PKT-014)

Frontend feedback has been returned for PKT-010, PKT-011, PKT-012, PKT-013, and PKT-014. All Pantheon review dispositions are `follow-up-required`; these loops remain open and are tracked separately in `ai-status.json`.

### BFF-gap closure note

BFF-gap files for already-closed packets must carry `status: resolved`. Resolved gaps with only a `resolved: true` field and no `status:` field are a coordination defect per Section 6.

### Parse-invalid files

parse-invalid `.coordination` files must be repaired immediately so that status scans do not silently drop active gaps
