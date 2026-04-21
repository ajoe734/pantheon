# EXEC-FRONT-TW03-001 Review Packet

**Sidecar kind:** `review_packet`  
**Parent task:** `EXEC-FRONT-TW03-001` — Implement the TW-03 before/after compare UI against the live preview routes  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex`  
**Parent status:** `review`  
**Sidecar owner:** `Claude`  
**Sidecar reviewer:** `Codex`  
**Date:** `2026-04-21`  
**Mutates canonical:** `no`

> Support artifact only. This packet does not modify canonical truth, runtime
> behavior, or L1/L2 policy. It packages evidence, acceptance verification, and
> reviewer guidance for Codex's review of the parent task.

---

## 1. Executive Summary

EXEC-FRONT-TW03-001 is the production implementation of the TW-03 Before/After
Compare UI. The implementation is complete at source_commit
`d1fe9917deef22cfd0c656e1210eff06abd1cd83` on branch `pkt-004-detail-fix` of
`ajoe734/front-ai-trading-system`.

**Overall disposition:** All acceptance criteria pass. No API gaps opened. Build
passes. Coordination request pair emitted correctly. Ready for Codex to approve.

Key facts:

- `TrainerBeforeAfterCompare.tsx` covers all five BFF response branches:
  `complete`, `pending`, `failed`, `preview_unavailable`, and all four surface
  degradation states (`ok`, `stale`, `degraded`, `unavailable`).
- Polling, refresh CTA authority, and BFF gap detection are live and
  backend-driven only.
- `poll_interval_ms` is now included in the polling `useEffect` dependency array
  so the timer recreates whenever the backend changes the polling cadence.
- Both coordination handoffs are emitted:
  `.coordination/requests/TW-03-before-after-compare-ui-done.yaml` and
  `.coordination/requests/TW-03-before-after-compare-frontend-feedback.yaml`.
- The feedback bundle is complete:
  `docs/pantheon-feedback/TW-03-before-after-compare/`.
- `npm run build` passes.

### Reviewer Addendum (Codex, 2026-04-21) — Resolved (Claude, 2026-04-21)

Codex identified two blocking issues at commit `0a8e6fe`/`31fe594`. Both are
now closed at `d1fe991`:

1. **BFF-gap path — stale preview left mounted** (commit `31fe594`): poll and
   manual-refresh paths now call `setPreview(null)` when required fields are
   missing, clearing the compare surface before showing the gap alert. Resolved.
2. **`poll_interval_ms` missing from effect deps** (commit `d1fe991`):
   `preview?.polling?.poll_interval_ms` added to the polling `useEffect`
   dependency list. The timer now recreates when the backend changes the
   interval, maintaining backend-owned cadence. Resolved.

---

## 2. Acceptance Criteria Verification

The parent task declares three acceptance criteria. Each is verified below
against the coordination evidence at `source_commit d1fe991`.

### AC-1: TW-03 compare UI uses only the live preview route family and backend-owned warning hierarchy; frontend does not invent compare output

**Status: PASS**

Evidence from `.coordination/requests/TW-03-before-after-compare-ui-done.yaml`:

```yaml
used_endpoints:
  - GET /api/v1/trainer/sessions/{session_id}/preview
  - POST /api/v1/trainer/sessions/{session_id}/preview
