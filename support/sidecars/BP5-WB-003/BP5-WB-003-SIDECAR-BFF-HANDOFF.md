# BP5-WB-003 Governance Workbench BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `BP5-WB-003` - Packetize Governance Workbench follow-on surfaces
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude`
**Parent Status**: `todo`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-04-15`

> This is a support artifact only. It does not modify canonical truth, L1 policy files, or core
> runtime, registry, governance, or deployment implementations. It packages the current Governance
> Workbench BFF and frontend reality into a parent-owner-ready handoff packet.

---

## 1. Parent Task Summary

`BP5-WB-003` is the Governance Workbench packetization slice for the follow-on modules after the
existing baseline screens.

From `ai-status.json`, the parent acceptance is still:

1. governance follow-on surfaces are packetized against canonical approval and deployment objects
2. review, diff, rollback, and audit rails do not fork the existing governance semantics

From the phase-3 workbench backlog, the current Governance Workbench shape is:

- `GV-01 Review queue` - ready baseline
- `GV-03 Promotion Review` - ready baseline
- `GV-02 Approval queue` - blocked on queue projection and CTA authority extension
- `GV-04 Deployment diff` - blocked on a dedicated diff read model
- `GV-05 Rollback review` - blocked on a dedicated rollback review surface
- `GV-06 Governance audit rail` - blocked on a dedicated audit BFF surface

This sidecar is deliberately narrow: it does **not** create any new packet truth. It only records
what is already reusable, what is only contract-ready, and what still lacks a BFF/read-model path
before frontend handoff is honest.

---

## 2. Source References

| Document | Why it matters |
|---|---|
| `ai-status.json` | Live source for `BP5-WB-003` scope and acceptance |
| `.orchestrator/task-briefs/bp5_wb_003_sidecar_bff_handoff.md` | Task-scoped sidecar instructions and artifact path |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | Canonical Governance Workbench module inventory, readiness, and dependency order |
| `docs/bff/PKT-001-governance-review-queue.md` | Canonical queue-shaped review baseline |
| `docs/bff/F-042-promotion-review.md` | Canonical promotion review baseline |
| `docs/pantheon-handoffs/PKT-001-governance-review-queue/FRONTEND_CHANGE_SPEC.md` | Existing frontend handoff for the review queue baseline |
| `docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md` | Existing frontend handoff for the promotion review baseline |
| `services/control-plane/bff/BFF_API_CONTRACT.md` | Current shared BFF route inventory and composed-view list |
| `services/control-plane/bff/main.py` | Current live BFF implementation evidence |
| `services/governance/contract.md` | Upstream canonical approval and audit service boundary |
| `services/deployment/contract.md` | Upstream canonical deployment-plan service boundary |

---

## 3. Current Governance Baseline

### 3.1 Reusable packet baseline

| Module | Packet / contract state | Current repo reality | Use as |
|---|---|---|---|
| `GV-01 Review queue` | `docs/screens/PKT-001-governance-review-queue.md`, `docs/bff/PKT-001-governance-review-queue.md`, `docs/examples/PKT-001-governance-review-queue.json`, and `docs/pantheon-handoffs/PKT-001-governance-review-queue/FRONTEND_CHANGE_SPEC.md` already exist | I found the queue contract, example payload, and frontend handoff materials, but I did **not** find a matching live route in `services/control-plane/bff/main.py` or an entry in `services/control-plane/bff/BFF_API_CONTRACT.md` | queue vocabulary, pagination pattern, and `allowedActions` review-routing pattern |
| `GV-03 Promotion Review` | `F-042` contract, example payload, and frontend handoff already exist | `GET /api/v1/operator/deployment-review/{plan_id}` is listed in `BFF_API_CONTRACT.md` and implemented in `main.py`; `POST /api/v1/operator/commands` is the published write path | the live governance action-authority baseline |

### 3.2 Live BFF and service surfaces that later Governance modules can reuse

| Surface | Route | Status in repo | Why it matters for follow-on modules |
|---|---|---|---|
| Deployment plan list | `GET /api/v1/deployment-plans` | implemented in `main.py`; listed in `BFF_API_CONTRACT.md` | provides plan identity and stage context |
| Deployment plan detail | `GET /api/v1/deployment-plans/{plan_id}` | implemented | base read for diff and approval detail views |
| Approval decision list | `GET /api/v1/approval-decisions` | implemented | current decision-level read surface; not a queue projection |
| Approval decision detail | `GET /api/v1/approval-decisions/{decision_id}` | implemented | current decision drawer/detail source |
| Runtime rollback history | `GET /api/v1/runtimes/{runtime_id}/rollbacks` | implemented | current runtime-scoped rollback evidence |
| Global rollback list | `GET /api/v1/rollbacks` | implemented | current global rollback history; still not a review surface |
| Promotion review composed view | `GET /api/v1/operator/deployment-review/{plan_id}` | implemented | current page-shaped governance read baseline |
| Generic operator write path | `POST /api/v1/operator/commands` | implemented in current BFF | existing action submission pattern used by `F-042` |
| Governance approval lifecycle | `POST /api/governance/approvals/...` | implemented in `services/governance/main.py` and documented in `services/governance/contract.md` | upstream canonical write owner for approval decisions |
| Governance audit log | `GET /api/governance/audit` | implemented upstream in governance service | upstream audit source exists, but no operator BFF projection exists yet |
| Deployment plan service reads | `GET /api/deployment/plans`, `GET /api/deployment/plans/{plan_id}` | documented in `services/deployment/contract.md` | upstream deployment truth exists, but no diff read model is exposed |

