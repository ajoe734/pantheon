# EXEC-FRONT-RW04-001 Acceptance Packet and Dependency Map (Sidecar)

**Parent Task**: `EXEC-FRONT-RW04-001` - Implement RW-04 experiment launch front-end flow against live Pantheon APIs
**Parent Owner**: `Copilot`
**Parent Reviewer**: `Codex`
**Parent Status**: `todo` (frontend implementation not yet started by the parent owner)
**Sidecar Task**: `EXEC-FRONT-RW04-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Copilot`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-21`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance / main frontend
> implementations. It packages the current RW-04 frontend readiness state into a
> reviewer-ready acceptance packet for parent-owner absorption.

---

## 1. Executive Summary

`EXEC-FRONT-RW04-001` is a frontend implementation slice, not a contract-writing
or BFF route-building task. The direct prerequisite for this work was
`EXEC-REBASE-RW04-001`, which already published a self-contained frontend
handoff bundle for RW-04 and closed as `done`.

The current repo state shows:

- the RW-04 contract is already published (`RW-04-EXPERIMENT-001`)
- the live BFF route family is already implemented and regression-covered
  (`AUTO-IMPL-RW04-001`)
- the frontend handoff / coordination bundle is already refreshed and internally
  consistent (`EXEC-REBASE-RW04-001`)
- no `bff-gap` or `ui-done` request file exists yet, which is consistent with the
  parent task still being `todo`

This means the parent owner should treat RW-04 as **ready for frontend
implementation**, with one explicit guardrail: if the live payload diverges from
the published bundle, the correct next action is to emit the RW-04
`bff-gap` handoff instead of inventing local UI truth.

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Canonical owner / reviewer / lifecycle truth for the parent sidecar and parent execution task |
| `ai-task-archive/tasks/EXEC-REBASE-RW04-001.json` | Confirms the direct dependency is `done`, reviewed, and finalized with the RW-04 handoff bundle aligned |
| `.coordination/responses/RW-04-experiment-launch-contract-ready.yaml` | Declares all four RW-04 routes live and frontend-ready |
| `.coordination/responses/RW-04-experiment-launch-lovable-ui-task.yaml` | Defines frontend constraints, completion handoff, and required feedback payloads |
| `docs/pantheon-handoffs/RW-04-experiment-launch/FRONTEND_CHANGE_SPEC.md` | Main frontend implementation brief, route semantics, state-machine guardrails, degradation handling, and completion instructions |
| `ai-task-archive/tasks/RW-04-EXPERIMENT-001.json` | Upstream contract publication record for launch/history/detail/cancel semantics |
| `ai-task-archive/tasks/AUTO-IMPL-RW04-001.json` | Upstream BFF implementation record confirming live route family and production-path regression coverage |
| `.coordination/requests/RW-04-experiment-launch-bff-gap.example.yaml` | Confirms the stop-and-escalate template exists if contract drift is found |
| `.coordination/requests/RW-04-experiment-launch-ui-done.example.yaml` | Confirms the completion handoff template exists for the parent owner to emit after frontend delivery |

---

## 3. Acceptance Scope Verification

The parent task acceptance says it must:

1. implement experiment launch / history / detail UI against the four live RW-04 routes and async state machine
2. avoid inventing progress / history or overriding `allowedActions.canCancel`
3. finish by submitting the UI-done handoff bundle

This sidecar verifies that the repo already provides the upstream truth needed to
attempt that work:

| Scope Item | Verification | Status |
|---|---|---|
| Four live routes are published to frontend | `contract-ready.yaml` lists `POST /api/v1/experiments/launch`, `GET /api/v1/experiments`, `GET /api/v1/experiments/{experiment_id}`, `POST /api/v1/experiments/{experiment_id}/cancel` as live | PASS |
| Direct dependency is satisfied | `EXEC-REBASE-RW04-001` archived as `done`; review notes say the RW-04 handoff bundle, prompt, example payload, and templates are self-consistent and ready for the frontend lane | PASS |
| Frontend route / state-machine semantics are explicit | `FRONTEND_CHANGE_SPEC.md` publishes legal transitions (`queued -> running`, `queued -> canceled`, `running -> completed|failed|canceled`) and says the backend owns the lifecycle | PASS |
| Cancel CTA truth is backend-owned | `lovable-ui-task.yaml` and `FRONTEND_CHANGE_SPEC.md` both require cancel visibility to come from `allowedActions.canCancel`, not client inference | PASS |
| Fake progress / fake history are explicitly forbidden | `contract-ready.yaml`, `lovable-ui-task.yaml`, and `FRONTEND_CHANGE_SPEC.md` all forbid synthesizing progress, history, or cancel authority from local timers / ticker data / artifact presence | PASS |
| Stop-and-escalate path exists if payloads drift | `RW-04-experiment-launch-bff-gap.example.yaml` exists and is referenced by both the handoff bundle and the frontend spec | PASS |
| Completion handoff path exists | `RW-04-experiment-launch-ui-done.example.yaml` exists and is referenced by both the handoff bundle and the frontend spec | PASS |

---

## 4. Dependency Map

### 4.1 Direct Parent Dependency

| Task ID | Status | Why it matters to `EXEC-FRONT-RW04-001` |
|---|---|---|
| `EXEC-REBASE-RW04-001` | `done` | Published the contract-ready coordination bundle, frontend handoff spec, required templates, and truthful readiness wording so frontend implementation can start without reopening the contract |

### 4.2 Upstream Truth Providers Behind the Rebase

| Task ID | Status | Contribution |
|---|---|---|
| `RW-04-EXPERIMENT-001` | `done` | Published the canonical launch/history/detail/cancel contract, async state machine, queued-to-canceled path, and persisted-history requirement |
| `AUTO-IMPL-RW04-001` | `done` | Landed the live BFF route family and fixed the non-fallback production-path bug so launch/list/detail/cancel work through the service-backed experiment store |

### 4.3 Artifact-Level Readiness Chain

```text
RW-04-EXPERIMENT-001
  -> docs/bff/RW-04-experiment-launch.md
  -> docs/examples/RW-04-experiment-launch.json

