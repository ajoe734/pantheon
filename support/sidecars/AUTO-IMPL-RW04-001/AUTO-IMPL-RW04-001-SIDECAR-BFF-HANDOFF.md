# AUTO-IMPL-RW04-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `AUTO-IMPL-RW04-001` — Implement RW-04 experiment launch route family  
**Parent Owner**: `Claude`  
**Parent Reviewer**: `Codex`  
**Parent Status**: `done`  
**Sidecar Task**: `AUTO-IMPL-RW04-001-SIDECAR-BFF-HANDOFF`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: `2026-04-20`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance / main BFF implementations.
> It packages the current RW-04 implementation state into a reviewer-ready
> handoff packet for parent-owner absorption.

---

## 1. Purpose

`AUTO-IMPL-RW04-001` already landed the RW-04 Experiment Launch route family and
closed the production-path regression that previously broke launch/list/detail/
cancel when `allow_local_snapshot_fallback=false`.

This sidecar exists to:

- summarize what is now live in the BFF
- record the remaining query / wiring caveats without reopening canonical truth
- map the truthful operator journey for launch, history, detail, and cancel
- give frontend and reviewer lanes one compact consume-rule packet

---

## 2. Current Slice State

| Item | Value |
|---|---|
| Module | `RW-04 Experiment Launch` |
| Canonical contract | `docs/bff/RW-04-experiment-launch.md` |
| Example payload | `docs/examples/RW-04-experiment-launch.json` |
| BFF route state | implemented in `services/control-plane/bff/main.py` |
| Read-store state | implemented in `services/control-plane/bff/read_store.py` |
| Parent closeout | archived as done at commit `b8756a8` |
| Regression status | production-path round trip fixed and covered |
| Sidecar conclusion | route gap is closed; remaining caveats are execution-wiring and truth-hardening follow-up, not missing RW-04 endpoints |

---

## 3. Source References

| Source | Why it matters |
|---|---|
| `docs/bff/RW-04-experiment-launch.md` | canonical route family, state machine, cancel authority, degradation semantics |
| `docs/examples/RW-04-experiment-launch.json` | canonical payload examples for launch, history, detail, terminal, and cancel branches |
| `docs/reviews/2026-04-19-rw-04-experiment-launch-review.md` | contract review notes and invariants that implementation must preserve |
| `ai-task-archive/tasks/AUTO-IMPL-RW04-001.json` | parent implementation closeout, production-path bug, and final review evidence |
| `services/control-plane/bff/main.py:6144-6396` | live RW-04 route handlers, request validation, links, and cancel preconditions |
| `services/control-plane/bff/read_store.py:4143-4336` | experiment projection, store selection, create/cancel persistence, and `canCancel` projection |
| `services/control-plane/bff/test_rw04_experiment_launch_contract.py` | executable proof for launch, list, detail, cancel, state-machine invariants, and non-fallback regression |

---

## 4. Live BFF Inventory

### 4.1 Route family

All four expected routes are live:

| Route | Method + Path | Live behavior |
|---|---|---|
| Launch | `POST /api/v1/experiments/launch` | validates required body fields, creates a durable queued record, returns canonical links and `allowedActions.canCancel=true` |
| History | `GET /api/v1/experiments` | supports `ticket_id`, `status`, `page_token`, `page_size`; returns paginated experiment summaries |
| Detail | `GET /api/v1/experiments/{experiment_id}` | returns full run detail, progress, warnings, artifacts, failure block, links, and `meta.surfaces.experiment_status` |
| Cancel | `POST /api/v1/experiments/{experiment_id}/cancel` | requires non-empty `reason`, rejects terminal runs, returns terminal `canceled` receipt with `allowedActions.canCancel=false` |

All read paths require a read-capable operator token through `_require_read_role`.

### 4.2 Request / filter semantics now enforced

| Input | Accepted values | Failure mode |
|---|---|---|
| `run_config.execution_mode` | `paper`, `backtest`, `simulation` | `422 INVALID_PARAMS` |
| `run_config.priority` | `normal`, `high` | `422 INVALID_PARAMS` |
| `status` query | `queued`, `running`, `completed`, `failed`, `canceled` | `422 INVALID_PARAMS` |
| `reason` on cancel | non-empty string | `422 INVALID_PARAMS` |
| cancel on terminal run | not allowed | `409 INVALID_STATE` |
| missing experiment id | not found | `404 OBJECT_NOT_FOUND` |

