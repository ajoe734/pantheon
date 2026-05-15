# EXEC-FRONT-TW03-001 Acceptance Packet and Dependency Map (Sidecar)

**Parent Task**: `EXEC-FRONT-TW03-001` - Implement the TW-03 before/after compare UI against the live preview routes
**Parent Owner**: `Codex`
**Parent Reviewer**: `Gemini`
**Parent Status**: `in_progress`
**Sidecar Task**: `EXEC-FRONT-TW03-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Claude`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-21`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance / main frontend
> implementations. It packages the current TW-03 parent-task acceptance state,
> dependency chain, and implementation guidance into a reviewer-ready packet.

---

## 1. Executive Summary

`EXEC-FRONT-TW03-001` is the production frontend implementation slice for the
TW-03 Before/After Compare screen. All upstream BFF and handoff dependencies
are satisfied; the front lane can implement against live routes without any
missing contract pieces.

Current state, condensed:

- `TW-01-FOUNDATION-001` is done: trainer session lifecycle, `session_id`, and
  session `status` semantics are canonical.
- `TW-02-CONTROLS-001` is done: backend-authored `previous_value`/`new_value`
  control diff semantics and `allowedActions` authority model are canonical.
- `EXEC-REBASE-TW03-001` is done: the TW-03 handoff bundle has been refreshed
  and confirmed route-live. Both preview routes are confirmed live in the BFF.
- The TW-03 handoff bundle (`FRONTEND_CHANGE_SPEC.md`, `contract-ready.yaml`,
  `lovable-ui-task.yaml`, `lovable-prompt.md`, `bff-gap.example.yaml`,
  `ui-done.example.yaml`) is fully published and current.
- `GET /api/v1/trainer/sessions/{session_id}/preview` is live at
  `services/control-plane/bff/main.py:5459-5497`.
- `POST /api/v1/trainer/sessions/{session_id}/preview` is live at
  `services/control-plane/bff/main.py:5500-5559`.
- The compare projection logic (warning ordering, `preview_unavailable` branch,
  refresh authority, polling semantics) is implemented in
  `services/control-plane/bff/read_store.py:7401-7545`.
- No real TW-03 `ui-done` or feedback bundle has been returned yet — that is
  the expected current state for an `in_progress` frontend task.

This sidecar maps the acceptance criteria, verifies which are pre-satisfied by
upstream truth, and gives the parent owner a precise checklist for what the
front implementation must produce and emit on completion.

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Canonical owner / reviewer / lifecycle truth for the parent task and this sidecar |
| `ai-task-archive/tasks/TW-01-FOUNDATION-001.json` | Archived record: trainer session lifecycle and `session_id` semantics are `done` |
| `ai-task-archive/tasks/TW-02-CONTROLS-001.json` | Archived record: control diff semantics and `allowedActions` authority model are `done` |
| `ai-task-archive/tasks/EXEC-REBASE-TW03-001.json` | Archived record: TW-03 handoff bundle refresh is `done` and route-live |
| `docs/bff/TW-03-before-after-compare.md` | Canonical BFF contract: GET/POST routes, required fields, refresh invariants, warning hierarchy |
| `docs/screens/TW-03-before-after-compare.md` | Page-level route-live rendering rules, section breakdown, readiness gate |
| `docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md` | Frontend implementation target: allowed APIs, required UI modules, state rules, failure rules, degradation rules, polling contract, completion rules |
| `.coordination/responses/TW-03-before-after-compare-contract-ready.yaml` | Published `status: live` handoff truth — `bff_route_live_at: 2026-04-21T06:22:46Z` |
| `.coordination/responses/TW-03-before-after-compare-lovable-ui-task.yaml` | Frontend dispatch packet: allowed endpoints, constraints, acceptance items, required feedback bundle |
| `.coordination/responses/TW-03-before-after-compare-lovable-prompt.md` | Same constraints in prompt form |
| `.coordination/requests/TW-03-before-after-compare-bff-gap.example.yaml` | Escalation template if live payload diverges from published contract |
| `.coordination/requests/TW-03-before-after-compare-ui-done.example.yaml` | Completion handoff template for the first real frontend return |
| `docs/examples/TW-03-before-after-compare.json` | Example preview payload with full field shape |
| `services/control-plane/bff/main.py:5459-5559` | Live GET/POST route handlers |
| `services/control-plane/bff/read_store.py:7401-7545` | Preview projection: warning ordering, `allowedActions`, polling windows, degraded copy |
| `support/sidecars/EXEC-REBASE-TW03-001/EXEC-REBASE-TW03-001-SIDECAR-BFF-HANDOFF.md` | Prior BFF sidecar confirming route-live classification and listing the only open wording-drift items |

---

## 3. Parent Acceptance Criteria Verification

The parent task durable acceptance in `ai-status.json` requires:

1. TW-03 compare UI uses only the live preview route family and backend-owned
   warning hierarchy
2. Frontend does not invent compare output, local polling rules, or degraded
   semantics
3. A canonical ui-done handoff is emitted when the screen is ready

These acceptance points decompose into the following implementation-level
checks. Items marked `GATE-PASSED (upstream)` are satisfied by the dependency
chain and do not require the front implementation to produce additional
evidence. Items marked `FRONT MUST PRODUCE` are the deliverable surface of
`EXEC-FRONT-TW03-001` itself.

### 3.1 Route and API Authority

| Check | Verification | Status |
|---|---|---|
| Only `GET /api/v1/trainer/sessions/{session_id}/preview` is used for reading compare state | Route is live in BFF `main.py:5459-5497` and exclusively enumerated in `lovable-ui-task.yaml` allowed endpoints | GATE-PASSED (upstream) |
| Only `POST /api/v1/trainer/sessions/{session_id}/preview` with `refresh_mode = "manual"` is used for refresh | Route is live in BFF `main.py:5500-5559`; contract rejects any other refresh body shape | GATE-PASSED (upstream) |
| All API calls route through the existing BFF client — no raw `fetch` from components | Rule enforced in `lovable-ui-task.yaml` constraints; front implementation must follow | FRONT MUST PRODUCE |
| No demo providers or mock data are imported | Rule enforced in `lovable-ui-task.yaml` constraints | FRONT MUST PRODUCE |

### 3.2 Compare Header and Snapshot Fields

| Check | Verification | Status |
|---|---|---|
| `session_id`, `status`, `eval_id` are rendered from the preview response | Required fields listed in `FRONTEND_CHANGE_SPEC.md` compare-header section | FRONT MUST PRODUCE |
| `baseline_snapshot_at`, `candidate_snapshot_at`, `meta.snapshot_at` are rendered without recalculation | Required fields listed in `FRONTEND_CHANGE_SPEC.md`; no client-side timestamp derivation | FRONT MUST PRODUCE |
| `status` is interpreted as the preview lifecycle, not the trainer session lifecycle | State rule enforced in `FRONTEND_CHANGE_SPEC.md` and `docs/screens/TW-03-before-after-compare.md` | FRONT MUST PRODUCE |

### 3.3 Backend-Owned Warning Hierarchy

| Check | Verification | Status |
|---|---|---|
| Warning severity (`level`) comes from each backend-authored `warnings[].level` value — not recalculated from metrics or message copy | Enforced in `FRONTEND_CHANGE_SPEC.md` state rules | FRONT MUST PRODUCE |
| `warning_count_by_level` summary is taken directly from the backend response | Summary truth rule in `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |
| `warnings[]` are rendered in backend array order | Ordering rule in `lovable-ui-task.yaml` and `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |
| Warning hierarchy rail renders all required fields: `warning_id`, `warning_code`, `level`, `parameter_key`, `metric_key`, `message`, `impact_summary` | Required fields from `FRONTEND_CHANGE_SPEC.md` warning rail section | FRONT MUST PRODUCE |

### 3.4 Metric Delta Panels

| Check | Verification | Status |
|---|---|---|
| `metric_delta[]` is rendered from the preview response only — not from TW-01 session detail or local backtests | Non-goal rule in `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |
| Each metric panel shows: `display_label`, `baseline_value`, `candidate_value`, `delta`, `delta_pct`, `unit`, `direction` | Required fields from `FRONTEND_CHANGE_SPEC.md` metric panel section | FRONT MUST PRODUCE |
| Frontend does not recompute `delta`, `delta_pct`, or `direction` | State rule in `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |

### 3.5 Control Diff Panel

| Check | Verification | Status |
|---|---|---|
| Control diff panel renders from `control_diff[]` in the preview response | Required by `FRONTEND_CHANGE_SPEC.md`; backend provides `previous_value`/`new_value` from TW-02 contract | GATE-PASSED (upstream for semantics); FRONT MUST PRODUCE (rendering) |
| Frontend does not reconstruct control diffs from patch history when `control_diff[]` is missing | Non-goal rule in `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |
| Each row shows: `control_id`, `parameter_key`, `display_label`, `previous_value`, `new_value`, `unit`, `last_modified_at` | Required fields from `FRONTEND_CHANGE_SPEC.md` control diff section | FRONT MUST PRODUCE |

