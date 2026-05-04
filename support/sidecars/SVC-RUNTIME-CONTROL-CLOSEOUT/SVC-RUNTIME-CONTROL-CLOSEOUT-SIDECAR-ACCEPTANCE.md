# SVC-RUNTIME-CONTROL-CLOSEOUT Acceptance Packet and Dependency Map

**Sidecar Task ID**: `SVC-RUNTIME-CONTROL-CLOSEOUT-SIDECAR-ACCEPTANCE`  
**Parent Task**: `SVC-RUNTIME-CONTROL-CLOSEOUT`  
**Current Parent Owner**: `Codex2`  
**Current Parent Reviewer**: `Codex`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Gemini`  
**Helper Kind**: `acceptance_packet`  
**Date**: 2026-04-28

This is a support artifact only. It does not update canonical truth, L1 policy,
core contracts, runtime-manager behavior, registry logic, governance
implementation, or compose wiring. The parent owner decides whether and how to
use this packet in the main `SVC-RUNTIME-CONTROL-CLOSEOUT` finalization.

---

## 1. Scope Snapshot

`SVC-RUNTIME-CONTROL-CLOSEOUT` exists to close the reviewed
`SVC-RUNTIME-CONTROL` implementation without overstating production maturity.
The closeout must preserve three caveats as post-close hardening work:

| Caveat | Closeout disposition |
|---|---|
| JWT/RBAC/MFA enforcement on protected runtime and internal command paths | Not production-complete; tracked by `SVC-RUNTIME-HARDENING`. |
| Legacy internal API kill-switch idempotency convergence | Not production-complete; tracked by `SVC-RUNTIME-HARDENING`. |
| `ApproveDeployment` placeholder approval authority | Not production-complete; tracked by `SVC-RUNTIME-HARDENING`. |

Current state from `ai-status.json` at packet creation:

| Task | Owner | Reviewer | Status | Note |
|---|---|---|---|---|
| `SVC-RUNTIME-CONTROL` | `Claude` | `Gemini` | `review_approved` | Awaiting owner finalization to `done`. |
| `SVC-RUNTIME-CONTROL-CLOSEOUT` | `Codex2` | `Codex` | `review_approved` | Auto-reassigned from Gemini after capacity failure; awaiting owner finalization. |
| `SVC-RUNTIME-HARDENING` | `Claude` | `Gemini` | `todo` | Owns the explicit production-hardening follow-up. |

---

## 2. Parent Acceptance Trace

| Parent closeout acceptance | Evidence trace | Sidecar assessment |
|---|---|---|
| Gemini produces approved or rejected disposition for `SVC-RUNTIME-CONTROL`. | `SVC-RUNTIME-CONTROL` is `review_approved` with Gemini review notes and review file `docs/reviews/2026-04-28-svc-runtime-control-claude-handoff.md`. | PASS |
| Auth/JWT validation, legacy idempotency convergence, and placeholder deployment approval are recorded as post-close hardening gaps. | `SVC-RUNTIME-CONTROL-CLOSEOUT` review notes state that production-grade closure is not claimed; `SVC-RUNTIME-HARDENING` carries JWT/RBAC/MFA, legacy idempotency, and approval-authority acceptance items. | PASS |
| `SVC-RUNTIME-CONTROL` is either archived done or returned with concrete reviewer blockers. | It is not archived yet; it is `review_approved`, which is the correct pre-finalization state. No reviewer blockers are open in the inspected task state. | PASS with finalization pending |

---

## 3. Runtime-Control Acceptance Trace

| Runtime-control acceptance item | Evidence trace | Sidecar assessment |
|---|---|---|
| Runtime-control is exposed as a deployable service with a stable port and health surface. | `docker-compose.yml` exposes `runtime-manager` on container port `8081`, host port `18081`, and healthchecks `http://127.0.0.1:8081/__health__`. `services/runtime-manager/main.py` defines `/__health__`. | PASS |
| Operator command plane is converged onto the deployable runtime-manager process. | `services/runtime-manager/main.py` registers `services/runtime-manager/internal_api_routes.py`, which mounts legacy `/api/internal/v1/...` operator command routes onto the runtime-manager Flask app. | PASS |
| BFF runtime commands target runtime-manager rather than a local-only placeholder path. | `docker-compose.yml` sets `PANTHEON_INTERNAL_API_URL` and `PANTHEON_RUNTIME_MANAGER_URL` to `http://runtime-manager:8081` for `operator-bff`. | PASS |
| Evolution approval/action no longer terminate in local BFF placeholders. | `operator-bff` receives `PANTHEON_GOVERNANCE_API_URL=http://evolution:8093` and `PANTHEON_EVOLUTION_API_URL=http://evolution:8093`; command executor tests cover approve/reject/execute URL contours. | PASS |

---

## 4. Dependency Map

### Upstream and direct dependencies