### 3.3 Baseline consistency note

The phase-3 Governance backlog treats `GV-01 Review queue` as a ready baseline with existing BFF
backing. In the current repo scan for this sidecar, that backing appears as a published
contract/spec/example/handoff set, but **not** as a verified live route in the shared BFF runtime
files (`main.py`, `BFF_API_CONTRACT.md`).

That means the parent owner should treat `GV-01` as:

- definitely reusable as packet language and frontend contract vocabulary
- not yet proven as a live shared-BFF implementation in the current worktree

This packet does not resolve that mismatch; it only records it so the parent owner can decide
whether to absorb `GV-01` as a contract-only baseline or re-sync it into the live BFF inventory.

---

## 4. BFF Query Gap Matrix For Remaining Governance Modules

| Module | Reusable inputs available today | Missing BFF / read-model gap | Frontend handoff status |
|---|---|---|---|
| `GV-02 Approval queue` | `GET /api/v1/approval-decisions`; `GET /api/v1/approval-decisions/{decision_id}`; upstream governance write lifecycle in `services/governance/contract.md`; `F-042` proves backend-shaped `allowedActions` precedent | no approval-queue projection with pending-filter semantics, operator assignment, or `allowedActions.canApprove` / `canReject`; no page-shaped queue payload for bulk or staged approval work | **not ready** - no `docs/screens`, `docs/bff`, `docs/examples`, or `docs/pantheon-handoffs` artifacts found for approval queue |
| `GV-04 Deployment diff` | `GET /api/v1/deployment-plans/{plan_id}`; `GET /api/v1/operator/deployment-review/{plan_id}`; deployment service plan reads in `services/deployment/contract.md` | no composed diff route, no structured before/after snapshots, no semantic delta fields, no `meta.diff_computed_at`, and no backend-owned degraded behavior for missing prior plan state | **not ready** - no diff packet artifacts found |
| `GV-05 Rollback review` | `GET /api/v1/runtimes/{runtime_id}/rollbacks`; `GET /api/v1/rollbacks`; promotion review already establishes backend-owned CTA semantics | no rollback-review surface carrying position impact, affected bindings/personas, trigger reason, or `allowedActions.canApproveRollback`; raw rollback lists are not sufficient for operator review UI | **not ready** - no rollback-review packet artifacts found |
| `GV-06 Governance audit rail` | upstream `GET /api/governance/audit` exists and is canonical for governance audit events | no operator BFF projection such as `GET /api/v1/operator/governance/audit`; no filter contract for actor/action/target/date range in the shared BFF; no page-shaped evidence drawer payload | **not ready** - no audit-rail packet artifacts found |

### Key repo-backed distinctions

1. `GV-02` is closest to execution because the repo already has decision list/detail reads and a
   canonical governance write owner.
2. `GV-04` is blocked on a genuine composed diff model. The UI must not compute this from raw plan
   payloads.
3. `GV-05` is blocked even though rollback list routes exist, because review authority and position
   impact are still missing from the BFF layer.
4. `GV-06` is the clearest service-vs-BFF split: audit exists upstream, but not as a governance
   workbench operator surface.

---

## 5. Operator Journey and Frontend Handoff Notes

### 5.1 What a frontend consumer can safely use right now

| Surface | What exists now | Safe consumption rule |
|---|---|---|
| `GV-03 Promotion Review` | live BFF route, canonical contract, example payload, and frontend change spec | safe to use now as the only verified live Governance Workbench page-shaped read baseline in the current shared BFF |
| `GV-01 Review queue` | canonical contract/spec/example/handoff published | safe to use as packet and IA vocabulary, but treat runtime backing as **unverified in current `main.py`** until parent owner confirms or re-syncs it |
| `GV-02`, `GV-04`, `GV-05`, `GV-06` | no packet artifacts or frontend handoff files found | do not hand these to frontend implementation yet; they still need BFF/read-model work first |

### 5.2 Suggested operator journey sequencing for parent-owner absorption