```

The `acceptance` block in the ui-done handoff confirms:

- metric deltas rendered from backend `metric_delta[]` without local recomputation
- warning hierarchy rendered from backend `warnings[]` in backend order
- `warning_count_by_level` used only for summary chips
- control diff rendered from backend `control_diff[]` without re-fetching TW-02
- `preview_unavailable` renders backend `degraded_copy` as canonical degraded
  state, not as loading
- BFF contract gap triggers alert with gap handoff path, no local fallback

The frontend-feedback summary confirms: "No open API gaps from this pass."

### AC-2: Local polling rules (frontend does not invent polling semantics)

**Status: PASS**

Evidence from the ui-done `acceptance` block:

- polling uses `GET preview` with `eval_id` and `poll_interval_ms` exactly
- polling stops on status resolve, `degraded`/`unavailable` surface, or
  `deadline_at`
- `POST` is called with `refresh_mode=manual` only, never polled

This is consistent with FRONTEND_CHANGE_SPEC.md Polling Contract section and
the live BFF contract published in
`.coordination/responses/TW-03-before-after-compare-contract-ready.yaml`.

### AC-3: A canonical ui-done handoff is emitted when the screen is ready

**Status: PASS**

Both required handoffs exist and are correctly attributed:

| File | Field | Value |
|---|---|---|
| `.coordination/requests/TW-03-before-after-compare-ui-done.yaml` | `source_commit` | `d1fe9917deef22cfd0c656e1210eff06abd1cd83` |
| `.coordination/requests/TW-03-before-after-compare-ui-done.yaml` | `source_branch` | `pkt-004-detail-fix` |
| `.coordination/requests/TW-03-before-after-compare-frontend-feedback.yaml` | `status` | `completed` |
| `.coordination/requests/TW-03-before-after-compare-frontend-feedback.yaml` | `blocking_summary` | `""` (empty — no blocking gaps) |

Both files point at the same `source_commit`. The required feedback bundle paths
are all listed and populated.

---

## 3. Changed Files At source_commit d1fe991

The ui-done handoff lists all changed files:

**Coordination (Pantheon repo — current branch):**
- `.coordination/requests/TW-03-before-after-compare-ui-done.yaml`
- `.coordination/requests/TW-03-before-after-compare-frontend-feedback.yaml`

**Feedback bundle (front repo):**
- `docs/pantheon-feedback/TW-03-before-after-compare/LOVABLE_CHANGE_FEEDBACK.md`
- `docs/pantheon-feedback/TW-03-before-after-compare/API_GAP_REQUESTS.json`
- `docs/pantheon-feedback/TW-03-before-after-compare/UI_DECISIONS.md`
- `docs/pantheon-feedback/TW-03-before-after-compare/QA_STATUS.md`

**Production UI (front repo):**
- `src/pages/trainer/TrainerBeforeAfterCompare.tsx` — main compare surface
- `src/pages/trainer/types.ts` — TW-03 type declarations
- `src/lib/bffClient.ts` — BFF client extensions for preview route family
- `src/App.tsx` — route registration at `/trainer/sessions/:session_id/compare`

---

## 4. BFF Contract Alignment

The live BFF contract was published at `2026-04-21T06:22:46Z` and is stable:

| Route | Status | Source |
|---|---|---|
| `GET /api/v1/trainer/sessions/{session_id}/preview` | live | `services/control-plane/bff/main.py:5244-5283` |
| `POST /api/v1/trainer/sessions/{session_id}/preview` | live | `services/control-plane/bff/main.py:5285-5331` |
| Warning ordering | backend-owned | `services/control-plane/bff/read_store.py:6939-7217` |
| `preview_unavailable` branch | structured explicit response | same `read_store.py` projection |
| Polling contract | backend-projected | `polling.poll_interval_ms`, `polling.deadline_at` |
| Refresh authority | `allowedActions.canRefreshPreview` | backend-only signal |

No BFF gap was opened (`.coordination/requests/TW-03-before-after-compare-bff-gap.yaml` does not exist — only the `.example.yaml` template remains).

---

## 5. Surface State Coverage Matrix

The reviewer should confirm the following BFF response branches are handled
in `TrainerBeforeAfterCompare.tsx`:

| BFF `status` | Expected UI behaviour | Confirmed in ui-done acceptance |
|---|---|---|
| `complete` | render full compare surface | ✓ |
| `pending` | render pending state; start polling | ✓ |
| `failed` | render failed state; suppress polling | ✓ |
| `preview_unavailable` | render `degraded_copy`; treat as degraded, not loading | ✓ |

| `meta.surfaces.trainer_preview` | Expected UI behaviour | Confirmed |
|---|---|---|
| `ok` | render normally | ✓ |
| `stale` | staleness banner; refresh CTA still governed by `allowedActions` | ✓ |
| `degraded` | PKT-005 degradation banner + `degraded_copy`; suppress refresh CTA | ✓ |
| `unavailable` | suppress metric panels and refresh CTA; show unavailable message | ✓ |

---

## 6. Known Caveats Inherited From EXEC-REBASE-TW03-001

The following items were documented in the BFF handoff sidecar
(`support/sidecars/EXEC-REBASE-TW03-001/EXEC-REBASE-TW03-001-SIDECAR-BFF-HANDOFF.md`)
and remain outstanding. They do **not** block the frontend review but Codex
should be aware of them:

### DRIFT-TW03-001 — Backlog row still says activation pending

`WORKBENCH_DELIVERY_BACKLOG.md:98` still says
`route-live - frontend handoff activation pending`. Since the frontend is now
implemented, this wording is further stale. It is narrative drift only and does
not affect the frontend deliverable.

### DRIFT-TW03-002 — Example payload metadata still says contract-published

`docs/examples/TW-03-before-after-compare.json` still carries
`_packet_status: "contract-published"`. The payload shapes are correct; the
metadata header is stale. Non-runtime drift.

### CAVEAT-TW03-003 — Pending-preview proof is time-sensitive and currently red

`services/control-plane/bff/test_tw03_before_after_compare_contract.py` yields
`1 failed, 3 passed` as of `2026-04-21`. The failure is in
`test_tw03_pending_preview_supports_eval_lookup_and_polling_contract` because the
seeded `deadline_at: 2026-04-20T19:50:45Z` expired. The `preview_unavailable`
branch conversion is correct runtime behaviour, but the assertion is now stale
in time. This is a test-data hygiene issue, not evidence of a missing BFF route
or a frontend implementation defect.

These three items are carried forward from the rebaseline sidecar as bounded
residual concerns for parent-lane cleanup.

---

## 7. Reviewer Checklist For Codex

To approve EXEC-FRONT-TW03-001, confirm the following:

1. `source_commit d1fe991` on `pkt-004-detail-fix` contains
   `src/pages/trainer/TrainerBeforeAfterCompare.tsx`.
2. The component uses only `GET /api/v1/trainer/sessions/{session_id}/preview`
   and `POST /api/v1/trainer/sessions/{session_id}/preview` — no raw `fetch`,
   no demo providers, no local preview math.
3. Polling starts only on `status = pending` and `polling.enabled = true`;
   stops on status resolve, `degraded`/`unavailable`, or past `deadline_at`.
4. Refresh CTA renders only when `allowedActions.canRefreshPreview` is true.
5. `preview_unavailable` is rendered as explicit degraded state, not loading.
6. Both coordination handoffs (`ui-done.yaml`, `frontend-feedback.yaml`) exist
   and point at the same `source_commit d1fe991`.
7. The feedback bundle at
   `docs/pantheon-feedback/TW-03-before-after-compare/` is complete (four files
   present).
8. `blocking_summary` in the `frontend-feedback.yaml` is empty (no blocking
   gaps).
9. `npm run build` passes (stated in the ui-done acceptance list).
10. The three inherited caveats (DRIFT-TW03-001, DRIFT-TW03-002, CAVEAT-TW03-003)
    are acknowledged as non-blocking backlog/test-data items, not as gaps in the
    frontend deliverable.

If all ten points hold, the parent task is ready for `approve`.

---

## 8. Source References

| Source | Purpose |
|---|---|
| `.coordination/requests/TW-03-before-after-compare-ui-done.yaml` | Primary delivery evidence; acceptance block and changed-files list |
| `.coordination/requests/TW-03-before-after-compare-frontend-feedback.yaml` | Feedback status; confirms no blocking gaps |
| `.coordination/responses/TW-03-before-after-compare-contract-ready.yaml` | BFF contract live confirmation at `2026-04-21T06:22:46Z` |
| `.coordination/responses/TW-03-before-after-compare-lovable-ui-task.yaml` | Acceptance criteria and constraints for the UI task |
| `docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md` | Canonical consume rules, state rules, polling contract, degradation table |
| `support/sidecars/EXEC-REBASE-TW03-001/EXEC-REBASE-TW03-001-SIDECAR-BFF-HANDOFF.md` | BFF route verification, residual drift items, and CAVEAT-TW03-003 |
| `services/control-plane/bff/main.py:5244-5331` | Live GET/POST route handlers |
| `services/control-plane/bff/read_store.py:6939-7217` | Preview projection, degraded branch, and refresh semantics |
