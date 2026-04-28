# SVC-RUNTIME-HARDENING BFF and Frontend Handoff Packet

**Sidecar Task ID**: `SVC-RUNTIME-HARDENING-SIDECAR-BFF-HANDOFF`  
**Parent Task**: `SVC-RUNTIME-HARDENING`  
**Parent Owner**: `Claude`  
**Parent Reviewer**: `Gemini`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-04-28  
**Last Refresh**: 2026-04-28T15:19:36Z  
**Mutates Canonical**: `no`

This is a support artifact only. It does not update canonical truth, L1 policy,
core contracts, runtime-manager behavior, registry logic, governance
implementation, BFF implementation, frontend code, or compose wiring. The
parent owner decides whether and how to absorb this packet into
`SVC-RUNTIME-HARDENING`.

---

## 1. Parent Scope Snapshot

`SVC-RUNTIME-HARDENING` is the production-hardening follow-up created from the
runtime-control closeout caveats. Its implementation scope is not a new
frontend feature; it is the backend/control-plane work needed to make the
existing BFF command path production-honest:

- protected runtime and internal command routes validate JWT claims, RBAC, and
  MFA policy;
- legacy internal API kill-switch and command paths converge with the durable
  foundation idempotency record path, or otherwise prove no divergent replay
  side effects;
- `ApproveDeployment` no longer creates placeholder approval ids and instead
  calls the authoritative governance or deployment API;
- focused tests cover auth denial, MFA denial, idempotent replay, and approval
  integration.

Frontend-facing disposition: the browser should continue to call the BFF. This
hardening slice should not introduce browser direct calls to runtime-manager,
internal API, governance, deployment, or evolution services.

---

## 2. Current BFF/Runtime Snapshot

