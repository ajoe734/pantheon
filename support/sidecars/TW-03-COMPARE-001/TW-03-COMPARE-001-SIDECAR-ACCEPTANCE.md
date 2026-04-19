# TW-03 Before/After Compare Acceptance and Dependency Map (Sidecar)

**Parent Task**: `TW-03-COMPARE-001` - Publish Trainer preview and before-after compare contract  
**Parent Owner**: `Copilot`  
**Parent Reviewer**: `Codex`  
**Parent Status**: `todo`  
**Sidecar Task**: `TW-03-COMPARE-001-SIDECAR-ACCEPTANCE`  
**Sidecar Owner**: `Claude`  
**Sidecar Reviewer**: `Codex`  
**Helper Kind**: `acceptance_packet`  
**Generated**: `2026-04-19`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or runtime / registry / governance implementations. It prepares a
> reviewable acceptance packet and dependency map for the parent `TW-03` task.

---

## 1. Executive Summary

`TW-03-COMPARE-001` must publish the BFF-owned preview and before/after compare
contract that unlocks the Trainer Workbench compare surface. The parent task
currently has no published contracts for:

1. preview / rapid-eval route (`POST` or `GET /api/v1/trainer/sessions/:id/preview`)
2. preview response contract (`metric_delta[]`, `warnings[]` with severity levels, `preview_quality`)
3. `preview_unavailable` degraded contract (structured BFF payload, not a 5xx)
4. async eval status polling semantics (polling interval, max wait, timeout)
5. `meta.surfaces.trainer_preview` staleness signal

Its direct upstream dependency is already done:

- `TW-02-CONTROLS-001`: done — the control-patch contract, `ControlParameter` schema,
  patch diff response shape (`previous_value` / `new_value` in `updated_controls[]`),
  and patch validation contract are all published as canonical BFF truth

This packet gives the parent owner and reviewer a concise crosswalk from the
recorded acceptance criteria to the already-established upstream constraints and
the still-missing `TW-03` contract surface.

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Canonical task board; confirms parent ownership, sidecar scope, and that `TW-02-CONTROLS-001` is the completed direct dependency |
| `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md` §E3 | Records the five architecture-team gaps that `TW-03` must close, and the minimum contract fields to lock |
| `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md` §TW-03 | Defines `TW-03` surface scope, backend gap table, packetization prerequisite, and readiness gate |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | Confirms the frontend treats the compare surface as a blocked shell pending the preview contract |
| `docs/bff/TW-01-teaching-dialog.md` | Published upstream trainer-session identity and read-side lifecycle contract used by `TW-03` baseline/candidate preview semantics |
| `docs/bff/TW-02-parameter-controls.md` | Published upstream control-patch contract and patch diff response shape; `TW-03` depends on `updated_controls[]` with `previous_value` / `new_value` |

---

## 3. Acceptance Checklist Crosswalk

Parent acceptance recorded in `ai-status.json`:

| Parent acceptance criterion | What must be true for the criterion to be honestly met |
|---|---|
| `preview route and response contract are published` | `TW-03` must publish a preview/rapid-eval route with a complete response contract: `eval_id`, `status`, `baseline_snapshot_at`, `candidate_snapshot_at`, `metric_delta[]`, `warnings[]` (with `level`), `preview_quality`, and `meta.surfaces.trainer_preview` — none of these may remain missing or undefined |
| `warning hierarchy is explicit` | warning severity levels must be BFF-defined; at minimum `critical`, `high`, `medium`, `informational`; the UI must not derive severity client-side |
| `degraded preview behavior is truthful` | `preview_unavailable` must be an explicit BFF contract state, not a 5xx or a stalled `status: pending`; the compare panel must receive a structured degraded payload from the BFF rather than masked as a loading spinner |

Derived verification checklist for the parent owner:

| Check | Repo-visible basis today | Status |
|---|---|---|
| Missing preview/rapid-eval route is identified | gap matrix §E3 + packet family both name this as missing | PENDING parent |
| `metric_delta[]` shape is locked | gap matrix §E3 lists `metric_key`, `baseline_value`, `candidate_value`, `delta`, `delta_pct`, `unit` as required fields | PENDING parent |
| `warnings[]` shape with severity levels is locked | gap matrix §E3 + packet family require BFF-defined warning levels | PENDING parent |
| `preview_unavailable` degraded contract is explicit | gap matrix §E3 + packet family explicitly forbid masking this as a 5xx or spinner | PENDING parent |
| Async eval polling semantics are defined | gap matrix §E3 requires polling interval, max wait, timeout — blocks UI from building a safe polling loop | PENDING parent |
| `meta.surfaces.trainer_preview` staleness signal is defined | packet family lists this as missing; controls canonical degradation banner via PKT-005 | PENDING parent |
| Upstream control-patch diff shape is available | `TW-02-CONTROLS-001` is done; `updated_controls[]` with `previous_value` / `new_value` is published in `docs/bff/TW-02-parameter-controls.md` | READY upstream |
| Upstream session identity and lifecycle are available | `docs/bff/TW-01-teaching-dialog.md` publishes immutable `persona_id`, session `status`, and lifecycle semantics; `PACKET_FAMILY.md` also treats `TW-01` as published in the module section, even though its cross-module summary table still contains one stale lifecycle row | READY upstream |

---

## 4. Dependency Map

### 4.1 Direct dependency already satisfied

| Task ID | Status | Why `TW-03` needs it |
|---|---|---|
| `TW-02-CONTROLS-001` | `done` | `TW-03` compare surface consumes the `updated_controls[]` diff (`previous_value` / `new_value`) from the patch response to render the control-state diff view; the patch validation contract also establishes which candidate state the preview evaluates |

