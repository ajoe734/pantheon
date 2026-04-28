# Governance-API Service Family — Family Contract

Last updated: 2026-04-28
Status: canonical family contract for SVC-GOVERNANCE-API
Tier: L1 Platform Architecture & Policy (binding)
Owner (this doc): Codex
Reviewer: Claude
Related L1 doc: `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §14.1–§14.4

---

## 1. Purpose

This is the single summary contract for the **governance-api family** — the four
deployable HTTP services that expose Pantheon's `ApprovalDecision`,
`DeploymentPlan` / `DeploymentSaga`, `PersonaCapitalBinding` / `CapitalPool`, and
`EvolutionDecision` domain objects.

It exists so that any cross-service or cross-plane edit can be reviewed against
one document instead of four. Per-service contracts remain authoritative for
internal route shapes; this file owns the **family-level** boundary, write
ownership, and integration rules.

---

## 2. Family Members

| Domain object | Service directory | Port | Per-service contract |
|---|---|---:|---|
| `ApprovalDecision` | `services/governance/` | `8082` | `services/governance/contract.md` |
| `DeploymentPlan` / `DeploymentSaga` | `services/deployment/` | `8095` | `services/deployment/contract.md` |
| `PersonaCapitalBinding` / `CapitalPool` | `services/capital/` | `8092` | `services/capital/contract.md` |
| `EvolutionDecision` | `services/evolution/` | `8093` | `services/control-plane/governance/evolution_decision.contract.md` |

Platform-object source (Python + JSON schema) for all four objects lives in
`services/control-plane/governance/`. Each deployable service wraps these
objects with a FastAPI surface, file-backed persistence, and an audit log.

---

## 3. Service Boundary with Runtime-Control

`runtime-manager` (`services/runtime-manager/`, port `8081`) is the
**runtime-control plane**. It owns operator command dispatch, kill-switch and
safe-mode, and `RuntimeBinding` writes.

The governance-api family and runtime-control plane are explicitly disjoint:

| Boundary | governance-api family | runtime-control (`runtime-manager`) |
|---|---|---|
| Writes `ApprovalDecision` | yes (governance) | no |
| Writes `DeploymentPlan` / `DeploymentSaga` | yes (deployment) | no |
| Writes `PersonaCapitalBinding` / `CapitalPool` | yes (capital) | no |
| Writes `EvolutionDecision` | yes (evolution) | no |
| Writes `RuntimeBinding` | no | yes |
| Owns kill-switch / safe-mode state | no | yes |
| Owns operator command queue / audit | no | yes |
| Reads governance objects to validate runtime work | n/a | yes (read-only HTTP) |

Cross-plane interaction is one-directional and read-shaped:

- `runtime-manager` reads `services/capital/` `/api/bindings/admissibility`
  before creating a `RuntimeBinding`.
- `runtime-manager` reads `services/deployment/` plan / saga state when
  deploying.
- `runtime-manager` reads `services/governance/` `latest-approved` when
  enforcing approval gates.
- governance-api family services do not call into `runtime-manager`. They emit
  outbox events (DEP-002) that `runtime-manager` consumes.

---

## 4. Write Authority Matrix (family-level)

| Mutation | Owning service | Authority document |
|---|---|---|
| Propose / review / decide / revoke an `ApprovalDecision` | `services/governance/` | `services/governance/write_authority.py` |
| Create / validate / dispatch a `DeploymentPlan`; saga lifecycle | `services/deployment/` | `services/control-plane/governance/deployment_saga.contract.md` |
| Create pool / binding; activate; mutate `allowed_deployment_scope` | `services/capital/` | `services/capital/write_authority.py` |
| Propose / review / approve / execute / followthrough an `EvolutionDecision` | `services/evolution/` | `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §11.1 |
| Create / replace / retire `RuntimeBinding`; kill-switch | `services/runtime-manager/` | `services/execution/runtime-manager/contract.md` (RUN-001) |

**Hard rule:** No service in the family writes a domain object owned by another
service. Cross-service writes always travel through the owning service's HTTP
API.

---

## 5. Read Path Discovery (BFF and other consumers)

The family is discoverable through these env vars; consumers must prefer them
over snapshot or default-seed read paths:

| Env var | Container URL |
|---|---|
| `PANTHEON_GOVERNANCE_APPROVAL_API_URL` | `http://governance:8082` |
| `PANTHEON_DEPLOYMENT_API_URL` | `http://deployment:8095` |
| `PANTHEON_CAPITAL_API_URL` | `http://capital:8092` |
| `PANTHEON_EVOLUTION_API_URL` | `http://evolution:8093` |
| `PANTHEON_INTERNAL_API_URL` / `PANTHEON_RUNTIME_MANAGER_URL` | `http://runtime-manager:8081` |

Legacy variable `PANTHEON_GOVERNANCE_API_URL` is retained pointing at evolution
for backward compatibility with the BFF `command_executor` evolution-proposal
flow accepted under SVC-RUNTIME-CONTROL. New code must use the explicit names
above. The legacy alias is scheduled to be retired by SVC-SURFACES once the BFF
read path is rewired.

---

## 6. Integration Invariants

1. `DeploymentPlan` creation requires an `ApprovalDecision` in `decided` /
   `approved` state with matching `target_id` / `target_version`. The
   deployment service resolves this via `services/governance/`'s
   `latest-approved` endpoint or the shared `approval_decisions.json` snapshot.
2. `DeploymentPlan.target_stage` must satisfy
   `PersonaCapitalBinding.allowed_deployment_scope >= target_stage` for the
   binding identified by the plan. Capital service is the source of truth.
3. `EvolutionDecision.execute()` mutates evolution state but does not write
   `RuntimeBinding`. Runtime follow-throughs (rollback, redeploy) emit a
   dispatch command that `runtime-manager` consumes.
4. Audit trail edges:
   - `governance` → `governance_audit` (`/api/governance/audit`)
   - `capital` → `capital_audit` (`/api/capital/audit`)
   - `deployment` → outbox/inbox tables (`/api/deployment/outbox`,
     `/api/deployment/inbox`)
   - `evolution` → review chain inside each decision document
   - `runtime-manager` → command-state file + kill-switch snapshot

---

## 7. Health Surfaces

All four governance-family services expose `GET /health` returning
`{"status": "ok", "service": "<name>"}`. `runtime-manager` exposes
`GET /__health__`. The single-VM compose stack uses these for healthchecks.

---

## 8. Acceptance Criteria for SVC-GOVERNANCE-API

- [x] Approval, deployment, binding, and evolution objects are each available
      through a stable, deployable HTTP service on a published port.
- [x] All four services are wired into `docker-compose.yml` with healthchecks
      and depend on shared infrastructure.
- [x] Operator BFF receives explicit env vars for each family member URL plus a
      runtime-manager URL, so the SVC-SURFACES rewiring has a stable target.
- [x] The service boundary between runtime-control (`runtime-manager`) and the
      governance-api family is documented in this file and in
      `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §14.1–§14.4.

---

## 9. Change Procedure

When a future task adds, removes, or changes a family member:

1. Update §2, §3, §4, §5 of this document first.
2. Update `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §14.1–§14.4 to match.
3. Update the per-service `contract.md`.
4. Update `docker-compose.yml` and the SVC-BASELINE port table in
   `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md`.
5. Add or refresh tests against the new boundary.

A change that updates per-service contracts but skips this file is rejected at
review.