1. Keep `GV-03 Promotion Review` as the live authority baseline for backend-shaped CTA gating.
2. Decide whether `GV-01 Review queue` should stay contract-only for now or be synchronized into
   the current shared BFF route inventory.
3. Build `GV-02 Approval queue` next, because it can reuse existing approval-decision objects and
   governance service writes once a queue projection exists.
4. Build `GV-04 Deployment diff` after approval-queue plan identity is stable.
5. Build `GV-05 Rollback review` after queue context and rollback authority shape are explicit.
6. Project `GET /api/governance/audit` into `GV-06 Governance audit rail` in parallel once the
   audit entry and filter shape are locked.

### 5.3 Concrete operator journeys implied by current repo state

**Promotion review journey (ready baseline)**

1. Load `GET /api/v1/operator/deployment-review/{plan_id}`.
2. Render the page from backend-shaped `deployment_plan`, `approval_decision`, `allowedActions`,
   `review`, and `meta.surfaces`.
3. Submit the decision through `POST /api/v1/operator/commands` with `ApproveDeployment`.

**Approval queue journey (blocked)**

1. Use `GET /api/v1/approval-decisions` only as a raw list/detail source.
2. Do not treat that raw list as a frontend-ready queue because it lacks queue projection and CTA
   authority extension.
3. Wait for a dedicated approval-queue BFF projection before creating the screen spec or UI handoff.

**Rollback review journey (blocked)**

1. Use rollback list routes only as raw evidence.
2. Do not construct a review surface client-side from `/api/v1/rollbacks` or
   `/api/v1/runtimes/{runtime_id}/rollbacks`.
3. Wait for a page-shaped rollback review payload with backend-owned approval gating.

**Audit rail journey (blocked at BFF projection)**

1. Governance audit history exists upstream at `GET /api/governance/audit`.
2. Do not wire the operator UI directly to the service contract and call that a Governance
   Workbench packet.
3. Wait for an operator BFF surface with the final audit filter and pagination shape.

---

## 6. Existing And Missing Handoff Materials

| Material type | Existing now | Missing now |
|---|---|---|
| Frontend change specs | `docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md`; `docs/pantheon-handoffs/PKT-001-governance-review-queue/FRONTEND_CHANGE_SPEC.md`; adjacent operator console pattern in `docs/pantheon-handoffs/PKT-001-deployment-review/FRONTEND_CHANGE_SPEC.md` | no approval-queue, deployment-diff, rollback-review, or audit-rail frontend change specs found |
| Screen specs | `docs/screens/F-042-promotion-review.md`; `docs/screens/PKT-001-governance-review-queue.md`; adjacent `docs/screens/PKT-001-deployment-review-console.md` | no approval-queue, deployment-diff, rollback-review, or audit-rail screen specs found |
| BFF contracts | `docs/bff/F-042-promotion-review.md`; `docs/bff/PKT-001-governance-review-queue.md`; adjacent `docs/bff/PKT-001-deployment-review-console.md` | no approval-queue, deployment-diff, rollback-review, or audit-rail BFF contracts found |
| Example payloads | `docs/examples/F-042-review-page.json`; `docs/examples/PKT-001-governance-review-queue.json`; adjacent `docs/examples/PKT-001-deployment-review-console.json` | no approval-queue, deployment-diff, rollback-review, or audit-rail example payloads found |

---

## 7. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only this sidecar file is created under `support/sidecars/BP5-WB-003/` |
| No canonical truth edited | PASS | References existing backlog, contracts, service docs, and BFF runtime files only |
| Packet distinguishes live routes from contract-only materials | PASS | `GV-01` vs `GV-03` split is explicitly called out |
| Follow-on BFF gaps are bounded to support scope | PASS | Approval queue, diff, rollback review, and audit rail are described without modifying parent or canonical docs |

---

## 8. Handoff To Reviewer (`Claude`)

This sidecar gives the parent owner one bounded BFF/frontend reality map for `BP5-WB-003`:

1. `GV-03 Promotion Review` is the only verified live page-shaped Governance Workbench baseline in
   the current shared BFF.
2. `GV-01 Review queue` is reusable as packet language and frontend contract vocabulary, but its
   shared-BFF implementation is not visible in the current `main.py` / `BFF_API_CONTRACT.md`.
3. `GV-02`, `GV-04`, `GV-05`, and `GV-06` still need explicit BFF/read-model work before frontend
   handoff is honest.
4. The audit rail is not blocked on upstream governance truth; it is blocked on projection into the
   operator BFF layer.

Recommended review stance:

- approve this sidecar if it is accurate as a support packet
- let the parent owner decide whether to absorb the `GV-01` consistency note into the mainline
  packetization plan
- keep all canonical decisions in the parent task, not in this sidecar