### 4.3 State-machine invariants preserved by implementation

- `queued -> canceled` is supported directly by the cancel route.
- `queued` and `running` are the only cancelable states.
- `completed`, `failed`, and `canceled` always project `allowedActions.canCancel=false`.
- repeated cancel against a terminal run is rejected with `409`, not silently accepted.

---

## 5. Remaining Query and Wiring Gaps

These are support-lane findings only. They do not reopen the parent task's
accepted route-family implementation.

### GAP-RW04-001 — Launch creates durable queued state, but this slice does not enqueue a real executor job

**Evidence**: `main.py:6228-6261` delegates to `read_store.create_research_experiment(...)`;  
`read_store.py:4269-4312` creates a record with `status="queued"` and saves it.

**Current behavior**:

- Launch persists a queued experiment record.
- No RW-04-specific queue publish, worker wake-up, or runtime-owned progress update is triggered in this BFF slice.
- Newly launched runs remain `queued` until some other writer mutates the experiment record.

**Frontend implication**:

- treat `queued` as truthful durable state, not as proof that execution has actually started
- never invent a progress bar or auto-advance the state machine from elapsed time
- polling may continue, but the UI must tolerate long-lived `queued` without treating it as a client bug

### GAP-RW04-002 — Explicit service-store split would diverge from BFF-local writes

**Evidence**: `read_store.py:4221-4232` reads from `ServiceBackedReadAdapter` when
`research_experiments` resolves to a service-backed path; `create_research_experiment`
and `cancel_research_experiment` at `4269-4336` only mutate `self._data` and then call
`_save()` on the BFF snapshot file.

**Current behavior**:

- The parent fix correctly covers the default production path where the same snapshot file
  is used for both read and write.
- If `PANTHEON_BFF_RESEARCH_EXPERIMENT_STORE` is later pointed at a separate service-owned
  file, list/detail will prefer that external store while launch/cancel still write only to
  the BFF snapshot payload.

**Impact**:

- a newly launched or canceled run could disappear from history/detail if read traffic is
  served from a different explicit store than the one the BFF writes

**Parent-owner follow-up**:

- if RW-04 graduates to a true service-owned experiment store, write paths must either
  target that store directly or be mirrored into it before the explicit env var is enabled

### GAP-RW04-003 — `meta.surfaces.*` reflects read-surface freshness, not executor liveness

**Evidence**: `main.py:6166` maps RW-04 surface state through `_rw01_surface_state`;  
`main.py:3425-3439` turns `local_snapshot` into `degraded` and stale dataset status into `fresh/stale/unavailable`.

**Current behavior**:

- `meta.surfaces.experiment_history` and `meta.surfaces.experiment_status` report whether the
  experiment dataset is readable and how fresh that read surface is
- they do not assert that an async worker is healthy, subscribed, or currently processing the run

**Frontend implication**:

- do use `meta.surfaces.*` for stale / degraded / unavailable banners
- do not read `fresh` as "the backend executor is actively progressing this run"

### GAP-RW04-004 — Cancel reason is validated for command authority but not returned in read models

**Evidence**: `main.py:6378-6396` requires `reason`; `read_store.py:4314-4336` does not persist
that reason into the projected experiment model.

**Impact**:

- current RW-04 UI can submit an operator-authored reason, but cannot later re-read that note
  from history/detail

**Scope note**:

- this is not a contract violation because the published cancel response does not require reason echo
- record it here so frontend does not assume a post-cancel audit note will appear automatically

---

## 6. Truthful Operator Journey

### 6.1 Launch flow

```text
Operator opens the experiment launch screen
    |
    v
Collects:
  ticket_id
  experiment_name
  strategy_selector
  parameter_set
  run_config
  optional launch_context.analysis_refs[]
    |
    v
POST /api/v1/experiments/launch
    |
    +-- 200
    |     returns experiment_id, status=queued, queued_at,
    |     allowedActions.canCancel=true, links.self, links.workbench_detail
    |     frontend stores experiment_id as the canonical run key
    |
    +-- 422
          inline validation only; do not synthesize a run record client-side
```

### 6.2 History and detail flow

