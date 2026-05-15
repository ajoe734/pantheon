# BFF-FIX-001 BFF and Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`
**Sidecar task:** `BFF-FIX-001-SIDECAR-BFF-HANDOFF`
**Helper parent:** `BFF-FIX-001` - Resolve all 5 open BFF gap packets
**Parent owner:** `Copilot`
**Parent reviewer:** `Codex`
**Prepared by:** `Codex2`
**Sidecar reviewer:** `Claude`
**Date:** `2026-04-18`
**Status:** `ready for review`

> Scope constraint: support artifact only. This packet does not modify canonical truth, L1 policy
> files, runtime implementation, registry state, or governance semantics. It packages the current
> BFF gap posture, frontend handoff state, and operator journey dependencies for parent task
> `BFF-FIX-001`.

---

## 1. Purpose

This packet gives the assigned reviewer one compact surface for the current BFF-gap closure wave:

1. restate the five parent gaps and their current repo-visible status
2. separate true BFF contract gaps from transport-only and runtime-data follow-up
3. map the operator journey across Incident Home, Incident Detail, Action Drawer, Post-Incident Review, and PKT-004 binding drilldowns
4. hand the parent owner a support-only absorption summary without rereading global history

---

## 2. Parent Gap Snapshot

| Feature | Gap type | Current state | Key note |
|---|---|---|---|
| `PKT-002-incident-action-drawer` | write-path BFF gap | **resolved** | `POST /api/v1/operator/commands` now accepts the PKT-002 runtime-targeted envelope without legacy `action` field |
| `PKT-002-incident-detail` | composed read BFF gap | **resolved** | detail response now exposes `affected_bindings[]`, `kill_switch`, `allowedActions`, contract-shaped `meta.surfaces`, `severity`, and `opened_at` |
| `PKT-002-incident-home` | read-envelope BFF gap | **resolved** | incident list and kill-switch status envelopes were aligned to the published contract |
| `PKT-003-post-incident-review` | list projection BFF gap | **resolved** | `GET /api/v1/incidents` now includes `items[].resolved_at` for the resolved list panel |
| `PKT-004-capital-binding-drilldowns` | server-side filter BFF gap | **still open** | `GET /api/v1/bindings` still ignores `persona_id` despite the published contract and current front client |

**Net result:** parent task `BFF-FIX-001` is no longer a five-gap problem in repo evidence. It is a one-gap BFF problem (`PKT-004`) plus one runtime-data blocker (`PKT-002 incident-detail`) plus several front-lane follow-ups that do not require new BFF endpoints.

---

## 3. Evidence Table

| Feature | Primary gap file | Repo-visible status | Main resolution or blocker artifact |
|---|---|---|---|
| `PKT-002-incident-action-drawer` | `.coordination/requests/PKT-002-incident-action-drawer-bff-gap.yaml` | `resolved: true` | `.coordination/responses/PKT-002-incident-action-drawer-contract-ready.yaml` |
| `PKT-002-incident-detail` | `.coordination/requests/PKT-002-incident-detail-bff-gap.yaml` | `resolved: true` | `.coordination/responses/PKT-002-incident-detail-contract-ready.yaml` |
| `PKT-002-incident-home` | `.coordination/requests/PKT-002-incident-home-bff-gap.yaml` | `resolved: true` | `.coordination/responses/PKT-002-incident-home-contract-ready.yaml` |
| `PKT-003-post-incident-review` | `.coordination/requests/PKT-003-post-incident-review-bff-gap.yaml` | `resolved: true` | `.coordination/responses/PKT-003-post-incident-review-contract-ready.yaml` |
| `PKT-004-capital-binding-drilldowns` | `.coordination/requests/PKT-004-capital-binding-drilldowns-bff-gap.yaml` | open | `.coordination/responses/PKT-004-capital-binding-drilldowns-contract-ready.yaml` still advertises `persona_id`, but live BFF filtering is not aligned |
| `PKT-002-incident-detail` runtime blocker | `.coordination/requests/PKT-002-incident-detail-needs-runtime.yaml` | `status: blocked` | incident dataset missing in local acceptance environment; this is not a contract-shape gap |

---

## 4. Operator Journey Map

### 4.1 Current route through the operator console

```text
PKT-002 Incident Home
  -> select incident
      -> PKT-002 Incident Detail
          -> open shared Incident Action Drawer
              -> submit runtime commands through POST /api/v1/operator/commands

resolved incident
  -> PKT-003 Post-Incident Review Console

persona capital investigation
  -> PKT-004 Capital Binding Drilldowns
      -> currently blocked on server-side persona_id filtering
```

### 4.2 Journey status by screen

| Screen | Front/BFF state | Notes for parent owner |
|---|---|---|
| `PKT-002-incident-home` | frontend feedback completed; no open API gap in current pass | Home screen is already implemented against `GET /api/v1/incidents` and `GET /api/v1/kill-switch/status`; next Pantheon step is runtime verification, not BFF redesign |
| `PKT-002-incident-detail` | composed BFF contract resolved, but Pantheon follow-up still open | Front review found transport cleanliness issues, route-copy cleanup, missing `opened_at` rendering, missing action rationale copy, and local runtime acceptance blocked by absent incident dataset |
| `PKT-002-incident-action-drawer` | frontend feedback completed; no open API gap in current pass | Reusable drawer is contract-shaped and already submits through the shared BFF client |
| `PKT-003-post-incident-review` | BFF gap resolved; front follow-up remains | No new Pantheon endpoint work required; remaining issues are replayable transport and PKT-005 SSE/staleness handling on the frontend |
| `PKT-004-capital-binding-drilldowns` | parent BFF work still required | Front contract already passes `persona_id`; backend route and read-store still need to honor it |

