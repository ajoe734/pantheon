# BFF Handoff Followup: LOOP-AUTO-BFF-004 — Filter Gap Resolution

**Sidecar kind:** bff_handoff_packet (followup)
**Parent task:** LOOP-AUTO-BFF-004
**Sidecar task:** LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
**Prepared by:** Claude
**Date:** 2026-06-27
**Reviewer:** Claude2

---

## Purpose

This document is a targeted followup to the original handoff packet
(`LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF.md`) and its review
(`LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-REVIEW.md`).

The review identified two filter field gaps in the BFF API contract (Section 3.4 of
the original packet). This document provides:

1. A concrete BFF contract amendment specification for both missing filters.
2. Task ownership recommendation for the fix.
3. Fallback drill procedures that allow drills to proceed if the filters are not
   added before the dependency tasks complete.
4. Updated acceptance conditions for the parent task (LOOP-AUTO-BFF-004) that
   account for the filter gap resolution path.

This packet does **not** modify any L1 canonical policy, `ai-status.json`,
the loop registry, or any BFF implementation file.

---

## 1. Filter Gap Summary (from Review Section 2)

Two BFF query filter fields used by the Drill 2 operator sequence are absent
from `BFF_API_CONTRACT.md`:

| Gap ID | Surface | Route | Missing filter | Used in drill step | Current allowlist |
|---|---|---|---|---|---|
| FG-001 | IN-01 | `GET /api/v1/incidents` | `runtime_id` | Drill 2 Step 3 | `status`, `severity`, `affected_pool_id` |
| FG-002 | EV-01 | `GET /api/v1/evolution-decisions` | `incident_id` | Drill 2 Step 5 | `action_type`, `risk_level`, `status`, `page_token`, `page_size` |

Without these filters, Drill 2 Steps 3 and 5 cannot be executed as written.
An operator would need to manually scan the full list response and match by ID —
which is not acceptable as repeatable evidence and creates ambiguity when multiple
incidents or decisions exist in the test environment.

---

## 2. BFF Contract Amendment Specification

The following amendments should be added to `BFF_API_CONTRACT.md` under the
relevant surface sections. The fix is narrow: add one new optional query parameter
per surface.

### 2.1 IN-01 — Add `runtime_id` filter

**Surface:** `GET /api/v1/incidents` (IN-01)

**Amendment:**

Add `runtime_id` to the allowed query parameter set for IN-01.

```
Current:  status, severity, affected_pool_id
Proposed: status, severity, affected_pool_id, runtime_id
```

**Semantics:**
- `runtime_id` (optional, string): Filter incidents to those whose
  `runtime_id` field equals the provided value.
- When absent, behavior is unchanged (return all incidents matching other filters).
- Must be indexed for performance; the incidents table already indexes
  `affected_pool_id` which shares the same access pattern.

**Contract entry (proposed):**

```
| `/api/v1/incidents` | GET | IN-01 | `{ data: [IncidentCase], meta }` |
  `status`, `severity`, `affected_pool_id`, `runtime_id` |
```

**Evidence of pattern precedent:**
- `GET /api/v1/rollbacks` (EV-04) already carries `runtime_id` as a filter,
  establishing the cross-surface query pattern. See `BFF_API_CONTRACT.md §9.8`.

### 2.2 EV-01 — Add `incident_id` filter

**Surface:** `GET /api/v1/evolution-decisions` (EV-01)

**Amendment:**

Add `incident_id` to the allowed query parameter set for EV-01.

```
Current:  action_type, risk_level, status, page_token, page_size
Proposed: action_type, risk_level, status, page_token, page_size, incident_id
```

**Semantics:**
- `incident_id` (optional, string): Filter decisions to those whose
  `linked_incident_id` field equals the provided value.
- When absent, behavior is unchanged.
- Resolves the Drill 2 Step 5 ambiguity where an operator must trace from a
  specific incident to its generated evolution proposal.

**Contract entry (proposed):**

```
| `/api/v1/evolution-decisions` | GET | EV-01 |
  `{ items: [EvolutionDecision], page_info: { next_page_token }, meta: { snapshot_at } }` |
  `action_type`, `risk_level`, `status`, `page_token`, `page_size`, `incident_id` |
```

---

## 3. Task Ownership Recommendation

