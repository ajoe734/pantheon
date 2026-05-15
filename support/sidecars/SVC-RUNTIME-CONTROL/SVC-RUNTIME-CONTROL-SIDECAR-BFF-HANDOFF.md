# SVC-RUNTIME-CONTROL BFF and Frontend Handoff Packet

**Sidecar Task ID**: `SVC-RUNTIME-CONTROL-SIDECAR-BFF-HANDOFF`
**Parent Task**: `SVC-RUNTIME-CONTROL`
**Parent Owner**: `Claude`
**Parent Reviewer**: `Gemini`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-28
**Last Refresh**: 2026-04-28T14:34:29Z
**Mutates Canonical**: `no`

This is a support artifact only. It does not update canonical truth, L1 policy,
core contracts, runtime-manager behavior, registry logic, governance
implementation, BFF implementation, frontend code, or compose wiring. The
parent owner decides whether and how to absorb this packet into the main
runtime-control closeout.

---

## 1. Scope Snapshot

`SVC-RUNTIME-CONTROL` has been reviewed and is currently `review_approved`.
The implementation converges the legacy operator-facing
`/api/internal/v1/...` command surface onto the deployable `runtime-manager`
Flask process while keeping frontend write authority on the existing BFF route:

- Frontend write surface: `POST /api/v1/operator/commands`
- Frontend command status: `GET /api/v1/operator/commands/{command_id}`
- BFF command executor runtime backend: `PANTHEON_INTERNAL_API_URL=http://runtime-manager:8081`
- BFF runtime-manager client backend: `PANTHEON_RUNTIME_MANAGER_URL=http://runtime-manager:8081`
- Evolution approve/reject/execute backend: `PANTHEON_GOVERNANCE_API_URL=http://evolution:8093`

The practical handoff is therefore not "frontend must call runtime-manager
directly." It is "frontend keeps calling BFF; BFF now has a deployable
runtime-manager backend for the legacy internal command paths."

---

## 2. Current Implementation Snapshot

| Area | Current fact | Evidence |
|---|---|---|
| Runtime-control service | `runtime-manager` exposes `/__health__` on container port `8081` and host port `18081`. | `services/runtime-manager/main.py`, `docker-compose.yml` |
| Legacy command routes | `services/runtime-manager/internal_api_routes.py` mounts legacy `/api/internal/v1/...` routes on the runtime-manager app and backs pause/rollback with the same in-process `RuntimeManagerService`. | `services/runtime-manager/internal_api_routes.py` |
| Kill-switch coherence | Legacy `/api/internal/v1/kill-switch` shares runtime-manager kill-switch state and persists safe-mode changes after legacy dispatch. | `services/runtime-manager/internal_api_routes.py`, `services/runtime-manager/test_internal_api_routes.py` |
| BFF command admission | BFF accepts governed commands at `POST /api/v1/operator/commands`, records foundation/idempotency/audit context, queues async processing, and exposes `GET /api/v1/operator/commands/{command_id}`. | `services/control-plane/bff/main.py`, `services/control-plane/bff/command_queue.py` |
| Runtime command executor | BFF dispatches pause, rollback, approval, kill-switch, and safe-mode commands to configured internal/runtime-manager paths. | `services/control-plane/bff/command_executor.py` |
| Evolution executor | BFF dispatches evolution approve/reject/execute and mutation approve/reject to the governance/evolution service URL, not to local placeholders. | `services/control-plane/bff/command_executor.py`, `docker-compose.yml` |
| Frontend route convergence | The sibling frontend repo uses BFF helpers for `POST /api/v1/operator/commands`, `GET /api/v1/operator/runtime-state`, and `GET /api/v1/kill-switch/status`. | `../front-ai-trading-system/src/lib/bffClient.ts` |

---

## 3. BFF Query Gap Matrix

