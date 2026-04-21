# EXEC-REBASE-TW03-001 BFF and Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`  
**Parent task:** `EXEC-REBASE-TW03-001` - Refresh TW-03 before-after compare frontend handoff and coordination bundle  
**Parent owner:** `Codex`  
**Parent reviewer:** `Copilot`  
**Parent status:** `review`  
**Sidecar owner:** `Codex`  
**Sidecar reviewer:** `Claude`  
**Date:** `2026-04-21`  
**Mutates canonical:** `no`

> Support artifact only. This packet does not reopen canonical handoff edits,
> modify runtime behavior, or change L1/L2 truth. It packages the current
> TW-03 route-live state, the frontend consume rules that are already settled,
> and the remaining drift or verification caveats that the parent reviewer
> should see before absorbing the main-lane work.

---

## 1. Executive Summary

`EXEC-REBASE-TW03-001` is not missing a BFF route or a frontend handoff bundle
anymore.

What is already true in the repo:

- `GET /api/v1/trainer/sessions/{session_id}/preview` is live in
  `services/control-plane/bff/main.py:5244-5283`.
- `POST /api/v1/trainer/sessions/{session_id}/preview` is live in
  `services/control-plane/bff/main.py:5285-5331`.
- `ReadSurfaceStore` already projects the compare payload, warning ordering,
  `preview_unavailable` degraded branch, refresh authority, and polling
  semantics in `services/control-plane/bff/read_store.py:6939-7217`.
- the TW-03 handoff bundle already exists:
  - `docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md`
  - `.coordination/responses/TW-03-before-after-compare-contract-ready.yaml`
  - `.coordination/responses/TW-03-before-after-compare-lovable-ui-task.yaml`
  - `.coordination/responses/TW-03-before-after-compare-lovable-prompt.md`
  - `.coordination/requests/TW-03-before-after-compare-bff-gap.example.yaml`
  - `.coordination/requests/TW-03-before-after-compare-ui-done.example.yaml`
- `docs/screens/TW-03-before-after-compare.md`,
  `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md`, and
  `docs/lovable/PANTHEON_FRONTEND_SA.md` now describe TW-03 as route-live and
  frontend-ready.

What still needs reviewer attention:

- `WORKBENCH_DELIVERY_BACKLOG.md` still says
  `route-live - frontend handoff activation pending` and still tells the next
  step to publish the bundle even though the bundle and ready dispatch packet
  already exist.
- `docs/examples/TW-03-before-after-compare.json` still carries
  `_note/_packet_status = contract-published`.
- the targeted TW-03 contract test is currently not replay-stable on
  `2026-04-21`: the seeded pending preview has an expired `deadline_at`, so the
  route intentionally converts the response to `preview_unavailable` and one
  assertion fails.

Bounded conclusion:

- do not reopen TW-03 route implementation work
- do not reopen TW-03 handoff-bundle creation work
- do treat backlog wording drift and the expired pending-preview proof as the
  only remaining review-facing concerns

## 2. Current Repo Truth Snapshot

| Area | Current truth | Notes |
|---|---|---|
| Parent lane | `ai-status.json` task `EXEC-REBASE-TW03-001` is in `review` | sidecar does not change parent ownership or canonical files |
| Canonical BFF contract | `docs/bff/TW-03-before-after-compare.md` | route family and consume rules are published |
| Live read route | `services/control-plane/bff/main.py:5244-5283` | returns preview payload or structured `preview_unavailable` success body |
| Live refresh route | `services/control-plane/bff/main.py:5285-5331` | validates `refresh_mode = manual`, reuses pending evals, enforces refresh authority |
| Compare projection logic | `services/control-plane/bff/read_store.py:6939-7217` | backend owns warning ordering, `allowedActions.canRefreshPreview`, polling windows, and degraded copy |
| Screen spec | `docs/screens/TW-03-before-after-compare.md` | now says route-live and build-now |
| Frontend change spec | `docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md` | production frontend contract exists |
| Contract-ready record | `.coordination/responses/TW-03-before-after-compare-contract-ready.yaml` | `status: live` |
| Frontend dispatch packet | `.coordination/responses/TW-03-before-after-compare-lovable-ui-task.yaml` | `status: ready` |
| Frontend prompt | `.coordination/responses/TW-03-before-after-compare-lovable-prompt.md` | points at the same route family and same handoff templates |
| BFF-gap template | `.coordination/requests/TW-03-before-after-compare-bff-gap.example.yaml` | template exists; no real TW-03 gap request is open |
| UI-done template | `.coordination/requests/TW-03-before-after-compare-ui-done.example.yaml` | template exists; no real returned frontend completion handoff yet |
| Packet-family sync | `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md` | TW-03 row is route-live and frontend handoff ready |
| Frontend SA sync | `docs/lovable/PANTHEON_FRONTEND_SA.md` | TW-03 is route-live in both route inventory and Trainer section |
| Backlog sync | `WORKBENCH_DELIVERY_BACKLOG.md:98` | still stale; says activation pending and bundle publication still needed |
| Example metadata | `docs/examples/TW-03-before-after-compare.json` | response shapes exist, but top-level metadata still says contract-published |
| Returned frontend loop | none yet | no real `.coordination/requests/TW-03-before-after-compare-ui-done.yaml` or frontend feedback bundle exists |