The filter gap fix should be owned by the task that controls the BFF contract for
the affected surfaces. Two options:

### Option A — Add to LOOP-AUTO-DEP-004 scope (preferred if scope is not locked)

LOOP-AUTO-DEP-004 ("Split promotion and deployment BFF truth by stage") already
owns changes to the runtime and deployment BFF surfaces. The `runtime_id` filter
on IN-01 is a natural companion to that stage-split work: the operator must be
able to filter incidents by `runtime_id` in the same panel that now shows the
five-stage breakdown.

If LOOP-AUTO-DEP-004 is not yet in review, add FG-001 and FG-002 to its scope.

### Option B — Open a narrow filter-gap task (if LOOP-AUTO-DEP-004 is locked)

If LOOP-AUTO-DEP-004 is already in review or its scope is locked, open a new
narrow task:

```
Task ID:    LOOP-AUTO-BFF-004-FILTER-GAP
Title:      Add runtime_id to IN-01 and incident_id to EV-01
Owner:      <same as LOOP-AUTO-DEP-004 or BFF maintainer>
Reviewer:   Claude2
Artifacts:  services/control-plane/bff/BFF_API_CONTRACT.md
            services/control-plane/bff/read_store.py (or equivalent route handler)
Acceptance: IN-01 accepts runtime_id query param; EV-01 accepts incident_id query param
            Drill 2 Steps 3 and 5 execute without manual list scanning
```

**Recommendation:** Use Option A if LOOP-AUTO-DEP-004 scope is open; Option B
if DEP-004 is already in review.

---

## 4. Fallback Drill Procedures

If the filter gap fix is not merged before LOOP-AUTO-BFF-004 drills are executed,
the following fallback procedures allow the drills to produce valid evidence while
clearly marking the limitation.

### 4.1 Fallback for Drill 2 Step 3 (IN-01 without `runtime_id`)

```
FALLBACK STEP 3 (no runtime_id filter):

  Action:  GET /api/v1/incidents?status=open
  Post-filter: from the returned list, select records where
               incident.runtime_id == {runtime_id}
  Verify:  Filtered incident record exists with severity, trigger_source,
           reconciliation_ids
  Pass if: filtered result contains the expected incident opened within
           the propagation window after the replay anomaly
  Evidence note: "runtime_id filter not yet in BFF_API_CONTRACT; filtered
                  client-side from full list. FG-001 pending."
```

### 4.2 Fallback for Drill 2 Step 5 (EV-01 without `incident_id`)

```
FALLBACK STEP 5 (no incident_id filter):

  Action:  GET /api/v1/evolution-decisions?status=proposed
  Post-filter: from the returned list, select records where
               decision.linked_incident_id == {incident_id}
  Verify:  Evolution decision record exists with status=proposed,
           action_type populated
  Pass if: exactly one decision per incident+target cluster after filtering
  Evidence note: "incident_id filter not yet in BFF_API_CONTRACT; filtered
                  client-side from full list. FG-002 pending."
```

### 4.3 Evidence File Annotation

When using fallback procedures, evidence files must include an explicit gap
annotation block at the top:

```markdown
## Filter Gap Status at Time of Drill

| Gap | Status | Workaround used |
|---|---|---|
| FG-001 runtime_id on IN-01 | PENDING / RESOLVED | Client-side filter / Native filter |
| FG-002 incident_id on EV-01 | PENDING / RESOLVED | Client-side filter / Native filter |

Note: Acceptance criterion AC-D2-3 and AC-D2-4 can still be satisfied using
client-side filtering. The gap annotation does not block drill evidence from
being accepted, but the filter gap tasks must be closed before BFF-004
reaches `proven-live` maturity.
```

---

## 5. Updated Acceptance Path for LOOP-AUTO-BFF-004

The original packet's Section 6 acceptance checklist (AC-D2-3 and AC-D2-4) is
still valid. This section adds a resolution gate:

### 5.1 Additional Acceptance Condition (AC-FG)

- [ ] **AC-FG-1 — Filter gap resolution confirmed before maturity promotion**
  - Either FG-001 and FG-002 are merged into `BFF_API_CONTRACT.md` (via
    LOOP-AUTO-DEP-004 or LOOP-AUTO-BFF-004-FILTER-GAP), **or**
  - The evidence files explicitly document use of fallback procedures and
    annotate the gap status, **and** the gap fix task is tracked as a follow-on
    item.
  - BFF-004 may reach `reconciled` maturity with fallback procedures. Promotion
    to `proven-live` requires the filters to be live in the contract.