| Area | Current fact | Evidence |
|---|---|---|
| Browser write entrypoint | BFF accepts operator commands at `POST /api/v1/operator/commands` and exposes status at `GET /api/v1/operator/commands/{command_id}`. | `services/control-plane/bff/main.py` |
| BFF command routing | `command_executor.py` dispatches runtime commands to `PANTHEON_INTERNAL_API_URL` and evolution/governance commands to `PANTHEON_GOVERNANCE_API_URL` / `PANTHEON_EVOLUTION_API_URL`. | `services/control-plane/bff/command_executor.py` |
| Compose backend wiring | Root compose points `operator-bff` at `runtime-manager:8081` for internal/runtime-manager paths and at `evolution:8093` for governance/evolution paths. | `docker-compose.yml` |
| BFF auth scaffold | BFF currently accepts a stub bearer token format and propagates the raw token plus optional `X-MFA-Token` to downstream command execution. | `services/control-plane/bff/main.py` |
| Command idempotency scaffold | BFF admission creates a foundation command envelope and idempotency record, then stores command status in the BFF command store. | `services/control-plane/bff/main.py`, `services/control-plane/bff/command_queue.py` |
| Secondary control guidance | BFF exposes `GET /api/v1/operator/degraded-control-guidance` with admin CLI/protected internal API guidance. | `services/control-plane/bff/main.py`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` |
| Deployment approval caveat | Legacy internal deployment approval still synthesizes `ad-{plan_id}-{timestamp}` and returns a placeholder approval result. | `services/control_plane/internal_api.py` |

---

## 3. BFF Query Gap Matrix

| Hardening topic | Current BFF/frontend surface | Parent implementation gap | Frontend implication |
|---|---|---|---|
| JWT/RBAC/MFA enforcement | Frontend sends `Authorization` and, for high-risk actions, may send `X-MFA-Token` to BFF. BFF validates stub roles/MFA and propagates token material downstream. | Runtime-manager/internal command routes need real token claim, role, audience/issuer, and MFA policy validation. | Preserve the existing headers and command body shape. Do not add client-side role decisions beyond hiding or disabling CTAs from backend-owned authority metadata. |
| MFA denial semantics | BFF already returns structured BFF errors for its own validation, while downstream denial shapes may vary by service. | Parent should align downstream 401/403/MFA-denied responses into BFF command status/error surfaces. | UI should render command receipt/status failures exactly as returned and avoid converting auth denial into a generic network failure. |
| Idempotency convergence | BFF admission deduplicates by foundation idempotency record. Direct legacy internal API command paths can still maintain their own command records. | Parent should converge legacy internal API kill-switch/runtime side effects with the durable foundation idempotency path or prove no divergent replay. | Keep sending `X-Idempotency-Key` where available. Poll the BFF command id for BFF-submitted commands; use internal command lookup only for explicit admin fallback flows. |
| ApproveDeployment authority | BFF routes `ApproveDeployment` to `/api/internal/v1/deployments/{plan_id}/approve`, and the legacy internal API currently returns a placeholder approval id. | Parent must replace placeholder approval creation with authoritative governance/deployment API integration. | Approval screens should not trust an `ad-{plan_id}-{timestamp}` pattern as proof of real governance approval. Treat the backend receipt/result as authoritative only after this parent gap is closed. |
| Fallback path authority | `degraded-control-guidance` advertises admin CLI/internal API fallback when BFF/read surfaces degrade. | Parent hardening should ensure fallback paths enforce the same or stricter auth/MFA/idempotency policy as BFF. | UI can display fallback guidance, but must not auto-submit fallback commands or browser-call protected services. |
| Command receipt routing | BFF command receipts currently report `routing_path: direct` for BFF-admitted commands. | If parent adds fallback or delegated routing, BFF should explicitly report it in receipt/status metadata. | Frontend should show routing metadata only when returned; do not infer hidden fallback routing. |

---

## 4. Operator Journey Handoff

### 4.1 Normal Hardened Command Journey

1. Operator enters the console through BFF-backed screens.
2. UI reads runtime/health/incident context from BFF read surfaces such as
   `/api/v1/operator/runtime-state`, `/api/v1/operator/health-status`,
   `/api/v1/operator/incident-response/{incident_id}`, and
   `/api/v1/kill-switch/status`.
3. UI derives available CTAs from backend-owned command authority and
   `allowedActions`; it does not manufacture target ids or authority locally.
4. Operator submits the command through `POST /api/v1/operator/commands` with:
   `Authorization`, optional `X-MFA-Token`, optional trace/correlation/request
   headers, optional `X-Idempotency-Key`, and audit context.
5. UI renders the BFF receipt and polls
   `GET /api/v1/operator/commands/{command_id}`.
6. BFF dispatches to the hardened backend authority. Runtime commands remain
   backend-routed; governance/deployment approval commands must terminate in the
   authoritative governance/deployment service, not placeholder approval ids.
7. UI refreshes the owning read surface after completion rather than locally
   deriving runtime, kill-switch, deployment, or approval state transitions.

### 4.2 Auth or MFA Denial Journey

1. If BFF rejects the command before dispatch, render the structured BFF error.
2. If downstream auth/MFA policy rejects the command after acceptance, render the
   command status failure from `GET /api/v1/operator/commands/{command_id}`.
3. Keep the original receipt, command id, trace/correlation ids, and audit reason
   visible for support and replay investigation.
4. Disable retry until the operator refreshes authority metadata or reauthenticates
   with the required role/MFA context.

### 4.3 Degraded or BFF-Outage Journey

1. If BFF is reachable but read surfaces are degraded, keep degraded/unavailable
   metadata visible and disable dependent CTAs.
2. Read `GET /api/v1/operator/degraded-control-guidance` for secondary control
   path instructions.
3. If BFF is unavailable, high-privilege operators use the non-BFF path defined
   by BFF HA policy: admin CLI, protected internal API, or runtime-manager
   protected admin endpoint.
4. Browser code must not call runtime-manager or protected internal APIs as a
   hidden fallback.

---

## 5. Frontend Handoff Materials

This sidecar does not create a new Lovable task or a new frontend implementation
request. Use the existing BFF/frontend materials, with the hardening notes below.

| Screen / flow | Existing material | Hardening note |
|---|---|---|
| Incident Action Drawer | `docs/bff/PKT-002-incident-action-drawer.md`, `docs/pantheon-delivery/PKT-002-incident-action-drawer/CONTRACT_LOCK.md` | Preserve BFF command submission and MFA/idempotency headers for high-risk actions. |
| Runtime State Board | `docs/bff/PKT-010-runtime-state-board.md`, `docs/examples/PKT-010-runtime-state-board.json`, `docs/screens/PKT-010-runtime-state-board.md` | Treat missing runtime authority as unavailable/degraded, not as permission to synthesize target context. |
| Health Status Board | `docs/bff/PKT-011-health-status-board.md`, `docs/screens/PKT-011-health-status-board.md` | Display secondary-control-path posture from BFF; do not create direct browser fallback calls. |
| Alerts Rail / Operator Home | `docs/bff/PKT-012-alerts-rail.md`, `docs/bff/PKT-013-operator-home.md` | Link into owning BFF surfaces and respect backend-owned command authority. |
| Deployment / Approval Review | `services/control-plane/bff/APP_001C_QUERY_CONTRACT_OUTLINE.md`, `docs/bff/PKT-001-deployment-review-console.md` where present | Do not treat placeholder approval ids as production proof. Parent hardening must close the authoritative approval gap. |
| Mutation / Evolution Review | `docs/bff/EW-05-mutation-review.md` | Continue routing approve/reject/execute through BFF command submission and configured governance/evolution backend. |

Frontend implementation constraints:

- keep the existing BFF client as the browser-facing integration point;
- preserve `Authorization`, `X-MFA-Token`, trace/correlation/request headers, and
  `X-Idempotency-Key` when available;
- render command receipt and command-status errors exactly as backend returns
  them;
- do not infer approval authority from id format, UI role labels, or local
  fixture data;
- keep backend-owned degraded/unavailable copy visible and disable dependent
  CTAs when authority or target state is unverifiable;
- do not add direct browser calls to `/api/internal/v1/...`, runtime-manager,
  governance, deployment, or evolution services.

---

## 6. Minimal Smoke Requests for Frontend QA

```http
GET /health
Authorization: Bearer op-admin:admin:mfa
```

```http
GET /api/v1/operator/degraded-control-guidance
Authorization: Bearer op-admin:admin:mfa
```

```http
GET /api/v1/kill-switch/status
Authorization: Bearer op-admin:admin:mfa
```

```http
POST /api/v1/operator/commands
Authorization: Bearer op-admin:admin:mfa
X-MFA-Token: 000000
X-Idempotency-Key: qa-hardening-safe-mode-001
X-Trace-Id: trace-qa-hardening-001
Content-Type: application/json