---

## 5. Distinguishing the Remaining Work

### 5.1 Still-open parent BFF work

Only one of the original five BFF gaps remains open in the current repo state:

- `PKT-004-capital-binding-drilldowns`
  - `GET /api/v1/bindings` does not accept or apply `persona_id`
  - acceptance requires both route-level query support and read-store filtering
  - this is the only gap that still fits the parent task's original BFF-fix scope directly

### 5.2 Not a BFF gap anymore, but still relevant

- `PKT-002-incident-detail-needs-runtime`
  - local acceptance is blocked because `main.read_store.dataset_source("incidents")` resolves to `missing`
  - smoke failures are `OBJECT_NOT_FOUND` 404s, not contract mismatches
  - this follow-up is already split into `RUNTIME-FIX-001`

### 5.3 Front-lane follow-up that should not be mistaken for backend scope

- `PKT-002-incident-detail`
  - canonical frontend-feedback request/bundle transport is incomplete
  - returned packet used `source_commit: HEAD`, which is not replay-clean
  - screen still needs `opened_at` rendering, action rationale copy, and truthful operator-route copy
- `PKT-003-post-incident-review`
  - no new endpoint work required
  - remaining work is replayable request-pair transport plus PKT-005 SSE/staleness alignment in the front repo

---

## 6. Recommended Parent Absorption Summary

If the parent owner wants a concise support-only summary to absorb into `BFF-FIX-001`, the current evidence supports this wording:

> Four of the five original BFF gap packets are already resolved in repo evidence:
> `PKT-002-incident-action-drawer`, `PKT-002-incident-detail`, `PKT-002-incident-home`, and
> `PKT-003-post-incident-review`. The remaining live BFF contract issue is
> `PKT-004-capital-binding-drilldowns`, where `GET /api/v1/bindings` still ignores `persona_id`.
> Separately, `PKT-002-incident-detail` still has a runtime-data blocker (`RUNTIME-FIX-001`) and
> front-lane follow-up items, but those should not be counted as unresolved BFF contract gaps.

This sidecar intentionally does not move the parent task status itself.

---

## 7. Artifact Inventory

| Category | Paths |
|---|---|
| BFF gap records | `.coordination/requests/PKT-002-incident-action-drawer-bff-gap.yaml`, `.coordination/requests/PKT-002-incident-detail-bff-gap.yaml`, `.coordination/requests/PKT-002-incident-home-bff-gap.yaml`, `.coordination/requests/PKT-003-post-incident-review-bff-gap.yaml`, `.coordination/requests/PKT-004-capital-binding-drilldowns-bff-gap.yaml` |
| Runtime blocker split | `.coordination/requests/PKT-002-incident-detail-needs-runtime.yaml` |
| Front feedback anchors | `docs/pantheon-feedback/PKT-002-incident-action-drawer/LOVABLE_CHANGE_FEEDBACK.md`, `docs/pantheon-feedback/PKT-002-incident-home/LOVABLE_CHANGE_FEEDBACK.md`, `docs/pantheon-feedback/PKT-002-incident-detail/LOVABLE_CHANGE_FEEDBACK.md`, `docs/pantheon-feedback/PKT-003-post-incident-review/LOVABLE_CHANGE_FEEDBACK.md` |
| Coordination follow-up packets | `.coordination/requests/PKT-002-incident-action-drawer-frontend-feedback.yaml`, `.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml`, `.coordination/requests/PKT-002-incident-detail-ui-done.yaml`, `.coordination/responses/PKT-002-incident-detail-frontend-feedback.yaml`, `.coordination/requests/PKT-003-post-incident-review-ui-done.yaml`, `.coordination/responses/PKT-003-post-incident-review-frontend-feedback.yaml` |
| This sidecar artifact | `support/sidecars/BFF-FIX-001/BFF-FIX-001-SIDECAR-BFF-HANDOFF.md` |

---

## 8. Sidecar Scope Declaration

- No canonical L1 or L2 document was modified by this sidecar
- No BFF runtime, read-store, registry, or governance implementation was modified by this sidecar
- No contract packet was edited by this sidecar
- The only artifact produced by this slice is this support packet
- Parent closeout remains at the discretion of the `BFF-FIX-001` owner/reviewer chain

---

## 9. Reviewer Handoff Notes

**Reviewer:** `Claude`

**What to verify**

1. Confirm section 2 accurately reflects the current state: four resolved parent gaps, one open parent gap, one separate runtime blocker.
2. Confirm section 4 separates operator journey dependencies from true BFF scope without inventing new parent requirements.
3. Confirm section 5 correctly classifies `PKT-004` as the remaining parent BFF issue and avoids misclassifying frontend transport/SSE work as backend work.
4. Confirm this packet stays support-only and is safe for parent-owner absorption.

**If approved**

Use:

```bash
AI_NAME=Claude python3 scripts/ai_status.py approve BFF-FIX-001-SIDECAR-BFF-HANDOFF "Handoff packet approved; BFF-FIX-001 is accurately reduced to one open PKT-004 BFF gap plus separate runtime/frontend follow-up."
```

**If changes are required**

Use:

```bash
AI_NAME=Claude python3 scripts/ai_status.py reopen BFF-FIX-001-SIDECAR-BFF-HANDOFF "Describe the specific handoff-packet corrections needed."
```