### 5.2 Maturity Gate Summary (updated)

| Maturity level | Requirement | Filter gap impact |
|---|---|---|
| api-only | API routes exist | None |
| scheduled | Scheduled workers run | None |
| reconciled (current) | Desired/actual state query live and auto-repairs | Fallback drill procedures sufficient |
| **proven-live (target)** | End-to-end evidence across restart, kill, replay; both drills pass | FG-001 and FG-002 must be in contract |

---

## 6. Operator Journey Amendments

No changes to the operator journey steps from the original packet (Sections 4.1
and 4.2). The fallback procedures in Section 4 of this document replace Steps 3
and 5 of Drill 2 only when the filter gap fix is not yet deployed.

---

## 7. Handoff Notes to Parent Owner (Claude2)

When Claude2 resumes LOOP-AUTO-BFF-004 execution:

1. **Check FG-001 and FG-002 resolution status first.**
   Run `grep -n "runtime_id\|incident_id" services/control-plane/bff/BFF_API_CONTRACT.md`
   to confirm whether the filters have been added. If not present, decide whether
   to use fallback procedures or wait for the fix task.

2. **If using fallback procedures**, include the gap annotation block in both
   drill evidence files (see Section 4.3 above).

3. **If opening LOOP-AUTO-BFF-004-FILTER-GAP**, file it as a dependency of the
   evidence review step, not as a blocker of the drills themselves.

4. **The filter gap does not block drill 1.** Drill 1 (source-to-health) uses
   only BFF surfaces already in the contract and is independent of FG-001/FG-002.

5. **Maturity statement at BFF-004 closeout** must declare which of the two paths
   was taken: native filter evidence or fallback with gap pending.

---

## 8. Reviewer Guidance (for Claude2)

When reviewing this followup packet:

- Confirm that the proposed `runtime_id` semantics in Section 2.1 match how
  `affected_runtime_id` is actually stored in the `IncidentCase` model
  (`services/control-plane/bff/models.py`).
- Confirm that `source_incident_id` exists (or the correct field name) in the
  `EvolutionDecision` model before accepting the Section 2.2 amendment spec.
- If either field name is wrong, correct it in the amendment spec and mark the
  change in the changelog below.
- The fallback procedures in Section 4 are acceptable for evidence gathering but
  must not be used as a substitute for the contract amendment at `proven-live`.

---

## 9. Sidecar Scope Constraints

This packet is a support artifact only. It:

- Does **not** modify any L1 canonical policy file
- Does **not** modify `ai-status.json`, `current-work.md`, or any loop registry
- Does **not** implement any BFF route, filter handler, or evidence collection
- Does **not** change the parent task's acceptance criteria (supplements them only)
- May be updated by Claude2 (parent owner) or Codex (reviewer) as drills are executed
- Must be absorbed into the parent task's final evidence packet at
  LOOP-AUTO-BFF-004 closeout alongside the original packet

---

## 10. Change Log

| Date | Author | Change |
|---|---|---|
| 2026-06-27 | Claude | Initial followup packet created; filter gap resolution spec, fallback procedures, ownership recommendation, updated maturity gate |
| 2026-06-27 | Claude2 (reviewer) | Corrected FG-001 §2.1: internal field reference `affected_runtime_id` → `runtime_id` (verified against `services/incidents/models.py` `IncidentResponse`). Corrected FG-002 §2.2: internal field reference `source_incident_id` → `linked_incident_id` (verified against `services/evolution/models.py` `DecisionResponse`). Fixed §2.1 contract section reference `§9.4` → `§9.8` (EV-04 `/api/v1/rollbacks` lives in §9.8 Evolution Surfaces). Ownership recommendation (Option A via LOOP-AUTO-DEP-004) confirmed valid — DEP-004 is still `todo`. |
| 2026-06-27 | Claude (owner closeout) | Applied same field name corrections to §4.1 fallback code block (`affected_runtime_id` → `runtime_id`) and §4.2 fallback code block (`source_incident_id` → `linked_incident_id`). Reviewer fixes in §2.1/§2.2 were propagated to the fallback procedures for consistency. |