### 3.6 Refresh CTA Authority

| Check | Verification | Status |
|---|---|---|
| Refresh CTA visibility and disabled state come only from `allowedActions.canRefreshPreview` | Enforced in `FRONTEND_CHANGE_SPEC.md` state rules — no inference from session status or metric presence | FRONT MUST PRODUCE |
| Refresh CTA is absent when `allowedActions.canRefreshPreview` is absent or false | Non-goal rule in `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |

### 3.7 Rapid-Eval Summary Card

| Check | Verification | Status |
|---|---|---|
| `preview_quality` is rendered as backend-owned — not inferred from `warnings[]`, metric presence, or local heuristics | State rule in `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |
| Summary card shows `warning_count_by_level` breakdown (critical, high, medium, informational) | Required fields from `FRONTEND_CHANGE_SPEC.md` summary card section | FRONT MUST PRODUCE |
| `degraded_copy` is rendered when present — no client-authored fallback | Required field in `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |

### 3.8 Degradation Handling

| Check | Verification | Status |
|---|---|---|
| Degradation state comes only from `meta.surfaces.trainer_preview` | Enforced in `FRONTEND_CHANGE_SPEC.md` state rules | FRONT MUST PRODUCE |
| `ok` → render normally | Degradation rules table in `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |
| `stale` → show last-known result with non-dismissable staleness banner; refresh CTA depends on `allowedActions` | Degradation rules table | FRONT MUST PRODUCE |
| `degraded` → show PKT-005 degradation banner plus `degraded_copy`; suppress refresh CTA | Degradation rules table | FRONT MUST PRODUCE |
| `unavailable` → suppress metric panels and refresh CTA; show only backend-authored unavailable message | Degradation rules table | FRONT MUST PRODUCE |
| `preview_unavailable` status is rendered as explicit degraded compare copy, not as loading | State rule and non-goal in `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |
| Empty `metric_delta[]` is not treated as authoritative when surface is `stale`, `degraded`, or `unavailable` | Degradation rule in `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |

### 3.9 Polling Contract

| Check | Verification | Status |
|---|---|---|
| Polling uses only `GET /api/v1/trainer/sessions/{session_id}/preview?eval_id={eval_id}` | Polling contract in `FRONTEND_CHANGE_SPEC.md` and BFF contract in `docs/bff/TW-03-before-after-compare.md` | FRONT MUST PRODUCE |
| Polling starts only when `status = "pending"` and `polling.enabled = true` | Polling contract in `FRONTEND_CHANGE_SPEC.md` | FRONT MUST PRODUCE |
| Poll interval uses exactly `polling.poll_interval_ms` — no custom backoff or client-side timeout heuristics | Polling contract rule | FRONT MUST PRODUCE |
| Polling stops when `status != "pending"`, surface becomes `degraded` or `unavailable`, or current time passes `polling.deadline_at` | Polling contract rule | FRONT MUST PRODUCE |
| No polling of the refresh POST route | Non-goal rule | FRONT MUST PRODUCE |

### 3.10 BFF-Gap Escalation

| Check | Verification | Status |
|---|---|---|
| If any required field is absent from the BFF response, emit TW-03 bff-gap handoff and stop rendering the affected surface | Failure rules in `FRONTEND_CHANGE_SPEC.md`; template at `.coordination/requests/TW-03-before-after-compare-bff-gap.example.yaml` | FRONT MUST PRODUCE (on condition) |
| No mocking of metric panels, warning ladders, control diffs, degraded copy, or polling timing in gap conditions | Failure rule | FRONT MUST PRODUCE |

### 3.11 Completion Handoff

| Check | Verification | Status |
|---|---|---|
| Emit TW-03 ui-done handoff when screen is complete (template: `.coordination/requests/TW-03-before-after-compare-ui-done.example.yaml`) | Completion rules in `FRONTEND_CHANGE_SPEC.md`; required by parent acceptance criterion 3 | FRONT MUST PRODUCE |
| Publish required feedback bundle: `LOVABLE_CHANGE_FEEDBACK.md`, `API_GAP_REQUESTS.json`, `UI_DECISIONS.md`, `QA_STATUS.md` under `docs/pantheon-feedback/TW-03-before-after-compare/` | Completion rules in `FRONTEND_CHANGE_SPEC.md` and `lovable-ui-task.yaml` | FRONT MUST PRODUCE |

### 3.12 Closure Gate Interpretation

All upstream gates are satisfied. The parent task can be completed by producing:

