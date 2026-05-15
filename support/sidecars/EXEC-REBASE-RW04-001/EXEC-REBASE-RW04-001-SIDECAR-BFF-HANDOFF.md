# EXEC-REBASE-RW04-001 BFF and Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`
**Parent task:** `EXEC-REBASE-RW04-001` - Refresh RW-04 experiment launch frontend handoff and coordination bundle
**Parent owner:** `Codex`
**Parent reviewer:** `Codex2`
**Parent terminal status:** `done`
**Sidecar owner:** `Codex`
**Sidecar reviewer:** `Claude`
**Date:** `2026-04-21`
**Mutates canonical:** `no`

> Support artifact only. This packet does not reopen the archived parent task,
> change canonical truth, or modify runtime, registry, governance, or main BFF
> behavior. It consolidates the final RW-04 route-live handoff state, the
> frontend consume rules, and the remaining non-blocking drift so the reviewer
> and any later parent-lane consumer have one bounded reference.

---

## 1. Executive Summary

`EXEC-REBASE-RW04-001` is already closed as a route-live handoff-activation
task. The important support-lane conclusion is:

- RW-04 is not a BFF query-gap anymore.
- The launch/history/detail/cancel route family is live.
- The frontend handoff bundle is published and self-contained.
- The coordination bundle is ready for the frontend lane.
- No returned frontend loop exists yet; only the example `bff-gap` and
  `ui-done` templates are present.

This means the next real execution step belongs to the frontend lane
(`EXEC-FRONT-RW04-001`), not to another Pantheon-side BFF or handoff repair
slice.

## 2. Current Repo Truth Snapshot

| Area | Current truth | Notes |
|---|---|---|
| Parent closeout | `ai-task-archive/tasks/EXEC-REBASE-RW04-001.json` | parent archived `done` with final review already recorded |
| Canonical BFF contract | `docs/bff/RW-04-experiment-launch.md` | marks all four routes live |
| Frontend handoff bundle | `docs/pantheon-handoffs/RW-04-experiment-launch/FRONTEND_CHANGE_SPEC.md` | production UI may proceed against the live route family |
| Contract-ready record | `.coordination/responses/RW-04-experiment-launch-contract-ready.yaml` | `status: live` |
| Frontend dispatch packet | `.coordination/responses/RW-04-experiment-launch-lovable-ui-task.yaml` | `status: ready` |
| Frontend prompt | `.coordination/responses/RW-04-experiment-launch-lovable-prompt.md` | uses the same route family and same handoff paths |
| BFF-gap template | `.coordination/requests/RW-04-experiment-launch-bff-gap.example.yaml` | template exists; no real RW-04 gap request is open |
| UI-done template | `.coordination/requests/RW-04-experiment-launch-ui-done.example.yaml` | template exists; no real returned UI completion handoff yet |
| Executable proof | `services/control-plane/bff/test_rw04_experiment_launch_contract.py` | covers launch, list, detail, cancel, and no-fallback round trip |
| Readiness sync | `WORKBENCH_DELIVERY_BACKLOG.md`, `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`, `docs/pantheon-handoffs/LOVABLE_MASTER_SA.md`, `docs/lovable/PANTHEON_FRONTEND_SA.md` | all already describe RW-04 as route-live / ready |

## 3. Source References

| Source | Why it matters |
|---|---|
| `docs/reviews/2026-04-20-exec-rebase-rw04-001-codex2-review.md` | reviewer-approved proof that the handoff bundle and coordination packet are self-contained |
| `ai-task-archive/tasks/EXEC-REBASE-RW04-001.json` | archived parent closeout, final delivery metadata, and handoff history |
| `docs/bff/RW-04-experiment-launch.md` | canonical route family, lifecycle, cancel authority, and degradation semantics |
| `docs/pantheon-handoffs/RW-04-experiment-launch/FRONTEND_CHANGE_SPEC.md` | frontend implementation target and consume rules |
| `.coordination/responses/RW-04-experiment-launch-contract-ready.yaml` | durable `status: live` coordination truth |
| `.coordination/responses/RW-04-experiment-launch-lovable-ui-task.yaml` | durable `status: ready` frontend dispatch truth |
| `.coordination/responses/RW-04-experiment-launch-lovable-prompt.md` | same constraints, same route family, same escalation paths |
| `.coordination/requests/RW-04-experiment-launch-bff-gap.example.yaml` | escalation template if live payload diverges |
| `.coordination/requests/RW-04-experiment-launch-ui-done.example.yaml` | completion template for the first real frontend return |
| `docs/examples/RW-04-experiment-launch.json` | example payloads for launch, history, detail, and cancel branches |
| `services/control-plane/bff/test_rw04_experiment_launch_contract.py` | executable route proof and authority invariants |

## 4. BFF Query-Gap Classification