| Topic | Current state | Frontend implication | Disposition |
|---|---|---|---|
| Runtime command backend naming | BFF still uses `PANTHEON_INTERNAL_API_URL`, but compose points it at `runtime-manager:8081`. | Frontend should not care about the env name; it still submits to BFF. | No frontend gap. Documentation/runbooks should avoid implying a separate default `control-plane-internal` service. |
| Command status split | BFF-submitted commands are tracked in BFF `CommandStore`; direct fallback/internal API commands are tracked by the runtime-manager-mounted legacy command state file. | Poll the BFF command id with `GET /api/v1/operator/commands/{command_id}` after BFF submission. Use `/api/internal/v1/commands/{command_id}` only for direct fallback/internal API flows. | Hardening/reconciliation concern, not a new frontend route. |
| Drawer runtime commands | `PauseExecution`, `IssueRiskOff`, `LiquidateAll`, `HardRollback`, and `IssueSafeMode` derive binding/pool context from BFF read surfaces before dispatch. | If runtime binding or capital pool context is unavailable, show the returned command failure and do not invent pool/binding ids client-side. | Frontend must preserve backend-owned target context. |
| Secondary control path receipt | `degraded-control-guidance` advertises admin CLI/internal API fallback. The normal BFF receipt projection currently returns `routing_path: direct` for BFF-admitted commands. | UI can show fallback guidance from `GET /api/v1/operator/degraded-control-guidance`, but should not claim the BFF auto-submitted a fallback command unless the receipt explicitly says so. | Residual BFF contract alignment gap for a later hardening slice. |
| Evolution actions | Evolution approve/reject/execute go to `evolution:8093` through `PANTHEON_GOVERNANCE_API_URL` / `PANTHEON_EVOLUTION_API_URL`. | Mutation/evolution screens continue to use `POST /api/v1/operator/commands`; do not add runtime-manager direct calls for evolution approval/action. | Closed for the SVC-RUNTIME-CONTROL slice. |
| Auth maturity | Runtime-manager and legacy internal paths enforce bearer presence/stub-level checks; production JWT/RBAC/MFA validation is not claimed closed. | Frontend must continue sending `Authorization`; high-risk actions should pass `X-MFA-Token` when available, but this packet does not claim production auth completion. | Explicit post-close hardening gap. |

---

## 4. Operator Journey Handoff

### 4.1 Normal Runtime-Control Journey

1. Operator enters the console through BFF-backed screens.
2. Runtime roster reads from `GET /api/v1/operator/runtime-state`.
3. Health and fallback posture read from `GET /api/v1/operator/health-status`
   and `GET /api/v1/operator/degraded-control-guidance`.
4. Incident drawer reads kill-switch authority from `GET /api/v1/kill-switch/status`.
5. Operator submits governed runtime/evolution commands through
   `POST /api/v1/operator/commands`.
6. UI renders the immediate command receipt and polls
   `GET /api/v1/operator/commands/{command_id}`.
7. BFF async worker routes runtime-control commands to the runtime-manager
   service and evolution commands to the evolution service.
8. UI refreshes the owning read surface after command completion instead of
   locally deriving state transitions.

### 4.2 Degraded or BFF-Outage Journey

1. If BFF read surfaces are degraded, keep backend-owned degraded/unavailable
   copy visible and disable dependent CTAs.
2. If BFF is reachable, fetch `GET /api/v1/operator/degraded-control-guidance`
   for admin CLI and protected internal API guidance.
3. If BFF is unavailable, operators use the non-BFF secondary path documented by
   BFF HA policy: admin CLI, protected internal API, or runtime-manager protected
   admin endpoint.
4. Frontend must not call runtime-manager directly from browser code as a
   hidden fallback.

---

## 5. Frontend Handoff Materials

Use the current BFF/front packets below as the frontend source set. This sidecar
does not create a new Lovable task or new frontend implementation request.

| Screen / flow | Frontend contract material | Notes |
|---|---|---|
| Runtime State Board | `docs/bff/PKT-010-runtime-state-board.md`, `docs/examples/PKT-010-runtime-state-board.json`, `docs/screens/PKT-010-runtime-state-board.md` | Read-only runtime roster. UI must not join runtime/telemetry/rollback primitives in the browser. |
| Incident Action Drawer | `docs/bff/PKT-002-incident-action-drawer.md`, `docs/pantheon-delivery/PKT-002-incident-action-drawer/CONTRACT_LOCK.md` | Runtime-control write actions use `POST /api/v1/operator/commands`; kill-switch status uses `GET /api/v1/kill-switch/status`. |
| Health Status Board | `docs/bff/PKT-011-health-status-board.md`, `docs/screens/PKT-011-health-status-board.md` | Backend-owned health groups and secondary-control-path display. |
| Alerts Rail | `docs/bff/PKT-012-alerts-rail.md` | Alert links may route operators into runtime state or incident response. |
| Operator Home | `docs/bff/PKT-013-operator-home.md` | Runtime card links to `/operator/runtime-state`; do not synthesize command authority from home cards. |
| Paper/Live Drift | `docs/bff/PKT-014-paper-live-drift.md` | Runtime drilldown/evidence view; command writes remain separate. |
| Mutation/Evolution Review | `docs/bff/EW-05-mutation-review.md` | Approve/reject mutation commands stay on BFF and route to the evolution service backend. |

