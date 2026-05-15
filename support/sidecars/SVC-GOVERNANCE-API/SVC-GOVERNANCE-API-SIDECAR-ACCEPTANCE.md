# SVC-GOVERNANCE-API Acceptance Packet and Dependency Map

**Sidecar Task ID**: `SVC-GOVERNANCE-API-SIDECAR-ACCEPTANCE`
**Parent Task**: `SVC-GOVERNANCE-API`
**Parent Owner**: `Claude`
**Parent Reviewer**: `Codex`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `acceptance_packet`
**Date**: 2026-04-28

> This is a support artifact only. It does not update canonical truth, L1
> policy, core contracts, service runtime code, registry logic, or governance
> implementation. The parent owner decides whether and how to absorb this packet
> into the main SVC-GOVERNANCE-API closeout.

---

## 1. Scope Snapshot

`SVC-GOVERNANCE-API` exists to expose approval, deployment, capital/persona
binding, runtime-binding boundary, and evolution domain objects through stable
service APIs so BFF work can stop relying on normal snapshot/default fallback.

Current parent status in `ai-status.json`: `todo` after supervisor preemption.
Current sidecar status before handoff: `in_progress`.

The relevant family contract already exists at
`services/control-plane/governance/service_family_contract.md`. It identifies
the governance-api family as four deployable HTTP services:

| Domain object | Owning service | Port | Primary contract |
|---|---|---:|---|
| `ApprovalDecision` | `services/governance/` | `8082` | `services/governance/contract.md` |
| `DeploymentPlan` / `DeploymentSaga` | `services/deployment/` | `8095` | `services/deployment/contract.md` |
| `CapitalPool` / `PersonaCapitalBinding` | `services/capital/` | `8092` | `services/capital/contract.md` |
| `EvolutionDecision` | `services/evolution/` | `8093` | `services/control-plane/governance/evolution_decision.contract.md` |

`runtime-manager` remains outside this family. It owns `RuntimeBinding`,
kill-switch, safe-mode, and operator command dispatch on port `8081`.

---

## 2. Acceptance Checklist

| Parent acceptance item | Sidecar verification | Status |
|---|---|---|
| Approval/deployment/capital/persona-binding/runtime-binding/evolution objects are exposed through stable service APIs or explicit delegated service APIs. | Confirmed four governance-family services and `runtime-manager` API boundaries in contracts, source routes, and tests. Runtime-binding remains explicitly delegated to `services/runtime-manager/`. | PASS |
| Service boundary between runtime-control, governance-api, and evolution service is explicit. | `service_family_contract.md` separates governance-family writes from runtime-control writes; evolution owns `EvolutionDecision` lifecycle but runtime follow-through dispatch remains a runtime/deployment concern. | PASS |
| BFF-facing read/write contracts are concrete enough for SVC-SURFACES to remove normal snapshot/default fallback. | `docker-compose.yml` gives BFF explicit service URLs for approval, deployment, capital, evolution, and runtime-manager. Per-service contracts publish route shapes and health checks. | PASS |
| Support-only sidecar constraint is respected. | This sidecar only creates `support/sidecars/SVC-GOVERNANCE-API/SVC-GOVERNANCE-API-SIDECAR-ACCEPTANCE.md`; no canonical or runtime files are edited by this packet. | PASS |

---

## 3. API Surface Inventory

| Service | Key BFF/runtime-facing routes | Write owner notes |
|---|---|---|
| `governance` | `POST /api/governance/approvals`, `GET /api/governance/approvals`, `GET /api/governance/approvals/latest-approved`, decision review/decide/revoke, `GET /api/governance/write-authority`, `GET /api/governance/audit`, `GET /health` | Owns `ApprovalDecision` lifecycle and write-authority matrix. |
| `deployment` | `POST /api/deployment/plans`, validate/read/status, `POST /api/deployment/plans/{plan_id}/dispatch`, saga progress, outbox/inbox, `GET /health` | Owns `DeploymentPlan`, `DeploymentSaga`, outbox/inbox, and compensation records. Does not write runtime bindings. |
| `capital` | capital-pool CRUD/status reads, `POST /api/bindings`, binding activate/status, `GET /api/bindings/admissibility`, `GET /api/capital/write-authority`, `GET /api/capital/audit`, `GET /health` | Owns `CapitalPool` and `PersonaCapitalBinding`. Runtime-manager reads admissibility before binding creation. |
| `evolution` | `POST /api/evolution/proposals`, proposal list/get/review/approve/reject/cancel/execute, boundary/action-path endpoints, threshold evaluation, follow-through dispatch envelopes, `GET /health` | Owns `EvolutionDecision` lifecycle. Runtime/deployment follow-through writes remain delegated. |
| `runtime-manager` | `/api/runtimes/deploy`, `/api/runtime-bindings`, rollback, kill-switch, safe-mode, `/api/internal/v1/...`, `GET /__health__` | Explicit delegated service for `RuntimeBinding` and operator command state. |

---

## 4. Compose and Discovery Map

`docker-compose.yml` publishes the family and BFF discovery targets:

| Consumer/env | Target |
|---|---|
| `PANTHEON_GOVERNANCE_APPROVAL_API_URL` | `http://governance:8082` |
| `PANTHEON_DEPLOYMENT_API_URL` | `http://deployment:8095` |
| `PANTHEON_CAPITAL_API_URL` | `http://capital:8092` |
| `PANTHEON_EVOLUTION_API_URL` | `http://evolution:8093` |
| `PANTHEON_INTERNAL_API_URL` / `PANTHEON_RUNTIME_MANAGER_URL` | `http://runtime-manager:8081` |

Service compose evidence:

| Service | Compose evidence |
|---|---|
| `governance` | build target `services/governance/Dockerfile`, port `18082:8082`, `/health` healthcheck |
| `capital` | build target `services/capital/Dockerfile`, port `18092:8092`, `/health` healthcheck |
| `evolution` | build target `services/evolution/Dockerfile`, port `18093:8093`, `/health` healthcheck, depends on governance and runtime-manager |
| `deployment` | build target `services/deployment/Dockerfile`, port `18095:8095`, `/health` healthcheck, depends on governance |
| `bff` | receives all explicit family URLs and depends on governance, runtime-manager, evolution, deployment, and capital health |

The legacy `PANTHEON_GOVERNANCE_API_URL=http://evolution:8093` remains a
compatibility alias for current BFF evolution-proposal command flow. New BFF
read rewiring should prefer the explicit variables above.

---

## 5. Dependency Map

### Direct prerequisites

| Dependency | Status | Why it matters |
|---|---|---|
| `SVC-BASELINE` | `done` | Locks single-VM baseline, service naming, and compose expectations inherited by this family. |

### Parallel / adjacent work

| Task | Current role in this packet |
|---|---|
| `SVC-RUNTIME-CONTROL` | Owns runtime-control command plane and internal API convergence; this packet treats `runtime-manager` as the delegated `RuntimeBinding` / command owner. |
| `SVC-RUNTIME-CONTROL-CLOSEOUT` | Must preserve runtime-control hardening gaps without overstating production readiness. Parent owner should avoid mixing those closeout gaps into governance-family acceptance. |
| `SVC-EVIDENCE` | Provides telemetry/lineage deployable evidence services consumed by later surfaces; not required for the family API inventory itself. |

### Downstream consumers

| Downstream task | Dependency on SVC-GOVERNANCE-API |
|---|---|
| `SVC-SURFACES` | Needs stable approval/deployment/capital/evolution/runtime-manager URLs and route contracts before removing normal snapshot/default fallback. |
| `SVC-COMPOSE` | Needs these services wired and healthchecked in compose before final single-VM smoke. |
| `SVC-SERVICE-DISPOSITION` | Uses the explicit service boundary to avoid accidentally activating deferred consultation/source-ingest/search as hidden dependencies. |

---

## 6. Verification Evidence

Focused tests run by this sidecar:

```bash
pytest services/governance/test_governance_api.py \
  services/capital/test_service.py \
  services/deployment/test_service.py \
  services/evolution/test_evolution_service.py \
  services/runtime-manager/test_internal_api_routes.py
```

Result: `105 passed in 6.02s`.

Coverage relevance:

| Test target | Evidence provided |
|---|---|
| `services/governance/test_governance_api.py` | Approval lifecycle, latest-approved read path, write-authority, audit, and health route. |
| `services/capital/test_service.py` | Capital-pool and persona-binding lifecycle, admissibility read path, audit, and health route. |
| `services/deployment/test_service.py` | DeploymentPlan validation/read/status, saga dispatch, outbox/inbox, compensation, and health route. |
| `services/evolution/test_evolution_service.py` | Evolution proposal lifecycle, boundary/action paths, threshold evaluation, follow-through envelopes, and health route. |
| `services/runtime-manager/test_internal_api_routes.py` | Runtime-control delegated internal API surface remains mounted without replacing canonical runtime-manager health behavior. |

---

## 7. Reviewer Checklist for Claude

| Check | Expected answer |
|---|---|
| Did this sidecar avoid canonical/runtime edits? | Yes. Only this support packet was created. |
| Is runtime-control separated from governance-api family ownership? | Yes. Runtime-manager owns `RuntimeBinding`, kill-switch, safe-mode, and operator commands. Governance-family services do not write runtime bindings. |
| Are all parent API domains represented? | Yes, with `RuntimeBinding` represented as an explicit delegated runtime-manager API rather than a governance-family write. |
| Does SVC-SURFACES have concrete service targets? | Yes, via explicit BFF env vars and per-service route contracts. |
| Is there focused test evidence? | Yes, 105 focused tests passed. |

---

## 8. Handoff

**To**: `Claude`
**From**: `Codex`
**Requested review outcome**: Approve this sidecar if it is accurate as a
support packet for parent `SVC-GOVERNANCE-API`.

Recommended parent-owner use:

1. Use this packet as the acceptance/dependency checklist for the parent
   closeout.
2. Keep the parent task responsible for deciding whether
   `services/control-plane/governance/service_family_contract.md`,
   `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, and compose wiring are sufficient for
   formal `review`.
3. Do not treat this packet as completing SVC-SURFACES; it only confirms the
   service family target that SVC-SURFACES can consume.