{
  "command": "IssueSafeMode",
  "target": { "type": "Runtime", "id": "runtime-042" },
  "params": { "safe_mode_level": "soft" },
  "audit_context": { "reason": "QA validation of hardened command receipt flow" }
}
```

```http
GET /api/v1/operator/commands/{command_id}
Authorization: Bearer op-admin:admin:mfa
```

```http
POST /api/v1/operator/commands
Authorization: Bearer op-approver:approver:mfa
X-MFA-Token: 000000
X-Idempotency-Key: qa-hardening-approve-deployment-001
Content-Type: application/json

{
  "command": "ApproveDeployment",
  "target": { "type": "DeploymentPlan", "id": "plan-042" },
  "params": {
    "deployment_plan_id": "plan-042",
    "approval_decision": "approve"
  },
  "audit_context": { "reason": "QA validation of authoritative approval routing" }
}
```

The sample write requests are for QA shape validation only. Use fixture ids that
exist in the target environment. The deployment approval sample should not be
treated as production-credible until the parent closes the placeholder approval
gap.

---

## 7. Suggested Parent Verification Focus

| Verification target | Suggested evidence |
|---|---|
| BFF preserves auth/MFA/idempotency metadata | BFF command admission tests asserting propagated `Authorization`, `X-MFA-Token`, trace/correlation/request, and idempotency context. |
| Downstream auth denial is operator-visible | Tests where runtime-manager/internal API returns 401/403/MFA-denied and BFF command status records a precise failure. |
| BFF idempotent replay stays stable | Repeat `POST /api/v1/operator/commands` with the same `X-Idempotency-Key` and same payload; assert the same command receipt is returned. |
| Idempotency conflict is rejected | Repeat the same key with a changed payload; assert conflict and no second side effect. |
| Legacy internal API replay is converged or proven safe | Direct internal kill-switch/runtime command replay tests against the parent-selected durable foundation path or no-divergent-side-effect proof. |
| ApproveDeployment is authoritative | Tests prove the command calls governance/deployment authority and no longer synthesizes `ad-{plan_id}-{timestamp}` placeholders. |
| Frontend-facing denial shape is stable | Contract tests cover BFF receipt/status payloads for auth denial, MFA denial, backend unavailable, and approval-authority denial. |

This sidecar did not run implementation tests because it did not modify runtime,
BFF, governance, deployment, or frontend code.

---

## 8. Non-Claims

This packet does not claim:

| Non-claim | Correct owner / follow-up |
|---|---|
| Production-grade JWT, RBAC, MFA, issuer/audience, and claims validation are already implemented. | `SVC-RUNTIME-HARDENING` parent implementation. |
| Legacy internal API command replay already uses one durable foundation idempotency ledger. | `SVC-RUNTIME-HARDENING` parent implementation. |
| Deployment approval already terminates in authoritative governance/deployment service state. | `SVC-RUNTIME-HARDENING` parent implementation. |
| Browser code should call runtime-manager, internal API, governance, deployment, or evolution services directly. | Explicitly out of scope; browser-facing path remains BFF. |
| BFF HA topology, multi-replica deployment, or LB placement is closed by this packet. | BFF HA/deployment hardening and compose/infra owners. |
| Default-stack boot or compose smoke is proven by this sidecar. | `SVC-COMPOSE` / parent verification. |

---

## 9. Reviewer Focus for Claude

1. Confirm this packet is support-only and does not promote new canonical
   runtime-control, governance, deployment, or BFF truth.
2. Confirm the frontend handoff keeps BFF as the only browser-facing write
   surface and does not introduce direct runtime-manager/internal API calls.
3. Confirm the query gaps align with `SVC-RUNTIME-HARDENING` acceptance:
   auth/RBAC/MFA, idempotency convergence, and authoritative deployment
   approval.
4. Confirm the operator journey separates normal BFF command flow, downstream
   auth/MFA denial, and degraded/admin fallback flow.
5. Confirm the suggested verification focus is useful to the parent owner but
   does not claim implementation proof from this sidecar.

Recommended disposition: approve this sidecar as support material, then let the
parent owner decide whether any section should be folded into the main
hardening implementation plan or downstream frontend handoff.