Frontend implementation constraints:

- Use the existing BFF client helpers; do not add raw browser fetches to
  runtime-manager or `/api/internal/v1/...`.
- Keep command authority backend-shaped through `allowedActions`.
- Render command receipts and command-status failures exactly as returned.
- Treat missing runtime binding, pool, rollback, kill-switch, or surface metadata
  as a `bff-gap`, not as permission to synthesize target context locally.
- For high-risk commands, preserve operator rationale, trace/idempotency headers
  when available, and MFA token propagation.

---

## 6. Minimal Smoke Requests for Frontend QA

```http
GET /health
Authorization: Bearer op-42:operator
```

```http
GET /api/v1/operator/runtime-state
Authorization: Bearer op-42:operator
```

```http
GET /api/v1/kill-switch/status
Authorization: Bearer op-admin:admin
```

```http
POST /api/v1/operator/commands
Authorization: Bearer op-admin:admin
X-MFA-Token: 000000
Content-Type: application/json

{
  "command": "IssueSafeMode",
  "target": { "type": "Runtime", "id": "runtime-042" },
  "params": { "safe_mode_level": "soft" },
  "audit_context": { "reason": "operator verified degraded runtime state" }
}
```

```http
GET /api/v1/operator/commands/{command_id}
Authorization: Bearer op-admin:admin
```

The sample write request is for QA shape validation only. Use fixture/runtime
ids that exist in the target test environment.

---

## 7. Verification Evidence

Focused verification run by this sidecar:

```bash
PYTHONPATH=/home/lupin/.local/lib/python3.12/site-packages python3.12 -m pytest \
  services/runtime-manager/test_internal_api_routes.py \
  services/control-plane/bff/test_command_executor.py \
  services/control-plane/bff/test_governance_command_submission.py \
  services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q
```

Result: `39 passed in 2.94s`.

Coverage relevance:

| Target | Evidence provided |
|---|---|
| Runtime-manager legacy command mount | Runtime-manager serves legacy pause/rollback/kill-switch/command lookup paths coherently with canonical runtime-manager routes. |
| BFF executor routing | Runtime, rollback, kill-switch, safe-mode, and evolution command executors dispatch to configured service URLs. |
| BFF command admission | `POST /api/v1/operator/commands` records foundation/idempotency/audit context and accepts published command payload variants. |
| Runtime-state read handoff | `GET /api/v1/operator/runtime-state` preserves backend-owned sorting/filtering, pagination, and degraded/unavailable semantics. |

---

## 8. Non-Claims

This packet does not claim:

| Non-claim | Correct owner / follow-up |
|---|---|
| Production-grade JWT, RBAC, MFA, token issuer/audience validation on runtime-control paths. | Runtime-control hardening follow-up. |
| Full convergence of legacy kill-switch idempotency with the runtime-manager foundation idempotency ledger. | Runtime-control hardening follow-up. |
| One unified command-history reconciliation surface across BFF-submitted and direct internal API commands. | Future BFF/runtime-control hardening. |
| BFF automatic fallback command submission with `routing_path: fallback`. | Future BFF contract/implementation alignment if required. |
| Full default-stack boot proof. | `SVC-COMPOSE`. |
| Removal of BFF read snapshot/default fallback in the normal single-VM path. | `SVC-SURFACES`. |

---

## 9. Reviewer Focus for Claude

1. Confirm this packet is support-only and does not promote new canonical
   runtime-control or BFF truth.
2. Confirm the frontend guidance preserves BFF as the only browser-facing write
   surface and does not create runtime-manager direct-call instructions.
3. Confirm the query gaps are framed as residual hardening/contract alignment,
   not blockers to the already reviewed `SVC-RUNTIME-CONTROL` implementation.
4. Confirm the operator journey correctly separates normal BFF command flow from
   degraded/admin fallback flow.
5. Confirm the evidence paths are sufficient for parent owner absorption or
   discard.

Recommended disposition: approve this sidecar as support material, then let the
parent owner decide whether any section should be folded into the main closeout
or downstream hardening work.