| Dependency | Current status | Why it matters |
|---|---|---|
| `SVC-BASELINE` | archived `done` | Locks single-VM port/env/volume/health/profile/Dockerfile expectations inherited by runtime-manager and BFF compose wiring. |
| `SVC-RUNTIME-CONTROL` | `review_approved` | Parent closeout depends on its Gemini-approved implementation handoff. It still needs Claude owner finalization. |

### Adjacent and downstream tasks

| Task | Current status | Relationship |
|---|---|---|
| `SVC-GOVERNANCE-API` | `review` | Adjacent boundary task for governance/evolution/deployment authority. Required by `SVC-RUNTIME-HARDENING` for approval-authority cleanup. |
| `SVC-RUNTIME-HARDENING` | `todo` | Follow-up implementation lane for JWT/RBAC/MFA, idempotency convergence, and authoritative deployment approval. |
| `SVC-SURFACES` | `todo` | Downstream BFF work depends on runtime closeout and governance API before disabling normal snapshot/default fallback. |
| `SVC-COMPOSE` | `todo` | Downstream stack proof depends on runtime closeout, governance API, evidence, surfaces, and service disposition. |

Recommended dependency disposition:

1. Finalizing `SVC-RUNTIME-CONTROL-CLOSEOUT` should not be blocked by
   `SVC-RUNTIME-HARDENING`; the hardening task is the accepted caveat container.
2. `SVC-SURFACES` and `SVC-COMPOSE` should treat `SVC-RUNTIME-CONTROL-CLOSEOUT`
   as closed only after the owner moves it from `review_approved` to `done`.
3. `SVC-RUNTIME-CONTROL` itself remains a separate owner-finalization step for
   Claude; this sidecar does not finalize or alter that task.

---

## 5. Verification Evidence

Focused verification run by this sidecar:

```bash
PYTHONPATH=/home/lupin/.local/lib/python3.12/site-packages python3.12 -m pytest \
  services/runtime-manager/test_internal_api_routes.py \
  services/control_plane/test_internal_api_incident.py \
  services/control-plane/bff/test_command_executor.py -q
```

Result: `41 passed in 1.23s`.

Compose validation:

```bash
docker compose config --quiet
```

Result: exit `0`.

Coverage relevance:

| Verification target | Evidence provided |
|---|---|
| `services/runtime-manager/test_internal_api_routes.py` | Legacy internal command routes are mounted on runtime-manager and share runtime/kill-switch state with canonical runtime-manager routes. |
| `services/control_plane/test_internal_api_incident.py` | Incident control internal API behavior remains covered for pause, rollback, kill-switch, and command-state paths. |
| `services/control-plane/bff/test_command_executor.py` | BFF command executor dispatches runtime, rollback, kill-switch, and evolution approve/reject/execute actions to configured service URLs. |
| `docker compose config --quiet` | Current compose graph parses successfully with runtime-manager and BFF service wiring. |

---

## 6. Non-Claims

This packet does not claim:

| Non-claim | Correct owner |
|---|---|
| Production-grade runtime-control auth, JWT claim validation, RBAC, or MFA. | `SVC-RUNTIME-HARDENING` |
| Foundation-level idempotency convergence for the legacy internal kill-switch route. | `SVC-RUNTIME-HARDENING` |
| Authoritative deployment approval integration replacing placeholder approval IDs. | `SVC-RUNTIME-HARDENING` |
| Full default-stack boot or smoke-profile proof. | `SVC-COMPOSE` |
| BFF read-path removal of snapshot/default fallback. | `SVC-SURFACES` |

---

## 7. Reviewer Checklist for Gemini

| Check | Expected answer |
|---|---|
| Did this sidecar avoid canonical/runtime edits? | Yes. It only creates this support packet. |
| Does the packet preserve closeout caveats instead of claiming production-grade runtime-control? | Yes. All three caveats are routed to `SVC-RUNTIME-HARDENING`. |
| Is parent closeout acceptance ready for owner finalization? | Yes, with the explicit note that parent is currently `review_approved`, not `done`. |
| Are downstream dependencies called out without moving them? | Yes. `SVC-SURFACES`, `SVC-COMPOSE`, and `SVC-RUNTIME-HARDENING` remain separate tasks. |
| Is focused verification recorded? | Yes. Runtime/control-plane/BFF command tests passed and compose config validation passed. |

---

## 8. Handoff

**To**: `Gemini`  
**From**: `Codex`  
**Requested review outcome**: Approve this sidecar if the acceptance packet and
dependency map are accurate support material for
`SVC-RUNTIME-CONTROL-CLOSEOUT`.

Recommended parent-owner use:

1. Use sections 2-4 as the closeout acceptance/dependency checklist.
2. Keep `SVC-RUNTIME-HARDENING` as the only owner of the production-hardening
   caveats.
3. Finalize `SVC-RUNTIME-CONTROL-CLOSEOUT` to `done` only through the normal
   owner path; this sidecar should remain support material, not canonical
   closure truth.