## 3. Source References

| Source | Why it matters |
|---|---|
| `docs/bff/TW-03-before-after-compare.md` | canonical read/refresh route family, warning ladder, degraded branch, and polling contract |
| `docs/screens/TW-03-before-after-compare.md` | page-level route-live rendering rules |
| `docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md` | frontend implementation target and failure/completion rules |
| `.coordination/responses/TW-03-before-after-compare-contract-ready.yaml` | durable `status: live` handoff truth |
| `.coordination/responses/TW-03-before-after-compare-lovable-ui-task.yaml` | durable `status: ready` frontend dispatch truth |
| `.coordination/responses/TW-03-before-after-compare-lovable-prompt.md` | same constraints and same endpoint scope in prompt form |
| `.coordination/requests/TW-03-before-after-compare-bff-gap.example.yaml` | escalation template if live payload diverges |
| `.coordination/requests/TW-03-before-after-compare-ui-done.example.yaml` | completion template for first real frontend return |
| `services/control-plane/bff/main.py:5244-5331` | live GET/POST route handlers |
| `services/control-plane/bff/read_store.py:6939-7217` | preview projection, deadline handling, degraded copy, and refresh semantics |
| `services/control-plane/bff/test_tw03_before_after_compare_contract.py` | executable proof, plus the current time-sensitive failure on the pending branch |
| `WORKBENCH_DELIVERY_BACKLOG.md:98` | still-stale readiness wording that the parent review should notice |
| `docs/examples/TW-03-before-after-compare.json` | example payload shapes are useful, but metadata still reflects older readiness text |

## 4. Verification Replayed For This Sidecar

On `2026-04-21`, this support slice re-ran:

- `pytest -q services/control-plane/bff/test_tw03_before_after_compare_contract.py`
- result: `1 failed, 3 passed`
- failure: `test_tw03_pending_preview_supports_eval_lookup_and_polling_contract`
  expected `status = pending`, but the live projection returned
  `preview_unavailable`
- reason: the seeded pending record still uses
  `deadline_at = 2026-04-20T19:50:45Z`, and
  `ReadSurfaceStore._project_trainer_preview_payload(...)` intentionally
  converts expired pending evaluations into the structured unavailable branch
- `python3 -m json.tool docs/examples/TW-03-before-after-compare.json`
- result: parses cleanly

Interpretation:

- this is not evidence of a missing TW-03 route
- this is evidence that the pending-eval proof is time-sensitive and no longer
  green as of `2026-04-21`
- the parent reviewer should treat this as a verification caveat or follow-up,
  not as proof that the handoff bundle is missing

## 5. BFF Query-Gap Classification

| Item | State | Why |
|---|---|---|
| TW-03 route family | closed | both preview GET and refresh POST routes are live in `main.py` |
| Warning hierarchy ownership | closed | `read_store.py` sorts and counts warnings backend-side |
| `preview_unavailable` degraded contract | closed | unavailable branch is explicit and structured |
| Polling contract | closed | interval, max wait, and deadline are projected backend-side |
| Frontend handoff bundle | closed | change spec, prompt, ready dispatch packet, and both request templates exist |
| Active Pantheon-side BFF gap | none open | only `.example.yaml` escalation templates exist |
| Returned frontend loop | not started | no real `ui-done` or frontend feedback record has been returned yet |
| Backlog readiness wording | open | `WORKBENCH_DELIVERY_BACKLOG.md` still understates TW-03 handoff completion |
| Example metadata wording | open | example file still announces contract-published instead of route-live support truth |
| Pending-preview proof stability | open | the targeted contract test currently fails because the seeded deadline expired |

Bounded conclusion:

- TW-03 no longer has a route gap
- TW-03 no longer has a missing handoff-bundle gap
- the only remaining review-facing issues are narrative drift and a
  time-sensitive verification fixture

## 6. Truthful Operator and Frontend Journey

### 6.1 Open the compare surface

```text
Operator opens /trainer/sessions/:session_id/compare
    |
    v
GET /api/v1/trainer/sessions/{session_id}/preview
    |
    +-- 200 complete
    |     render compare header, summary, metric deltas, warning rail,
    |     and control diff from the backend payload
    |
    +-- 200 pending
    |     render pending state and use backend polling contract only
    |
    +-- 200 preview_unavailable
          render canonical degraded copy; do not invent compare results
```

### 6.2 Poll only when the backend says a preview is pending