### 4.2 Earlier family prerequisites established in packet truth

These are not recorded as direct blockers in the sidecar task entry, but the
Trainer Workbench packet family says `TW-03` builds on them:

| Upstream module | Why it matters to `TW-03` |
|---|---|
| `TW-01 Teaching Dialog` | provides `session_id`, `status = active`, session lifecycle contract, and the baseline snapshot identity that the preview evaluates against; source of truth is `docs/bff/TW-01-teaching-dialog.md`, not the older summary row in `PACKET_FAMILY.md` that still labels lifecycle as missing |
| `TW-02 Parameter Controls` | provides the patchable candidate state (the preview evaluates the session after patches are applied) and the `previous_value` / `new_value` diff shape |

### 4.3 Downstream surface still blocked on the parent contract

| Surface | Current truthful state | What `TW-03` must unlock |
|---|---|---|
| Before/After Compare panel in Trainer Workbench | blocked shell only | publish preview route contract and response fields |
| `TW-04 Teaching Replay` | blocked (depends on `TW-03`) | `TW-04` replay evidence includes `preview_trigger` events and `eval_ref`; it cannot be fully schema-complete until the `TW-03` preview `eval_id` and contract are stable |

---

## 5. Contract Shape That Must Exist Before Parent Review

From the gap matrix §E3 and packet family §TW-03, the minimum truthful `TW-03`
contract surface must lock the following:

| Contract area | Required shape |
|---|---|
| Route | `POST /api/v1/trainer/sessions/:id/preview` or `GET /api/v1/trainer/sessions/:id/preview` |
| `eval_id` | stable identifier returned on every preview response |
| `status` | `complete \| pending \| failed` |
| `baseline_snapshot_at` | timestamp of the baseline session state the eval compares against |
| `candidate_snapshot_at` | timestamp of the candidate (post-patch) state |
| `metric_delta[]` | `{metric_key, baseline_value, candidate_value, delta, delta_pct, unit}` |
| `warnings[]` | `{warning_id, level, parameter_key, message}` where `level` is BFF-defined |
| Warning levels | at minimum `critical \| high \| medium \| informational` |
| `preview_quality` | BFF-authored quality indicator; shape to be locked by parent |
| `meta.surfaces.trainer_preview` | staleness signal present on every preview response |
| `preview_unavailable` contract | structured BFF payload when rapid-eval infrastructure is unavailable — must not be a 5xx |
| Polling semantics | if `status = pending`: explicit polling interval, max wait, and timeout must be defined |
| Non-goal | the UI must not derive metric deltas from raw control-state parameter values or local simulation |
| Non-goal | the UI must not mask `preview_unavailable` as a loading spinner |

---

## 6. Parent Readiness Snapshot

Current repo truth for the parent is bounded well enough for parent execution,
with one known packet-family summary-table drift called out explicitly below:

| File | Current statement about `TW-03` |
|---|---|
| `PACKET_FAMILY.md` | `TW-03` is not ready; preview route, response contract, `preview_unavailable` contract, polling semantics, and `meta.surfaces.trainer_preview` are all still missing |
| Gap matrix §E3 | Lists all five gaps above; confirms none are published yet |
| `docs/bff/TW-01-teaching-dialog.md` | Confirms trainer session identity and read-side lifecycle are already published upstream; this sidecar therefore treats `TW-01` as satisfied even though the packet family's cross-module summary table still has one outdated lifecycle row |
| `docs/bff/TW-02-parameter-controls.md` | Upstream control-patch contract is done; `TW-03` can build on the diff shape immediately |

That means the parent should not claim readiness yet. The real open work is
narrowly scoped: publish the five missing `TW-03` BFF contracts and an example
payload without changing canonical L1 policy.

---

## 7. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar creates only `support/sidecars/TW-03-COMPARE-001/TW-03-COMPARE-001-SIDECAR-ACCEPTANCE.md` |
| No canonical truth edited | PASS | No L0/L1/L2 canonical contract or runtime file was modified |
| Parent acceptance is faithfully restated | PASS | Acceptance crosswalk uses the exact parent criteria from `ai-status.json` |
| Dependency map matches repo truth | PASS | Direct dep `TW-02-CONTROLS-001` matches the sidecar task entry and the workbench packet family ordering |
| Upstream contracts are confirmed present | PASS | `docs/bff/TW-01-teaching-dialog.md` and `docs/bff/TW-02-parameter-controls.md` both exist and publish the upstream field shapes this sidecar depends on |
| Handoff is actionable for parent owner | PASS | Packet isolates the five required `TW-03` contract surfaces; non-goals are explicit |

---

## 8. Handoff to Reviewer (`Codex`)

This sidecar is ready for review as the acceptance packet for
`TW-03-COMPARE-001`.

What it gives you:

1. a clean acceptance crosswalk from the three parent task criteria to the five
   specific missing BFF contracts that must land before the compare surface is
   packet-ready
2. a dependency map showing that the control-patch upstream (`TW-02`) is done
   and the contract shape is already published
3. a reviewer-ready minimum contract shape table so the parent owner can author
   the `TW-03` BFF contract doc without rediscovering what the gap matrix already
   established

Recommended reviewer stance:

1. approve this sidecar if it accurately reflects the current repo-visible
   `TW-03` gaps and the satisfied upstream control-patch dependency
2. keep the parent task focused on the five publishable contracts: preview route,
   response shape, `preview_unavailable` semantics, polling contract, and
   `meta.surfaces.trainer_preview`

---

*Generated by Claude as a sidecar `acceptance_packet` helper for `TW-03-COMPARE-001`. This file is a support artifact and does not modify canonical truth.*