AUTO-IMPL-RW04-001
  -> live BFF route family + regression coverage

EXEC-REBASE-RW04-001
  -> .coordination/responses/RW-04-experiment-launch-contract-ready.yaml
  -> .coordination/responses/RW-04-experiment-launch-lovable-ui-task.yaml
  -> docs/pantheon-handoffs/RW-04-experiment-launch/FRONTEND_CHANGE_SPEC.md
  -> .coordination/requests/RW-04-experiment-launch-bff-gap.example.yaml
  -> .coordination/requests/RW-04-experiment-launch-ui-done.example.yaml

EXEC-FRONT-RW04-001
  -> frontend implementation in front-ai-trading-system
  -> emits either:
       .coordination/requests/RW-04-experiment-launch-bff-gap.yaml
       or
       .coordination/requests/RW-04-experiment-launch-ui-done.yaml
```

### 4.4 Current Not-Yet-Produced Outputs

These files do **not** currently exist in the working tree, which matches the
parent task still being pre-implementation:

- `.coordination/requests/RW-04-experiment-launch-bff-gap.yaml`
- `.coordination/requests/RW-04-experiment-launch-ui-done.yaml`

That absence should be read as "frontend work has not yet returned a handoff,"
not as a gap in the readiness bundle.

---

## 5. Parent-Owner Action Summary

For `Copilot` as parent owner, the support recommendation is:

1. treat RW-04 as a ready-to-implement frontend slice, not a waiting-for-BFF slice
2. implement only against the published bundle:
   `contract-ready.yaml`, `lovable-ui-task.yaml`, and `FRONTEND_CHANGE_SPEC.md`
3. keep all lifecycle truth backend-owned:
   `status`, `progress.*`, `meta.surfaces.*`, `artifact_ids[]`, and
   `allowedActions.canCancel`
4. use the existing BFF client only; do not add raw fetches or mock/demo data
5. if any required field is missing from the live response, stop and emit
   `.coordination/requests/RW-04-experiment-launch-bff-gap.yaml`
6. if the implementation is completed cleanly, emit
   `.coordination/requests/RW-04-experiment-launch-ui-done.yaml`
   together with the required feedback docs named in `lovable-ui-task.yaml`

Required frontend feedback outputs after completion:

- `docs/pantheon-feedback/RW-04-experiment-launch/LOVABLE_CHANGE_FEEDBACK.md`
- `docs/pantheon-feedback/RW-04-experiment-launch/API_GAP_REQUESTS.json`
- `docs/pantheon-feedback/RW-04-experiment-launch/UI_DECISIONS.md`
- `docs/pantheon-feedback/RW-04-experiment-launch/QA_STATUS.md`

---

## 6. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only this sidecar acceptance file is added |
| No canonical truth edited | PASS | No L0/L1 policy docs, coordination payloads, runtime files, or frontend source files changed |
| Parent dependency is truly satisfied | PASS | `EXEC-REBASE-RW04-001` archived as `done` with review notes confirming a self-contained frontend handoff bundle |
| Upstream contract and BFF truth are explicit | PASS | `RW-04-EXPERIMENT-001` and `AUTO-IMPL-RW04-001` archived as `done` and aligned with the published bundle |
| Packet stays in support-only scope | PASS | Content is limited to readiness, dependency mapping, and handoff guardrails |
| Reviewer can use this as a start packet for the parent task | PASS | Packet identifies exact ready inputs, exact stop path, and exact completion outputs |

---

## 7. Handoff to Reviewer (`Copilot`)

This sidecar is ready for review as the acceptance packet for
`EXEC-FRONT-RW04-001`.

What it gives you:

1. a single-page confirmation that RW-04 is already route-live and handoff-ready
2. the dependency chain from published contract -> live BFF -> refreshed frontend
   bundle -> expected frontend return files
3. the exact guardrails that prevent fake run progress, fake history, or
   client-invented cancel authority

Recommended reviewer stance:

1. approve this sidecar if it matches the repo's current RW-04 readiness state
2. use it as the acceptance checklist for starting the parent frontend task
3. keep the parent work scoped to frontend implementation and returned handoff
   files, not renewed BFF / contract redesign

---
*Generated by Codex as a sidecar `acceptance_packet` helper for `EXEC-FRONT-RW04-001`. This file is a support artifact and does not modify canonical truth.*