```text
status = pending and polling.enabled = true
    |
    v
GET /api/v1/trainer/sessions/{session_id}/preview?eval_id={eval_id}
    |
    +-- pending before deadline
    |     keep polling at poll_interval_ms
    |
    +-- complete / failed / preview_unavailable
          stop polling immediately
```

Rules already settled by the live handoff:

- poll only the GET preview route with `eval_id`
- do not poll the POST refresh route
- do not continue polling after `polling.deadline_at`
- if the backend resolves an expired pending preview to `preview_unavailable`,
  the UI must accept that as authoritative

### 6.3 Refresh only when the backend authorizes it

```text
Render Refresh CTA
    only when allowedActions.canRefreshPreview === true
        |
        v
POST /api/v1/trainer/sessions/{session_id}/preview
  { "refresh_mode": "manual" }
```

Rules already settled by the live handoff:

- `allowedActions.canRefreshPreview` is the only refresh CTA authority signal
- do not infer refresh authority from session status, metric presence, or
  staleness alone
- if an evaluation for the same candidate snapshot is already pending, the BFF
  may return that existing pending preview instead of creating a new one

### 6.4 Escalate or complete through the existing templates

If the live payload diverges from the synced contract:

- write `.coordination/requests/TW-03-before-after-compare-bff-gap.yaml`
- start from `.coordination/requests/TW-03-before-after-compare-bff-gap.example.yaml`

If the frontend implementation is complete:

- write `.coordination/requests/TW-03-before-after-compare-ui-done.yaml`
- start from `.coordination/requests/TW-03-before-after-compare-ui-done.example.yaml`

## 7. Frontend Consume Rules Already Settled

The reviewer should treat these as already-settled handoff truth, not as new
scope:

- use the dedicated preview route family only
- use the existing BFF client only; do not add raw `fetch` in components
- do not derive metric deltas from TW-02 patch responses or local simulation
- do not derive warning severity from message copy or metric direction
- do not treat `preview_unavailable` as loading
- do not keep polling after `polling.deadline_at`
- do not show refresh controls when `allowedActions.canRefreshPreview` is false
- if any required field is missing, emit the TW-03 `bff-gap` handoff instead of
  mocking compare data

## 8. Residual Drift And Verification Caveat

### DRIFT-TW03-001 - Backlog row still says activation pending

Evidence:

- `WORKBENCH_DELIVERY_BACKLOG.md:98` still says
  `route-live - frontend handoff activation pending`
- the same row still says the frontend handoff bundle and canonical
  coordination wording need to be completed before the UI lane starts

Impact:

- a reader following the backlog alone can conclude TW-03 still lacks the
  bundle that already exists
- this can misroute work back into handoff publication even though the actual
  next step is frontend execution

Disposition:

- narrative drift only
- relevant to parent review because backlog sync was part of the main-lane
  acceptance

### DRIFT-TW03-002 - Example payload metadata still says contract-published

Evidence:

- `docs/examples/TW-03-before-after-compare.json` still contains:
  - `_note: "Contract-published example payloads ..."`
  - `_packet_status: "contract-published"`

Impact:

- the payload body is useful and aligned, but a reader opening only the file
  header can infer an older readiness state than the contract-ready packet and
  frontend dispatch packet now claim

Disposition:

- non-runtime narrative drift
- safe for parent-lane cleanup if desired

### CAVEAT-TW03-003 - Pending-preview proof is time-sensitive and currently red

Evidence:

- `services/control-plane/bff/test_tw03_before_after_compare_contract.py:88-102`
  still expects `teval-20260419-015` to remain `pending`
- the seed data in `services/control-plane/bff/data/read_surfaces.json:291-324`
  uses `deadline_at: 2026-04-20T19:50:45Z`
- `services/control-plane/bff/read_store.py:6949-6967` intentionally converts
  expired pending previews into `preview_unavailable`
- re-running the test on `2026-04-21` yields `1 failed, 3 passed`

Impact:

- the live TW-03 route exists, but the targeted proof suite is no longer
  replay-stable for the pending branch
- a reviewer relying on "test file exists" alone will miss that the current
  assertion is stale in time

Disposition:

- verification caveat
- not evidence of a missing route or missing handoff packet
- parent reviewer should decide whether to request a proof refresh before
  approving the main lane

## 9. Reviewer Focus

For `Claude` reviewing this sidecar:

1. Confirm the packet stays support-only and does not mutate canonical truth.
2. Confirm TW-03 is classified as `no open BFF query gap` and
   `no missing handoff-bundle gap`.
3. Confirm the two remaining drift items are backlog/example wording, not route
   absence.
4. Confirm the pending-preview test failure is documented as a time-based proof
   caveat rather than silently ignored.
5. Use this packet as reviewer context for the parent task, not as a
   replacement for canonical rebaseline work.

If those points hold, this sidecar is ready to move to review and serve as the
bounded TW-03 support reference.