1. A frontend implementation that renders the six required UI modules
   (compare header, rapid-eval summary card, metric delta panels, warning
   hierarchy rail, control diff panel, refresh CTA) against the live routes
2. Proof that no client-side metric math, warning severity inference, polling
   heuristics, or refresh authority logic was added
3. A real `.coordination/requests/TW-03-before-after-compare-ui-done.yaml`
   (filled from the `.example.yaml` template) with source branch and commit
4. The complete feedback bundle under
   `docs/pantheon-feedback/TW-03-before-after-compare/`

The closure gate is **not** blocked by:

- BFF route availability (routes are live)
- missing handoff bundle (fully published)
- missing example payload (published)
- upstream contract dependencies (TW-01, TW-02, EXEC-REBASE-TW03-001 all done)

---

## 4. Dependency Map

### 4.1 Upstream Truth Providers

| Task / artifact | Status | Contribution to the parent slice |
|---|---|---|
| `TW-01-FOUNDATION-001` | `done` | Published trainer session lifecycle, `session_id` identity, and session `status` semantics — the compare header depends on these |
| `TW-02-CONTROLS-001` | `done` | Published backend-authored control diff semantics (`previous_value`, `new_value`, `allowedActions`) — the control diff panel depends on these |
| `EXEC-REBASE-TW03-001` | `done` | Refreshed TW-03 handoff bundle to route-live state; confirmed both preview routes live in BFF; no BFF gap open |
| `AUTO-IMPL-TW03-001` | archived (`done`) | Delivered the BFF route implementation and `ReadSurfaceStore` compare projection logic |
| `docs/bff/TW-03-before-after-compare.md` | published | Canonical field shape, refresh invariants, warning hierarchy rules |
| `docs/screens/TW-03-before-after-compare.md` | route-live | Page-level rendering rules and readiness gate |
| `docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md` | published | Complete frontend integration brief, failure rules, degradation rules, polling contract, completion rules |
| `.coordination/responses/TW-03-before-after-compare-contract-ready.yaml` | `status: live` | Durable BFF readiness gate at `2026-04-21T06:22:46Z` |
| `.coordination/responses/TW-03-before-after-compare-lovable-ui-task.yaml` | `status: ready` | Frontend dispatch packet with allowed endpoints, constraints, and acceptance items |

### 4.2 Live BFF Surface Summary

| Route | Handler location | State |
|---|---|---|
| `GET /api/v1/trainer/sessions/{session_id}/preview` | `services/control-plane/bff/main.py:5459-5497` | live |
| `POST /api/v1/trainer/sessions/{session_id}/preview` | `services/control-plane/bff/main.py:5500-5559` | live |
| Compare projection logic | `services/control-plane/bff/read_store.py:7401-7545` | live |

BFF-side behaviors confirmed live:
- Warning sorting and `warning_count_by_level` are computed backend-side
- `allowedActions.canRefreshPreview` is backend-owned
- `preview_unavailable` status returns a structured degraded envelope, not a generic error
- Polling fields (`poll_interval_ms`, `max_wait_ms`, `deadline_at`) are projected backend-side
- `degraded_copy` is backend-authored
- Duplicate pending preview jobs are deduplicated — POST returns existing `eval_id` when a same-snapshot pending eval exists

### 4.3 Artifact Flow

```text
TW-01-FOUNDATION-001 (done)
  -> session_id, trainer session lifecycle and status semantics
  -> compare header depends on these

TW-02-CONTROLS-001 (done)
  -> control_diff[].previous_value / new_value semantics
  -> allowedActions authority model
  -> control diff panel depends on these

AUTO-IMPL-TW03-001 (done)
  -> services/control-plane/bff/main.py:5459-5559 (GET/POST routes live)
  -> services/control-plane/bff/read_store.py:7401-7545 (projection live)
  -> docs/examples/TW-03-before-after-compare.json (example payload)

EXEC-REBASE-TW03-001 (done)
  -> docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md
  -> .coordination/responses/TW-03-before-after-compare-contract-ready.yaml
  -> .coordination/responses/TW-03-before-after-compare-lovable-ui-task.yaml
  -> .coordination/responses/TW-03-before-after-compare-lovable-prompt.md
  -> .coordination/requests/TW-03-before-after-compare-bff-gap.example.yaml
  -> .coordination/requests/TW-03-before-after-compare-ui-done.example.yaml
  -> docs/screens/TW-03-before-after-compare.md (route-live)

EXEC-FRONT-TW03-001 (in_progress — this is the current production slice)
  -> must produce: front implementation against live routes
  -> must produce: .coordination/requests/TW-03-before-after-compare-ui-done.yaml (real, filled)
  -> must produce: docs/pantheon-feedback/TW-03-before-after-compare/ feedback bundle
  -> may produce: .coordination/requests/TW-03-before-after-compare-bff-gap.yaml (if live payload diverges)
```

