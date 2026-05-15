# EXEC-RUNTIME-RW04-001 Acceptance Packet and Dependency Map (Sidecar)

Date: `2026-04-21`
Sidecar task: `EXEC-RUNTIME-RW04-001-SIDECAR-ACCEPTANCE`
Parent task: `EXEC-RUNTIME-RW04-001`
Sidecar owner / reviewer: `Codex2` / `Codex`
Parent owner / reviewer: `Claude` / `Codex`
Helper kind: `acceptance_packet`
Scope: support-only acceptance packet and dependency map; no canonical truth, runtime implementation, or contract docs are modified here

## Parent Status Snapshot

- `./scripts/ai-status.sh show EXEC-RUNTIME-RW04-001` now resolves the parent from the archive snapshot, not the active task table.
- The durable parent snapshot records:
  - `status: done`
  - `terminal_outcome: completed`
  - `archived_at: 2026-04-21T19:21:36Z`
- The final parent handoff says:
  - `RW-04 runtime refresh revalidated over live HTTP; acceptance met and ready for owner finalization.`
- The archived parent `next` field says:
  - `Owner finalized: RW-04 runtime refresh accepted and closed. All 4 /api/v1/experiments* routes confirmed live on 18001 via live probes; pytest 21 passed; degraded/unavailable/queued/running/completed/failed/canceled/OBJECT_NOT_FOUND semantics verified. Frontend feedback can_close=true, loop_close_condition met. No API gaps remain. Deployed browser QA deferred as non-blocking follow-up.`
- The durable review note remains:
  - `重新驗證 RW-04 runtime acceptance：python3 -m pytest -q services/control-plane/bff/test_rw04_experiment_launch_contract.py 21 項通過；18001 live OpenAPI 仍公布四條 /api/v1/experiments* 路由；authenticated probes 確認 degraded list/detail、404 OBJECT_NOT_FOUND、以及 queued→canceled round-trip；另以臨時 18012 unavailable probe 驗證 truthful unavailable list/detail envelopes。主 task 可進入 review_approved，等待 owner 正式收尾。`
- Delivery closeout is also recorded in the archive snapshot at commit `12be7b49848c20526a57664c237b58ad99ca8b3e` with subject `EXEC-RUNTIME-RW04-001 finalize approved RW-04 runtime refresh closeout`.
- This sidecar now serves as a refreshed support packet so `Codex` can review the sidecar itself against the parent's archived closeout truth without re-reading the full RW-04 loop.

## Executive Summary

For the parent task's own scope, the evidence surface is now acceptance-ready.
The active operator BFF on `http://127.0.0.1:18001` once again exposes the full
RW-04 experiment route family over live HTTP, and the repo now has durable
artifacts showing:

- route publication is visible in live `/openapi.json`
- authenticated live probes cover degraded list/detail behavior
- the full run-state spread is observed over HTTP:
  `queued`, `running`, `completed`, `failed`, `canceled`, and
  `404 OBJECT_NOT_FOUND`
- a workspace-backed HTTP probe on `http://127.0.0.1:18012` confirms truthful
  `unavailable` envelopes for list and detail
- the earlier runtime blocker artifact is now resolved as `status: completed`

Important boundary:

- the parent runtime task already satisfied its own runtime-refresh acceptance,
  passed review, and has now been finalized to archived `done`
- the broader RW-04 front feedback closeout also now records
  `disposition: close`, `can_close: true`, and `loop_close_condition: Met`
- this sidecar remains support-only; it does not itself finalize the parent
  task, reopen it, or redefine any canonical execution truth

## Acceptance Mapping

The parent acceptance in the archived snapshot
`ai-task-archive/tasks/EXEC-RUNTIME-RW04-001.json` is:

1. the active operator-bff runtime exposes the full RW-04
   `/api/v1/experiments` route family over live HTTP
2. the live runtime returns the published `queued`, `running`, `degraded`,
   `unavailable`, and terminal semantics
3. the existing RW-04 front follow-up no longer waits on runtime freshness

Current evidence posture:

| Parent acceptance item | Evidence | Status |
|---|---|---|
| Live RW-04 route family exposed on active runtime | `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml` says `/openapi.json` on `18001` advertises `GET /api/v1/experiments`, `POST /api/v1/experiments/launch`, `GET /api/v1/experiments/{experiment_id}`, and `POST /api/v1/experiments/{experiment_id}/cancel` | PASS |
| Published semantics return over truthful HTTP | Same artifact records authenticated degraded list/detail probes, `queued` launch, `running` / `completed` / `failed` detail states, terminal `canceled`, `404 OBJECT_NOT_FOUND`, plus workspace-backed `unavailable` list/detail probes on `18012` | PASS |
| Front loop no longer blocked on runtime freshness | The `needs-runtime` artifact is marked `completed`, its `next_step` hands control back to Pantheon review / front-sync, `.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml` records `can_close: true` with `loop_close_condition: Met`, and the archived parent `next` field confirms no API gaps remain | PASS |