```text
History page load
    |
    v
GET /api/v1/experiments?[ticket_id][status][page_token][page_size]
    |
    +-- data[] summaries
    |     render backend order and backend links only
    |
    +-- meta.surfaces.experiment_history = degraded/stale
    |     keep last-known rows visible with banner
    |
    +-- meta.surfaces.experiment_history = unavailable
          show unavailable state; do not claim empty history

Row click or post-launch redirect
    |
    v
GET /api/v1/experiments/{experiment_id}
    |
    +-- status = queued/running
    |     show live run detail; cancel CTA only from allowedActions.canCancel
    |
    +-- status = completed/failed/canceled
    |     show terminal detail; artifact_ids and failure are backend-authored
    |
    +-- meta.surfaces.experiment_status != fresh
          banner only; do not invent state changes
```

### 6.3 Cancel flow

```text
Operator presses cancel only when allowedActions.canCancel=true
    |
    v
POST /api/v1/experiments/{experiment_id}/cancel
{ "reason": "..." }
    |
    +-- 200
    |     returns status=canceled, completed_at, canCancel=false
    |     UI must flip CTA off immediately from authoritative response
    |
    +-- 409
          experiment already terminal or no longer cancelable;
          refresh detail/history instead of retry loop
```

---

## 7. Frontend Handoff Rules

### 7.1 What frontend can assume now

- the RW-04 route family exists and is callable
- `experiment_id` is the canonical run identity and must be taken from backend payloads
- history rows and detail payloads already carry the `allowedActions.canCancel` authority bit
- detail payloads may include `progress`, `validation_warnings`, `artifact_ids`, and `failure`
- terminal cancel authority is already enforced and test-covered

### 7.2 What frontend must not do

- do not derive cancel authority from `status`; always read `allowedActions.canCancel`
- do not invent progress percent or phase when `progress.*` is null
- do not infer history from live worker presence or local in-memory pending jobs
- do not construct URLs from `ticket_id` or `experiment_id`; use `links.*`
- do not treat `meta.surfaces.* = unavailable` as a legitimate empty state
- do not assume cancel reason will come back in history or detail

### 7.3 Render checklist

- [ ] Use `POST /api/v1/experiments/launch` for creation, not local form-only optimistic runs.
- [ ] After a successful launch, key the screen on returned `experiment_id`.
- [ ] Use `GET /api/v1/experiments` for history refresh and pagination.
- [ ] Preserve backend row order; the BFF already sorts newest `queued_at` first.
- [ ] Use `GET /api/v1/experiments/{experiment_id}` as the source of truth for detail and polling.
- [ ] Only show cancel CTA when `allowedActions.canCancel=true`.
- [ ] On `queued`, show queued / waiting copy rather than a fake running indicator.
- [ ] On `failed`, render `failure.reason_code` and `failure.message` if present.
- [ ] On `completed`, treat `artifact_ids[]` as durable outputs for downstream RW-05 entry points.
- [ ] On `degraded` or `stale`, keep last-known data but display banner copy.
- [ ] On `unavailable`, suppress authoritative history/detail claims and show unavailable state.

### 7.4 Suggested screen mapping

| Screen responsibility | BFF source |
|---|---|
| Launch form submit | `POST /api/v1/experiments/launch` |
| History list | `GET /api/v1/experiments` |
| Detail drawer / page | `GET /api/v1/experiments/{experiment_id}` |
| Cancel action | `POST /api/v1/experiments/{experiment_id}/cancel` |
| Ticket back-link | `links.linked_ticket_detail` from detail payload |
| Workbench route transition | `links.workbench_detail` from launch/history/detail payloads |

---

## 8. Verification Snapshot

Local verification for the landed route family:

```bash
pytest services/control-plane/bff/test_rw04_experiment_launch_contract.py
```

This suite covers:

- launch happy path and validation failures
- history list shape, filters, pagination envelope, and links
- detail contract for running / completed / failed / missing cases
- cancel for queued and running runs
- terminal cancel rejection and repeat-cancel rejection
- non-fallback production-path round trip for launch -> list -> detail -> cancel

---

## 9. Reviewer / Parent Owner Notes

This packet should be read as a compact post-implementation support artifact:

- the RW-04 endpoint family is live and the prior production-path bug is closed
- frontend can now integrate against real BFF routes instead of `pending-bff` placeholders
- the remaining work is not "implement missing RW-04 routes" but rather:
  executor wiring, service-store ownership alignment, and downstream truth rebaseline

Parent owner can absorb this packet as reviewer context or ignore it without
affecting canonical truth.

---

*Generated by Codex as a sidecar `bff_handoff_packet` helper for `AUTO-IMPL-RW04-001`. This file is a support artifact and does not modify canonical truth.*