### 4.4 Not-Yet-Produced Outputs (Expected for In-Progress Slice)

These files do not yet exist and should be created by the front lane as part of
`EXEC-FRONT-TW03-001`:

- `.coordination/requests/TW-03-before-after-compare-ui-done.yaml` (real; fill from `.example.yaml`)
- `docs/pantheon-feedback/TW-03-before-after-compare/LOVABLE_CHANGE_FEEDBACK.md`
- `docs/pantheon-feedback/TW-03-before-after-compare/API_GAP_REQUESTS.json`
- `docs/pantheon-feedback/TW-03-before-after-compare/UI_DECISIONS.md`
- `docs/pantheon-feedback/TW-03-before-after-compare/QA_STATUS.md`

The absence of these files is the expected state for an `in_progress` task; it
does not indicate a dependency failure.

### 4.5 Known Verification Caveat (Inherited from Rebaseline Sidecar)

The targeted TW-03 contract test currently has one time-sensitive failure:

- Test: `test_tw03_pending_preview_supports_eval_lookup_and_polling_contract`
- Failure reason: seeded pending preview has `deadline_at = 2026-04-20T19:50:45Z`
  (already expired), so `ReadSurfaceStore` correctly converts it to
  `preview_unavailable` and the assertion expecting `status = pending` fails
- This is correct BFF behavior, not a missing route or broken projection
- The fix is to reseed the test with a future `deadline_at` value
- This should be treated as a follow-up verification item, not a blocker for
  the frontend implementation slice

---

## 5. Non-Goals for This Sidecar

This packet does **not**:

- approve or close the parent task `EXEC-FRONT-TW03-001`
- produce or modify the frontend implementation itself
- emit a real TW-03 `ui-done` or `bff-gap` request
- update `WORKBENCH_DELIVERY_BACKLOG.md` wording (tracked in the rebaseline
  sidecar as a follow-up item)
- update the example payload metadata (tracked in the rebaseline sidecar)

---

## 6. Parent-Owner Action Summary

For `Codex` as parent owner of `EXEC-FRONT-TW03-001`, the support recommendation
is:

1. Treat all upstream BFF and handoff dependencies as **satisfied** — no
   dependency work is blocking the frontend implementation
2. Implement the six required UI modules against `GET /preview` and `POST /preview`
   using the existing BFF client only
3. Follow the exact degradation state machine from `FRONTEND_CHANGE_SPEC.md`:
   `ok` / `stale` / `degraded` / `unavailable` from `meta.surfaces.trainer_preview`
4. Follow the exact polling contract: poll only `GET /preview?eval_id=…` while
   `status = pending` and `polling.enabled = true`; stop on any resolving
   condition or deadline expiry
5. Surface the refresh CTA only when `allowedActions.canRefreshPreview` is
   explicitly `true`
6. On screen completion:
   a. Fill and emit `.coordination/requests/TW-03-before-after-compare-ui-done.yaml`
      from the `.example.yaml` template, including `source_branch`, `source_commit`,
      and `implemented_paths`
   b. Publish the four required feedback docs under
      `docs/pantheon-feedback/TW-03-before-after-compare/`
7. If the live BFF payload is missing any required field at runtime:
   - emit `.coordination/requests/TW-03-before-after-compare-bff-gap.yaml`
   - stop rendering the affected surface
   - do not mock or fill from local state
8. After emitting the completion handoff, move the parent task back into `review`
   for `Gemini` to verify

---

## 7. Handoff Instructions

This sidecar is ready for reviewer `Codex` to assess. The packet is bounded:
no canonical files were modified, no L1 policy docs were touched, and no main
runtime or governance implementation was altered.

Reviewer focus points:
1. Is the acceptance checklist complete and correctly classified against upstream evidence?
2. Is the dependency map accurate for the current state of TW-01, TW-02, EXEC-REBASE-TW03-001?
3. Is the list of `FRONT MUST PRODUCE` items accurate and sufficient to close the parent acceptance gate?
4. Is the completion handoff protocol (ui-done + feedback bundle) correctly described?
5. Are the live BFF code references synchronized to the current repo truth for `main.py` and `read_store.py`?
6. Does the packet correctly separate upstream gate-passed items from frontend-produced deliverables?
7. Are any non-goals or caveats missing or misstated?