Reviewer posture to preserve:

- accept the parent runtime slice as materially complete for its stated
  acceptance and already archived as `done`
- do not treat this sidecar packet as canonical closure by itself; it is a
  support artifact aligned to the durable truth already recorded elsewhere
- keep any residual risk framed as non-blocking post-close browser QA, not as a
  runtime freshness or publication-truth blocker

## Dependency Map

### 1. Direct Runtime Acceptance Sources

| Artifact | Role in acceptance decision |
|---|---|
| `ai-task-archive/tasks/EXEC-RUNTIME-RW04-001.json` | Durable parent owner / reviewer / acceptance text / terminal closeout snapshot after archival |
| `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml` | Main live-runtime proof artifact; records completion, live probe results, and handoff back to review |
| `.coordination/reviews/RW-04-experiment-launch-review.md` | Review record that originally identified the runtime freshness blocker and now documents the revalidated runtime positives |

### 2. Supporting Truth Providers Behind the Runtime Check

| Source | Contribution |
|---|---|
| `docs/bff/RW-04-experiment-launch.md` | Canonical RW-04 route family and behavior contract for launch/list/detail/cancel |
| `services/control-plane/bff/test_rw04_experiment_launch_contract.py` | Regression proof cited by both review and needs-runtime artifacts; `21 passed` |
| `.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml` | Confirms the prior front publication blocker is resolved and the loop close condition is met from the current replayable Git-visible chain |

### 3. Dependency Chain

```text
docs/bff/RW-04-experiment-launch.md
  -> defines the published RW-04 launch/list/detail/cancel contract

services/control-plane/bff/test_rw04_experiment_launch_contract.py
  -> proves contract behavior remains regression-covered (21 passed)

.coordination/reviews/RW-04-experiment-launch-review.md
  -> records the earlier stale-runtime finding and the later live-runtime revalidation

.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml
  -> closes the runtime freshness blocker with live 18001/18012 evidence

EXEC-RUNTIME-RW04-001
  -> already finalized and archived as the completed runtime-refresh slice

.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml
  -> records replay-clean closeout, can_close=true, and loop_close_condition met
```

## Evidence Highlights

From `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml`:

- runtime blocker is resolved as `status: completed`
- live runtime URL: `http://127.0.0.1:18001`
- unavailable probe URL: `http://127.0.0.1:18012`
- verification records:
  - `pytest` returned `21 passed`
  - live OpenAPI advertises all four RW-04 methods
  - live list probe returned `200` with `experiment_history = degraded`
  - live detail probes returned completed, running, failed, and missing-id truth
  - live launch produced queued `exp-20260421-004`
  - cancel produced terminal `canceled`
  - unavailable list/detail probes returned truthful `unavailable` envelopes

From `.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml`:

- disposition is `close`
- `can_close` is `true`
- `loop_close_condition` is explicitly `Met`
- no Pantheon API gap remains in this loop
- remaining risk is deferred non-blocking browser QA only

## Recommended Review Flow

For `Codex` as sidecar reviewer:

1. confirm this packet stayed within support-only scope
2. compare the parent acceptance text in the archived snapshot
   `ai-task-archive/tasks/EXEC-RUNTIME-RW04-001.json` against
   `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml`
3. verify the packet reflects the updated frontend closeout truth without
   claiming to be the canonical source of that closure

For the archived parent closeout record:

1. treat `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml`
   as the primary live acceptance artifact
2. use `.coordination/reviews/RW-04-experiment-launch-review.md` only to
   understand what was previously broken and what was revalidated
3. note that parent review and owner finalization have both already happened in
   durable state; this packet should not imply further parent action is needed

## Suggested Disposition

- Sidecar disposition: approve if this packet accurately captures the current
  acceptance boundary and dependency chain
- Parent disposition: no new action. The durable truth already records archived
  `done` for the runtime-refresh slice with completed delivery metadata

## Sidecar Acceptance Check

- Support artifact created only: yes
- Canonical truth modified: no
- Parent dependency / evidence map included: yes
- Reviewer handoff ready: yes
