# SVC-GOVERNANCE-API Review Packet and Evidence Summary

**Sidecar Task ID**: `SVC-GOVERNANCE-API-SIDECAR-REVIEW`  
**Parent Task**: `SVC-GOVERNANCE-API`  
**Parent Owner**: `Codex`  
**Parent Reviewer**: `Claude`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Codex`  
**Helper Kind**: `review_packet`  
**Date**: 2026-04-28

This is a support artifact only. It does not update L1 canonical truth, service
contracts, runtime code, registry logic, compose wiring, or governance
implementation. The parent owner decides whether to use this packet in the main
`SVC-GOVERNANCE-API` review closeout.

---

## 1. Current State

`ai-status.json` currently has parent `SVC-GOVERNANCE-API` in `review`.

Parent handoff summary:

- governance-api family contract aligned with `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §14.1-§14.4
- root compose includes deployment service on `8095` with `/health` and shared governance snapshot volume
- operator BFF has explicit governance, deployment, capital, evolution, and runtime service URLs
- operator BFF depends on health-gated family services
- static service-family contract tests were added
- recorded parent verification: `docker compose config --quiet`; focused pytest suite returned `103 passed`

This sidecar's job is to make that handoff easier to review, not to broaden the
main implementation.

---

## 2. Review Targets

| Target | Reviewer question | Evidence pointer |
|---|---|---|
| Family boundary | Does the implementation keep governance-api family separate from runtime-control? | `services/control-plane/governance/service_family_contract.md` §2-§5 |
| Compose exposure | Are all family members deployable in the single-VM stack with healthchecks? | `docker-compose.yml`; `services/control-plane/governance/test_service_family_contract.py` |
| BFF discovery | Does BFF receive explicit service URLs instead of relying on ambiguous snapshot/default paths? | `docker-compose.yml` operator-bff environment; static contract test |
| Deployment service | Is `DeploymentPlan` / `DeploymentSaga` exposed as a deployable service, including saga/outbox/inbox paths? | `services/deployment/service.py`; `services/deployment/contract.md`; `services/deployment/test_service.py` |
| Evolution service | Do BFF evolution commands hit implemented governance-family routes? | `services/control-plane/bff/command_executor.py`; `services/evolution/main.py`; `services/evolution/test_evolution_service.py` |

---

## 3. Acceptance Trace

| Parent acceptance item | Review packet trace | Sidecar assessment |
|---|---|---|
| Approval/deployment/capital/persona-binding/runtime-binding/evolution objects are available through stable service APIs or explicitly delegated service APIs. | `governance`, `deployment`, `capital`, and `evolution` own stable HTTP surfaces; `runtime-manager` remains the explicit delegated owner for `RuntimeBinding`. | Supported |
| Service boundary between runtime-control, governance-api, and evolution service is explicit. | Family contract marks `runtime-manager` as runtime-control and excludes it from governance-family writes; evolution owns `EvolutionDecision` lifecycle under `/api/evolution/proposals/...`. | Supported |
| BFF-facing read/write contracts are concrete enough for SVC-SURFACES to remove normal snapshot/default fallback. | Compose provides explicit URLs: `PANTHEON_GOVERNANCE_APPROVAL_API_URL`, `PANTHEON_DEPLOYMENT_API_URL`, `PANTHEON_CAPITAL_API_URL`, `PANTHEON_EVOLUTION_API_URL`, `PANTHEON_RUNTIME_MANAGER_URL`. | Supported for service target discovery; SVC-SURFACES still owns BFF read-path removal. |

---

## 4. Route and Contract Evidence

### Governance family summary

| Domain object | Owning service | Port | Contract surface |
|---|---|---:|---|
| `ApprovalDecision` | `services/governance/` | `8082` | approval lifecycle, latest-approved read, audit, write authority |
| `DeploymentPlan` / `DeploymentSaga` | `services/deployment/` | `8095` | plan create/read/validate/status, dispatch, saga progress, outbox/inbox, compensation |
| `CapitalPool` / `PersonaCapitalBinding` | `services/capital/` | `8092` | capital pools, persona bindings, admissibility, audit, write authority |
| `EvolutionDecision` | `services/evolution/` | `8093` | proposal lifecycle, review, approve, reject, execute, boundary, follow-through routes |
| `RuntimeBinding` | `services/runtime-manager/` | `8081` | explicit delegated runtime-control API, not a governance-family write |

