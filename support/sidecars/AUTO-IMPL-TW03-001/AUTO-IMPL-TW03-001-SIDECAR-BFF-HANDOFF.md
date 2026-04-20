# AUTO-IMPL-TW03-001 - BFF and Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`  
**Parent task:** `AUTO-IMPL-TW03-001` - Implement TW-03 before after compare preview routes  
**Parent owner:** `Codex2`  
**Parent reviewer:** `Claude`  
**Parent status:** `review`  
**Sidecar task:** `AUTO-IMPL-TW03-001-SIDECAR-BFF-HANDOFF`  
**Prepared by:** `Codex`  
**Reviewer:** `Claude`  
**Date:** `2026-04-20`  
**Mutates canonical:** `no`

---

## 1. Purpose

This packet captures the TW-03 BFF query semantics, operator journey, and
frontend integration checklist after the parent implementation landed in the
BFF codebase. It is a support artifact only. It does not change the published
contract, L1 truth, or the main runtime implementation.

The goal is to give the parent owner and reviewer one compact handoff file they
can use when deciding whether TW-03 is ready to absorb into the main line and
when briefing the frontend lane on what is now live.

---

## 2. Current Slice State

| Item | Value |
|---|---|
| Screen / module | `TW-03 Before/After Compare` |
| Frontend route | `/trainer/sessions/:session_id/compare` |
| Canonical contract | `docs/bff/TW-03-before-after-compare.md` |
| Parent task state | `review` |
| BFF route state | Implemented in `services/control-plane/bff/main.py` |
| Read-model state | Implemented in `services/control-plane/bff/read_store.py` |
| Example payload state | Already published in `docs/examples/TW-03-before-after-compare.json` |
| Contract verification | `pytest services/control-plane/bff/test_tw03_before_after_compare_contract.py services/control-plane/bff/test_tw01_teaching_dialog_contract.py` -> `9 passed` |

Current conclusion: the BFF gap for TW-03 is no longer "missing routes". The
remaining work is reviewer acceptance and any downstream frontend absorption.

---

## 3. Source References

| Source | Why it matters |
|---|---|
| `docs/bff/TW-03-before-after-compare.md:5-62` | Canonical route family, required fields, and POST refresh invariants |
| `docs/bff/TW-03-before-after-compare.md:143-226` | Canonical status branches, polling contract, write authority, and degradation rules |
| `docs/screens/TW-03-before-after-compare.md:1-107` | Screen intent, route, pending-BFF placeholder history, and frontend rendering constraints |
| `docs/examples/TW-03-before-after-compare.json` | Happy-path and degraded example payloads for frontend wiring |
| `services/control-plane/bff/main.py:4853-4952` | Landed GET/POST preview endpoints and refresh precondition handling |
| `services/control-plane/bff/read_store.py:5531-5868` | Surface-state projection, degraded copy, deadline collapse, eval lookup, and refresh persistence |
| `services/control-plane/bff/test_tw03_before_after_compare_contract.py:50-159` | Executable proof for complete, pending, refresh dedupe, and preview-unavailable branches |
| `services/control-plane/bff/data/read_surfaces.json:166-421` | Seed data that exercises complete, pending, failed, and preview-unavailable states |

---

## 4. Landed BFF Behavior

### 4.1 Live endpoints

TW-03 now has the expected route family:

| Method | Path | Landed behavior |
|---|---|---|
| `GET` | `/api/v1/trainer/sessions/{session_id}/preview` | Returns latest preview or an explicit `eval_id` record; falls back to structured `preview_unavailable` when no preview exists |
| `POST` | `/api/v1/trainer/sessions/{session_id}/preview` | Accepts only `refresh_mode = "manual"`; rejects invalid refresh authority; reuses existing pending eval instead of duplicating work |

Implementation evidence:
- `main.py:4853-4891` wires GET preview and builds the degraded success body when
  no preview record is available.
- `main.py:4894-4952` validates refresh mode, enforces refresh authority, checks
  trainer session state, and returns `503` only when the preview store itself
  cannot persist.

### 4.2 Projection logic that frontend must respect

The read-store projection now normalizes the raw preview record into the exact
screen-facing payload:

- `read_store.py:5563-5585` derives `meta.surfaces.trainer_preview` from both
  preview status and dataset source.
- `read_store.py:5603-5616` collapses expired `pending` previews into
  `preview_unavailable` after `deadline_at`.
- `read_store.py:5618-5674` re-sorts warnings and re-computes
  `warning_count_by_level` on projection.
- `read_store.py:5643-5648` recalculates `allowedActions.canRefreshPreview`
  instead of trusting raw stored values.
- `read_store.py:5682-5688` forces backend-authored `degraded_copy` whenever the
  status is unavailable or the surface is not `ok`.

This matters because the frontend must treat the projected response as the
complete truth. It should not infer status, warning counts, refresh authority,
or degradation from partial fields.

### 4.3 Refresh semantics

Refresh behavior is now concrete, not hypothetical:

1. Operator presses refresh only when `allowedActions.canRefreshPreview = true`.
2. `POST /preview` accepts only `{ "refresh_mode": "manual" }`.
3. If a preview for the current candidate is already `pending`, the POST path
   returns that same `eval_id`.
4. Otherwise the read store creates a new pending evaluation record with:
   - a backend-authored `eval_id`
   - copied `control_diff`
   - `poll_interval_ms = 3000`
   - `max_wait_ms = 45000`
   - a concrete `deadline_at`
5. Frontend polling then uses `GET /preview?eval_id=...` until resolution.

Persistence evidence:
- `read_store.py:5778-5868` creates and stores the pending preview record and
  returns the projected result.

---

## 5. Query and State Matrix

### 5.1 GET query matrix

| Query shape | Expected use | Expected response |
|---|---|---|
| `GET /preview` | Load current compare page | Latest preview if present; otherwise structured `preview_unavailable` |
| `GET /preview?eval_id=<id>` | Poll a pending preview or inspect a named eval | Named preview record; `404` only when the `eval_id` does not belong to the session |

### 5.2 Status rendering matrix

| `status` | Metric panels | Warning rail | Refresh CTA | Polling |
|---|---|---|---|---|
| `complete` | render backend `metric_delta[]` | render backend ordered `warnings[]` | allowed only if backend says true | off |
| `pending` | no synthetic metrics; use pending placeholders | may be empty | hidden / disabled | `GET` poll by `eval_id` |
| `failed` | suppress fake metrics | may be empty | backend-controlled; do not guess | off |
| `preview_unavailable` | suppress metrics | empty with zero counts | false | off |

### 5.3 Surface-state matrix

| `meta.surfaces.trainer_preview` | Meaning | Frontend obligation |
|---|---|---|
| `ok` | current compare data is healthy | normal render |
| `stale` | last-known compare is being served, often from local snapshot fallback | show non-dismissable stale banner and preserve backend content only |
| `degraded` | compare page may still show control diff, but preview surface is not healthy | show degradation substrate and suppress refresh |
| `unavailable` | no compare content beyond canonical unavailable messaging | suppress metric panels and refresh entirely |

Important projection detail:
- `local_snapshot` + requested `ok` becomes `stale`
- `preview_unavailable` + control diff becomes `degraded`
- `preview_unavailable` without control diff becomes `unavailable`

Those transitions are projection rules, not frontend heuristics.

---

## 6. Operator Journey

The truthful operator flow for TW-03 is now:

```text
Operator opens /trainer/sessions/:session_id/compare
    |
    v
GET /api/v1/trainer/sessions/{session_id}/preview
    |
    +-- status = complete
    |      Render compare header, metric deltas, warnings, control diff
    |      Respect allowedActions.canRefreshPreview
    |
    +-- status = pending
    |      Render header + pending summary + control diff
    |      Poll GET /preview?eval_id={eval_id} every 3000 ms until resolved
    |
    +-- status = failed
    |      Render header + degraded copy + any backend-supplied control diff
    |      Do not invent metrics
    |
    +-- status = preview_unavailable
           Render canonical unavailable copy
           Keep control diff only if backend still supplies it
           Do not treat this as loading
```

Manual refresh journey:

```text
Refresh CTA visible only when allowedActions.canRefreshPreview = true
    |
    v
POST /api/v1/trainer/sessions/{session_id}/preview
{ "refresh_mode": "manual" }
    |
    +-- existing pending eval -> returns same eval_id
    +-- new pending eval -> returns pending payload with polling contract
```

Reviewer note: this journey now matches the contract and the executable tests.
The browser should not need any local compare math, local warning taxonomy, or
client-authored polling policy.

---

## 7. Frontend Handoff Requirements

### 7.1 What the frontend can now assume

- The TW-03 BFF route family exists.
- `preview_unavailable` is a structured success body, not a generic fetch
  failure.
- Warning order and warning counts are backend-owned.
- Refresh dedupe is backend-owned.
- `allowedActions.canRefreshPreview` is the only refresh-CTA authority.
- Poll timing is backend-owned: `3000 ms` interval, `45000 ms` max wait.

### 7.2 What the frontend must not do

- Do not reconstruct compare data from TW-01 session detail.
- Do not reconstruct control diffs from TW-02 patch responses.
- Do not derive warning severity from `warning_code`, metric direction, or copy.
- Do not keep polling after `deadline_at`.
- Do not treat empty `metric_delta[]` as equivalent to healthy zero change.
- Do not map `preview_unavailable` to a loading spinner.
- Do not poll the POST route.

### 7.3 Recommended frontend wiring checklist

- Call `GET /api/v1/trainer/sessions/{session_id}/preview` on initial page load.
- When `status = "pending"` and `polling.enabled = true`, poll only the GET route
  with `eval_id`.
- Render `warning_count_by_level` directly; do not recalculate it from the array.
- Render `warnings[]` in the order returned by the BFF.
- Show `degraded_copy.title` and `degraded_copy.body` exactly as returned.
- Hide or disable refresh strictly from `allowedActions.canRefreshPreview`.
- Treat `meta.surfaces.trainer_preview = "stale"` as a banner state, not a hard
  failure.
- If any required field is missing from the live payload, raise a new BFF gap
  instead of papering over it in the screen.

---

## 8. Verification Snapshot

Verified locally on 2026-04-20:

```bash
pytest services/control-plane/bff/test_tw03_before_after_compare_contract.py \
       services/control-plane/bff/test_tw01_teaching_dialog_contract.py
```

Result:
- `9 passed in 3.43s`

TW-03-specific coverage from `test_tw03_before_after_compare_contract.py`:

| Test | Evidence |
|---|---|
| Complete preview payload | Confirms backend-owned compare payload, warning ordering, stale surface handling, and refresh authority |
| Pending preview lookup | Confirms `eval_id` polling path and exact polling contract |
| Refresh dedupe | Confirms second POST returns the same pending `eval_id` |
| Preview unavailable success body | Confirms structured degraded response with zero warning counts and no refresh CTA |

---

## 9. Reviewer Checklist

- [ ] Support artifact only; no canonical or runtime truth was changed here
- [ ] Parent implementation evidence in `main.py`, `read_store.py`, and TW-03
      tests is represented accurately
- [ ] Query matrix and operator journey match the landed route behavior
- [ ] Frontend checklist does not invent new contract requirements
- [ ] Packet is sufficient to hand TW-03 to a frontend consumer or to absorb
      into the parent task review

---

## 10. Final Handoff
This sidecar has been reviewed and approved, and is ready to remain as support
material for the parent task closeout.

What it gives you:
1. A compact statement that TW-03 no longer has a "missing BFF route" gap.
2. A reviewer-friendly summary of the exact route, polling, degraded, and
   refresh semantics that landed.
3. A frontend-facing checklist that stays inside the published contract and the
   implemented behavior.

Recommended use after approval:
1. Keep this sidecar as the support appendix for `AUTO-IMPL-TW03-001`.
2. Let the parent owner decide whether any part of this packet should be folded
   into the main review notes or downstream frontend handoff materials.
3. Treat the packet as support-only evidence; the parent owner decides whether
   and how to absorb it into the mainline review narrative.

---

*This is a support artifact only. It does not modify canonical truth, mainline
runtime code, registry truth, or governance policy.*