| Item | State | Why |
|---|---|---|
| RW-04 route family | closed | `POST /api/v1/experiments/launch`, `GET /api/v1/experiments`, `GET /api/v1/experiments/{experiment_id}`, and `POST /api/v1/experiments/{experiment_id}/cancel` are live |
| Frontend handoff bundle | closed | change spec, prompt, and lovable task all exist and agree on the same route family |
| Coordination template coverage | closed | both example request templates now exist and match the dispatch packet |
| Active Pantheon-side BFF gap | none open | only `.example.yaml` templates exist; there is no active RW-04 gap request to resolve |
| Returned frontend loop | not started | there is no real `.coordination/requests/RW-04-experiment-launch-ui-done.yaml` yet |
| Non-blocking narrative drift | minor | `docs/examples/RW-04-experiment-launch.json` still carries `_packet_status: "contract-published"` even though the route family is live |

Bounded conclusion:

- do not reopen Pantheon BFF implementation work for RW-04
- do not treat the absence of a real `ui-done` file as a backend blocker
- route the next action to the frontend owner using the existing handoff bundle

## 5. Truthful Operator and Frontend Journey

### 5.1 Launch a run

```text
Operator opens /research/experiments/launch
    |
    v
Submits POST /api/v1/experiments/launch
    |
    +-- 200
    |     use returned experiment_id as the canonical run key
    |     show status=queued and queued_at exactly as returned
    |
    +-- 422
          render validation failure only; do not invent a run record
```

### 5.2 Observe history and detail

```text
Operator opens /research/experiments
    |
    v
GET /api/v1/experiments
    |
    +-- render backend-owned summaries and pagination only
    |
    +-- row click
          GET /api/v1/experiments/{experiment_id}
```

The detail payload is the only authority for:

- `progress.percent`, `progress.phase`, `progress.message`
- `validation_warnings[]`
- `artifact_ids[]`
- `failure.reason_code` and `failure.message`
- `allowedActions.canCancel`
- `meta.surfaces.experiment_status`

### 5.3 Cancel correctly

```text
Render Cancel CTA
    only when allowedActions.canCancel === true
    and the current detail surface is not unavailable
        |
        v
POST /api/v1/experiments/{experiment_id}/cancel
        |
        v
Re-fetch detail or accept the returned canceled receipt
```

Rules that must remain backend-owned:

- terminal runs are never cancelable
- the frontend must not infer cancel authority from `status` alone
- stale local UI state loses to the next authoritative payload

### 5.4 Handle drift or completion through coordination artifacts

If the live payload is missing required fields or diverges from the synced
contract:

- write `.coordination/requests/RW-04-experiment-launch-bff-gap.yaml`
- start from `.coordination/requests/RW-04-experiment-launch-bff-gap.example.yaml`

If the frontend implementation is complete:

- write `.coordination/requests/RW-04-experiment-launch-ui-done.yaml`
- start from `.coordination/requests/RW-04-experiment-launch-ui-done.example.yaml`

## 6. Frontend Consume Rules

The reviewer and parent-lane consumer should treat these rules as already-settled
truth, not new scope.

- Use the existing BFF client only; do not add raw `fetch` in component files.
- Launch only through `POST /api/v1/experiments/launch`.
- Poll or refresh detail through `GET /api/v1/experiments/{experiment_id}`; do
  not synthesize progress from timers.
- Render the status vocabulary exactly: `queued`, `running`, `completed`,
  `failed`, `canceled`.
- Treat `allowedActions.canCancel` as the only cancel CTA authority signal.
- Treat `meta.surfaces.experiment_history` and
  `meta.surfaces.experiment_status` as banner/degradation signals, not as proof
  of executor liveness.
- Render `artifact_ids[]` as a backend-owned ledger; do not invent artifact
  detail URLs beyond the published links.
- If any required field is absent, emit a BFF-gap handoff instead of mocking or
  backfilling state.

## 7. Residual Drift That Does Not Reopen Mainline

### DRIFT-RW04-001 — Example payload metadata still says contract-published

Evidence:

- `docs/examples/RW-04-experiment-launch.json` still contains
  `_packet_status: "contract-published"`.

Impact:

- a reader opening only the example file can infer an older readiness state than
  the actual contract-ready packet, handoff bundle, and review record.

Disposition:

- non-blocking
- not grounds to reopen `EXEC-REBASE-RW04-001`
- safe for a later parent-lane cleanup if someone wants the example metadata to
  match the already-live route family

## 8. Reviewer Focus

For `Claude` reviewing this sidecar:

1. Confirm the packet stays support-only and does not mutate canonical truth.
2. Confirm RW-04 is classified as `no open BFF query gap`.
3. Confirm the next real step is frontend execution, not Pantheon-side repair.
4. Confirm the only residual issue recorded here is minor narrative drift, not a
   reopened route-family defect.

If those points hold, this packet is ready to move to review and serve as the
bounded handoff reference for later frontend activation or reviewer context.