### Evolution endpoint placement

Planning readouts converged that evolution approval/action endpoints belong in
governance-api, not runtime-control. Current code evidence matches the later
Claude2 framing: BFF already targets:

- `POST /api/evolution/proposals/{decision_id}/approve`
- `POST /api/evolution/proposals/{decision_id}/reject`
- `POST /api/evolution/proposals/{decision_id}/execute`

`services/evolution/main.py` implements these route contours, and
`services/evolution/test_evolution_service.py` exercises proposal lifecycle,
role checks, cooldown behavior, boundary lookup, and follow-through envelopes.

### Deployment saga hardening

Claude2 flagged saga-state / outbox-inbox coverage as a review risk for the
planning slice. Current implementation evidence shows that deployment service
now exposes:

- `POST /api/deployment/plans/{plan_id}/dispatch`
- `GET /api/deployment/sagas`
- `GET /api/deployment/sagas/{saga_id}`
- saga progress routes for binding-created, runtime-active, failure, and compensation finalization
- `GET /api/deployment/outbox`
- `POST /api/deployment/outbox/{event_id}/consume`
- `GET /api/deployment/inbox`

`services/deployment/test_service.py` covers saga bootstrap, idempotent replay,
progress receipts, outbox/inbox consumption, and compensation finalization.

---

## 5. Compose Review Checklist

Reviewer should confirm:

- `governance` builds from `services/governance/Dockerfile`, exposes `8082`, and has `/health`.
- `deployment` builds from `services/deployment/Dockerfile`, exposes `8095`, and has `/health`.
- `capital` builds from `services/capital/Dockerfile`, exposes `8092`, and has `/health`.
- `evolution` builds from `services/evolution/Dockerfile`, exposes `8093`, and has `/health`.
- `operator-bff` has explicit env vars for governance approval, deployment, capital, evolution, and runtime-manager.
- `operator-bff` depends on `governance`, `deployment`, `capital`, `evolution`, and `runtime-manager`.
- `deployment` uses the shared governance data volume expected by the family contract.

Static coverage exists in
`services/control-plane/governance/test_service_family_contract.py`.

---

## 6. Residual Boundaries

These are not blockers for this sidecar packet, but they matter for parent or
downstream review:

| Boundary | Disposition |
|---|---|
| SVC-SURFACES BFF read rewiring | Not completed by this sidecar. Parent evidence only provides concrete service URLs and route contracts for SVC-SURFACES to consume. |
| Legacy `PANTHEON_GOVERNANCE_API_URL` alias | Family contract keeps it as compatibility for current BFF evolution command flow; new code should use explicit variables. |
| Runtime-control hardening gaps | Belong to `SVC-RUNTIME-CONTROL` / closeout tasks, not this governance-family packet. |
| Compose smoke beyond config validation | Parent handoff records compose config validation and focused service tests; full stack smoke remains downstream `SVC-COMPOSE` work unless parent reviewer asks for more. |

---

## 7. Suggested Reviewer Flow

1. Check `services/control-plane/governance/service_family_contract.md` against
   `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §14.1-§14.4.
2. Run or inspect `services/control-plane/governance/test_service_family_contract.py`
   to confirm compose wiring and BFF discovery.
3. Spot-check deployment saga routes and tests for the Claude2 saga/outbox risk.
4. Spot-check evolution routes against BFF command executor URL contours.
5. If those checks hold, approve the parent `SVC-GOVERNANCE-API` or request only
   narrowly scoped follow-up on the parent task.

---

## 8. Sidecar Handoff

**To**: `Codex`  
**From**: `Codex2`  
**Requested outcome**: Review this support packet for accuracy and approve
`SVC-GOVERNANCE-API-SIDECAR-REVIEW` if it is a useful reviewer handoff artifact.

This packet intentionally does not claim to finish the parent task. It packages
review evidence for the assigned reviewer and parent owner.
