# EXEC-RUNTIME-RW04-001 Review Packet and Evidence Summary (Sidecar)

Date: `2026-04-21`
Sidecar task: `EXEC-RUNTIME-RW04-001-SIDECAR-REVIEW`
Parent task: `EXEC-RUNTIME-RW04-001`
Sidecar owner / reviewer: `Claude` / `Codex`
Parent owner / reviewer: `Claude` / `Codex`
Helper kind: `review_packet`
Scope: support-only review packet and evidence summary; no canonical truth, runtime implementation, or contract docs are modified here

---

## Parent Task Summary

**EXEC-RUNTIME-RW04-001** — RW-04 Runtime Refresh and Verification

The parent task was created after the original RW-04 front review identified a stale-runtime blocker: the operator-bff at the time no longer advertised the full `/api/v1/experiments*` route family. The task scope was to:

1. reboot/refresh the active operator-bff so the four RW-04 routes return to live HTTP publication
2. revalidate the route family via authenticated live probes covering all declared run states
3. re-satisfy the three prior frontend logic regressions against the newly-live runtime
4. unblock the RW-04 front loop so the `can_close` condition could be marked `Met`

The parent task is now finalized. As of `2026-04-21T19:21:36Z` it is archived as `done` with a completion commit at `12be7b49848c20526a57664c237b58ad99ca8b3e`.

---

## Parent Acceptance Criteria and Current Status

| Acceptance criterion | Evidence artifact | Status |
|---|---|---|
| Active operator-bff exposes full RW-04 `/api/v1/experiments*` route family over live HTTP | `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml` — live OpenAPI on `18001` lists all four routes | **PASS** |
| Live runtime returns truthful `queued`, `running`, `degraded`, `unavailable`, and terminal semantics | Same artifact; live probes on `18001` and workspace-backed `18012` cover all declared states | **PASS** |
| Existing RW-04 front follow-up no longer waits on runtime freshness | `.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml` records `can_close: true`, `loop_close_condition: Met`, no API gaps remain | **PASS** |

---

## Evidence Summary

### Primary Live-Runtime Proof

**Artifact:** `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml`

- `status: completed`, `resolved_at: 2026-04-21T16:50:45Z`, `resolved_by: Codex`
- Runtime URL: `http://127.0.0.1:18001`
- Unavailable probe URL: `http://127.0.0.1:18012`

Live verification results recorded in the artifact:

| Verification step | Result |
|---|---|
| `pytest` on RW-04 contract slice | `21 passed` |
| Live OpenAPI on `18001` | Advertises all 4 RW-04 methods (GET list, POST launch, GET detail, POST cancel) |
| Live list probe on `18001` | `200` with `experiment_history: degraded`; seeded completed/running/failed rows |
| Live detail probes on `18001` | `200` completed `exp-20260419-012` (`canCancel: false`); `200` running `exp-20260418-009` (`canCancel: true`); `200` failed `exp-20260417-004` with `reason_code: MISSING_DATA_PARTITION`; `404 OBJECT_NOT_FOUND` for unknown id |
| Live launch + cancel round-trip | Queued `exp-20260421-004`; cancel returned `canceled`, `canCancel: false`; follow-up probes confirmed terminal state |
| Unavailable list probe on `18012` | `200` with `ids: []`, `total: 0`, `experiment_history: unavailable` |
| Unavailable detail probe on `18012` | `200` with `experiment_status: unavailable`; backend-owned run snapshot preserved |

### Review Record

**Artifact:** `.coordination/reviews/RW-04-experiment-launch-review.md`

Reviewer: Codex — Decision: **Approved** (2026-04-21)

Key review findings:
- Runtime freshness blocker resolved: live OpenAPI on `18001` returned `200` listing all four `/api/v1/experiments*` routes
- Front publication chain now replayable from Git history: `origin/pkt-004-detail-fix` → `f00791b217e5550d80c1add72a8560b42bc3a056`; request pair references reviewed UI transport commit `f672af2c0019618ce05cf07c7ed50c65897e9fbb`
- App-shell and shared-client wiring confirmed in immutable Git history (App.tsx routes, AppSidebar.tsx navigation, WorkbenchBreadcrumb.tsx mapping, bffClient.ts RW-04 client)
- Three prior logic regressions confirmed fixed: route-not-live 404 classification, launch-polling continuation, history filter pagination reset
- Cancel/degradation handling contract-aligned: `allowedActions.canCancel` plus surface state gate, no local lifecycle inference
- Static verification passed: `npm run build` succeeded in `/home/lupin/code/front-ai-trading-system`

### Frontend Closeout

**Artifact:** `.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml`

- `disposition: close`
- `can_close: true`
- `loop_close_condition: Met`
- All 8 acceptance criteria passed (see artifact for detail)
- `api_gaps: []`
- Remaining risk: deferred non-blocking deployed browser QA only
- `lovable_ui_task_status: closed`
- `next_action: none`

### Delivery Commit

- Commit `12be7b49848c20526a57664c237b58ad99ca8b3e`
- Subject: `EXEC-RUNTIME-RW04-001 finalize approved RW-04 runtime refresh closeout`

---

## Reviewer Notes on Parent Scope Boundary

The parent task **EXEC-RUNTIME-RW04-001** was strictly a runtime-refresh-and-verification slice:

- it did not modify the RW-04 API contract document (`docs/bff/RW-04-experiment-launch.md`)
- it did not modify the front repo implementation files
- its repo-tracked evidence surface stayed in Pantheon-side coordination / review / delivery records, while the live runtime refresh itself happened in operator-bff process state
- the contract test (`services/control-plane/bff/test_rw04_experiment_launch_contract.py`) is a regression harness used to confirm existing behavior, not a new product capability

---

## Sidecar Reviewer Instructions (for Codex)

This sidecar is a support-only artifact. Reviewing it does not replace or reopen the parent task's already-archived `done` state. The sidecar review confirms:

1. **Scope compliance** — the packet stays within support-only bounds (no canonical truth modified)
2. **Evidence accuracy** — the claims in this packet match what is recorded in the primary coordination artifacts listed above
3. **Completeness** — the evidence surface covers all three parent acceptance criteria

Suggested review steps:

1. Spot-check `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml` to confirm the `status: completed` and the listed live-probe results match this packet's Evidence Summary table.
2. Spot-check `.coordination/reviews/RW-04-experiment-launch-review.md` to confirm the `Decision: Approved` and the wiring evidence lines match this packet's Review Record section.
3. Spot-check `.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml` to confirm `can_close: true` and `loop_close_condition: Met`.
4. Confirm this packet does not touch any L1 canonical document, contract doc, or runtime implementation file.

---

## Sidecar Acceptance Check

- Support artifact created only: **yes**
- Canonical truth modified: **no**
- Parent evidence / acceptance map included: **yes**
- Reviewer handoff ready: **yes**
- Parent task already closed: **yes** — archived as `done` at `2026-04-21T19:21:36Z`

---

## Suggested Sidecar Disposition

Approve this packet if the evidence claims above are accurate when spot-checked against the three listed coordination artifacts. No further action on the parent task is required or appropriate; it is already archived.
